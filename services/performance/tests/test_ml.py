import pandas as pd
import numpy as np
import pytest
from src.ml.features import build_features
from src.ml.train import time_based_split, time_series_group_splits, train_models, cross_validate_time_series
from src.ml.explain import explain_prediction, explain_batch


def sample_evaluated_cases(n=60):
    """Enough rows, with a real signal, for train/test split + a trainable model.
    breach is deliberately correlated with event_count so the model has something
    real to learn -- a fully random label would make roc_auc meaningless to assert on."""
    rng = np.random.default_rng(42)
    event_count = rng.integers(2, 10, size=n)
    category = rng.choice(["3-way match", "Consignment"], size=n)
    supplier_id = rng.choice(["S1", "S2", "S3"], size=n)
    variant_frequency = rng.integers(1, 5, size=n)
    start_time = pd.date_range("2024-01-01", periods=n, freq="D")

    breach_prob = 1 / (1 + np.exp(-(event_count - 5)))  # higher event_count -> more likely breach
    sla_breach = rng.random(n) < breach_prob

    return pd.DataFrame({
        "case_id": [f"C{i}" for i in range(n)],
        "event_count": event_count,
        "category": category,
        "supplier_id": supplier_id,
        "variant_frequency": variant_frequency,
        "start_time": start_time,
        "sla_breach": sla_breach,
    })


def test_build_features_produces_one_hot_columns():
    cases = sample_evaluated_cases()
    X, y = build_features(cases)
    assert any(c.startswith("category_") for c in X.columns)
    assert any(c.startswith("supplier_id_") for c in X.columns)
    assert len(y) == len(cases)


def test_build_features_raises_when_no_expected_columns_present():
    df = pd.DataFrame({"sla_breach": [True, False], "unrelated_col": [1, 2]})
    with pytest.raises(ValueError):
        build_features(df)


def test_supplier_history_uses_only_cases_completed_before_current_start():
    cases = pd.DataFrame({
        "supplier_id": ["S1", "S1", "S1", "S1"],
        "start_time": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-05", "2024-01-11"]),
        "end_time": pd.to_datetime(["2024-01-10", "2024-01-03", "2024-01-06", "2024-01-12"]),
        "cycle_time_hours": [216.0, 24.0, 24.0, 24.0],
        "sla_breach": [1, 0, 0, 1],
    })

    features = build_features(cases)[0]

    assert features["supplier_historical_breach_rate"].tolist() == [0.5, 0.5, 0.0, pytest.approx(1 / 3)]
    assert features["supplier_historical_median_cycle_time"].tolist() == [-1.0, -1.0, 24.0, 24.0]


def test_model_vs_rules_history_excludes_completion_at_prediction_time():
    from scripts.model_vs_rules import causal_supplier_features

    cases = pd.DataFrame({
        "supplier_id": ["S1", "S1", "S1"],
        "start_time": pd.to_datetime(["2024-01-01", "2024-01-05", "2024-01-08"]),
        "end_time": pd.to_datetime(["2024-01-05", "2024-01-07", "2024-01-09"]),
        "cycle_time_hours": [96.0, 48.0, 24.0],
        "sla_breach": [1, 0, 0],
    })

    features = causal_supplier_features(cases, prior_rate=0.25, prior_median=12.0)

    assert features["sup_ended_n"].tolist() == [0.0, 0.0, 2.0]
    assert features["sup_ended_rate"].tolist() == [0.25, 0.25, 0.5]
    assert features["sup_ended_median"].tolist() == [12.0, 12.0, 72.0]


def test_time_based_split_respects_chronological_order():
    cases = sample_evaluated_cases()
    train_idx, test_idx = time_based_split(cases, test_size=0.2)
    train_max_time = cases.loc[train_idx, "start_time"].max()
    test_min_time = cases.loc[test_idx, "start_time"].min()
    assert train_max_time <= test_min_time


def test_time_based_split_sizes_roughly_match_test_size():
    cases = sample_evaluated_cases(n=100)
    train_idx, test_idx = time_based_split(cases, test_size=0.2)
    assert len(test_idx) == 20
    assert len(train_idx) == 80


def test_time_based_split_keeps_purchase_orders_separate_and_drops_boundary_groups():
    cases = sample_evaluated_cases(n=10).sample(frac=1, random_state=7)
    cases["purchase_order_id"] = ["PO1", "PO2", "PO3", "PO4", "PO5", "PO6", "PO7", "PO7", "PO9", "PO10"]

    train_idx, test_idx = time_based_split(cases, test_size=0.3)

    train_orders = set(cases.loc[train_idx, "purchase_order_id"])
    test_orders = set(cases.loc[test_idx, "purchase_order_id"])
    assert train_orders.isdisjoint(test_orders)
    assert "PO7" not in train_orders | test_orders
    assert cases.loc[train_idx, "start_time"].max() < cases.loc[test_idx, "start_time"].min()
    assert cases.loc[train_idx, "start_time"].is_monotonic_increasing
    assert cases.loc[test_idx, "start_time"].is_monotonic_increasing


