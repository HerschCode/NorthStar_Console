"""Tests for the LLM faithfulness judge (src/evaluation/llm_gate.py).

No real LLM calls — all tests use a mock client.
"""
import pytest
from unittest.mock import MagicMock, patch
from src.evaluation.llm_gate import LLMGate, JudgeResult, _parse
from scripts import evaluate_llm_gate
from scripts.evaluate_llm_gate import _confusion, _store_result


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
    # A label appearing in explanatory text is not a valid structured verdict.
    r = _parse("Based on the context, the answer is UNFAITHFUL because the number differs.")
    assert r.faithful is False
    assert "parse error" in r.reason


def test_parse_faithful_via_fallback():
    r = _parse("The answer is clearly FAITHFUL to the retrieved context.")
    assert r.faithful is False
    assert "parse error" in r.reason


def test_parse_empty_defaults_unfaithful():
    r = _parse("")
    assert r.faithful is False
    assert "parse error" in r.reason


def test_parse_truncated_unfaith_defaults_unfaithful():
    r = _parse("UNFAITH")
    assert r.faithful is False


def test_parse_truncated_faithf_prefix_fails_closed():
    r = _parse("FAITHF")
    assert r.faithful is False
    assert "parse error" in r.reason


def test_parse_truncated_unfaithf_prefix_returns_unfaithful():
    r = _parse("UNFAITHF")
    assert r.faithful is False
    assert "parse error" in r.reason


def test_parse_json_response():
    r = _parse('{"faithful": false, "reason": "The answer reverses the policy polarity."}')
    assert r.faithful is False
    assert "polarity" in r.reason


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
    gate.provider = "groq"
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
    gate.provider = "groq"
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


def test_judge_gemini_with_injected_client():
    client = MagicMock()
    client.models.generate_content.return_value.text = (
        "UNFAITHFUL: The answer says No where the context establishes Yes."
    )
    gate = LLMGate(provider="gemini", model="gemini-test", client=client)

    result = gate.judge("Is the time included?", "No.", ["Yes, the time is included."])

    assert result.faithful is False
    client.models.generate_content.assert_called_once()
    assert client.models.generate_content.call_args.kwargs["model"] == "gemini-test"


def test_judge_retries_unparseable_response():
    gate = _make_gate_with_response("FAITHF")
    gate._client.chat.completions.create.side_effect = [
        _make_groq_response("FAITHF"),
        _make_groq_response("FAITHFUL: Supported by the retrieved context."),
    ]

    with patch("time.sleep"):
        result = gate.judge("q", "a", ["context"], retries=1)

    assert result.faithful is True
    assert gate._client.chat.completions.create.call_count == 2


def test_gemini_uses_current_default_model():
    gate = LLMGate(provider="gemini", client=MagicMock())
    assert gate.model == "gemini-3.8-flash"


def test_judge_does_not_retry_permanent_provider_error():
    error = RuntimeError("project access denied")
    error.status_code = 403
    gate = _make_gate_with_response("")
    gate._client.chat.completions.create.side_effect = error

    result = gate.judge("q", "a", ["context"], retries=3)

    assert result.reason.startswith("[api error]")
    assert gate._client.chat.completions.create.call_count == 1


def test_confusion_excludes_llm_operational_errors():
    rows = [
        {
            "label": "correct",
            "expected_faithful": True,
            "lexical": True,
            "llm": False,
            "ensemble": False,
            "judge_error": True,
        },
        {
            "label": "wrong_fact",
            "expected_faithful": False,
            "lexical": False,
            "llm": False,
            "ensemble": False,
            "judge_error": False,
        },
    ]

    confusion = _confusion(rows, "llm")

    assert confusion["n_evaluated"] == 1
    assert confusion["n_operational_errors"] == 1
    assert confusion["tn"] == 1
    assert confusion["correct_pass_rate"] is None
    assert confusion["wrong_block_rate"] == 1.0


def test_store_result_replaces_resumed_operational_error():
    failed = {"id": 4, "label": "correct", "judge_error": True}
    results = [failed]
    positions = {(4, "correct"): 0}

    _store_result(
        results,
        positions,
        {"id": 4, "label": "correct", "judge_error": False},
    )

    assert len(results) == 1
    assert results[0]["judge_error"] is False


def test_context_snapshot_round_trip_and_source_validation(tmp_path, monkeypatch):
    labels = tmp_path / "labels.json"
    questions = tmp_path / "questions.json"
    labels.write_text('{"rows":[]}', encoding="utf-8")
    questions.write_text('{"results":[]}', encoding="utf-8")
    monkeypatch.setattr(evaluate_llm_gate, "LABELED", labels)
    monkeypatch.setattr(evaluate_llm_gate, "FAITH", questions)
    snapshot = tmp_path / "contexts.json"
    source_contexts = {2: ["policy evidence"], 9: ["partner evidence"]}

    evaluate_llm_gate._write_context_snapshot(snapshot, source_contexts)

    assert evaluate_llm_gate._load_context_snapshot(snapshot) == source_contexts
    labels.write_text('{"rows":[{"id":2}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        evaluate_llm_gate._load_context_snapshot(snapshot)
