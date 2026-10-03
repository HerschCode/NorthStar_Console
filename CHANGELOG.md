# Changelog

## Unreleased -- Post-v1.0.0 upgrade work

### Dashboard navigation and responsive layout (2026-10-03)
- Added sticky in-page section navigation and clearer visual grouping for summary, process/risk,
  controls/finance, and platform-health sections. Risk worklists now reflow for narrow screens.

### External validation on public data (2026-10-03)
- `scripts/external_validation.py`, `docs/external-validation.md`: AP controls on UCI Online Retail II and SEC EDGAR 2024q1. C4: 21x lift vs reversal proxy; C6 flags ~55% of ordinary real data (negative result, not tuned).

### Analysis: model vs rules with causal features and bootstrap CIs (2026-10-03)
- `scripts/model_vs_rules.py` -> `reports/model_vs_rules.json`; write-up `docs/model-vs-rules.md`.
- Negative result: adding supplier workload features does not beat the rules at 5-10% treated. Found that
  `supplier_historical_breach_rate` uses unfinished earlier cases' outcomes (look-ahead); strictly causal it ~ random.
  Retraining with a causal history is flagged, not yet done.

### Fix: CI failure from an unpinned scikit-learn vs a pickled model (2026-10-03)
- After the 9,228-case resample, gradient boosting won model selection. Its pickle embeds sklearn-internal
  loss classes, so `models/sla_risk_model.joblib` failed to load on CI's newer sklearn ("No module named
  `_loss`"), failing `Tests` since 2026-09-29. The same unpinned install on Render would have broken
  `/orders/{id}/risk` after a redeploy.
- `requirements.txt` now pins `scikit-learn==1.7.1` (the training version); `meta.json` records
  `sklearn_version`; a test fails with a readable message if the pin and the artifact drift apart.
  Bump the pin together with a retrain.

### Finance module: AP controls, working capital, resample (2026-09-28/29)
- **Recovered real BPI 2019 fields the original 8-column sample dropped**: match type
  (`item_category`), goods-receipt/invoice-verification flags, document/item type, company,
  sub-spend-area, per-event actor (`user_id`), and order value (`net_worth_eur`). Confirmed present
  in the source XES by probing raw attribute keys before adding them, not assumed. Resampled with a
  coverage-based stopping rule (>=10 POs for >=60 vendors) instead of "first N cases": 9,228 cases
  (was 3,000), `data/raw/bpi2019_events.sample_method.json` records the method.
- **Six AP controls** (`src/controls/ap_controls.py`, `config/ap_controls.yaml`): three-way-match
  violation, invoice/GR order mismatch, approval-threshold splitting, possible duplicate invoice,
  payment-block override with a segregation-of-duties flag, Benford's-law screen. Framed throughout
  as anomaly triage, not fraud detection -- BPI 2019 has no fraud labels.
- **Two real bugs found and fixed before reporting** (both via the planted-anomaly evaluation):
  threshold-splitting flagged 3,288 of 9,228 cases (routine purchasing, not splits) until a
  near-threshold/window-size filter was added; duplicate-invoice detection had a loop-scoping bug
  that skipped almost every real pair, and after fixing it, ~12,000 pairs turned out to be a
  vendor's own routine recurring amount, filtered out by an exact-recurring-amount check. Both fixes
  and their real exception counts (before/after) are in `docs/ap-controls.md`.
- **Planted-anomaly evaluation** (`scripts/evaluate_ap_controls.py`, `docs/ap-controls-evaluation.md`):
  recall 97.5% (C1), 66.7% (C3), 80.0% (C4) against seeded synthetic injections; an Isolation Forest
  baseline catches 1 of 200 (0.5%) -- a real negative result showing these anomalies are relational,
  not extreme-value.
- **Working capital** (`src/analytics/working_capital.py`): invoice-to-clear DPO proxy, late-payment
  exposure, early-payment-discount scenario (stated assumptions).
- New endpoints `GET /controls/exceptions`, `GET /controls/summary`, `GET /working-capital/summary`;
  dashboard tiles for both; `analytics.ap_control_exceptions` table, dbt-exposed
  (`mart_ap_control_exceptions`, Python-computed and documented as such).
