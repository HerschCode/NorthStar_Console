# Model vs. simple rules -- PO-isolated evaluation

Reproduce: `python -m scripts.model_vs_rules` -> `reports/model_vs_rules.json`.
The current run uses 1,708 held-out cases from a forward-in-time split that keeps each purchase order
on one side of the boundary. Confidence intervals use 500 paired bootstrap resamples of purchase
orders, not individual cases. The target is the per-category p75 cycle-time threshold derived from
the training window (holdout breach rate 24.4%).

## Precision at treated shares

| Strategy | 5% | 10% | 20% | 30% |
|---|---:|---:|---:|---:|
| Existing feature set, completed-only history (M0) | 0.329 | 0.263 | 0.404 | 0.424 |
| M0 + supplier workload features (M1) | 0.106 | 0.140 | 0.456 | 0.490 |
| Compact features + workload (M2) | 0.235 | 0.298 | 0.462 | 0.449 |
| Explicit completed-only history (M3) | 0.176 | 0.222 | 0.462 | 0.469 |
| No supplier history (M4) | 0.824 | 0.561 | 0.430 | 0.428 |
| Supplier-history rule (completed cases only) | 0.235 | 0.269 | 0.263 | 0.275 |
| Supplier-volume rule (training window) | 0.235 | 0.216 | 0.316 | 0.229 |
| Order-value rule | 0.600 | 0.468 | 0.363 | 0.330 |
| Random | 0.271 | 0.228 | 0.269 | 0.254 |

95% cluster-bootstrap intervals and paired model-minus-rule comparisons are in the JSON report.
For the corrected M0, the model does not show a statistically clear advantage over order value at
any tested share. At 30%, M0 precision is 0.424 vs 0.330 for order value; paired difference is
0.094 (95% CI [-0.007, 0.189]). M1 has a 30% difference of 0.160 (95% CI [0.047, 0.276]),
but it is an evaluation variant, not the currently deployed model.

## Findings and limits

1. Supplier-history breach rate and median cycle time now use only cases whose `end_time` is
   strictly before the scored case's `start_time`; a tie at the timestamp is not treated as prior.
2. The PO-aware temporal holdout removes boundary-crossing purchase orders. The confidence intervals
   are much wider than the earlier case-level bootstrap, especially at 5–20% targeting.
3. Order value is a strong baseline, particularly at small targeting shares. M0 does not establish
   an advantage over it; M1's apparent 30% gain needs independent temporal validation.
4. Top-ranked model cases are concentrated in one supplier, so precision does not establish that
   the model generalizes across suppliers.
5. This is an offline evaluation on a static historical event log. The revised model has not been
   retrained and deployed, and no real interventions or outcomes are available to establish lift.

## Error analysis and feature importance

The workload variant is not uniformly better: at 10% treated, M1 precision is 0.140 versus
0.468 for the order-value rule; at 30%, M1 is 0.490 versus 0.330, with a paired 95% interval
for the difference of [0.047, 0.276]. This argues against presenting workload features as a
general win at every intervention capacity.

The highest-AUC model in this run is M3 (leak-free completed-case history). Its top permutation
importance is `sup_open_cases` (+0.0352 ROC-AUC), followed by the last activity
`Record Invoice Receipt` (+0.0117), `sup_ended_n` (+0.0096), and `sup_ended_median` (+0.0072).
These are permutation importances on this single held-out window, not causal effects.

At 5%, the model's top set and the completed-case supplier-history rule's top set do not overlap:
model-only precision is 0.1765 versus 0.2118 for rule-only selections. The model top set contains
only one distinct supplier, compared with 53 suppliers in the rule top set. This concentration
and the wide cluster-bootstrap intervals are material error-analysis findings, not evidence that
the model generalizes better. All point estimates and 500-resample purchase-order bootstrap
intervals remain in `reports/model_vs_rules.json`.
