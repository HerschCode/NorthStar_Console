"""Semi-synthetic validation of the uplift method (Upgrade 3): plant a KNOWN, heterogeneous treatment effect
on top of real case covariates and real model risk scores, then check that the estimators recover it.

Design
- Covariates: the real held-out-window cases (last 20% by start time, 600 cases) with their real features.
  Baseline breach probability p0 = the model's risk score for that case (scenario "p75": p75-trained,
  sigmoid-calibrated forest; scenario "served": the deployed model on the configured 97%-breach target).
- Planted effect (absolute reduction in breach probability): tau = 0.05 + 0.20 * [supplier is one of the 5
  busiest] - 0.10 * [first activity is the most common one], with p1 = clip(p0 - tau, 0, 1) and the realized
  true effect tau_true = p0 - p1. So the action helps most for big suppliers and BACKFIRES for one starting stage.
- Outcomes: Y0 = 1[u < p0], Y1 = 1[u < p1] with u ~ U(0,1) drawn per unit; Y observed under the arm assigned.
- Assignment: src/roi/randomizer.py with a 50% holdout (the deterministic randomizer, one experiment id per
  replicate), so the estimators see randomized data.
- Each replicate splits the 600 original cases 300/300 into train and test, then draws 4,000 units with
  replacement from each half (covariates repeat, outcomes are fresh draws) -- so train and test never share a case.
- 20 replicates; estimators: T-learner, X-learner (src/roi/causal.py). Baselines: "treat highest model risk"
  (the naive triage this project simulated before) and random.

Acceptance criteria, fixed BEFORE running (a failure is reported, not tuned away):
  A1 ATE: |mean bias| <= 0.02 (2 percentage points) for difference-in-means and for the X-learner's mean tau_hat.
  A2 CATE: mean Pearson r between tau_hat and the true tau >= 0.5 for at least one of T/X-learner.
  A3 Targeting: mean oracle Qini coefficient (AUUC) of the best learner > that of "highest risk" and > 0.

Run: python -m scripts.uplift_validation  -> reports/uplift_validation.json, docs/uplift-qini.png
"""
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.ensemble import RandomForestClassifier

from scripts.target_sensitivity import targets_from_training_percentile
from src.analytics.sla_analysis import evaluate_sla, load_sla_targets
from src.api.db import load_cases
from src.ml.features import build_features
from src.ml.predict import load_model, predict_sla_risk
from src.ml.train import fit_calibrated, time_based_split
from src.roi.causal import (
    auuc, difference_in_means, fit_t_learner, fit_x_learner, oracle_curve, qini_curve)
from src.roi.randomizer import HOLDOUT, assign

warnings.filterwarnings("ignore")
N_REPLICATES = 20
N_DRAW = 4000
HOLDOUT_SHARE = 0.5
GRID = np.linspace(0, 1, 101)


def scenario_inputs(cases, which):
    """(features for the learners, raw frame, baseline risk p0) on the held-out window."""
    if which == "p75":
        ev = evaluate_sla(cases, targets_from_training_percentile(cases, 75))
        X, y = build_features(ev)
        tr, te = time_based_split(ev)
        fit_rf = lambda X_, y_: RandomForestClassifier(
            n_estimators=200, max_depth=8, class_weight="balanced", random_state=42).fit(X_, y_)
        _, cal, _, _ = fit_calibrated(fit_rf, X.loc[tr], y.loc[tr])
        p0 = cal.predict_proba(X.loc[te])[:, 1]
    else:
        ev = evaluate_sla(cases, load_sla_targets())
        X, _ = build_features(ev)
        _, te = time_based_split(ev)
        p0 = predict_sla_risk(X.loc[te], load_model())["breach_probability"].values
    Xw = X.loc[te].astype(float)
    Xw = Xw.loc[:, (Xw.mean() >= 0.02) | (Xw.nunique() > 2)]     # drop rare one-hot columns
    return Xw.reset_index(drop=True), ev.loc[te].reset_index(drop=True), np.asarray(p0, dtype=float)


