# Production-Readiness Review (Phase 31)

Four perspectives, answered honestly against the actual code -- "yes," "no," and "partially" are
all acceptable answers here. The point is knowing which, not making every answer yes.

## Data
| Question | Answer |
|---|---|
| Can data be regenerated? | **Yes.** `scripts/run_pipeline.py` is idempotent (Phase 5) -- rerunning against the same source produces the same end state. The golden dataset (Phase 27) is fully synthetic and version-controlled. |
| Can bad input be detected? | **Yes.** `data_contract.py` fails fast on schema-shape violations; `validate_input.py` flags content issues; `data_quality.py` reports duplicates, missing fields, out-of-order events (with a real bug fixed in that last check -- Phase 9). |
| Are metrics reproducible? | **Yes, and independently verified.** Phase 27's golden dataset proves every core metric against hand calculation, not just internal consistency. |
| Is provenance documented? | **Yes.** `docs/data-contract.md` explicitly separates real (BPI 2019) from synthetic (SLA rules, process model, supplier context) data. |

## Application
| Question | Answer |
|---|---|
| Can APIs fail gracefully? | **Yes** for the endpoints that exist (404s with detail, 503 when no model is trained). **Not yet exercised under real failure** -- no test stops a live dependency mid-request the way `operations-assistant`'s Phase 23 does for its own API. |
| Are inputs validated? | **Yes** at the API boundary (Pydantic/FastAPI query param types); analytics functions raise clearly on missing required columns (e.g. `supplier_analysis.py` on a dataset with no `supplier_id`). |
| Are dependencies isolated? | **Yes for authentication now** (`src/api/auth.py`, added in a later session -- every endpoint except `GET /health` requires a matching `X-API-Key`). Least-privilege DB roles still aren't applied automatically (documented as a manual admin step, deliberately). CORS is properly restricted. |

## AI (the ML component -- SLA-risk model)
| Question | Answer |
|---|---|
| Can the model hallucinate? | Not applicable in the LLM sense -- this is a classifier, not a generative model. It can be **wrong** (false positives/negatives), which is why `docs/ml-model.md` discusses the precision/recall trade-off explicitly rather than reporting only accuracy. |
| Is the model's behavior explainable? | **Partially.** `src/ml/explain.py` gives per-prediction feature importance, but it's explicitly documented as a lightweight approximation, not real SHAP -- named as a limitation, not oversold. |
| Is the training process sound? | **Yes** -- time-based split (not random), explicitly to avoid leaking future information into training for this temporal process (Phase 4). |

## Operations
| Question | Answer |
|---|---|
| Can I see what happened? | **Yes.** Structured JSON logging (Phase 10) with per-stage timing; `pipeline_runs` audit table (Phase 5) surfaced via `GET /observability/pipeline-runs`. |
| Can I diagnose failure? | **Yes, for pipeline failures** -- `pipeline_runs.error_message` captures the actual exception. **Not yet proven for API failures under real load** -- no chaos/failure testing exists here the way it does in the companion project. |
| Can someone reproduce deployment? | **Was NO until Phase 30, now yes.** Every documented script invocation (`python scripts/run_pipeline.py`) was actually broken -- `ModuleNotFoundError` on the very first import, for anyone following the README exactly as written, since Phase 0. Found by actually running the benchmark script rather than reading the code. Fixed (`scripts/__init__.py`, `python -m scripts.foo` everywhere). This is the single most consequential finding in this entire review -- everything else in this doc assumes the system runs at all, and until Phase 30 it didn't, for the most basic possible reason. |
| Does the API scale past current load? | **No caching layer exists.** Every metrics endpoint re-reads and recomputes from the full underlying tables on every request (`src/api/db.py`'s `load_cases()`/`load_events()`) -- confirmed in code, not assumed. Real latency numbers were measured (`docs/api-latency.md`), but from a development machine with a noticeably worse network path to Neon than Render's own -- the absolute numbers there aren't trustworthy for a production claim yet, only the no-caching finding is. Adding a cache (even a simple TTL'd in-memory one, given how rarely the underlying data changes between pipeline runs) is the concrete next step, not built here. |

## What this review changes, concretely
Nothing new was built by writing this doc -- its value is in cross-referencing everything already
built against a fixed checklist and finding the gaps between "the code exists" and "the code is
known to work," which is exactly what caught the Phase 30 script-invocation bug in the first
place (this review's own "reproduce deployment" question is what prompted actually running the
benchmark command as a real user would, rather than importing it from within a test).
