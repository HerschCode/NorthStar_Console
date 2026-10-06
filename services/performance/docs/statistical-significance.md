# Statistical significance: current PO-grouped results

## Current analysis (2026-10-03)

The analysis now uses a forward-in-time holdout with purchase orders kept intact and a 2,000-draw
purchase-order cluster bootstrap. The test set contains 1,708 cases. On the configured SLA target,
the random forest ROC-AUC is 0.9783 (95% cluster-bootstrap CI [0.9660, 0.9875]); this remains a
weak business result because 96.3% of test cases breach.

Five paired PO-grouped temporal folds compare random forest and logistic regression:

```
RF: [0.9586, 0.9841, 0.8851, 0.9868, 0.9839]
LR: [0.9792, 0.9750, 0.9464, 0.9976, 0.9428]
Mean RF - LR: -0.0085
Paired t-test p=0.6413; Wilcoxon p=0.6250
```

There is no statistically clear algorithm winner across these folds. Logistic regression emitted
convergence warnings in two folds, so its scores also need caution. With only five folds, the tests
have limited power. Full raw output: [`statistical_significance_raw_result.json`](statistical_significance_raw_result.json).

The analysis below is the earlier row-level evaluation. It is retained as history only; its bootstrap
intervals and fold results are superseded by the PO-grouped results above.

## Superseded row-level analysis

Every number in this repo's model comparison — ROC-AUC 0.986 vs 0.910 — was a
single point estimate from one train/test split, with no uncertainty
attached and no test of whether the gap could be noise. That's a real gap:
"model A beats model B" said with one number each is not evidence of a
reliable difference, it's an anecdote with a metric attached. Ran
`scripts/statistical_significance.py` to close it — and it produced a result
that pushes back on this project's own headline claim, not one that
comfortably confirms it.

## 1. Bootstrap 95% CI on the deployed model's held-out ROC-AUC

Percentile bootstrap (2,000 resamples) on the fixed test-set predictions:

```
point estimate: 0.9857
95% CI: [0.9742, 0.9949]
```

This part holds up fine — the deployed random forest's 0.986 on the single
held-out split is precise, not a fluke of one lucky test set. That's not the
part in question.

## 2. Paired significance test: random forest vs. logistic regression, across the SAME 5 TimeSeriesSplit folds

This is the real test the single-split comparison never got. Same 5
chronological folds for both models (paired, not independent samples), so
the per-fold *difference* is the right quantity — a matched comparison, the
same way you'd compare two treatments on the same patients rather than two
different, unmatched groups.

```
RF fold ROC-AUC:  [0.9624, 0.8483, 0.9710, 0.9737, 0.9688]
LR fold ROC-AUC:  [0.9235, 0.9908, 0.9714, 0.9990, 0.8592]
per-fold diff (RF-LR): [+0.0389, -0.1425, -0.0004, -0.0253, +0.1096]
mean diff: -0.0039

paired t-test:          t=-0.095, p=0.929
Wilcoxon signed-rank:    W=7.000,  p=1.000
```

**Random forest wins 2 of 5 folds, logistic regression wins 2, one is
essentially tied. Neither test finds a significant difference — not close.**
This directly contradicts this README's existing "Why Random Forest? ...
random forest wins decisively (0.986 vs LR's 0.910)" framing, which was built
from exactly one train/test split (the most recent chronological chunk).

## The honest reconciliation

Both results are real; they're just measuring different things, and the
single-split number happened to land on the RF-favorable end of a
distribution that — properly measured — straddles zero:

- **The single split tests one specific time window** (the most recent ~20%
  of the data). Fold 5 in the CV breakdown above (RF 0.969, close to the
  single-split story) covers similar recent territory. Fold 2, where LR wins
  by 0.14 (0.848 vs 0.991), covers an earlier window — and it's the single
  biggest swing in either direction across all 5 folds.
- **The variance across folds is large relative to the mean difference
  between models.** RF's own fold scores range from 0.848 to 0.974 — a wider
  spread than the gap between RF and LR in most folds. That's the real
  signal here: **which model wins depends more on which time window you test
  than on which algorithm you pick**, for this feature set and this amount of
  data.
- **n=5 paired folds is genuinely small**, and both tests' exact p-values
  should be read as supporting evidence, not proof — but the small-n caveat
  cuts against overclaiming *either* direction, including the original
  "random forest wins decisively" claim, not just against this finding.

## What this changes, and what it doesn't

- **The deployed model stays random forest** — this finding is "statistically
  indistinguishable from logistic regression across time windows," not
  "logistic regression is actually better." There's no evidence-based reason
  to swap. RF also doesn't need feature scaling and handles the 541
  one-hot columns without LR's convergence warnings (see `README.md`'s
  own note on `max_iter`), which are real, non-statistical reasons to prefer
  it operationally when the accuracy case is a wash.
- **The README's "why random forest" section needed correcting, not
  deleting** — the honest version is "the single-split number that motivated
  this choice doesn't hold up under proper cross-validated comparison; the
  two models are statistically tied, and RF was kept for operational reasons
  once the accuracy argument stopped being the deciding one." That's a
  materially different, more defensible claim than "wins decisively," and a
  more honest one than quietly leaving the old claim standing next to a new
  script that contradicts it.
- **This is the same category of finding as the P3 gateway's leakage bug and
  the P2 retrieval-number corrections** — a headline number that looked
  clean because it was never tested properly, caught by actually running the
  test rather than assuming a single split generalizes.

## Reproducing this

```bash
python -m scripts.statistical_significance
```

Raw numbers: [`statistical_significance_raw_result.json`](statistical_significance_raw_result.json).
