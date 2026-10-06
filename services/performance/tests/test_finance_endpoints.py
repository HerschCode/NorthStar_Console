import json
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app, headers={"X-API-Key": "test-key-do-not-use-in-production"})


def _cases():
    return pd.DataFrame({"case_id": ["A", "B"], "supplier_id": ["S1", "S2"], "category": ["3-way match", "Consignment"]})


def _exceptions_df():
    return pd.DataFrame({
        "exception_id": [1, 2], "case_id": ["A", "B"], "control_id": ["C4_possible_duplicate_invoice", "C6_benford_deviation"],
        "severity": ["medium", "low"], "exposure_eur": [500.0, None],
        "evidence": [json.dumps({"supplier_id": "S1"}), json.dumps({"supplier_id": "S2"})],
        "generated_at": ["2026-01-01", "2026-01-01"],
    })


@patch("src.api.routes.load_cases")
@patch("src.api.routes.pd.read_sql")
def test_controls_exceptions_returns_rows_with_parsed_evidence(mock_sql, mock_cases):
    mock_sql.return_value = _exceptions_df()
    mock_cases.return_value = _cases()
    r = client.get("/controls/exceptions")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2 and isinstance(body[0]["evidence"], dict)


@patch("src.api.routes.pd.read_sql")
def test_controls_summary_aggregates_by_control(mock_sql):
    mock_sql.return_value = pd.DataFrame({
        "control_id": ["C1_three_way_match", "C1_three_way_match", "C6_benford_deviation"],
        "severity": ["high", "high", "low"], "exposure_eur": [100.0, 200.0, None],
    })
    r = client.get("/controls/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_exceptions"] == 3
    c1 = next(c for c in body["controls"] if c["control_id"] == "C1_three_way_match")
    assert c1["count"] == 2 and c1["total_exposure_eur"] == 300.0
    assert "not confirmed fraud" in body["label"]


@patch("src.api.routes.pd.read_sql")
def test_controls_summary_handles_no_exceptions_yet(mock_sql):
    mock_sql.return_value = pd.DataFrame(columns=["control_id", "severity", "exposure_eur"])
    r = client.get("/controls/summary")
    assert r.status_code == 200 and r.json()["total_exceptions"] == 0


@patch("src.api.routes.load_events")
def test_working_capital_summary_returns_assumptions(mock_events):
    mock_events.return_value = pd.DataFrame({
        "case_id": ["A", "A", "B", "B"],
        "activity": ["Vendor creates invoice", "Clear Invoice", "Vendor creates invoice", "Clear Invoice"],
        "timestamp": pd.to_datetime(["2024-01-01", "2024-01-05", "2024-01-01", "2024-02-01"]),
        "supplier_id": ["S1", "S1", "S2", "S2"], "net_worth_eur": [1000.0, 1000.0, 2000.0, 2000.0],
    })
    r = client.get("/working-capital/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["assumptions"]["payment_terms_days"] == 30
    assert body["late_payment"]["n_cases"] == 2


@patch("src.api.routes.load_events")
def test_working_capital_summary_404s_on_no_data(mock_events):
    mock_events.return_value = pd.DataFrame()
    assert client.get("/working-capital/summary").status_code == 404
