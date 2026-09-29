# Audit evaluation results

**Model:** openai/gpt-oss-120b (groq)  
**Questions:** 30 total, 0 errors

## Overall scores

| Dimension | Score | Notes |
|---|---|---|
| Control identified (planted, n=10) | 100% | Correct C1–C6 in summary or data_evidence |
| Clause cited (planted + policy, n=20) | 65% | Expected keyword in policy_clauses |
| Recommendation type (planted, n=10) | 70% | Expected action keyword in recommended_action |
| No fraud claim (all, n=30) | 100% | Limitations must say 'not proof of fraud' / 'anomaly' |
| Abstain on clean (clean, n=10) | 80% | Empty data + no hold/escalate recommended |

## Per-category breakdown

### Planted Exception (10 questions)

| Dimension | Score |
|---|---|
| Control Identified | 100% |
| Clause Cited | 80% |
| Recommendation Ok | 70% |
| No Fraud Claim | 100% |

### Clean Case (10 questions)

| Dimension | Score |
|---|---|
| No Fraud Claim | 100% |
| Abstain Correct | 80% |

### Policy Only (10 questions)

| Dimension | Score |
|---|---|
| Clause Cited | 50% |
| No Fraud Claim | 100% |

## Failures

12 question(s) with at least one failing dimension:

**A001** (planted_exception) — C4 duplicate invoice — same vendor, same amount, 28 days apart
- Failed: clause_cited
- exception_summary: Potential duplicate invoice detected for Acme Supplies: INV-2024-089 matches INV-2024-072, same vendor, EUR 12,450 amoun
- recommended_action: AP team should review the duplicate alert, verify that INV-2024-089 and INV-2024-072 are indeed the same invoice, and if
- limitations: This finding is an anomaly flag based on policy criteria and the provided data evidence; it does not constitute definiti

**A003** (planted_exception) — C3 approval-threshold splitting — two invoices from same vendor totalling $19,800 within 7 days
- Failed: clause_cited
- exception_summary: High‑severity potential splitting detected: two invoices (INV-2024-301 EUR 9,900 and INV-2024-302 EUR 9,850) submitted b
- recommended_action: Escalate the case to Internal Audit within 2 business days as mandated by Section 6.1; the AP Manager should place a pay
- limitations: This is an anomaly flag based on policy indicators and the provided data; it does not constitute definitive proof of fra

**A007** (planted_exception) — C2 invoice before goods receipt — invoice posted 5 days before GR
- Failed: recommendation_ok
- exception_summary: Invoice INV-2024-355 (EUR 22,000) was posted on 2024-08-01, five days before the Goods Receipt was recorded on 2024-08-0
- recommended_action: PROPOSAL: AP Manager should verify whether a payment block was applied to INV-2024-355, obtain and document a business j
- limitations: This flag identifies a control anomaly based on the available data; it does not constitute proof of fraud or misconduct.

**A008** (planted_exception) — C1 + C4 compound — both three-way match violation and duplicate flag on same vendor
- Failed: recommendation_ok
- exception_summary: Two high‑severity anomalies were detected for Meridian Consulting: (1) Invoice INV‑2024‑500 shows a variance of EUR 750 
- recommended_action: PROPOSAL: AP manager to review and confirm the payment block for INV‑2024‑500 due to tolerance breach; investigate the r
- limitations: This report flags anomalies based on policy criteria and exposure data; it does not constitute proof of fraud or miscond

**A010** (planted_exception) — C5 SoD + C3 threshold splitting — compound exception with critical severity
- Failed: recommendation_ok
- exception_summary: AP Clerk RBROWN both created invoice INV-2024-700 and removed its payment block (SOD-04 violation) and submitted two inv
- recommended_action: Propose that Internal Audit be engaged to review RBROWN’s activities, that the removal of the payment block be reversed 
- limitations: This finding is an anomaly flag based on the provided evidence and policy; it does not constitute definitive proof of fr

**B007** (clean_case) — Clean vendor — invoices spread over 60 days, no duplicate pattern
- Failed: abstain_correct
- exception_summary: No AP control exceptions were found for vendor Trusted Parts Co.
- recommended_action: Continue routine monitoring of AP transactions for Trusted Parts Co. and periodically review compliance with the approva
- limitations: This finding is an anomaly flag based on the available data; it does not constitute proof of fraud or misconduct.

**B009** (clean_case) — Clean vendor — multiple invoices, all below $10k, different requesters
- Failed: abstain_correct
- exception_summary: No AP control exceptions were found for vendor TechSupply Direct.
- recommended_action: Document the finding and retain for periodic review; consider confirming that the segregation‑of‑duties controls (e.g., 
- limitations: This is an anomaly flag based on the provided data evidence; it does not constitute proof of fraud or misconduct.

**C005** (policy_only) — What is the secondary approval threshold for purchase requisitions?
- Failed: clause_cited
- exception_summary: No AP control exceptions were found for case/vendor POLICY-QUERY-005.
- recommended_action: Maintain routine monitoring of AP transactions and periodic review of compliance with the documented controls, including
- limitations: This is an anomaly flag based on the available data; it does not constitute proof of fraud or misconduct.

**C006** (policy_only) — How long may a temporary SoD exception last?
- Failed: clause_cited
- exception_summary: No AP control exceptions were found for case/vendor POLICY-QUERY-006.
- recommended_action: Continue routine monitoring of AP transactions and periodic review of control compliance; no immediate remediation requi
- limitations: This finding is an anomaly flag based on the available data; it does not constitute proof of fraud or misconduct.

**C007** (policy_only) — How often does Benford screening run?
- Failed: clause_cited
- exception_summary: No AP control exceptions were found for case/vendor POLICY-007.
- recommended_action: Maintain routine monitoring of AP transactions and periodic compliance reviews; no immediate remediation is required but
- limitations: This is an anomaly flag based on the available data; it does not constitute proof of fraud or misconduct.

**C008** (policy_only) — Can the requester also clear the invoice?
- Failed: clause_cited
- exception_summary: No AP control exceptions were found for case/vendor POLICY-QUERY-008.
- recommended_action: Continue routine monitoring of AP transactions and periodic review of segregation‑of‑duties compliance; no immediate rem
- limitations: This finding is an anomaly flag based on the available data; it does not constitute proof of fraud or misconduct.

**C009** (policy_only) — Who approves duplicate-alert resolution?
- Failed: clause_cited
- exception_summary: No AP control exceptions were found for case/vendor POLICY-QUERY-009.
- recommended_action: Maintain routine monitoring of AP transactions and schedule periodic reviews to ensure continued adherence to the contro
- limitations: This is an anomaly flag indicating no detected exceptions; it does not constitute proof of fraud or misconduct.

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
