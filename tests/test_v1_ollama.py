"""The local-model provider (src/v1/llm.py OllamaLLM): request shape, every failure becomes LLMUnavailable (so callers fall back to a labelled template),
a prompt that would not fit the context window is refused instead of silently truncated, and provider selection never auto-detects a local server."""
import json
import logging

import httpx
import pytest

from src.v1.llm import AnthropicLLM, LLMUnavailable, NoLLM, OllamaLLM, get_llm
from src.v1.spend import SpendGuard

JSON_SYSTEM = "Return ONLY JSON: {\"answer\": \"...\"}"


def make(handler, tmp_path, **kw):
    guard = SpendGuard(tmp_path / "spend.db")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaLLM(guard=guard, client=client, **kw), guard


def ok(content="{\"answer\": \"hi\"}", **extra):
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}, "prompt_eval_count": 120, "eval_count": 30, **extra})


def test_request_shape_result_and_zero_cost_spend_record(tmp_path):
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return ok()

    llm, guard = make(handler, tmp_path)
    res = llm.complete(JSON_SYSTEM, "QUESTION: how many cases are late?", max_tokens=300, label="ask")
    body = seen["body"]
    assert seen["url"] == "http://localhost:11434/api/chat"
    assert body["model"] == "qwen2.5:7b-instruct" and body["stream"] is False and body["format"] == "json"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["options"] == {"temperature": 0, "num_predict": 300, "num_ctx": 8192}
    assert (res.text, res.model, res.input_tokens, res.output_tokens, res.cost_usd) == ("{\"answer\": \"hi\"}", "ollama:qwen2.5:7b-instruct", 120, 30, 0.0)
    assert llm.name == "ollama:qwen2.5:7b-instruct"
    assert guard.spent("day") == 0.0                                 # local calls cost nothing, so the money caps never refuse them...
    with guard._conn() as c:                                         # ...but each call is still on the record
        assert c.execute("SELECT label, model, input_tokens, output_tokens, cost_usd FROM spend").fetchall() == [("ask", "ollama:qwen2.5:7b-instruct", 120, 30, 0.0)]


def test_json_mode_only_when_the_prompt_asks_for_json(tmp_path):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return ok("plain prose")

    llm, _ = make(handler, tmp_path)
    llm.complete("Summarise the evidence.", "x")
    assert "format" not in bodies[0]


def test_model_url_and_context_come_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:3b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-box:11434/")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "4096")
    seen = {}

    def handler(request):
        seen["url"], seen["opts"] = str(request.url), json.loads(request.content)["options"]
        return ok()

    llm, _ = make(handler, tmp_path)
    assert llm.complete(JSON_SYSTEM, "x").model == "ollama:llama3.2:3b"
    assert seen["url"] == "http://gpu-box:11434/api/chat" and seen["opts"]["num_ctx"] == 4096


def test_ollama_not_running_is_llm_unavailable_with_a_hint(tmp_path):
    def handler(request):
        raise httpx.ConnectError("refused")

    llm, _ = make(handler, tmp_path)
    with pytest.raises(LLMUnavailable, match=r"ConnectError.*is Ollama running at http://localhost:11434"):
        llm.complete(JSON_SYSTEM, "x")


def test_a_model_that_is_not_pulled_says_how_to_pull_it(tmp_path):
    llm, _ = make(lambda r: httpx.Response(404, json={"error": "model not found"}), tmp_path)
    with pytest.raises(LLMUnavailable, match="ollama pull qwen2.5:7b-instruct"):
        llm.complete(JSON_SYSTEM, "x")


@pytest.mark.parametrize("response", [httpx.Response(500, text="boom"), httpx.Response(200, text="not json"), httpx.Response(200, json={"unexpected": 1}),
                                      httpx.Response(200, json={"message": None})])
def test_server_errors_and_unreadable_bodies_are_llm_unavailable(tmp_path, response):
    llm, _ = make(lambda r: response, tmp_path)
    with pytest.raises(LLMUnavailable):
        llm.complete(JSON_SYSTEM, "x")


def test_a_prompt_that_would_not_fit_the_context_window_is_refused_not_truncated(tmp_path):
    calls = []
    llm, _ = make(lambda r: calls.append(r) or ok(), tmp_path, num_ctx=2048)
    with pytest.raises(LLMUnavailable, match="context window"):
        llm.complete(JSON_SYSTEM, "evidence " * 1000, max_tokens=900)
    assert calls == []                                               # nothing was sent: Ollama would have cut the prompt without saying so


def test_missing_token_counts_fall_back_to_an_estimate(tmp_path):
    llm, _ = make(lambda r: httpx.Response(200, json={"message": {"content": "{}"}}), tmp_path)
    res = llm.complete(JSON_SYSTEM, "x" * 400)
    assert res.input_tokens > 0 and res.output_tokens > 0


# ── provider selection ──
def clean_env(monkeypatch, tmp_path):
    for name in ("P2_LLM_PROVIDER", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("P2_SPEND_DB", str(tmp_path / "spend.db"))


def test_no_configuration_means_no_model_and_never_probes_a_local_server(monkeypatch, tmp_path):
    clean_env(monkeypatch, tmp_path)
    assert isinstance(get_llm(), NoLLM)


def test_ollama_is_selected_only_when_asked_for(monkeypatch, tmp_path):
    clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("P2_LLM_PROVIDER", "ollama")
    assert isinstance(get_llm(), OllamaLLM)
    monkeypatch.setenv("P2_LLM_PROVIDER", "OLLAMA")                  # case-insensitive, like the other values
    assert isinstance(get_llm(), OllamaLLM)


def test_anthropic_still_needs_its_key_and_an_unknown_provider_warns(monkeypatch, tmp_path, caplog):
    clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("P2_LLM_PROVIDER", "anthropic")
    assert isinstance(get_llm(), NoLLM)                              # asked for Anthropic, no key: still no model, never a half-configured client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    assert isinstance(get_llm(), AnthropicLLM)
    monkeypatch.setenv("P2_LLM_PROVIDER", "olama")
    with caplog.at_level(logging.WARNING, logger="src.v1.llm"):
        assert isinstance(get_llm(), NoLLM)
    assert any("olama" in r.getMessage() for r in caplog.records)


def test_the_no_model_message_names_the_local_option():
    with pytest.raises(LLMUnavailable, match="P2_LLM_PROVIDER=ollama"):
        NoLLM().complete("s", "u")
