"""
Tests for the audit investigation mode (Phase F3).

Verifies:
- AuditResult structure: data_evidence from P1, policy_clauses from docs, gate applied to doc section
- Gate filters clauses that aren't supported by retrieved chunks (and never touches data_evidence)
- P1 unavailability is handled gracefully (report still compiles with fallback data)
- Validation: case_id or vendor must be provided
- Route responds 422 when neither case_id nor vendor is given

No real LLM calls: the compile step's client is injected as a fake that returns a
deterministic compile_audit_report tool response.
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from src.agent.audit import (
    run_audit,
    AuditResult,
    _build_policy_query,
    _gate_clauses,
)
from src.tools.client import OpsPerformanceUnavailable
from src.api.main import app

client = TestClient(app)


# ── helpers ───────────────────────────────────────────────────────────────────

def _fake_exception(control="C4", vendor="Acme", exposure=12450.0):
    return {
        "control_id": control,
        "vendor": vendor,
        "severity": "high",
        "exposure_eur": exposure,
        "evidence": "INV-2024-089 matches INV-2024-072 (same vendor, EUR 12,450, 28 days apart)",
    }


def _make_policy_hit(citation, text):
    hit = MagicMock()
    hit.text = text
    hit.citation = citation
    return hit


def _fake_client(compile_output: dict):
    """Build a fake Anthropic client whose messages.create returns a forced tool-use block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = compile_output
    response = MagicMock()
    response.content = [tool_block]
    fake = MagicMock()
    fake.messages.create.return_value = response
    return fake


# ── _build_policy_query ────────────────────────────────────────────────────────

def test_build_policy_query_uses_control_ids():
    exceptions = [{"control_id": "C4"}, {"control_id": "C5"}]
    query = _build_policy_query(exceptions)
    assert "duplicate" in query
    assert "payment block" in query or "segregation" in query


def test_build_policy_query_fallback_when_no_exceptions():
    query = _build_policy_query([])
    assert "accounts payable" in query


# ── _gate_clauses ─────────────────────────────────────────────────────────────

def test_gate_passes_clause_supported_by_chunks():
    chunks = ["A duplicate invoice is defined as the same vendor, same amount within 30 days."]
    clauses = ["A duplicate invoice is defined as the same vendor, same amount within 30 days."]
    supported, flagged = _gate_clauses(clauses, chunks)
    assert len(supported) == 1
    assert len(flagged) == 0


def test_gate_flags_clause_not_in_chunks():
    chunks = ["Invoice processing requires three-way match of PO, GR, and invoice."]
    clauses = ["Payment must be released within 5 business days of the Benford anomaly review."]
    supported, flagged = _gate_clauses(clauses, chunks)
    assert len(supported) == 0
    assert len(flagged) == 1
    assert "clause" in flagged[0]
    assert "reasons" in flagged[0]


def test_gate_short_clause_passes_without_sentences():
    supported, flagged = _gate_clauses(["See policy."], ["AP policy document."])
    assert len(supported) == 1
    assert len(flagged) == 0


# ── run_audit ─────────────────────────────────────────────────────────────────

def _mock_policy_hits():
    return [_make_policy_hit(
        "AP Controls Policy > Duplicate Invoice Check",
        "A duplicate invoice is defined as same vendor, same amount (±EUR 1), within 30 days. "
        "The system auto-blocks; AP Supervisor must review before release.",
    )]


