# Model risk (Phase F5)

This project doesn't claim compliance with anything. **SR 11-7** (the US Federal Reserve's
guidance on model risk management) is cited only as the reference framework banks and finance GCCs
use to structure this kind of review -- this document maps what already exists in this project to
that structure, in plain language, so the mapping itself is checkable against the code and docs it
points at.

## Model inventory
| Model | Intended use | Where |
|---|---|---|
| SLA-risk random forest | Late-stage triage: flag in-progress cases likely to breach an SLA | `src/ml/train.py`, `models/sla_risk_model.joblib` |
| Early-risk GRU ensemble | Early-warning score from the first k events (explicitly weak, ships its own AUC) | `src/ml/early_risk.py`, `GET /orders/{id}/early-risk` |
| AP control rules (C1-C6) | Anomaly triage for AP review, not fraud detection | `src/controls/ap_controls.py` |
| Isolation Forest (evaluation only, not served) | Comparison baseline for the AP controls | `scripts/evaluate_ap_controls.py` |

## Intended use and limitations, stated where a reviewer would look for them
- **SLA-risk model**: `docs/evaluation.md` states the model's skill comes mostly from late-case
  features (event count, activity sequence), not creation-time information. Current PO-grouped
  realistic-target results are in [`less-degenerate-target.md`](less-degenerate-target.md);
  `README.md` distinguishes them from older sequence/prefix studies.
- **AP controls**: `docs/ap-controls.md` states explicitly that these are rule violations / anomalies
  on an unlabeled dataset, not fraud findings, in its first paragraph, and documents each control's
  known false-positive sources.
- **Early-risk model**: every API response carries its own held-out ROC-AUC and a "weak signal"
  warning string, so a caller cannot use it without seeing the caveat.

## Validation evidence
- **Temporal, purchase-order-grouped train/test split** for the current SLA-risk evaluation
  (`time_based_split`); orders crossing the cutoff are excluded. Older sequence/prefix artifacts
  are flagged as awaiting the same validation protocol.
- **Independent-dataset validation**: the same pipeline scored against BPI 2012
  (`docs/external-validation-bpi2012.md`), a dataset this project's label was never designed around.
- **Feature ablation**: which feature families drive the score (`docs/less-degenerate-target.md`,
  `scripts/ablation_study.py`) -- necessary to state "most of the skill comes from late features."
- **AP controls**: unit tests with hand-built positive and negative cases per control
  (`tests/test_ap_controls.py`), plus the planted-anomaly recall/FPR evaluation
  (`docs/ap-controls-evaluation.md`) -- the closest available substitute for labeled validation on an
  unlabeled dataset.

## Calibration
`docs/calibration.md`: a real, found-and-fixed bug (the served model's calibrator had been fitted on
its own training rows, cutting its ranking ability far below the raw model's) is documented with the
before/after numbers, the fix (a held-out temporal slice, Platt scaling chosen by held-out
comparison), and a regression test (`tests/test_calibration.py`) asserting the served and raw models
stay within 0.01 ROC-AUC of each other going forward.

## Drift monitoring
`docs/feature-drift.md` and `GET /health/drift/features`: per-feature Population Stability Index
against the training baseline, with a documented, disclosed limitation (on this static replay
dataset, "recent" means the later period of the same historical sample, not live traffic, so an
"alert" here does not mean live drift the way it would in production).

## Change control
The Prefect flow's promotion gate (`docs/orchestration.md`) only replaces the deployed model if a
retrained candidate beats it by a stated margin on the same held-out split -- a new model is not
served just because a retrain ran. The gate itself is proven with a simulated-drift test
(`tests/test_pipeline_flow.py`).

## Audit trail
- `analytics.pipeline_runs`: every ingest/retrain run, `GET /observability/pipeline-runs`.
- `analytics.ap_control_exceptions`: every control run's output, with `generated_at` and a full
  evidence JSON per exception -- not just a flag, the specific numbers that triggered it.
- `analytics.interventions`: every logged and simulated intervention, with `assignment` (treat vs.
  randomized holdout) and `experiment_id` for the uplift work (`docs/uplift-method.md`).
- `CHANGELOG.md` and `docs/development-log.md`: every claim this project has walked back or
  corrected, with the commit that did it -- itself a form of audit trail on the project's own
  honesty, not just on the data pipeline.

## What this project does NOT have, stated plainly
- No independent model-validation function separate from the person who built the model (this is a
  solo portfolio project).
- No formal model risk tiering, no board-level sign-off, no periodic revalidation schedule.
- No production incident history (nothing here has served real production traffic).
- The AP controls' thresholds (`config/ap_controls.yaml`) are stated assumptions, not derived from a
  real risk appetite statement or backtested loss data.

Citing SR 11-7 here is about structure, not certification: a real bank's model risk function would
still need to do the things this document lists as missing before this could go anywhere near a
real payment decision.
