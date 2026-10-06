# Measuring the AP controls honestly (Phase F3)

BPI 2019 has no fraud or anomaly labels. There is no way to know, from the real exception counts in
`docs/ap-controls.md`, which ones are genuine problems. So this document does not attempt to score
the controls against real data at all -- instead it plants synthetic anomalies at a **known, seeded
rate** into a copy of the real data, runs the controls, and reports recall and false-positive rate
against that planted set only.

Run: `python -m scripts.evaluate_ap_controls` -> `reports/ap_controls_evaluation.json`.

## What is planted, and why these three controls
| Control | Plant procedure |
|---|---|
| C1 (three-way match) | Remove the goods-receipt event from 40 real 3-way-match cases that currently have one |
| C3 (threshold splitting) | Add 40 synthetic 3-piece splits: same supplier, a new synthetic requester, 3 orders each 60% of the threshold, 1 day apart |
| C4 (duplicate invoice) | Clone 40 real invoices with a near-identical amount (±0.5%) 1 day later, a new case id |

C2 (order mismatch) and C5 (payment-block override) are **not** evaluated this way: both are
structural checks comparing real fields against each other, with no plausible "plant a synthetic
instance" procedure that wouldn't just be a differently-labeled real case. Their real counts stand
as reported, disclosed as unlabeled. C6 (Benford) is a vendor-level screening signal, not a per-case
detector, so case-level recall/FPR doesn't apply to it either.

## Results

| Control | Recall | 95% CI | Caught | False-positive rate (upper bound) | Real cases implicated |
|---|---|---|---|---|---|
| C1_three_way_match | **97.5%** | [87.1%, 99.6%] | 39/40 | 0.04% | 4 |
| C3_threshold_splitting | **66.7%** | [57.8%, 74.5%] | 80/120* | 0.98% | 90 |
| C4_possible_duplicate_invoice | **80.0%** | [65.2%, 89.5%] | 32/40 | 19.2% | 1,774 |

\* C3 plants 3-piece splits (120 planted case ids across 40 splits); the control reports one
exception per split event, crediting a planted case as "caught" if it appears in that exception's
evidence. Exactly 2 of each 3 planted pieces are credited by design: the rolling-sum threshold is
already crossed by the *second* piece (0.6 + 0.6 = 1.2x the threshold), so the exception fires
before the third piece's timestamp exists to be included -- the split IS detected, one piece simply
arrives after detection already happened. Recall would read 100% if credit were given per split
event rather than per case id; 66.7% is the more conservative, per-case number reported here.

**What "false-positive rate" means here, precisely**: the fraction of *real* (unplanted) cases that
this run's controls flagged. A flagged real case might be a genuine issue or might not -- there is
no ground truth to tell either way -- so this is an **upper bound** on the true false-positive rate,
not a confirmed one. C4's 19.2% is high and matches the real-data caveat in `docs/ap-controls.md`:
near-identical recurring amounts that aren't quite frequent enough to be excluded as routine are the
main driver.

## Isolation Forest vs. the rules
An unsupervised `IsolationForest` on case-level numeric features (event count, unique activities,
rework count, cycle time, order value) was scored against the union of all 200 planted anomalies
(same features, no rule logic), and its top-200-by-anomaly-score cases compared to the planted set.

**Result: 1 of 200 (0.5%) recall.** This is a real, reported negative result, not a bug: the planted
anomalies are almost entirely about *relationships between events or between cases* (a missing
event, a value shared with another case, a value shared with the same supplier's other orders) --
none of which shows up as an extreme value in a single case's own numeric feature vector. A generic
anomaly detector over case-level numbers is the wrong tool for this kind of anomaly; the rule-based
controls, which are built around exactly these relationships, are why they exist.

## Two real bugs this evaluation caught before publishing
Running the planted evaluation is what surfaced both fixes described in `docs/ap-controls.md`'s "two
real bugs" sections for C3 and C4 -- the first versions of both controls scored recall near 0% (C3)
or were flooded with false positives that made recall meaningless to interpret (C4) until the loop
and filtering bugs were found and fixed. The numbers above are measured on the fixed code.

## Limits
- 40 planted instances per control, one seed -- CIs are wide (see the table). This measures whether
  the *mechanism* works, not a production-grade false-positive budget.
- Planting is simple (single amount/time perturbations); a more sophisticated adversary evading
  detection deliberately is not modeled.
- The "false-positive rate" caveat above applies throughout -- read it as an upper bound, always.
