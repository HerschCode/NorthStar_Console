"""GRU/LSTM over the first k events vs the prefix random forest, same rows, same split.

Reuses scripts/prefix_model_bpi2019.py's design (label = cycle time above the training-window
per-category percentile; population = cases with >= k events still under target at event k;
strictly causal supplier history; last 20% by start time held out). Adds k=1. The sequence models
get an inner temporal validation slice (last 15% of the training window) for early stopping; the
RF is fit on the full training window as in the prefix script. 3 seeds for the neural nets.

Run: python -m scripts.sequence_model_bpi2019 [--no-mlflow]   -> reports/sequence_model_results.json
"""
import json
import os
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from dotenv import load_dotenv
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from scripts.prefix_model_bpi2019 import causal_supplier_history
from scripts.target_sensitivity import targets_from_training_percentile
from src.analytics.sla_analysis import evaluate_sla
from src.cleaning.clean_events import clean_events
from src.ingestion.load_event_log import load_event_log
from src.ml.sequence_model import (
    ActivityVocab, SequenceRiskModel, cpu_latency_ms, fit, make_sequence_tensors, predict_proba)
from src.transformation.build_process_cases import build_process_cases

warnings.filterwarnings("ignore")
load_dotenv()
SEEDS = (0, 1, 2)
KS = (1, 2, 3, 5)
PCTS = (50, 75)


def metrics(y, p):
    return {"roc_auc": roc_auc_score(y, p), "pr_auc": average_precision_score(y, p),
            "brier": brier_score_loss(y, p)}


def static_frame(ev):
    base = pd.DataFrame({
        "hour": ev["start_time"].dt.hour / 23.0, "dow": ev["start_time"].dt.dayofweek / 6.0,
        "month": ev["start_time"].dt.month / 12.0, "supplier_hist": ev["supplier_hist"]})
    cat = pd.get_dummies(ev["category"].fillna("UNKNOWN"), prefix="cat").astype(float)
    return pd.concat([base, cat], axis=1)


def rf_frame(ev, prefixes, times, k):
    X = static_frame(ev).copy()
    X["elapsed_h"] = [t[k - 1] for t in times]
    X["uniq"] = [len(set(a[:k])) for a in prefixes]
    X["repeats"] = [k - len(set(a[:k])) for a in prefixes]
    kth = pd.get_dummies(pd.Series([a[k - 1] for a in prefixes], index=X.index), prefix="kth").astype(float)
    first = pd.get_dummies(ev["first_activity"].fillna("UNKNOWN"), prefix="first").astype(float)
    return pd.concat([X, kth, first], axis=1)


def bootstrap_diff(y, a, b, n=500, seed=0):
    rng = np.random.default_rng(seed)
    d = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if len(set(y[i])) == 2:
            d.append(roc_auc_score(y[i], a[i]) - roc_auc_score(y[i], b[i]))
    lo, hi = np.percentile(d, [2.5, 97.5])
    return [float(np.mean(d)), float(lo), float(hi)]


