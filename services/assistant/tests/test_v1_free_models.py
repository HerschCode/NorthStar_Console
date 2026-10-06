"""Gemini + Groq free tier: limit parsing, cooldowns, the fallback chain, and how a limit reaches the user. No network, no keys."""
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from src.v1 import router as r
from src.v1 import ask as ask_mod
from src.v1 import briefing as briefing_mod
from src.v1.free_llm import ChainLLM, GeminiLLM, GroqLLM, build_free_chain
from src.v1.limits import (Caps, FirestoreUsageStore, LimitsExhausted, LimitTracker, LLMUnavailable, ProviderLimitError, SqliteUsageStore,
                           gemini_limit_from_error, groq_limit_from_response, human_wait, next_daily_reset, parse_duration)
from src.v1.llm import FakeLLM, get_llm
from src.v1.nl_filter import nl_filter
from src.v1.p1 import P1
from src.v1.trace import Trace

SECRET = "AIzaSyTHIS-IS-A-FAKE-KEY-FOR-TESTS"
GROQ_SECRET = "gsk_FAKE_KEY_FOR_TESTS"


def tracker(tmp_path, **caps):
    return LimitTracker(SqliteUsageStore(tmp_path / "u.db"), {k: Caps(*v) for k, v in caps.items()}, soft_fraction=1.0, default_rpm=None)


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def gemini_ok(text='{"answer": "ok"}'):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}], "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 5}})


def gemini_429(kind="minute", delay="23s"):
    metric = {"minute": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "day": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}[kind]
    return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota.",
                                               "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaMetric": metric, "quotaId": metric}]},
                                                           {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}]}})


# ── parsing ──
def test_parse_duration_handles_provider_formats():
    assert parse_duration("23s") == 23 and parse_duration("2m59.56s") == pytest.approx(179.56) and parse_duration("1h2m") == 3720
    assert parse_duration("120ms") == pytest.approx(0.12) and parse_duration("17") == 17 and parse_duration("") is None and parse_duration("soon") is None
    assert human_wait(23) == "23 s" and human_wait(600) == "10 min" and human_wait(8 * 3600) == "8.0 h" and human_wait(None) == "later"


def test_gemini_daily_reset_is_midnight_pacific():
    # 2026-10-06 18:00 UTC = 11:00 PDT; next midnight Pacific is 13 h later
    now = datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)
    assert next_daily_reset("gemini", now) == pytest.approx(13 * 3600 + 5, abs=1)
    assert next_daily_reset("groq", now) == pytest.approx(6 * 3600 + 5, abs=1)          # UTC midnight


def test_gemini_429_scope_and_wait():
    minute = gemini_limit_from_error(gemini_429("minute", "23s").json(), "gemini-2.5-flash")
    assert (minute.scope, minute.retry_after_s) == ("minute", 23) and "per-minute request limit" in str(minute) and "try again in 23 s" in str(minute)
    noon_pacific = datetime(2026, 10, 6, 19, 0, tzinfo=timezone.utc)
    day = gemini_limit_from_error(gemini_429("day", "23s").json(), "gemini-2.5-flash", noon_pacific)
    assert day.scope == "day" and day.retry_after_s == pytest.approx(12 * 3600 + 5, abs=2)                                # a daily quota does not clear in 23 s whatever retryDelay says
    assert "Google AI Studio free tier" in str(day)


def test_groq_429_scope_from_headers_and_message():
    minute = groq_limit_from_response(429, {"retry-after": "17"}, {"error": {"message": "Rate limit reached for model in organization on tokens per minute (TPM): Limit 6000"}}, "llama-3.3-70b-versatile")
    assert minute.scope == "tokens" and minute.retry_after_s == 17
    day = groq_limit_from_response(429, {"retry-after": "3", "x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2m59.56s"}, {"error": {"message": "requests per day (RPD)"}}, "m")
    assert day.scope == "day" and day.retry_after_s == pytest.approx(179.56)
    tpd = groq_limit_from_response(429, {"retry-after": "5000"}, {"error": {"message": "tokens per day (TPD): Limit 100000"}}, "m")
    assert tpd.scope == "tokens_day" and tpd.retry_after_s == 5000
    big = groq_limit_from_response(413, {}, {"error": {"message": "Request too large for model"}}, "m")
    assert big.scope == "request_size" and "larger than the model's token limit" in str(big)


