"""
Trains a real model on synthetic data (same pattern as
tests/test_e2e_scenarios.py::_train_scenario_model) and runs REAL shap.Explainer
against it -- no mocking of shap itself, since the entire point is verifying the
real library behaves correctly against this project's actual feature shape and
model bundle format.
"""
import numpy as np
import pandas as pd
import pytest

from src.ml.features import build_features
from src.ml.train import train_models, save_best_model
from src.ml.predict import load_model
from src.ml.explain_shap import explain_prediction_shap, explain_batch_shap


@pytest.fixture(scope="module")
def trained_bundle(tmp_path_factory):
    rng = np.random.default_rng(11)
    n = 150
    event_count = rng.integers(2, 12, size=n)
    category = rng.choice(["3-way match", "Consignment"], size=n)
    supplier_id = rng.choice(["S1", "S2", "S3"], size=n)
    variant_frequency = rng.integers(1, 5, size=n)
    start_time = pd.date_range("2024-01-01", periods=n, freq="D")
    breach_prob = 1 / (1 + np.exp(-(event_count - 6)))
    sla_breach = rng.random(n) < breach_prob

    synthetic = pd.DataFrame({
        "case_id": [f"SYN{i}" for i in range(n)],
        "event_count": event_count, "category": category, "supplier_id": supplier_id,
        "variant_frequency": variant_frequency, "start_time": start_time, "sla_breach": sla_breach,
    })

    X, y = build_features(synthetic)
    results = train_models(X, y, synthetic)
    model_path = tmp_path_factory.mktemp("model") / "explain_test_model.joblib"
    save_best_model(results, out_path=model_path)
    bundle = load_model(model_path)
    return bundle, X


def test_explain_prediction_shap_returns_real_values_per_feature(trained_bundle):
    bundle, X = trained_bundle
    model = bundle["model"]
    background = X.sample(n=30, random_state=1)
    single_row = X.iloc[[0]]

    explanation = explain_prediction_shap(model, background, single_row, top_n=3)

    assert len(explanation) == 3
    for item in explanation:
        assert "feature" in item and "shap_value" in item and "value" in item
        assert isinstance(item["shap_value"], float)


def test_explain_prediction_shap_ranks_by_absolute_magnitude(trained_bundle):
    bundle, X = trained_bundle
    model = bundle["model"]
    background = X.sample(n=30, random_state=1)
    single_row = X.iloc[[0]]

    explanation = explain_prediction_shap(model, background, single_row, top_n=len(X.columns))
    magnitudes = [abs(item["shap_value"]) for item in explanation]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_explain_batch_shap_returns_one_row_per_case(trained_bundle):
    bundle, X = trained_bundle
    model = bundle["model"]
    background = X.sample(n=30, random_state=1)
    sample_cases = X.iloc[:5]

    result = explain_batch_shap(model, background, sample_cases, top_n=3)

    assert len(result) == 5
    for explanation in result["top_factors_shap"]:
        assert len(explanation) == 3


def test_explain_batch_shap_matches_single_prediction_explanation(trained_bundle):
    """The batch path builds one explainer and reuses it across rows (for
    efficiency) rather than explain_prediction_shap's per-call explainer -- this
    confirms that optimization doesn't change the actual answer for a given row."""
    bundle, X = trained_bundle
    model = bundle["model"]
    background = X.sample(n=30, random_state=1)
    single_row = X.iloc[[0]]

    single = explain_prediction_shap(model, background, single_row, top_n=3)
    batch = explain_batch_shap(model, background, single_row, top_n=3)["top_factors_shap"].iloc[0]

    single_features = [item["feature"] for item in single]
    batch_features = [item["feature"] for item in batch]
    assert single_features == batch_features