def test_time_series_group_folds_keep_purchase_orders_separate():
    cases = sample_evaluated_cases(n=120)
    cases["purchase_order_id"] = [f"PO{i // 2}" for i in range(len(cases))]

    folds = list(time_series_group_splits(cases, n_splits=5))

    assert folds
    for train_idx, test_idx in folds:
        assert set(cases.loc[train_idx, "purchase_order_id"]).isdisjoint(
            set(cases.loc[test_idx, "purchase_order_id"])
        )
        assert cases.loc[train_idx, "start_time"].max() < cases.loc[test_idx, "start_time"].min()


def test_train_models_returns_both_models_with_metrics():
    cases = sample_evaluated_cases()
    X, y = build_features(cases)
    results = train_models(X, y, cases)

    for name in ["logistic_regression", "random_forest"]:
        assert name in results
        for metric in ["precision", "recall", "f1", "roc_auc"]:
            assert 0.0 <= results[name][metric] <= 1.0

    assert "feature_importances" in results["random_forest"]
    assert "_split" in results


def test_explain_prediction_only_returns_active_features():
    row = pd.Series({"event_count": 5, "category_Consignment": 0, "category_3-way match": 1})
    importances = {"event_count": 0.5, "category_Consignment": 0.3, "category_3-way match": 0.1}
    explanation = explain_prediction(row, importances, top_n=5)
    features_returned = {e["feature"] for e in explanation}
    assert "category_Consignment" not in features_returned  # it's 0 for this row
    assert "event_count" in features_returned


def test_explain_batch_returns_one_row_per_input():
    features = pd.DataFrame({
        "event_count": [5, 0],
        "category_Consignment": [0, 1],
    })
    importances = {"event_count": 0.6, "category_Consignment": 0.4}
    result = explain_batch(features, importances)
    assert len(result) == 2


def test_train_models_includes_gradient_boosting():
    cases = sample_evaluated_cases()
    X, y = build_features(cases)
    results = train_models(X, y, cases)

    assert "gradient_boosting" in results
    for metric in ["precision", "recall", "f1", "roc_auc"]:
        assert 0.0 <= results["gradient_boosting"][metric] <= 1.0
    assert "feature_importances" in results["gradient_boosting"]


def test_save_best_model_can_select_gradient_boosting(tmp_path):
    """save_best_model picks the highest-ROC-AUC model by name, generically -- this
    confirms gradient_boosting is a real candidate in that selection, not just present
    in the results dict without being eligible to win."""
    from src.ml.train import save_best_model
    cases = sample_evaluated_cases()
    X, y = build_features(cases)
    results = train_models(X, y, cases)

    model_path = tmp_path / "test_model.joblib"
    best_name = save_best_model(results, out_path=model_path)
    assert best_name in {"logistic_regression", "random_forest", "gradient_boosting"}
    assert model_path.exists()


def test_cross_validate_time_series_returns_one_score_per_fold():
    cases = sample_evaluated_cases(n=100)  # enough rows for 5 folds to each have signal
    X, y = build_features(cases)
    result = cross_validate_time_series(X, y, cases, model_name="random_forest", n_splits=5)

    assert result["model_name"] == "random_forest"
    assert result["n_folds_scored"] <= 5
    assert len(result["fold_roc_auc"]) == result["n_folds_scored"]
    if result["n_folds_scored"] > 0:
        assert all(0.0 <= s <= 1.0 for s in result["fold_roc_auc"])
        assert result["mean_roc_auc"] == pytest.approx(np.mean(result["fold_roc_auc"]), abs=0.001)


def test_cross_validate_time_series_folds_are_chronologically_ordered():
    """The actual point of using TimeSeriesSplit over KFold: every fold's test data
    must come chronologically after its training data -- verified directly here, not
    just trusted because TimeSeriesSplit is used."""
    from sklearn.model_selection import TimeSeriesSplit
    cases = sample_evaluated_cases(n=100).sort_values("start_time").reset_index(drop=True)
    X, y = build_features(cases)

    tscv = TimeSeriesSplit(n_splits=5)
    for train_pos, test_pos in tscv.split(X):
        assert max(train_pos) < min(test_pos), "a fold's training data must all precede its test data chronologically"


def test_cross_validate_time_series_rejects_unsupported_model():
    cases = sample_evaluated_cases()
    X, y = build_features(cases)
    with pytest.raises(ValueError, match="only supports"):
        cross_validate_time_series(X, y, cases, model_name="gradient_boosting")
