from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from src.api.main import app
from src.roi.ledger import (
    InterventionValidationError, break_even_effect, load_policy, roi_summary, validate_intervention)

POLICY = {"breach_cost": 400.0, "capacity_pct": 0.2, "default_type": "a",
          "types": {"a": {"cost": 25.0, "effect": 0.5}, "b": {"cost": 60.0, "effect": 0.1}}}
client = TestClient(app, headers={"X-API-Key": "test-key-do-not-use-in-production"})


def test_real_policy_file_loads_and_is_labelled_as_assumptions():
    p = load_policy()
    assert p["default_type"] in p["types"] and p["breach_cost"] > 0
    assert "SIMULATION" in roi_summary([], p)["label"]


@pytest.mark.parametrize("payload,msg", [
    ({"case_id": "C1", "intervention_type": "nope", "risk_at_intervention": 0.5}, "unknown intervention_type"),
    ({"case_id": " ", "risk_at_intervention": 0.5}, "case_id"),
    ({"case_id": "C1", "risk_at_intervention": 1.5}, "between 0 and 1"),
    ({"case_id": "C1", "risk_at_intervention": None}, "between 0 and 1"),
    ({"case_id": "C1", "risk_at_intervention": 0.5, "cost": -1}, "cost"),
])
def test_validation_rejects_bad_input(payload, msg):
    with pytest.raises(InterventionValidationError, match=msg):
        validate_intervention(payload, POLICY)


def test_validation_defaults_type_and_cost():
    row = validate_intervention({"case_id": "C1", "risk_at_intervention": 0.9}, POLICY)
    assert row["intervention_type"] == "a" and row["cost"] == 25.0


def test_roi_arithmetic_matches_hand_calculation():
    rows = [
        {"intervention_type": "a", "risk_at_intervention": 0.8, "cost": 25.0, "breached_after": True, "is_simulated": True},
        {"intervention_type": "a", "risk_at_intervention": 0.4, "cost": 25.0, "breached_after": False, "is_simulated": True},
        {"intervention_type": "b", "risk_at_intervention": 1.0, "cost": 60.0, "breached_after": None, "is_simulated": False},
    ]
    s = roi_summary(rows, POLICY)
    sim, real = s["simulated"], s["logged"]
    assert sim["interventions"] == 2 and real["interventions"] == 1     # kept separate
    assert sim["avoided_breaches_by_model_risk"] == pytest.approx((0.8 + 0.4) * 0.5)   # 0.6
    assert sim["net_value_by_model_risk"] == pytest.approx(0.6 * 400 - 50)             # 190
    assert sim["avoided_breaches_by_observed_outcomes"] == pytest.approx(0.5)          # one breached x 0.5
    assert real["outcomes_known"] == 0 and real["avoided_breaches_by_observed_outcomes"] == 0
    assert s["break_even_effect_simulated"] == pytest.approx(50 / (400 * 1.2), abs=1e-4)


def test_break_even_effect_none_without_risk():
    assert break_even_effect([], POLICY) is None


@contextmanager
def _engines(case_exists=True, insert_error=None):
    read, write = MagicMock(), MagicMock()
    read.connect.return_value.__enter__.return_value.execute.return_value.first.return_value = (1,) if case_exists else None
    conn = write.begin.return_value.__enter__.return_value
    if insert_error:
        conn.execute.side_effect = insert_error
    else:
        conn.execute.return_value.scalar.return_value = 7
    with patch("src.api.routes.get_engine", return_value=read), patch("src.api.routes.get_write_engine", return_value=write):
        yield


GOOD = {"case_id": "C1", "risk_at_intervention": 0.9}


def test_post_creates_intervention():
    with _engines():
        r = client.post("/interventions", json=GOOD)
    assert r.status_code == 201 and r.json()["intervention_id"] == 7 and r.json()["cost"] > 0


def test_post_validation_error_is_422():
    with _engines():
        assert client.post("/interventions", json={**GOOD, "intervention_type": "bogus"}).status_code == 422
        assert client.post("/interventions", json={**GOOD, "risk_at_intervention": 2}).status_code == 422


def test_post_unknown_case_is_404():
    with _engines(case_exists=False):
        assert client.post("/interventions", json=GOOD).status_code == 404


def test_post_duplicate_is_409():
    with _engines(insert_error=IntegrityError("x", {}, Exception("dup"))):
        assert client.post("/interventions", json=GOOD).status_code == 409


def test_roi_summary_endpoint_separates_simulated_and_logged():
    df = pd.DataFrame([
        {"intervention_type": "expedite_approval", "risk_at_intervention": 0.9, "cost": 25.0, "breached_after": True, "is_simulated": True},
        {"intervention_type": "expedite_approval", "risk_at_intervention": 0.7, "cost": 25.0, "breached_after": None, "is_simulated": False},
    ])
    with patch("src.api.routes.pd.read_sql", return_value=df), patch("src.api.routes.get_engine"):
        body = client.get("/roi/summary").json()
    assert body["simulated"]["interventions"] == 1 and body["logged"]["interventions"] == 1
    assert "SIMULATION" in body["label"] and "effect" in body["assumptions"]["types"]["expedite_approval"]


