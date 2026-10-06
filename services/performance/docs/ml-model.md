# Model Card — SLA Breach Risk

## Objective
Binary classification: will this case's cycle time exceed its SLA target
(`config/sla.yaml`)? Trained and evaluated in `src/ml/train.py`.

## Training data
BPI Challenge 2019 procurement cases, transformed via `build_process_cases.py` and labeled via
`sla_analysis.evaluate_sla`. Evaluation excludes zero-duration truncated cases. The current
holdout is **forward-in-time and purchase-order-grouped** (`train.py:time_based_split`): all cases
from one purchase order remain on one side, and boundary-crossing orders are excluded. This avoids
same-order leakage as well as future-to-past leakage. The deployed artifact has not yet been
retrained with this corrected split and supplier-history logic.

## Features
The model uses process-completion, calendar, category/supplier, and supplier-history features;
see `src/ml/features.py`. Full-case features such as event count, final activity, rework, and
variant frequency are unavailable at case creation, so this is not a creation-time predictor.
Supplier breach-rate and median-duration features include only same-supplier cases that ended
strictly before the current case starts. No-history cases use fixed sentinels rather than statistics
computed from the full dataset.

## Models
Three, compared on the same PO-grouped time-based split:
- Baseline: Logistic Regression (`class_weight="balanced"` to account for breach being the
  minority class)
- Random Forest (200 trees, max_depth=8, same class balancing)
- Gradient Boosting (150 estimators, max_depth=3, learning_rate=0.1) -- a genuinely different
  algorithm family (sequential error-correcting ensemble, vs. a single linear model, vs. a bagged
  ensemble of independent trees), worth comparing rather than assuming Random Forest is
  automatically the right choice just because it was the first non-baseline model tried.
  `GradientBoostingClassifier` doesn't support `class_weight` directly the way the other two do --
  `sample_weight` is computed manually in `train.py` for the equivalent balancing effect.

The better model by ROC-AUC on the time-based test split is saved to
`models/sla_risk_model.joblib` -- so a genuine three-way comparison determines which model ships,
not a default assumption.

## Cross-validation
`train.py:cross_validate_time_series` uses expanding forward-in-time folds grouped by purchase
order. Each fold keeps related cases together and removes orders crossing its temporal boundary.
The same fold generator is used by target sensitivity, prediction-time analysis, ablation, and
hyperparameter tuning.

Reports per-fold ROC-AUC plus mean/std across folds -- the std matters as much as the mean. A
model that scores well on one time window but swings wildly across others is a real stability risk
a single train/test split's one number can't reveal.

## Evaluation
Precision, recall, F1, ROC-AUC, confusion matrix -- all computed in `train.py:_evaluate`.

**Current offline evaluation:** realistic percentile-label experiments produce full-feature
holdout ROC-AUC 0.763–0.865 and creation-time-only 0.641–0.869 across p50/p75 targets. The
configured label has a 96.3% breach rate in the latest holdout, making its high AUC uninformative.
The existing model does not show a statistically clear advantage over order-value ranking at
5–30% intervention shares. See [`less-degenerate-target.md`](less-degenerate-target.md) and
[`model-vs-rules.md`](model-vs-rules.md). These are offline experiments, not measured intervention
lift or the performance of a newly retrained deployment.

**Precision/recall trade-off, explicitly:** a false negative (predicted low-risk, actually
breaches) means an at-risk order gets no intervention and a client SLA gets missed. A false
positive (predicted high-risk, doesn't breach) means an analyst spends a few minutes reviewing an
order that was fine. Those costs aren't symmetric -- we'd rather over-flag than under-flag, which
is why `class_weight="balanced"` is used rather than optimizing for raw accuracy.

## Explainability
`src/ml/explain_shap.py` provides per-prediction SHAP explanations through
`GET /orders/{case_id}/risk?explain=true`. Treat these as model attribution, not causal explanations
of procurement delays.

## Known limitations
- No fresh labels or real-time event stream exists; the dataset is a static historical log.
- Prediction-distribution drift is monitored, but no live calibration/precision/recall monitoring
  is possible without later outcomes. Retraining is not automatically triggered by drift.
- No fairness review across suppliers or categories has been completed.
- Logistic regression emits convergence warnings in some grouped evaluation folds; its results
  require caution until numeric scaling/convergence is addressed.
- No real interventions have been run. ROI and uplift outputs are simulated or semi-synthetic.
- The deployed model artifact has not been regenerated with the new completed-history features
  and PO-grouped split. Do not describe the offline results as deployed model performance.

## Retraining
The pipeline has retraining checks and model promotion gates, but real-world performance monitoring
still needs fresh labeled outcomes. Retrain and reevaluate when the process/schema changes, an SLA
category is added, or a new time period has enough completed cases for a meaningful grouped holdout.
