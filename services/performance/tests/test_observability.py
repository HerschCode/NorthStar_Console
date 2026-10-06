import json
import logging
import io
from src.observability.logging_config import JsonFormatter


def test_json_formatter_produces_valid_json():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="something happened", args=(), exc_info=None,
    )
    output = formatter.format(record)
    parsed = json.loads(output)  # raises if not valid JSON
    assert parsed["message"] == "something happened"
    assert parsed["level"] == "INFO"


def test_json_formatter_includes_extra_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="pipeline step", args=(), exc_info=None,
    )
    record.run_id = 42
    record.stage = "clean"
    output = json.loads(formatter.format(record))
    assert output["run_id"] == 42
    assert output["stage"] == "clean"


def test_json_formatter_handles_exceptions():
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="failed", args=(), exc_info=sys.exc_info(),
        )
    output = json.loads(formatter.format(record))
    assert "exception" in output
    assert "boom" in output["exception"]


def test_api_db_prefers_dedicated_credential_over_pipeline_credential(monkeypatch):
    import importlib
    from src.api import db as db_module

    monkeypatch.setenv("DB_USER", "pipeline_user")
    monkeypatch.setenv("DB_PASSWORD", "pipeline_pass")
    monkeypatch.setenv("API_DB_USER", "api_reader_user")
    monkeypatch.setenv("API_DB_PASSWORD", "api_reader_pass")
    monkeypatch.setenv("DB_HOST", "localhost")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "testdb")

    importlib.reload(db_module)  # reset the module-level _engine singleton
    engine = db_module.get_engine()
    assert "api_reader_user" in str(engine.url)
    assert "pipeline_user" not in str(engine.url)


def test_api_db_falls_back_to_pipeline_credential_when_unset(monkeypatch):
    import importlib
    from src.api import db as db_module

    monkeypatch.setenv("DB_USER", "pipeline_user")
    monkeypatch.setenv("DB_PASSWORD", "pipeline_pass")
    monkeypatch.delenv("API_DB_USER", raising=False)
    monkeypatch.delenv("API_DB_PASSWORD", raising=False)
    monkeypatch.setenv("DB_HOST", "localhost")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "testdb")

    importlib.reload(db_module)
    engine = db_module.get_engine()
    assert "pipeline_user" in str(engine.url)
