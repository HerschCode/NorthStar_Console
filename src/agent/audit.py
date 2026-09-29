"""
AP controls audit investigation mode.

POST /investigate/audit is a deterministic pipeline (no LLM tool-selection loop):
  1. Fetch AP exception data directly from P1 via the AP controls tools
  2. Retrieve relevant policy clauses via hybrid_search
  3. LLM compile (forced tool-use): data + chunks -> structured audit report
  4. Claim-support gate on policy_clauses ONLY — P1 figures in data_evidence
     are not in policy documents and must never be gated against them

This separation (data evidence vs document evidence, gate on doc section only)
is the fix for the "gate isn't on the live agent path" gap: we can now run the
gate without blocking correct P1 figures that wouldn't survive a doc-chunk check.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.tools.ap_controls import get_control_exceptions
from src.retrieval.search import hybrid_search
from src.agent.agent import load_agent_config
from src.evaluation.claim_support import sentence_support, split_sentences
from src.tools.client import OpsPerformanceUnavailable

_SUPPORT_MIN_RECALL = 0.65   # same as grounded_search.py SUPPORT_MIN_RECALL

COMPILE_AUDIT_TOOL = {
    "name": "compile_audit_report",
    "description": "Compile AP controls audit findings into a structured report.",
    "input_schema": {
        "type": "object",
        "properties": {
            "exception_summary": {
                "type": "string",
                "description": "One sentence: what was found and which control (C1–C6) was triggered.",
            },
            "policy_clauses": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Direct quotes or close paraphrases from the policy context, each ending with "
                    "'(Source: <document title>, <section>)'. Only quote what is in the context above."
                ),
            },
            "risk_assessment": {
                "type": "string",
                "description": "Severity level and EUR exposure taken directly from the data evidence.",
            },
            "recommended_action": {
                "type": "string",
                "description": (
                    "One proposed action for human review — a PROPOSAL, not a decision. "
                    "Example: 'Propose a payment hold on <case_id> pending AP Supervisor review, "
                    "citing C4 and Section 4.2 of the AP Controls Policy.' "
                    "Never say the action has been or will be executed."
                ),
            },
            "limitations": {
                "type": "string",
                "description": (
                    "Caveats. Must include: this report flags an anomaly; it is not proof of "
                    "fraud or misconduct. Also note any data gaps (P1 unavailable, no matching records)."
                ),
            },
        },
        "required": [
            "exception_summary", "policy_clauses", "risk_assessment",
            "recommended_action", "limitations",
        ],
    },
}


@dataclass
class AuditReport:
    exception_summary: str
    data_evidence: list[dict] = field(default_factory=list)
    policy_clauses: list[str] = field(default_factory=list)
    flagged_clauses: list[dict] = field(default_factory=list)
    risk_assessment: str = ""
    recommended_action: str = ""
    limitations: str = ""
    gate_applied: bool = True


@dataclass
class AuditResult:
    report: AuditReport
    p1_unavailable: bool = False
    parse_failed: bool = False


def _build_policy_query(exceptions: list[dict]) -> str:
    """Build a hybrid-search query from the control IDs in the exception data."""
    controls = {e.get("control_id") or e.get("control") for e in exceptions if isinstance(e, dict)} - {None}
    parts = []
    if "C1" in controls:
        parts.append("three-way match purchase order invoice goods receipt tolerance")
    if "C2" in controls:
        parts.append("invoice before goods receipt exception approval")
    if "C3" in controls:
        parts.append("approval threshold splitting same vendor requester")
    if "C4" in controls:
        parts.append("duplicate invoice same vendor amount 30 days")
    if "C5" in controls:
        parts.append("payment block removal segregation of duties AP Supervisor")
    if "C6" in controls:
        parts.append("Benford law invoice amount anomaly screening")
    return " ".join(parts) if parts else "accounts payable controls payment block approval policy"


def _gate_clauses(
    clauses: list[str], chunks: list[str]
) -> tuple[list[str], list[dict]]:
    """Apply the claim-support gate to each policy clause.

    Clauses where every sentence passes the gate go to `supported`.
    Failing clauses go to `flagged` with a per-sentence reason so the
    caller can audit which specific claim wasn't in the retrieved text.
    """
    supported: list[str] = []
    flagged: list[dict] = []
    for clause in clauses:
        sentences = split_sentences(clause)
        if not sentences:
            supported.append(clause)
            continue
        clause_reasons: list[str] = []
        for sent in sentences:
            result = sentence_support(sent, chunks)
            ok = (
                result["numbers_supported"]
                and result["key_terms_supported"]
                and result["content_recall"] >= _SUPPORT_MIN_RECALL
            )
            if not ok:
                parts = []
                if result["missing_number_claims"]:
                    parts.append(f"unsupported numbers: {result['missing_number_claims']}")
                if result["missing_key_terms"]:
                    parts.append(f"unsupported terms: {result['missing_key_terms']}")
                if result["content_recall"] < _SUPPORT_MIN_RECALL:
                    parts.append(f"low recall: {result['content_recall']:.0%}")
                clause_reasons.append("; ".join(parts))
        if not clause_reasons:
            supported.append(clause)
        else:
            flagged.append({"clause": clause, "reasons": clause_reasons})
    return supported, flagged


def run_audit(
    case_id: str | None = None,
    vendor: str | None = None,
    config_path: str = "config/agent.yaml",
    client=None,
) -> AuditResult:
    if not case_id and not vendor:
        raise ValueError("At least one of case_id or vendor must be provided.")

    config = load_agent_config(config_path)

    # ── 1. Gather data evidence from P1 ──────────────────────────────────────
    try:
        raw_exceptions = get_control_exceptions(vendor=vendor, top_n=20)
        if case_id and isinstance(raw_exceptions, list):
            filtered = [
                e for e in raw_exceptions
                if isinstance(e, dict) and (
                    e.get("case_id") == case_id
                    or e.get("document_id") == case_id
                    or case_id in str(e.get("evidence", ""))
                )
            ]
            raw_exceptions = filtered or raw_exceptions
        p1_unavailable = False
    except OpsPerformanceUnavailable:
        raw_exceptions = []
        p1_unavailable = True

    # ── 2. Retrieve relevant policy chunks ───────────────────────────────────
    policy_query = _build_policy_query(raw_exceptions)
    try:
        policy_hits = hybrid_search(policy_query, top_k=6)
        chunks = [r.text for r in policy_hits]
        chunk_refs = [r.citation for r in policy_hits]
    except Exception:
        chunks = []
        chunk_refs = []

    # ── 3. LLM compile ───────────────────────────────────────────────────────
    data_text = (
        json.dumps(raw_exceptions[:5], default=str, indent=2)
        if raw_exceptions
        else "No exceptions retrieved (P1 unavailable or no matching records)."
    )
    policy_text = "\n\n---\n\n".join(
        f"[{ref}]\n{chunk}" for ref, chunk in zip(chunk_refs, chunks)
    ) or "No policy clauses retrieved."

    compile_prompt = (
        "You are compiling an AP controls audit report. "
        "Do not add any facts beyond what the data evidence and policy context provide.\n\n"
        f"Audit scope: case_id={case_id!r}, vendor={vendor!r}\n\n"
        f"## Data evidence (P1 controls layer — treat as ground truth, do not rephrase numbers):\n"
        f"{data_text}\n\n"
        f"## Policy context (retrieved from policy documents — only quote what is here):\n"
        f"{policy_text}\n\n"
        "Rules:\n"
        "- policy_clauses must only quote or closely paraphrase the policy context above.\n"
        "- recommended_action must be a PROPOSAL for human review, not an executed decision.\n"
        "- limitations MUST state this is an anomaly flag, not proof of fraud or misconduct."
    )

    report_data: dict | None = None
    parse_failed = False
    try:
        from src.agent.agent import _default_client  # noqa: PLC0415
        _client = client or _default_client()
        resp = _client.messages.create(
            model=config["model"],
            max_tokens=config.get("max_tokens", 1500),
            temperature=0.0,
            messages=[{"role": "user", "content": compile_prompt}],
            tools=[COMPILE_AUDIT_TOOL],
            tool_choice={"type": "tool", "name": "compile_audit_report"},
        )
        tool_blocks = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if tool_blocks:
            report_data = tool_blocks[0].input
    except Exception:
        parse_failed = True

    if report_data is None:
        parse_failed = True
        exc_count = len(raw_exceptions)
        report_data = {
            "exception_summary": (
                f"Found {exc_count} exception(s) for "
                f"{'case ' + case_id if case_id else 'vendor ' + (vendor or '?')}."
                if exc_count else
                "No exception data available (P1 unavailable)."
            ),
            "policy_clauses": [],
            "risk_assessment": f"{exc_count} exception(s) found." if raw_exceptions else "No data.",
            "recommended_action": "Manual review required.",
            "limitations": (
                "Report compilation failed (LLM unavailable). "
                "This is an anomaly flag, not proof of fraud or misconduct."
            ),
        }

    # ── 4. Gate policy_clauses against retrieved policy chunks ───────────────
    raw_clauses: list[str] = report_data.get("policy_clauses") or []
    if chunks and raw_clauses:
        supported_clauses, flagged_clauses = _gate_clauses(raw_clauses, chunks)
    else:
        supported_clauses, flagged_clauses = raw_clauses, []

    return AuditResult(
        report=AuditReport(
            exception_summary=report_data.get("exception_summary", ""),
            data_evidence=raw_exceptions,
            policy_clauses=supported_clauses,
            flagged_clauses=flagged_clauses,
            risk_assessment=report_data.get("risk_assessment", ""),
            recommended_action=report_data.get("recommended_action", ""),
            limitations=report_data.get("limitations", "Anomaly flag, not proof of fraud or misconduct."),
            gate_applied=bool(chunks),
        ),
        p1_unavailable=p1_unavailable,
        parse_failed=parse_failed,
    )
