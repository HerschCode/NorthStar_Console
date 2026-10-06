"""
Phase 28: canonical business scenarios traced end-to-end through as much real code as
this environment allows -- real FastAPI routing, real Pydantic validation, real
analytics/ML functions, against the Phase 27 golden dataset (scenarios 1-2, where the
answer is independently known) or a purpose-built larger synthetic set (scenario 3,
where the golden set is too small to train/test meaningfully). The one thing NOT real
here is the database layer -- src.api.routes.load_cases/load_events are mocked to
return the golden/synthetic DataFrames directly rather than querying a live Postgres
instance, since none is available in this environment. Everything from that mock
boundary outward (route handler, response schema validation, the analytics/ML
functions themselves) is the genuine production code path.
"""
import pandas as pd
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from src.api.main import app
from src.ingestion.load_event_log import load_event_log
from src.cleaning.clean_events import clean_events
from src.transformation.build_process_cases import build_process_cases
from src.analytics.sla_analysis import load_sla_targets, evaluate_sla

client = TestClient(app, headers={"X-API-Key": "test-key-do-not-use-in-production"})


def _golden_cases():
    raw = load_event_log("data/golden/golden_raw_events.csv")
    cleaned, _ = clean_events(raw)
    cases = build_process_cases(cleaned)
    return evaluate_sla(cases, load_sla_targets())


def _golden_events():
    raw = load_event_log("data/golden/golden_raw_events.csv")
    cleaned, _ = clean_events(raw)
    return cleaned


# ---------------------------------------------------------------------------
# Scenario 1: "Identify the biggest procurement bottleneck."
# events -> analytics -> API -> (dashboard would read this next)
# ---------------------------------------------------------------------------

@patch("src.api.routes.load_events")
def test_scenario_1_identify_biggest_bottleneck(mock_load_events):
    mock_load_events.return_value = _golden_events()

    response = client.get("/metrics/bottlenecks?top_n=10")
    assert response.status_code == 200

    body = response.json()
    top = body[0]
    # matches docs/data-correctness-audit.md's hand-derived answer exactly
    assert top["stage"] == "Purchase Requisition -> Approval"
    assert top["avg_hours"] == pytest.approx(41.857, abs=0.1)
    assert top["case_count"] == 7


# ---------------------------------------------------------------------------
# Scenario 2: "Which supplier has the worst SLA performance?"
# Postgres -> supplier analytics -> API -> (dashboard would read this next)
# ---------------------------------------------------------------------------

@patch("src.api.routes.load_cases")
def test_scenario_2_worst_supplier_by_sla(mock_load_cases):
    mock_load_cases.return_value = _golden_cases()

    response = client.get("/suppliers/performance?min_volume=2")
    assert response.status_code == 200

    body = response.json()
    worst = max(body, key=lambda s: s["sla_breach_rate"] or 0)
    # matches docs/data-correctness-audit.md: S3 is worst by both metrics, unambiguously
    assert worst["supplier_id"] == "S3"
    assert worst["sla_breach_rate"] == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# Scenario 3: "Is this order likely to breach SLA?"
# order -> features -> ML model -> prediction -> explanation -> API
#
# The golden 7-case set is intentionally too small to train/test meaningfully (a
# time-based split needs real volume) -- this scenario uses a separate, larger
# synthetic set purpose-built for training, not the hand-verified golden set. The
# prediction target case is deliberately extreme (high event_count, matches the
# pattern the synthetic labels were generated from) so the assertion is about the
# scenario's shape (risk correctly identified as elevated) rather than an exact
# probability, since a trained model's exact output isn't something to hand-derive
# the way Phase 27's deterministic analytics are.
# ---------------------------------------------------------------------------

def _train_scenario_model(tmp_path):
    import numpy as np
    from src.ml.features import build_features
    from src.ml.train import train_models, save_best_model

    rng = np.random.default_rng(7)
    n = 100
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
    model_path = tmp_path / "scenario_model.joblib"
    save_best_model(results, out_path=model_path)
    return model_path, synthetic


@patch("src.api.routes.load_model")
@patch("src.api.routes.load_cases")
def test_scenario_3_predict_sla_risk_for_a_specific_order(mock_load_cases, mock_load_model, tmp_path):
    from src.ml.predict import load_model as real_load_model

    model_path, synthetic = _train_scenario_model(tmp_path)
    mock_load_model.return_value = real_load_model(model_path)

    # a case that looks like the high-risk pattern the synthetic labels were built from
    high_risk_case = pd.DataFrame({
        "case_id": ["SCENARIO_CASE"], "event_count": [11], "category": ["3-way match"],
        "supplier_id": ["S1"], "variant_frequency": [1],
        "start_time": pd.to_datetime(["2024-06-01"]), "end_time": pd.to_datetime(["2024-06-15"]),
        "cycle_time_hours": [336.0],
    })
    mock_load_cases.return_value = high_risk_case

    response = client.get("/orders/SCENARIO_CASE/risk")
    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == "SCENARIO_CASE"
    assert 0.0 <= body["breach_probability"] <= 1.0
    assert body["risk_level"] in {"LOW", "MEDIUM", "HIGH"}
    # the scenario's actual claim: a case built from the highest-risk end of the
    # synthetic feature distribution should score above the lowest band, not that
    # it hits any exact number
    assert body["risk_level"] in {"MEDIUM", "HIGH"}
