"""
Manual run of src/ml/tune.py's hyperparameter search against the real live database
-- not part of the automated retrain path (scripts/check_and_retrain.py deliberately
keeps using train.py's fixed hyperparameters, since tuning is an investment decision
a human should review, not something that silently changes on every scheduled
retrain). Run this when deciding whether new hyperparameters are worth adopting;
inspect results with `mlflow ui` (reads ./mlruns, the same directory this writes to).
"""
import os
from dotenv import load_dotenv

from src.api.db import load_cases
from src.analytics.sla_analysis import load_sla_targets, evaluate_sla
from src.ml.features import build_features
from src.ml.tune import tune_random_forest, tune_gradient_boosting


def main():
    load_dotenv()
    cases = load_cases()
    evaluated = evaluate_sla(cases, load_sla_targets())
    X, y = build_features(evaluated)

    print("Tuning random forest...")
    rf_result = tune_random_forest(X, y, evaluated)
    print(f"  best params: {rf_result['best_params']}")
    print(f"  cv_best_roc_auc={rf_result['cv_best_roc_auc']:.4f} test_roc_auc={rf_result['roc_auc']:.4f}")

    print("\nTuning gradient boosting...")
    gb_result = tune_gradient_boosting(X, y, evaluated)
    print(f"  best params: {gb_result['best_params']}")
    print(f"  cv_best_roc_auc={gb_result['cv_best_roc_auc']:.4f} test_roc_auc={gb_result['roc_auc']:.4f}")

    print("\nRun `mlflow ui` and open http://localhost:5000 to compare these runs visually.")


if __name__ == "__main__":
    main()