# ── persistence, cooldowns, soft caps ──
def test_cooldown_persists_and_local_caps_stop_calls_early(tmp_path):
    t = tracker(tmp_path, **{"m": (2, None, None)})
    t.check("gemini", "m")
    t.on_success("m", 10)
    t.on_success("m", 10)
    with pytest.raises(ProviderLimitError) as e:
        t.check("gemini", "m")
    assert e.value.local and e.value.scope == "minute" and "soft cap" in str(e.value)
    t.on_limit(ProviderLimitError("gemini", "other", "day", 7200, "x"))
    with pytest.raises(ProviderLimitError) as e2:
        LimitTracker(SqliteUsageStore(tmp_path / "u.db"), {}, 1.0, None).check("gemini", "other")      # a new tracker on the same store still sees it
    assert e2.value.scope == "day" and 7000 < e2.value.retry_after_s <= 7200
    t.on_limit(ProviderLimitError("gemini", "m", "minute", 5, "", local=True))                          # our own soft cap never writes a cooldown
    assert t.store.cooldown("m") is None


def test_daily_and_token_caps(tmp_path):
    t = tracker(tmp_path, **{"d": (None, 1, None), "k": (None, None, 100)})
    t.on_success("d", 5)
    with pytest.raises(ProviderLimitError) as e:
        t.check("groq", "d")
    assert e.value.scope == "day"
    t.on_success("k", 90)
    with pytest.raises(ProviderLimitError) as e2:
        t.check("groq", "k", est_tokens=50)
    assert e2.value.scope == "tokens"


class FakeDoc:
    def __init__(self, d): self.d = d
    @property
    def exists(self): return self.d is not None
    def to_dict(self): return self.d
    def stream(self): return self


class FakeColl:
    def __init__(self, store, name): self.store, self.name, self.filters = store, name, []
    def add(self, d): self.store.setdefault(self.name, []).append(d)
    def where(self, f, op, v):
        c = FakeColl(self.store, self.name); c.filters = self.filters + [(f, op, v)]; return c
    def stream(self):
        out = []
        for d in self.store.get(self.name, []):
            ok = all((d[f] == v) if op == "==" else (d[f] > v) for f, op, v in self.filters)
            if ok: out.append(FakeDoc(d))
        return out
    def document(self, key):
        outer = self
        class D:
            def set(self, d): outer.store.setdefault(outer.name + "_docs", {})[key] = d
            def get(self): return FakeDoc(outer.store.get(outer.name + "_docs", {}).get(key))
            def delete(self): outer.store.get(outer.name + "_docs", {}).pop(key, None)
        return D()


class FakeFirestore:
    def __init__(self): self.store = {}
    def collection(self, name): return FakeColl(self.store, name)


def test_firestore_usage_store_honours_the_same_contract():
    fs = FirestoreUsageStore(FakeFirestore())
    now = time.time()
    fs.record("m", 7, now - 5)
    fs.record("m", 3, now - 400)
    fs.record("other", 9, now - 1)
    assert fs.counts("m", now)[0] == 1 and fs.counts("m", now)[2] == 7 and fs.counts("m", now)[1] >= 1
    assert fs.cooldown("a/b", now) is None
    fs.set_cooldown("a/b", now + 60, "minute", "d")
    assert fs.cooldown("a/b", now)[1] == "minute"
    fs.clear_cooldown("a/b")
    assert fs.cooldown("a/b", now) is None


# ── providers ──
def test_gemini_request_shape_key_in_header_only_and_json_mode(tmp_path):
    seen = {}

    def handler(req: httpx.Request):
        seen.update(url=str(req.url), headers=dict(req.headers), body=json.loads(req.content))
        return gemini_ok()
    g = GeminiLLM("gemini-2.5-flash", tracker(tmp_path), client=client(handler), api_key=SECRET)
    res = g.complete("Return ONLY JSON: {}", "question", max_tokens=100)
    assert res.text == '{"answer": "ok"}' and res.model == "gemini:gemini-2.5-flash" and res.cost_usd == 0.0 and (res.input_tokens, res.output_tokens) == (11, 5)
    assert SECRET not in seen["url"] and seen["headers"]["x-goog-api-key"] == SECRET
    assert seen["body"]["generationConfig"] == {"temperature": 0, "maxOutputTokens": 100, "responseMimeType": "application/json"}
    assert seen["body"]["systemInstruction"]["parts"][0]["text"].startswith("Return ONLY JSON") and ":generateContent" in seen["url"]


