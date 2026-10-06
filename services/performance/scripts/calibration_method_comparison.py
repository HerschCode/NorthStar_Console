"""Calibration method comparison on the held-out window, for both targets (configured SLA ~97% breach,
training-window p75 ~27% breach). Reproduces the bug that shipped until 2026-09-25 and the fix.

Rows:
- raw:                the forest fitted on the earliest 80% of the training window, uncalibrated
- old (in-sample isotonic): forest fitted on the FULL training window, isotonic fitted on those same rows
                      (sklearn CalibratedClassifierCV + FrozenEstimator) -- the previously served model
- isotonic (held-out): isotonic fitted on the latest 20% of the training window
- sigmoid (held-out):  Platt scaling fitted on that same slice
Metrics on the untouched test window: Brier score, ROC-AUC, PR-AUC, and tied scores
(= n_test - number of distinct scores). "auto" is what src/ml/train.py serves.

Run: python -m scripts.calibration_method_comparison  -> reports/calibration_comparison.json
"""
import json
import warnings
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from scripts.target_sensitivity import targets_from_training_percentile
from src.analytics.sla_analysis import evaluate_sla, load_sla_targets
from src.api.db import load_cases
from src.ml.features import build_features
from src.ml.train import CALIBRATION_FRACTION, calibrate_model, choose_calibration_method, time_based_split

warnings.filterwarnings("ignore")


def forest(X, y):
    return RandomForestClassifier(n_estimators=200, max_depth=8, class_weight="balanced", random_state=42).fit(X, y)


def metrics(y, p):
    return {"brier": round(float(brier_score_loss(y, p)), 5), "roc_auc": round(float(roc_auc_score(y, p)), 4),
            "pr_auc": round(float(average_precision_score(y, p)), 4),
            "tied_scores": int(len(p) - len(np.unique(p))), "distinct_scores": int(len(np.unique(p)))}


def run_target(name, ev):
    X, y = build_features(ev)
    tr, te = time_based_split(ev)
    Xtr, ytr, Xte, yte = X.loc[tr], y.loc[tr], X.loc[te], y.loc[te]
    cut = int(len(ytr) * (1 - CALIBRATION_FRACTION))
    base = forest(Xtr.iloc[:cut], ytr.iloc[:cut])
    Xc, yc = Xtr.iloc[cut:], ytr.iloc[cut:]
    rows = {"raw": metrics(yte, base.predict_proba(Xte)[:, 1])}
    old_forest = forest(Xtr, ytr)
    old = CalibratedClassifierCV(FrozenEstimator(old_forest), method="isotonic").fit(Xtr, ytr)
    rows["old: isotonic fitted on training rows"] = metrics(yte, old.predict_proba(Xte)[:, 1])
    rows["old: (its raw forest, full train)"] = metrics(yte, old_forest.predict_proba(Xte)[:, 1])
    for m in ("isotonic", "sigmoid"):
        rows[f"{m} (held-out slice)"] = metrics(yte, calibrate_model(base, Xc, yc, m).predict_proba(Xte)[:, 1])
    chosen = choose_calibration_method(base, Xc, yc)
    return {"target": name, "n_test": len(yte), "base_rate": round(float(yte.mean()), 4),
            "negatives_in_test": int((yte == 0).sum()), "negatives_in_calibration_slice": int((yc == 0).sum()),
            "auto_choice": chosen, "methods": rows}


def main():
    load_dotenv()
    cases = load_cases()
    cases = cases[cases["cycle_time_hours"].astype(float) > 0].sort_values("start_time").reset_index(drop=True)  # measurable only
    out = {"configured": run_target("configured SLA targets", evaluate_sla(cases, load_sla_targets())),
           "p75": run_target("training-window per-category p75", evaluate_sla(cases, targets_from_training_percentile(cases, 75)))}
    Path("reports").mkdir(exist_ok=True)
    Path("reports/calibration_comparison.json").write_text(json.dumps(out, indent=2))
    for k, v in out.items():
        print(f"\n[{k}] n_test={v['n_test']} base={v['base_rate']} negatives test/cal={v['negatives_in_test']}/{v['negatives_in_calibration_slice']} auto={v['auto_choice']}")
        for m, r in v["methods"].items():
            print(f"  {m:42s} Brier {r['brier']:.4f}  ROC-AUC {r['roc_auc']:.4f}  PR-AUC {r['pr_auc']:.4f}  tied {r['tied_scores']}")


if __name__ == "__main__":
    main()
