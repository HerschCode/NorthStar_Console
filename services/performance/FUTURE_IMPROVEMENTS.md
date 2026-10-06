# Future Improvements (Post-v1.0 Backlog)

## Done since v1.0 (moved out of this list)
`sql/analysis/*.sql` is now filled in with real SQL. `GET /metrics/conformance` and
`GET /metrics/sla-risk-distribution` are now built, unlocking both dashboard pages 5-6 and
`operations-assistant`'s `process.py` tools. Dead stub files (never-built transformation/cleaning
modules, a superseded scenario-analysis placeholder) were removed rather than left empty in a
frozen repo. API-key authentication is now real (`src/api/auth.py`). `check_conformance` is now
fully vectorized (~4x speedup, after an honestly-documented first attempt that didn't actually
help) -- see `PLAN.md` and `docs/performance-notes.md`. `scenario_analysis.py` is now real
(stage-duration what-if impact estimation, hand-verified against the golden dataset). ML model
comparison now includes Gradient Boosting plus proper time-series cross-validation
(`TimeSeriesSplit`, not standard k-fold). An automated retraining trigger now exists
(`src/ml/retrain_trigger.py`) -- time- and volume-based, real decision logic ready for a real
scheduler to call. CI now runs a `pip-audit` dependency scan.

## Post-v1.0: real SHAP explainability
`src/ml/explain_shap.py` -- real `shap.Explainer`-based per-prediction and batch
explanations, additive alongside the original lightweight approximation (nothing
depended on that module changing). Verified with 4 new tests (real shap against a
real trained model, no mocking) and manually against the actual production model
and live Neon data. See PLAN.md.

## Still open
- **Power BI / Looker Studio specifically** -- FEATURES.md's Tier 1 literally names one of
  these two tools. What's actually built instead is a custom FastAPI+Chart.js dashboard at
  `/dashboard` (live, real data, light/dark theme) covering the same content (and more --
  Supplier/Conformance are Tier 2 items also included). This is a deliberate substitution,
  not an oversight, but it means the literal Tier 1 item is still technically open if an
  interviewer asks specifically "did you use a BI tool."

## Post-v1.0: hyperparameter tuning + MLflow tracking
`src/ml/tune.py` -- RandomizedSearchCV with TimeSeriesSplit, additive alongside
train.py's fixed-hyperparameter comparison. MLflow tracking via local SQLite
(`sqlite:///mlflow.db`) after a real finding: MLflow 3.x deprecated the plain
file-store backend. Verified against real live Neon data. See PLAN.md.

## Post-v1.0: retrain trigger wired to a real scheduler
`.github/workflows/retrain-check.yml` (daily cron) + `scripts/check_and_retrain.py`.
Verified against real live Neon data, not just mocked tests. Needs `DB_HOST`/
`DB_NAME`/`DB_USER`/`DB_PASSWORD` set as GitHub Actions repo secrets to actually
run on schedule. See PLAN.md.
