"""Confirm what your two free keys can actually do, before anything depends on them.

    python -m scripts.check_free_models              # list the models each key can call and compare with config/free_models.yaml
    python -m scripts.check_free_models --probe      # also make ONE tiny call per configured model and show the limits the provider reports

Keys come from the environment only (GEMINI_API_KEY, alias GOOGLE_API_KEY; GROQ_API_KEY) and are never printed. A probe costs one request
from each model's free quota. What it tells you that no document can: whether a model name exists for YOUR key, and (Groq) your actual
remaining daily requests and per-minute tokens from the x-ratelimit-* headers. Gemini's numbers are shown in Google AI Studio; copy the
ones you want enforced locally into config/free_models.yaml.
"""
import argparse
import os
import sys

import httpx

from src.v1.free_llm import GeminiLLM, GroqLLM, load_free_config
from src.v1.limits import LLMUnavailable, ProviderLimitError


def gemini_models(key: str) -> dict[str, list[str]]:
    out, token = {}, None
    while True:
        params = {"pageSize": 200, **({"pageToken": token} if token else {})}
        r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", params=params, headers={"x-goog-api-key": key}, timeout=30)
        if r.status_code != 200:
            raise SystemExit(f"Gemini model list failed (HTTP {r.status_code}): {r.json().get('error', {}).get('message', '')[:160] if r.headers.get('content-type', '').startswith('application/json') else ''}")
        body = r.json()
        for m in body.get("models", []):
            out[m["name"].removeprefix("models/")] = m.get("supportedGenerationMethods", [])
        token = body.get("nextPageToken")
        if not token:
            return out


def groq_models(key: str) -> list[str]:
    r = httpx.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"Groq model list failed (HTTP {r.status_code})")
    return sorted(m["id"] for m in r.json().get("data", []))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="one tiny call per configured model (uses a little free quota)")
    a = ap.parse_args()
    cfg = load_free_config()
    gkey = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    qkey = os.environ.get("GROQ_API_KEY")
    print(f"Gemini key: {'present' if gkey else 'MISSING (set GEMINI_API_KEY from Google AI Studio)'} | Groq key: {'present' if qkey else 'MISSING (set GROQ_API_KEY from the Groq console)'}")
    if not gkey and not qkey:
        return 2
    have_g = gemini_models(gkey) if gkey else {}
    have_q = groq_models(qkey) if qkey else []
    problems = 0
    print("\nconfigured chain (config/free_models.yaml):")
    for c in cfg["chain"]:
        if c["provider"] == "gemini":
            if not gkey:
                state = "no key"
            elif c["model"] in have_g:
                state = "available" + ("" if "generateContent" in have_g[c["model"]] else " BUT does not support generateContent")
            else:
                state = "NOT available to this key"
        else:
            state = "no key" if not qkey else "available" if c["model"] in have_q else "NOT available to this key"
        problems += state.startswith("NOT") or "BUT" in state
        print(f"  {c['provider']:7s} {c['model']:34s} {state}")
    if gkey:
        flash = sorted(m for m, methods in have_g.items() if "generateContent" in methods and ("flash" in m or "lite" in m))
        print("\nGemini models your key can call that look free-tier friendly:", ", ".join(flash[:12]) or "none found")
    if qkey:
        print("Groq models your key can call:", ", ".join(m for m in have_q if not any(x in m for x in ("whisper", "tts", "guard", "playai")))[:600])
    if a.probe:
        from src.v1.llm import LLMResult  # noqa: F401
        print("\nprobe (one tiny request each):")
        for c in cfg["chain"]:
            llm = GeminiLLM(c["model"]) if c["provider"] == "gemini" and gkey else GroqLLM(c["model"]) if c["provider"] == "groq" and qkey else None
            if llm is None:
                continue
            try:
                res = llm.complete("Reply with JSON only: {\"ok\": true}", "ping", max_tokens=20, label="probe")
                extra = ""
                if isinstance(llm, GroqLLM) and llm.last_headers:
                    h = llm.last_headers
                    extra = f" | remaining today: {h.get('x-ratelimit-remaining-requests', '?')} of {h.get('x-ratelimit-limit-requests', '?')}, tokens/min limit {h.get('x-ratelimit-limit-tokens', '?')}"
                print(f"  OK   {llm.name:44s} {res.latency_ms:.0f} ms{extra}")
            except ProviderLimitError as e:
                print(f"  LIMIT {e}")
            except LLMUnavailable as e:
                problems += 1
                print(f"  FAIL {llm.name}: {e}")
    print("\nNext: copy the per-minute / per-day numbers from AI Studio (Gemini) and the Groq console limits page into config/free_models.yaml (rpm, rpd, tpm).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
