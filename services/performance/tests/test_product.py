import json

import pandas as pd
from fastapi.testclient import TestClient

from src.api import app_routes
from src.api.main import app
from src.api.product import _monthly_trend, _process_flow, _stage_share

client = TestClient(app)


def test_monthly_trend_drops_thin_months_and_computes_rates():
    ends = pd.to_datetime(["2018-01-15"] * 40 + ["2018-02-10"] * 5, utc=True)
    d = pd.DataFrame({"end_time": ends, "sla_breach": [True] * 10 + [False] * 30 + [True] * 5,
                      "cycle_time_hours": [48.0] * 45})
    t = _monthly_trend(d)
    assert [r["month"] for r in t] == ["2018-01"] and t[0]["breach_rate"] == 25.0


def test_process_flow_builds_edges_with_wait_times_and_breach_rate():
    ts = pd.to_datetime(["2018-01-01 00:00", "2018-01-01 10:00", "2018-01-02 10:00"] * 2, utc=True)
    ev = pd.DataFrame({"case_id": ["a"] * 3 + ["b"] * 3, "activity": ["Create", "Approve", "Receive"] * 2, "timestamp": ts})
    flow = _process_flow(ev, pd.Series({"a": 1.0, "b": 0.0}))
    e = next(x for x in flow["edges"] if x["from"] == "Create" and x["to"] == "Approve")
    assert e["median_hours"] == 10.0 and e["cases"] == 2 and e["breach_rate"] == 50.0
    assert {n["id"] for n in flow["nodes"]} == {"Create", "Approve", "Receive"}


def test_stage_share_ignores_rare_transitions():
    b = pd.DataFrame({"stage": ["rare", "common"], "avg_hours": [9000.0, 10.0], "median_hours": [9000.0, 9.0],
                      "p90_hours": [9500.0, 20.0], "case_count": [3, 500], "pct_of_total_delay": [5.0, 1.0]})
    assert [r["stage"] for r in _stage_share(b)] == ["common"]


def test_app_page_is_served_and_has_every_nav_target():
    r = client.get("/app")
    assert r.status_code == 200 and "Northstar Procurement Intelligence" in r.text
    for target in ("queue", "suppliers", "process", "finance", "interventions", "governance", "models", "data", "evidence"):
        assert f"'{target}'" in r.text


def test_app_data_falls_back_to_the_committed_snapshot_when_the_database_is_down(monkeypatch):
    from src.api import db

    def boom():
        raise RuntimeError("quota exceeded")
    monkeypatch.setattr(db, "load_cases", boom)
    monkeypatch.setattr(app_routes, "_cache", {"data": None, "at": 0.0})
    body = client.get("/app/data").json()
    assert body["snapshot"]["live"] is False and "quota exceeded" in body["snapshot"]["reason"]
    assert body["kpis"]["cases"] > 0


def test_committed_snapshot_is_complete_and_internally_consistent():
    snap = app_routes.load_product_snapshot()
    assert snap is not None
    for key in ("kpis", "trend", "funnel", "queue", "po", "suppliers", "supplier_detail", "process", "finance",
                "models", "data_quality", "evidence", "governance", "intervention_config", "scatter"):
        assert snap.get(key) not in (None, [], {}), key
    assert all(q["case_id"] in snap["po"] for q in snap["queue"])
    assert all(p["timeline"] and p["shap"] for p in snap["po"].values())
    assert snap["funnel"][-1]["n"] == 0                       # no real interventions are claimed
    assert snap["intervention_config"]["label"].startswith("SIMULATION")
    assert any(c["status"] == "not_valid" for c in snap["finance"]["control_health"])   # Benford is shown as not valid
    json.dumps(snap)
