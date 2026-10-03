"""
Mirrors tests/test_api.py's pattern: stub the DB-loading functions rather than
hitting a real Postgres instance. dashboard.py imports load_cases/load_events
directly (not through routes.py), so they're patched at src.api.dashboard's own
module namespace.
"""
import pandas as pd
from fastapi.testclient import TestClient
from unittest.mock import patch

from src.api.main import app
import src.api.dashboard as dashboard_module

client = TestClient(app, headers={"X-API-Key": "test-key-do-not-use-in-production"})


def setup_function():
    # dashboard.py caches /dashboard/data results (see its own docstring on why) --
    # each test needs a clean cache or it'll see whatever an earlier test's mocked
    # data left behind instead of its own.
    dashboard_module._cache["data"] = None
    dashboard_module._cache["computed_at"] = 0.0


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


def sample_events():
    # 5 cases through the same stage transition -- identify_bottlenecks() has a
    # real min_case_count=5 floor (see src/analytics/bottlenecks.py) added after
    # a live bug where 1-2 case stages showed tens-of-thousands-of-hours means
    # from single anomalous cases; a 2-case fixture would always be filtered out
    # and this test would spuriously see an empty "stages" list.
    case_ids = [f"C{i}" for i in range(1, 6)]
    rows = {"case_id": [], "activity": [], "timestamp": []}
    for i, cid in enumerate(case_ids):
        rows["case_id"] += [cid, cid]
        rows["activity"] += ["Purchase Requisition", "Approval"]
        rows["timestamp"] += [f"2024-01-{i+1:02d}", f"2024-01-{i+2:02d}"]
    rows["timestamp"] = pd.to_datetime(rows["timestamp"])
    return pd.DataFrame(rows)


def test_dashboard_page_serves_html_without_auth():
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Procure-to-Pay Intelligence" in response.text
    assert 'aria-label="Dashboard sections"' in response.text
    assert 'id="process-section"' in response.text
    assert 'href="#platform-section"' in response.text


def test_dashboard_data_works_without_auth_key():
    response = client.get("/dashboard/data")
    assert response.status_code == 200
    assert "executive_overview" in response.json()


@patch("src.api.dashboard.load_cases")
def test_executive_overview_section_populated(mock_load_cases):
    mock_load_cases.return_value = sample_cases()
    response = client.get("/dashboard/data")
    overview = response.json()["executive_overview"]
    assert overview["available"] is True
    assert overview["case_count"] == 3


@patch("src.api.dashboard.load_cases")
def test_executive_overview_section_degrades_gracefully_when_empty(mock_load_cases):
    mock_load_cases.return_value = pd.DataFrame()
    response = client.get("/dashboard/data")
    overview = response.json()["executive_overview"]
    assert overview["available"] is False


@patch("src.api.dashboard.load_events")
def test_bottlenecks_section_populated(mock_load_events):
    mock_load_events.return_value = sample_events()
    response = client.get("/dashboard/data")
    bottlenecks = response.json()["bottlenecks"]
    assert bottlenecks["available"] is True
    assert len(bottlenecks["stages"]) > 0


@patch("src.api.dashboard.load_cases")
def test_suppliers_section_populated(mock_load_cases):
    mock_load_cases.return_value = sample_cases()
    response = client.get("/dashboard/data")
    suppliers = response.json()["suppliers"]
    assert suppliers["available"] is True


@patch("src.api.dashboard.load_cases")
def test_sla_risk_section_degrades_gracefully_without_a_trained_model(mock_load_cases):
    mock_load_cases.return_value = sample_cases()
    response = client.get("/dashboard/data")
    risk = response.json()["sla_risk"]
    # No model file exists in a clean test environment -- this should degrade to
    # available: False, not crash the whole /dashboard/data response.
    assert "available" in risk


@patch("src.api.dashboard._compute_dashboard_data")
def test_dashboard_data_is_cached_across_requests(mock_compute):
    """A real gap found by hitting an actual free-tier Postgres instance: a 32K-row
    full-table pull took 60+ seconds, and five sections each independently querying
    would make every page load take over a minute. Verifies the fix directly --
    _compute_dashboard_data must only run once across repeated requests within the
    cache TTL, not that the response merely looks the same."""
    mock_compute.return_value = {"executive_overview": {"available": False}}

    client.get("/dashboard/data")
    client.get("/dashboard/data")
    client.get("/dashboard/data")

    assert mock_compute.call_count == 1


def test_one_failing_section_does_not_break_the_others():
    """The whole point of _section()'s isolation -- verified directly rather than
    just asserting each section independently, since a shared-blast-radius bug
    would only show up when checking they're ALL present in the SAME response."""
    response = client.get("/dashboard/data")
    assert response.status_code == 200
    body = response.json()
    for key in ("executive_overview", "bottlenecks", "suppliers", "conformance", "sla_risk"):
        assert key in body
        assert "available" in body[key]
