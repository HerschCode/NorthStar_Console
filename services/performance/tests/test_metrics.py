"""Tests for the Prometheus /metrics scrape endpoint and the instrumentation
that feeds it (src/api/metrics.py, wired in src/api/middleware.py)."""
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from sklearn.linear_model import LogisticRegression

from src.api.main import app
from src.api.metrics import PREDICTIONS_TOTAL
from src.ml.features import build_features
from src.analytics.sla_analysis import load_sla_targets, evaluate_sla

client = TestClient(app, headers={"X-API-Key": "test-key-do-not-use-in-production"})


def _sample_cases():
    return pd.DataFrame({
        "case_id": ["C1", "C2", "C3"],
        "cycle_time_hours": [48.0, 96.0, 200.0],
        "category": ["3-way match", "3-way match", "Consignment"],
        "supplier_id": ["S1", "S1", "S2"],
        "event_count": [4, 5, 6],
        "variant_frequency": [2, 2, 1],
        "start_time": pd.to_datetime(["2024-01-01", "2024-01-05", "2024-02-01"]),
        "end_time": pd.to_datetime(["2024-01-03", "2024-01-09", "2024-02-10"]),
    })


def test_metrics_endpoint_is_unauthenticated_and_returns_prometheus_format():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    # Prometheus text exposition format: metrics start with '# HELP'/'# TYPE'
    # comment lines before the sample lines.
    assert "# HELP" in response.text
    assert "# TYPE" in response.text


def test_metrics_endpoint_reflects_request_count_after_a_request():
    client.get("/health")
    after = client.get("/metrics").text
    assert "http_requests_total" in after
    # A /health hit should be counted under its route template, not blow up
    # into one label per distinct URL.
    assert 'path="/health"' in after


@patch("src.api.routes.load_model")
@patch("src.api.routes.load_cases")
def test_prediction_endpoint_increments_predictions_total(mock_load_cases, mock_load_model):
    cases = _sample_cases()
    mock_load_cases.return_value = cases
    evaluated = evaluate_sla(cases, load_sla_targets())
    X, _ = build_features(evaluated)
    lr = LogisticRegression(max_iter=100)
    lr.fit(X, np.array([0, 1, 0]))
    mock_load_model.return_value = {"model": lr, "columns": X.columns.tolist()}

    response = client.get("/orders/C1/risk")
    assert response.status_code == 200
    risk_level = response.json()["risk_level"]

    before = PREDICTIONS_TOTAL.labels(risk_level=risk_level)._value.get()
    client.get("/orders/C1/risk")  # a second real call
    after = PREDICTIONS_TOTAL.labels(risk_level=risk_level)._value.get()
    assert after == before + 1  # exactly one prediction counted for this call
