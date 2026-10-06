import json
import pickle
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import GradientBoostingClassifier

from src.ml.feature_drift import compute_baselines, compute_feature_drift
from src.mlops import metrics as M
from src.mlops.cycle import rollback, run_cycle, shadow_compare
from src.mlops.gate import evaluate_gate, load_config
from src.mlops.modelcard import render
from src.mlops.registry import ModelRegistry, RegistryError
from src.mlops.safe_load import UnsafeModelError, load_verified, restricted_load


def synth(n=1500, shift=0.0, seed=0):
    r = np.random.default_rng(seed)
    X = pd.DataFrame({"a": r.normal(shift, 1, n), "b": r.normal(0, 1, n), "c": r.integers(0, 5, n)})
    y = (X["a"] - shift + 0.8 * X["b"] + r.normal(0, 1, n) > 0.2).astype(int)
    return X, y


def trainer(tmp_path, shift=0.0, seed=0, noise=False):
    def fn():
        X, y = synth(shift=shift, seed=seed)
        Xt, yt = synth(shift=shift, seed=seed + 1)
        if noise:
            y = pd.Series(np.random.default_rng(9).integers(0, 2, len(y)))
        m = GradientBoostingClassifier(random_state=0).fit(X, y)
        p = m.predict_proba(Xt)[:, 1]
        path = tmp_path / f"m{shift}{seed}{noise}.joblib"
        joblib.dump(m, path)
        meta = {"trained_at": "2026-01-01T00:00:00+00:00", "train_row_count": len(X), "test_row_count": len(Xt),
                "feature_baselines": compute_baselines(X)}
        return str(path), M.evaluate(yt, p, n_boot=50), meta, "fp-" + str(seed)
    return fn


