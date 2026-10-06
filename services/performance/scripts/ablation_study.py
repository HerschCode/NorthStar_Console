"""
Feature-family ablation study for SLA breach prediction.

Trains Logistic Regression on four incremental feature sets and reports
ROC-AUC, PR-AUC (AP), and F1 so we can see each family's marginal value.

Feature families (cumulative, in addition order):
  baseline         — event_count, variant_frequency, category, supplier_id
  + temporal       — start_hour, start_dayofweek, start_month, start_quarter
  + process        — first_activity, last_activity, unique_activity_count, rework_count
  + supplier hist  — supplier_historical_breach_rate,
                     supplier_historical_median_cycle_time, sla_target_hours

All models use five expanding temporal folds grouped by purchase order, so related
cases cannot cross a train/validation boundary.

Run from repo root:
    python scripts/ablation_study.py

Requires: scikit-learn, pandas, python-dotenv, psycopg2
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.ingestion.load_event_log import load_event_log
from src.cleaning.clean_events import clean_events
from src.transformation.build_process_cases import build_process_cases
from src.analytics.sla_analysis import evaluate_sla, load_sla_targets
from src.ml.features import (
    FEATURE_FAMILIES,
    _add_derived_features,
    build_features,
)
from src.ml.train import time_series_group_splits


FAMILY_ORDER = ["baseline", "temporal", "process", "supplier_history"]

FAMILY_LABELS = {
    "baseline": "Baseline (structural)",
    "temporal": "+ Temporal (month/quarter/hour/dow)",
    "process":  "+ Process (unique acts / rework / first|last act)",
    "supplier_history": "+ Supplier history (breach rate / median cycle time / sla target)",
}

N_SPLITS = 5


def cumulative_families(up_to: int) -> list[str]:
    return FAMILY_ORDER[: up_to + 1]


def features_for_families(df: pd.DataFrame, families: list[str]) -> pd.DataFrame:
    cols = []
    for fam in families:
        cols.extend(FEATURE_FAMILIES[fam])
    available = [c for c in cols if c in df.columns]
    X = df[available].copy()
    for cat_col in ["category", "supplier_id", "first_activity", "last_activity"]:
        if cat_col in X.columns:
            X[cat_col] = X[cat_col].fillna("UNKNOWN")
            X = pd.get_dummies(X, columns=[cat_col], prefix=cat_col)
    return X


def cv_scores(X: pd.DataFrame, y: pd.Series, cases: pd.DataFrame) -> dict:
    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("lr", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42)),
    ])

    aucs, aps, f1s = [], [], []
    for train_idx, val_idx in time_series_group_splits(cases, n_splits=N_SPLITS):
        X_tr, X_val = X.loc[train_idx], X.loc[val_idx]
        y_tr, y_val = y.loc[train_idx], y.loc[val_idx]
        if y_tr.nunique() < 2 or y_val.nunique() < 2:
            continue
        pipe.fit(X_tr, y_tr)
        prob = pipe.predict_proba(X_val)[:, 1]
        pred = pipe.predict(X_val)
        aucs.append(roc_auc_score(y_val, prob))
        aps.append(average_precision_score(y_val, prob))
        f1s.append(f1_score(y_val, pred, zero_division=0))

    return {
        "roc_auc": float(np.mean(aucs)),
        "pr_auc":  float(np.mean(aps)),
        "f1":      float(np.mean(f1s)),
        "n_features": int(X.shape[1]),
    }


def main():
    load_dotenv()
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(raw)
    cases = build_process_cases(cleaned)
    cases = cases[cases["cycle_time_hours"] > 0].reset_index(drop=True)
    evaluated = evaluate_sla(cases, load_sla_targets())

    df = _add_derived_features(evaluated.copy())
    y = df["sla_breach"].astype(int)

    print(f"\n{'='*72}")
    print(f"  Feature-family ablation  |  {N_SPLITS} PO-grouped temporal folds  |  LR")
    print(f"{'='*72}")
    print(f"  {'Feature set':<52}  {'ROC-AUC':>7}  {'PR-AUC':>7}  {'F1':>6}  {'#feat':>6}")
    print(f"  {'-'*52}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*6}")

    prev_auc = None
    for i, fam_name in enumerate(FAMILY_ORDER):
        families = cumulative_families(i)
        X = features_for_families(df, families)
        scores = cv_scores(X, y, evaluated)

        delta = ""
        if prev_auc is not None:
            diff = scores["roc_auc"] - prev_auc
            delta = f"  ({'+' if diff >= 0 else ''}{diff:.3f})"
        prev_auc = scores["roc_auc"]

        label = FAMILY_LABELS[fam_name]
        print(
            f"  {label:<52}  {scores['roc_auc']:>7.3f}  {scores['pr_auc']:>7.3f}  "
            f"{scores['f1']:>6.3f}  {scores['n_features']:>6}{delta}"
        )

    print(f"{'='*72}\n")


if __name__ == "__main__":
    main()
