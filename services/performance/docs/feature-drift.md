# Feature drift and the retrain loop (Phase 4)

`/observability/prediction-drift` only compares the model's *output* risk buckets. Inputs move
first, so `src/ml/feature_drift.py` computes a Population Stability Index (PSI) per raw model input
(11 features, before one-hot encoding — otherwise one supplier shift would be counted 486 times).

- **Baselines** are computed at train time from the training rows and stored in
  `models/sla_risk_model.meta.json` (`feature_baselines`). Numeric: training-quantile bins.
  Categorical: top-30 shares plus `__other__` (unseen values land there).
- **Thresholds** (`config/drift.yaml`): warn 0.10, alert 0.25; the overall report is `alert` when at
  least `retrain_min_alert_features` (2) inputs alert. One noisy feature warns but does not retrain.
- **Endpoint:** `GET /health/drift/features` (unauthenticated, like `/health/model`) — compares the
  most recent 500 cases to the baseline. Shown in the dashboard's Model Health card.
- **Trigger:** `check_retrain_needed(..., feature_drift=report)` adds a "feature drift" reason.
- **Flow:** `flows/pipeline_flow.py` now has `retrain_gate_task` after ingest + dbt. No trigger →
  skip training and report `retrained: false`. `pipeline_flow(force_retrain=True)` bypasses it. The
  existing promotion gate (candidate must beat the deployed ROC-AUC by 0.005) still applies after.

## Proof it fires (`tests/test_feature_drift.py`)
Simulated drift (supplier mix 85% one supplier, `event_count` mean 10→16) → report `alert`,
trigger reason present, gate returns `retrain: True`, flow calls train + evaluate. Same-distribution
data → `stable`, no reason, flow skips training. One drifted feature → only `warn`.
Run: `python -m pytest tests/test_feature_drift.py`

## Real result, and an honest caveat
Backfilled into the deployed model with `python -m scripts.backfill_feature_baselines` (same 2,400
training rows), then measured on the latest 500 cases:

| feature | PSI |
|---|---|
| first_activity | 4.754 |
| category | 3.140 |
| supplier_id | 2.233 |
| event_count | 1.692 |
| unique_activity_count | 1.595 |
| start_dayofweek | 1.144 |
| variant_frequency | 0.927 |
| last_activity | 0.475 |
| supplier_historical_median_cycle_time | 0.343 |
| start_hour | 0.089 (stable) |

9 of 10 alert. This is **not** production drift: the data is a static, time-ordered sample, the model
trains on the first 80% and "recent" is the final 20%, so this measures the real gap between those
periods. Thresholds were deliberately not loosened to hide it. Consequence: on this static replay
the retrain gate will always fire; the promotion gate is what protects the deployed model. On a live
stream the same check would be meaningful. Prediction-level drift is unaffected.