- `docs/model-risk.md`: maps existing validation/calibration/drift/audit-trail work to the SR 11-7
  structure, explicit about what a real bank's model-risk function would still need.
- **The 3x larger, richer resample changes the model's headline numbers** (see README/docs for the
  regenerated ranges): a larger, more representative sample is expected to shift ranges from the
  3,000-case sample. The best-performing candidate is now selected purely by held-out ROC-AUC as
  before; which algorithm wins is no longer assumed to be the random forest -- see `docs/ml-model.md`.
  The ROI order-value strategy, previously in `strategies_not_run` for lack of a value column, now
  runs (`scripts/roi_sensitivity.py`).

### Fix: zero-duration cases in SLA labels (2026-09-28)
- 104 truncated cases (cycle_time_hours == 0; 96 single-event) were labelled "not breached" in every breach rate
  and in the model's training labels. `evaluate_sla()` now drops them by default (single-case scoring keeps them);
  dbt `int_case_sla_scored` gives them a NULL label and `mart_sla_daily` divides by labelled cases only.
- Model retrained (2,896 cases; served ROC-AUC 0.975); every headline number regenerated: realistic targets
  0.84-0.88, creation-time 0.62-0.67, prefix RF 0.67-0.73, served early-risk GRU 0.67-0.77.
- The supplier-rule finding weakens: the rule captures 94-97% of the model's advantage at 5-10% treated but only
  75%/66% at 20%/30% (was 81-106%); the bad labels had flattered one supplier's history.

### Dashboard documented; two data bugs fixed (2026-09-26)
- `dashboard/README.md` replaces an unbuilt Power BI spec with the real `/dashboard` page, screenshots and caveats.
- Conformance was 0% by construction: `config/process.yaml` used activity names absent from BPI 2019. Now the log's
  most frequent variant (20.9% conformant). Golden-fixture tests pin their own textbook config; a regression test
  checks expected activities exist in the log.
- Supplier scorecard excludes 104 zero-duration (truncated) cases that made one supplier look instant.
- API description no longer says "Read-only" (POST /interventions exists); CORS allows POST for it.
- Dashboard text: unsourced "industry benchmark" relabelled; ROC-AUC shown with its degenerate-target caveat.

### Upgrade 4 + hygiene (2026-09-25)
- `GET /orders/{case_id}/early-risk?k=` (additive): first-k-events GRU ensemble (3 seeds, k in 2/3/5, p75 target)
  exported to ONNX (`scripts/export_early_risk.py`, `src/ml/early_risk.py`), served with onnxruntime only --
  torch is not a serving dependency (asserted in a test). Response carries the served model's held-out ROC-AUC
  (0.76 / 0.76 / 0.79, measured on the ONNX artifact), base rate and an early-warning label; cases already past
  their target or with fewer than k events get 409. ONNX-vs-PyTorch parity < 1e-5 (tested).
- Hygiene: `@app.on_event("startup")` replaced by a FastAPI lifespan handler (test added); the deprecation
  warnings are gone from the suite.

### Upgrade 3: randomized holdout + uplift validation (2026-09-25)
- Ledger: `assignment` (treat/holdout) and `experiment_id` (migration 006); deterministic SHA-256 randomizer
  (`src/roi/randomizer.py`, holdout share in `config/interventions.yaml`); `POST /interventions` respects it
  (holdout cases logged with cost 0, response `assignment`/`action_required`); `/roi/summary` gains an
  `experiment` block (per-arm breach rates, measured uplift with CI once >= 30 outcomes per arm).
- `src/roi/causal.py` + `scripts/uplift_validation.py`: T- and X-learner, Qini/AUUC, validated on real
  covariates with a planted heterogeneous effect (20 replicates, both scenarios; acceptance criteria set in
  advance and all met). Naive "treat highest risk" is worse than random on the p75 scenario.
  `reports/uplift_validation.json`, `docs/uplift-qini.png`, `docs/uplift-method.md`.

