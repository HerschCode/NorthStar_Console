"""
Hyperparameter tuning + MLflow experiment tracking, additive alongside
src/ml/train.py's fixed-hyperparameter 3-model comparison -- that module's own
comment named this gap explicitly ("3-model comparison with fixed hyperparameters
and time-series CV now exists, tuning doesn't yet"). train.py is unchanged and
still what scripts/check_and_retrain.py and save_best_model() use for the actual
deployed model; this module is a genuine improvement path someone would run
before deciding to adopt new hyperparameters, not a silent replacement.

RandomizedSearchCV (not GridSearchCV) -- the parameter spaces below have enough
combinations that an exhaustive grid would cost meaningfully more compute for
diminishing returns; a randomized search over the same space finds comparably
good hyperparameters for a fraction of the fits. Cross-validation is forward-in-time
and purchase-order-grouped to prevent both temporal and same-order leakage.

MLflow tracking uses a local SQLite file (`sqlite:///mlflow.db`), not the plain
`file:./mlruns` directory backend that used to be MLflow's own default -- MLflow
3.x put that file-store backend into maintenance mode and raises unless you
explicitly opt back in (found by actually running this against live data, not
assumed from the docs). SQLite still needs no tracking server, same zero-
infrastructure story for a single-developer portfolio project; `mlflow ui
--backend-store-uri sqlite:///mlflow.db` reads the same file.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import RandomizedSearchCV

from src.ml.train import time_based_split, time_series_group_splits, _evaluate

RANDOM_FOREST_PARAM_SPACE = {
    "n_estimators": [100, 200, 300, 400],
    "max_depth": [4, 6, 8, 10, None],
    "min_samples_leaf": [1, 2, 4, 8],
    "max_features": ["sqrt", "log2", None],
}

GRADIENT_BOOSTING_PARAM_SPACE = {
    "n_estimators": [50, 100, 150, 200],
    "max_depth": [2, 3, 4, 5],
    "learning_rate": [0.01, 0.05, 0.1, 0.2],
    "subsample": [0.6, 0.8, 1.0],
}


def _grouped_cv_splits(cases: pd.DataFrame, n_splits: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Convert shared group-fold row indexes into sklearn's positional CV indexes."""
    return [
        (cases.index.get_indexer(train_idx), cases.index.get_indexer(test_idx))
        for train_idx, test_idx in time_series_group_splits(cases, n_splits=n_splits)
    ]


def _mlflow_log_run(run_name: str, params: dict, metrics: dict, tracking_uri: str = "sqlite:///mlflow.db"):
    """Isolated so tests can patch this one function rather than mocking the whole
    mlflow module -- and so tuning still works (just without tracking) if mlflow
    genuinely isn't installed, since it's not a hard dependency of the rest of this
    project's ML pipeline."""
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("sla_risk_model_tuning")
    with mlflow.start_run(run_name=run_name):
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)


def tune_random_forest(
    X: pd.DataFrame, y: pd.Series, cases: pd.DataFrame,
    n_iter: int = 20, n_splits: int = 5, random_state: int = 42,
    log_to_mlflow: bool = True,
) -> dict:
    train_idx, test_idx = time_based_split(cases)
    X_train, X_test = X.loc[train_idx], X.loc[test_idx]
    y_train, y_test = y.loc[train_idx], y.loc[test_idx]
    cv_splits = _grouped_cv_splits(cases.loc[train_idx], n_splits)

    search = RandomizedSearchCV(
        RandomForestClassifier(class_weight="balanced", random_state=random_state),
        param_distributions=RANDOM_FOREST_PARAM_SPACE,
        n_iter=n_iter,
        cv=cv_splits,
        scoring="roc_auc",
        random_state=random_state,
        n_jobs=-1,
    )
    search.fit(X_train, y_train)

    best_model = search.best_estimator_
    evaluation = _evaluate(best_model, X_test, y_test)

    if log_to_mlflow:
        _mlflow_log_run(
            "random_forest_tuned",
            params=search.best_params_,
            metrics={"cv_best_roc_auc": search.best_score_, "test_roc_auc": evaluation["roc_auc"]},
        )

    return {
        "model_name": "random_forest_tuned",
        "best_params": search.best_params_,
        "cv_best_roc_auc": round(float(search.best_score_), 4),
        **evaluation,
        "model": best_model,
    }


def tune_gradient_boosting(
    X: pd.DataFrame, y: pd.Series, cases: pd.DataFrame,
    n_iter: int = 20, n_splits: int = 5, random_state: int = 42,
    log_to_mlflow: bool = True,
) -> dict:
    train_idx, test_idx = time_based_split(cases)
    X_train, X_test = X.loc[train_idx], X.loc[test_idx]
    y_train, y_test = y.loc[train_idx], y.loc[test_idx]
    cv_splits = _grouped_cv_splits(cases.loc[train_idx], n_splits)

    # GradientBoostingClassifier doesn't support class_weight -- sample_weight is
    # the equivalent, same approach train.py's own train_models() already uses.
    class_counts = y_train.value_counts()
    sample_weight = y_train.map({
        cls: len(y_train) / (2 * count) for cls, count in class_counts.items()
    })

    search = RandomizedSearchCV(
        GradientBoostingClassifier(random_state=random_state),
        param_distributions=GRADIENT_BOOSTING_PARAM_SPACE,
        n_iter=n_iter,
        cv=cv_splits,
        scoring="roc_auc",
        random_state=random_state,
        n_jobs=-1,
    )
    search.fit(X_train, y_train, sample_weight=sample_weight)

    best_model = search.best_estimator_
    evaluation = _evaluate(best_model, X_test, y_test)

    if log_to_mlflow:
        _mlflow_log_run(
            "gradient_boosting_tuned",
            params=search.best_params_,
            metrics={"cv_best_roc_auc": search.best_score_, "test_roc_auc": evaluation["roc_auc"]},
        )

    return {
        "model_name": "gradient_boosting_tuned",
        "best_params": search.best_params_,
        "cv_best_roc_auc": round(float(search.best_score_), 4),
        **evaluation,
        "model": best_model,
    }
