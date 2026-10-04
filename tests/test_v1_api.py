"""Integration tests for the /v1 routes with every external dependency faked (P1, P3 gateway, retrieval, model)."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.tools.client import OpsPerformanceUnavailable
from src.v1 import router as r
from src.v1.interventions import GatewayClient, Ledger, LedgerError
from src.v1.llm import FakeLLM, NoLLM
from src.v1.p1 import P1
from src.v1.spend import SpendExhausted, SpendGuard

client = TestClient(app)
KEY = {"X-API-Key": "k"}


def hdr(user="alice", role="analyst", **extra):
    return {**KEY, "X-Northstar-User": user, "X-Northstar-Role": role, **extra}


OVERVIEW = {"as_of": "2018-04-16", "kpis": {"already_late": {"value": 544, "unit": "cases", "provenance": "measured", "source": "s"}}}
HITS = [SimpleNamespace(document_id="d1", title="Procurement Policy", section_title="4.2", citation="Procurement Policy, Section 4.2",
                        text="Orders above 10,000 euros require a second approval.")]


class FakeGateway(GatewayClient):
    def __init__(self, effect="require_approval"):
        self.effect, self.calls, self.sources = effect, [], []

    def register_source(self, session_id, kind, text, trust, trace_header=None):
        self.sources.append((kind, trust))

    def authorize(self, session_id, role, user_id, tool, args, trace_header=None):
        self.calls.append((tool, args, role, user_id))
        return {"effect": self.effect, "tool": tool, "approval_id": "ap-1" if self.effect == "require_approval" else None,
                "stage": "approval" if self.effect == "require_approval" else "policy", "reasons": ["test"]}

    def decide(self, approval_id, approve, approver_id, approver_role, note=""):
        self.calls.append(("decide", approval_id, approve, approver_id))
        return {"status": "approved" if approve else "denied"}


@pytest.fixture(autouse=True)
def wired(monkeypatch, tmp_path):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("API_KEYS", "svc:k:admin")
    fresh = r.Deps()
    fresh.llm = FakeLLM(lambda s, u: json.dumps({"answer": "544 cases late.", "claims": [{"text": "544 cases are late.", "evidence_ids": ["e2"]}]}))
    fresh.retriever = lambda q, k: HITS
    fresh.p1_factory = lambda trace: P1(trace, lambda path, params=None, **kw: {"/v1/overview": OVERVIEW,
                                                                              "/v1/briefing": {"as_of": "2018-04-16", "facts": [{"id": "late", "fact": "544 open cases are late."}]}}[path])
    fresh.stores = {"ledger": Ledger(tmp_path / "l.db"), "traces": r.TraceStore(tmp_path / "t.db"),
                    "investigations": r.inv_mod.InvestigationStore(tmp_path / "i.db"), "spend": SpendGuard(tmp_path / "s.db")}
    fresh.gateway = FakeGateway()
    monkeypatch.setattr(r, "deps", fresh)
    from src.v1 import briefing
    briefing._CACHE.clear()
    return fresh


def test_all_v1_routes_require_the_service_key():
    assert client.post("/v1/ask", json={"question": "late?"}).status_code == 401
    assert client.get("/v1/interventions").status_code == 401


def test_ask_roundtrip_with_trace_and_persisted_trace_lookup():
    tid = "4bf92f3577b34da6a3ce929d0e0e4736"
    out = client.post("/v1/ask", json={"question": "How many cases are late?", "context": {"page": "overview"}},
                      headers=hdr(traceparent=f"00-{tid}-00f067aa0ba902b7-01"))
    assert out.status_code == 200
    body = out.json()
    assert body["trace_id"] == tid and body["claims"][0]["supported"] and body["evidence"]
    tr = client.get(f"/v1/traces/{tid}", headers=KEY).json()
    assert {s["kind"] for s in tr["spans"]} >= {"p1", "retrieval", "llm", "gate"} and tr["cost_usd"] > 0
    assert client.get("/v1/traces/" + "0" * 31 + "1", headers=KEY).status_code == 404


def test_spend_exhaustion_returns_429_and_nothing_else_leaks(wired):
    class Broke:
        name = "x"

        def complete(self, *a, **k):
            raise SpendExhausted("daily", 2.0, 1.99, 0.05)
    wired.llm = Broke()
    res = client.post("/v1/ask", json={"question": "How many cases are late?"}, headers=hdr())
    assert res.status_code == 429 and res.json()["scope"] == "daily"


def test_investigation_persists_and_exports_markdown_and_pdf(wired):
    wired.llm = FakeLLM(lambda s, u: json.dumps({"summary": "Late cases coincide with receipt delays.",
                                                  "root_causes": [{"text": "Receipt delays are associated with 544 late cases.", "evidence_ids": ["e2"]},
                                                                  {"text": "Late because of suppliers.", "evidence_ids": ["e2"]}],
                                                  "recommendations": [{"text": "Review receipt queue.", "evidence_ids": ["e2"]}], "limitations": "Replay data."}))
    inv = client.post("/v1/investigations", json={"question": "Why are cases late?", "context": {"page": "overview"}}, headers=hdr()).json()
    assert inv["warnings"] and inv["root_causes"][1]["causal_language"] and inv["evidence"]["documents"][0]["citation"]
    got = client.get(f"/v1/investigations/{inv['id']}", headers=KEY).json()
    assert got["summary"] == inv["summary"]
    assert [i["id"] for i in client.get("/v1/investigations", headers=KEY).json()["investigations"]] == [inv["id"]]
    md = client.get(f"/v1/investigations/{inv['id']}/export?format=md", headers=KEY).text
    assert "## Root causes (association, not causation)" in md and "## Limitations" in md
    pdf = client.get(f"/v1/investigations/{inv['id']}/export?format=pdf", headers=KEY)
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert client.get("/v1/investigations/nope", headers=KEY).status_code == 404


def test_briefing_and_nl_filter_routes(wired):
    wired.llm = NoLLM()
    b = client.post("/v1/briefing", json={"as_of": "2018-04-16"}, headers=KEY).json()
    assert b["source"] == "template" and b["items"][0]["sentences"][0]["fact_ids"] == ["late"]
    f = client.post("/v1/nl-filter", json={"question": "orders above 50k in invoicing"}, headers=KEY).json()
    assert f["filter"] == {"min_value": 50000.0, "stage": "invoicing"} and f["restatement"]
    assert client.post("/v1/nl-filter", json={"question": "orders due next week"}, headers=KEY).json()["rejected"]


# ── interventions through the gateway ──
def _propose(role="analyst", user="alice", **kw):
    body = {"case_id": "C1", "intervention_type": "expedite_approval", "rationale": "Critical expected loss", "risk": 0.64, **kw}
    return client.post("/v1/interventions", json=body, headers=hdr(user, role))


def test_viewer_cannot_propose():
    assert _propose(role="viewer").status_code == 403


def test_gateway_deny_blocks_the_proposal(wired):
    wired.gateway = FakeGateway("deny")
    row = _propose().json()
    assert row["status"] == "gateway_denied" and [h["status"] for h in row["history"]] == ["proposed", "gateway_denied"]
    assert wired.gateway.calls[0][0] == "propose_intervention"


def test_held_then_approved_by_another_manager_executes_in_p1(wired, monkeypatch):
    posted = []
    orig = Ledger.execute
    monkeypatch.setattr(r.Ledger, "execute", lambda self, iid, p1_post=None, trace_header=None: orig(
        self, iid, p1_post=lambda body: posted.append(body) or {"intervention_id": 77, "assignment": "treat"}, trace_header=trace_header))
    row = _propose(sources=[{"kind": "retrieved_document", "text": "doc text", "trust": "untrusted"}]).json()
    assert row["status"] == "gateway_held" and wired.gateway.sources == [("retrieved_document", "untrusted")]
    iid = row["id"]
    assert client.post(f"/v1/interventions/{iid}/approve", json={}, headers=hdr("alice", "manager")).status_code == 403      # separation of duties
    assert client.post(f"/v1/interventions/{iid}/approve", json={}, headers=hdr("bob", "analyst")).status_code == 403         # role
    done = client.post(f"/v1/interventions/{iid}/approve", json={"note": "ok"}, headers=hdr("bob", "manager")).json()
    assert done["status"] == "executed" and done["p1_intervention_id"] == 77 and posted[0]["case_id"] == "C1"
    assert [h["status"] for h in done["history"]] == ["proposed", "gateway_held", "approved", "executed"]
    assert client.post(f"/v1/interventions/{iid}/approve", json={}, headers=hdr("bob", "manager")).status_code == 409           # not twice


def test_reject_and_unknown_and_outcome_flow(wired, monkeypatch):
    row = _propose().json()
    rej = client.post(f"/v1/interventions/{row['id']}/reject", json={"note": "no"}, headers=hdr("bob", "manager")).json()
    assert rej["status"] == "rejected"
    assert client.post("/v1/interventions/999/reject", json={}, headers=hdr("bob", "manager")).status_code == 404
    assert client.get("/v1/interventions/999", headers=KEY).status_code == 404
    # outcome only after execution
    row2 = _propose().json()
    assert client.post(f"/v1/interventions/{row2['id']}/outcome", json={"breached_after": False}, headers=hdr("bob", "manager")).status_code == 409


def test_policy_allow_executes_immediately_and_p1_failure_is_recorded(wired, monkeypatch):
    wired.gateway = FakeGateway("allow")
    orig = Ledger.execute
    monkeypatch.setattr(r.Ledger, "execute", lambda self, iid, p1_post=None, trace_header=None: orig(
        self, iid, p1_post=lambda body: (_ for _ in ()).throw(OpsPerformanceUnavailable("p1 down")), trace_header=trace_header))
    row = _propose().json()
    assert row["status"] == "execution_failed" and [h["status"] for h in row["history"]][-2:] == ["approved", "execution_failed"]


def test_holdout_is_logged_but_flagged_and_outcome_recorded_in_p1(tmp_path):
    led = Ledger(tmp_path / "x.db")
    gw = FakeGateway("allow")
    row = led.propose(case_id="C2", intervention_type="expedite_approval", rationale="r", risk=0.5, identity={"user": "a", "role": "analyst"}, gateway=gw)
    assert row["status"] == "approved"
    ex = led.execute(row["id"], p1_post=lambda b: {"intervention_id": 5, "assignment": "holdout"})
    assert ex["status"] == "executed" and "no action taken" in ex["history"][-1]["detail"]["note"]
    patched = []
    out = led.record_outcome(row["id"], True, {"user": "m", "role": "manager"}, p1_patch=lambda pid, body: patched.append((pid, body)))
    assert out["outcome"] == "breached" and patched == [(5, {"breached_after": True})]
    with pytest.raises(LedgerError):
        led.record_outcome(row["id"], True, {"user": "v", "role": "viewer"}, p1_patch=lambda *a: None)


def test_missing_approval_id_fails_closed(tmp_path):
    led = Ledger(tmp_path / "y.db")

    class Odd(FakeGateway):
        def authorize(self, *a, **k):
            return {"effect": "require_approval"}
    row = led.propose(case_id="C3", intervention_type="x", rationale="r", risk=0.5, identity={"user": "a", "role": "analyst"}, gateway=Odd())
    with pytest.raises(LedgerError) as e:
        led.decide(row["id"], True, {"user": "b", "role": "manager"}, Odd())
    assert e.value.status == 409


def test_openapi_lists_the_v1_surface():
    paths = app.openapi()["paths"]
    for p in ("/v1/ask", "/v1/investigations", "/v1/briefing", "/v1/nl-filter", "/v1/interventions", "/v1/traces/{trace_id}", "/v1/spend"):
        assert p in paths