### Fix: served-model calibration bug (2026-09-25)
- Served model was isotonic-calibrated on the forest's own training rows (7 distinct scores on 600 test
  cases; ROC-AUC 0.665-0.69 vs 0.983-0.986 raw) and `meta.json` recorded the raw score. Now: forest on
  the earliest 80% of the training window, Platt/sigmoid calibration on the latest 20%
  (`src/ml/calibration.py`, `fit_calibrated`); isotonic only if it wins Brier without losing AUC and the
  slice has >= 50 of each class. `meta.json` records `served_model` and `raw_model` separately.
- Deployed model retrained (served ROC-AUC = raw = 0.9835); `simulate_interventions`, `roi_sensitivity`,
  `calibration_analysis`, `cost_threshold_analysis` rerun; `docs/evaluation.md` tables regenerated
  (Brier no longer improves on the 97% target; optimal threshold 0.35 -> 0.65).
- Corrected finding: the supplier-history rule captures 81-106% (96% at k=20%) of the model's net-value
  advantage over random, not 80%. New `docs/calibration.md`, `tests/test_calibration.py`.

### Fix 2: ROI on a non-degenerate target + sensitivity grid (2026-09-25)
- `scripts/roi_sensitivity.py`: policy replay on the p75 target (27% base rate, p75-trained model),
  grid = treated share x effect x breach cost, strategies model / random / supplier-history rule /
  busiest-supplier. `reports/roi_sensitivity.json` (committed), `docs/roi-sensitivity.png`,
  returned by `GET /roi/summary` as `sensitivity`. "Highest order value" not run: no such column.
- Result: model beats random on p75 (precision 0.775 vs 0.27 at 20%; break-even effect 8% vs 23%);
  on the configured target nothing beats random.
- Found: the served isotonic-calibrated model has ROC-AUC 0.665 on the held-out window vs 0.986 for
  the raw forest (ties from in-sample calibration). Reported, not changed. See `docs/uplift-method.md`.

### Fix: calibrated-or-not inconsistency (2026-09-25)
- `docs/uplift-method.md` called the ROI risk scores "uncalibrated"; they are not. The simulation
  already used `bundle["model"]` (the isotonic `CalibratedClassifierCV` that the API serves), and now
  asserts it. Doc corrected, including that the calibration layer was fit on the training rows.
  No other doc made the claim.

### Phase 7: README rewrite (2026-09-24)
- README cut from 368 to ~100 lines: leads with the honest headline (late-stage triage 0.83-0.89 on
  realistic targets; early-case 0.62-0.76), new title, architecture SVG redrawn for Phases 1-6,
  one-command reproduction table.
- The previous README moved verbatim to `docs/evaluation.md` (links fixed); no caveat was dropped.

### Phase 6: Intervention ledger + ROI simulation (2026-09-24)
- `analytics.interventions` table (migration 005), `config/interventions.yaml` policy, `src/roi/ledger.py`.
- New `POST /interventions` (admin, validated; separate write engine since the API's default DB
  role is read-only) and `GET /roi/summary` (simulated vs logged kept apart, SIMULATION label +
  assumptions in every response). Additive; no existing endpoint changed.
- `scripts/simulate_interventions.py`: policy replay over the held-out window (120 cases; break-even
  effect 6.25%). Effects are assumptions, not measured uplift -- `docs/uplift-method.md`.
