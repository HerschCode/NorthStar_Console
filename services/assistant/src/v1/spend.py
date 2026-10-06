"""Runtime spend guard for every model call the /v1 surfaces make.

Caps (USD) per request, per UTC day and per UTC month, persisted in SQLite so a restart cannot reset them. A call that
would cross a cap is refused BEFORE it is made (SpendExhausted -> HTTP 429); actual usage is recorded after. The
estimate uses the same chars/4 heuristic as src/evaluation/cost_estimator.py and the real usage numbers once the
response is back. Defaults are deliberately small; override with P2_SPEND_PER_REQUEST_USD / _PER_DAY_USD / _PER_MONTH_USD.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class SpendExhausted(RuntimeError):
    def __init__(self, scope: str, limit: float, spent: float, wanted: float):
        super().__init__(f"{scope} spend cap ${limit:.2f} would be exceeded (spent ${spent:.4f}, this call ~${wanted:.4f})")
        self.scope, self.limit, self.spent, self.wanted = scope, limit, spent, wanted


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class SpendGuard:
    def __init__(self, db_path: str | Path | None = None, per_request_usd: float | None = None,
                 per_day_usd: float | None = None, per_month_usd: float | None = None):
        self.db_path = Path(db_path or os.environ.get("P2_SPEND_DB", "data/spend.db"))
        self.per_request = per_request_usd if per_request_usd is not None else _env_float("P2_SPEND_PER_REQUEST_USD", 0.05)
        self.per_day = per_day_usd if per_day_usd is not None else _env_float("P2_SPEND_PER_DAY_USD", 2.0)
        self.per_month = per_month_usd if per_month_usd is not None else _env_float("P2_SPEND_PER_MONTH_USD", 20.0)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS spend (ts TEXT, day TEXT, month TEXT, label TEXT, model TEXT, "
                      "input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL)")

    def _conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path)

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        return now or datetime.now(timezone.utc)

    def spent(self, scope: str, now: datetime | None = None) -> float:
        n = self._now(now)
        col, key = ("day", n.strftime("%Y-%m-%d")) if scope == "day" else ("month", n.strftime("%Y-%m"))
        with self._conn() as c:
            return float(c.execute(f"SELECT COALESCE(SUM(cost_usd), 0) FROM spend WHERE {col} = ?", (key,)).fetchone()[0])

    def check(self, estimate_usd: float, now: datetime | None = None) -> None:
        """Raise SpendExhausted if this call would cross a cap."""
        if estimate_usd > self.per_request:
            raise SpendExhausted("per-request", self.per_request, 0.0, estimate_usd)
        d = self.spent("day", now)
        if d + estimate_usd > self.per_day:
            raise SpendExhausted("daily", self.per_day, d, estimate_usd)
        m = self.spent("month", now)
        if m + estimate_usd > self.per_month:
            raise SpendExhausted("monthly", self.per_month, m, estimate_usd)

    def record(self, label: str, model: str, input_tokens: int, output_tokens: int, cost_usd: float,
               now: datetime | None = None) -> None:
        n = self._now(now)
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO spend VALUES (?,?,?,?,?,?,?,?)",
                      (n.isoformat(timespec="seconds"), n.strftime("%Y-%m-%d"), n.strftime("%Y-%m"), label, model,
                       int(input_tokens), int(output_tokens), float(cost_usd)))

    def status(self, now: datetime | None = None) -> dict:
        return {"per_request_cap_usd": self.per_request, "daily_cap_usd": self.per_day, "monthly_cap_usd": self.per_month,
                "spent_today_usd": round(self.spent("day", now), 4), "spent_month_usd": round(self.spent("month", now), 4)}
