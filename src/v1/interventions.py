"""Persisted intervention ledger with status history, routed through P3's action firewall.

proposed -> (gateway) denied | held | allowed -> approved | rejected -> executed | execution_failed -> outcome recorded

- A recommendation becomes a `propose_intervention` call authorised by P3 (policy -> taint -> approval). P2 never decides
  on its own whether an action is allowed.
- Approval is a human act with separation of duties: the approver must be a manager or admin and must not be the proposer.
- Execution writes the intervention to P1's ledger (POST /interventions). P1 randomizes treat vs holdout; a holdout case is
  logged for its outcome and NOT acted on.
- Everything is stored in SQLite with one history row per transition, including the gateway decision.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import httpx

from src.tools import client as p1_client
from src.tools.client import OpsPerformanceUnavailable

PROPOSE_ROLES = {"analyst", "manager", "finance", "admin"}
# P3's tool policy (config/tool_policies.yaml) allows `propose_intervention` only with an `action` from a fixed list and strict
# arguments (no extra keys), so P2's business intervention types are mapped onto those actions here. The gateway, not this
# service, decides whether the caller's role may do it.
ACTION_FOR_TYPE = {"expedite_approval": "request_approval", "supplier_escalation": "escalate_case", "reassign_owner": "notify_manager",
                   "hold_payment": "hold_payment", "release_payment": "release_payment"}
APPROVE_ROLES = {"manager", "admin"}
OUTCOME_ROLES = {"manager", "admin"}


class LedgerError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class GatewayClient:
    """HTTP client for P3's action firewall (/gateway/actions/*). The approver token proves the caller may act as an
    approver at all; it is read from the environment and never logged."""

    def __init__(self, base_url: str | None = None, http: httpx.Client | None = None):
        self.base = (base_url or os.environ.get("GATEWAY_URL", "http://localhost:8080")).rstrip("/")
        self.http = http or httpx.Client(timeout=15)

    def _headers(self, trace_header: str | None = None, approver: bool = False) -> dict:
        h = {}
        if trace_header:
            h["traceparent"] = trace_header
        if approver and os.environ.get("GATEWAY_APPROVER_TOKEN"):
            h["X-Approver-Token"] = os.environ["GATEWAY_APPROVER_TOKEN"]
        return h

    def _post(self, path: str, body: dict, trace_header=None, approver=False) -> dict:
        try:
            r = self.http.post(self.base + path, json=body, headers=self._headers(trace_header, approver))
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            raise LedgerError(f"gateway unavailable or refused the call ({type(exc).__name__})", 502) from exc

    def register_source(self, session_id: str, kind: str, text: str, trust: str, trace_header=None) -> None:
        self._post("/gateway/actions/sources", {"session_id": session_id, "kind": kind, "text": text[:4000], "trust": trust}, trace_header)

    def authorize(self, session_id: str, role: str, user_id: str, tool: str, args: dict, trace_header=None) -> dict:
        return self._post("/gateway/actions/authorize", {"session_id": session_id, "role": role, "user_id": user_id, "tool": tool, "args": args}, trace_header)

    def decide(self, approval_id: str, approve: bool, approver_id: str, approver_role: str, note: str = "") -> dict:
        path = f"/gateway/actions/approvals/{approval_id}/{'approve' if approve else 'deny'}"
        return self._post(path, {"approver_id": approver_id, "approver_role": approver_role, "note": note}, approver=True)


class Ledger:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or os.environ.get("P2_LEDGER_DB", "data/ledger.db"))
        with self._c() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS interventions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, intervention_type TEXT, rationale TEXT, risk REAL,
                proposer TEXT, proposer_role TEXT, status TEXT, approval_id TEXT, gateway_decision TEXT,
                p1_intervention_id INTEGER, assignment TEXT, outcome TEXT, created REAL, updated REAL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, intervention_id INTEGER, ts REAL, status TEXT, actor TEXT, detail TEXT)""")

    def _c(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _log(self, c, iid: int, status: str, actor: str, detail: dict | None = None):
        c.execute("INSERT INTO history (intervention_id, ts, status, actor, detail) VALUES (?,?,?,?,?)", (iid, time.time(), status, actor, json.dumps(detail or {})))
        c.execute("UPDATE interventions SET status = ?, updated = ? WHERE id = ?", (status, time.time(), iid))

    def get(self, iid: int) -> dict | None:
        with self._c() as c:
            row = c.execute("SELECT * FROM interventions WHERE id = ?", (iid,)).fetchone()
            if not row:
                return None
            hist = [dict(h) for h in c.execute("SELECT ts, status, actor, detail FROM history WHERE intervention_id = ? ORDER BY id", (iid,))]
        d = dict(row)
        d["gateway_decision"] = json.loads(d["gateway_decision"] or "{}")
        d["history"] = [{**h, "detail": json.loads(h["detail"] or "{}")} for h in hist]
        return d

    def list(self, status: str | None = None, limit: int = 100) -> list[dict]:
        q = "SELECT id FROM interventions" + (" WHERE status = ?" if status else "") + " ORDER BY id DESC LIMIT ?"
        with self._c() as c:
            ids = [r["id"] for r in c.execute(q, (status, limit) if status else (limit,))]
        return [self.get(i) for i in ids]

    # ── transitions ──
    def propose(self, *, case_id: str, intervention_type: str, rationale: str, risk: float, identity: dict, gateway: GatewayClient,
                sources: list[dict] | None = None, trace_header: str | None = None) -> dict:
        user, role = identity["user"], identity["role"]
        if role not in PROPOSE_ROLES:
            raise LedgerError(f"role '{role}' may not propose interventions", 403)
        with self._c() as c:
            cur = c.execute("INSERT INTO interventions (case_id, intervention_type, rationale, risk, proposer, proposer_role, status, created, updated) "
                            "VALUES (?,?,?,?,?,?,?,?,?)", (case_id, intervention_type, rationale, risk, user, role, "proposed", time.time(), time.time()))
            iid = cur.lastrowid
            self._log(c, iid, "proposed", user, {"rationale": rationale})
        session = f"p2-int-{iid}"
        for s in sources or []:
            gateway.register_source(session, s["kind"], s["text"], s["trust"], trace_header)
        action = ACTION_FOR_TYPE.get(intervention_type)
        if action is None:
            with self._c() as c:
                self._log(c, iid, "gateway_denied", "p2", {"note": f"intervention type {intervention_type!r} has no mapped gateway action"})
            return self.get(iid)
        args = {"action": action, "target": case_id, "reason": rationale[:300], "priority": "high" if action == "hold_payment" else "normal"}
        decision = gateway.authorize(session, role, user, "propose_intervention", args, trace_header)
        with self._c() as c:
            c.execute("UPDATE interventions SET gateway_decision = ?, approval_id = ? WHERE id = ?", (json.dumps(decision), decision.get("approval_id"), iid))
            effect = decision.get("effect")
            if effect == "deny":
                self._log(c, iid, "gateway_denied", "gateway", decision)
            elif effect == "require_approval":
                self._log(c, iid, "gateway_held", "gateway", decision)
            elif effect == "allow":
                self._log(c, iid, "approved", "gateway-policy", {"note": "allowed by policy without human approval", **decision})
            else:
                self._log(c, iid, "gateway_denied", "gateway", {"note": "unrecognised gateway decision; failing closed", **decision})
        return self.get(iid)

    def decide(self, iid: int, approve: bool, identity: dict, gateway: GatewayClient, note: str = "") -> dict:
        row = self.get(iid)
        if row is None:
            raise LedgerError("no such intervention", 404)
        if row["status"] != "gateway_held":
            raise LedgerError(f"intervention is '{row['status']}', not awaiting approval", 409)
        if identity["role"] not in APPROVE_ROLES:
            raise LedgerError(f"role '{identity['role']}' may not decide interventions", 403)
        if identity["user"] == row["proposer"]:
            raise LedgerError("separation of duties: the proposer cannot decide their own intervention", 403)
        if not row["approval_id"]:
            raise LedgerError("no gateway approval id recorded; failing closed", 409)
        res = gateway.decide(row["approval_id"], approve, identity["user"], identity["role"], note)
        with self._c() as c:
            self._log(c, iid, "approved" if approve else "rejected", identity["user"], {"note": note, "gateway": res})
        return self.get(iid)

    def execute(self, iid: int, p1_post=None, trace_header: str | None = None) -> dict:
        """Write an approved intervention to P1's ledger. Safe to call once: only 'approved' rows execute."""
        row = self.get(iid)
        if row is None or row["status"] != "approved":
            raise LedgerError("only approved interventions can be executed", 409)
        post = p1_post or (lambda body: p1_client.send("POST", "/interventions", body, extra_headers={"traceparent": trace_header} if trace_header else None))
        try:
            out = post({"case_id": row["case_id"], "intervention_type": row["intervention_type"],
                        "risk_at_intervention": row["risk"], "notes": f"Northstar intervention #{iid}: {row['rationale'][:200]}"})
        except OpsPerformanceUnavailable as exc:
            with self._c() as c:
                self._log(c, iid, "execution_failed", "system", {"error": str(exc)[:200]})
            return self.get(iid)
        with self._c() as c:
            c.execute("UPDATE interventions SET p1_intervention_id = ?, assignment = ? WHERE id = ?", (out.get("intervention_id"), out.get("assignment"), iid))
            self._log(c, iid, "executed", "system", {"p1": out, "note": "holdout case: logged for its outcome, no action taken" if out.get("assignment") == "holdout" else "treated"})
        return self.get(iid)

    def record_outcome(self, iid: int, breached_after: bool, identity: dict, p1_patch=None) -> dict:
        row = self.get(iid)
        if row is None:
            raise LedgerError("no such intervention", 404)
        if identity["role"] not in OUTCOME_ROLES:
            raise LedgerError(f"role '{identity['role']}' may not record outcomes", 403)
        if row["status"] != "executed":
            raise LedgerError(f"intervention is '{row['status']}'; outcomes are recorded after execution", 409)
        if row["p1_intervention_id"] is not None:
            patch = p1_patch or (lambda pid, body: p1_client.send("PATCH", f"/v1/interventions/{pid}/outcome", body))
            try:
                patch(row["p1_intervention_id"], {"breached_after": breached_after})
            except OpsPerformanceUnavailable as exc:
                raise LedgerError(f"could not record the outcome in P1: {str(exc)[:150]}", 502)
        with self._c() as c:
            c.execute("UPDATE interventions SET outcome = ? WHERE id = ?", ("breached" if breached_after else "not_breached", iid))
            self._log(c, iid, "outcome_recorded", identity["user"], {"breached_after": breached_after})
        return self.get(iid)
