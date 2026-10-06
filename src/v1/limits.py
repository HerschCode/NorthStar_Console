"""Free-tier limit handling shared by every free model: what was hit, when it clears, and what to tell the user.

Three layers, in order of authority:
1. the provider's own 429 (authoritative): parsed into a ProviderLimitError with the scope (per-minute / per-day / token size) and the
   time it clears (retry-after, Gemini's retryDelay, or the next daily reset);
2. a cooldown per model, persisted, so the next request skips that model instead of paying for another 429;
3. optional local soft caps (rpm / rpd / tpm from config/free_models.yaml, the numbers the user copied from their dashboards), checked
   BEFORE a call so we usually stop before the provider has to say no.

Usage counters sit behind UsageStore: SQLite locally, Firestore on Cloud Run (its disk is ephemeral). Both are tested.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

try:                                    # Gemini's daily quota resets at midnight Pacific time
    from zoneinfo import ZoneInfo
    PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:                       # pragma: no cover - tzdata missing: fall back to a fixed UTC-8 offset
    PACIFIC = timezone(timedelta(hours=-8))


class LLMUnavailable(RuntimeError):
    """Re-exported by src.v1.llm; defined here so limits and llm do not import each other."""


SCOPE_TEXT = {"minute": "per-minute request limit", "day": "per-day request limit", "tokens": "per-minute token limit",
              "tokens_day": "per-day token limit", "request_size": "request is larger than the model's token limit", "unknown": "rate limit"}


def human_wait(seconds: float | None) -> str:
    if seconds is None:
        return "later"
    s = max(0, int(round(seconds)))
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{round(s / 60)} min"
    return f"{s / 3600:.1f} h"


class ProviderLimitError(LLMUnavailable):
    """One model said no (or we know it would). `retry_after_s` is when it can answer again."""

    def __init__(self, provider: str, model: str, scope: str, retry_after_s: float | None, detail: str = "", local: bool = False):
        self.provider, self.model, self.scope, self.retry_after_s, self.detail, self.local = provider, model, scope, retry_after_s, detail, local
        who = {"gemini": "Gemini (Google AI Studio free tier)", "groq": "Groq (free plan)"}.get(provider, provider)
        super().__init__(f"{who} · {model}: {SCOPE_TEXT.get(scope, scope)} reached — try again in {human_wait(retry_after_s)}"
                         + (" (our own soft cap, set from your dashboard limits)" if local else ""))

    def to_dict(self) -> dict:
        return {"provider": self.provider, "model": self.model, "scope": self.scope, "retry_after_s": self.retry_after_s,
                "message": str(self), "local_cap": self.local}


class ModelNotAvailable(LLMUnavailable):
    """The key cannot call this model name (retired, misspelt, or not on this plan). Remembered for a few hours so the chain does not
    pay a failed request for it on every question."""


UNAVAILABLE_FOR_S = 6 * 3600


class LimitsExhausted(LLMUnavailable):
    """Every model in the chain is limited (or has no key). Carries each reason so the UI can list them."""

    def __init__(self, limited: list[ProviderLimitError], other: list[str] | None = None):
        self.limited, self.other = limited, other or []
        waits = [e.retry_after_s for e in limited if e.retry_after_s is not None]
        self.retry_after_s = min(waits) if waits else None
        head = "All free models are at their limits right now" if limited else "No free model could answer"
        tail = f" — soonest retry in {human_wait(self.retry_after_s)}" if self.retry_after_s is not None else ""
        super().__init__(head + tail + ". " + " | ".join([str(e) for e in limited] + self.other))

    def to_dict(self) -> dict:
        return {"exhausted": True, "retry_after_s": self.retry_after_s, "models": [e.to_dict() for e in self.limited], "other": self.other}


# ── parsing what providers tell us ──
_DUR = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$")


def parse_duration(text: str | None) -> float | None:
    """'23s', '2m59.56s', '1h2m', '120ms', '17' (seconds) -> seconds; None when unparseable."""
    if text is None:
        return None
    t = str(text).strip().lower()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        pass
    m = _DUR.match(t)
    if not m or not any(m.groups()):
        return None
    h, mi, s, ms = (float(x) if x else 0.0 for x in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def next_daily_reset(provider: str, now: datetime | None = None) -> float:
    """Seconds until the provider's daily quota resets: midnight Pacific for Gemini, UTC midnight as the conservative default."""
    now = now or datetime.now(timezone.utc)
    if provider == "gemini":
        local = now.astimezone(PACIFIC)
        nxt = (local + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
    else:
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
    return max(1.0, (nxt - now).total_seconds())


def gemini_limit_from_error(body: dict | None, model: str, now: datetime | None = None) -> ProviderLimitError:
    """Gemini 429 RESOURCE_EXHAUSTED: scope from the violated quota's id/metric, wait from RetryInfo.retryDelay."""
    err = (body or {}).get("error", {}) if isinstance(body, dict) else {}
    details = err.get("details") or []
    text = " ".join([str(err.get("message", ""))] + [str(d) for d in details]).lower()
    delay = None
    for d in details:
        if isinstance(d, dict) and str(d.get("@type", "")).endswith("RetryInfo"):
            delay = parse_duration(d.get("retryDelay"))
    if "perday" in text.replace(" ", "").replace("_", "") or "per day" in text or "daily" in text:
        scope = "day"
    elif "inputtoken" in text.replace(" ", "").replace("_", "") or "tokens per minute" in text:
        scope = "tokens"
    elif "perminute" in text.replace(" ", "").replace("_", "") or "per minute" in text:
        scope = "minute"
    else:
        scope = "unknown"
    if scope == "day" and (delay is None or delay < 3600):
        delay = next_daily_reset("gemini", now)          # a per-day quota does not clear in 23 s even if retryDelay says so
    return ProviderLimitError("gemini", model, scope, delay if delay is not None else 60.0, str(err.get("message", ""))[:200])


def groq_limit_from_response(status: int, headers: dict, body: dict | None, model: str, now: datetime | None = None) -> ProviderLimitError:
    """Groq 429 (or 413): retry-after header; x-ratelimit-limit-requests is the DAILY request quota, limit-tokens the PER-MINUTE token quota."""
    h = {k.lower(): v for k, v in (headers or {}).items()}
    msg = str((body or {}).get("error", {}).get("message", "")) if isinstance(body, dict) else ""
    low = msg.lower()
    retry = parse_duration(h.get("retry-after"))
    try:
        rem_req = float(h["x-ratelimit-remaining-requests"]) if "x-ratelimit-remaining-requests" in h else None
        rem_tok = float(h["x-ratelimit-remaining-tokens"]) if "x-ratelimit-remaining-tokens" in h else None
    except ValueError:
        rem_req = rem_tok = None
    if status == 413 or "request too large" in low:
        return ProviderLimitError("groq", model, "request_size", None, msg[:200])
    if "tokens per day" in low or "(tpd)" in low:
        scope = "tokens_day"
    elif "requests per day" in low or "(rpd)" in low or (rem_req is not None and rem_req <= 0):
        scope = "day"
    elif "tokens per minute" in low or "(tpm)" in low or (rem_tok is not None and rem_tok <= 0):
        scope = "tokens"
    elif "requests per minute" in low or "(rpm)" in low:
        scope = "minute"
    else:
        scope = "unknown"
    if scope in ("day", "tokens_day"):
        reset = parse_duration(h.get("x-ratelimit-reset-requests"))
        retry = retry if retry is not None and retry >= 60 else (reset if reset is not None else next_daily_reset("groq", now))
    return ProviderLimitError("groq", model, scope, retry if retry is not None else 60.0, msg[:200])


# ── persistence ──
@dataclass
class ModelStatus:
    provider: str
    model: str
    state: str                     # ok | cooling | no_key | unavailable
    cooldown_scope: str | None = None
    cooldown_until: float | None = None
    retry_after_s: float | None = None
    used_minute: int = 0
    used_day: int = 0
    tokens_minute: int = 0
    caps: dict = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class UsageStore(Protocol):
    def record(self, model: str, tokens: int, ts: float | None = None) -> None: ...
    def counts(self, model: str, now: float | None = None) -> tuple[int, int, int]: ...      # (requests last minute, requests today, tokens last minute)
    def set_cooldown(self, model: str, until: float, scope: str, detail: str = "") -> None: ...
    def cooldown(self, model: str, now: float | None = None) -> tuple[float, str, str] | None: ...
    def clear_cooldown(self, model: str) -> None: ...


def _day_key(ts: float) -> str:
    return datetime.fromtimestamp(ts, PACIFIC).strftime("%Y-%m-%d")


class SqliteUsageStore:
    def __init__(self, db_path: str | Path | None = None):
        self.path = Path(db_path or os.environ.get("P2_USAGE_DB", "data/usage.db"))
        self._lock = threading.Lock()
        with self._c() as c:
            c.execute("CREATE TABLE IF NOT EXISTS calls (ts REAL, model TEXT, tokens INTEGER)")
            c.execute("CREATE INDEX IF NOT EXISTS ix_calls ON calls (model, ts)")
            c.execute("CREATE TABLE IF NOT EXISTS cooldowns (model TEXT PRIMARY KEY, until REAL, scope TEXT, detail TEXT)")

    def _c(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.path, timeout=15)

    def record(self, model, tokens, ts=None):
        with self._lock, self._c() as c:
            c.execute("INSERT INTO calls VALUES (?,?,?)", (ts or time.time(), model, int(tokens)))
            c.execute("DELETE FROM calls WHERE ts < ?", (time.time() - 3 * 86400,))

    def counts(self, model, now=None):
        now = now or time.time()
        with self._c() as c:
            minute = c.execute("SELECT COUNT(*), COALESCE(SUM(tokens),0) FROM calls WHERE model=? AND ts>?", (model, now - 60)).fetchone()
            day_rows = c.execute("SELECT ts FROM calls WHERE model=? AND ts>?", (model, now - 86400 - 3600)).fetchall()
        today = sum(1 for (t,) in day_rows if _day_key(t) == _day_key(now))
        return int(minute[0]), today, int(minute[1])

    def set_cooldown(self, model, until, scope, detail=""):
        with self._lock, self._c() as c:
            c.execute("INSERT OR REPLACE INTO cooldowns VALUES (?,?,?,?)", (model, until, scope, detail[:200]))

    def cooldown(self, model, now=None):
        now = now or time.time()
        with self._c() as c:
            row = c.execute("SELECT until, scope, detail FROM cooldowns WHERE model=?", (model,)).fetchone()
        return (row[0], row[1], row[2]) if row and row[0] > now else None

    def clear_cooldown(self, model):
        with self._lock, self._c() as c:
            c.execute("DELETE FROM cooldowns WHERE model=?", (model,))


class FirestoreUsageStore:
    """Same contract on Firestore (Cloud Run's disk is ephemeral). `client` is a google.cloud.firestore.Client; tests pass a fake.
    One document per call is simple and good enough for a free-tier trickle; a high-volume deployment would shard counters."""

    def __init__(self, client=None, collection: str = "llm_usage"):
        if client is None:                                  # pragma: no cover - needs google-cloud-firestore and credentials
            from google.cloud import firestore
            client = firestore.Client()
        self.db, self.col = client, collection

    def record(self, model, tokens, ts=None):
        self.db.collection(f"{self.col}_calls").add({"model": model, "ts": ts or time.time(), "tokens": int(tokens)})

    def counts(self, model, now=None):
        now = now or time.time()
        rows = [d.to_dict() for d in self.db.collection(f"{self.col}_calls").where("model", "==", model).where("ts", ">", now - 86400 - 3600).stream()]
        minute = [r for r in rows if r["ts"] > now - 60]
        return len(minute), sum(1 for r in rows if _day_key(r["ts"]) == _day_key(now)), sum(int(r["tokens"]) for r in minute)

    def set_cooldown(self, model, until, scope, detail=""):
        self.db.collection(f"{self.col}_cooldowns").document(model.replace("/", "__")).set({"until": until, "scope": scope, "detail": detail[:200]})

    def cooldown(self, model, now=None):
        now = now or time.time()
        d = self.db.collection(f"{self.col}_cooldowns").document(model.replace("/", "__")).get()
        row = d.to_dict() if d.exists else None
        return (row["until"], row["scope"], row.get("detail", "")) if row and row["until"] > now else None

    def clear_cooldown(self, model):
        self.db.collection(f"{self.col}_cooldowns").document(model.replace("/", "__")).delete()


def get_usage_store() -> UsageStore:
    if os.environ.get("P2_USAGE_BACKEND", "sqlite").lower() == "firestore":
        return FirestoreUsageStore()
    return SqliteUsageStore()


@dataclass
class Caps:
    rpm: int | None = None
    rpd: int | None = None
    tpm: int | None = None


class LimitTracker:
    """Decides before a call whether a model may be tried, and records what happened after."""

    def __init__(self, store: UsageStore, caps: dict[str, Caps], soft_fraction: float = 0.85, default_rpm: int | None = 8):
        self.store, self.caps, self.soft, self.default_rpm = store, caps, soft_fraction, default_rpm

    def _cap(self, model: str) -> Caps:
        c = self.caps.get(model) or Caps()
        return Caps(c.rpm if c.rpm is not None else self.default_rpm, c.rpd, c.tpm)

    def check(self, provider: str, model: str, est_tokens: int = 0, now: float | None = None) -> None:
        now = now or time.time()
        cd = self.store.cooldown(model, now)
        if cd and cd[1] == "unavailable":
            raise ModelNotAvailable(f"{model} is not available to this key (checked recently; python -m scripts.check_free_models shows what is)")
        if cd:
            raise ProviderLimitError(provider, model, cd[1], cd[0] - now, cd[2])
        cap = self._cap(model)
        n_min, n_day, tok_min = self.store.counts(model, now)
        if cap.rpm is not None and n_min >= max(1, int(cap.rpm * self.soft)):
            raise ProviderLimitError(provider, model, "minute", 60 - (now % 60), "", local=True)
        if cap.rpd is not None and n_day >= max(1, int(cap.rpd * self.soft)):
            raise ProviderLimitError(provider, model, "day", next_daily_reset(provider, datetime.fromtimestamp(now, timezone.utc)), "", local=True)
        if cap.tpm is not None and tok_min + est_tokens > cap.tpm * self.soft:
            raise ProviderLimitError(provider, model, "tokens", 60 - (now % 60), "", local=True)

    def on_success(self, model: str, tokens: int) -> None:
        self.store.record(model, tokens)

    def on_unavailable(self, model: str, detail: str = "", now: float | None = None) -> None:
        self.store.set_cooldown(model, (now or time.time()) + UNAVAILABLE_FOR_S, "unavailable", detail)

    def on_limit(self, err: ProviderLimitError, now: float | None = None) -> None:
        if err.local:
            return
        now = now or time.time()
        wait = err.retry_after_s if err.retry_after_s is not None else 60.0
        if err.scope != "request_size":
            self.store.set_cooldown(err.model, now + wait, err.scope, err.detail)

    def status(self, provider: str, model: str, has_key: bool, now: float | None = None) -> ModelStatus:
        now = now or time.time()
        cap = self._cap(model)
        n_min, n_day, tok_min = self.store.counts(model, now)
        st = ModelStatus(provider, model, "ok", used_minute=n_min, used_day=n_day, tokens_minute=tok_min,
                         caps={"rpm": cap.rpm, "rpd": cap.rpd, "tpm": cap.tpm})
        if not has_key:
            st.state, st.note = "no_key", f"set {'GEMINI_API_KEY' if provider == 'gemini' else 'GROQ_API_KEY'} to enable"
            return st
        cd = self.store.cooldown(model, now)
        if cd and cd[1] == "unavailable":
            st.state, st.cooldown_until, st.retry_after_s, st.note = "unavailable", cd[0], cd[0] - now, "not available to this key; re-checked in " + human_wait(cd[0] - now)
        elif cd:
            st.state, st.cooldown_until, st.cooldown_scope, st.retry_after_s, st.note = "cooling", cd[0], cd[1], cd[0] - now, cd[2]
        return st