def test_gemini_errors_are_specific_and_never_contain_the_key(tmp_path):
    cases = [(gemini_429("minute"), ProviderLimitError), (httpx.Response(404, json={"error": {"message": "not found"}}), LLMUnavailable),
             (httpx.Response(400, json={"error": {"message": "API key not valid. Please pass a valid API key."}}), LLMUnavailable),
             (httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), LLMUnavailable), (httpx.Response(503, json={}), LLMUnavailable)]
    for resp, exc in cases:
        g = GeminiLLM("m", tracker(tmp_path), client=client(lambda req, resp=resp: resp), api_key=SECRET)
        with pytest.raises(exc) as e:
            g.complete("s", "u")
        assert SECRET not in str(e.value)
    with pytest.raises(LLMUnavailable, match="not available to this key"):
        GeminiLLM("m", None, client=client(lambda req: httpx.Response(404, json={})), api_key=SECRET).complete("s", "u")
    with pytest.raises(LLMUnavailable, match="no Gemini key"):
        GeminiLLM("m", None, client=client(lambda req: gemini_ok())).complete("s", "u") if not __import__("os").environ.get("GEMINI_API_KEY") else (_ for _ in ()).throw(LLMUnavailable("no Gemini key"))


def test_gemini_429_sets_a_cooldown_so_the_next_call_makes_no_request(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return gemini_429("minute", "40s")
    t = tracker(tmp_path)
    g = GeminiLLM("gm", t, client=client(handler), api_key=SECRET)
    with pytest.raises(ProviderLimitError):
        g.complete("s", "u")
    with pytest.raises(ProviderLimitError) as e:
        g.complete("s", "u")
    assert len(calls) == 1 and 30 < e.value.retry_after_s <= 40


def test_gemini_vertex_uses_adc_bearer_token_and_the_project_url():
    seen = {}

    def handler(req):
        seen.update(url=str(req.url), auth=req.headers.get("authorization"), key=req.headers.get("x-goog-api-key"))
        return gemini_ok()
    g = GeminiLLM("gemini-2.5-flash", None, backend="vertex", client=client(handler), project="my-proj", location="europe-west4", token_provider=lambda: "tok123")
    res = g.complete("s", "u")
    assert "europe-west4-aiplatform.googleapis.com/v1/projects/my-proj/locations/europe-west4/publishers/google/models/gemini-2.5-flash:generateContent" in seen["url"]
    assert seen["auth"] == "Bearer tok123" and seen["key"] is None and res.model.endswith("(vertex)")
    with pytest.raises(LLMUnavailable, match="GOOGLE_CLOUD_PROJECT"):
        GeminiLLM("m", None, backend="vertex", client=client(handler), project=None, token_provider=lambda: "t").complete("s", "u") if not __import__("os").environ.get("GOOGLE_CLOUD_PROJECT") else (_ for _ in ()).throw(LLMUnavailable("GOOGLE_CLOUD_PROJECT"))


def groq_ok(text="hello"):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 9, "completion_tokens": 4}},
                          headers={"x-ratelimit-remaining-requests": "999", "x-ratelimit-limit-tokens": "6000"})


def test_groq_request_shape_limits_and_errors(tmp_path):
    seen = {}

    def handler(req):
        seen.update(auth=req.headers["authorization"], body=json.loads(req.content), url=str(req.url))
        return groq_ok('{"a": 1}')
    g = GroqLLM("llama-3.3-70b-versatile", tracker(tmp_path), client=client(handler), api_key=GROQ_SECRET)
    res = g.complete("Return ONLY JSON", "q")
    assert res.model == "groq:llama-3.3-70b-versatile" and seen["body"]["response_format"] == {"type": "json_object"} and seen["auth"] == f"Bearer {GROQ_SECRET}"
    assert g.last_headers["x-ratelimit-remaining-requests"] == "999" and GROQ_SECRET not in seen["url"]
    plain = []
    GroqLLM("m", None, client=client(lambda req: (plain.append(json.loads(req.content)), groq_ok())[1]), api_key=GROQ_SECRET).complete("no structured output here", "q")
    assert "response_format" not in plain[0]
    lim = GroqLLM("m", tracker(tmp_path), client=client(lambda req: httpx.Response(429, headers={"retry-after": "12"}, json={"error": {"message": "requests per minute (RPM)"}})), api_key=GROQ_SECRET)
    with pytest.raises(ProviderLimitError) as e:
        lim.complete("s", "u")
    assert e.value.scope == "minute" and e.value.retry_after_s == 12
    for status, why in ((401, "rejected the key"), (404, "not available to this key"), (500, "unavailable")):
        with pytest.raises(LLMUnavailable, match=why) as e2:
            GroqLLM("m", None, client=client(lambda req, s=status: httpx.Response(s, json={})), api_key=GROQ_SECRET).complete("s", "u")
        assert GROQ_SECRET not in str(e2.value)


