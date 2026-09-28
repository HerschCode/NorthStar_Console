"""Fix 2: rerun the intervention policy replay on a NON-degenerate target and report a sensitivity
grid instead of one point estimate.

Two scenarios over the same held-out window (last 20% of cases by start time):
- p75: breach = cycle time above the training-window per-category p75 (~27% base rate), scored by a
  random forest trained on that target (same hyperparameters as src/ml/train.py, calibrated on a held-out
  temporal slice by fit_calibrated, like the served model).
- configured: the deployed model on the configured SLA targets (~94% breach) -- kept for contrast.

Strategies for "which k cases get the intervention":
- model: top-k by calibrated model risk
- random: random k (expected value exact; 200-draw Monte Carlo std reported)
- supplier_history: top-k by the causal supplier historical breach rate -- a one-feature rule
  needing no model
- supplier_volume: top-k by the supplier's training-window case count ("busiest supplier first")
NOT included: "highest order value". This dataset's event log has no order-value column
(BPI 2019 sample: case, PO, item, spend area, vendor, activity, timestamp, resource), so that
comparison cannot be run honestly here; supplier volume is the closest available business rule.

Grid: treated share {5,10,20,30%} x assumed effect {5,10,20,30%} x breach cost {200,400,800};
per-treatment cost = config/interventions.yaml default type. Outcomes are the cases' ACTUAL breaches,
avoided = effect x (actual breaches among treated). The effect is still an assumption (SIMULATION).
Net value = avoided x breach_cost - treated x cost. Break-even effect = cost / (breach_cost x
breaches among treated).

Run: python -m scripts.roi_sensitivity
  -> reports/roi_sensitivity.json, docs/roi-sensitivity.png
"""
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score

from scripts.target_sensitivity import targets_from_training_percentile
from src.analytics.sla_analysis import evaluate_sla, load_sla_targets
from src.api.db import load_cases
from src.ml.features import build_features
from src.ml.predict import load_model, predict_sla_risk
from src.ml.train import fit_calibrated, time_based_split
from src.roi.ledger import load_policy

warnings.filterwarnings("ignore")
SHARES = (0.05, 0.10, 0.20, 0.30)
EFFECTS = (0.05, 0.10, 0.20, 0.30)
BREACH_COSTS = (200.0, 400.0, 800.0)
STRATEGIES = ("model", "random", "supplier_history", "supplier_volume")
OUT_JSON = Path("reports/roi_sensitivity.json")
OUT_PNG = Path("docs/roi-sensitivity.png")


def top_k_breaches(score: np.ndarray, y: np.ndarray, k: int, rng) -> int:
    order = np.lexsort((rng.random(len(score)), -score))   # ties broken randomly
    return int(y[order[:k]].sum())


def scenario(ev: pd.DataFrame, test_idx, scores: dict, y: np.ndarray, cost_per: float) -> dict:
    rng = np.random.default_rng(0)
    n, base = len(y), float(y.mean())
    rows, lift = [], []
    for share in SHARES:
        k = max(1, round(share * n))
        breaches = {"model": top_k_breaches(scores["model"], y, k, rng),
                    "supplier_history": top_k_breaches(scores["supplier_history"], y, k, rng),
                    "supplier_volume": top_k_breaches(scores["supplier_volume"], y, k, rng),
                    "random": k * base}                       # exact expectation
        mc = [int(y[rng.choice(n, k, replace=False)].sum()) for _ in range(200)]
        for strat in STRATEGIES:
            b = breaches[strat]
            for cost in BREACH_COSTS:
                be = cost_per * k / (cost * b) if b else None
                for eff in EFFECTS:
                    rows.append({"strategy": strat, "share": share, "effect": eff, "breach_cost": cost,
                                 "treated": k, "breaches_in_treated": round(b, 2),
                                 "precision": round(b / k, 4),
                                 "net_value": round(eff * b * cost - k * cost_per, 2),
                                 "break_even_effect": None if be is None else round(be, 4)})
        # precision lift of the model over random, paired bootstrap over held-out cases
        diffs = []
        for _ in range(300):
            i = rng.integers(0, n, n)
            ys, sc = y[i], scores["model"][i]
            diffs.append(ys[np.argsort(-sc, kind="stable")[:k]].mean() - ys.mean())
        lo, hi = np.percentile(diffs, [2.5, 97.5])
        lift.append({"share": share, "model_precision": round(breaches["model"] / k, 4),
                     "random_precision": round(base, 4), "lift_vs_random": round(breaches["model"] / k - base, 4),
                     "lift_ci95": [round(float(lo), 4), round(float(hi), 4)],
                     "random_breaches_mc_std": round(float(np.std(mc)), 2)})
    return {"n_test": n, "base_rate": round(base, 4),
            "model_roc_auc": round(float(roc_auc_score(y, scores["model"])), 4) if len(set(y)) > 1 else None,
            "grid": rows, "model_vs_random": lift}


def make_scores(ev, train_idx, test_idx, model_scores):
    hist = ev.loc[test_idx, "supplier_historical_breach_rate"].values
    vol = ev.loc[train_idx].groupby("supplier_id").size()
    return {"model": model_scores, "supplier_history": hist,
            "supplier_volume": ev.loc[test_idx, "supplier_id"].map(vol).fillna(0).values.astype(float)}


