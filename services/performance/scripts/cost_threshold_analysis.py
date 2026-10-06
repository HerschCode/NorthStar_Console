"""
Cost-sensitive threshold analysis for the SLA breach predictor.

Standard ML evaluation (ROC-AUC, F1) treats FP and FN as equally costly.
In operations, they are not:
  - A false negative (missed breach prediction) = a supplier SLA breach that
    arrives as a surprise, causing downstream disruption. Estimated cost: high.
  - A false positive (false alarm) = an analyst investigates a case that doesn't
    breach. Estimated cost: low (analyst hours only).

This script uses the calibrated model's probabilities to sweep the decision
threshold and compute expected cost under three C_FN/C_FP cost ratios. The
calibrated model is the key enabler: an uncalibrated model produces rankings,
not probabilities. The calibrated model produces P(breach | features) ≈ actual
breach rate at that score, so a threshold maps directly to a business-meaningful
operating point: "flag every case the model scores ≥ t (t is computed by this script, not fixed)" is an operational
instruction, not just a ranking.

Run from repo root (requires RAW_EVENT_LOG_PATH env var or .env file):
    python scripts/cost_threshold_analysis.py

Outputs: per-threshold precision/recall/cost table and recommended threshold,
saved to models/threshold_analysis.json.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

load_dotenv()

from sklearn.metrics import confusion_matrix

from src.ingestion.load_event_log import load_event_log
from src.cleaning.clean_events import clean_events
from src.transformation.build_process_cases import build_process_cases
from src.analytics.sla_analysis import evaluate_sla, load_sla_targets
from src.ml.features import build_features
from src.ml.train import calibrate_model, time_based_split, train_models

THRESHOLDS = np.arange(0.10, 0.95, 0.05).round(2)

# C_FN / C_FP: how many times more expensive is a missed breach than a false alarm?
COST_RATIOS = {
    "5:1":  (5,  1),
    "10:1": (10, 1),
    "20:1": (20, 1),
}


def main():
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(raw)
    cases = build_process_cases(cleaned)
    evaluated = evaluate_sla(cases, load_sla_targets())
    X, y = build_features(evaluated)

    train_idx, test_idx = time_based_split(evaluated)
    X_train, X_test = X.loc[train_idx], X.loc[test_idx]
    y_train, y_test = y.loc[train_idx], y.loc[test_idx]

    # Calibrated gradient boosting (temporal held-out calibration, src/ml/train.py) for the threshold sweep
    results = train_models(X, y, evaluated)
    gb_cal = results["gradient_boosting"]["calibrated_model"]
    probs = gb_cal.predict_proba(X_test)[:, 1]
    from sklearn.metrics import brier_score_loss
    brier = brier_score_loss(y_test, probs)
    bss = 1 - brier / brier_score_loss(y_test, [y_test.mean()] * len(y_test))

    n_pos = int(y_test.sum())
    n_neg = int((~y_test.astype(bool)).sum())

    print(f"\n{'='*76}")
    print(f"  Cost-Sensitive Threshold Analysis — Calibrated Gradient Boosting (BSS={bss:.3f})")
    print(f"  Test set: {len(y_test)} cases | {n_pos} breaches ({n_pos/len(y_test):.1%}) | {n_neg} non-breaches")
    print(f"{'='*76}\n")

    ratio_hdr = "  ".join(f"{'cost_'+k:>12}" for k in COST_RATIOS)
    print(f"  {'thresh':>6}  {'precision':>9}  {'recall':>6}  {'FP':>4}  {'FN':>4}  {ratio_hdr}")
    print(f"  {'-'*6}  {'-'*9}  {'-'*6}  {'-'*4}  {'-'*4}  {'  '.join(['-'*12]*len(COST_RATIOS))}")

    records = []
    for t in THRESHOLDS:
        preds = (probs >= t).astype(int)
        tn, fp, fn, tp = confusion_matrix(y_test, preds, labels=[0, 1]).ravel()
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        costs = {k: int(cfn * fn + cfp * fp) for k, (cfn, cfp) in COST_RATIOS.items()}

        cost_str = "  ".join(f"{costs[k]:>12,}" for k in COST_RATIOS)
        print(f"  {t:>6.2f}  {precision:>9.1%}  {recall:>6.1%}  {fp:>4}  {fn:>4}  {cost_str}")

        records.append({
            "threshold": float(t),
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
            "costs": costs,
        })

    # Optimal threshold per cost ratio
    print(f"\n  Optimal threshold per cost ratio:")
    recommendations = {}
    for ratio_name in COST_RATIOS:
        best = min(records, key=lambda r: r["costs"][ratio_name])
        recommendations[ratio_name] = best["threshold"]
        print(f"    {ratio_name:>5}  t={best['threshold']:.2f}  "
              f"precision={best['precision']:.1%}  recall={best['recall']:.1%}  "
              f"cost={best['costs'][ratio_name]:,}")

    # Default t=0.5 baseline
    default = next((r for r in records if abs(r["threshold"] - 0.5) < 0.01), None)
    if default:
        print(f"\n  Sklearn default t=0.50: precision={default['precision']:.1%}  "
              f"recall={default['recall']:.1%}  10:1 cost={default['costs']['10:1']:,}")

    # Interpretation at recommended 10:1 threshold
    rec_t = recommendations.get("10:1", 0.5)
    rec = next((r for r in records if abs(r["threshold"] - rec_t) < 0.01), None)
    if rec:
        print(f"\n  Recommended operating point (10:1, t={rec_t:.2f}):")
        print(f"    Flag {rec['tp']+rec['fp']} cases (TP={rec['tp']}, FP={rec['fp']})")
        print(f"    Miss {rec['fn']} of {n_pos} actual breaches (recall {rec['recall']:.1%})")
        print(f"    Precision {rec['precision']:.1%} — of every 10 flagged cases, "
              f"~{round(rec['precision']*10):.0f} are real breaches")
        print(f"  Of the {rec['tp']+rec['fp']} cases scored >= {rec_t:.2f}, {rec['precision']:.1%} actually breached.")
        print(f"  NOTE: with a {n_pos/len(y_test):.0%} base rate this flags nearly every case, so the threshold barely discriminates.")

    print(f"\n{'='*76}\n")

    out = REPO_ROOT / "models" / "threshold_analysis.json"
    out.write_text(json.dumps({
        "model": "gradient_boosting_calibrated",
        "bss": round(float(bss), 4),
        "test_size": len(y_test),
        "n_breaches": n_pos,
        "n_clean": n_neg,
        "thresholds": records,
        "recommendations": recommendations,
    }, indent=2))
    print(f"  Saved to models/threshold_analysis.json\n")


if __name__ == "__main__":
    main()
