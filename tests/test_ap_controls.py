"""
Tests for the AP controls tools (Phase F2 finance module).

Verifies:
- argument sets match what the MCP server / registry exposes (schema pin)
- read tools pass the right query parameters to the P1 client
- write tools delegate to propose_intervention with the correct action strings
- both payment actions are in VALID_ACTIONS (so interventions.py accepts them)
- the registry includes all five AP tools

No real network calls: P1 client and propose_intervention are patched throughout.
"""
import pytest
from unittest.mock import patch, MagicMock

from src.tools.ap_controls import (
    get_control_exceptions,
    get_control_summary,
    get_working_capital_summary,
    propose_payment_hold,
    propose_payment_release,
    ALL_AP_TOOLS,
    GET_CONTROL_EXCEPTIONS_SCHEMA,
    GET_CONTROL_SUMMARY_SCHEMA,
    GET_WORKING_CAPITAL_SUMMARY_SCHEMA,
    PROPOSE_PAYMENT_HOLD_SCHEMA,
    PROPOSE_PAYMENT_RELEASE_SCHEMA,
)
from src.tools.interventions import VALID_ACTIONS, _STORE
from src.tools.registry import ALL_TOOLS, TOOL_FUNCTIONS


# ── schema pin: argument sets must stay stable ────────────────────────────────

def test_get_control_exceptions_schema_required_fields():
    schema = GET_CONTROL_EXCEPTIONS_SCHEMA
    assert schema["name"] == "get_control_exceptions"
    props = schema["input_schema"]["properties"]
    assert "control" in props
    assert "vendor" in props
    assert "min_exposure" in props
    assert "top_n" in props
    assert schema["input_schema"]["required"] == []


def test_get_control_summary_schema_no_required_args():
    schema = GET_CONTROL_SUMMARY_SCHEMA
    assert schema["name"] == "get_control_summary"
    assert schema["input_schema"]["required"] == []


def test_get_working_capital_summary_schema_no_required_args():
    schema = GET_WORKING_CAPITAL_SUMMARY_SCHEMA
    assert schema["name"] == "get_working_capital_summary"
    assert schema["input_schema"]["required"] == []


def test_propose_payment_hold_schema_required_args():
    schema = PROPOSE_PAYMENT_HOLD_SCHEMA
    assert schema["name"] == "propose_payment_hold"
    required = schema["input_schema"]["required"]
    assert "case_id" in required
    assert "reason" in required


def test_propose_payment_release_schema_required_args():
    schema = PROPOSE_PAYMENT_RELEASE_SCHEMA
    assert schema["name"] == "propose_payment_release"
    required = schema["input_schema"]["required"]
    assert "case_id" in required
    assert "reason" in required


# ── read tools: correct endpoints and params ──────────────────────────────────

@patch("src.tools.ap_controls.get")
def test_get_control_exceptions_default_params(mock_get):
    mock_get.return_value = []
    get_control_exceptions()
    mock_get.assert_called_once_with("/controls/exceptions", params={"top_n": 20})


@patch("src.tools.ap_controls.get")
def test_get_control_exceptions_with_filters(mock_get):
    mock_get.return_value = []
    get_control_exceptions(control="C4", vendor="Acme", min_exposure=500.0, top_n=5)
    mock_get.assert_called_once_with(
        "/controls/exceptions",
        params={"top_n": 5, "control": "C4", "vendor": "Acme", "min_exposure": 500.0},
    )


@patch("src.tools.ap_controls.get")
def test_get_control_exceptions_top_n_clamped_at_100(mock_get):
    mock_get.return_value = []
    get_control_exceptions(top_n=999)
    call_params = mock_get.call_args[1]["params"]
    assert call_params["top_n"] == 100


@patch("src.tools.ap_controls.get")
def test_get_control_summary_calls_correct_endpoint(mock_get):
    mock_get.return_value = {"C1": {"count": 3}}
    result = get_control_summary()
    mock_get.assert_called_once_with("/controls/summary")
    assert result == {"C1": {"count": 3}}


@patch("src.tools.ap_controls.get")
def test_get_working_capital_summary_calls_correct_endpoint(mock_get):
    mock_get.return_value = {"dpo": 42.1}
    result = get_working_capital_summary()
    mock_get.assert_called_once_with("/working-capital/summary")
    assert result == {"dpo": 42.1}


# ── write tools: delegate to propose_intervention with correct action ─────────

def setup_function():
    _STORE.clear()


@patch("src.tools.interventions._fetch_roi_context", return_value=None)
def test_propose_payment_hold_uses_hold_payment_action(_mock_roi):
    result = propose_payment_hold(
        case_id="INV-2024-089",
        reason="C4 duplicate invoice: same vendor, EUR 12,450, within 30 days",
    )
    assert result["status"] == "pending_approval"
    assert result["action"] == "hold_payment"
    assert result["target"] == "INV-2024-089"
    assert result["priority"] == "high"


@patch("src.tools.interventions._fetch_roi_context", return_value=None)
def test_propose_payment_release_uses_release_payment_action(_mock_roi):
    result = propose_payment_release(
        case_id="INV-2024-089",
        reason="Duplicate resolved: vendor confirmed single invoice, AP Manager authorised release",
    )
    assert result["status"] == "pending_approval"
    assert result["action"] == "release_payment"
    assert result["target"] == "INV-2024-089"
    assert result["priority"] == "normal"


# ── interventions.py accepts the payment actions ──────────────────────────────

def test_hold_payment_in_valid_actions():
    assert "hold_payment" in VALID_ACTIONS


def test_release_payment_in_valid_actions():
    assert "release_payment" in VALID_ACTIONS


# ── registry includes all five AP tools ───────────────────────────────────────

def test_all_ap_tools_in_registry():
    registry_names = {schema["name"] for schema, _ in ALL_TOOLS}
    for schema, _ in ALL_AP_TOOLS:
        assert schema["name"] in registry_names, f"{schema['name']} missing from registry"


def test_ap_tool_functions_callable_from_registry():
    for schema, fn in ALL_AP_TOOLS:
        assert TOOL_FUNCTIONS[schema["name"]] is fn
