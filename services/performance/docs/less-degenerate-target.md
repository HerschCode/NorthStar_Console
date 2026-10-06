# Re-evaluating on a less degenerate SLA target

Reproduce: `python -m scripts.target_sensitivity` (PO-isolated temporal holdout and grouped folds)

## Why

The configured SLA targets (`config/sla.yaml`, 10–14 days) are far below BPI 2019's real cycle
times (median ≈ 84 days), so 95.5% of measurable cases breach under the configured rules. The
latest PO-isolated holdout is **96.3% breaches — 63 negatives out of 1,708**. ROC-AUC on this
target remains sensitive to the small negative class. Percentile targets provide a more balanced
evaluation, but are evaluation labels, not contractual SLAs.

Target definitions: per-category percentile of cycle time computed on the **training window only**
(first 80% of purchase-order groups by start time; categories with < 30 training cases fall back to
the global percentile), so the test period never influences the label. Models: logistic regression
and random forest. Holdout groups and 5-fold validation keep purchase orders intact. Supplier-history
features use only cases completed strictly before each case starts.

## Results

| Target | Holdout base rate (negatives) | Feature set | Model | Holdout ROC-AUC | Holdout PR-AUC | 5-fold mean ROC-AUC (min–max) |
|---|---|---|---|---|---|---|
| Configured (10–14 d) | 96.3% (63) | all | LR | 0.950 | 0.995 | 0.968 (0.943–0.998) |
| | | all | RF | 0.983 | 0.999 | 0.963 (0.897–0.988) |
| | | creation-time only | LR / RF | 0.752 / 0.702 | 0.984 / 0.981 | 0.737 / 0.666 (0.388–0.871) |
| p50 (78 d default) | 54.5% (777) | all | LR | 0.861 | 0.826 | 0.843 (0.768–0.904) |
| | | all | RF | 0.865 | 0.889 | 0.821 (0.730–0.872) |
| | | creation-time only | LR / RF | 0.869 / 0.814 | 0.882 / 0.852 | 0.838 / 0.803 (0.715–0.904) |
| p75 (108 d default) | 24.5% (1,290) | all | LR | 0.763 | 0.454 | 0.795 (0.740–0.832) |
| | | all | RF | 0.763 | 0.559 | 0.792 (0.742–0.847) |
| | | creation-time only | LR / RF | 0.704 / 0.641 | 0.337 / 0.300 | 0.767 / 0.723 (0.670–0.857) |

## What this says

1. **Configured-target ROC-AUC is misleadingly high.** The holdout has only 63 negatives, and
   PR-AUC 0.995–0.999 is close to the 0.963 base rate.
2. **Realistic-target results depend on target definition.** Full-feature holdout ROC-AUC is
   0.763–0.865; p75 PR-AUC is 0.454 (LR) / 0.559 (RF), against a 0.245 base rate.
3. **Creation-time performance is not uniformly weak, but is unstable by target/model.** It
   ranges from 0.641 (p75 RF) to 0.869 (p50 LR); p75 RF grouped-fold AUC ranges 0.670–0.798.
4. **Treat these as offline experiments, not deployment results.** The deployed model has not
   been retrained with the revised feature code and group-aware split.

## Not done

- The deployed model still uses the configured target; changing what "breach" means would change
  API semantics and is a product decision, not an evaluation one.
- Percentile targets are a modelling choice for testing, not a real contractual SLA.

## Regenerated 2026-10-03
The current table uses 9,108 measurable cases, a 1,708-case holdout, and PO-isolated grouped folds.
The configured target has 63 held-out negatives; p50 and p75 targets have 777 and 1,290 respectively.
Logistic regression emitted convergence warnings in two evaluation folds; interpret its results cautiously
until the evaluation pipeline scales numeric inputs or otherwise verifies convergence.
