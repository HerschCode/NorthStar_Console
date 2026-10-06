from fastapi.testclient import TestClient

from src.api.main import app
from src.api.middleware import parse_traceparent
from src.v1 import router as v1router

client = TestClient(app)


def test_parse_traceparent_accepts_w3c_and_rejects_garbage():
    tid = "4bf92f3577b34da6a3ce929d0e0e4736"
    assert parse_traceparent(f"00-{tid}-00f067aa0ba902b7-01") == tid
    for bad in (None, "", "garbage", "00-" + "0" * 32 + "-00f067aa0ba902b7-01", "00-xyz-00f067aa0ba902b7-01"):
        assert parse_traceparent(bad) is None


def test_trace_id_is_echoed_on_the_response():
    tid = "4bf92f3577b34da6a3ce929d0e0e4736"
    r = client.get("/health", headers={"traceparent": f"00-{tid}-00f067aa0ba902b7-01"})
    assert r.headers.get("X-Trace-ID") == tid


def test_outcome_write_is_closed_without_a_key_and_requires_admin(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("API_KEYS", "reader:rk:reader,boss:ak:admin")
    body = {"breached_after": False}
    assert client.patch("/v1/interventions/1/outcome", json=body).status_code == 401
    assert client.patch("/v1/interventions/1/outcome", json=body, headers={"X-API-Key": "rk"}).status_code == 403


def test_outcome_is_recorded_for_admin_and_404_when_missing(monkeypatch):
    monkeypatch.setenv("API_KEYS", "boss:ak:admin")
    calls = []
    monkeypatch.setattr(v1router, "_write_outcome", lambda i, b: calls.append((i, b.breached_after)) or i == 7)
    ok = client.patch("/v1/interventions/7/outcome", json={"breached_after": True}, headers={"X-API-Key": "ak"})
    assert ok.status_code == 200 and ok.json()["recorded"] is True and calls == [(7, True)]
    assert client.patch("/v1/interventions/8/outcome", json={"breached_after": True}, headers={"X-API-Key": "ak"}).status_code == 404
