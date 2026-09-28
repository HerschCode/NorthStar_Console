# Procurement Process Intelligence & SLA Triage

![Tests](https://github.com/HerschCode/operations-performance/actions/workflows/test.yml/badge.svg)
![dbt](https://github.com/HerschCode/operations-performance/actions/workflows/dbt.yml/badge.svg)

**Live dashboard:** <https://operations-performance.onrender.com/dashboard> ([what it shows](dashboard/README.md)) · **Live API:** <https://operations-performance.onrender.com/docs> (Neon Postgres, real BPI 2019 procurement
data; free tier, the first request may be slow to wake). A conversational layer over the same data lives
in [`operations-assistant`](https://operations-assistant.onrender.com).

## Headline

> **Late-stage SLA triage model: 0.84–0.88 ROC-AUC on realistic targets; early-case 0.67–0.77.**

This is a **late-stage triage score, not a creation-time predictor.** Most of the model's skill comes from
features only known late in a case (event count, full activity path). On the configured SLA targets 97% of
held-out cases breach, so the 0.975 the training script prints is inflated by a degenerate target; the
figures above come from percentile-based targets with 27–52% base rates. Details and every caveat:
[`docs/evaluation.md`](docs/evaluation.md).

| Setting | ROC-AUC | Where |
|---|---|---|
| Full-case features, realistic base rates (25–46%) | **0.84–0.88** | [`less-degenerate-target.md`](docs/less-degenerate-target.md) |
| Creation-time features only | 0.62–0.67 | [`prediction-time-availability.md`](docs/prediction-time-availability.md) |
| Prefix (first k events) RF | 0.67–0.73 | [`prefix-model-bpi2019.md`](docs/prefix-model-bpi2019.md) |
| GRU ensemble on the first k events (served, `/early-risk`) | 0.67–0.77 | [`sequence-model.md`](docs/sequence-model.md) |
| Independent log (BPI 2012), first 3 events | 0.77–0.92 | [`external-validation-bpi2012.md`](docs/external-validation-bpi2012.md) |

**Does the model beat a simple rule?** Only when more cases are treated, and that is reported. In the
intervention simulation on the realistic (p75, 25% base rate) target, treating the top-k cases by a one-feature
supplier-history rule captures **94-97% of the model's net-value advantage over random at k = 5-10%**, falling to
75% at 20% and 66% at 30%. So a model is only worth running if the team can act on a fifth or more of cases;
below that, the rule is nearly as good. Against random the model wins clearly (precision 0.69 vs 0.25 at k = 20%). Effect sizes are
assumptions (SIMULATION); "highest order value first" could not be run because this dataset has no order value,
so busiest-supplier stood in and is worse than random. Method and grid: [`docs/uplift-method.md`](docs/uplift-method.md).

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
service, and the MLOps around it (dbt, Prefect, drift monitoring, an intervention ROI ledger). The
public event log has no real interventions, business costs or SLAs, so those parts are labelled as
assumptions or simulation wherever they appear.

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
| **Model** | Random forest (deployed), LR, GB; temporal split, held-out sigmoid calibration, SHAP explanations, ablations, bootstrap CIs | [`ml-model.md`](docs/ml-model.md) |
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
