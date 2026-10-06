# Intervention ROI and uplift method (Phase 6)

> **This is a simulation.** No intervention has ever been run on this data (a public event log), so no
> effect size here was measured. Every number in `/roi/summary` is an arithmetic consequence of the
> assumptions in [`config/interventions.yaml`](../config/interventions.yaml), and the API says so in
> its `label` field and echoes the assumptions back.

## What exists
- `analytics.interventions` (`sql/schema/005_create_interventions.sql`): one row per action on a case:
  type, model risk at the time, cost, outcome once known, and `is_simulated`. One row per (case, type).
- `POST /interventions` (admin role): validates type against the policy, risk in [0, 1], cost ≥ 0,
  and that the case exists (422 / 404 / 409 on duplicates). It uses a separate write engine
  (`LEDGER_DB_USER`/`LEDGER_DB_PASSWORD`, falling back to `DB_USER`), because the API's normal
  credential is the SELECT-only `ops_api_reader` role by design.
- `GET /roi/summary`: simulated and logged rows reported **separately**, never blended.
- `python -m scripts.simulate_interventions`: policy replay — act on the top 20% of the held-out window
  (last 20% by start time, unseen in training) by model risk with `expedite_approval`; loads
  `is_simulated = TRUE` rows, idempotently.

## Arithmetic
For each treated case, the assumed relative effect *e* of its type cuts breach probability by *e*.
- Avoided breaches (by model risk) = Σ risk × e. Avoided (by observed outcomes) = Σ e over treated
  cases that did breach (only where the outcome is known).
- Net value = avoided × `breach_cost` − Σ cost.
- Break-even effect = Σ cost / (`breach_cost` × Σ risk): the uniform effect at which net value is zero.
  This is the honest headline, because it doesn't depend on a guessed effect.

## Live simulation result
120 interventions, cost 3,000, 17.96 avoided breaches by model risk (18.0 by observed outcomes), net
+4,183 (+4,200). (Rerun after the calibration fix; earlier: 18.0 / 17.4, +4,200 / +3,960.) **Break-even effect: 6.25%** — under these assumed costs and a 400 breach cost, the
action pays for itself if it prevents more than ~1 breach in 16 among treated cases.

## Why this is not uplift, and what real uplift needs
- **Effect sizes are assumed.** Uplift is the *difference in outcome caused by treatment*; it cannot be
  read from a risk model. A risk score says who is likely to breach, not who is *helped* by an action —
  high-risk cases may be unfixable, and the best targets are often mid-risk.
- **The counterfactual is missing.** A real estimate needs a randomized holdout (treat a random share of
  flagged cases, leave the rest), then compare breach rates, ideally with an uplift model (two-model or
  causal-forest) trained on that data. The ledger now collects it: see "Randomized holdout" below.
- **Risk scores are the served model's probabilities** -- the sigmoid-calibrated random forest
  (`bundle["model"]`, a `HeldOutCalibratedClassifier`; calibrated on a held-out temporal slice, see
  [`calibration.md`](calibration.md)). The configured SLA targets make ~94%
  of cases breach, so the top-20% by risk are nearly all ≈1.0 risk. Consequently "by model risk" and "by
  observed outcomes" nearly agree here, and the ranking adds little over acting on any 20% of cases. On a
  less degenerate target (see [`less-degenerate-target.md`](less-degenerate-target.md)) triage would matter more.
- Cost and breach value are placeholders; change them in the YAML and rerun to see the sensitivity.

## Sensitivity on a non-degenerate target (Fix 2)

Reproduce: `python -m scripts.roi_sensitivity` -> `reports/roi_sensitivity.json` (committed, raw),
[`roi-sensitivity.png`](roi-sensitivity.png); the same grid is returned by `GET /roi/summary` under
`sensitivity`. Same held-out window as everywhere else (last 20% by start time, n = 600). Grid: treated
share {5, 10, 20, 30%} x assumed effect {5, 10, 20, 30%} x breach cost {200, 400, 800}, 25 per treatment.
Outcomes are the cases' actual breaches; only the *effect* is assumed. **SIMULATION.**

![Net value grid](roi-sensitivity.png)

**p75 target** (breach = above the training-window per-category p75, base rate 27%; p75-trained RF,
sigmoid-calibrated on a held-out temporal slice, ROC-AUC 0.858). Treat 20% of cases, assumed effect 10%,
breach cost 400:

