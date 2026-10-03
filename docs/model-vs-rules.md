# Model vs. simple rules -- with causal features and uncertainty

Reproduce: `PYTHONPATH=. python -m scripts.model_vs_rules` -> `reports/model_vs_rules.json`
(500-draw paired bootstrap, 1,822 held-out cases, base breach rate 24.9%). The design was written in the
script's docstring before the run; the question was "does adding the features the rules use make the model beat them?"

## Result: no, at small treated shares -- and the reason is a leak, not a weak model

Precision@k, held-out latest 20% of cases (95% CI in `reports/model_vs_rules.json`):

| Strategy | 5% | 10% | 20% | 30% |
|---|---|---|---|---|
| Model as deployed (M0) | 0.47 | 0.52 | 0.46 | 0.46 |
| Model + workload features (M1) | 0.18 | 0.23 | 0.50 | 0.52 |
| Model, supplier history made strictly causal (M3) | 0.19 | 0.23 | 0.49 | 0.51 |
| Model, no supplier history (M4) | 0.36 | 0.24 | 0.46 | 0.45 |
| Rule: supplier breach rate, as shipped | 0.69 | 0.57 | 0.45 | 0.38 |
| Rule: supplier breach rate, strictly causal (ended cases only) | 0.20 | 0.21 | 0.23 | 0.26 |
| Rule: supplier volume (training window) | 0.79 | 0.47 | 0.30 | 0.23 |
| Rule: order value | 0.58 | 0.46 | 0.36 | 0.32 |
| Random | 0.28 | 0.23 | 0.23 | 0.23 |

Findings:

1. **Adding the rules' features did not make the model beat them.** M1/M2 were *worse* than M0 at 5-10%.
2. **The shipped supplier-history feature looks ahead.** `supplier_historical_breach_rate` uses the outcomes of
   earlier-*started* cases, some of which had not finished when the later case started. Restricting to cases that had
   *ended* before the case began (`rule_ended`) drops that rule from 0.69 to 0.20 at 5% -- indistinguishable from
   random. The same change cuts model ROC-AUC from 0.789 to 0.764 (M3) and, with history removed entirely, to 0.725 (M4).
   So ~0.025-0.065 of the reported AUC and the rule's small-share advantage trace to this look-ahead.
   It is the top permutation feature by 4x (0.040 vs <=0.011 for the next).
3. **Volume's 5% win is concentration.** "Busiest supplier" picks come from 2 suppliers; it falls to random by 20%.
4. **Against rules that are actually deployable** (everything except the look-ahead rule), the model loses at 5%,
   ties at 10%, and wins at 20% (+0.10, CI [0.03, 0.16]) and 30% (+0.14, [0.09, 0.18]) over order-value.
   The model's genuine advantage is at broad targeting (>=20% of cases), not at the top of the list.
5. **Model top-5% is degenerate when workload features are added**: M1's top 5% is 7 purchase orders from one supplier.
6. **Uncertainty is understated.** Items of one purchase order share vendor, timing and outcome (716 POs for 1,822
   cases), so case-level bootstrap CIs are too narrow, most of all at 5%. Treat 5% comparisons as indicative only.

## What this changes

- README and `docs/uplift-method.md` claims that the supplier-history rule is "causal" are withdrawn: it is causal
  only in start order, not in outcome availability.
- Not yet done (a behaviour change, so deliberately left for a decision): make `_add_derived_features` ended-only and
  retrain. Expect headline ROC-AUC to fall by about 0.025 (0.789 -> 0.764 on this split) and the deployed model,
  `meta.json`, and the ranges quoted in the docs to move with it.
