# Probability calibration: the bug, the fix, the numbers

Reproduce: `python -m scripts.calibration_method_comparison` (raw JSON: `reports/calibration_comparison.json`),
`python -m scripts.calibration_analysis`, `python -m scripts.cost_threshold_analysis`.

## The bug (found 2026-09-25, shipped since the calibration feature was added)
`save_best_model()` served `CalibratedClassifierCV(FrozenEstimator(forest), method="isotonic")` **fitted on the
forest's own training rows**. A random forest scores rows it was trained on close to 0 or 1; isotonic regression
fitted to those scores is a coarse step function, so many test cases receive the *same* probability. On the
configured (97%-breach) target the old served model produced **7 distinct scores for 600 test cases** and a
ROC-AUC of 0.69 when rebuilt (the deployed artifact measured 0.665) against 0.983-0.985 for the raw forest.
`meta.json` recorded the raw forest's ROC-AUC (0.9857), so the served-vs-raw gap was invisible in the metadata.

## The fix
1. **Temporal calibration split of the training window:** the forest is fitted on the earliest 80% and the
   calibrator on the latest 20%; the test window is never touched (`fit_calibrated` in `src/ml/train.py`).
2. **Sigmoid (Platt) by default.** It is strictly monotonic, so it cannot change ROC-AUC (asserted in
   `tests/test_calibration.py`). Isotonic is used only if, judged out-of-sample inside the calibration slice, it
   wins on Brier score without losing ROC-AUC **and** the slice has at least 50 examples of each class. The first
   version of that rule had no minimum and picked isotonic on the 97% target (16 negatives in the slice), which
   cost 0.075 ROC-AUC on the test window; the minimum is the fix, and the comparison below is why.
3. A small custom wrapper (`src/ml/calibration.py`) instead of sklearn's `CalibratedClassifierCV`, because that
   class cross-fits and refuses a slice with fewer than 5 examples of a class.
4. `meta.json` records `served_model` and `raw_model` metrics separately (`roc_auc`, `pr_auc`, `brier_score`,
   `n_unique_scores`); top-level `roc_auc` is the served model's. The pipeline flow's promotion gate also compares
   the served model's ROC-AUC now.
5. `/orders/{id}/risk` response shape is unchanged (sigmoid is monotonic, so a separate raw score adds nothing for
   ranking, and no `risk_score_raw` field was added).

## Method comparison on the held-out test window (n = 600)
| Target | Method | Brier | ROC-AUC | PR-AUC | Tied scores |
|---|---|---|---|---|---|
| Configured (97% breach; 18 test / 16 calibration negatives) | raw forest | 0.0209 | 0.9831 | 0.9995 | 295 |
| | **old: isotonic fitted on training rows** | 0.0217 | **0.6926** | 0.9813 | 593 |
| | isotonic, held-out slice | 0.0213 | 0.9083 | 0.9945 | 588 |
| | **sigmoid, held-out slice (served)** | 0.0220 | 0.9831 | 0.9995 | 295 |
| p75 (27% breach; 438 test / 387 calibration negatives) | raw forest | 0.1930 | 0.8584 | 0.7038 | 194 |
| | old: isotonic fitted on training rows | 0.1474 | 0.8758 | 0.6968 | 583 |
| | isotonic, held-out slice | 0.1425 | 0.8493 | 0.6496 | 583 |
| | **sigmoid, held-out slice (served)** | **0.1412** | 0.8584 | 0.7038 | 194 |

Tied scores = 600 minus the number of distinct scores. The raw forest's own ties come from many cases receiving the
same leaf-vote share; sigmoid adds none (295 and 194 both before and after).

## What this does and does not show
- **Sigmoid wins on both targets**, as expected for a slice with few negatives; isotonic loses ROC-AUC on both.
- **Calibration helps only where there is something to calibrate.** On p75 it cuts Brier by 27% (0.193 to 0.141). On
  the 97%-breach target it does *not* improve Brier (0.0209 raw to 0.0220), and the same holds for logistic
  regression and gradient boosting (`docs/evaluation.md`). The old table's "+0.003 improvement" and GB Brier skill
  score of 0.526 came from in-sample calibration and are retracted.
- **Cost of a held-out slice:** the forest sees 20% less training data. ROC-AUC moved 0.9853 to 0.9831 (configured)
  and 0.8757 to 0.8584 (p75). That is the price of an honest calibrator; the alternative (calibrate on training rows)
  is the bug.
- **Threshold analysis redone:** the old recommended operating point (t = 0.35, 8 false alarms) was computed on the
  flawed scores. Now t = 0.65, 9 false alarms, 100% recall, 591 of 600 cases flagged. With a 97% base rate the
  threshold barely discriminates either way.
- Only 16-18 negatives support every configured-target number here; treat them as noisy.
- Figures elsewhere that quote ROC-AUC 0.986 / 0.985 (statistical-significance, less-degenerate-target,
  orchestration) come from experiments that fitted the forest on the full training window and are correct for those
  experiments; the current deployed model scores 0.9835 (`models/sla_risk_model.meta.json`).

## Same mistake, other project
Calibrating on the training rows, and fine-tuning P2 while testing on answers that were in its training data, are the
same error: evaluating or fitting a downstream step on data the model has already seen. What caught this one was
computing the *served* artifact's metric independently of the number stored in its metadata.


## Regenerated after the zero-duration label fix (2026-09-28)
With 104 unmeasurable cases excluded (held-out window 580 cases, 15 non-breaches on the configured target), sigmoid is
still chosen on both targets. Configured: raw ROC-AUC 0.9785, Brier 0.0282; p75: raw ROC-AUC 0.8524, Brier 0.214 raw.
Deployed model: served ROC-AUC = raw = 0.975. `reports/calibration_comparison.json` holds the full table; the table
above is the previous run. Threshold analysis now recommends t = 0.10 (flag everything; 15 false alarms on 580) --
with 97% breaching, the configured target leaves nothing to threshold.