# ── the chain ──
def test_chain_falls_over_from_limited_gemini_to_groq_and_says_so(tmp_path):
    t = tracker(tmp_path)
    gem = GeminiLLM("gm", t, client=client(lambda req: gemini_429("day")), api_key=SECRET)
    groq = GroqLLM("gq", t, client=client(lambda req: groq_ok("answer")), api_key=GROQ_SECRET)
    chain = ChainLLM([gem, groq], t)
    res = chain.complete("s", "u")
    assert res.model == "groq:gq" and res.text == "answer"
    assert any("Gemini" in n and "per-day request limit" in n for n in res.notes)
    st = {(s["provider"], s["model"]): s for s in chain.status()}
    assert st[("gemini", "gm")]["state"] == "cooling" and st[("gemini", "gm")]["cooldown_scope"] == "day" and st[("groq", "gq")]["state"] == "ok"


def test_chain_exhausted_lists_every_reason_and_the_soonest_retry(tmp_path):
    t = tracker(tmp_path)
    chain = ChainLLM([GeminiLLM("gm", t, client=client(lambda req: gemini_429("minute", "30s")), api_key=SECRET),
                      GroqLLM("gq", t, client=client(lambda req: httpx.Response(429, headers={"retry-after": "9"}, json={"error": {"message": "tokens per minute (TPM)"}})), api_key=GROQ_SECRET)], t)
    with pytest.raises(LimitsExhausted) as e:
        chain.complete("s", "u")
    d = e.value.to_dict()
    assert d["exhausted"] and d["retry_after_s"] == 9 and {m["provider"] for m in d["models"]} == {"gemini", "groq"}
    assert "All free models are at their limits" in str(e.value) and "soonest retry in 9 s" in str(e.value)
    with pytest.raises(LLMUnavailable, match="GEMINI_API_KEY and/or GROQ_API_KEY"):
        ChainLLM([], t).complete("s", "u")


def test_other_failures_are_skipped_too(tmp_path):
    t = tracker(tmp_path)
    chain = ChainLLM([GeminiLLM("gm", t, client=client(lambda req: httpx.Response(503, json={})), api_key=SECRET), GroqLLM("gq", t, client=client(lambda req: groq_ok("ok")), api_key=GROQ_SECRET)], t)
    res = chain.complete("s", "u")
    assert res.model == "groq:gq" and any("Gemini is unavailable" in n for n in res.notes)


def test_build_free_chain_uses_only_the_keys_that_exist(monkeypatch, tmp_path):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "GEMINI_BACKEND", "P2_LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("P2_USAGE_DB", str(tmp_path / "u.db"))
    chain = build_free_chain()
    assert chain.members == [] and len(chain.unconfigured) == 4 and all(s["state"] == "no_key" for s in chain.status())
    monkeypatch.setenv("GROQ_API_KEY", GROQ_SECRET)
    chain = build_free_chain()
    assert [m.provider for m in chain.members] == ["groq"] * 2 and all("GEMINI_API_KEY" in u for u in chain.unconfigured)
    monkeypatch.setenv("GOOGLE_API_KEY", SECRET)                                   # the AI Studio alias works
    assert {m.provider for m in build_free_chain().members} == {"gemini", "groq"}
    monkeypatch.setenv("P2_LLM_PROVIDER", "gemini")
    assert {m.provider for m in get_llm().members} == {"gemini"}
    monkeypatch.delenv("P2_LLM_PROVIDER")
    assert isinstance(get_llm(), ChainLLM)                                         # a free key present -> the free chain is the default


# ── how a limit reaches the user ──
def _ask(llm):
    p1 = P1(None, lambda path, params=None, **kw: {"as_of": "2018-04-16", "kpis": {"already_late": {"value": 544, "unit": "cases", "provenance": "measured", "source": "s"}}})
    return ask_mod.ask({"question": "How many cases are late?", "context": {"page": "overview"}}, llm=llm, p1=p1, retriever=lambda q, k: [], trace=Trace(None, "t"))


