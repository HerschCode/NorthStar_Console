"""
src/api/main.py builds the FastAPI app (and reads API_KEY) at import time, so these
tests reload the app module after setting/clearing the env var, rather than relying
on FastAPI re-evaluating the dependency's environment lookup per-request (it does --
require_api_key reads os.environ live -- but reloading main.py per test here keeps
these tests independent of that implementation detail and robust either way).
"""
import importlib
from fastapi.testclient import TestClient


def _client_with_api_key(monkeypatch, key: str | None):
    if key is None:
        monkeypatch.delenv("API_KEY", raising=False)
    else:
        monkeypatch.setenv("API_KEY", key)
    import src.api.main as main_module
    importlib.reload(main_module)
    return TestClient(main_module.app)


def test_health_never_requires_a_key(monkeypatch):
    client = _client_with_api_key(monkeypatch, "secret-123")
    response = client.get("/health")
    assert response.status_code == 200


def test_protected_endpoint_fails_closed_when_no_key_configured(monkeypatch):
    client = _client_with_api_key(monkeypatch, None)
    response = client.get("/metrics/cycle-time")
    assert response.status_code == 401  # no API_KEY set -> fail closed


def test_protected_endpoint_rejects_missing_header_when_key_configured(monkeypatch):
    client = _client_with_api_key(monkeypatch, "secret-123")
    response = client.get("/metrics/cycle-time")
    assert response.status_code == 401


def test_protected_endpoint_rejects_wrong_key(monkeypatch):
    client = _client_with_api_key(monkeypatch, "secret-123")
    response = client.get("/metrics/cycle-time", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401


def test_protected_endpoint_accepts_correct_key(monkeypatch):
    from unittest.mock import patch
    client = _client_with_api_key(monkeypatch, "secret-123")
    with patch("src.api.routes.load_cases") as mock_load_cases:
        import pandas as pd
        mock_load_cases.return_value = pd.DataFrame({"case_id": ["C1"], "cycle_time_hours": [10.0]})
        response = client.get("/metrics/cycle-time", headers={"X-API-Key": "secret-123"})
    assert response.status_code == 200


def _client_with_api_keys(monkeypatch, keys_env: str | None):
    monkeypatch.delenv("API_KEY", raising=False)
    if keys_env is None:
        monkeypatch.delenv("API_KEYS", raising=False)
    else:
        monkeypatch.setenv("API_KEYS", keys_env)
    import src.api.main as main_module
    importlib.reload(main_module)
    return TestClient(main_module.app)


def test_multiple_clients_each_have_their_own_working_key(monkeypatch):
    from unittest.mock import patch
    client = _client_with_api_keys(monkeypatch, "analyst:key-a:reader,ci:key-b:reader")
    with patch("src.api.routes.load_cases") as mock_load_cases:
        import pandas as pd
        mock_load_cases.return_value = pd.DataFrame({"case_id": ["C1"], "cycle_time_hours": [10.0]})
        r1 = client.get("/metrics/cycle-time", headers={"X-API-Key": "key-a"})
        r2 = client.get("/metrics/cycle-time", headers={"X-API-Key": "key-b"})
    assert r1.status_code == 200
    assert r2.status_code == 200


def test_revoking_one_clients_key_does_not_affect_another(monkeypatch):
    """The actual point of per-client keys: removing ONE entry from API_KEYS
    revokes only that client, not everyone -- unlike the single shared-key model
    where rotating meant every caller needed the new value."""
    from unittest.mock import patch
    # ci's key ("key-b") is simply absent now, simulating revocation
    client = _client_with_api_keys(monkeypatch, "analyst:key-a:reader")
    with patch("src.api.routes.load_cases") as mock_load_cases:
        import pandas as pd
        mock_load_cases.return_value = pd.DataFrame({"case_id": ["C1"], "cycle_time_hours": [10.0]})
        still_works = client.get("/metrics/cycle-time", headers={"X-API-Key": "key-a"})
        revoked = client.get("/metrics/cycle-time", headers={"X-API-Key": "key-b"})
    assert still_works.status_code == 200
    assert revoked.status_code == 401


def test_malformed_api_keys_entry_is_skipped_not_fatal(monkeypatch):
    """A typo in one entry (missing a ':' segment) shouldn't take down every other
    configured key, or crash the app at request time."""
    from unittest.mock import patch
    client = _client_with_api_keys(monkeypatch, "broken-entry-no-colons,analyst:key-a:reader")
    with patch("src.api.routes.load_cases") as mock_load_cases:
        import pandas as pd
        mock_load_cases.return_value = pd.DataFrame({"case_id": ["C1"], "cycle_time_hours": [10.0]})
        response = client.get("/metrics/cycle-time", headers={"X-API-Key": "key-a"})
    assert response.status_code == 200


def test_legacy_api_key_still_works_alongside_api_keys(monkeypatch):
    """Backward compatibility: an existing deployment's API_KEY doesn't silently
    stop working once API_KEYS is introduced."""
    from unittest.mock import patch
    monkeypatch.setenv("API_KEY", "legacy-secret")
    monkeypatch.setenv("API_KEYS", "analyst:key-a:reader")
    import src.api.main as main_module
    importlib.reload(main_module)
    client = TestClient(main_module.app)
    with patch("src.api.routes.load_cases") as mock_load_cases:
        import pandas as pd
        mock_load_cases.return_value = pd.DataFrame({"case_id": ["C1"], "cycle_time_hours": [10.0]})
        legacy = client.get("/metrics/cycle-time", headers={"X-API-Key": "legacy-secret"})
        new_style = client.get("/metrics/cycle-time", headers={"X-API-Key": "key-a"})
    assert legacy.status_code == 200
    assert new_style.status_code == 200
