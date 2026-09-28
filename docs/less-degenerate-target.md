# Re-evaluating on a less degenerate SLA target

Reproduce: `python -m scripts.target_sensitivity`

## Why

The configured SLA targets (`config/sla.yaml`, 10–14 days) are far below BPI 2019's real cycle
times (median ≈ 84 days), so 93.8% of cases breach and the held-out split (last 20% by start
time) is **97.0% breaches — 18 negatives out of 600**. ROC-AUC there is computed over 18
negatives and is high-variance. This re-runs the same models and features with the breach label
redefined from data, so the metric is measured where negatives are not rare.

Target definitions: per-category percentile of cycle time computed on the **training window only**
(first 80% of cases by start time; categories with < 30 training cases fall back to the global
percentile), so the test period never influences the label. Models: logistic regression and
random forest, same hyperparameters as `src/ml/train.py`. `sla_target_hours` and the
supplier-history features are recomputed from the new label.

## Results

| Target | Holdout base rate (negatives) | Feature set | Model | Holdout ROC-AUC | Holdout PR-AUC | 5-fold mean ROC-AUC (min–max) |
|---|---|---|---|---|---|---|
| Configured (10–14 d) | 97.0% (18) | all | LR | 0.910 | 0.997 | 0.949 (0.860–0.999) |
| | | all | RF | 0.985 | 1.000 | 0.944 (0.847–0.976) |
| | | creation-time only | LR / RF | 0.740 / 0.707 | 0.982 / 0.984 | 0.595 / 0.540 (0.13–0.93) |
| p50 (85 d default) | 51.7% (290) | all | LR | 0.783 | 0.826 | 0.908 (0.827–0.955) |
| | | all | RF | 0.830 | 0.812 | 0.898 (0.825–0.945) |
| | | creation-time only | LR / RF | 0.515 / 0.514 | 0.497 / 0.520 | 0.669 / 0.626 (0.49–0.85) |
| p75 (113 d default) | 27.0% (438) | all | LR | 0.833 | 0.690 | 0.902 (0.860–0.937) |
| | | all | RF | 0.885 | 0.745 | 0.909 (0.890–0.941) |
| | | creation-time only | LR / RF | 0.648 / 0.648 | 0.379 / 0.371 | 0.717 / 0.691 (0.60–0.81) |

## What this says

1. **The 0.986 headline overstates skill.** With a non-degenerate label the same model and
   features score **0.83–0.89 holdout ROC-AUC** (0.90–0.91 averaged over 5 folds). Still a real
   signal, but not near-perfect ranking.
2. **PR-AUC is the honest number on the configured target.** 0.997–1.000 there is essentially the
   base rate (0.970); on p75 PR-AUC 0.745 against a base rate of 0.270 is a genuine lift.
3. **Creation-time-only features are close to chance on the balanced target** (holdout 0.51 at
   p50, 0.65 at p75), consistent with `docs/prediction-time-availability.md`: the model's skill
   comes from features that describe a case that has already progressed.
4. **RF vs LR** ordering on holdout (RF ahead) is within the fold-to-fold spread and matches the
   earlier paired test (`docs/statistical-significance.md`) finding no significant difference.

## Not done

- The deployed model still uses the configured target; changing what "breach" means would change
  API semantics and is a product decision, not an evaluation one.
- Percentile targets are a modelling choice for testing, not a real contractual SLA.

## Regenerated 2026-09-28 (zero-duration cases excluded)
104 truncated cases with zero measured duration were labelled "not breached"; they are now excluded (2,896 cases,
580 held out). Holdout ROC-AUC, all features: p50 LR 0.879 / RF 0.876; p75 LR 0.841 / RF 0.870 -> **0.84-0.88**
(was 0.83-0.89). Creation-time only: 0.62-0.67 (was 0.51-0.65). Configured target: RF 0.972, 15 negatives of 580.
Reproduce: `python -m scripts.target_sensitivity`. Tables above are the previous run.
