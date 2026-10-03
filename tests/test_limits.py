"""Input size limits (gateway/limits.py): an oversized request is refused before any detector runs."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from gateway import ip_limits
from gateway import auth
from gateway.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_ip_limit(monkeypatch):
    monkeypatch.setenv("GATEWAY_IP_RATE_LIMIT", "0")
    ip_limits.reset_for_tests()
    yield
    ip_limits.reset_for_tests()


def chat(prompt="What is the status of order 12345?", **kw):
    body = {"prompt": prompt, "session_id": "lim", "backend": "trivial_echo", **kw}
    return client.post("/gateway/chat", json=body)


def test_a_normal_request_is_unaffected():
    r = chat()
    assert r.status_code == 200 and r.json()["allowed"] is True


def test_a_prompt_over_the_limit_is_a_422_and_no_detector_ran(monkeypatch):
    calls = []
    from gateway import middleware as mw_mod
    monkeypatch.setattr(mw_mod.GatewayMiddleware, "_run_injection_ensemble", lambda *a, **k: calls.append(1) or (False, None, None, {}))
    r = chat("x" * 20_001)
    assert r.status_code == 422 and "longer than 20000" in r.text
    assert calls == []


def test_a_prompt_at_the_limit_is_accepted():
    assert chat("a" * 20_000).status_code == 200


def test_the_prompt_limit_is_tunable_per_request(monkeypatch):
    monkeypatch.setenv("GATEWAY_MAX_PROMPT_CHARS", "50")
    assert chat("y" * 51).status_code == 422
    assert chat("y" * 50).status_code == 200


@pytest.mark.parametrize("field,value", [("session_id", "s" * 129), ("session_id", ""), ("role", "r" * 33), ("backend", "b" * 65), ("user_id", "u" * 129)])
def test_identity_fields_are_bounded(field, value):
    body = {"prompt": "hi", "session_id": "lim", "backend": "trivial_echo", field: value}
    assert client.post("/gateway/chat", json=body).status_code == 422


def test_a_declared_content_length_over_the_limit_is_a_413_without_reading_the_body(monkeypatch):
    monkeypatch.setenv("GATEWAY_MAX_BODY_BYTES", "1000")
    r = client.post("/gateway/chat", content=b"{" + b" " * 2000 + b"}", headers={"content-type": "application/json"})
    assert r.status_code == 413 and "larger than 1000" in r.text


def test_a_chunked_body_that_streams_past_the_limit_is_cut_off(monkeypatch):
    """No Content-Length: the limit has to be enforced as the bytes arrive."""
    monkeypatch.setenv("GATEWAY_MAX_BODY_BYTES", "1000")

    def body():
        yield b'{"prompt": "'
        for _ in range(50):
            yield b"a" * 100
        yield b'", "session_id": "s"}'

    r = client.post("/gateway/chat", content=body(), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_the_body_limit_applies_to_every_route(monkeypatch):
    monkeypatch.setenv("GATEWAY_MAX_BODY_BYTES", "200")
    r = client.post("/gateway/actions/authorize", content=b"{" + b" " * 500 + b"}", headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_zero_disables_the_body_limit(monkeypatch):
    monkeypatch.setenv("GATEWAY_MAX_BODY_BYTES", "0")
    assert chat("z" * 10_000).status_code == 200


def test_identity_authentication_fails_closed_when_the_shared_secret_is_missing(monkeypatch):
    monkeypatch.setenv("GATEWAY_REQUIRE_IDENTITY", "1")
    monkeypatch.delenv("GATEWAY_IDENTITY_TOKEN", raising=False)
    assert chat("hi", user_id="u-0001", role="manager").status_code == 503


def test_identity_is_taken_from_the_authenticated_proxy_not_the_request_body(monkeypatch):
    monkeypatch.setenv("GATEWAY_REQUIRE_IDENTITY", "1")
    monkeypatch.setenv("GATEWAY_IDENTITY_TOKEN", "proxy-secret")
    assert chat("hi", user_id="manager-from-body", role="admin").status_code == 401
    with pytest.raises(HTTPException) as error:
        auth.require_trusted_identity("\u00e9", "employee-1", "employee")
    assert error.value.status_code == 401

    r = client.post(
        "/gateway/chat",
        json={"prompt": "hi", "session_id": "lim", "backend": "trivial_echo",
              "user_id": "manager-from-body", "role": "admin"},
        headers={"X-Gateway-Identity-Token": "proxy-secret",
                 "X-Gateway-User-ID": "employee-1", "X-Gateway-Role": "employee"},
    )
    assert r.status_code == 200


def test_authenticated_sessions_are_scoped_to_the_trusted_user():
    from gateway.auth import TrustedIdentity, scope_session_id

    alice = scope_session_id("shared-session", TrustedIdentity("alice", "employee"))
    bob = scope_session_id("shared-session", TrustedIdentity("bob", "employee"))
    assert alice != bob and len(alice) == 32


def test_auth_dependency_preserves_demo_mode_when_identity_is_not_required(monkeypatch):
    monkeypatch.delenv("GATEWAY_REQUIRE_IDENTITY", raising=False)
    monkeypatch.delenv("GATEWAY_IDENTITY_TOKEN", raising=False)
    assert auth.require_trusted_identity() is None


def test_identity_auth_covers_control_and_demo_surfaces(monkeypatch):
    monkeypatch.setenv("GATEWAY_REQUIRE_IDENTITY", "1")
    monkeypatch.delenv("GATEWAY_IDENTITY_TOKEN", raising=False)
    assert client.get("/gateway/dashboard").status_code == 503
    assert client.get("/gateway/stats").status_code == 503
    assert client.get("/gateway/connectivity").status_code == 503
    assert client.get("/gateway/actions/policy").status_code == 503
    assert client.post("/gateway/demo/run", json={"prompt": "hi"}).status_code == 503


def test_authenticated_dashboard_uses_proxy_approval_identity(monkeypatch):
    monkeypatch.setenv("GATEWAY_REQUIRE_IDENTITY", "1")
    monkeypatch.setenv("GATEWAY_IDENTITY_TOKEN", "proxy-secret")
    headers = {
        "X-Gateway-Identity-Token": "proxy-secret",
        "X-Gateway-User-ID": "approver-1",
        "X-Gateway-Role": "manager",
    }
    r = client.get("/gateway/dashboard", headers=headers)
    assert r.status_code == 200 and "const identityAuthEnabled = true;" in r.text


def test_get_requests_and_health_are_untouched():
    assert client.get("/health").status_code == 200