def plant_effect(raw, p0):
    big = raw["supplier_id"].isin(raw["supplier_id"].value_counts().head(5).index).values
    modal = raw["first_activity"] == raw["first_activity"].mode().iloc[0]
    tau = 0.05 + 0.20 * big - 0.10 * modal.values
    p1 = np.clip(p0 - tau, 0.0, 1.0)
    return p1, p0 - p1


def one_replicate(Xw, raw, p0, rep):
    rng = np.random.default_rng(1000 + rep)
    perm = rng.permutation(len(Xw))
    halves = (perm[: len(perm) // 2], perm[len(perm) // 2:])
    p1_all, tau_all = plant_effect(raw, p0)
    sets = []
    for h, half in enumerate(halves):
        idx = rng.choice(half, N_DRAW, replace=True)
        u = rng.random(N_DRAW)
        ids = [f"rep{rep}-half{h}-{i}" for i in range(N_DRAW)]
        t = np.array([0 if assign(cid, f"validation-{rep}", HOLDOUT_SHARE) == HOLDOUT else 1 for cid in ids])
        y0, y1 = (u < p0[idx]).astype(int), (u < p1_all[idx]).astype(int)
        sets.append({"X": Xw.iloc[idx].values, "t": t, "y": np.where(t == 1, y1, y0),
                     "tau": tau_all[idx], "p0": p0[idx]})
    tr, te = sets
    ate_true = float(te["tau"].mean())
    dm, dm_ci = difference_in_means(te["y"], te["t"])
    out = {"ate_true": ate_true, "ate_diff_in_means": dm, "n_treat_test": int(te["t"].sum())}
    scores = {"random": np.random.default_rng(rep).random(N_DRAW), "highest_risk": te["p0"]}
    for name, fit in (("t_learner", fit_t_learner), ("x_learner", fit_x_learner)):
        tau_hat = fit(tr["X"], tr["y"], tr["t"], seed=rep)(te["X"])
        scores[name] = tau_hat
        out[f"{name}_ate"] = float(tau_hat.mean())
        out[f"{name}_cate_rmse"] = float(np.sqrt(np.mean((tau_hat - te["tau"]) ** 2)))
        out[f"{name}_cate_corr"] = float(np.corrcoef(tau_hat, te["tau"])[0, 1]) if tau_hat.std() > 0 else 0.0
    scores["oracle"] = te["tau"]
    curves = {}
    for name, s in scores.items():
        fr, g, rnd = oracle_curve(s, te["tau"])
        out[f"{name}_oracle_auuc"] = auuc(fr, g, rnd)
        out[f"{name}_top20_benefit"] = float(np.interp(0.2, fr, g))
        _, qg, qr = qini_curve(s, te["y"], te["t"])
        out[f"{name}_observed_auuc"] = auuc(fr, qg, qr)
        curves[name] = {"oracle": g, "observed": qg}
    return out, curves


def summarise(reps):
    keys = reps[0].keys()
    return {k: {"mean": round(float(np.mean([r[k] for r in reps])), 5),
                "sd": round(float(np.std([r[k] for r in reps])), 5)} for k in keys}


def acceptance(s):
    a1 = (abs(s["ate_diff_in_means"]["mean"] - s["ate_true"]["mean"]) <= 0.02 and
          abs(s["x_learner_ate"]["mean"] - s["ate_true"]["mean"]) <= 0.02)
    a2 = max(s["t_learner_cate_corr"]["mean"], s["x_learner_cate_corr"]["mean"]) >= 0.5
    best = max(s["t_learner_oracle_auuc"]["mean"], s["x_learner_oracle_auuc"]["mean"])
    a3 = best > s["highest_risk_oracle_auuc"]["mean"] and best > 0
    return {"A1_ate_within_0.02": bool(a1), "A2_cate_corr_ge_0.5": bool(a2), "A3_beats_highest_risk_and_random": bool(a3)}


def plot(all_curves, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(13, 5), constrained_layout=True)
    style = {"oracle": ("black", ":"), "x_learner": ("#1f77b4", "-"), "t_learner": ("#ff7f0e", "-"),
             "highest_risk": ("#d62728", "--"), "random": ("grey", "-.")}
    for ax, (scen, curves) in zip(axes, all_curves.items()):
        for name, (c, ls) in style.items():
            g = np.mean([r[name]["oracle"] for r in curves], axis=0)
            ax.plot(GRID, g, color=c, ls=ls, label={"oracle": "best possible (true effect)", "x_learner": "X-learner",
                    "t_learner": "T-learner", "highest_risk": "treat highest model risk", "random": "random"}[name])
        ax.axhline(0, color="#ccc", lw=0.8)
        ax.set_title(f"{scen}: expected breaches avoided vs share treated\n(true planted effect, mean of {len(curves)} replicates)", fontsize=10)
        ax.set_xlabel("share of cases treated (ranked by score)")
        ax.set_ylabel("expected breaches avoided (4,000-case test set)")
        ax.legend(fontsize=8)
    fig.suptitle("SEMI-SYNTHETIC: planted effect = +0.05, +0.20 for 5 busiest suppliers, -0.10 for the most common first activity", fontsize=10)
    fig.savefig(path, dpi=130)


def main():
    load_dotenv()
    cases = load_cases()
    cases = cases[cases["cycle_time_hours"].astype(float) > 0].sort_values("start_time").reset_index(drop=True)  # measurable only
    result, all_curves = {"design": __doc__.split("Design")[1].split("Run:")[0].strip()[:0] or "see script docstring",
                          "n_replicates": N_REPLICATES, "n_draw_per_half": N_DRAW, "holdout_share": HOLDOUT_SHARE,
                          "scenarios": {}}, {}
    for which, label in (("p75", "p75 target (27% base rate)"), ("served", "served model, configured target (97%)")):
        Xw, raw, p0 = scenario_inputs(cases, which)
        reps, curves = [], []
        for rep in range(N_REPLICATES):
            r, c = one_replicate(Xw, raw, p0, rep)
            reps.append(r)
            curves.append(c)
            print(f"[{which}] rep {rep + 1}/{N_REPLICATES} true ATE {r['ate_true']:.3f} DiM {r['ate_diff_in_means']:.3f} "
                  f"X {r['x_learner_ate']:.3f} corr X {r['x_learner_cate_corr']:.2f}", flush=True)
        s = summarise(reps)
        result["scenarios"][which] = {"label": label, "mean_baseline_risk": round(float(p0.mean()), 4),
                                      "summary": s, "acceptance": acceptance(s)}
        all_curves[label] = curves
    Path("reports").mkdir(exist_ok=True)
    Path("reports/uplift_validation.json").write_text(json.dumps(result, indent=2))
    plot(all_curves, Path("docs/uplift-qini.png"))
    for w, sc in result["scenarios"].items():
        s = sc["summary"]
        print(f"\n[{w}] {sc['label']}  baseline risk {sc['mean_baseline_risk']}")
        print(f"  ATE true {s['ate_true']['mean']:.4f} | diff-in-means {s['ate_diff_in_means']['mean']:.4f} | "
              f"T {s['t_learner_ate']['mean']:.4f} | X {s['x_learner_ate']['mean']:.4f}")
        for n in ("t_learner", "x_learner"):
            print(f"  {n}: CATE RMSE {s[n + '_cate_rmse']['mean']:.4f}  corr {s[n + '_cate_corr']['mean']:.3f}")
        for n in ("oracle", "x_learner", "t_learner", "highest_risk", "random"):
            print(f"  {n:13s} oracle AUUC {s[n + '_oracle_auuc']['mean']:8.2f} (sd {s[n + '_oracle_auuc']['sd']:.2f})  "
                  f"benefit@20% {s[n + '_top20_benefit']['mean']:7.2f}  observed AUUC {s[n + '_observed_auuc']['mean']:8.2f}")
        print("  acceptance:", sc["acceptance"])


if __name__ == "__main__":
    main()
