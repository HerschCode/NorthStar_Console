# Procurement Process Intelligence & SLA Triage

![Tests](https://github.com/HerschCode/operations-performance/actions/workflows/test.yml/badge.svg)
![dbt](https://github.com/HerschCode/operations-performance/actions/workflows/dbt.yml/badge.svg)

**Live dashboard:** <https://operations-performance.onrender.com/dashboard> ([what it shows](dashboard/README.md)) · **Live API:** <https://operations-performance.onrender.com/docs> (Neon Postgres, real BPI 2019 procurement
data; free tier, the first request may be slow to wake). A conversational layer over the same data lives
in [`operations-assistant`](https://operations-assistant.onrender.com).

## Headline

> **PO-isolated SLA-risk evaluation: 0.76–0.87 ROC-AUC on realistic targets; creation-time-only 0.64–0.87.**

This is a **case-progress-aware risk evaluation, not a validated creation-time predictor.** Full-case
features are only available as a case progresses. The full-feature and creation-time analyses below hold
out whole purchase orders across a temporal boundary; 1,708 measurable cases remain in the latest holdout.
The configured SLA label still
has a 96.3% holdout breach rate, so its high ROC-AUC is not a useful headline. The realistic-target
figures below are separate percentile-label experiments, not the currently deployed model's measured
performance. Details and every caveat:
[`docs/evaluation.md`](docs/evaluation.md).

| Setting | ROC-AUC | Where |
|---|---|---|
| Full-case features, p50/p75 targets (holdout base rates 54.5% / 24.5%) | **0.763–0.865** | [`less-degenerate-target.md`](docs/less-degenerate-target.md) |
| Creation-time-only features, same targets/models | 0.641–0.869 | [`prediction-time-availability.md`](docs/prediction-time-availability.md) |
| Prefix (first k events) RF | 0.59–0.82 | [`prefix-model-bpi2019.md`](docs/prefix-model-bpi2019.md) |
| GRU ensemble on the first k events (served, `/early-risk`) | 0.63–0.83 | [`sequence-model.md`](docs/sequence-model.md) |
| Independent log (BPI 2012), first 3 events | 0.77–0.92 | [`external-validation-bpi2012.md`](docs/external-validation-bpi2012.md) |

*These current ranges use purchase-order-isolated temporal holdouts and folds. Creation-time-only results
vary substantially by target and model; they are not a single expected production score.*
*Prefix, GRU, and BPI 2012 rows are separate older experiments and have not yet been rerun with PO-grouped
validation; do not compare them directly to the updated full-case and creation-time rows.*

**Simple-rule comparison:** on the PO-isolated holdout, the existing model is statistically tied with
order-value ranking at every tested share. Adding supplier-workload features produces a 30% precision of
0.490 vs 0.330 for order value (cluster-bootstrap difference CI [0.047, 0.276]); this variant is not the
currently deployed model. Intervals are wide, especially at small targeting shares. Supplier history now
uses only cases completed before the scored case starts. Full results and limitations:
[`docs/model-vs-rules.md`](docs/model-vs-rules.md). Effect sizes elsewhere are assumptions (SIMULATION).

**Bug found and fixed (2026-09-25):** the served model's probability calibration had been fitted on the same rows
the forest trained on, cutting its ranking ROC-AUC from 0.986 to 0.665 while `meta.json` reported the raw
forest's score. Now calibrated on a held-out temporal slice; served ROC-AUC = raw. See
[`docs/calibration.md`](docs/calibration.md).

**Label bug found and fixed (2026-09-28):** 104 truncated cases with zero measured duration (96 single-event) were
labelled "not breached" in every breach rate and in the model's training labels. They are now excluded
(`evaluate_sla`, dbt `int_case_sla_scored`), the model retrained on 2,896 cases, and every number on this page
regenerated. It mattered most for the rule comparison above: those cases had made one supplier look perfect.

## What this is

An operations-analytics platform for a Procure-to-Pay process (fictional client *Northstar Manufacturing*):
event-log ingestion, SQL process mining, an SLA-breach risk model with calibrated probabilities, a FastAPI
service, the MLOps around it (dbt, Prefect, drift monitoring, an intervention ROI ledger), and an
accounts-payable controls layer (three-way match, threshold splitting, duplicate invoice, payment-block
override, Benford screen) with working-capital metrics, on real match-type and order-value fields BPI 2019
carries but the original sample dropped. The public event log has no real interventions, business costs,
SLAs or fraud labels, so those parts are labelled as assumptions, simulation or planted-anomaly evaluation
wherever they appear.

![Architecture](docs/architecture.svg)

[![Dashboard](dashboard/screenshots/dashboard-full.png)](dashboard/README.md)

## What's in it

| Area | What it does | Docs |
|---|---|---|
| **Analytical SQL** | 10 window-function queries (LAG, NTILE, RANGE frames, FIRST/LAST_VALUE), run against Neon; EXPLAIN before/after for the index change, including a result that did not improve | [`sql-window-functions.md`](docs/sql-window-functions.md) |
| **dbt** | staging → intermediate → marts, 30 passing tests, lineage graph, CI job; ML features can read `fct_cases` (`FEATURE_SOURCE=dbt_marts`, parity-checked) | [`dbt-project.md`](docs/dbt-project.md) |
| **Orchestration** | Prefect flow: ingest → DQ gate → dbt → retrain gate → train → promote only if it beats the deployed model by a margin → report | [`orchestration.md`](docs/orchestration.md) |
| **Feature drift** | Per-feature PSI against training baselines, `GET /health/drift/features`, drift as a retrain trigger, proven on simulated drift | [`feature-drift.md`](docs/feature-drift.md) |
| **Sequence model** | GRU/LSTM vs prefix RF, k∈{1,2,3,5}, ROC/PR/Brier/CPU latency, MLflow-logged. Mixed result; the GRU ensemble is served for early warning via ONNX (no torch in the image), the RF stays for full-case triage | [`sequence-model.md`](docs/sequence-model.md) |
| **Intervention ROI** | `interventions` ledger, `POST /interventions`, `GET /roi/summary`. Effects are **assumptions**, not measured uplift | [`uplift-method.md`](docs/uplift-method.md) |
| **Model** | RF / LR / GB compared, best by held-out AUC deployed; temporal split, held-out sigmoid calibration, SHAP explanations, ablations, bootstrap CIs | [`ml-model.md`](docs/ml-model.md) |
| **AP controls** (finance) | 6 rule-based controls (3-way match, threshold splitting, duplicate invoice, payment-block override, Benford screen) on real recovered order-value/match-type fields; measured on planted anomalies since BPI 2019 has no fraud labels | [`ap-controls.md`](docs/ap-controls.md), [`ap-controls-evaluation.md`](docs/ap-controls-evaluation.md) |
| **Working capital** | Days-payable-outstanding proxy, late-payment exposure, early-discount scenario (stated assumptions) | `src/analytics/working_capital.py` |
| **Ops** | Prometheus `/metrics`, structured logs, per-client API keys, read-only DB role, data contract and DQ checks | [`security-notes.md`](docs/security-notes.md) |

## API (selection)

| Endpoint | Purpose |
|---|---|
| `GET /orders/{case_id}/risk?explain=true` | Breach probability, risk level, SHAP top factors (late-stage triage) |
| `GET /orders/{case_id}/early-risk?k=3` | Early-warning score from the first k events (GRU via ONNX; labelled weak, returns its own held-out AUC) |
| `GET /metrics/sla`, `/metrics/cycle-time`, `/metrics/bottlenecks` | SLA, cycle-time and stage analytics |
| `GET /suppliers/performance` | Supplier scorecard |
| `GET /health/model`, `/health/drift/features` | Model age/metrics, per-feature PSI (unauthenticated) |
| `GET /observability/data-quality`, `/observability/prediction-drift` | DQ report, output-distribution drift |
| `POST /interventions`, `GET /roi/summary` | Intervention ledger (admin), simulated vs logged ROI |
| `GET /controls/exceptions`, `GET /controls/summary` | AP control exceptions and per-control rollup (anomaly triage, not fraud) |
| `GET /working-capital/summary` | DPO proxy, late-payment exposure, early-discount scenario |
| `GET /metrics` | Prometheus scrape target |

Existing routes are stable: `operations-assistant` consumes them, so new work only adds endpoints.

## Reproduce

```bash
cp .env.example .env                       # DB credentials (Neon needs DB_SSLMODE=require)
pip install -r requirements.txt
python -m scripts.setup_database           # schema, indexes, interventions table
python -m scripts.run_pipeline             # ingest → staging.events / analytics.process_cases
python -m src.ml.train                     # train + save the model (writes MLflow run)
python -m scripts.run_analysis             # API at http://localhost:8000/docs
```

Every number in this repo is regenerated by one command:

| Result | Command |
|---|---|
| Realistic-target ROC-AUC | `python -m scripts.target_sensitivity` |
| Creation-time vs late features | `python -m scripts.prediction_time_check` |
| Prefix model | `python -m scripts.prefix_model_bpi2019` |
| GRU/LSTM comparison (needs `requirements-sequence.txt`) | `python -m scripts.sequence_model_bpi2019` |
| Export the early-warning GRUs to ONNX (measures the ONNX artifact) | `python -m scripts.export_early_risk` |
| SQL analysis | `python -m scripts.run_sql_analysis` |
| dbt build | `cd dbt && dbt build --profiles-dir .` |
| Full flow (needs `requirements-orchestration.txt`) | `python -m flows.pipeline_flow` |
| Feature baselines for an existing model | `python -m scripts.backfill_feature_baselines` |
| Simulated intervention ROI + sensitivity grid | `python -m scripts.simulate_interventions`, `python -m scripts.roi_sensitivity` |
| AP control exceptions (real data) | `python -m scripts.run_ap_controls` |
| AP control recall/FPR (planted anomalies) | `python -m scripts.evaluate_ap_controls` |
| Uplift estimators on planted effects (T/X-learner, Qini) | `python -m scripts.uplift_validation` |
| Calibration method comparison | `python -m scripts.calibration_method_comparison` |
| Tests | `python -m pytest` (200+ pass, 18 opt-in live-DB tests skipped by default) |

## Known limits

- **Degenerate configured targets** (97% breach) and a static replay dataset: drift alerts on 9 of 10
  features because "recent" is the later period of the same sample, not a live stream.
- **ROI numbers are a simulation**: a randomized-holdout ledger exists but no real case has been treated, so there is no measured uplift; the uplift estimators are validated only on semi-synthetic data (`docs/uplift-method.md`).
- `analytics.suppliers` is empty and `sla_rules` covers 3 categories, so most cases use the 240 h default.
- Early-case models are weak on this data; the sequence model does not clearly beat the RF.
- Cloud migration (BigQuery/Cloud Run) is code-only and **not run**; it is deferred.

Change history: [`CHANGELOG.md`](CHANGELOG.md) · development log: [`docs/development-log.md`](docs/development-log.md) ·
feature spec: [`FEATURES.md`](FEATURES.md).
