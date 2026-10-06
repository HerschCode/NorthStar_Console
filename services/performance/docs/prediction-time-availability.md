# Prediction-time availability: what the model can actually see, and when

Reproduce: `python -m scripts.prediction_time_check` (5 PO-grouped temporal folds, measurable
cases only; 95.5% configured-target breach rate).

## The finding

Several features are aggregates over the *finished* case, so they are not knowable when a
purchase order is created:

| Feature | Why it is completion-time |
|---|---|
| `event_count` | number of events in the whole case |
| `variant_frequency` | frequency of the case's full activity sequence |
| `last_activity_*` | the case's final activity |
| `unique_activity_count`, `rework_count` | computed over the whole event sequence |

The supplier-history features (`supplier_historical_*`) now use only cases whose `end_time` is
strictly before the scored case's `start_time`. Cases ending at the same timestamp are excluded.
Cases without completed supplier history receive explicit sentinel values, not statistics from
the full dataset.

## Measured effect (mean ROC-AUC over 5 time-ordered folds)

| Feature set | Logistic regression | Random forest |
|---|---|---|
| All features | 0.968 | 0.960 |
| Drop end-only activity features | 0.734 | 0.678 |
| Creation-time only (also drop supplier history) | 0.737 | 0.679 |

These are means over five PO-grouped folds. Creation-time-only RF varies from 0.455 to 0.801;
the all-feature RF varies from 0.885 to 0.987. The configured target is highly imbalanced, so
these scores should not be read as evidence of useful creation-time decision quality.

## What this means

- The model should be described as a **case-progress-aware risk classifier**, not as a validated
  creation-time predictor. The full-case features become available as events accumulate.
- The configured-target AUC is inflated by the 95.5% breach base rate and completion-time features.
- A stronger creation-time claim needs a **prefix-based** formulation: build
  features from only the first *k* events (or first *t* hours) of each case and evaluate
  performance as a function of *k*. That is not implemented; this document only measures
  the gap.
- The dominant 95.5% breach rate also means configured-target ROC-AUC is measured on a heavily imbalanced set.
