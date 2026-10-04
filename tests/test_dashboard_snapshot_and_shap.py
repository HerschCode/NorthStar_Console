import json

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from src.api import dashboard
from src.api.snapshot import load_snapshot
from src.ml.calibration import HeldOutCalibratedClassifier
from src.ml.explain import explain_shap_batch


def test_shap_batch_returns_real_shap_values_for_the_held_out_calibrated_model():
    """Regression: explain_shap_batch only unwrapped sklearn's CalibratedClassifierCV, so with the
    HeldOutCalibratedClassifier served since 2026-09-25 it silently fell back to the legacy importance
    approximation (no 'shap' key) and the dashboard's top-risk panel failed with KeyError."""
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(200, 4)), columns=list("abcd"))
    y = (X["a"] + rng.normal(scale=0.3, size=200) > 0).astype(int)
    rf = RandomForestClassifier(50, random_state=0).fit(X, y)
    model = HeldOutCalibratedClassifier(rf).fit(X.iloc[:100], y.iloc[:100])
    bundle = {"model": model, "columns": list("abcd"), "feature_importances": {}}
    out = explain_shap_batch(X.iloc[:3], bundle, top_n=2)
    assert len(out) == 3 and all("shap" in f for row in out for f in row)
    top = [row[0]["feature"] for row in explain_shap_batch(X.iloc[:40], bundle, top_n=1)]
    assert top.count("a") > len(top) // 2     # the feature that drives y dominates


def test_snapshot_loader_labels_snapshot_and_handles_missing_file(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"executive_overview": {"available": True}, "snapshot": {"as_of": "2026-10-04"}}))
    snap = load_snapshot(p)
    assert snap["snapshot"]["live"] is False and snap["snapshot"]["as_of"] == "2026-10-04"
    assert load_snapshot(tmp_path / "missing.json") is None


def test_dashboard_serves_snapshot_when_database_is_unreachable(monkeypatch):
    def boom():
        raise RuntimeError("quota exceeded")
    monkeypatch.setattr(dashboard, "load_cases", boom)
    monkeypatch.setattr(dashboard, "load_events", boom)
    monkeypatch.setattr(dashboard, "load_snapshot", lambda: {"executive_overview": {"available": True}, "snapshot": {"live": False}})
    assert dashboard._compute_dashboard_data()["snapshot"]["live"] is False


def test_dashboard_without_snapshot_reports_the_database_error(monkeypatch):
    def boom():
        raise RuntimeError("quota exceeded")
    monkeypatch.setattr(dashboard, "load_cases", boom)
    monkeypatch.setattr(dashboard, "load_events", boom)
    monkeypatch.setattr(dashboard, "load_snapshot", lambda: None)
    data = dashboard._compute_dashboard_data()
    assert data["executive_overview"] == {"available": False, "reason": "quota exceeded"}


def test_committed_snapshot_has_every_section_available_except_none_expected():
    snap = load_snapshot()
    assert snap is not None and snap["snapshot"]["as_of"]
    for k in ("executive_overview", "bottlenecks", "suppliers", "conformance", "sla_risk",
              "top_risk_cases", "ap_controls", "working_capital"):
        assert snap[k]["available"], (k, snap[k].get("reason"))
