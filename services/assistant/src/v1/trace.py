"""Request traces for the AI audit-trace page: GET /v1/traces/{trace_id}.

A trace id is taken from the caller's W3C `traceparent` header (so one id spans console -> gateway -> this service ->
P1) or generated. Spans record retrieval, each P1 call, each model call (model, tokens, cost), the claim-support gate
and action proposals. Persisted in SQLite so the trace survives the request.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def parse_traceparent(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.strip().split("-")
    if len(parts) == 4 and len(parts[1]) == 32 and set(parts[1]) <= set("0123456789abcdef") and set(parts[1]) != {"0"}:
        return parts[1]
    return None


def new_trace_id() -> str:
    return secrets.token_hex(16)


class Trace:
    def __init__(self, trace_id: str | None = None, name: str = "request"):
        self.trace_id = trace_id or new_trace_id()
        self.name = name
        self.started_at = time.time()
        self.spans: list[dict] = []

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{secrets.token_hex(8)}-01"

    @contextmanager
    def span(self, name: str, kind: str = "internal", **attrs):
        rec = {"name": name, "kind": kind, "start_ms": round((time.time() - self.started_at) * 1000, 1), "attrs": dict(attrs), "status": "ok"}
        t0 = time.perf_counter()
        try:
            yield rec["attrs"]
        except Exception as exc:
            rec["status"] = "error"
            rec["attrs"]["error"] = type(exc).__name__
            raise
        finally:
            rec["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            self.spans.append(rec)

    def add(self, name: str, kind: str, duration_ms: float = 0.0, **attrs) -> None:
        self.spans.append({"name": name, "kind": kind, "start_ms": round((time.time() - self.started_at) * 1000, 1),
                           "duration_ms": duration_ms, "attrs": attrs, "status": "ok"})

    def to_dict(self) -> dict:
        total = round((time.time() - self.started_at) * 1000, 1)
        cost = round(sum(s["attrs"].get("cost_usd", 0.0) for s in self.spans if s["kind"] == "llm"), 6)
        return {"trace_id": self.trace_id, "name": self.name, "started_at": self.started_at, "total_ms": total,
                "cost_usd": cost, "spans": self.spans}


class TraceStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or os.environ.get("P2_TRACE_DB", "data/traces.db"))
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS traces (trace_id TEXT PRIMARY KEY, created REAL, body TEXT)")

    def _conn(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path)

    def save(self, trace: Trace) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO traces VALUES (?,?,?)", (trace.trace_id, time.time(), json.dumps(trace.to_dict())))
            c.execute("DELETE FROM traces WHERE created < ?", (time.time() - 14 * 86400,))

    def get(self, trace_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT body FROM traces WHERE trace_id = ?", (trace_id,)).fetchone()
        return json.loads(row[0]) if row else None
