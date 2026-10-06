"""The two-key free-tier models: Gemini (Google AI Studio, or Vertex AI on Google Cloud) and Groq, chained so a limit on one moves the
question to the next. Limit parsing and persistence live in src/v1/limits.py; this module only talks to the providers.

Keys (environment only, never logged, never in a URL): GEMINI_API_KEY (alias GOOGLE_API_KEY) and GROQ_API_KEY.
"""
from __future__ import annotations

import os
import time
from typing import Callable

from src.evaluation.cost_estimator import calculate_cost, estimate_tokens
from src.v1.limits import (Caps, LimitsExhausted, LimitTracker, LLMUnavailable, ModelNotAvailable, ProviderLimitError, gemini_limit_from_error,
                           get_usage_store, groq_limit_from_response)
from src.v1.llm import LLMResult
from src.v1.spend import SpendGuard


def _wants_json(system: str) -> bool:
    return "json" in system.lower()


def _adc_token() -> str:                                    # pragma: no cover - needs google-auth and credentials
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


class GeminiLLM:
    """Gemini through Google AI Studio's free tier, or through Vertex AI on Google Cloud (GEMINI_BACKEND=vertex: application-default
    credentials, no free tier, cost counted by the SpendGuard when the model is priced in config/pricing.yaml).

    A 429 RESOURCE_EXHAUSTED becomes a ProviderLimitError that says WHICH limit (per-minute / per-day / token size) and when it clears
    (RetryInfo.retryDelay, or the next midnight-Pacific reset for a daily quota)."""

    provider = "gemini"

    def __init__(self, model: str, tracker: LimitTracker | None = None, backend: str | None = None, client=None, api_key: str | None = None,
                 project: str | None = None, location: str | None = None, token_provider: Callable[[], str] | None = None, guard: SpendGuard | None = None):
        self.model = model
        self.backend = (backend or os.environ.get("GEMINI_BACKEND") or "ai_studio").lower()
        self.name = f"gemini:{model}" + (" (vertex)" if self.backend == "vertex" else "")
        self.tracker, self._client, self._key, self.guard = tracker, client, api_key, guard
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1"
        self._token_provider = token_provider

    @staticmethod
    def key_present() -> bool:
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=float(os.environ.get("P2_LLM_TIMEOUT_S", "60")))
        return self._client

    def _request(self) -> tuple[str, dict]:
        if self.backend == "vertex":
            if not self.project:
                raise LLMUnavailable("Vertex AI needs GOOGLE_CLOUD_PROJECT")
            token = (self._token_provider or _adc_token)()
            url = (f"https://{self.location}-aiplatform.googleapis.com/v1/projects/{self.project}/locations/{self.location}"
                   f"/publishers/google/models/{self.model}:generateContent")
            return url, {"Authorization": f"Bearer {token}"}
        key = self._key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMUnavailable("no Gemini key: set GEMINI_API_KEY (a Google AI Studio key)")
        return f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent", {"x-goog-api-key": key}

    def complete(self, system: str, user: str, *, max_tokens: int = 900, label: str = "ask") -> LLMResult:
        est = estimate_tokens(system) + estimate_tokens(user) + max_tokens
        if self.tracker:
            self.tracker.check("gemini", self.model, est)
        url, headers = self._request()
        gen: dict = {"temperature": 0, "maxOutputTokens": max_tokens}
        if _wants_json(system):
            gen["responseMimeType"] = "application/json"
        payload = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": gen}
        t0 = time.perf_counter()
        try:
            resp = self._http().post(url, json=payload, headers=headers)
        except Exception as exc:
            raise LLMUnavailable(f"Gemini call failed ({type(exc).__name__})") from exc
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code == 429:
            err = gemini_limit_from_error(body, self.model)
            if self.tracker:
                self.tracker.on_limit(err)
            raise err
        if resp.status_code in (400, 401, 403):
            msg = str((body or {}).get("error", {}).get("message", ""))[:160]
            if "api key" in msg.lower() or resp.status_code in (401, 403):
                raise LLMUnavailable(f"Gemini rejected the key or this model for it (HTTP {resp.status_code}); check GEMINI_API_KEY and the model name")
            raise LLMUnavailable(f"Gemini rejected the request (HTTP 400): {msg}")
        if resp.status_code == 404:
            if self.tracker:
                self.tracker.on_unavailable(self.model, "HTTP 404")
            raise ModelNotAvailable(f"Gemini model {self.model!r} is not available to this key (run: python -m scripts.check_free_models)")
        if resp.status_code != 200 or not isinstance(body, dict):
            raise LLMUnavailable(f"Gemini is unavailable (HTTP {resp.status_code})")
        cands = body.get("candidates") or []
        parts = (cands[0].get("content", {}).get("parts") or []) if cands else []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text:
            why = (body.get("promptFeedback") or {}).get("blockReason") or (cands[0].get("finishReason") if cands else None) or "empty"
            raise LLMUnavailable(f"Gemini returned no text ({why})")
        u = body.get("usageMetadata") or {}
        tin = int(u.get("promptTokenCount") or estimate_tokens(system + user))
        tout = int(u.get("candidatesTokenCount") or estimate_tokens(text))
        cost = 0.0
        if self.backend == "vertex" and self.guard is not None:
            try:
                cost = calculate_cost(tin, tout, self.model).total_cost_usd
            except Exception:
                cost = 0.0                                  # an unpriced model is counted in tokens only
            self.guard.record(label, self.name, tin, tout, cost)
        if self.tracker:
            self.tracker.on_success(self.model, tin + tout)
        return LLMResult(text, self.name, tin, tout, cost, round((time.perf_counter() - t0) * 1000, 1))


