"""
These use FastAPI's TestClient with the real routes, but stub out the DB-loading
functions rather than hitting a real Postgres instance -- keeps the test suite
runnable without a database, which matters for CI (Phase 9).
"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from src.api.main import app

client = TestClient(app, headers={"X-API-Key": "test-key-do-not-use-in-production"})


def sample_cases():
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


def test_health_endpoint_responds():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@patch("src.api.routes.load_cases")
def test_cycle_time_endpoint(mock_load_cases):
    mock_load_cases.return_value = sample_cases()
    response = client.get("/metrics/cycle-time")
    assert response.status_code == 200
    body = response.json()
    assert body["case_count"] == 3
    assert body["mean_hours"] > 0


@patch("src.api.routes.load_cases")
def test_cycle_time_endpoint_404_on_empty(mock_load_cases):
    mock_load_cases.return_value = pd.DataFrame()
    response = client.get("/metrics/cycle-time")
    assert response.status_code == 404


@patch("src.api.routes.load_cases")
def test_sla_metrics_endpoint(mock_load_cases):
    mock_load_cases.return_value = sample_cases()
    response = client.get("/metrics/sla")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert response.json()[0]["case_count"] == 3


@patch("src.api.routes.load_cases")
def test_order_risk_endpoint_404_on_unknown_case(mock_load_cases):
    mock_load_cases.return_value = sample_cases()
    response = client.get("/orders/UNKNOWN/risk")
    assert response.status_code == 404


@patch("src.api.routes.explain_prediction_shap")
@patch("src.api.routes.load_model")
@patch("src.api.routes.load_cases")
def test_order_risk_explain_returns_explanation(mock_load_cases, mock_load_model, mock_explain):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from src.ml.features import build_features
    from src.analytics.sla_analysis import load_sla_targets, evaluate_sla

    cases = sample_cases()
    mock_load_cases.return_value = cases
    sla_targets = load_sla_targets()
    evaluated = evaluate_sla(cases, sla_targets)
    X, _ = build_features(evaluated)
    lr = LogisticRegression(max_iter=100)
    lr.fit(X, np.array([0, 1, 0]))
    mock_load_model.return_value = {"model": lr, "columns": X.columns.tolist()}
    mock_explain.return_value = [
        {"feature": "event_count", "shap_value": 0.12, "value": 4},
        {"feature": "variant_frequency", "shap_value": -0.05, "value": 2},
    ]

    response = client.get("/orders/C1/risk?explain=true")
    assert response.status_code == 200
    body = response.json()
    assert body["explanation"] is not None
    assert body["explanation"][0]["feature"] == "event_count"
    assert mock_explain.called


@patch("src.api.routes.load_model")
@patch("src.api.routes.load_cases")
def test_order_risk_no_explain_omits_explanation(mock_load_cases, mock_load_model):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from src.ml.features import build_features
    from src.analytics.sla_analysis import load_sla_targets, evaluate_sla

    cases = sample_cases()
    mock_load_cases.return_value = cases
    sla_targets = load_sla_targets()
    evaluated = evaluate_sla(cases, sla_targets)
    X, _ = build_features(evaluated)
    lr = LogisticRegression(max_iter=100)
    lr.fit(X, np.array([0, 1, 0]))
    mock_load_model.return_value = {"model": lr, "columns": X.columns.tolist()}

    response = client.get("/orders/C1/risk")
    assert response.status_code == 200
    assert response.json()["explanation"] is None


@patch("src.api.routes.pd.read_sql")
@patch("src.api.routes.get_engine")
def test_pipeline_runs_endpoint(mock_get_engine, mock_read_sql):
    mock_read_sql.return_value = pd.DataFrame({
        "run_id": [1],
        "started_at": pd.to_datetime(["2024-01-01 09:00:00"]),
        "finished_at": pd.to_datetime(["2024-01-01 09:05:00"]),
        "status": ["success"],
        "raw_row_count": [1000],
        "cleaned_row_count": [980],
        "case_count": [300],
        "error_message": [None],
    })
    response = client.get("/observability/pipeline-runs")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["status"] == "success"
    assert body[0]["run_id"] == 1


@patch("src.api.routes.pd.read_sql")
@patch("src.api.routes.get_engine")
def test_data_quality_endpoint_survives_empty_predictions_table(mock_get_engine, mock_read_sql):
    """Real crash, found live: analytics.sla_predictions had 0 rows in production, so
    'SELECT ... SUM(...)/COUNT(*) ...' returns SQL NULL, and predictions.high_risk_rate's
    fmt/warn_fn/fail_fn lambdas did float(None), an unhandled TypeError -- the whole endpoint
    returned overall='fail' with a raw exception message instead of reporting cleanly.
    Reproduces the exact 0-rows scenario and asserts it no longer crashes."""
    def fake_read_sql(query, engine, **kwargs):
        # Order matters: the high_risk_rate query also contains "COUNT" (COUNT(*) in its
        # denominator), so the more specific risk_level pattern must be checked first, or it
        # falls into the row_count branch below and the regression this test exists to catch
        # goes unexercised.
        if "risk_level = 'HIGH'" in query:
            return pd.DataFrame([[None]])  # predictions.high_risk_rate: 0/0 -> NULL
        if "sla_predictions" in query and "COUNT" in query:
            return pd.DataFrame([[0]])  # predictions.row_count
        if "MAX(timestamp)" in query:
            return pd.DataFrame([[pd.Timestamp("2019-04-30", tz="UTC")]])
        if "AVG(c.n)" in query:
            return pd.DataFrame([[3.0]])
        return pd.DataFrame([[100]])  # every other count/rate check

    mock_read_sql.side_effect = fake_read_sql
    response = client.get("/observability/data-quality")
    assert response.status_code == 200
    checks = {c["name"]: c for c in response.json()["checks"]}
    assert checks["predictions.high_risk_rate"]["status"] != "fail" or \
        "TypeError" not in str(checks["predictions.high_risk_rate"].get("detail", ""))
    assert checks["predictions.high_risk_rate"]["value"] == "n/a (no predictions yet)"
    assert checks["predictions.row_count"]["status"] == "fail"  # 0 rows really is a fail, honestly


def test_request_logging_middleware_adds_request_id_header():
    response = client.get("/health")
    assert "X-Request-ID" in response.headers


@patch("src.api.routes.load_events")
def test_conformance_endpoint_against_golden_dataset(mock_load_events):
    from src.ingestion.load_event_log import load_event_log
    from src.cleaning.clean_events import clean_events

    raw = load_event_log("data/golden/golden_raw_events.csv")
    cleaned, _ = clean_events(raw)
    mock_load_events.return_value = cleaned

    # the golden cases use textbook activity names, so pin the textbook sequence (production config uses BPI names)
    import src.analytics.conformance as conf
    textbook = "tests/fixtures/process_textbook.yaml"
    real = conf.load_expected_sequence
    with patch.object(conf, "load_expected_sequence", lambda path=None: real(textbook)):
        response = client.get("/metrics/conformance")
    assert response.status_code == 200
    body = response.json()
    # matches docs/data-correctness-audit.md's hand-derived answer exactly
    assert body["total_cases"] == 7
    assert body["conformant_cases"] == 4
    assert body["deviation_breakdown"]["repeated_activity"] == 1
    assert body["deviation_breakdown"]["skipped_step"] == 1
    assert body["deviation_breakdown"]["unexpected_activity"] == 1
    assert "out_of_order" not in body["deviation_breakdown"]  # Phase 27's fix, still holding


@patch("src.api.routes.load_events")
def test_conformance_endpoint_404_on_no_data(mock_load_events):
    mock_load_events.return_value = pd.DataFrame()
    response = client.get("/metrics/conformance")
    assert response.status_code == 404


@patch("src.api.routes.load_model")
@patch("src.api.routes.load_cases")
def test_sla_risk_distribution_endpoint_buckets_all_cases(mock_load_cases, mock_load_model):
    import numpy as np
    from src.ml.features import build_features
    from src.ml.train import train_models, save_best_model
    from src.ml.predict import load_model as real_load_model
    import tempfile, os as os_module

    rng = np.random.default_rng(3)
    n = 40
    synthetic = pd.DataFrame({
        "case_id": [f"D{i}" for i in range(n)],
        "event_count": rng.integers(2, 10, size=n),
        "category": rng.choice(["3-way match", "Consignment"], size=n),
        "supplier_id": rng.choice(["S1", "S2"], size=n),
        "variant_frequency": rng.integers(1, 4, size=n),
        "start_time": pd.date_range("2024-01-01", periods=n, freq="D"),
        "cycle_time_hours": rng.uniform(10, 300, size=n),
        "sla_breach": rng.random(n) < 0.3,
    })
    X, y = build_features(synthetic)
    results = train_models(X, y, synthetic)

    with tempfile.TemporaryDirectory() as tmpdir:
        model_path = os_module.path.join(tmpdir, "m.joblib")
        save_best_model(results, out_path=model_path)
        mock_load_model.return_value = real_load_model(model_path)

        mock_load_cases.return_value = synthetic.rename(columns={})  # already has needed columns
        response = client.get("/metrics/sla-risk-distribution")

    assert response.status_code == 200
    body = response.json()
    assert body["total_cases_scored"] == n
    total_bucketed = sum(b["case_count"] for b in body["buckets"])
    assert total_bucketed == n
    assert {b["risk_level"] for b in body["buckets"]} == {"LOW", "MEDIUM", "HIGH"}


@patch("src.api.routes.load_cases")
def test_sla_risk_distribution_endpoint_404_on_no_data(mock_load_cases):
    mock_load_cases.return_value = pd.DataFrame()
    response = client.get("/metrics/sla-risk-distribution")
    assert response.status_code == 404


def test_cors_preflight_allows_post_for_the_intervention_ledger():
    r = client.options("/interventions", headers={"Origin": "http://localhost:3000",
                                                  "Access-Control-Request-Method": "POST"})
    assert r.status_code == 200 and "POST" in r.headers.get("access-control-allow-methods", "")
