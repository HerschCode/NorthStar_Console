# Prefect orchestration (Phase 3)

Reproduce: `pip install -r requirements.txt -r requirements-orchestration.txt`, then
`python -m flows.pipeline_flow` (needs the same `DB_*` env vars every other script in this
project uses). Runs against real infrastructure -- live Neon, a real `dbt build`, real model
training -- not a dry run.

## The flow

`ingest -> validate -> dbt build -> train -> evaluate/register -> report`
([`flows/pipeline_flow.py`](../flows/pipeline_flow.py))

| Step | What it does | Reuses |
|---|---|---|
| `ingest_task` | Downloads/loads/cleans the event log, writes `staging.events` + `analytics.process_cases` | `scripts/run_pipeline.py::run()`, unchanged |
| `validate_task` | Runs `src/cleaning/data_quality.py`'s checks against what was just ingested, gated by `config/orchestration.yaml` thresholds | `run_data_quality_checks()`, unchanged |
| `dbt_build_task` | Runs the real `dbt build` (Phase 2) | `dbt/`, unchanged |
| `train_task` | Trains LR/RF/GBM on the current data | `src/ml/train.py::train_models()`, unchanged |
| `evaluate_and_register_task` | Registers the best candidate **only if it beats the currently deployed model** by a configured ROC-AUC margin | `save_best_model()`, called conditionally |
| `report_task` | Writes `reports/pipeline_flow_last_run.json` | new, small |

## A real integration conflict, found by running the flow end to end, not by inspection

The first live run failed at `ingest_task`: `cannot drop table staging.events because other
objects depend on it` -- Phase 2's dbt views (`dbt_staging.stg_events` and everything built on
top of it) now depend on the very table `run_pipeline.py`'s `to_sql(if_exists="replace")` needs
to drop and recreate. The correct fix is sequencing, not avoiding dbt: `dbt_build_task` runs
immediately after `ingest_task` in the same flow, so `ingest_task` now drops the dbt-managed
schemas first and lets `dbt_build_task` fully rebuild them right after. A second, smaller issue
from earlier manual debugging (a stale `dbt_dbt_staging` schema, from before Phase 2's
`generate_schema_name` macro fix) was found and dropped from the live database in the process.

## "Beats the current Production model" -- what that actually means here

There is no real MLflow "Production" stage in active use in this project (`src/ml/train.py`'s
own `__main__` block only ever transitions to "Staging"). The model that actually serves
predictions is the `.joblib` file at `models/sla_risk_model.joblib`, read directly by the API --
so that file's `roc_auc` (from its `.meta.json` sidecar) is the honest comparison target, not an
MLflow stage nothing else in the project reads from.

`config/orchestration.yaml`'s `min_roc_auc_improvement` (0.005) is deliberately small: it's a
sanity floor against an outright regression, not a claim that a 0.005 gap is itself a meaningful
improvement -- `docs/statistical-significance.md` already found RF vs LR aren't statistically
distinguishable at gaps around 0.02-0.05 on this data, and a single train/test split (which is
what a flow run measures) can't establish significance on its own either. Stated in the config
file's own comment, not just here.

## First real end-to-end run (2026-09-24)

```
Trained candidates: [('logistic_regression', 0.9101), ('random_forest', 0.9888), ('gradient_boosting', 0.7677)]
Best candidate: random_forest (ROC-AUC 0.9888)
Promotion decision: KEEP DEPLOYED -- candidate 0.9888 does not beat deployed 0.9857 by >= 0.005
```

Correct behavior, not a bug: the currently deployed model (also random_forest, from an earlier
run) already scores 0.9857 on its own held-out split; this run's fresh split scored the new
candidate at 0.9888, a +0.0031 gap -- below the configured margin, so the flow correctly left
the deployed model in place rather than replacing it on a difference this small. Full record:
[`reports/pipeline_flow_last_run.json`](../reports/pipeline_flow_last_run.json) (gitignored,
regenerated on every run).

## Production schedule (documented, not deployed)

This flow is not deployed against a real Prefect server/Cloud schedule -- that needs a
persistent Prefect server, which is out of scope for a project that otherwise runs entirely on
free-tier, no-server infrastructure (Render, Neon, GitHub Actions). In production it would run
**daily at 07:00 UTC**, one hour after `operations-performance`'s existing GitHub Actions
`retrain-check.yml` cron (06:00 UTC) -- so a full re-ingest + `dbt build` + retrain-if-warranted
always runs *after*, not racing, the simpler daily check that workflow already does. The real
command to wire this up against an actual Prefect server: `prefect deployment build
flows/pipeline_flow.py:pipeline_flow --cron "0 7 * * *"`.

## Tests

`tests/test_pipeline_flow.py` -- mocks the DB/dbt/training boundary (no live infrastructure
needed), same pattern as `tests/test_check_and_retrain.py`. The one thing Phase 3's acceptance
criterion explicitly calls out --
**"a failed DQ check stops the flow before training"** -- is proven directly, not just asserted:
`test_failed_dq_check_stops_the_flow_before_training` runs the real flow function with
`validate_task` raising the same exception a real bad report would, and asserts
`dbt_build_task`/`train_task`/`evaluate_and_register_task`/`report_task` are never called.

A real Prefect gotcha, found while writing these tests: `get_run_logger()` (used throughout the
flow for structured logging) requires an active Prefect run context, and raises
`MissingContextError` when a task's `.fn` is called directly -- the standard way to unit-test
Prefect task logic without spinning up the full Prefect runtime. Fixed with a small `_logger()`
fallback (real stdlib logger outside a run context) rather than making every task conditionally
skip logging in tests.
