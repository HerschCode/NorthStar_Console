"""Model access for the /v1 surfaces: one small interface, every call behind the SpendGuard.

- AnthropicLLM: Claude through the Messages API (static system prefix marked for prompt caching).
- OllamaLLM: a local model through Ollama (default qwen2.5:7b-instruct); free, offline, weaker. Labelled "ollama:<model>" in every result.
- NoLLM: no model configured -> LLMUnavailable; callers fall back to a deterministic, clearly labelled template.
- FakeLLM: scripted responses for tests (never used in production wiring).

Provider is chosen by P2_LLM_PROVIDER (free | gemini | groq | anthropic | ollama | none). Default: `free` (the Gemini + Groq free-tier chain, config/free_models.yaml)
when GEMINI_API_KEY or GROQ_API_KEY is set, else anthropic when ANTHROPIC_API_KEY is set, else none; ollama only when asked for. The API key is
read from the environment by the SDK and is never logged or returned.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from src.evaluation.cost_estimator import calculate_cost, estimate_tokens
from src.v1.spend import SpendGuard

log = logging.getLogger(__name__)


from src.v1.limits import LLMUnavailable  # noqa: E402  (defined with the limit errors so the two modules do not import each other)


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    notes: list = field(default_factory=list)   # what the free chain skipped on the way (limits), shown to the user


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str, *, max_tokens: int = 900, label: str = "ask") -> LLMResult: ...


class NoLLM:
    name = "none"

    def complete(self, system: str, user: str, *, max_tokens: int = 900, label: str = "ask") -> LLMResult:
        raise LLMUnavailable("no model configured (set ANTHROPIC_API_KEY, or run Ollama and set P2_LLM_PROVIDER=ollama)")


class AnthropicLLM:
    def __init__(self, model: str | None = None, guard: SpendGuard | None = None, client=None):
        import yaml

        pricing = yaml.safe_load(open("config/pricing.yaml", encoding="utf-8"))
        self.model = model or os.environ.get("P2_MODEL") or pricing.get("default_model", "claude-sonnet-5-5")
        self.name = f"anthropic:{self.model}"
        self.guard = guard or SpendGuard()
        self._client = client

    def _sdk(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()          # reads ANTHROPIC_API_KEY from the environment
        return self._client

    def complete(self, system: str, user: str, *, max_tokens: int = 900, label: str = "ask") -> LLMResult:
        est = calculate_cost(estimate_tokens(system) + estimate_tokens(user), max_tokens, self.model).total_cost_usd
        self.guard.check(est)                              # raises SpendExhausted before any money is spent
        t0 = time.perf_counter()
        try:
            resp = self._sdk().messages.create(
                model=self.model, max_tokens=max_tokens, temperature=0.0,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}])
        except Exception as exc:                           # network, auth, rate limit: never leak details
            raise LLMUnavailable(f"model call failed ({type(exc).__name__})") from exc
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        usage = getattr(resp, "usage", None)
        tin = int(getattr(usage, "input_tokens", 0) or 0)
        tout = int(getattr(usage, "output_tokens", 0) or 0)
        cost = calculate_cost(tin, tout, self.model).total_cost_usd
        self.guard.record(label, self.model, tin, tout, cost)
        return LLMResult(text, self.model, tin, tout, cost, round((time.perf_counter() - t0) * 1000, 1))


class OllamaLLM:
    """A model served by a local Ollama (default qwen2.5:7b-instruct): free, offline, and far weaker than Claude.

    It exists so the whole platform runs without an API key. Everything downstream is unchanged: the claim gate still verifies every claim against the evidence, and
    a model that cannot produce valid JSON gets a "no claims could be verified" note rather than a trusted answer. Results carry model="ollama:<name>" so a trace or
    an eval row can never be mistaken for a Claude run, and cost is 0, so the SpendGuard caps (which exist for money) do not apply, but each call is still recorded.

    Two things Ollama does silently that matter here: a prompt longer than the context window is TRUNCATED (so the model would answer from part of the evidence), and the
    default window is small. This provider sets the window explicitly and refuses a prompt that would not fit, instead of answering from a cut-off one.
    """

    def __init__(self, model: str | None = None, base_url: str | None = None, num_ctx: int | None = None, timeout_s: float | None = None,
                 guard: SpendGuard | None = None, client=None):
        self.model = model or os.environ.get("OLLAMA_MODEL") or "qwen2.5:7b-instruct"
        self.name = f"ollama:{self.model}"
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
        self.num_ctx = int(num_ctx or os.environ.get("OLLAMA_NUM_CTX") or 8192)
        self.timeout_s = float(timeout_s or os.environ.get("OLLAMA_TIMEOUT_S") or 180)
        self.guard = guard or SpendGuard()
        self._client = client

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client()
        return self._client

    def complete(self, system: str, user: str, *, max_tokens: int = 900, label: str = "ask") -> LLMResult:
        # chars/3 over-counts on purpose: numbers, ids and JSON tokenize worse than prose, and under-counting here means silent truncation
        if (len(system) + len(user)) / 3 + max_tokens > self.num_ctx:
            raise LLMUnavailable(f"prompt too long for the local model's {self.num_ctx}-token context window (raise OLLAMA_NUM_CTX)")
        payload = {"model": self.model, "stream": False, "keep_alive": "10m",
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": self.num_ctx}}
        if "json" in system.lower():                       # every /v1 prompt that wants JSON says so; the server then constrains decoding to valid JSON
            payload["format"] = "json"
        t0 = time.perf_counter()
        try:
            resp = self._http().post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout_s)
        except Exception as exc:                           # connection refused, timeout: Ollama is not running or the model is still loading
            raise LLMUnavailable(f"local model call failed ({type(exc).__name__}); is Ollama running at {self.base_url}?") from exc
        if resp.status_code == 404:
            raise LLMUnavailable(f"local model {self.model!r} is not installed (ollama pull {self.model})")
        if resp.status_code != 200:
            raise LLMUnavailable(f"local model call failed (HTTP {resp.status_code})")
        try:
            body = resp.json()
            text = body["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMUnavailable("local model returned an unreadable response") from exc
        tin = int(body.get("prompt_eval_count") or estimate_tokens(system) + estimate_tokens(user))
        tout = int(body.get("eval_count") or estimate_tokens(text))
        self.guard.record(label, self.name, tin, tout, 0.0)
        return LLMResult(text, self.name, tin, tout, 0.0, round((time.perf_counter() - t0) * 1000, 1))


class FakeLLM:
    """Test double: `script` is a callable (system, user) -> str, or a list of canned responses consumed in order."""
    name = "fake"

    def __init__(self, script: Callable[[str, str], str] | list[str], cost_per_call: float = 0.001):
        self.script, self.calls, self.cost = script, [], cost_per_call

    def complete(self, system: str, user: str, *, max_tokens: int = 900, label: str = "ask") -> LLMResult:
        self.calls.append({"system": system, "user": user, "label": label})
        text = self.script(system, user) if callable(self.script) else self.script.pop(0)
        return LLMResult(text, "fake", estimate_tokens(system + user), estimate_tokens(text), self.cost, 1.0)


def get_llm() -> LLM:
    from src.v1.free_llm import ChainLLM, GeminiLLM, GroqLLM, build_free_chain

    free_keys = GeminiLLM.key_present() or GroqLLM.key_present()
    provider = os.environ.get("P2_LLM_PROVIDER", "free" if free_keys else "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "none").lower()
    if provider in ("free", "gemini", "groq"):             # the two-key free-tier chain (models and order in config/free_models.yaml)
        chain: ChainLLM = build_free_chain()
        if provider in ("gemini", "groq"):
            chain.members = [m for m in chain.members if m.provider == provider]
        return chain
    if provider == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicLLM()
    if provider == "ollama":                               # only on request: never auto-detected, so a deployed instance does not probe localhost
        return OllamaLLM()
    if provider not in ("anthropic", "none"):
        log.warning("unknown P2_LLM_PROVIDER %r: no model is configured (use anthropic, ollama or none)", provider)
    return NoLLM()