- Found: live `analytics.process_cases` has no PK (pipeline's to_sql replace drops it), so the ledger
  has no FK and the API checks case existence instead.

### Phase 5: Sequence model vs prefix RF (2026-09-24)
- `src/ml/sequence_model.py` (GRU/LSTM over first k events) + `scripts/sequence_model_bpi2019.py`
  (k in 1,2,3,5; p50/p75 targets; 3 seeds; ROC-AUC/PR-AUC/Brier/CPU latency; MLflow-logged);
  `requirements-sequence.txt`; tests skip if torch is absent.
- Result is mixed and reported as such: GRU beats the RF by +0.05-0.07 ROC-AUC at p75 (k>=2) but
  loses at p50 (up to -0.08) and has worse Brier at p75 k<=2; absolute 0.74-0.76. **RF kept**, nothing
  deployed. See `docs/sequence-model.md`.

### Phase 4: Feature drift + retrain loop (2026-09-24)
- `src/ml/feature_drift.py`: per-feature PSI on raw inputs, baselines saved in model metadata at
  train time (`config/drift.yaml` thresholds). `scripts/backfill_feature_baselines.py` added them to
  the deployed model without retraining.
- New `GET /health/drift/features` (additive; existing endpoints unchanged) + dashboard section.
- `retrain_trigger` gains a feature-drift reason; the Prefect flow gains `retrain_gate_task`
  (`force_retrain` option). Simulated-drift tests prove it fires and that calm data skips training.
- Honest finding: on the static replay data 9/10 features alert (train-vs-later-period gap);
  thresholds not tuned. See `docs/feature-drift.md`.

### Phase 3: Prefect orchestration (2026-09-24)
No orchestration existed beyond a bare GitHub Actions cron calling one script
(`retrain-check.yml`). Added `flows/pipeline_flow.py`: ingest -> validate -> dbt build -> train
-> evaluate/register -> report, each step reusing existing, already-tested code rather than
duplicating it (`scripts/run_pipeline.py::run()`, `src/cleaning/data_quality.py`, Phase 2's
`dbt build`, `src/ml/train.py::train_models()`/`save_best_model()`).

- **Register only if the candidate beats the deployed model, verified against a real live run,
  not just asserted:** `config/orchestration.yaml`'s `min_roc_auc_improvement` (0.005) gates
  `save_best_model()`. First real end-to-end run: candidate random_forest scored 0.9888 vs the
  already-deployed model's 0.9857 (+0.0031, below the margin) -- correctly kept the deployed
  model rather than replacing it on a difference too small to trust, matching the paired
  significance finding in `docs/statistical-significance.md`.
- **A real integration conflict, found by running the flow, not by inspection:**
  `ingest_task`'s raw-table replace (`DROP TABLE staging.events`) failed once Phase 2's dbt
  views existed on top of it -- "other objects depend on it". Fixed by sequencing: `ingest_task`
  now drops the dbt-managed schemas first, and `dbt_build_task` (which runs immediately after in
  the same flow) fully rebuilds them.
- **Acceptance criterion proven directly, not just asserted:** "a failed DQ check stops the flow
  before training" -- `tests/test_pipeline_flow.py::test_failed_dq_check_stops_the_flow_before_training`
  runs the real flow function with `validate_task` raising, and asserts
  `dbt_build_task`/`train_task`/`evaluate_and_register_task`/`report_task` are never called.
- A real Prefect gotcha found while writing tests: `get_run_logger()` needs an active run
  context and raises `MissingContextError` when a task's `.fn` is called directly (the standard
  way to unit-test Prefect tasks without the full runtime) -- fixed with a small stdlib-logger
  fallback, `_logger()`.
- `requirements-orchestration.txt`: dbt-core/dbt-postgres/prefect kept separate from
  `requirements.txt` so the deployed API image doesn't pull in either toolchain's dependency
  tree. `.github/workflows/test.yml` installs `prefect` alone (not the full dbt toolchain) since
  `tests/test_pipeline_flow.py` only imports it at module level; dbt itself is invoked as a
  subprocess inside a task body, never imported directly.