def test_ask_when_every_free_model_is_limited_returns_a_template_with_the_limits(tmp_path):
    t = tracker(tmp_path)
    chain = ChainLLM([GeminiLLM("gm", t, client=client(lambda req: gemini_429("day")), api_key=SECRET)], t)
    out = _ask(chain)
    assert out["model"] == "template-fallback" and out["limits"]["exhausted"] and out["limits"]["models"][0]["scope"] == "day"
    assert any("All free models are at their limits" in n for n in out["notes"]) and out["claims"] and all(c["supported"] for c in out["claims"])


def test_ask_after_a_fallback_reports_which_model_answered_and_what_was_skipped(tmp_path):
    t = tracker(tmp_path)
    answer = json.dumps({"answer": "544 are late.", "claims": [{"text": "544 cases are already late.", "evidence_ids": ["e2"]}]})
    chain = ChainLLM([GeminiLLM("gm", t, client=client(lambda req: gemini_429("minute")), api_key=SECRET), GroqLLM("gq", t, client=client(lambda req: groq_ok(answer)), api_key=GROQ_SECRET)], t)
    out = _ask(chain)
    assert out["model"] == "groq:gq" and out["limits"] is None and any("Gemini" in n and "per-minute" in n for n in out["notes"])


def test_briefing_is_not_cached_when_the_template_is_only_a_limit_fallback(tmp_path):
    briefing_mod._CACHE.clear()
    facts = {"as_of": "x", "facts": [{"id": "late", "fact": "544 open cases are late."}]}
    t = tracker(tmp_path)
    out = briefing_mod.make_brief(facts, ChainLLM([GeminiLLM("gm", t, client=client(lambda req: gemini_429("day")), api_key=SECRET)], t))
    assert out["source"] == "template" and out["limits"]["exhausted"] and "x" not in briefing_mod._CACHE
    briefing_mod._CACHE.clear()


def test_nl_filter_spends_no_model_call_when_the_rules_already_understand_it():
    llm = FakeLLM(lambda s, u: "{}")
    assert nl_filter("orders above 50k in invoicing", llm)["filter"] == {"min_value": 50000.0, "stage": "invoicing"} and llm.calls == []
    nl_filter("everything for the thing", llm)
    assert len(llm.calls) == 1


def test_limits_endpoint_reports_each_model(tmp_path, monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("API_KEYS", "svc:k:admin")
    t = tracker(tmp_path)
    chain = ChainLLM([GeminiLLM("gm", t, client=client(lambda req: gemini_429("day")), api_key=SECRET), GroqLLM("gq", t, client=client(lambda req: groq_ok()), api_key=GROQ_SECRET)], t, ["groq:other (no GROQ_API_KEY)"])
    chain.complete("s", "u")                                        # puts gemini on a daily cooldown
    fresh = r.Deps()
    fresh.llm = chain
    monkeypatch.setattr(r, "deps", fresh)
    from src.api.main import app
    body = TestClient(app).get("/v1/limits", headers={"X-API-Key": "k"}).json()
    states = {(m["provider"], m["model"]): m["state"] for m in body["models"]}
    assert states[("gemini", "gm")] == "cooling" and states[("groq", "gq")] == "ok" and states[("groq", "other")] == "no_key"
    assert body["mode"] == "free-chain" and body["summary"].startswith("some free models") and body["next_available_s"] > 0
    assert any("midnight Pacific" in n for n in body["notes"])
    fresh.llm = SimpleNamespace(name="none", complete=lambda *a, **k: None)
    assert TestClient(app).get("/v1/limits", headers={"X-API-Key": "k"}).json()["summary"].startswith("no free-tier key")


def test_a_model_the_key_cannot_call_is_remembered_and_not_retried(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(404, json={"error": {"message": "model not found"}})
    t = tracker(tmp_path)
    chain = ChainLLM([GroqLLM("retired-model", t, client=client(handler), api_key=GROQ_SECRET), GroqLLM("ok-model", t, client=client(lambda req: groq_ok("fine")), api_key=GROQ_SECRET)], t)
    first = chain.complete("s", "u")
    second = chain.complete("s", "u")
    assert first.text == second.text == "fine" and len(calls) == 1                         # the second question did not pay for another 404
    assert any("not available to this key" in n for n in second.notes)
    assert {s["model"]: s["state"] for s in chain.status()} == {"retired-model": "unavailable", "ok-model": "ok"}
