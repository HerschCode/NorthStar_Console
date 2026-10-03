"""LLM-based faithfulness judge — a drop-in alternative to the lexical claim_support gate.

The lexical gate (claim_support.py) cannot detect polarity flips:
  "included" vs "excluded", "required" vs "not required", "Yes" vs "No"
are all equally well-supported by lexical presence. This judge uses a language model
to catch exactly those cases by checking semantic entailment, not term overlap.

The judge returns a boolean (FAITHFUL / UNFAITHFUL) plus a one-sentence reason.
It makes a single LLM call per answer and is intentionally cheap: the prompt is short
and the only output is a label + one sentence.

Supported providers:
  from src.evaluation.llm_gate import LLMGate
  gate = LLMGate()                          # uses GROQ_API_KEY, gpt-oss-120b
  gate = LLMGate(model="openai/gpt-oss-20b")
  gate = LLMGate(provider="gemini")         # uses GEMINI_API_KEY, gemini-2.0-flash
  result = gate.judge(question, answer, chunks)
  # result.faithful: bool
  # result.reason:   str
  # result.raw:      str (full model output, for debugging)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

_SYSTEM = (
    "You are a faithfulness judge for a retrieval-augmented assistant. "
    "Your only job is to decide whether an ANSWER is faithfully supported by the RETRIEVED CONTEXT."
)

_PROMPT_TEMPLATE = """\
RULES:
1. Every factual claim (numbers, team names, roles, timelines, yes/no statements, included/excluded) \
must appear in or be directly inferable from the RETRIEVED CONTEXT.
2. No claim in the ANSWER may contradict the RETRIEVED CONTEXT.
3. The ANSWER must not introduce information that is absent from the RETRIEVED CONTEXT.
4. Polarity matters: "included" vs "excluded", "required" vs "optional", "Yes" vs "No" \
are different claims. If the answer says one and the context says the other, the answer is UNFAITHFUL.

RETRIEVED CONTEXT:
{context}

QUESTION:
{question}

ANSWER:
{answer}

Respond with exactly one line: FAITHFUL or UNFAITHFUL, a colon, and a single sentence explaining why.
Example: FAITHFUL: The answer correctly quotes the 5-business-day target from Section 2.1.
Example: UNFAITHFUL: The answer says 7 days but the context states the target is 5 business days.
"""


@dataclass
class JudgeResult:
    faithful: bool
    reason: str
    raw: str


def _parse(raw: str) -> JudgeResult:
    text = raw.strip()
    if text.startswith("{"):
        try:
            import json

            parsed = json.loads(text)
            faithful = parsed.get("faithful")
            if isinstance(faithful, bool):
                return JudgeResult(
                    faithful=faithful,
                    reason=str(parsed.get("reason", "")),
                    raw=raw,
                )
        except (json.JSONDecodeError, AttributeError):
            pass

    m = re.match(r"(FAITHFUL|UNFAITHFUL)\s*:?\s*(.*)", text, re.IGNORECASE | re.DOTALL)
    if m:
        label = m.group(1).upper()
        reason = m.group(2).strip()
        return JudgeResult(faithful=(label == "FAITHFUL"), reason=reason, raw=raw)
    return JudgeResult(faithful=False, reason=f"[parse error] {text[:120]}", raw=raw)


class LLMGate:
    """LLM-based faithfulness judge using Groq or Google Gemini."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        provider: str = "groq",
        client=None,
    ):
        if provider not in {"groq", "gemini"}:
            raise ValueError("provider must be 'groq' or 'gemini'")
        self.provider = provider
        self.model = model or (
            "openai/gpt-oss-120b" if provider == "groq" else "gemini-3.8-flash"
        )
        self._client = client or self._make_client(api_key)

    def _make_client(self, api_key: str | None):
        if self.provider == "groq":
            try:
                import groq  # type: ignore
            except ImportError as exc:
                raise ImportError("groq package required: pip install groq") from exc
            return groq.Groq(api_key=api_key or os.environ.get("GROQ_API_KEY"))

        try:
            from google import genai
        except ImportError as exc:
            raise ImportError("google-genai package required: pip install google-genai") from exc
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY (or GOOGLE_API_KEY) is required for Gemini")
        return genai.Client(api_key=key)

    def _generate(self, prompt: str) -> str:
        if self.provider == "gemini":
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={
                    "system_instruction": _SYSTEM,
                    "temperature": 0.0,
                    "max_output_tokens": 256,
                },
            )
            return (response.text or "").strip()

        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=256,
            temperature=0.0,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        return (response.choices[0].message.content or "").strip()

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        for value in (
            getattr(exc, "status_code", None),
            getattr(exc, "code", None),
            getattr(getattr(exc, "response", None), "status_code", None),
        ):
            if isinstance(value, int):
                return value
        return None

    def judge(self, question: str, answer: str, chunks: list[str], retries: int = 3) -> JudgeResult:
        """Return FAITHFUL/UNFAITHFUL for this answer given the retrieved chunks."""
        import time
        import re as _re
        context = "\n\n---\n\n".join(chunks) if chunks else "(no context retrieved)"
        prompt = _PROMPT_TEMPLATE.format(
            context=context[:4000],  # cap to keep tokens manageable
            question=question.strip(),
            answer=answer.strip(),
        )
        raw = ""
        for attempt in range(retries + 1):
            try:
                raw = self._generate(prompt)
                result = _parse(raw)
                if not result.reason.startswith("[parse error]"):
                    return result
                if attempt < retries:
                    time.sleep(2.0)
            except Exception as exc:
                status_code = self._status_code(exc)
                if status_code is not None and 400 <= status_code < 500 and status_code not in {408, 429}:
                    return JudgeResult(faithful=False, reason=f"[api error] {str(exc)[:120]}", raw="")
                delay = 15.0
                m = _re.search(r"try again in\s+([\d.]+)s", str(exc), _re.IGNORECASE)
                if m:
                    delay = float(m.group(1)) + 1.0
                if attempt < retries:
                    time.sleep(delay)
                else:
                    return JudgeResult(faithful=False, reason=f"[api error] {str(exc)[:120]}", raw="")
        return _parse(raw)
