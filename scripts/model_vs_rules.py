"""Week-4 question: can P1's model beat the simple supplier rules at small treated shares -- and if not, why?

Context: earlier case-level evaluation suggested supplier-history and volume rules outperformed the model
at small treated shares. This run uses a PO-isolated temporal holdout and cluster-bootstrap intervals.

Design, written down BEFORE running (so a result cannot be tuned toward):
  Population: p75 target (cycle time above the training-window per-category p75), measurable cases only,
              held-out = latest temporal purchase-order groups, with boundary-crossing groups excluded.
  Models (all RF, calibrated on a held-out temporal slice by src/ml/train.py::fit_calibrated, seed 42):
    M0 current  : the model's current feature set (reproduces roi_sensitivity's "model").
    M1 +workload: M0 + causal supplier features computed only from information available at case start:
                  prior case count, cases still open, ended-case breach rate (outcomes already KNOWN),
                  and that rate shrunk toward the training prior (m=10).
    M2 compact  : numeric + category only (no 800 one-hot supplier/activity columns) + the M1 features.
  Rules (score = rank key):
    rule_supplier_history: supplier breach rate using only cases that had ENDED before this one started.
    rule_shrunk : ended-case rate shrunk toward the prior.
    volume_train / volume_prior : busiest-supplier by training-window count (as before) / by causal prior count.
    order_value, random.
    Metrics: precision@k for k in {5,10,20,30}% of the held-out window, ROC-AUC; paired cluster bootstrap
                     (500 resamples of purchase orders) for every strategy's precision and model-rule differences.
  Diagnosis: permutation importance of the best model on the held-out window; at k=5%, overlap between model and
             rule picks and the precision of the rule-only vs model-only picks.
  Pre-stated reading: the model "beats the rules" at a share only if the paired 95% CI of
             (model - best rule) precision excludes 0 in the model's favour. Anything else is reported as a tie
             or a loss, not tuned away.

Run: python -m scripts.model_vs_rules   -> reports/model_vs_rules.json
"""
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score

from scripts.target_sensitivity import targets_from_training_percentile
from src.analytics.sla_analysis import evaluate_sla
from src.api.db import load_cases, load_events
from src.controls.case_attributes import case_ap_attributes
from src.ml.features import build_features
from src.ml.train import fit_calibrated, time_based_split

warnings.filterwarnings("ignore")
SHARES = (0.05, 0.10, 0.20, 0.30)
N_BOOT = 500
SHRINK_M = 10
OUT = Path("reports/model_vs_rules.json")


def causal_supplier_features(ev: pd.DataFrame, prior_rate: float, prior_median: float) -> pd.DataFrame:
    """Per case, using ONLY information available when it starts: how many cases this supplier had started,
    how many were still open, and the breach rate of the ones that had already ENDED."""
    out = pd.DataFrame(index=ev.index, columns=["sup_prior_cases", "sup_open_cases", "sup_ended_n", "sup_ended_rate",
                                                "sup_ended_median"], dtype=float)
    cyc = ev["cycle_time_hours"].astype(float)
    y = ev["sla_breach"].astype(int)
    for _, g in ev.groupby("supplier_id"):
        g = g.sort_values("start_time")
        s, e, yy = g["start_time"].values, g["end_time"].values, y.loc[g.index].values
        for pos, idx in enumerate(g.index):
            prev_end = e[:pos]
            ended = prev_end < s[pos]
            n_ended = int(ended.sum())
            out.loc[idx, "sup_prior_cases"] = pos
            out.loc[idx, "sup_open_cases"] = int((~ended).sum())
            out.loc[idx, "sup_ended_n"] = n_ended
            out.loc[idx, "sup_ended_rate"] = yy[:pos][ended].mean() if n_ended else prior_rate
            out.loc[idx, "sup_ended_median"] = float(np.median(cyc.loc[g.index].values[:pos][ended])) if n_ended else prior_median
    out["sup_shrunk_rate"] = (out["sup_ended_rate"] * out["sup_ended_n"] + prior_rate * SHRINK_M) / (out["sup_ended_n"] + SHRINK_M)
    return out


def precision_at(score, y, share, rng=None):
    n = len(y)
    k = max(1, round(share * n))
    sc = score if rng is None else score + rng.random(n) * 1e-9          # random tie-breaking
    return float(y[np.argsort(-sc, kind="stable")[:k]].mean())


def boot(scores: dict, y: np.ndarray, cluster_ids, n_boot=N_BOOT, seed=0):
    """Paired cluster bootstrap over held-out purchase orders."""
    rng = np.random.default_rng(seed)
    codes, unique_clusters = pd.factorize(pd.Series(cluster_ids).astype("string"), sort=False)
    cluster_members = [np.flatnonzero(codes == code) for code in range(len(unique_clusters))]
    res = {s: {sh: [] for sh in SHARES} for s in scores}
    for _ in range(n_boot):
        sampled_clusters = rng.integers(0, len(cluster_members), len(cluster_members))
        i = np.concatenate([cluster_members[cluster] for cluster in sampled_clusters])
        jit = rng.random(len(i)) * 1e-9
        for name, sc in scores.items():
            for sh in SHARES:
                k = max(1, round(sh * len(i)))
                res[name][sh].append(float(y[i][np.argsort(-(sc[i] + jit), kind="stable")[:k]].mean()))
    return res