| Who gets treated | Precision (breaches among treated) | Net value | Break-even effect |
|---|---|---|---|
| Top 20% by model risk | 0.692 | **+320** | 9.0% |
| Top 20% by supplier history rule (no model) | 0.675 | +240 | 9.3% |
| Random 20% | 0.270 | -1,704 | 23.2% |
| Busiest suppliers first | 0.100 | -2,520 | 62.5% |

The model's precision lift over random is +0.66 / +0.58 / +0.42 / +0.32 at 5 / 10 / 20 / 30% treated, with
bootstrap 95% CIs all excluding 0 (e.g. +0.32 to +0.49 at 20%). So on a realistic target triage **does** have
business value in this simulation: random targeting needs a ~23% effect to break even, the model ~9%.

**But a one-feature rule gets almost all of it.** Ranking cases by the causal supplier historical breach rate --
no model -- captures **100% / 106% / 96% / 81%** of the model's net-value advantage over random at 5 / 10 / 20 /
30% treated (effect 10%, breach cost 400; e.g. at 20%: +1,944 vs +2,024). At small treated shares the rule is as
good as the model or better. The model's edge only shows at the largest share. Any claim that this model adds
business value should therefore be made against the rule, not against random.

At a 5% effect and breach cost 200 nothing pays for itself (net -500 to -3,340 depending on share); that region
is in the grid, not hidden. **Not run: "highest order value first".** This dataset has no order-value column
(recorded in the JSON as `strategies_not_run`), so it could not be compared honestly; busiest-supplier stands in
as the nearest available business rule, and it is *worse than random*.

**Configured target** (~97% breach in the window): every strategy is within 3 points of random by construction
(the maximum possible precision lift is 0.03). The served model reaches precision 1.0 at every share, a lift of
+0.030 [+0.018, +0.043] -- statistically real, practically negligible, and net values are within 150 of random.

