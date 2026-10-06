# Sequence model (GRU/LSTM) vs prefix random forest (Phase 5)

**Evaluation status:** these results use the older case-level temporal split and have not been rerun with
purchase-order-grouped holdouts. Do not compare these numbers directly with the updated full-case model
results until the sequence evaluation is repeated under the same protocol.

Reproduce: `python -m scripts.sequence_model_bpi2019` (≈2 min on CPU; writes
`reports/sequence_model_results.json`, logs every run to MLflow experiment `sequence_vs_prefix_rf`).
Code: `src/ml/sequence_model.py`. Install torch first (`requirements-sequence.txt`).

**Question:** does a recurrent model over the first *k* events beat the prefix random forest from
[`prefix-model-bpi2019.md`](prefix-model-bpi2019.md), for k ∈ {1, 2, 3, 5}?

**Design** (same as the prefix script; fixed before results): label = cycle time above the
training-window per-category p50 / p75; population = cases with ≥ k events still under target at event k;
strictly causal supplier history; last 20% by start time held out; RF and neural nets scored on the
**same rows**. Neural nets: embedded activities + log elapsed / log inter-event hours per step, static
features (category, start hour/day/month, supplier history) joined to the final hidden state; hidden 32,
Adam, early stopping on the last 15% of the training window, class-weighted BCE, **3 seeds** (metrics
are seed means; "gain" is the 3-seed *averaged-prediction* ROC-AUC minus the RF's, with a 95% paired
bootstrap over test cases). RF: 200 trees, depth 8, one seed. No hyperparameter search for either.

## Results (ROC-AUC; CPU latency = median ms to score one case, single thread)

| Target | k | Test n | RF | GRU | LSTM | GRU − RF [95% CI] | RF / GRU / LSTM latency |
|---|---|---|---|---|---|---|---|
| p50 | 1 | 600 | **0.598** | 0.534 | 0.512 | −0.072 [−0.099, −0.044] | 24.0 / 0.7 / 0.9 ms |
| p50 | 2 | 600 | **0.653** | 0.640 | 0.620 | −0.006 [−0.032, +0.020] | 24.0 / 0.8 / 0.9 |
| p50 | 3 | 591 | **0.664** | 0.589 | 0.584 | −0.077 [−0.111, −0.044] | 24.6 / 0.9 / 1.0 |
| p50 | 5 | 524 | **0.618** | 0.552 | 0.563 | −0.082 [−0.124, −0.038] | 24.2 / 1.1 / 0.9 |
| p75 | 1 | 600 | 0.651 | **0.665** | 0.657 | +0.012 [−0.011, +0.033] | 24.3 / 0.8 / 0.9 |
| p75 | 2 | 600 | 0.710 | **0.757** | 0.706 | +0.048 [+0.025, +0.074] | 24.9 / 0.9 / 1.1 |
| p75 | 3 | 591 | 0.691 | **0.739** | 0.696 | +0.070 [+0.037, +0.105] | 24.1 / 0.9 / 0.9 |
| p75 | 5 | 579 | 0.722 | **0.762** | 0.753 | +0.066 [+0.025, +0.105] | 24.3 / 1.1 / 0.9 |

PR-AUC and Brier for every cell are in the JSON. Brier is the caveat: the GRU's is **worse** than the
RF's at p75 k=1–2 (0.338 vs 0.318; 0.284 vs 0.231) and equal-ish beyond, i.e. its scores rank a bit
better but are less well calibrated; the LSTM's Brier is better than the RF's at p75 k=5 (0.186 vs 0.218).

## Verdict

**Mixed, and not enough to replace the RF.**
- p75 (26–27% base rate): the GRU beats the RF by +0.05 to +0.07 ROC-AUC for k ≥ 2 (CIs exclude 0), and is
  ~25× faster per case on CPU. Absolute quality is still only **0.74–0.76** — below the 0.83–0.89 of the
  late-stage model — so this does not change the project's headline.
- p50 (coin-flip target): the RF wins or ties at every k, by up to 0.08. The sequence model is worse where
  there is the least signal, consistent with a neural net over-fitting ~2,400 training rows.
- The RF's 24 ms is scikit-learn's per-call overhead on a 200-tree forest, not a fundamental cost; a batched
  or trimmed forest would narrow the latency gap. Latency is not the deciding factor here.
- LSTM ≤ GRU on nearly every cell, with seed variance up to ±0.09 ROC-AUC (p75 k=3) — treat single-cell
  differences under ~0.05 as noise.

**Update 2026-09-25: the GRU is now served** for early warning, see [`early-risk.md`](early-risk.md). The decision below (RF stays) still holds for the full-case triage model.

**Decision at the time: the random forest stays** for the deployed full-case model, and nothing from this phase
was deployed. The GRU at p75, k=2–3 was named as the early-warning candidate, pending a calibration step; that
step (Platt scaling on the validation slice) and the serving path now exist -- see [`early-risk.md`](early-risk.md).

## Limits
One split, ~500–600 test cases per cell; bootstrap covers test sampling, not a different split. Seeds vary
only initialisation/shuffling. No tuning of either model, so a tuned RF could narrow the p75 gap, and a
tuned GRU could improve p50. The p50 and p75 targets are synthetic percentile definitions, not a business SLA.