def ci(a):
    lo, hi = np.percentile(a, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]


def main():
    load_dotenv()
    cases = load_cases()
    cases = cases[cases["cycle_time_hours"].astype(float) > 0].sort_values("start_time").reset_index(drop=True)
    case_attributes = case_ap_attributes(load_events())
    attribute_columns = ["case_id", "order_value_eur"]
    if "purchase_order_id" in case_attributes.columns:
        attribute_columns.append("purchase_order_id")
    cases = cases.merge(case_attributes[attribute_columns], on="case_id", how="left")
    ev = evaluate_sla(cases, targets_from_training_percentile(cases, 75))
    tr, te = time_based_split(ev)
    X, y = build_features(ev)
    prior = float(y.loc[tr].mean())
    prior_median = float(ev.loc[tr, "cycle_time_hours"].median())
    new = causal_supplier_features(ev, prior, prior_median)
    yte = y.loc[te].values.astype(int)

    def rf(X_, y_):
        return RandomForestClassifier(n_estimators=200, max_depth=8, class_weight="balanced", random_state=42).fit(X_, y_)

    numeric_core = ["event_count", "variant_frequency", "start_hour", "start_dayofweek", "start_month", "start_quarter",
                    "unique_activity_count", "rework_count", "supplier_historical_breach_rate",
                    "supplier_historical_median_cycle_time", "sla_target_hours"]
    cat_cols = [c for c in X.columns if c.startswith("category_")]
    leaky = ["supplier_historical_breach_rate", "supplier_historical_median_cycle_time"]
    leak_free_new = ["sup_ended_rate", "sup_shrunk_rate", "sup_ended_median", "sup_ended_n", "sup_open_cases"]
    feature_sets = {
        "M0_current": X,
        "M1_plus_workload": pd.concat([X, new], axis=1),
        "M2_compact": pd.concat([X[numeric_core + cat_cols], new], axis=1),
        # M3: the two supplier-history features replaced by versions that use only cases that had ENDED
        "M3_leak_free_history": pd.concat([X.drop(columns=leaky), new[leak_free_new]], axis=1),
        # M4: no supplier history at all
        "M4_no_supplier_history": X.drop(columns=leaky),
    }
    fitted, model_scores = {}, {}
    for name, Xm in feature_sets.items():
        _, cal, method, _ = fit_calibrated(rf, Xm.loc[tr], y.loc[tr])
        fitted[name] = cal
        model_scores[name] = cal.predict_proba(Xm.loc[te])[:, 1]
        print(f"{name}: ROC-AUC {roc_auc_score(yte, model_scores[name]):.4f} (calibration {method}, {Xm.shape[1]} features)", flush=True)

    vol_train = ev.loc[tr].groupby("supplier_id").size()
    scores = dict(model_scores)
    scores["rule_supplier_history (completed only)"] = new.loc[te, "sup_ended_rate"].values.astype(float)
    scores["rule_shrunk"] = new.loc[te, "sup_shrunk_rate"].values.astype(float)
    scores["volume_train"] = ev.loc[te, "supplier_id"].map(vol_train).fillna(0).values.astype(float)
    scores["volume_prior (causal)"] = new.loc[te, "sup_prior_cases"].values.astype(float)
    scores["order_value"] = ev.loc[te, "order_value_eur"].fillna(0).values.astype(float)
    scores["random"] = np.zeros(len(yte))                      # ties everywhere -> random order via jitter

    point = {s: {sh: precision_at(sc, yte, sh, np.random.default_rng(1)) for sh in SHARES} for s, sc in scores.items()}
    aucs = {s: round(float(roc_auc_score(yte, sc)), 4) for s, sc in scores.items() if s != "random"}
    cluster_ids = ev.loc[te, "purchase_order_id"] if "purchase_order_id" in ev.columns else ev.loc[te, "case_id"]
    cluster_ids = cluster_ids.where(cluster_ids.notna(), ev.loc[te, "case_id"])
    bs = boot(scores, yte, cluster_ids)
    table = {s: {f"{int(sh * 100)}%": {"precision": round(point[s][sh], 4), "ci95": ci(bs[s][sh])} for sh in SHARES}
             for s in scores}

    rules = [s for s in scores if s.startswith(("rule", "volume", "order"))]
    deployable = rules
    verdicts, verdicts_deployable = {}, {}
    for sh in SHARES:
        for m in model_scores:
            best_rule = max(rules, key=lambda r: point[r][sh])
            d = np.array(bs[m][sh]) - np.array(bs[best_rule][sh])
            lo, hi = np.percentile(d, [2.5, 97.5])
            verdict = "model wins" if lo > 0 else "model loses" if hi < 0 else "tie (CI includes 0)"
            verdicts[f"{m} @ {int(sh * 100)}%"] = {"best_rule": best_rule, "diff": round(point[m][sh] - point[best_rule][sh], 4),
                                                     "diff_ci95": [round(float(lo), 4), round(float(hi), 4)], "verdict": verdict}
            bd = max(deployable, key=lambda r: point[r][sh])
            d2 = np.array(bs[m][sh]) - np.array(bs[bd][sh])
            lo2, hi2 = np.percentile(d2, [2.5, 97.5])
            verdicts_deployable[f"{m} @ {int(sh * 100)}%"] = {
                "best_deployable_rule": bd, "diff": round(point[m][sh] - point[bd][sh], 4),
                "diff_ci95": [round(float(lo2), 4), round(float(hi2), 4)],
                "verdict": "model wins" if lo2 > 0 else "model loses" if hi2 < 0 else "tie (CI includes 0)"}

    # diagnosis: permutation importance on the best model by AUC, held-out window
    best = max(model_scores, key=lambda m: roc_auc_score(yte, model_scores[m]))
    pi = permutation_importance(fitted[best], feature_sets[best].loc[te], yte, scoring=lambda est, X_, y_: roc_auc_score(y_, est.predict_proba(X_)[:, 1]), n_repeats=5,
                                random_state=0, n_jobs=1)
    imp = sorted(zip(feature_sets[best].columns, pi.importances_mean), key=lambda kv: -kv[1])[:12]

    # overlap analysis at 5%
    k = max(1, round(0.05 * len(yte)))
    top = lambda sc: set(np.argsort(-(sc + np.random.default_rng(2).random(len(sc)) * 1e-9), kind="stable")[:k])
    mset, rset = top(model_scores[best]), top(scores["rule_supplier_history (completed only)"])
    only_m, only_r, both = mset - rset, rset - mset, mset & rset
    sup_te = ev.loc[te, "supplier_id"].values
    overlap = {"model": best, "k": k, "overlap": len(both),
               "precision_model_only": round(float(yte[list(only_m)].mean()), 4) if only_m else None,
               "precision_rule_only": round(float(yte[list(only_r)].mean()), 4) if only_r else None,
               "precision_both": round(float(yte[list(both)].mean()), 4) if both else None,
               "distinct_suppliers_in_model_top": int(len(set(sup_te[list(mset)]))),
               "distinct_suppliers_in_rule_top": int(len(set(sup_te[list(rset)]))),
               "top_supplier_share_rule_top": round(float(pd.Series(sup_te[list(rset)]).value_counts(normalize=True).iloc[0]), 4)}

    po = cluster_ids.astype(str).values
    cluster = {"n_cases": int(len(yte)), "distinct_purchase_orders": int(len(set(po))), "distinct_suppliers": int(len(set(sup_te)))}
    for nm in ("M0_current", "M1_plus_workload", "M3_leak_free_history", "rule_supplier_history (completed only)", "volume_train"):
        sc = scores[nm] if nm in scores else model_scores[nm]
        t = list(top(sc))
        cluster[f"top5pct[{nm}]"] = {"distinct_suppliers": int(len(set(sup_te[t]))), "distinct_purchase_orders": int(len(set(po[t])))}
    cluster["note"] = ("Items of one purchase order share vendor, timing and (often) outcome. Intervals use a "
                       "purchase-order cluster bootstrap, but there are few independent suppliers and POs at k=5% "
                       "where the picks come from few suppliers/POs.")

    result = {"design": __doc__.split("Run:")[0], "bootstrap_unit": "purchase_order_id",
              "n_test": int(len(yte)), "base_rate": round(float(yte.mean()), 4),
              "roc_auc": aucs, "precision_at_k": table, "model_vs_best_rule": verdicts, "model_vs_best_deployable_rule": verdicts_deployable,
              "clustering": cluster,
              "permutation_importance_best_model": [[a, round(float(b), 5)] for a, b in imp],
              "overlap_at_5pct": overlap, "n_bootstrap": N_BOOT}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    print("\nprecision@k (95% CI):")
    for s in scores:
        print(f"  {s:30s}", "  ".join(f"{k_}: {v['precision']:.3f} {v['ci95']}" for k_, v in table[s].items()))
    print("\nverdicts:")
    for k_, v in verdicts.items():
        print(f"  {k_:28s} vs {v['best_rule']:28s} diff {v['diff']:+.3f} {v['diff_ci95']} -> {v['verdict']}")
    print("\nbest model:", best, "\ntop permutation importances:", imp[:8], "\noverlap:", overlap)


if __name__ == "__main__":
    main()