def test_load_sensitivity_returns_both_scenarios_and_trims_uncalibrated_grids(tmp_path):
    import json
    from src.roi.ledger import load_sensitivity

    scen = {"target": "t", "grid": [{"x": 1}], "model_vs_random": []}
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"label": "SIMULATION: x", "cost_per_treatment": 25, "strategies_not_run": {},
                             "scenarios": {"p75": scen, "configured": scen, "p75_uncalibrated": scen}}))
    out = load_sensitivity(f)
    assert {"p75", "configured"} <= set(out["scenarios"])
    assert "grid" in out["scenarios"]["p75"] and "grid" not in out["scenarios"]["p75_uncalibrated"]
    assert load_sensitivity(tmp_path / "missing.json") is None


def test_committed_sensitivity_grid_is_complete_and_labelled():
    from src.roi.ledger import load_sensitivity

    out = load_sensitivity()
    assert out is not None and "SIMULATION" in out["label"]
    g = out["scenarios"]["p75"]["grid"]
    assert {r["strategy"] for r in g} == {"model", "random", "supplier_history", "supplier_volume", "order_value"}
    assert len(g) == 5 * 4 * 4 * 3           # strategies x shares x effects x breach costs
    assert "order_value" in out.get("strategies_history", {})
    # on the non-degenerate target the model must beat random at every treated share (CI lower bound > 0)
    assert all(r["lift_ci95"][0] > 0 for r in out["scenarios"]["p75"]["model_vs_random"])


def test_roi_summary_endpoint_includes_sensitivity():
    df = pd.DataFrame([{"intervention_type": "expedite_approval", "risk_at_intervention": 0.9, "cost": 25.0,
                        "breached_after": None, "is_simulated": True}])
    with patch("src.api.routes.pd.read_sql", return_value=df), patch("src.api.routes.get_engine"):
        body = client.get("/roi/summary").json()
    assert set(body["sensitivity"]["scenarios"]) >= {"p75", "configured"}


def _first_case(target):
    from src.roi.ledger import assign_case
    policy = load_policy()
    return next(f"C{i}" for i in range(1000) if assign_case(f"C{i}", policy)[0] == target)


def test_post_respects_holdout_assignment_and_records_zero_cost():
    cid = _first_case("holdout")
    with _engines():
        r = client.post("/interventions", json={"case_id": cid, "risk_at_intervention": 0.9})
    body = r.json()
    assert r.status_code == 201 and body["assignment"] == "holdout" and body["action_required"] is False
    assert body["cost"] == 0.0 and body["experiment_id"]


def test_post_treat_assignment_requires_action():
    cid = _first_case("treat")
    with _engines():
        body = client.post("/interventions", json={"case_id": cid, "risk_at_intervention": 0.9}).json()
    assert body["assignment"] == "treat" and body["action_required"] is True and body["cost"] > 0


def test_holdout_rows_carry_no_cost_or_benefit_in_roi_but_feed_the_experiment_block():
    p = load_policy()
    eid = p["experiment"]["id"]
    rows = [{"intervention_type": "expedite_approval", "risk_at_intervention": 0.9, "cost": 25.0,
             "breached_after": i % 4 == 0, "is_simulated": False, "assignment": "treat", "experiment_id": eid}
            for i in range(60)]
    rows += [{"intervention_type": "expedite_approval", "risk_at_intervention": 0.9, "cost": 0.0,
              "breached_after": i % 2 == 0, "is_simulated": False, "assignment": "holdout", "experiment_id": eid}
             for i in range(60)]
    s = roi_summary(rows, p)
    assert s["logged"]["interventions"] == 60 and s["logged"]["total_cost"] == 60 * 25.0
    e = s["experiment"]
    assert e["treated"]["breach_rate"] == 0.25 and e["holdout"]["breach_rate"] == 0.5
    assert e["measured_uplift"]["breach_reduction"] == pytest.approx(0.25)
    lo, hi = e["measured_uplift"]["ci95"]
    assert lo < 0.25 < hi


def test_experiment_block_says_not_enough_data_below_thresholds_and_ignores_simulated_rows():
    p = load_policy()
    rows = [{"intervention_type": "expedite_approval", "risk_at_intervention": 0.9, "cost": 25.0, "breached_after": True,
             "is_simulated": True, "assignment": "treat", "experiment_id": None}] * 200
    e = roi_summary(rows, p)["experiment"]
    assert e["measured_uplift"] is None and e["treated"]["cases"] == 0