### Correction history
An earlier version of this section (2026-09-25) reported the model's p75 net value as +720 and the rule as
capturing "about 80%". Those figures used a forest fitted on the full training window and calibrated in-sample;
after the calibration fix the forest is fitted on 80% of it, ROC-AUC fell from 0.876 to 0.858, and the rule now
captures 96% at 20% treated. Also found while producing them: the served isotonic calibrator (fitted on the
forest's own training rows) had ROC-AUC 0.665 vs 0.986 raw; fixed in [`calibration.md`](calibration.md).

## Randomized holdout (implemented) and how real uplift would be measured
- **Deterministic randomizer** (`src/roi/randomizer.py`): a case's arm is a SHA-256 hash of `experiment_id:case_id`
  mapped to [0, 1); below `holdout_share` (20%, `config/interventions.yaml`) it is **holdout**, otherwise
  **treat**. It depends only on the ids, so it is repeatable (a retried request cannot flip a case), independent of
  risk and of the caller, balanced, and re-randomized by changing the experiment id
  (`tests/test_randomizer.py` asserts determinism, balance to within binomial error, boundary shares, independence
  across experiments and across neighbouring ids).
- `POST /interventions` applies it: a holdout case is logged with cost 0 and the response says
  `assignment: "holdout", action_required: false`. `analytics.interventions` stores `assignment` and
  `experiment_id` (migration 006). Holdout rows carry no cost or benefit in the ROI numbers.
- `GET /roi/summary` gains an `experiment` block: breach rate per arm and, once each arm has >= 30 known outcomes,
  the **measured** breach reduction (holdout minus treated) with a 95% CI. Today it is empty of outcomes: no real
  case has been treated, so the assumed effects above remain assumptions.

## Validated on semi-synthetic data
Reproduce: `python -m scripts.uplift_validation` (about 3 minutes; raw JSON `reports/uplift_validation.json`).
Real held-out-window covariates and real model risk scores, with a **planted, known, heterogeneous effect**: the
action reduces breach probability by 0.05 for everyone, +0.20 more for the 5 busiest suppliers, and -0.10
(it backfires) for cases starting with the most common first activity. Outcomes are simulated, assignment uses the
randomizer at 50%, estimators are fitted on one half of the 600 cases and scored on the other (resampled to 4,000
units per half, 20 replicates). Estimators: T-learner and X-learner (own implementation on scikit-learn,
`src/roi/causal.py`; `econml`/`causalml` were not needed). Baselines: treat the highest model risk, and random.

Acceptance criteria were fixed before the run: **A1** ATE within 0.02 of truth (difference-in-means and X-learner);
**A2** CATE correlation with the true effect >= 0.5; **A3** the best learner's oracle Qini coefficient beats both
"highest risk" and random. **All three pass in both scenarios.**

| | p75 target (baseline risk 0.35) | Served model, 97% target (baseline risk 0.99) |
|---|---|---|
| True ATE | 0.0345 | 0.0687 |
| Difference-in-means ATE (mean, sd over replicates) | 0.0305 (0.010) | 0.0707 (0.007) |
| T-learner / X-learner mean tau_hat | 0.0368 / 0.0367 | 0.0690 / 0.0691 |
| CATE RMSE, T / X | 0.063 / 0.063 | 0.037 / 0.037 |
| CATE correlation with truth, T / X | 0.67 / 0.69 | 0.88 / 0.88 |
| Oracle Qini coefficient: best possible / X / T | 84.4 / 60.1 / 58.8 | 78.2 / 72.6 / 71.9 |
| ... treat highest model risk | **-13.3** | 17.2 |
| ... random | -0.1 | 0.2 |
| Breaches avoided by treating the top 20% (of 4,000 test cases; best possible / X / risk / random) | 126 / 95 / -7 / 27 | 151 / 146 / 73 / 55 |

![Gain curves](uplift-qini.png)

What it shows, and what it does not:
- **The estimators recover the planted effect.** Average effect within 0.002-0.004 (p75) and 0.000-0.002 (served) of
  truth, and the ranking they produce captures about 70% (p75) and 93% (served) of the best possible Qini
  coefficient. Errors of the *individual* effect are larger (RMSE 0.063 against a mean effect of 0.035 on p75), so
  use them to rank and to estimate averages, not to quote a per-case number.
- **Risk is not uplift.** On the p75 scenario, treating the highest-risk cases is *worse than random* (Qini
  coefficient -13): the backfire group (most common first activity) is over-represented among high-risk cases, so
  risk-ranked triage sends the action where it hurts. On the 97% scenario risk ranking beats random only because
  ~99% of cases are high-risk. That the naive rule loses this badly is a property of the planted structure (the
  backfire group correlates with risk here); with an effect that grew with risk, risk-ranking would do well. The
  point is that risk ranking cannot know which world it is in and uplift estimation can.
- **Observed and oracle Qini agree** in sign and rough size (e.g. 65.6 vs 60.1 for the X-learner on p75), so the
  observed-outcome Qini curve, the only one available with real data, is a usable evaluation.
- **Limits:** covariates come from only 600 real cases resampled (300 unique profiles per half), so the learners
  see far less covariate diversity than a real deployment; the planted effect is a simple function of two observed
  variables and there is no interference or unobserved confounding (assignment is randomized by construction);
  the X-learner is not consistently better than the T-learner here (differences inside one standard deviation).
  This validates the *method*; it says nothing about whether real interventions work.

Reproduce the ledger simulation: `python -m scripts.setup_database && python -m scripts.simulate_interventions`, then `GET /roi/summary`.


## Regenerated after the zero-duration label fix (2026-09-28)
104 truncated cases (zero measured duration) had been labelled "not breached". They are now excluded everywhere and
all simulations were rerun (`reports/roi_sensitivity.json`, `reports/uplift_validation.json`; 580-case held-out window).
Tables above this section show the previous run.

**p75 target** (base rate 24.8%, model ROC-AUC 0.852), treat 20%, effect 10%, breach cost 400:

| Who gets treated | Precision | Net value | Break-even effect |
|---|---|---|---|
| Top 20% by model risk | 0.690 | **+300** | 9.1% |
| Supplier-history rule | 0.578 | -220 | 10.8% |
| Random | 0.248 | -1,748 | 25.2% |
| Busiest suppliers first | 0.069 | -2,580 | 90.6% |

**The rule finding changes.** The supplier-history rule now captures **94% / 97% / 75% / 66%** of the model's net-value
advantage over random at 5 / 10 / 20 / 30% treated (previously 100% / 106% / 96% / 81%). The 104 non-breach labels
had given one supplier (vendorID_0358, 94 such cases) a spotless history, which flattered the rule. Conclusion: at
small treated shares the rule is nearly as good as the model; from about 20% up the model is clearly better.
Model lift over random: +0.58 / +0.60 / +0.44 / +0.34 (95% CIs exclude 0).

Ledger replay: 116 interventions, 17.4 avoided breaches, net +4,051 (by model risk). Uplift validation: all three
pre-registered criteria still pass in both scenarios (p75: true ATE 0.039, X-learner 0.040, difference-in-means 0.037).
