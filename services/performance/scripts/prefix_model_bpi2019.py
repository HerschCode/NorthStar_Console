"""Prefix (first-k-events) model on this project's own BPI 2019 data.

Question: can an early-warning score be built from only what is known after the first k events of
a case, and does it beat creation-time-only features *on the same cases*?

Design (fixed before looking at results):
- Label: cycle time above a per-category percentile (p50 / p75) of the TRAINING window (first 80% of
  cases by start time). Same definition as scripts/target_sensitivity.py; test period never affects it.
- Population at prefix k: cases with >= k events AND still under their target at event k (their label
  is not already determined by elapsed time). The SAME rows are used for the creation-time-only
  baseline, so the comparison is like for like (rows for different k are still different populations
  and are not comparable to each other).
- Supplier history is strictly causal: only cases that had already ENDED before this case started.
  (The main pipeline's version uses earlier-*started* cases, whose outcomes may not yet be known.)
- Last 20% by start time is the held-out set; RF and LR, fixed hyperparameters, one seed.

Run: python -m scripts.prefix_model_bpi2019
"""
import os
import warnings

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

from src.cleaning.clean_events import clean_events
from src.ingestion.load_event_log import load_event_log
from scripts.target_sensitivity import targets_from_training_percentile
from src.analytics.sla_analysis import evaluate_sla
from src.transformation.build_process_cases import build_process_cases

warnings.filterwarnings("ignore")
load_dotenv()


def causal_supplier_history(cases: pd.DataFrame) -> pd.Series:
    """Mean breach rate of this supplier's cases that had ENDED before this case started."""
    out = pd.Series(np.nan, index=cases.index)
    prior = cases["sla_breach"].astype(int).mean()
    for _, g in cases.groupby("supplier_id"):
        s, e, y = g["start_time"].values, g["end_time"].values, g["sla_breach"].astype(int).values
        for pos, idx in enumerate(g.index):
            done = e <= s[pos]
            out.loc[idx] = y[done].mean() if done.any() else prior
    return out.fillna(prior)


def main():
    events = clean_events(load_event_log(os.environ["RAW_EVENT_LOG_PATH"]))[0]
    events = events.sort_values(["case_id", "timestamp"])
    cases = build_process_cases(events)
    cases = cases[cases["cycle_time_hours"] > 0].sort_values("start_time").reset_index(drop=True)  # measurable only
    by_case = {cid: g for cid, g in events.groupby("case_id")}
    n = len(cases)
    cut = int(n * 0.8)
    print(f"cases={n}  train={cut}  test={n - cut}")

    for pct in (50, 75):
        targets = targets_from_training_percentile(cases, pct)
        ev = evaluate_sla(cases, targets)
        ev["supplier_hist"] = causal_supplier_history(ev)
        y = ev["sla_breach"].astype(int)
        print(f"\n== breach = cycle time > training-window p{pct} per category (default {targets['default']/24:.0f} d) ==")
        for k in (2, 3, 5):
            rows = []
            for cid, tgt in zip(ev["case_id"], ev["sla_target_hours"]):
                g = by_case[cid]
                if len(g) < k:
                    rows.append(None)
                    continue
                t = g["timestamp"].values
                elapsed = (t[k - 1] - t[0]) / np.timedelta64(1, "h")
                a = g["activity"].tolist()[:k]
                rows.append({"elapsed_h": elapsed, "uniq": len(set(a)), "repeats": k - len(set(a)),
                             "kth_act": a[-1], "open": elapsed <= tgt})
            keep = np.array([r is not None and r["open"] for r in rows])
            base = pd.DataFrame({
                "hour": ev["start_time"].dt.hour, "dow": ev["start_time"].dt.dayofweek,
                "month": ev["start_time"].dt.month, "first_act": ev["first_activity"],
                "category": ev["category"].fillna("UNKNOWN"),
                "supplier_hist": ev["supplier_hist"],
            })
            pre = base.copy()
            for col in ("elapsed_h", "uniq", "repeats", "kth_act"):
                pre[col] = [r[col] if r else np.nan for r in rows]
            enc = lambda X: pd.get_dummies(X, columns=[c for c in X.columns if X[c].dtype == object]).astype(float).fillna(0)
            tr = np.where(keep & (np.arange(n) < cut))[0]
            te = np.where(keep & (np.arange(n) >= cut))[0]
            if len(te) < 30 or y.iloc[te].nunique() < 2 or y.iloc[tr].nunique() < 2:
                print(f"  k={k}: too few open cases (test n={len(te)})")
                continue
            res, preds = {}, {}
            for fs, X in (("creation-only", base), (f"first {k} events", pre)):
                Xe = enc(X)
                for mname, mk in (("LR", lambda: LogisticRegression(max_iter=3000, class_weight="balanced")),
                                  ("RF", lambda: RandomForestClassifier(n_estimators=200, max_depth=8,
                                                                        class_weight="balanced", random_state=42))):
                    m = mk().fit(Xe.iloc[tr], y.iloc[tr])
                    p = m.predict_proba(Xe.iloc[te])[:, 1]
                    res[(fs, mname)] = (roc_auc_score(y.iloc[te], p), average_precision_score(y.iloc[te], p))
                    preds[(fs, mname)] = p
            print(f"  k={k}: train n={len(tr)} test n={len(te)} base rate={y.iloc[te].mean():.3f}")
            for (fs, mname), (roc, pr) in res.items():
                print(f"      {fs:16s} {mname}: ROC-AUC {roc:.3f}  PR-AUC {pr:.3f}")
            # paired bootstrap over held-out cases: prefix RF minus creation-only RF (same rows)
            yt = y.iloc[te].values
            a, b = preds[(f"first {k} events", "RF")], preds[("creation-only", "RF")]
            rng = np.random.default_rng(0)
            diffs = []
            for _ in range(500):
                i = rng.integers(0, len(yt), len(yt))
                if len(set(yt[i])) < 2:
                    continue
                diffs.append(roc_auc_score(yt[i], a[i]) - roc_auc_score(yt[i], b[i]))
            lo, hi = np.percentile(diffs, [2.5, 97.5])
            print(f"      RF ROC-AUC gain (prefix - creation-only): {np.mean(diffs):+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]")


if __name__ == "__main__":
    main()