class GroqLLM:
    """A model on Groq's free plan through its OpenAI-compatible API. `retry-after` and the daily/minute `x-ratelimit-*` headers decide
    the cooldown; a 413 (request larger than the model's token limit) is reported as exactly that."""

    provider = "groq"
    BASE = "https://api.groq.com/openai/v1"

    def __init__(self, model: str, tracker: LimitTracker | None = None, client=None, api_key: str | None = None):
        self.model = model
        self.name = f"groq:{model}"
        self.tracker, self._client, self._key = tracker, client, api_key
        self.last_headers: dict = {}

    @staticmethod
    def key_present() -> bool:
        return bool(os.environ.get("GROQ_API_KEY"))

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=float(os.environ.get("P2_LLM_TIMEOUT_S", "60")))
        return self._client

    def complete(self, system: str, user: str, *, max_tokens: int = 900, label: str = "ask") -> LLMResult:
        est = estimate_tokens(system) + estimate_tokens(user) + max_tokens
        if self.tracker:
            self.tracker.check("groq", self.model, est)
        key = self._key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise LLMUnavailable("no Groq key: set GROQ_API_KEY")
        payload: dict = {"model": self.model, "temperature": 0, "max_tokens": max_tokens,
                         "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if _wants_json(system):
            payload["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        try:
            resp = self._http().post(f"{self.BASE}/chat/completions", json=payload, headers={"Authorization": f"Bearer {key}"})
        except Exception as exc:
            raise LLMUnavailable(f"Groq call failed ({type(exc).__name__})") from exc
        self.last_headers = {k.lower(): v for k, v in resp.headers.items() if k.lower().startswith(("x-ratelimit", "retry-after"))}
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code in (429, 413):
            err = groq_limit_from_response(resp.status_code, dict(resp.headers), body, self.model)
            if self.tracker:
                self.tracker.on_limit(err)
            raise err
        if resp.status_code in (401, 403):
            raise LLMUnavailable(f"Groq rejected the key (HTTP {resp.status_code}); check GROQ_API_KEY")
        err_msg = str((body or {}).get("error", {}).get("message", "")) if isinstance(body, dict) else ""
        if resp.status_code == 404 or (resp.status_code == 400 and "model" in err_msg.lower()):
            if self.tracker:
                self.tracker.on_unavailable(self.model, f"HTTP {resp.status_code}")
            raise ModelNotAvailable(f"Groq model {self.model!r} is not available to this key (run: python -m scripts.check_free_models)")
        if resp.status_code != 200 or not isinstance(body, dict):
            raise LLMUnavailable(f"Groq is unavailable (HTTP {resp.status_code})")
        try:
            text = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailable("Groq returned an unreadable response") from exc
        if not text.strip():
            raise LLMUnavailable("Groq returned no text")
        u = body.get("usage") or {}
        tin = int(u.get("prompt_tokens") or estimate_tokens(system + user))
        tout = int(u.get("completion_tokens") or estimate_tokens(text))
        if self.tracker:
            self.tracker.on_success(self.model, tin + tout)
        return LLMResult(text, self.name, tin, tout, 0.0, round((time.perf_counter() - t0) * 1000, 1))


class ChainLLM:
    """Try the free models in order. A limited model (the provider said so, or our soft cap says it would) is skipped and the next one
    answers; the result's `notes` say what was skipped and why. When none can answer, LimitsExhausted lists every reason and when the
    soonest clears, so the UI says exactly that instead of "unavailable"."""

    name = "free-chain"

    def __init__(self, members: list, tracker: LimitTracker, unconfigured: list[str] | None = None):
        self.members, self.tracker, self.unconfigured = members, tracker, unconfigured or []

    def complete(self, system: str, user: str, *, max_tokens: int = 900, label: str = "ask") -> LLMResult:
        limited: list[ProviderLimitError] = []
        other: list[str] = []
        for m in self.members:
            try:
                res = m.complete(system, user, max_tokens=max_tokens, label=label)
            except ProviderLimitError as e:
                limited.append(e)
                continue
            except LLMUnavailable as e:
                other.append(f"{m.name}: {e}")
                continue
            res.notes = [str(e) for e in limited] + other + list(res.notes)
            return res
        if not self.members:
            raise LLMUnavailable("no free model is configured (set GEMINI_API_KEY and/or GROQ_API_KEY)")
        raise LimitsExhausted(limited, other)

    def status(self) -> list[dict]:
        rows = [self.tracker.status(m.provider, m.model, True).to_dict() for m in self.members]
        for u in self.unconfigured:
            prov, rest = u.split(":", 1)
            rows.append({"provider": prov, "model": rest.split(" ")[0], "state": "no_key", "note": u})
        return rows


def load_free_config(path: str = "config/free_models.yaml") -> dict:
    import yaml

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_free_chain(store=None, config: dict | None = None, http_client=None) -> ChainLLM:
    cfg = config or load_free_config()
    caps = {c["model"]: Caps(c.get("rpm"), c.get("rpd"), c.get("tpm")) for c in cfg.get("chain", [])}
    tracker = LimitTracker(store or get_usage_store(), caps, float(cfg.get("soft_cap_fraction", 0.85)), cfg.get("default_rpm", 8))
    members, missing = [], []
    for c in cfg.get("chain", []):
        if c["provider"] == "gemini":
            if GeminiLLM.key_present() or os.environ.get("GEMINI_BACKEND") == "vertex":
                members.append(GeminiLLM(c["model"], tracker, client=http_client))
            else:
                missing.append(f"gemini:{c['model']} (no GEMINI_API_KEY)")
        elif c["provider"] == "groq":
            if GroqLLM.key_present():
                members.append(GroqLLM(c["model"], tracker, client=http_client))
            else:
                missing.append(f"groq:{c['model']} (no GROQ_API_KEY)")
    return ChainLLM(members, tracker, missing)
