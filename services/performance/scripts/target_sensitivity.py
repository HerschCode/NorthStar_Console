"""Re-evaluate the SLA-risk model on less degenerate breach definitions.

The configured SLA targets (config/sla.yaml, 10-14 days) sit far below BPI 2019's real cycle
times, so ~94% of cases breach and the held-out split has only 18 negatives in 600 -- ROC-AUC
on that split is computed over a handful of negatives. Here the target is instead set to a
percentile of the TRAINING window's cycle time (computed on the first 80% of cases by start
time only, so the test period never influences the label definition), giving base rates the
model can actually be tested on.

For each target definition it reports, on the latest purchase-order groups held out in time:
base rate, number of negatives, ROC-AUC, PR-AUC, and the same for five PO-grouped temporal folds, with
(a) all features and (b) creation-time-only features (see docs/prediction-time-availability.md).

Run: python -m scripts.target_sensitivity   (writes docs/less-degenerate-target.md numbers to stdout)
"""
import os
import warnings

import numpy as np
from dotenv import load_dotenv
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

from src.analytics.sla_analysis import evaluate_sla
from src.cleaning.clean_events import clean_events
from src.ingestion.load_event_log import load_event_log
from src.ml.features import build_features
from src.ml.train import time_based_split, time_series_group_splits
from src.transformation.build_process_cases import build_process_cases

warnings.filterwarnings("ignore")
load_dotenv()

END_ONLY = {"event_count", "variant_frequency", "unique_activity_count", "rework_count"}
HIST = {"supplier_historical_breach_rate", "supplier_historical_median_cycle_time"}


def _end_only(c: str) -> bool:
    return c in END_ONLY or c.startswith("last_activity_")


def targets_from_training_percentile(cases, pct: float, train_frac: float = 0.8) -> dict:
    ordered = cases[cases["cycle_time_hours"].astype(float) > 0].sort_values("start_time")   # measurable cases only
    train_idx, _ = time_based_split(ordered, test_size=1 - train_frac)
    train = ordered.loc[train_idx]
    targets = {"default": float(train["cycle_time_hours"].quantile(pct / 100))}
    for cat, grp in train.groupby("category"):
        if len(grp) >= 30:
            targets[cat] = float(grp["cycle_time_hours"].quantile(pct / 100))
    return targets


def _models():
    return {
        "LR": lambda: LogisticRegression(max_iter=3000, class_weight="balanced"),
        "RF": lambda: RandomForestClassifier(
            n_estimators=200, max_depth=8, class_weight="balanced", random_state=42),
    }


def _score(y_true, p):
    if y_true.nunique() < 2:
        return np.nan, np.nan
    return roc_auc_score(y_true, p), average_precision_score(y_true, p)


def evaluate(cases, label: str):
    X, y = build_features(cases)
    cols_all = list(X.columns)
    cols_early = [c for c in cols_all if not _end_only(c) and c not in HIST]
    tr, te = time_based_split(cases)
    print(f"\n### {label}: overall base rate {y.mean():.3f} | holdout base rate {y.loc[te].mean():.3f} "
          f"| holdout negatives {(1 - y.loc[te]).sum()} of {len(te)}")
    for fs_name, cols in [("all features", cols_all), ("creation-time only", cols_early)]:
        for mname, mk in _models().items():
            m = mk().fit(X.loc[tr, cols], y.loc[tr])
            roc, pr = _score(y.loc[te], m.predict_proba(X.loc[te, cols])[:, 1])
            folds = []
            for fold_train, fold_test in time_series_group_splits(cases, n_splits=5):
                if y.loc[fold_train].nunique() < 2 or y.loc[fold_test].nunique() < 2:
                    continue
                mm = mk().fit(X.loc[fold_train, cols], y.loc[fold_train])
                folds.append(roc_auc_score(y.loc[fold_test], mm.predict_proba(X.loc[fold_test, cols])[:, 1]))
            print(f"  {fs_name:20s} {mname}: holdout ROC-AUC {roc:.3f}  PR-AUC {pr:.3f} "
                  f"(base {y.iloc[te].mean():.3f}) | 5-fold mean ROC-AUC {np.mean(folds):.3f} "
                  f"(min {np.min(folds):.3f}, max {np.max(folds):.3f})")


def main():
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(raw)
    cases = build_process_cases(cleaned)
    cases = cases[cases["cycle_time_hours"] > 0].sort_values("start_time").reset_index(drop=True)  # measurable only

    from src.analytics.sla_analysis import load_sla_targets
    evaluate(evaluate_sla(cases, load_sla_targets()), "configured SLA (10-14 days) -- current deployed target")
    for pct in (50, 75):
        t = targets_from_training_percentile(cases, pct)
        evaluate(evaluate_sla(cases, t), f"target = training-window p{pct} cycle time per category "
                                          f"(default {t['default'] / 24:.0f} days)")


if __name__ == "__main__":
    main()
