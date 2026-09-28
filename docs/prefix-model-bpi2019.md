# Early warning from the first k events (BPI 2019, this project's own data)

Reproduce: `python -m scripts.prefix_model_bpi2019`

**Question:** [`prediction-time-availability.md`](prediction-time-availability.md) showed the deployed
model's skill comes from features only known late in a case. Can a model built from *only* the first
k events do better than creation-time features, and how close does it get to the full-case model?

**Design (fixed before results were seen):**
- Label: cycle time above the per-category **training-window** p50 / p75 (same as
  [`less-degenerate-target.md`](less-degenerate-target.md)); last 20% of cases by start time held out.
- Population at k: cases with ≥ k events **and still under their target at event k** (otherwise the
  label is already determined by elapsed time). The creation-only baseline is scored on the *same rows*.
- Supplier history is strictly causal (only cases already *ended* before this case started). The main
  pipeline's version uses earlier-*started* cases, whose outcomes may not yet be known — a separate,
  smaller instance of the same availability problem (not changed in the deployed model).
- k = 2, 3, 5 (only 15.7% of BPI 2019 cases reach 10 events).

## Results (RF; ROC-AUC; 95% CI = paired bootstrap over held-out cases, 500 resamples)

| Target | k | Test n | Base rate | Creation-only | First k events | Gain [95% CI] |
|---|---|---|---|---|---|---|
| p50 (85 d) | 2 | 600 | 0.517 | 0.538 | 0.620 | +0.082 [+0.056, +0.109] |
| | 3 | 591 | 0.516 | 0.516 | 0.641 | +0.125 [+0.093, +0.157] |
| | 5 | 524 | 0.462 | 0.573 | 0.637 | +0.064 [+0.027, +0.099] |
| p75 (113 d) | 2 | 600 | 0.270 | 0.669 | 0.698 | +0.029 [+0.011, +0.047] |
| | 3 | 591 | 0.266 | 0.659 | 0.699 | +0.040 [+0.019, +0.064] |
| | 5 | 579 | 0.257 | 0.667 | 0.709 | +0.042 [+0.017, +0.068] |

(LR at k=5, p75: creation-only 0.673 → first-5 0.762; LR results are in the script output. For scale,
LR creation-only at p50 dips to 0.46–0.50, i.e. ±0.05 of noise is normal here.)

## Conclusions

1. **Real but small.** The first events add a statistically detectable +0.03 to +0.13 ROC-AUC over
   creation-time features (intervals exclude zero), but absolute early-warning quality is **0.62–0.76**,
   well below the **0.83–0.89** the full-case model reaches. On this data an early-warning score would
   be weak.
2. **Different from BPI 2012**, where three events reached 0.77–0.92
   ([`external-validation-bpi2012.md`](external-validation-bpi2012.md)). BPI 2012's early events carry
   routing (quick decline vs. continue); BPI 2019's first events mostly don't discriminate.
3. So P1 remains, honestly, a **late-stage / triage** risk score. This is a measured result about
   this dataset, not a claim that early warning is impossible.

## Limits

One split, one seed; the bootstrap covers test-set sampling only, not training variance or a
different split. Test sets are 520–600 cases. Nothing here changes the deployed model.

## Regenerated 2026-09-28 (zero-duration cases excluded)
RF, first k events vs creation-only on the same rows (gain, 95% CI):
p50 k=2 0.671 vs 0.674 (-0.004 [-0.031, +0.022], no gain); k=3 0.673 vs 0.615 (+0.058); k=5 0.670 vs 0.637 (+0.032).
p75 k=2 0.697 vs 0.667 (+0.030); k=3 0.696 vs 0.652 (+0.045); k=5 0.725 vs 0.692 (+0.033).
Absolute early-warning quality: **0.67-0.73** (was 0.62-0.76). The conclusion stands and is a little weaker: first
events add a small, mostly significant amount, and at p50 k=2 nothing at all.
