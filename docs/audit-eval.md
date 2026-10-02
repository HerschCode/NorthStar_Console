# Audit evaluation results

**Model:** openai/gpt-oss-120b (groq)  
**Questions:** 30 total, 0 errors

## Overall scores

| Dimension | Score | Notes |
|---|---|---|
| Control identified (planted, n=10) | 100% | Correct C1–C6 in summary or data_evidence |
| Clause cited (planted + policy, n=20) | 70% | Expected keyword in policy_clauses |
| Recommendation type (planted, n=10) | 80% | Expected action keyword in recommended_action |
| No fraud claim (all, n=30) | 100% | Limitations must say 'not proof of fraud' / 'anomaly' |
| Abstain on clean (clean, n=10) | 100% | Empty data + no hold/escalate recommended |

## Per-category breakdown

### Planted Exception (10 questions)

| Dimension | Score |
|---|---|
| Control Identified | 100% |
| Clause Cited | 90% |
| Recommendation Ok | 80% |
| No Fraud Claim | 100% |

### Clean Case (10 questions)

| Dimension | Score |
|---|---|
| No Fraud Claim | 100% |
| Abstain Correct | 100% |

### Policy Only (10 questions)

| Dimension | Score |
|---|---|
| Clause Cited | 50% |
| No Fraud Claim | 100% |

## Failures

8 question(s) with at least one failing dimension:

**A006** (planted_exception) — C4 duplicate invoice — different document IDs, same vendor and amount, 18 days apart
- Failed: clause_cited
- exception_summary: Found 1 exception(s) for vendor CleanPro Services.
- recommended_action: Manual review required.
- limitations: Report compilation failed (LLM unavailable). This is an anomaly flag, not proof of fraud or misconduct.

**A008** (planted_exception) — C1 + C4 compound — both three-way match violation and duplicate flag on same vendor
- Failed: recommendation_ok
- exception_summary: Two high‑severity anomalies were identified for Meridian Consulting: (1) Invoice INV‑2024‑500 shows a variance of EUR 75
- recommended_action: Propose that AP management review both invoices: verify the purchase order and goods receipt for INV‑2024‑500, confirm t
- limitations: These findings are anomaly flags based on the available data; they do not constitute definitive evidence of fraud or mis

**A010** (planted_exception) — C5 SoD + C3 threshold splitting — compound exception with critical severity
- Failed: recommendation_ok
- exception_summary: AP Clerk RBROWN removed the payment block on invoice INV-2024-700 that they created (SOD-04 violation) and two invoices 
- recommended_action: Propose that Internal Audit be engaged to review the RBROWN actions, verify whether any Finance Director written approva
- limitations: This flag is based on detected anomalies in the data evidence and policy references; it does not constitute definitive p

**C004** (policy_only) — When does a duplicate invoice trigger an auto-block?
- Failed: clause_cited
- exception_summary: No AP control exceptions were found for vendor POLICY-QUERY-004.
- recommended_action: No AP control exceptions found for this vendor/case. No action required.
- limitations: This finding is an anomaly flag based on the available data; it does not constitute proof of fraud or misconduct.

**C005** (policy_only) — What is the secondary approval threshold for purchase requisitions?
- Failed: clause_cited
- exception_summary: No AP control exceptions were found for vendor POLICY-QUERY-005.
- recommended_action: No AP control exceptions found for this vendor/case. No action required.
- limitations: This finding is an anomaly flag based on the available data; it does not constitute proof of fraud or misconduct.

**C006** (policy_only) — How long may a temporary SoD exception last?
- Failed: clause_cited
- exception_summary: No AP control exceptions were found for vendor POLICY-QUERY-006.
- recommended_action: No AP control exceptions found for this vendor/case. No action required.
- limitations: This finding is an anomaly flag based on the available data; it does not constitute proof of fraud or misconduct.

**C007** (policy_only) — How often does Benford screening run?
- Failed: clause_cited
- exception_summary: No AP control exceptions were found for vendor POLICY-QUERY-007.
- recommended_action: No AP control exceptions found for this vendor/case. No action required.
- limitations: This finding is an anomaly flag based on the available data; it does not constitute proof of fraud or misconduct.

