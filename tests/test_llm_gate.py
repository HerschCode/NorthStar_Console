"""Tests for the LLM faithfulness judge (src/evaluation/llm_gate.py).

No real LLM calls — all tests use a mock client.
"""
import pytest
from unittest.mock import MagicMock, patch
from src.evaluation.llm_gate import LLMGate, JudgeResult, _parse


# ── _parse ─────────────────────────────────────────────────────────────────────

def test_parse_faithful_with_reason():
    r = _parse("FAITHFUL: The answer correctly quotes the 5-day target from Section 2.1.")
    assert r.faithful is True
    assert "5-day" in r.reason


def test_parse_unfaithful_with_reason():
    r = _parse("UNFAITHFUL: The answer says 7 days but the context states 5 business days.")
    assert r.faithful is False
    assert "7 days" in r.reason


def test_parse_faithful_no_colon():
    r = _parse("FAITHFUL The answer matches the context.")
    assert r.faithful is True


def test_parse_unfaithful_via_fallback():
    # model returns answer without leading label — fallback scan
    r = _parse("Based on the context, the answer is UNFAITHFUL because the number differs.")
    assert r.faithful is False


def test_parse_faithful_via_fallback():
    r = _parse("The answer is clearly FAITHFUL to the retrieved context.")
    assert r.faithful is True


def test_parse_empty_defaults_unfaithful():
    r = _parse("")
    assert r.faithful is False
    assert "parse error" in r.reason


def test_parse_truncated_unfaith_defaults_unfaithful():
    r = _parse("UNFAITH")
    assert r.faithful is False


def test_parse_truncated_faithf_prefix_returns_faithful():
    # Groq sometimes returns a few characters of a truncated response
    r = _parse("FAITHF")
    assert r.faithful is True
    assert "truncated" in r.reason


def test_parse_truncated_unfaithf_prefix_returns_unfaithful():
    r = _parse("UNFAITHF")
    assert r.faithful is False
    assert "truncated" in r.reason


def test_parse_short_fa_defaults_unfaithful():
    # "FA" alone is ambiguous — too short to infer; conservative default
    r = _parse("FA")
    assert r.faithful is False
    assert "parse error" in r.reason


def test_parse_case_insensitive():
    r = _parse("faithful: the answer is correct.")
    assert r.faithful is True


# ── LLMGate.judge ──────────────────────────────────────────────────────────────

def _make_groq_response(content: str):
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_gate_with_response(content: str) -> LLMGate:
    gate = LLMGate.__new__(LLMGate)
    gate.model = "mock"
    gate._client = MagicMock()
    gate._client.chat.completions.create.return_value = _make_groq_response(content)
    return gate


def test_judge_faithful():
    gate = _make_gate_with_response(
        "FAITHFUL: The answer correctly quotes the 5-business-day target."
    )
    result = gate.judge("What is the target?", "5 business days.", ["5 business days target."])
    assert result.faithful is True


def test_judge_unfaithful():
    gate = _make_gate_with_response(
        "UNFAITHFUL: The answer says 7 days but context says 5."
    )
    result = gate.judge("What is the target?", "7 business days.", ["5 business days target."])
    assert result.faithful is False


def test_judge_retries_on_empty():
    """Retries when the first response is empty, succeeds on second attempt."""
    call_count = 0

    def _side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_groq_response("")  # empty first
        return _make_groq_response("FAITHFUL: Answer matches context.")

    gate = LLMGate.__new__(LLMGate)
    gate.model = "mock"
    gate._client = MagicMock()
    gate._client.chat.completions.create.side_effect = _side_effect

    with patch("time.sleep"):
        result = gate.judge("q", "a", ["context"], retries=1)
    assert result.faithful is True
    assert call_count == 2


def test_judge_empty_chunks():
    gate = _make_gate_with_response("UNFAITHFUL: No context was retrieved.")
    result = gate.judge("What is the target?", "5 business days.", [])
    assert result.faithful is False
