"""PLAN.md Phase 4: per-feature PSI, the retrain trigger's drift reason, /health/drift/features,
and the flow's retrain gate proven on simulated drift."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.ml.feature_drift import (
    compute_baselines, compute_feature_drift, psi,
)

CFG = {"psi_warn": 0.10, "psi_alert": 0.25, "retrain_min_alert_features": 2}


def _frame(n=2000, seed=0, suppliers=("A", "B", "C", "D"), weights=None, loc=10.0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "event_count": rng.normal(loc, 2, n).round(),
        "supplier_id": rng.choice(suppliers, n, p=weights),
        "category": rng.choice(["x", "y"], n),
    })


def test_psi_is_zero_for_identical_and_matches_hand_calc():
    assert psi([0.5, 0.5], [0.5, 0.5]) == pytest.approx(0.0)
    # (0.7-0.5)ln(1.4) + (0.3-0.5)ln(0.6)
    assert psi([0.5, 0.5], [0.7, 0.3]) == pytest.approx(0.2 * np.log(1.4) + (-0.2) * np.log(0.6))


def test_psi_empty_bin_is_finite():
    assert np.isfinite(psi([0.5, 0.5], [1.0, 0.0]))


def test_same_distribution_is_stable():
    base = compute_baselines(_frame(seed=1))
    report = compute_feature_drift(base, _frame(seed=2), CFG)
    assert report.status == "stable"
    assert all(f.psi < 0.10 for f in report.features)


def test_shifted_distribution_alerts_on_the_shifted_features():
    base = compute_baselines(_frame(seed=1))
    drifted = _frame(seed=3, weights=[0.85, 0.05, 0.05, 0.05], loc=16.0)
    report = compute_feature_drift(base, drifted, CFG)
    assert report.status == "alert"
    assert {"event_count", "supplier_id"} <= set(report.alert_features)
    assert "category" not in report.alert_features


def test_single_alert_feature_is_only_a_warn_not_a_retrain_alert():
    base = compute_baselines(_frame(seed=1))
    report = compute_feature_drift(base, _frame(seed=3, loc=16.0), CFG)
    assert report.alert_features == ["event_count"]
    assert report.status == "warn"


def test_unseen_category_lands_in_other_bucket():
    base = compute_baselines(_frame(seed=1))
    cur = _frame(seed=2)
    cur["supplier_id"] = "NEW_SUPPLIER"
    report = compute_feature_drift(base, cur, CFG)
    sup = next(f for f in report.features if f.feature == "supplier_id")
    assert sup.status == "alert"


def test_no_baseline_is_reported_not_crashed():
    assert compute_feature_drift(None, _frame(), CFG).status == "no_baseline"


def _meta(tmp_path, days_old=1):
    p = tmp_path / "meta.json"
    p.write_text(json.dumps({
        "trained_at": (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat(),
        "train_row_count": 800, "test_row_count": 200,
    }))
    return p


def test_trigger_fires_on_feature_drift_and_not_without_it(tmp_path):
    from src.ml.retrain_trigger import check_retrain_needed

    base = compute_baselines(_frame(seed=1))
    drifted = compute_feature_drift(base, _frame(seed=3, weights=[0.85, 0.05, 0.05, 0.05], loc=16.0), CFG)
    calm = compute_feature_drift(base, _frame(seed=2), CFG)
    meta = _meta(tmp_path)

    assert not check_retrain_needed(1000, meta_path=meta, feature_drift=calm).should_retrain
    assert not check_retrain_needed(1000, meta_path=meta).should_retrain
    rec = check_retrain_needed(1000, meta_path=meta, feature_drift=drifted)
    assert rec.should_retrain
    assert any("feature drift" in r for r in rec.reasons)


def test_endpoint_returns_report():
    from fastapi.testclient import TestClient
    from src.api.main import app
    from src.ml.feature_drift import FeatureDrift, FeatureDriftReport

    fake = FeatureDriftReport("alert", [FeatureDrift("supplier_id", 1.2, "alert"),
                                        FeatureDrift("event_count", 0.3, "alert")], 500)
    with patch("src.ml.feature_drift.current_feature_drift", return_value=fake):
        r = TestClient(app, headers={"X-API-Key": "test-key-do-not-use-in-production"}).get("/health/drift/features")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "alert" and body["alert_features"] == ["supplier_id", "event_count"]
    assert body["thresholds"]["psi_alert"] == 0.25


def _run_flow(gate_result, force=False):
    import flows.pipeline_flow as pf
    with patch.object(pf, "ingest_task", return_value=1), \
         patch.object(pf, "validate_task", return_value=None), \
         patch.object(pf, "dbt_build_task", return_value="ok"), \
         patch.object(pf, "retrain_gate_task", return_value=gate_result), \
         patch.object(pf, "train_task", return_value={"rf": {"roc_auc": 0.9}}) as train, \
         patch.object(pf, "evaluate_and_register_task", return_value={"promoted": True}) as ev, \
         patch.object(pf, "report_task") as report:
        out = pf.pipeline_flow.fn(force)
    return out, train, ev, report


def test_flow_retrains_when_gate_fires_on_simulated_drift():
    out, train, ev, report = _run_flow({"retrain": True, "reasons": ["feature drift: ..."]})
    train.assert_called_once(); ev.assert_called_once(); report.assert_called_once()
    assert out["retrained"] is True


def test_flow_skips_training_when_no_trigger_fires():
    out, train, ev, report = _run_flow({"retrain": False, "reasons": []})
    train.assert_not_called(); ev.assert_not_called(); report.assert_called_once()
    assert out["retrained"] is False


def test_gate_task_end_to_end_on_simulated_drift(tmp_path, monkeypatch):
    """Real retrain_gate_task body + real trigger + real PSI: drift in -> RETRAIN, calm -> SKIP."""
    import flows.pipeline_flow as pf
    from src.ml.feature_drift import compute_feature_drift

    base = compute_baselines(_frame(seed=1))
    meta = _meta(tmp_path)
    monkeypatch.setattr("src.ml.retrain_trigger.check_retrain_needed.__defaults__",
                        (str(meta), 30, 0.20, None, None))
    cases = pd.DataFrame({"case_id": range(1000)})
    for cur, expected in ((_frame(seed=3, weights=[0.85, .05, .05, .05], loc=16.0), True),
                          (_frame(seed=2), False)):
        with patch("src.api.db.load_cases", return_value=cases), \
             patch("src.ml.feature_drift.current_feature_drift",
                   return_value=compute_feature_drift(base, cur, CFG)):
            assert pf.retrain_gate_task.fn("ok")["retrain"] is expected
    with patch("src.api.db.load_cases", return_value=cases), \
         patch("src.ml.feature_drift.current_feature_drift", side_effect=RuntimeError("db down")):
        assert pf.retrain_gate_task.fn("ok")["feature_drift_status"] is None
        assert pf.retrain_gate_task.fn("ok", True)["retrain"] is True