- Production schedule documented, not deployed (needs a persistent Prefect server, out of scope
  for this project's free-tier, no-server infrastructure): daily 07:00 UTC, one hour after
  `retrain-check.yml`'s existing 06:00 UTC cron. Full writeup:
  [`docs/orchestration.md`](docs/orchestration.md).

### Phase 2: dbt transformation layer (2026-09-24)
No orchestration/transformation layer existed -- `sql/analysis/*.sql` and the Python analytics
were the only consumers of raw data, each re-deriving stage durations and rework detection
independently. Added `dbt/`:

- **staging** (`stg_events`, `stg_process_cases`, `stg_suppliers`, `stg_sla_rules`): 1:1 with
  raw tables. **intermediate** (`int_stage_durations`, `int_case_rework`, `int_case_sla_scored`):
  the dbt-model versions of `bottlenecks.sql`/`rework_detection.sql`'s logic, reused as real
  models. **marts** (`fct_cases`, `dim_supplier`, `dim_category`, `mart_sla_daily`).
  `dim_category`, not `dim_business_unit` -- no such column exists in this data.
- `unique`/`not_null`/`relationships` tests plus one custom singular test
  (`assert_no_negative_stage_durations.sql`). `dbt build` run live: 30 pass, 0 errors, 2 warnings.
- **A real bug found by running `dbt build`, not by inspection:** a comment in `stg_events.sql`
  used a literal `ref()` call as a prose example, which dbt's Jinja renderer evaluated as a real
  self-referencing dependency, failing every build with "Found a cycle:
  model.operations_performance.stg_events". Fixed by describing the pattern without writing a
  literal Jinja call to the model's own name inside its own file.
- **Two real data-quality findings, surfaced by the `relationships` tests' warning counts, not
  by inspection:** `analytics.suppliers` is completely empty (0 rows; confirmed unused by any
  live code, not a production bug); `analytics.sla_rules` has only 3 categories against 10+ real
  ones in `process_cases.category`, so 2,972 of 3,000 cases fall back to the default 240h SLA
  target rather than a matched one -- a pre-existing gap in the Python pipeline too, made visible
  as a number by dbt's test rather than introduced by it. Full writeup:
  [`docs/dbt-project.md`](docs/dbt-project.md).
- `dbt docs generate` + a live `dbt docs serve` run, lineage graph captured from the real running
  page (canvas pixels POSTed to a local save server, not hand-typed base64 -- an earlier attempt
  to manually transcribe the ~30KB image data produced a corrupted file, caught by loading it
  back before committing): [`docs/dbt-lineage-graph.png`](docs/dbt-lineage-graph.png).
- `src/ml/features.py::load_evaluated_cases_from_dbt_marts()`: optional (`FEATURE_SOURCE=dbt_marts`)
  read path from `fct_cases`, feeding the same unmodified `build_features()`. Parity verified
  against live data with rows aligned by `case_id`.
- `.github/workflows/dbt.yml`: `dbt build` against a real Postgres 16 service container on every
  push, seeded via the same `scripts/setup_database.py` schema plus a small fixture.

### Phase 1: Real analytical SQL (2026-09-24)
`sql/analysis/*.sql` was thin -- `bottlenecks.sql` was the only file using a window function,
despite docs claiming LAG/LEAD/RANK/rolling metrics (v1.0.0's own "known limitations" already
named this: "`sql/analysis/*.sql` still stubs"). Closed:

- 9 new/rewritten files, run live against Neon, not just written: `waiting_time_lag.sql` (LAG),
  `variant_ranking.sql` (RANK/DENSE_RANK, two rankings -- volume and breach rate),
  `rolling_sla_breach_rate.sql` (RANGE window frame over a calendar interval, per category),
  `monthly_cohort_breach_trend.sql` (cumulative SUM() OVER), `supplier_quartiles.sql`
  (NTILE/PERCENT_RANK), `slowest_stage_per_case.sql` (FIRST_VALUE/LAST_VALUE),
  `running_event_count.sql` (ROW_NUMBER + a correlated subquery, with the reasoning for why a
  window function doesn't fit that specific question), `category_relative_cycle_time.sql`
  (PERCENT_RANK/z-score), `rework_detection.sql` (window COUNT, no self-join).
- 3 real bugs found by running these, not by inspection: `COUNT(DISTINCT ...) OVER (...)`
  doesn't exist in Postgres; `ROUND(double precision, integer)` doesn't exist; `QUALIFY`
  (Snowflake/BigQuery syntax) doesn't exist either. All three fixed with the correct Postgres
  idiom, documented in the file where they were found.
- A 4th, more consequential bug in `scripts/setup_database.py`'s migration runner (naive
  `split(";")` breaks on a semicolon inside a `--` comment) surfaced real schema drift: none of
  `sql/schema/002_create_indexes.sql`'s indexes had ever actually been applied to the live
  database. Fixed and re-applied for real, verified via `pg_indexes`.
- `EXPLAIN ANALYZE` before/after a new `(case_id, timestamp)` composite index: the query plan
  changed (Seq Scan -> Index Scan) but measured execution time did not (58.4ms -> 59.2ms) at
  this table's current 32K-row size -- reported as a real negative result, not a speedup.
- Seeded-database test suite (`tests/test_sql_analysis_live.py`): no local Postgres/Docker is
  available in this environment, so it seeds a real, throwaway Postgres *database* (not just a
  schema -- these files hardcode `staging.`/`analytics.` schema names, which already exist in
  production on the same server) on the same Neon project, with hand-designed fixture data whose
  correct answers are known in advance, runs all 10 files against it, and drops the database in
  a `finally` block. Opt-in (`RUN_LIVE_SQL_TESTS=1`) since it needs real DB credentials with
  `CREATE DATABASE` privilege that CI does not have configured.
- Full writeup: [`docs/sql-window-functions.md`](docs/sql-window-functions.md).

Remaining, explicitly deferred, larger phases (dbt transformation layer, Prefect orchestration,
feature-level drift, a sequence model for early prediction, an intervention/ROI simulation
layer, README rewrite) are tracked separately -- not attempted in this same pass, since each is
a multi-day scope of its own and rushing them would violate this project's own honesty standard
(every number reproducible, negative results reported not tuned away).

## v1.0.0 -- Freeze

Everything through Phase 30 (Performance & cost pass) is complete, tested, and audited. This is
the frozen baseline -- no further feature work happens against this version. New ideas go in
`FUTURE_IMPROVEMENTS.md`, not directly into the code.

**To actually tag this once the repo is under real git version control:**
```
git init   # if not already
git add -A && git commit -m "v1.0.0 -- Operations Performance"
git tag -a v1.0.0 -m "Frozen baseline after Phases 1-30"
```

### What's in v1.0.0
- Full ETL pipeline: contract-checked, validated, cleaned ingestion of BPI Challenge 2019 data,
  idempotent, audit-logged (`pipeline_runs`)
- Process analytics: cycle time, bottlenecks, variants, conformance, rework, root-cause,
  supplier scorecards -- every metric independently hand-verified against a golden dataset
  (Phase 27), not just unit-tested
- SLA-risk ML model: time-based split, lightweight explainability, documented model card
- FastAPI analytics layer (8 endpoints) consumed by both a local dashboard spec and the
  companion `operations-assistant` project
- BigQuery migration path with a real Postgres-vs-BigQuery comparison
- Least-privilege DB roles, input-validated CORS, structured JSON logging
- 65 tests, all passing, including a real correctness audit and 3 end-to-end business scenarios
- CI running the full suite on every push

### Real bugs found and fixed during the build (kept for the record, not scrubbed out)
1. `_count_out_of_order` sorted before diffing, silently defeating the check it implemented
2. A pandas-3.0 dtype-check incompatibility in the data contract
3. `conformance.py`'s out-of-order check never correctly handled allowed repeated activities --
   `allowed_repeats` silently never worked for conformance checking, only rework detection
   (found by Phase 27's hand-verified golden dataset)
4. `rework_by_case` was ~98x slower than necessary due to a Python-level loop where a single
   vectorized groupby would do (found by Phase 30's benchmark)
5. **Every documented script invocation was broken from Phase 0 onward** -- `python
   scripts/run_pipeline.py` (exactly what the README said to run) failed on the first import,
   for anyone, the whole time. Found by Phase 30 actually running the benchmark command as a
   real user would, not by code inspection.
6. Two smaller Phase 25 audit findings: an inconsistent import path, an undeclared direct
   dependency (`pydantic`)

### Known limitations, stated plainly (not gaps to be embarrassed by -- see FUTURE_IMPROVEMENTS.md)
- SHAP-based explainability not implemented (lightweight approximation only)
- `sql/analysis/*.sql` still stubs -- the Python analytics have SQL equivalents planned, not built
- Dashboard is a documented build spec, not an actual built `.pbix`/Looker file
- No API authentication
- `check_conformance` has the same performance shape `rework_by_case` had before its Phase 30
  fix -- not urgent at current scale, documented in `docs/performance-notes.md`
- Never run against a live Postgres instance or real BigQuery project in this environment
