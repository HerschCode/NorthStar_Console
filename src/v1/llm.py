"""Model access for the /v1 surfaces: one small interface, every call behind the SpendGuard.

- AnthropicLLM: Claude through the Messages API (static system prefix marked for prompt caching).
- NoLLM: no model configured -> LLMUnavailable; callers fall back to a deterministic, clearly labelled template.
- FakeLLM: scripted responses for tests (never used in production wiring).

Provider is chosen by P2_LLM_PROVIDER (anthropic | none; default anthropic when ANTHROPIC_API_KEY is set). The API key is
read from the environment by the SDK and is never logged or returned.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from src.evaluation.cost_estimator import calculate_cost, estimate_tokens
from src.v1.spend import SpendGuard


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str, *, max_tokens: int = 900, label: str = "ask") -> LLMResult: ...


class NoLLM:
    name = "none"

    def complete(self, system: str, user: str, *, max_tokens: int = 900, label: str = "ask") -> LLMResult:
        raise LLMUnavailable("no model configured (set ANTHROPIC_API_KEY and P2_LLM_PROVIDER=anthropic)")


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
    provider = os.environ.get("P2_LLM_PROVIDER", "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "none").lower()
    if provider == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicLLM()
    return NoLLM()
