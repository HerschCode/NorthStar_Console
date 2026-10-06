import joblib
import numpy as np
from fastapi.testclient import TestClient

from src.api.main import app
from src.mlops.registry import ModelRegistry

client = TestClient(app)


def _model(tmp_path):
    p = tmp_path / "m.joblib"; joblib.dump({"x": 1}, p); return p


def test_no_registry_is_reported_not_created(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_REGISTRY_DIR", str(tmp_path / "none"))
    r = client.get("/v1/mlops/registry")
    assert r.status_code == 200 and r.json()["available"] is False and "No model" in r.json()["note"]
    assert not (tmp_path / "none").exists()                    # a read never creates the directory


def test_registry_status_with_champion_and_challenger(tmp_path, monkeypatch):
    reg = ModelRegistry(tmp_path / "reg"); m = {"roc_auc": 0.8, "brier": 0.15, "ece": 0.03, "n_test": 500, "base_rate": 0.4}
    v1 = reg.register("sla_risk", _model(tmp_path), m, {}, "fp1"); reg.set_alias("sla_risk", "champion", v1["version"], "first")
    v2 = reg.register("sla_risk", _model(tmp_path), {**m, "roc_auc": 0.7}); reg.set_alias("sla_risk", "challenger", v2["version"], "drift")
    monkeypatch.setenv("MODEL_REGISTRY_DIR", str(tmp_path / "reg"))
    d = client.get("/v1/mlops/registry").json()
    assert d["available"] and d["champion"]["version"] == "v0001" and d["challenger"]["version"] == "v0002"
    assert d["champion_hash_verified"] is True and d["versions"] == ["v0001", "v0002"]
    assert d["champion"]["metrics"]["roc_auc"] == 0.8 and d["events"][-1]["event"] == "alias_set"
    reg.artifact_path("sla_risk", "v0001").write_bytes(b"tampered")
    assert client.get("/v1/mlops/registry").json()["champion_hash_verified"] is False


def test_name_is_validated(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_REGISTRY_DIR", str(tmp_path))
    assert client.get("/v1/mlops/registry", params={"name": "../etc"}).status_code == 422