def with_history(ev):
    # same causal derivation the model's features use
    from src.ml.features import _add_derived_features
    return _add_derived_features(ev.copy())


def plot(result: dict, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sc = result["scenarios"]["p75"]
    g = pd.DataFrame(sc["grid"])
    fig, axes = plt.subplots(2, 3, figsize=(13, 7.5), constrained_layout=True)
    for j, cost in enumerate(BREACH_COSTS):
        for i, (title, fn) in enumerate((("Model top-k: net value", lambda d: d[d.strategy == "model"]),
                                         ("Model minus random: net value gained", None))):
            ax = axes[i, j]
            m = g[(g.breach_cost == cost) & (g.strategy == "model")].pivot(index="share", columns="effect", values="net_value")
            if fn is None:
                r = g[(g.breach_cost == cost) & (g.strategy == "random")].pivot(index="share", columns="effect", values="net_value")
                m = m - r
            lim = max(abs(m.values).max(), 1)
            ax.imshow(m.values, cmap="RdBu", vmin=-lim, vmax=lim, aspect="auto")
            ax.set_xticks(range(len(EFFECTS)), [f"{e:.0%}" for e in EFFECTS])
            ax.set_yticks(range(len(SHARES)), [f"{s:.0%}" for s in SHARES])
            for a in range(m.shape[0]):
                for b in range(m.shape[1]):
                    ax.text(b, a, f"{m.values[a, b]:,.0f}", ha="center", va="center", fontsize=8,
                            color="white" if abs(m.values[a, b]) > 0.6 * lim else "black")
            ax.set_title(f"{title}\nbreach cost {cost:,.0f}", fontsize=10)
            ax.set_xlabel("assumed effect")
            ax.set_ylabel("treated share")
    fig.suptitle(f"SIMULATION - p75 target (base rate {sc['base_rate']:.0%}, n={sc['n_test']}); "
                 "effects are assumptions, blue = positive", fontsize=11)
    fig.savefig(path, dpi=130)


def main():
    load_dotenv()
    policy = load_policy()
    cost_per = policy["types"][policy["default_type"]]["cost"]
    cases = load_cases()
    cases = cases[cases["cycle_time_hours"].astype(float) > 0].sort_values("start_time").reset_index(drop=True)  # measurable only
    result = {"label": "SIMULATION: effect sizes are assumptions (config/interventions.yaml), not measured uplift.",
              "cost_per_treatment": cost_per, "treatment": policy["default_type"],
              "strategies_not_run": {"order_value": "no order-value column in this dataset"},
              "scenarios": {}}

    # p75 scenario: trained on p75 labels, evaluated on the held-out window
    ev = evaluate_sla(cases, targets_from_training_percentile(cases, 75))
    X, y = build_features(ev)
    tr, te = time_based_split(ev)
    fit_rf = lambda X_, y_: RandomForestClassifier(
        n_estimators=200, max_depth=8, class_weight="balanced", random_state=42).fit(X_, y_)
    _, cal, cal_method, _ = fit_calibrated(fit_rf, X.loc[tr], y.loc[tr])   # temporal split of the training window
    evd = with_history(ev)
    result["scenarios"]["p75"] = scenario(
        evd, te, make_scores(evd, tr, te, cal.predict_proba(X.loc[te])[:, 1]), y.loc[te].values, cost_per)
    result["scenarios"]["p75"]["target"] = "cycle time > training-window per-category p75; p75-trained RF, " + cal_method+" calibration on a held-out training slice"
    # configured scenario: the deployed (served, calibrated) model
    ev2 = evaluate_sla(cases, load_sla_targets())
    X2, y2 = build_features(ev2)
    tr2, te2 = time_based_split(ev2)
    bundle = load_model()
    risk = predict_sla_risk(X2.loc[te2], bundle)["breach_probability"].values
    evd2 = with_history(ev2)
    result["scenarios"]["configured"] = scenario(
        evd2, te2, make_scores(evd2, tr2, te2, risk), y2.loc[te2].values, cost_per)
    result["scenarios"]["configured"]["target"] = "configured SLA targets (config/sla.yaml); deployed model as served (held-out-slice calibrated)"

    OUT_JSON.parent.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2))
    plot(result, OUT_PNG)
    for name, sc in result["scenarios"].items():
        print(f"\n[{name}] n={sc['n_test']} base={sc['base_rate']} model AUC={sc['model_roc_auc']}")
        for r in sc["model_vs_random"]:
            print(f"  top {r['share']:.0%}: model precision {r['model_precision']:.3f} vs random {r['random_precision']:.3f}"
                  f"  lift {r['lift_vs_random']:+.3f} CI {r['lift_ci95']}")
        g = pd.DataFrame(sc["grid"])
        s20 = g[(g.share == 0.20) & (g.effect == 0.10) & (g.breach_cost == 400)].set_index("strategy")
        print("  share 20%, effect 10%, breach cost 400 -> net value:", s20["net_value"].to_dict())
        print("  break-even effect:", s20["break_even_effect"].to_dict())
    print(f"\nwrote {OUT_JSON} and {OUT_PNG}")


if __name__ == "__main__":
    main()
