"""
Real RandomizedSearchCV runs against synthetic data (same pattern as
tests/test_e2e_scenarios.py's _train_scenario_model) -- nothing about sklearn's
search itself is mocked. MLflow logging is mocked (log_to_mlflow=False in most
tests, or the log function patched) since these tests shouldn't depend on
filesystem writes to ./mlruns succeeding in CI.
"""
import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

from src.ml.features import build_features
from src.ml.tune import tune_random_forest, tune_gradient_boosting


@pytest.fixture(scope="module")
def synthetic_data():
    rng = np.random.default_rng(21)
    n = 200
    event_count = rng.integers(2, 12, size=n)
    category = rng.choice(["3-way match", "Consignment"], size=n)
    supplier_id = rng.choice(["S1", "S2", "S3"], size=n)
    variant_frequency = rng.integers(1, 5, size=n)
    start_time = pd.date_range("2024-01-01", periods=n, freq="D")
    breach_prob = 1 / (1 + np.exp(-(event_count - 6)))
    sla_breach = rng.random(n) < breach_prob

    cases = pd.DataFrame({
        "case_id": [f"SYN{i}" for i in range(n)],
        "purchase_order_id": [f"PO{i // 2}" for i in range(n)],
        "event_count": event_count, "category": category, "supplier_id": supplier_id,
        "variant_frequency": variant_frequency, "start_time": start_time, "sla_breach": sla_breach,
    })
    X, y = build_features(cases)
    return X, y, cases


def test_tune_random_forest_returns_a_real_fitted_model(synthetic_data):
    X, y, cases = synthetic_data
    result = tune_random_forest(X, y, cases, n_iter=4, n_splits=3, log_to_mlflow=False)

    assert 0.0 <= result["roc_auc"] <= 1.0
    assert "n_estimators" in result["best_params"]
    # the returned model is actually fitted and can predict, not just a config dict
    assert result["model"].predict(X.iloc[:5]).shape == (5,)


def test_tune_gradient_boosting_returns_a_real_fitted_model(synthetic_data):
    X, y, cases = synthetic_data
    result = tune_gradient_boosting(X, y, cases, n_iter=4, n_splits=3, log_to_mlflow=False)

    assert 0.0 <= result["roc_auc"] <= 1.0
    assert "learning_rate" in result["best_params"]
    assert result["model"].predict(X.iloc[:5]).shape == (5,)


@patch("src.ml.tune._mlflow_log_run")
def test_tune_random_forest_logs_to_mlflow_when_enabled(mock_log, synthetic_data):
    X, y, cases = synthetic_data
    tune_random_forest(X, y, cases, n_iter=3, n_splits=3, log_to_mlflow=True)

    mock_log.assert_called_once()
    call_kwargs = mock_log.call_args
    assert "cv_best_roc_auc" in call_kwargs.kwargs["metrics"]
    assert "test_roc_auc" in call_kwargs.kwargs["metrics"]


@patch("src.ml.tune._mlflow_log_run")
def test_tune_does_not_log_when_disabled(mock_log, synthetic_data):
    X, y, cases = synthetic_data
    tune_random_forest(X, y, cases, n_iter=3, n_splits=3, log_to_mlflow=False)

    mock_log.assert_not_called()


def test_tuned_random_forest_uses_purchase_order_grouped_temporal_folds(synthetic_data):
    """Verify tuning uses the shared fold builder and requested fold count."""
    X, y, cases = synthetic_data

    with patch("src.ml.tune.time_series_group_splits", wraps=__import__(
        "src.ml.train", fromlist=["time_series_group_splits"]
    ).time_series_group_splits) as mock_group_splits:
        tune_random_forest(X, y, cases, n_iter=2, n_splits=3, log_to_mlflow=False)

    mock_group_splits.assert_called_once()
    assert mock_group_splits.call_args.kwargs["n_splits"] == 3
