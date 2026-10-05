"""Persisted governance data: every decision the gateway makes, queryable by window.

The gateway already writes two append-only JSONL audit files (logs/gateway.jsonl for text decisions, logs/actions.jsonl for
tool-call decisions). This store ingests them incrementally into SQLite (idempotent, tracked by byte offset), so the
governance APIs read persisted data, never in-process counters. On a host with an ephemeral disk the SQLite file can later
be swapped for Cloud Logging / BigQuery behind the same methods.

Privacy: session and user ids are stored only as short salted hashes; prompt text is never stored (the gateway's own log does
not contain it either).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from gateway.actions.firewall import APPROVAL_DECISION_STAGE

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "logs" / "governance.db"
WINDOWS = {"5m": 300, "1h": 3600, "24h": 86400, "7d": 7 * 86400}


def _salt() -> str:
    return os.environ.get("GATEWAY_GOVERNANCE_SALT", "northstar-governance")


def short_hash(value: str | None) -> str:
    return hashlib.sha256(f"{_salt()}|{value}".encode("utf-8")).hexdigest()[:10] if value else ""


class GovernanceStore:
    def __init__(self, db_path: str | Path | None = None, gateway_log: Path | None = None, actions_log: Path | None = None):
        self.db_path = Path(db_path or os.environ.get("GATEWAY_GOVERNANCE_DB", DEFAULT_DB))
        self.gateway_log = gateway_log or Path(os.environ.get("GATEWAY_LOG_PATH") or ROOT / "logs" / "gateway.jsonl")
        self.actions_log = actions_log or Path(os.environ.get("GATEWAY_ACTIONS_AUDIT", ROOT / "logs" / "actions.jsonl"))
        self._lock = threading.Lock()
        with self._c() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, request_id TEXT, phase TEXT, decision TEXT,
                layer TEXT, rule_id TEXT, latency_ms REAL, session_hash TEXT, user_hash TEXT, trace_id TEXT, pii_found INTEGER, route TEXT);
            CREATE INDEX IF NOT EXISTS ix_dec_ts ON decisions (ts);
            CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, tool TEXT, effect TEXT, stage TEXT, rule TEXT,
                risk TEXT, session_hash TEXT, user_hash TEXT, role TEXT, approval_id TEXT, reasons TEXT, trace_id TEXT);
            CREATE INDEX IF NOT EXISTS ix_act_ts ON actions (ts);
            CREATE TABLE IF NOT EXISTS offsets (source TEXT PRIMARY KEY, pos INTEGER, head TEXT);
            """)

    def _c(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.db_path, timeout=15)
        c.row_factory = sqlite3.Row
        return c

    # ── ingestion ──
    def ingest(self) -> dict:
        """Read new complete lines from both JSONL files. Safe to call before every query."""
        with self._lock:
            return {"decisions": self._ingest_file("gateway", self.gateway_log, self._insert_decision),
                    "actions": self._ingest_file("actions", self.actions_log, self._insert_action)}

    def _ingest_file(self, source: str, path: Path, insert) -> int:
        if not path.exists():
            return 0
        with self._c() as c:
            row = c.execute("SELECT pos, head FROM offsets WHERE source = ?", (source,)).fetchone()
            pos, head = (row["pos"], row["head"]) if row else (0, "")
            size = path.stat().st_size
            with open(path, "rb") as hf:
                cur_head = hf.read(256).decode("utf-8", "replace")
            if size < pos or (head and not cur_head.startswith(head)):      # rotated or truncated: start over
                pos = 0
            n = 0
            with open(path, "rb") as f:
                f.seek(pos)
                for raw in f:
                    if not raw.endswith(b"\n"):
                        break                                              # an in-progress write: retry next time
                    pos += len(raw)
                    try:
                        rec = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(rec, dict) and insert(c, rec):
                        n += 1
            c.execute("INSERT OR REPLACE INTO offsets VALUES (?,?,?)", (source, pos, cur_head))
        return n

    @staticmethod
    def _insert_decision(c, r: dict) -> bool:
        if not all(k in r for k in ("timestamp", "phase", "decision")):
            return False
        extra = r.get("extra") or {}
        c.execute("INSERT INTO decisions (ts, request_id, phase, decision, layer, rule_id, latency_ms, session_hash, user_hash, trace_id, pii_found, route) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (float(r["timestamp"]), str(r.get("request_id") or ""), r["phase"], r["decision"], r.get("detection_layer_used"),
                   r.get("matched_pattern_id"), float(r.get("latency_ms") or 0), short_hash(r.get("session_id")), short_hash(r.get("user_id")),
                   extra.get("trace_id"), len(extra.get("pii_found") or []) if isinstance(extra.get("pii_found"), list) else int(bool(extra.get("pii_found"))),
                   extra.get("route")))
        return True

    @staticmethod
    def _insert_action(c, r: dict) -> bool:
        if "timestamp" not in r or "effect" not in r:
            return False
        c.execute("INSERT INTO actions (ts, tool, effect, stage, rule, risk, session_hash, user_hash, role, approval_id, reasons, trace_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (float(r["timestamp"]), r.get("tool"), r["effect"], r.get("stage"), r.get("rule"), r.get("risk"), short_hash(r.get("session_id")),
                   short_hash(r.get("user_id")), r.get("role"), r.get("approval_id"), json.dumps(r.get("reasons") or [])[:500], r.get("trace_id")))
        return True

    # ── queries ──
    def summary(self, window: str = "1h", now: float | None = None) -> dict:
        if window not in WINDOWS:
            raise ValueError(f"window must be one of {sorted(WINDOWS)}")
        self.ingest()
        t1 = time.time() if now is None else now
        t0 = t1 - WINDOWS[window]
        bucket = {"5m": 30, "1h": 300, "24h": 3600, "7d": 6 * 3600}[window]
        with self._c() as c:
            dec = [dict(r) for r in c.execute("SELECT * FROM decisions WHERE ts >= ? AND ts <= ? ORDER BY ts", (t0, t1))]
            act = [dict(r) for r in c.execute("SELECT * FROM actions WHERE ts >= ? AND ts <= ? ORDER BY ts", (t0, t1))]
            pending = c.execute("SELECT COUNT(*) FROM actions WHERE effect = 'require_approval'").fetchone()[0]
        # a human deciding a held action is audited too, but it is not another authorisation decision: count it apart so held/allowed/denied stay per action
        decided = [a for a in act if a["stage"] == APPROVAL_DECISION_STAGE]
        act = [a for a in act if a["stage"] != APPROVAL_DECISION_STAGE]
        reqs: dict[str, dict] = {}
        for i, d in enumerate(dec):
            key = d["request_id"] or f"row{i}"
            q = reqs.setdefault(key, {"ts": d["ts"], "blocked": False, "layer": None, "rule": None, "latency": 0.0, "pii": 0})
            q["latency"] += d["latency_ms"] or 0.0
            q["pii"] = max(q["pii"], d["pii_found"] or 0)
            if d["decision"] == "block" and not q["blocked"]:
                q.update(blocked=True, layer=d["layer"] or "none", rule=d["rule_id"])
        blocked = [q for q in reqs.values() if q["blocked"]]
        lat = sorted(q["latency"] for q in reqs.values())

        def pct(p):
            return round(lat[min(len(lat) - 1, int(p * len(lat)))], 2) if lat else None

        series: dict[int, dict] = {}
        for q in reqs.values():
            b = int(q["ts"] // bucket) * bucket
            s = series.setdefault(b, {"t": b, "requests": 0, "blocked": 0})
            s["requests"] += 1
            s["blocked"] += int(q["blocked"])
        effects: dict[str, int] = {}
        for a in act:
            key = {"allow": "allowed", "require_approval": "held"}.get(a["effect"], None)
            if a["effect"] == "deny":
                key = "denied_taint" if a["stage"] == "taint" else "denied_budget" if a["stage"] == "budget" else "denied_policy"
            effects[key or a["effect"]] = effects.get(key or a["effect"], 0) + 1
        blocks_by_layer: dict[str, int] = {}
        blocks_by_rule: dict[str, int] = {}
        for q in blocked:
            blocks_by_layer[q["layer"]] = blocks_by_layer.get(q["layer"], 0) + 1
            if q["rule"]:
                blocks_by_rule[q["rule"]] = blocks_by_rule.get(q["rule"], 0) + 1
        return {"window": window, "from": t0, "to": t1, "provenance": "measured", "source": "persisted gateway decision log",
                "requests": len(reqs), "blocked_requests": len(blocked),
                "block_rate": round(len(blocked) / len(reqs), 4) if reqs else None,
                "blocks_by_layer": blocks_by_layer, "blocks_by_rule": dict(sorted(blocks_by_rule.items(), key=lambda kv: -kv[1])[:15]),
                "pii_requests": sum(1 for q in reqs.values() if q["pii"]),
                "latency_ms": {"p50": pct(0.5), "p95": pct(0.95)},
                "actions": {"total": len(act), **effects, "pending_approvals_logged": pending,
                            "approvals_decided": {"approved": sum(a["effect"] == "approval_granted" for a in decided), "rejected": sum(a["effect"] == "approval_rejected" for a in decided)}},
                "series": [series[k] for k in sorted(series)]}

    def events(self, limit: int = 100, admin: bool = False) -> list[dict]:
        """Recent decisions. Viewers get hashed session/user ids and no rule text; admins also get the raw reasons."""
        self.ingest()
        limit = max(1, min(limit, 500))
        with self._c() as c:
            dec = [dict(r) for r in c.execute("SELECT * FROM decisions ORDER BY ts DESC LIMIT ?", (limit,))]
            act = [dict(r) for r in c.execute("SELECT * FROM actions ORDER BY ts DESC LIMIT ?", (limit,))]
        out = [{"kind": "text", "ts": d["ts"], "request_id": d["request_id"], "phase": d["phase"], "decision": d["decision"], "layer": d["layer"],
                "rule_id": d["rule_id"], "latency_ms": round(d["latency_ms"], 2), "session": d["session_hash"], "trace_id": d["trace_id"]} for d in dec]
        for a in act:
            e = {"kind": "action", "ts": a["ts"], "tool": a["tool"], "effect": a["effect"], "stage": a["stage"], "rule": a["rule"], "risk": a["risk"],
                 "role": a["role"], "session": a["session_hash"], "trace_id": a["trace_id"], "approval_id": a["approval_id"]}
            if admin:
                e["reasons"] = json.loads(a["reasons"] or "[]")
                e["user"] = a["user_hash"]
            out.append(e)
        return sorted(out, key=lambda e: -e["ts"])[:limit]