@patch("src.agent.audit.hybrid_search")
@patch("src.agent.audit.get_control_exceptions")
def test_run_audit_separates_data_and_policy_evidence(mock_get_exc, mock_search):
    mock_get_exc.return_value = [_fake_exception()]
    mock_search.return_value = _mock_policy_hits()
    compile_out = {
        "exception_summary": "C4 duplicate invoice detected for vendor Acme.",
        "policy_clauses": [
            "A duplicate invoice is defined as same vendor, same amount within 30 days. "
            "(Source: AP Controls Policy, Duplicate Invoice Check)"
        ],
        "risk_assessment": "High severity; EUR 12,450 exposure.",
        "recommended_action": "Propose a payment hold on INV-2024-089 pending AP Supervisor review (C4).",
        "limitations": "This is an anomaly flag, not proof of fraud or misconduct.",
    }
    result = run_audit(
        vendor="Acme",
        client=_fake_client(compile_out),
    )
    assert not result.p1_unavailable
    assert not result.parse_failed
    report = result.report
    # data_evidence is P1 data — never model-written
    assert len(report.data_evidence) == 1
    assert report.data_evidence[0]["control_id"] == "C4"
    # policy_clauses from LLM + gate
    assert "anomaly" in report.limitations.lower()
    assert "fraud" in report.limitations.lower()


@patch("src.agent.audit.hybrid_search")
@patch("src.agent.audit.get_control_exceptions")
def test_gate_applied_only_to_policy_clauses_not_data(mock_get_exc, mock_search):
    """P1 data never passes through the gate. data_evidence is always returned as-is."""
    raw = _fake_exception(exposure=99999.0)
    mock_get_exc.return_value = [raw]
    mock_search.return_value = _mock_policy_hits()
    compile_out = {
        "exception_summary": "C4 found.",
        "policy_clauses": ["Payment hold requires AP Supervisor approval. (Source: AP Controls Policy)"],
        "risk_assessment": "EUR 99,999 exposure.",
        "recommended_action": "Propose hold.",
        "limitations": "Anomaly flag, not proof of fraud or misconduct.",
    }
    result = run_audit(vendor="Acme", client=_fake_client(compile_out))
    assert result.report.data_evidence[0]["exposure_eur"] == 99999.0
    assert result.report.gate_applied is True


@patch("src.agent.audit.hybrid_search")
@patch("src.agent.audit.get_control_exceptions", side_effect=OpsPerformanceUnavailable("P1 down"))
def test_run_audit_p1_unavailable_flag_set(mock_get_exc, mock_search):
    mock_search.return_value = []
    compile_out = {
        "exception_summary": "No exception data available.",
        "policy_clauses": [],
        "risk_assessment": "No data.",
        "recommended_action": "Manual review required.",
        "limitations": "P1 unavailable. Anomaly flag, not proof of fraud or misconduct.",
    }
    result = run_audit(case_id="INV-001", client=_fake_client(compile_out))
    assert result.p1_unavailable is True
    assert result.report.data_evidence == []


def test_run_audit_raises_when_no_target():
    with pytest.raises(ValueError, match="At least one"):
        run_audit()


# ── API route ─────────────────────────────────────────────────────────────────

def test_audit_route_422_when_no_case_or_vendor():
    resp = client.post("/investigate/audit", json={})
    assert resp.status_code == 422


@patch("src.api.routes.run_audit")
def test_audit_route_returns_structured_response(mock_run_audit):
    from src.agent.audit import AuditReport
    mock_run_audit.return_value = AuditResult(
        report=AuditReport(
            exception_summary="C4 duplicate invoice.",
            data_evidence=[_fake_exception()],
            policy_clauses=["Duplicate invoices are blocked automatically. (Source: AP Controls Policy)"],
            flagged_clauses=[],
            risk_assessment="High; EUR 12,450.",
            recommended_action="Propose payment hold.",
            limitations="Anomaly flag, not proof of fraud or misconduct.",
            gate_applied=True,
        ),
        p1_unavailable=False,
        parse_failed=False,
    )
    resp = client.post("/investigate/audit", json={"vendor": "Acme"})
    assert resp.status_code == 200
    body = resp.json()
    assert "report" in body
    assert body["report"]["exception_summary"] == "C4 duplicate invoice."
    assert len(body["report"]["data_evidence"]) == 1
    assert len(body["report"]["policy_clauses"]) == 1
    assert body["report"]["gate_applied"] is True
    assert body["p1_unavailable"] is False
    assert body["parse_failed"] is False
