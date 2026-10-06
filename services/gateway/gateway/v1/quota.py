"""Daily AI-question budget per identity and overall, so a public demo cannot burn the shared free-tier model quota (Gemini + Groq).

This is the gateway's own guard in FRONT of the providers' limits: it says "your daily allowance is used" in the gateway's words, long
before a provider has to say 429. In memory (resets with the process and at 00:00 UTC); a multi-instance deployment would put the counters
in the same Firestore/Redis the assistant uses. Env: GATEWAY_AI_DAILY_PER_USER (default 40), GATEWAY_AI_DAILY_GLOBAL (default 400); 0 disables.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

_LOCK = threading.Lock()
_STATE: dict = {"day": None, "users": {}, "total": 0}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def seconds_to_reset(now: float | None = None) -> int:
    t = datetime.fromtimestamp(now or time.time(), timezone.utc)
    nxt = (t.replace(hour=0, minute=0, second=0, microsecond=0)).timestamp() + 86400
    return max(1, int(nxt - t.timestamp()))


class QuotaExceeded(Exception):
    def __init__(self, scope: str, limit: int, retry_after_s: int):
        self.scope, self.limit, self.retry_after_s = scope, limit, retry_after_s
        who = "this demo identity" if scope == "user" else "the whole demo"
        hrs = f"{retry_after_s / 3600:.1f} h" if retry_after_s >= 5400 else f"{max(1, round(retry_after_s / 60))} min"
        super().__init__(f"Daily AI question allowance for {who} is used ({limit}). It protects the shared Gemini/Groq free-tier quota and resets at 00:00 UTC (in {hrs}).")


def _roll(now: float) -> None:
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    if _STATE["day"] != day:
        _STATE.update(day=day, users={}, total=0)


def consume(user_id: str, now: float | None = None) -> None:
    """Count one AI question or raise QuotaExceeded. Called before the request is forwarded."""
    now = now or time.time()
    per_user, total = _env_int("GATEWAY_AI_DAILY_PER_USER", 40), _env_int("GATEWAY_AI_DAILY_GLOBAL", 400)
    with _LOCK:
        _roll(now)
        if total and _STATE["total"] >= total:
            raise QuotaExceeded("global", total, seconds_to_reset(now))
        if per_user and _STATE["users"].get(user_id, 0) >= per_user:
            raise QuotaExceeded("user", per_user, seconds_to_reset(now))
        _STATE["users"][user_id] = _STATE["users"].get(user_id, 0) + 1
        _STATE["total"] += 1


def status(user_id: str | None = None, now: float | None = None) -> dict:
    now = now or time.time()
    with _LOCK:
        _roll(now)
        return {"per_user_daily": _env_int("GATEWAY_AI_DAILY_PER_USER", 40), "global_daily": _env_int("GATEWAY_AI_DAILY_GLOBAL", 400),
                "used_by_you": _STATE["users"].get(user_id, 0) if user_id else None, "used_overall": _STATE["total"], "resets_in_s": seconds_to_reset(now)}


def reset_for_tests() -> None:
    with _LOCK:
        _STATE.update(day=None, users={}, total=0)
