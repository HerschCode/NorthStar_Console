"""Prediction-time availability check: how much of the model's ROC-AUC survives when
only features knowable at the moment a case is created are allowed?

event_count, variant_frequency (the full activity sequence), last_activity,
unique_activity_count and rework_count are all aggregated over the FINISHED case, so a
model using them cannot score a case at creation time -- it can only classify a case
that has (nearly) completed. The supplier-history features use prior cases' outcomes,
which are only known once those cases have ended, so they are reported separately.
Run: python -m scripts.prediction_time_check  (results in docs/prediction-time-availability.md)
"""
import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv()
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score

from src.ingestion.load_event_log import load_event_log
from src.cleaning.clean_events import clean_events
from src.transformation.build_process_cases import build_process_cases
from src.analytics.sla_analysis import load_sla_targets, evaluate_sla
from src.ml.features import build_features
from src.ml.train import time_series_group_splits

raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
cleaned, _ = clean_events(raw)
cases = build_process_cases(cleaned)
cases = cases[cases["cycle_time_hours"] > 0].reset_index(drop=True)
cases = evaluate_sla(cases, load_sla_targets())
X, y = build_features(cases)

END_ONLY = ["event_count", "variant_frequency", "unique_activity_count", "rework_count"]
def is_end_only(c):
    return c in END_ONLY or c.startswith("last_activity_")
# supplier history features use prior cases' outcomes, which are only known once those
# cases have ended -- flag them separately
HIST = ["supplier_historical_breach_rate", "supplier_historical_median_cycle_time"]

sets = {
    "all 15 (current)": list(X.columns),
    "drop end-only (event_count, variant, last_act, unique, rework)": [c for c in X.columns if not is_end_only(c)],
    "creation-time only (also drop supplier history)": [c for c in X.columns if not is_end_only(c) and c not in HIST],
}
print("rows", len(X), "breach rate", round(y.mean(), 3))
for name, cols in sets.items():
    for mname, mk in [("LR", lambda: LogisticRegression(max_iter=3000, class_weight="balanced")),
                      ("RF", lambda: RandomForestClassifier(n_estimators=200, max_depth=8, class_weight="balanced", random_state=42))]:
        scores = []
        for tr, te in time_series_group_splits(cases, n_splits=5):
            if y.loc[te].nunique() < 2 or y.loc[tr].nunique() < 2:
                continue
            m = mk().fit(X.loc[tr, cols], y.loc[tr])
            scores.append(roc_auc_score(y.loc[te], m.predict_proba(X.loc[te, cols])[:, 1]))
        print(f"{name:66s} {mname} n_feat={len(cols):3d} folds={len(scores)} mean AUC={np.mean(scores):.3f} folds={[round(s,3) for s in scores]}")
