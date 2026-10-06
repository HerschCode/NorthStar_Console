# Northstar: complete feature list

Status labels: **built** = in the code and tested; **written** = code exists but has not been run against a live cloud;
**measured** / **simulated** follow the provenance badges used in the console. Limits are stated next to each item.

## Console (`apps/console`)
- Overview: KPIs with provenance badges, executive brief traced to facts, breach trend with 95% bands, risk funnel, risk × value, where time goes, control health, confidence panel
- Action Center: expected-loss queue, filters, natural-language filter, bulk investigate
- Case 360: drivers (descriptive, not SHAP), timeline with idle gaps, AP exceptions, ask, propose
- Suppliers (Wilson intervals, ranked by lower bound, quadrant), Risk Map, Process Mining (stage/activity graph, median/P75/P90/breach contribution)
- AP Controls (control C6 shown as not valid), Working Capital (discount slider labelled as an assumption), ROI & Uplift (model vs rules vs random)
- Interventions: review/approval with separation of duties, status timeline, outcome entry
- Ask Northstar and Investigations: claim-level support marks, evidence drawer, Markdown/PDF export
- AI Security, Tool Permissions, Approvals, Attack Lab (live step timeline, defenses on/off), Audit Log
- Observability: one trace across gateway → assistant → P1 → model, with cost; Model Health, Data Quality, Lineage, Architecture, Experiments (failed, leaked and tied results shown), Evidence & Limitations
- Command palette (Ctrl/⌘-K); replay-clock date picker re-queries everything; demo sign-in with roles
- Free-model quota status (which limit, when it clears); provenance badge on every API number
- Quality: typed clients generated from pinned OpenAPI specs, 26 unit + 23 Playwright tests, axe (no serious/critical on 15 pages), no horizontal scroll at 375 px, Lighthouse a11y/best-practices 100 (performance 73–90, stated)
- Cloud: unprivileged nginx Dockerfile, manual deploy workflow via workload identity federation (**written**, not run)

## Performance service, P1 (`services/performance`)
- Ingestion of BPI 2019 procurement event log; staging and analytics schemas; data contract and data-quality checks
- Analytical SQL: 10 window-function queries with EXPLAIN before/after
- dbt: staging → intermediate → marts, 30 tests, lineage, CI job; parity-checked ML feature source
- Prefect flow: ingest → DQ gate → dbt → retrain gate → train → promote only if better by a margin → report
- Models: RF / LR / GB comparison, temporal split, held-out calibration, SHAP, ablations, bootstrap CIs, hyperparameter tuning (time-series CV) with MLflow; sequence models (GRU/LSTM) served via ONNX for early warning
- Evaluation variants: realistic targets, creation-time-only, prefix model, external validation on BPI 2012, model vs simple rules
- Drift: per-feature PSI, prediction drift, retrain trigger and a scheduled retrain check
- Process mining: conformance, bottlenecks, cycle time, SLA risk distribution; supplier scorecard
- AP controls: three-way match, threshold splitting, duplicate invoice, payment-block override, Benford screen (evaluated on planted anomalies); working-capital metrics
- Intervention ledger and ROI (**simulated** effects), uplift estimators (T/X-learner, Qini) validated on semi-synthetic data
- Ops: FastAPI, per-client API keys, read-only DB role, Prometheus `/metrics`, structured logs; classic `/dashboard`; BigQuery schema (**written**)
- Documented bugs found and fixed: calibration fitted on training rows; truncated cases labelled "not breached"

## MLOps and data engineering additions (`services/performance`)
- Model registry (immutable versions, champion/challenger aliases, audit log, rollback), promotion gate (min test rows, AUC gain, Brier/ECE non-regression, degenerate-target and suspicious-jump review), retrain cycle, shadow comparison, generated model cards
- Restricted model loading (sha256 check + allowlisted unpickler), `models/MANIFEST.sha256` verified in CI
- Conformal prediction sets with abstention, leakage-safe group+temporal splits, calibration/lift/expected-loss metrics with group bootstrap CIs
- Partitioned Parquet lakehouse queried with DuckDB; replayable event log with idempotent, checkpointed consumer and incremental case metrics; Airflow DAG (written, not run)
- Offline power planner for a two-arm binary breach-outcome trial, with the normal-approximation assumptions and clustering/attrition limitations documented

## Assistant service, P2 (`services/assistant`)
- Hybrid retrieval (BM25 + semantic, RRF) with optional cross-encoder rerank, over 12 policy/SOP documents; retrieval benchmark (165 questions)
- Answer gate: claim-support check (default), NLI gate (corrected pair order), LLM-judge comparison, evaluation on public RAGTruth data
- Agent with five interchangeable providers (Groq, Anthropic, Gemini, LangChain, LangGraph with parallel tool dispatch), tool-selection evals (25-question smoke, 101-question v2)
- Optional polarity check for the answer gate (`GATE_POLARITY=1`), measured trade-off and paired bootstrap intervals in `reports/polarity_eval.json` (labels are not independently annotated)
- Runtime grounding report on every answer; conversational context; semantic cache (calibrated); cost estimator; free-model quota handling
- Investigations and intervention ledger, evidence drawer data, MCP server, document upload, API-key auth, rate limiting, observability and load test
- Fine-tuning experiments (embeddings, generation, tool selection) with results written up, including negative ones

## Gateway service, P3 (`services/gateway`)
- Text pipeline: limits, PII redaction/pseudonymization (Indian identifiers, optional Presidio), normalisation, rules + 2 MB classifier, optional ONNX student, response checks, session checks, adaptive threshold, model-integrity (SHA-256 manifest)
- Action firewall: default-deny policy, role/argument rules, human approval for every write, no self-approval, taint check, finance (AP) controls, stdio MCP proxy
- Console API (`/v1`): identity, allow-listed read-only passthrough to P1, spend/daily allowance, audit, governance history, live demo and dashboard
- Red-team: garak, promptfoo, adaptive LLM attacker, mutation attacker; 12-finding report mapped to OWASP LLM 2025 and MITRE ATLAS
- Supply chain: hash-locked dependencies, SHA-pinned actions, SBOM per release, Semgrep/Trivy/gitleaks in CI
- Honest baselines against ProtectAI DeBERTa and Prompt Guard 2; retraining/recalibration flows

## Infrastructure (`infra`)
- Terraform: 10 modules, 2 root modules (bootstrap applied by a person, dev applied by CI), ten key-less service accounts, ID-token service-to-service auth, only the gateway public
- 21 policy-as-code rules, 104 policy unit tests, 20 mutation tests on real plans, checkov/tflint/Trivy clean
- Cost guardrails (budget alerts, max-instances, scale-to-zero, plan rules against paid resources), uptime checks that pass only on HTTP 403
- Generated identity map and docs, pinned tools; **written and verified offline, never applied**
