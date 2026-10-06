"""
Domain-shift / generalization analysis for the SLA breach predictor.

The model was trained on all BPI 2019 purchase-order categories together.
This script asks two questions:

  1. Per-category performance: does the existing model perform equally across
     all 17 purchase categories, or does it degrade on minority ones?

  2. Leave-majority-out: if we train ONLY on the two dominant categories
     (Packaging + Sales, ~59% of the data) and test on all others, how much
     does performance degrade? This simulates deploying to a company whose
     process mix differs from the training mix.

Outputs:
  reports/p1_domain_shift.json   — structured results for READMEs / dashboards
  (printed to stdout)

Requires .env with Neon DB credentials (to load live case data for scoring).
The existing trained model bundle (models/sla_risk_model.joblib) is used
for question 1 -- no retraining needed. Question 2 retrains a lightweight
model in-process (no Neon write -- model is not saved).

Run from repo root:
    python scripts/domain_shift_analysis.py
"""
import json
import sys
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
load_dotenv()

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score

from src.api.db import load_cases
from src.analytics.sla_analysis import evaluate_sla, load_sla_targets
from src.ml.features import build_features
from src.ml.predict import load_model, predict_sla_risk

MIN_CATEGORY_SIZE = 20  # skip categories too small to score reliably


def _evaluate_model_on_subset(model, columns, X_full, y_full, mask):
    """Evaluate a fitted model on the rows selected by mask."""
    X_sub = X_full[mask].reindex(columns=columns, fill_value=0)
    y_sub = y_full[mask]
    if len(y_sub) < MIN_CATEGORY_SIZE or y_sub.nunique() < 2:
        return None
    probs = model.predict_proba(X_sub)[:, 1]
    return {
        "n": int(len(y_sub)),
        "breach_rate": round(float(y_sub.mean()), 3),
        "roc_auc": round(roc_auc_score(y_sub, probs), 4),
        "brier_score": round(brier_score_loss(y_sub, probs), 4),
    }


def _train_and_eval(X_train, y_train, X_test, y_test, columns):
    """Train a Random Forest on the given split, evaluate on test set."""
    if len(y_train) < MIN_CATEGORY_SIZE or y_train.nunique() < 2:
        return None
    if len(y_test) < MIN_CATEGORY_SIZE or y_test.nunique() < 2:
        return None

    rf = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1)
    cal = CalibratedClassifierCV(rf, cv=3, method="isotonic")
    cal.fit(X_train.reindex(columns=columns, fill_value=0), y_train)

    X_t = X_test.reindex(columns=columns, fill_value=0)
    probs = cal.predict_proba(X_t)[:, 1]
    return {
        "train_n": int(len(y_train)),
        "test_n": int(len(y_test)),
        "test_breach_rate": round(float(y_test.mean()), 3),
        "roc_auc": round(roc_auc_score(y_test, probs), 4),
        "brier_score": round(brier_score_loss(y_test, probs), 4),
    }


def main():
    print("Loading data...")
    cases = load_cases()
    evaluated = evaluate_sla(cases, load_sla_targets())
    X, y = build_features(evaluated)

    if "category" not in evaluated.columns:
        print("No 'category' column found — cannot do per-category analysis.")
        sys.exit(1)

    categories = evaluated["category"].values
    category_counts = evaluated["category"].value_counts()
    print(f"Total cases: {len(y)}, breach rate: {y.mean():.1%}")
    print(f"Categories: {len(category_counts)}")

    # --- Part 1: per-category performance of the existing model ---
    print("\n[1] Loading existing model bundle...")
    bundle = load_model()
    trained_model = bundle["model"]
    columns = bundle["columns"]

    per_category = {}
    for cat in category_counts.index:
        mask = (categories == cat)
        result = _evaluate_model_on_subset(trained_model, columns, X, y, mask)
        if result:
            per_category[cat] = result
            print(f"  {cat:<30} n={result['n']:>4}  "
                  f"breach={result['breach_rate']:.1%}  "
                  f"ROC-AUC={result['roc_auc']:.3f}  "
                  f"Brier={result['brier_score']:.4f}")
        else:
            print(f"  {cat:<30} skipped (n={int(mask.sum())}, too small or single class)")

    scored_auc = [v["roc_auc"] for v in per_category.values()]
    auc_range = {"min": round(min(scored_auc), 4), "max": round(max(scored_auc), 4),
                 "std": round(float(np.std(scored_auc)), 4)} if scored_auc else {}

    # --- Part 2: leave-majority-out ---
    # Train on Packaging + Sales (the two largest, ~59% of data), test on the rest.
    majority_cats = {"Packaging", "Sales"}
    majority_mask = np.isin(categories, list(majority_cats))
    minority_mask = ~majority_mask

    print(f"\n[2] Leave-majority-out:")
    print(f"  Majority (train): Packaging + Sales — {majority_mask.sum()} cases")
    print(f"  Minority (test):  all other categories — {minority_mask.sum()} cases")

    X_train = X[majority_mask]
    y_train = y[majority_mask]
    X_test = X[minority_mask]
    y_test = y[minority_mask]

    lmo = _train_and_eval(X_train, y_train, X_test, y_test, columns)
    if lmo:
        print(f"  Test ROC-AUC: {lmo['roc_auc']:.3f}  (full model: {bundle.get('brier_score') and 'see meta'})")
        print(f"  Test Brier:   {lmo['brier_score']:.4f}")

    # Full model overall stats (from meta sidecar)
    meta_path = REPO_ROOT / "models" / "sla_risk_model.meta.json"
    full_model_meta = {}
    if meta_path.exists():
        full_model_meta = json.loads(meta_path.read_text())

    report = {
        "full_model": {
            "trained_on": "all categories",
            "roc_auc": full_model_meta.get("roc_auc"),
            "train_rows": full_model_meta.get("train_row_count"),
            "test_rows": full_model_meta.get("test_row_count"),
        },
        "per_category_performance": per_category,
        "per_category_auc_range": auc_range,
        "leave_majority_out": {
            "train_categories": sorted(majority_cats),
            "test_categories": sorted(c for c in category_counts.index if c not in majority_cats),
            **lmo,
        } if lmo else None,
        "interpretation": (
            "Per-category ROC-AUC varies across process types. "
            "The leave-majority-out experiment trains on Packaging+Sales only "
            "(~59% of cases) and tests on all other categories -- a proxy for "
            "deploying to a different company whose process mix differs from the "
            "training distribution. AUC degradation quantifies the generalization gap."
        ),
    }

    out = REPO_ROOT / "reports" / "p1_domain_shift.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nSaved → {out}")


if __name__ == "__main__":
    main()
