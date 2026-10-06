# Northstar threat model (whole system)

Scope: browser → console → **gateway (P3)** → assistant (P2) → performance API (P1) → database/model files, plus the CI/CD and model
supply chain. The gateway's own STRIDE analysis is in [`services/gateway/SECURITY.md`](../services/gateway/SECURITY.md); the red-team report is
[`services/gateway/reports/redteam-2026-09.md`](../services/gateway/reports/redteam-2026-09.md). This page connects them and says where nothing covers a risk.

**Status words:** *tested* = an automated test or measured evaluation exists; *designed* = implemented but not independently exercised;
*gap* = not covered. No independent penetration test has been done; the red-team was run by the author with standard scanners and an LLM attacker.

## Trust boundaries
| # | Boundary | Rule |
|---|---|---|
| B1 | Internet → gateway | The only public API. Identity and role come from a trusted proxy header when enforced (`GATEWAY_REQUIRE_IDENTITY`), never from the request body. |
| B2 | Gateway → assistant → performance API | Private. Service-to-service Google ID tokens (`AUTH_MODE=google_id_token`) in the cloud design, API keys locally. |
| B3 | Agent → tools | Every tool call is authorised by the action firewall (default deny, human approval for writes, no self-approval, taint check). |
| B4 | Model files → process | sha256 against the registry/manifest, then a restricted unpickler (`src/mlops/safe_load.py`). |
| B5 | Source → deployed image | Hash-locked dependencies, SHA-pinned actions, SBOM per release, secret/IaC scanning in CI. |

## OWASP Top 10 for LLM applications (2025)
| ID | Risk | Controls in this repo | Status |
|---|---|---|---|
| LLM01 | Prompt injection | Text pipeline (normalisation, rules, classifier), action firewall (policy, taint, approval). Text detectors are weak on indirect injection (ROC-AUC 0.37–0.68 on InjecAgent/BIPIA); the defence for that class is the firewall. | tested (limits stated); RT-05 open |
| LLM02 | Sensitive information disclosure | PII redaction/pseudonymization (Indian identifiers), response checks, role-based detokenization. Confidential data from a *trusted* tool can still reach a write's arguments (RT-12). | tested; RT-12 open by design |
| LLM03 | Supply chain | Hash-locked dependencies, SHA-pinned actions, SBOM, Semgrep/Trivy/gitleaks, model manifest + restricted unpickler for P1 models, model integrity check in the gateway. No artifact *signing* yet. | tested; signing gap |
| LLM04 | Data and model poisoning | Retraining only through the promotion gate (metric, calibration, degenerate-target and leakage checks) with audit log and rollback. Corpus uploads (`POST /documents`) require the admin role, but uploaded content is not scanned for poisoning. | partial; corpus poisoning gap |
| LLM05 | Improper output handling | No `innerHTML`/`dangerouslySetInnerHTML` anywhere in `apps/console/src` (React escapes text); a stored XSS in the gateway's own dashboard was found in the red-team and fixed with a retest. | tested |
| LLM06 | Excessive agency | Default-deny tool policy, every write held for a human, requester cannot approve own request, session write caps. Known misses (cross-call state, amounts) are pinned as strict xfail tests. | tested; documented misses |
| LLM07 | System prompt leakage | Response checks; extraction prompts are part of the (author-written) attack corpus. Not independently verified that no secret ever enters a prompt. | tested (author-written cases) |
| LLM08 | Vector and embedding weaknesses | Retrieval benchmark and OOD abstention measured; no access control per document (single corpus). | partial |
| LLM09 | Misinformation | Evidence-grounded answers, claim-support gate (optional polarity check), provenance badges on every number, "insufficient data" behaviour. Gate still lets some wrong-fact answers through (16–25% on the labelled set). | tested; residual rate stated |
| LLM10 | Unbounded consumption | Prompt/body size limits, per-IP and per-session rate limits, daily AI allowance per identity and overall, spend guard, cloud budget guard and max-instances. | tested / designed (cloud part unapplied) |

## MITRE ATLAS (selected)
AML.T0051 (LLM prompt injection) → LLM01 controls; AML.T0057 (LLM data leakage) → LLM02; AML.T0010 (ML supply chain compromise) → B4/B5 controls;
AML.T0020 (poison training data) → promotion gate. The red-team report carries the verified technique IDs per finding.

## Classical-ML risks (the P1 model)
* **Unsafe deserialisation** of `.joblib`: mitigated by manifest + restricted unpickler; tested with a malicious pickle.
* **Silent quality regression or leakage** (a past bug: calibration fitted on training rows): mitigated by the held-out gate and the "suspicious AUC jump" review rule.
* **Distribution shift**: PSI drift report, retrain trigger, conformal abstention (guarantee breaks under shift; `tests/test_conformal.py` shows it).

## Known gaps (priority order)
1. Sign model artifacts and images (cosign/Sigstore) and verify at deploy: needs a registry/CI identity, not done.
2. Independent test of the gateway (someone other than the author).
3. RAG corpus poisoning and per-document access control.
4. Cross-call state in the action firewall (RT findings), and amount/threshold checks on payment tools.
5. Cloud controls (private invoker, budget guard) are written and policy-tested offline but **never applied**.