def main(use_mlflow=True):
    torch.set_num_threads(1)
    events = clean_events(load_event_log(os.environ["RAW_EVENT_LOG_PATH"]))[0].sort_values(["case_id", "timestamp"])
    cases = build_process_cases(events)
    cases = cases[cases["cycle_time_hours"] > 0].sort_values("start_time").reset_index(drop=True)  # measurable only
    by_case = {cid: (g["activity"].tolist(), g["timestamp"].values) for cid, g in events.groupby("case_id")}
    n, cut = len(cases), int(len(cases) * 0.8)
    results = []
    for pct in PCTS:
        targets = targets_from_training_percentile(cases, pct)
        ev = evaluate_sla(cases, targets)
        ev["supplier_hist"] = causal_supplier_history(ev)
        y_all = ev["sla_breach"].astype(int).values
        static_all = static_frame(ev)
        for k in KS:
            rows = []
            for cid, tgt in zip(ev["case_id"], ev["sla_target_hours"]):
                acts, ts = by_case[cid]
                if len(acts) < k:
                    rows.append(None)
                    continue
                off = (ts[:k] - ts[0]) / np.timedelta64(1, "h")
                rows.append((acts[:k], off) if off[-1] <= tgt else None)
            keep = np.array([r is not None for r in rows])
            tr = np.where(keep & (np.arange(n) < cut))[0]
            te = np.where(keep & (np.arange(n) >= cut))[0]
            if len(te) < 30 or len(set(y_all[te])) < 2 or len(set(y_all[tr])) < 2:
                print(f"p{pct} k={k}: too few open cases")
                continue
            sub = np.concatenate([tr, te])
            pref = {i: rows[i] for i in sub}
            inner = int(len(tr) * 0.85)
            tr_fit, tr_val = tr[:inner], tr[inner:]
            vocab = ActivityVocab(a for i in tr for a in pref[i][0])
            stat_cols = static_all.columns

            def mk(idx):
                acts, feats = make_sequence_tensors([pref[i][0] for i in idx], [pref[i][1] for i in idx], vocab, k)
                st = torch.tensor(static_all.loc[idx, stat_cols].values, dtype=torch.float32)
                return acts, feats, st, torch.tensor(y_all[idx], dtype=torch.float32)

            T_fit, T_val, T_te = mk(tr_fit), mk(tr_val), mk(te)
            yte = y_all[te]

            evs = ev.loc[sub]
            Xrf = rf_frame(evs, [pref[i][0] for i in sub], [pref[i][1] for i in sub], k).fillna(0)
            Xtr, Xte = Xrf.loc[tr], Xrf.loc[te]
            rf = RandomForestClassifier(n_estimators=200, max_depth=8, class_weight="balanced",
                                        random_state=42).fit(Xtr, y_all[tr])
            p_rf = rf.predict_proba(Xte)[:, 1]
            one = Xte.iloc[[0]]
            rf_lat = cpu_latency_ms(lambda: rf.predict_proba(one))
            entry = {"pct": pct, "k": k, "n_train": len(tr), "n_test": len(te),
                     "base_rate": float(yte.mean()),
                     "rf": {**metrics(yte, p_rf), "latency_ms_p50": rf_lat[0], "latency_ms_p95": rf_lat[1]}}
            for cell in ("gru", "lstm"):
                ps, ms = [], []
                for seed in SEEDS:
                    torch.manual_seed(seed)
                    m = fit(SequenceRiskModel(len(vocab), T_fit[2].shape[1], cell), T_fit, T_val, seed=seed)
                    p = predict_proba(m, *T_te[:3])
                    ps.append(p)
                    ms.append(metrics(yte, p))
                one_in = tuple(t[:1] for t in T_te[:3])
                lat = cpu_latency_ms(lambda: predict_proba(m, *one_in))
                agg = {key: float(np.mean([x[key] for x in ms])) for key in ms[0]}
                agg["roc_auc_seed_std"] = float(np.std([x["roc_auc"] for x in ms]))
                agg["latency_ms_p50"], agg["latency_ms_p95"] = lat
                agg["gain_vs_rf_roc_auc"] = bootstrap_diff(yte, np.mean(ps, axis=0), p_rf)
                entry[cell] = agg
            results.append(entry)
            print(f"p{pct} k={k} n_te={len(te)} RF {entry['rf']['roc_auc']:.3f} | "
                  f"GRU {entry['gru']['roc_auc']:.3f} | LSTM {entry['lstm']['roc_auc']:.3f}", flush=True)
            if use_mlflow:
                try:
                    import mlflow
                    mlflow.set_experiment("sequence_vs_prefix_rf")
                    for name in ("rf", "gru", "lstm"):
                        with mlflow.start_run(run_name=f"p{pct}_k{k}_{name}"):
                            mlflow.log_params({"model": name, "k": k, "target_pct": pct,
                                               "n_train": len(tr), "n_test": len(te)})
                            mlflow.log_metrics({kk: v for kk, v in entry[name].items()
                                                if not isinstance(v, list)})
                except Exception as exc:
                    print(f"(mlflow skipped: {str(exc)[:80]})")
                    use_mlflow = False
    out = Path("reports")
    out.mkdir(exist_ok=True)
    (out / "sequence_model_results.json").write_text(json.dumps(results, indent=2))
    print("wrote reports/sequence_model_results.json")


if __name__ == "__main__":
    main("--no-mlflow" not in sys.argv)