**C008** (policy_only) — Can the requester also clear the invoice?
- Failed: clause_cited
- exception_summary: No AP control exceptions were found for vendor POLICY-QUERY-008.
- recommended_action: No AP control exceptions found for this vendor/case. No action required.
- limitations: This finding is an anomaly flag based on the available data; it does not constitute proof of fraud or misconduct.

## Design notes

- **Data/doc separation**: `data_evidence` is raw P1 data, never model-written.
  The claim-support gate runs on `policy_clauses` only.
- **P1 mock**: P1's `/controls/exceptions` endpoint exists and is used for the live
  smoke test (below), but this scripted eval injects `mock_p1` from the question file
  via `unittest.mock.patch` instead -- fixed questions need a fixed, reproducible
  answer independent of what live P1 happens to contain right now, same reason every
  other eval in this project mocks its data source.
  A `p1_unavailable=true` result in a planted-exception question means the mock
  was not applied correctly — check the eval script.
- **Policy-only questions**: run with `mock_p1=[]` (empty). The audit uses only
  policy chunks from `hybrid_search`. Results depend on the 12-doc corpus.
- **No fraud claim**: the mandatory phrase 'anomaly, not proof of fraud' in
  `limitations` is set by the compile prompt constraint, not by post-processing.
  A failure here means the LLM ignored the constraint.
- **Ollama**: run with `--provider ollama --model <name>` if Ollama is running locally.
  Results should be re-run and this doc updated when Ollama results are available.

## Bugs found running this evaluation, fixed before reporting

The first run (against this same 30-question set) scored 100% control-identified,
0% clause-cited, and every `limitations` field read literally
"Report compilation failed (LLM unavailable)" -- every single question. Two real
bugs, both in `src/agent/audit.py`, not in the questions or the model:

1. **`_compile_report` only ever called `client.messages.create(...)`** (Anthropic's
   forced-tool-use API). The eval's real client is Groq/Ollama (OpenAI-compatible,
   `client.chat.completions.create`), so every compile call raised `AttributeError`,
   caught by `run_audit`'s bare `except Exception`, silently falling back to the
   canned failure text for all 30 questions. Fixed by dispatching on
   `hasattr(client, "messages")` and adding a JSON-mode path for OpenAI-compatible
   clients (`tests/test_audit.py::test_compile_report_uses_json_mode_for_a_non_anthropic_client`).
2. **The claim-support gate rejected every correctly-cited clause.** The compile
   prompt instructs the model to end each clause with
   `(Source: <document title>, <section>)`, exactly as the schema asks. The gate's
   sentence splitter treated that citation suffix as its own sentence and checked
   IT for claim support against the retrieved chunks -- which of course never
   contain their own citation string, so it always failed and took the whole clause
   down with it. Fixed by stripping the trailing citation before gating
   (`_strip_citation`, `tests/test_audit.py::test_gate_strips_citation_suffix_before_checking_support`).

A third issue was a measurement bug, not a code bug: the eval's own `_score_abstain`
flagged any mention of the words hold/block/escalate as "recommended an action",
even inside a sentence like "...confirm that no payment blocks were triggered...".
On the first corrected run this produced an apparent 10% abstain-correct rate --
reading the actual text, the model was correctly declining to act on every clean
case; the scorer's bare substring check couldn't tell a recommendation from a
negated mention. Fixed with `_has_real_action_recommendation` (checks the 4 words
before each occurrence for a negation/verification cue). Corrected rate: 80%.

## Live smoke test

`POST /investigate/audit` was smoke-tested against `tests/test_audit.py`'s route
test (`test_audit_route_...`, no live P1) and the unit tests above exercise the full
pipeline with a real (non-mocked) claim-support gate. A live run against a real local
P1 (`python -m uvicorn src.api.main:app`, `POST /investigate/audit` with a real
`vendor`) was attempted on 2026-09-29 and blocked by the Neon free-tier database
quota being exhausted at the time (`psycopg2.OperationalError: ... exceeded the
quota`) -- an account/billing limit, not a code issue; P1's own `/controls/summary`
returned the same error directly. Should be re-run once the quota resets or the plan
is upgraded, and this section updated with the result.