def test_metrics_basic():
    y = np.array([0, 0, 1, 1]); p = np.array([0.1, 0.4, 0.35, 0.8])
    assert M.roc_auc(y, p) == pytest.approx(0.75)
    assert M.precision_at_k(y, p, 2) == 0.5
    assert M.ece(np.array([1, 1, 0, 0]), np.array([1.0, 1.0, 0.0, 0.0])) == 0.0
    assert M.ece(np.array([1, 0] * 50), np.full(100, 0.9)) == pytest.approx(0.4)
    lo, hi = M.bootstrap_ci(M.roc_auc, y.repeat(20), p.repeat(20), n=100, groups=np.arange(80) // 4)
    assert lo <= 0.75 <= hi


def test_registry_versions_aliases_rollback_and_verify(tmp_path):
    reg = ModelRegistry(tmp_path / "r")
    f = tmp_path / "a.bin"; f.write_bytes(b"one")
    v1 = reg.register("m", f, {"roc_auc": 0.7}); f.write_bytes(b"two"); v2 = reg.register("m", f, {"roc_auc": 0.8})
    assert (v1["version"], v2["version"]) == ("v0001", "v0002")
    reg.set_alias("m", "champion", "v0001"); reg.set_alias("m", "champion", "v0002")
    assert reg.resolve("m")["version"] == "v0002"
    assert reg.rollback("m", reason="bad")["version"] == "v0001" and reg.resolve("m")["version"] == "v0001"
    assert len(reg.alias_history("m")) == 3  # history is appended to, never rewritten
    assert reg.verify("m", "v0001")
    reg.artifact_path("m", "v0001").write_bytes(b"tampered")
    assert not reg.verify("m", "v0001")
    with pytest.raises(RegistryError):
        ModelRegistry(tmp_path / "x").rollback("none")


def test_gate_rules():
    cfg = load_config()
    good = {"roc_auc": 0.80, "brier": 0.15, "ece": 0.03, "n_test": 1000, "base_rate": 0.4, "n_unique_scores": 500}
    assert evaluate_gate(good, None, cfg).passed
    assert not evaluate_gate({**good, "base_rate": 0.97}, None, cfg).passed          # degenerate target
    assert not evaluate_gate({**good, "n_test": 50}, None, cfg).passed
    assert not evaluate_gate({**good, "ece": 0.3}, None, cfg).passed
    champ = {**good, "roc_auc": 0.78}
    assert evaluate_gate({**good, "roc_auc": 0.80}, champ, cfg).passed
    assert not evaluate_gate({**good, "roc_auc": 0.78}, champ, cfg).passed            # no gain
    worse_cal = evaluate_gate({**good, "roc_auc": 0.82, "ece": 0.09}, champ, cfg)
    assert not worse_cal.passed and "ECE" in worse_cal.summary()
    jump = evaluate_gate({**good, "roc_auc": 0.95}, champ, cfg)                       # the calibration-on-train-rows pattern
    assert not jump.passed and jump.needs_review


def test_full_loop_promote_reject_rollback_and_card(tmp_path):
    reg = ModelRegistry(tmp_path / "reg")
    # no champion: first model is promoted
    r1 = run_cycle(reg, "sla", trainer(tmp_path, seed=1), current_case_count=1500)
    assert r1.action == "promoted" and reg.resolve("sla")["version"] == "v0001"
    # nothing changed: skipped (champion meta says trained 2026-01-01, so pretend it is the same day)
    from datetime import datetime, timezone
    r2 = run_cycle(reg, "sla", trainer(tmp_path, seed=2), current_case_count=1500, now=datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert r2.action == "skipped" and len(reg.versions("sla")) == 1
    # drift fires the trigger; a challenger trained on pure noise is rejected, champion unchanged
    X0, _ = synth(); Xd, _ = synth(shift=3.0, seed=5); Xd["b"] = Xd["b"] + 3.0  # two inputs move together
    drift = compute_feature_drift(compute_baselines(X0), Xd)
    assert drift.status == "alert"
    r3 = run_cycle(reg, "sla", trainer(tmp_path, seed=3, noise=True), current_case_count=1500, feature_drift=drift,
                   now=datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert r3.action in ("rejected", "needs_review") and reg.resolve("sla")["version"] == "v0001"
    # a genuinely better challenger (forced) is promoted, then rolled back
    r4 = run_cycle(reg, "sla", trainer(tmp_path, seed=4), current_case_count=1500, force=True,
                   gate_cfg={**load_config(), "min_auc_gain": -1.0, "max_auc_drop": 1.0, "max_brier_worsening": 1.0})
    assert r4.action == "promoted" and reg.resolve("sla")["version"] == "v0003"
    assert rollback(reg, "sla", "metric regression in production")["version"] == "v0001"
    card = render(reg, "sla", "v0001")
    assert "Model card" in card and "rolled_back" in card and reg.resolve("sla")["sha256"] in card
    assert any(e["event"] == "gate_decision" for e in reg.events())


def test_shadow_compare_detects_disagreement():
    a = np.arange(100.0)
    assert shadow_compare(a, a)["top_k_overlap"] == 1.0
    assert shadow_compare(a, -a)["top_k_overlap"] == 0.0


def test_restricted_load_and_verification(tmp_path):
    reg = ModelRegistry(tmp_path / "reg")
    run_cycle(reg, "sla", trainer(tmp_path, seed=1), current_case_count=1500)
    model, rec = load_verified(reg, "sla")
    assert hasattr(model, "predict_proba")
    # a swapped file fails the hash check
    reg.artifact_path("sla", "v0001").write_bytes(b"x")
    with pytest.raises(UnsafeModelError):
        load_verified(reg, "sla")


class Evil:
    def __reduce__(self):
        import os
        return (os.system, ("echo pwned",))


def test_malicious_pickle_is_blocked(tmp_path):
    p = tmp_path / "evil.joblib"
    joblib.dump(Evil(), p)
    with pytest.raises(UnsafeModelError, match="os|posix|nt"):
        restricted_load(p)


def test_committed_model_loads_under_the_allowlist():
    path = Path("models/sla_risk_model.joblib")
    if not path.exists():
        pytest.skip("model artifact not present")
    bundle = restricted_load(path)  # the project saves a dict: calibrated model + metadata
    assert hasattr(bundle["model"], "predict_proba")
