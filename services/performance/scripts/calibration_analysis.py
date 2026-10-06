"""
Probability calibration analysis for the SLA breach predictor.

Answers: "Is breach_probability: 0.73 actually meaningful, or is it just a
ranking score?" — i.e. when the model says 73%, do ~73% of those cases
actually breach?

Outputs:
  - Brier score (before and after calibration) for each model
  - Reliability diagram data (fraction_of_positives vs mean_predicted_value)
  - Summary printed to stdout
  - reliability_diagram.json saved to models/ for the dashboard to consume

Run from repo root:
    python scripts/calibration_analysis.py
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sklearn.calibration import calibration_curve
from sklearn.metrics import brier_score_loss

from src.ingestion.load_event_log import load_event_log
from src.cleaning.clean_events import clean_events
from src.transformation.build_process_cases import build_process_cases
from src.analytics.sla_analysis import evaluate_sla, load_sla_targets
from src.ml.features import build_features
from src.ml.train import calibrate_model, time_based_split, train_models


def brier_skill_score(brier, y_test):
    """Brier Skill Score vs the naive 'always predict the base rate' forecaster.
    BSS = 1 means perfect, 0 means no better than climatology, negative means worse."""
    base_rate = y_test.mean()
    brier_baseline = brier_score_loss(y_test, [base_rate] * len(y_test))
    return 1 - brier / brier_baseline


def main():
    load_dotenv()
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(raw)
    cases = build_process_cases(cleaned)
    evaluated = evaluate_sla(cases, load_sla_targets())
    X, y = build_features(evaluated)

    train_idx, test_idx = time_based_split(evaluated)
    X_train, X_test = X.loc[train_idx], X.loc[test_idx]
    y_train, y_test = y.loc[train_idx], y.loc[test_idx]

    results = train_models(X, y, evaluated)

    print(f"\n{'='*72}")
    print(f"  Calibration Analysis — SLA Breach Predictor")
    print(f"  Test set: {len(y_test)} cases, breach rate {y_test.mean():.1%}")
    print(f"{'='*72}")
    print(f"\n  {'Model':<25} {'Brier (raw)':>11} {'Brier (cal)':>11} {'BSS (cal)':>10}  {'Improvement':>12}")
    print(f"  {'-'*25} {'-'*11} {'-'*11} {'-'*10}  {'-'*12}")

    diagram_data = {}

    for name in ["logistic_regression", "random_forest", "gradient_boosting"]:
        r = results[name]
        raw_model = r["model"]
        cal_model = r["calibrated_model"]

        raw_probs = raw_model.predict_proba(X_test)[:, 1]
        cal_probs = cal_model.predict_proba(X_test)[:, 1]

        brier_raw = brier_score_loss(y_test, raw_probs)
        brier_cal = brier_score_loss(y_test, cal_probs)
        bss = brier_skill_score(brier_cal, y_test)
        improvement = brier_raw - brier_cal

        print(f"  {name:<25} {brier_raw:>11.4f} {brier_cal:>11.4f} {bss:>10.3f}  {improvement:>+12.4f}")

        # Reliability diagram: both raw and calibrated curves
        frac_raw, pred_raw = calibration_curve(y_test, raw_probs, n_bins=10, strategy="quantile")
        frac_cal, pred_cal = calibration_curve(y_test, cal_probs, n_bins=10, strategy="quantile")

        diagram_data[name] = {
            "raw": {
                "fraction_of_positives": frac_raw.tolist(),
                "mean_predicted_value": pred_raw.tolist(),
                "brier": round(float(brier_raw), 4),
            },
            "calibrated": {
                "fraction_of_positives": frac_cal.tolist(),
                "mean_predicted_value": pred_cal.tolist(),
                "brier": round(float(brier_cal), 4),
                "brier_skill_score": round(float(bss), 4),
            },
        }

    base_rate = float(y_test.mean())
    brier_baseline = brier_score_loss(y_test, [base_rate] * len(y_test))
    print(f"\n  Baseline (always predict {base_rate:.1%}): Brier = {brier_baseline:.4f}")
    print(f"\n  Lower Brier = better. Perfect = 0.0. BSS > 0 means better than naive.")

    # Save for dashboard consumption
    out = Path("models/calibration_data.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "test_breach_rate": base_rate,
        "brier_baseline": round(brier_baseline, 4),
        "models": diagram_data,
    }, indent=2))
    print(f"\n  Reliability diagram data saved to {out}")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    main()
