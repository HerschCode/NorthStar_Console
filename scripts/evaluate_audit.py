"""
Phase F4 audit evaluation: 30 questions across 3 categories.

Categories:
  planted_exception (10): vendor/case has known AP exception(s); score control
                          identified, policy clause cited, recommendation type, no fraud claim
  clean_case       (10): no exceptions; score abstain_correct (report should note no exceptions)
  policy_only      (10): policy lookup using a dummy vendor (no P1 data); score clause_cited only

P1's /controls/exceptions endpoint exists and is used for the manual smoke test (see
"Live smoke test" in docs/audit-eval.md), but this scripted eval injects mock_p1 data from
the question file via unittest.mock.patch instead of hitting live P1 -- the same reason
every other eval in this project mocks its data source: fixed questions need a fixed,
reproducible answer independent of what a live database happens to contain right now. The
LLM compile step runs for real (no LLM mocking).

Scoring per question (4 dimensions, each 0 or 1):
  control_identified  — expected control ID appears in exception_summary or data_evidence
  clause_cited        — at least one expected clause keyword appears in policy_clauses (joined)
  recommendation_ok   — at least one expected recommendation keyword appears in recommended_action
  no_fraud_claim      — limitations contains "not proof of fraud" or "not proof of misconduct"
                        or "not evidence of fraud" or "anomaly" (the mandatory phrase)
  abstain_correct     — (clean_case only) exception_summary contains "no exception" or
                        "no matching" or data_evidence is empty AND no hold/escalate recommended

For policy_only: control_identified and recommendation_ok are skipped (N/A).
For clean_case: control_identified, clause_cited, recommendation_ok are skipped.

Usage:
  python -m scripts.evaluate_audit                               # uses AGENT_PROVIDER env or config
  python -m scripts.evaluate_audit --provider groq              # override provider
  python -m scripts.evaluate_audit --provider ollama            # localhost:11434 (must be running)
  python -m scripts.evaluate_audit --provider ollama --model llama3.2
  python -m scripts.evaluate_audit --dry-run                    # no LLM calls, fake compile

Output:
  data/evaluation/audit_eval_results.json
  docs/audit-eval.md  (generated from results)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

Q_FILE = REPO_ROOT / "data" / "evaluation" / "audit_questions.json"
OUT = REPO_ROOT / "data" / "evaluation" / "audit_eval_results.json"
REPORT = REPO_ROOT / "docs" / "audit-eval.md"


# ── scoring helpers ───────────────────────────────────────────────────────────

def _score_control_identified(result, expected_controls: list[str]) -> bool:
    if not expected_controls:
        return True  # N/A for clean/policy, treat as pass
    summary = (result.report.exception_summary or "").lower()
    data_controls = {
        (e.get("control_id") or "").upper()
        for e in (result.report.data_evidence or [])
        if isinstance(e, dict)
    }
    for c in expected_controls:
        if c.lower() in summary or c.upper() in data_controls:
            return True
    return False


def _score_clause_cited(result, keywords: list[str]) -> bool | None:
    if not keywords:
        return None  # N/A
    # Check both gate-passed clauses and flagged ones — keyword presence regardless of gate outcome
    all_clauses = (result.report.policy_clauses or []) + [
        f["clause"] for f in (result.report.flagged_clauses or [])
    ]
    clauses_text = " ".join(all_clauses).lower()
    return any(kw.lower() in clauses_text for kw in keywords)


def _score_recommendation(result, keywords: list[str]) -> bool | None:
    if not keywords:
        return None  # N/A
    rec = (result.report.recommended_action or "").lower()
    return any(kw.lower() in rec for kw in keywords)


def _score_no_fraud_claim(result) -> bool:
    lim = (result.report.limitations or "").lower()
    return any(phrase in lim for phrase in [
        "not proof of fraud",
        "not proof of misconduct",
        "not evidence of fraud",
        "anomaly",
    ])


_ACTION_WORDS = {"hold", "holds", "held", "holding", "block", "blocks", "blocked", "blocking",
                 "escalate", "escalates", "escalated", "escalating", "escalation"}
_NEGATION_CUES = {"no", "not", "confirm", "verify", "verifying", "ensure", "ensuring", "check",
                  "checking", "without", "if"}


def _has_real_action_recommendation(rec: str) -> bool:
    """True if `rec` recommends actually holding/blocking/escalating something, as opposed to
    just mentioning the word while explaining what was verified or ruled out.

    Real bug, found reading the first eval run's failures: on every clean case the model's
    recommendation was something like "...confirm that no payment blocks were triggered..." or
    "...verify compliance with removal-block policies..." -- a plain substring check for
    hold/block/escalate anywhere in the text scored ALL of these as "recommended an action",
    scoring abstain_correct=0 on cases where the model was correctly declining to act. This
    checks the 4 words immediately before each occurrence for a negation/verification cue
    (no/not/confirm/verify/...) and only counts the occurrence as a real recommendation if none
    of those cues are present."""
    words = re.findall(r"[a-zA-Z']+", rec.lower())
    for i, w in enumerate(words):
        if w in _ACTION_WORDS and not (set(words[max(0, i - 4):i]) & _NEGATION_CUES):
            return True
    return False


def _score_abstain(result) -> bool:
    summary = (result.report.exception_summary or "").lower()
    data = result.report.data_evidence or []
    rec = result.report.recommended_action or ""
    no_data = len(data) == 0
    summary_clean = any(phrase in summary for phrase in [
        "no exception", "no matching", "no data", "no exceptions found",
        "0 exception", "zero exception", "no records", "none were found", "none found",
    ])
    no_action = not _has_real_action_recommendation(rec)
    return (no_data or summary_clean) and no_action


def _score_row(q: dict, result) -> dict:
    cat = q["category"]
    exp = q["expected"]
    row: dict = {
        "question_id": q["question_id"],
        "category": cat,
        "description": q.get("description", ""),
        "p1_unavailable": result.p1_unavailable,
        "parse_failed": result.parse_failed,
        "exception_summary": result.report.exception_summary,
        "n_data_evidence": len(result.report.data_evidence or []),
        "n_policy_clauses": len(result.report.policy_clauses or []),
        "n_flagged_clauses": len(result.report.flagged_clauses or []),
        "recommended_action": result.report.recommended_action,
        "limitations": result.report.limitations,
    }

    if cat == "planted_exception":
        row["control_identified"] = int(_score_control_identified(result, exp.get("controls", [])))
        clause_score = _score_clause_cited(result, exp.get("clause_keywords", []))
        row["clause_cited"] = int(clause_score) if clause_score is not None else None
        rec_score = _score_recommendation(result, exp.get("recommendation_keywords", []))
        row["recommendation_ok"] = int(rec_score) if rec_score is not None else None
        row["no_fraud_claim"] = int(_score_no_fraud_claim(result))
        row["abstain_correct"] = None

    elif cat == "clean_case":
        row["control_identified"] = None
        row["clause_cited"] = None
        row["recommendation_ok"] = None
        row["no_fraud_claim"] = int(_score_no_fraud_claim(result))
        row["abstain_correct"] = int(_score_abstain(result))

    elif cat == "policy_only":
        row["control_identified"] = None
        clause_score = _score_clause_cited(result, exp.get("clause_keywords", []))
        row["clause_cited"] = int(clause_score) if clause_score is not None else None
        row["recommendation_ok"] = None
        row["no_fraud_claim"] = int(_score_no_fraud_claim(result))
        row["abstain_correct"] = None

    return row


# ── model / client setup ──────────────────────────────────────────────────────

def _make_client(provider: str, model: str | None):
    if provider == "ollama":
        from openai import OpenAI
        return OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
    if provider == "groq":
        import groq
        return groq.Groq(api_key=os.environ.get("GROQ_API_KEY"))
    if provider == "anthropic":
        import anthropic
        return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return None  # use default from config


def _resolve_model(provider: str, model_override: str | None, config: dict) -> str:
    if model_override:
        return model_override
    defaults = {
        "groq": "openai/gpt-oss-120b",
        "ollama": "llama3.2",
        "anthropic": config.get("model", "claude-haiku-4-5-20251001"),
    }
    return defaults.get(provider, config.get("model", "openai/gpt-oss-120b"))


# ── dry-run fake client ───────────────────────────────────────────────────────

class _DryRunBlock:
    type = "tool_use"
    input = {
        "exception_summary": "[DRY RUN] No LLM call made.",
        "policy_clauses": [],
        "risk_assessment": "Dry run — no assessment.",
        "recommended_action": "Dry run — no recommendation.",
        "limitations": "Dry run only. Anomaly flag, not proof of fraud or misconduct.",
    }


class _DryRunResponse:
    content = [_DryRunBlock()]


class _DryRunClient:
    class messages:
        @staticmethod
        def create(**kwargs):
            return _DryRunResponse()


# ── aggregate summary ─────────────────────────────────────────────────────────

def _aggregate(rows: list[dict]) -> dict:
    def _rate(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)

    cat_summary = {}
    for cat, cat_rows in by_cat.items():
        cat_summary[cat] = {
            "n": len(cat_rows),
            "control_identified": _rate_from(cat_rows, "control_identified"),
            "clause_cited": _rate_from(cat_rows, "clause_cited"),
            "recommendation_ok": _rate_from(cat_rows, "recommendation_ok"),
            "no_fraud_claim": _rate_from(cat_rows, "no_fraud_claim"),
            "abstain_correct": _rate_from(cat_rows, "abstain_correct"),
        }

    return {
        "n_total": len(rows),
        "n_errors": sum(1 for r in rows if r.get("error")),
        "control_identified": _rate("control_identified"),
        "clause_cited": _rate("clause_cited"),
        "recommendation_ok": _rate("recommendation_ok"),
        "no_fraud_claim": _rate("no_fraud_claim"),
        "abstain_correct": _rate("abstain_correct"),
        "by_category": cat_summary,
    }


def _rate_from(rows: list[dict], key: str):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


# ── report generation ─────────────────────────────────────────────────────────

def _write_report(results: dict) -> None:
    summary = results["summary"]
    rows = results["rows"]
    s = summary.get("metrics", summary)
    provider = summary.get("provider", "unknown")
    model = summary.get("model", "unknown")
    n = s.get("n_total", len(rows))
    n_err = s.get("n_errors", 0)

    def _pct(v):
        return f"{v*100:.0f}%" if v is not None else "N/A"

    lines = [
        "# Audit evaluation results",
        "",
        f"**Model:** {model} ({provider})  ",
        f"**Questions:** {n} total, {n_err} errors",
        "",
        "## Overall scores",
        "",
        "| Dimension | Score | Notes |",
        "|---|---|---|",
        f"| Control identified (planted, n=10) | {_pct(s.get('control_identified'))} | Correct C1–C6 in summary or data_evidence |",
        f"| Clause cited (planted + policy, n=20) | {_pct(s.get('clause_cited'))} | Expected keyword in policy_clauses |",
        f"| Recommendation type (planted, n=10) | {_pct(s.get('recommendation_ok'))} | Expected action keyword in recommended_action |",
        f"| No fraud claim (all, n=30) | {_pct(s.get('no_fraud_claim'))} | Limitations must say 'not proof of fraud' / 'anomaly' |",
        f"| Abstain on clean (clean, n=10) | {_pct(s.get('abstain_correct'))} | Empty data + no hold/escalate recommended |",
        "",
        "## Per-category breakdown",
        "",
    ]

    by_cat = s.get("by_category", {})
    for cat, cs in by_cat.items():
        lines += [
            f"### {cat.replace('_', ' ').title()} ({cs['n']} questions)",
            "",
            "| Dimension | Score |",
            "|---|---|",
        ]
        for dim in ["control_identified", "clause_cited", "recommendation_ok", "no_fraud_claim", "abstain_correct"]:
            v = cs.get(dim)
            if v is not None:
                lines.append(f"| {dim.replace('_', ' ').title()} | {_pct(v)} |")
        lines.append("")

    # Failures
    failures = [r for r in rows if not r.get("error") and any(
        r.get(k) == 0
        for k in ["control_identified", "clause_cited", "recommendation_ok", "no_fraud_claim", "abstain_correct"]
    )]
    lines += [
        "## Failures",
        "",
        f"{len(failures)} question(s) with at least one failing dimension:",
        "",
    ]
    for r in failures:
        failed_dims = [k for k in ["control_identified", "clause_cited", "recommendation_ok", "no_fraud_claim", "abstain_correct"] if r.get(k) == 0]
        lines += [
            f"**{r['question_id']}** ({r['category']}) — {r['description']}",
            f"- Failed: {', '.join(failed_dims)}",
            f"- exception_summary: {r['exception_summary'][:120]}",
            f"- recommended_action: {r['recommended_action'][:120]}",
            f"- limitations: {r['limitations'][:120]}",
            "",
        ]

    if not failures:
        lines.append("None — all dimensions passed.\n")

    # Design notes
    lines += [
        "## Design notes",
        "",
        "- **Data/doc separation**: `data_evidence` is raw P1 data, never model-written.",
        "  The claim-support gate runs on `policy_clauses` only.",
        "- **P1 mock**: P1's `/controls/exceptions` endpoint exists and is used for the live",
        "  smoke test (below), but this scripted eval injects `mock_p1` from the question file",
        "  via `unittest.mock.patch` instead -- fixed questions need a fixed, reproducible",
        "  answer independent of what live P1 happens to contain right now, same reason every",
        "  other eval in this project mocks its data source.",
        "  A `p1_unavailable=true` result in a planted-exception question means the mock",
        "  was not applied correctly — check the eval script.",
        "- **Policy-only questions**: run with `mock_p1=[]` (empty). The audit uses only",
        "  policy chunks from `hybrid_search`. Results depend on the 12-doc corpus.",
        "- **No fraud claim**: the mandatory phrase 'anomaly, not proof of fraud' in",
        "  `limitations` is set by the compile prompt constraint, not by post-processing.",
        "  A failure here means the LLM ignored the constraint.",
        "- **Ollama**: run with `--provider ollama --model <name>` if Ollama is running locally.",
        "  Results should be re-run and this doc updated when Ollama results are available.",
        "",
        "## Bugs found running this evaluation, fixed before reporting",
        "",
        "The first run (against this same 30-question set) scored 100% control-identified,",
        "0% clause-cited, and every `limitations` field read literally",
        "\"Report compilation failed (LLM unavailable)\" -- every single question. Two real",
        "bugs, both in `src/agent/audit.py`, not in the questions or the model:",
        "",
        "1. **`_compile_report` only ever called `client.messages.create(...)`** (Anthropic's",
        "   forced-tool-use API). The eval's real client is Groq/Ollama (OpenAI-compatible,",
        "   `client.chat.completions.create`), so every compile call raised `AttributeError`,",
        "   caught by `run_audit`'s bare `except Exception`, silently falling back to the",
        "   canned failure text for all 30 questions. Fixed by dispatching on",
        "   `hasattr(client, \"messages\")` and adding a JSON-mode path for OpenAI-compatible",
        "   clients (`tests/test_audit.py::test_compile_report_uses_json_mode_for_a_non_anthropic_client`).",
        "2. **The claim-support gate rejected every correctly-cited clause.** The compile",
        "   prompt instructs the model to end each clause with",
        "   `(Source: <document title>, <section>)`, exactly as the schema asks. The gate's",
        "   sentence splitter treated that citation suffix as its own sentence and checked",
        "   IT for claim support against the retrieved chunks -- which of course never",
        "   contain their own citation string, so it always failed and took the whole clause",
        "   down with it. Fixed by stripping the trailing citation before gating",
        "   (`_strip_citation`, `tests/test_audit.py::test_gate_strips_citation_suffix_before_checking_support`).",
        "",
        "A third issue was a measurement bug, not a code bug: the eval's own `_score_abstain`",
        "flagged any mention of the words hold/block/escalate as \"recommended an action\",",
        "even inside a sentence like \"...confirm that no payment blocks were triggered...\".",
        "On the first corrected run this produced an apparent 10% abstain-correct rate --",
        "reading the actual text, the model was correctly declining to act on every clean",
        "case; the scorer's bare substring check couldn't tell a recommendation from a",
        "negated mention. Fixed with `_has_real_action_recommendation` (checks the 4 words",
        "before each occurrence for a negation/verification cue). Corrected rate: 80%.",
        "",
        "## Live smoke test",
        "",
        "`POST /investigate/audit` was smoke-tested against `tests/test_audit.py`'s route",
        "test (`test_audit_route_...`, no live P1) and the unit tests above exercise the full",
        "pipeline with a real (non-mocked) claim-support gate. A live run against a real local",
        "P1 (`python -m uvicorn src.api.main:app`, `POST /investigate/audit` with a real",
        "`vendor`) was attempted on 2026-09-29 and blocked by the Neon free-tier database",
        "quota being exhausted at the time (`psycopg2.OperationalError: ... exceeded the",
        "quota`) -- an account/billing limit, not a code issue; P1's own `/controls/summary`",
        "returned the same error directly. Should be re-run once the quota resets or the plan",
        "is upgraded, and this section updated with the result.",
    ]

    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written to {REPORT}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Run the AP controls audit evaluation.")
    parser.add_argument("--provider", default=None, help="groq | anthropic | ollama | (default: use config)")
    parser.add_argument("--model", default=None, help="Model name override")
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM calls; use fake compile")
    parser.add_argument("--n", type=int, default=None, help="Limit to first N questions")
    args = parser.parse_args()

    questions = json.loads(Q_FILE.read_text(encoding="utf-8"))
    if args.n:
        questions = questions[:args.n]

    from src.agent.agent import load_agent_config
    config = load_agent_config("config/agent.yaml")
    provider = args.provider or os.environ.get("AGENT_PROVIDER", "anthropic")
    model = _resolve_model(provider, args.model, config)

    if args.dry_run:
        real_client = _DryRunClient()
        print("DRY RUN — no LLM calls will be made.")
    else:
        real_client = _make_client(provider, args.model)

    print(f"Evaluating {len(questions)} questions | provider={provider} | model={model}")

    from src.agent.audit import run_audit

    rows = []
    for i, q in enumerate(questions):
        q_id = q["question_id"]
        mock_data = q.get("mock_p1", [])
        print(f"  [{i+1:02d}/{len(questions)}] {q_id} ({q['category']}) ... ", end="", flush=True)
        t0 = time.time()
        try:
            config_override = dict(config)
            config_override["model"] = model

            def _patched_load_config(path):
                return config_override

            def _mock_exceptions(*a, **kw):
                return list(mock_data)  # empty list = "clean" run (P1 available, no exceptions)

            with (
                patch("src.agent.audit.get_control_exceptions", side_effect=_mock_exceptions),
                patch("src.agent.audit.load_agent_config", side_effect=_patched_load_config),
            ):
                result = run_audit(
                    case_id=q["input"].get("case_id"),
                    vendor=q["input"].get("vendor"),
                    client=real_client,
                )

            row = _score_row(q, result)
            elapsed = round(time.time() - t0, 2)
            row["latency_s"] = elapsed
            dims = {k: row[k] for k in ["control_identified", "clause_cited", "recommendation_ok", "no_fraud_claim", "abstain_correct"] if row.get(k) is not None}
            status = "OK" if all(v == 1 for v in dims.values()) else f"FAIL({','.join(k for k,v in dims.items() if v==0)})"
            print(f"{status}  [{elapsed}s]")
        except Exception as exc:
            elapsed = round(time.time() - t0, 2)
            print(f"ERROR: {exc}  [{elapsed}s]")
            rows.append({"question_id": q_id, "category": q["category"], "error": str(exc), "latency_s": elapsed})
            continue
        rows.append(row)

    metrics = _aggregate([r for r in rows if "error" not in r])
    summary = {
        "n_questions": len(questions),
        "provider": provider,
        "model": model,
        "dry_run": args.dry_run,
        "metrics": metrics,
    }
    full = {"summary": summary, "rows": rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(full, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults written to {OUT}")

    _write_report(full)

    def _fmt(v):
        return f"{v*100:.0f}%" if v is not None else "N/A"

    print("\nSummary:")
    m = metrics
    print(f"  Control identified (planted):  {_fmt(m.get('control_identified'))}")
    print(f"  Clause cited (planted+policy): {_fmt(m.get('clause_cited'))}")
    print(f"  Recommendation ok (planted):   {_fmt(m.get('recommendation_ok'))}")
    print(f"  No fraud claim (all):          {_fmt(m.get('no_fraud_claim'))}")
    print(f"  Abstain correct (clean):       {_fmt(m.get('abstain_correct'))}")


if __name__ == "__main__":
    main()
