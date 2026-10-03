"""LLM-based faithfulness judge — a drop-in alternative to the lexical claim_support gate.

The lexical gate (claim_support.py) cannot detect polarity flips:
  "included" vs "excluded", "required" vs "not required", "Yes" vs "No"
are all equally well-supported by lexical presence. This judge uses a language model
to catch exactly those cases by checking semantic entailment, not term overlap.

The judge returns a boolean (FAITHFUL / UNFAITHFUL) plus a one-sentence reason.
It makes a single LLM call per answer and is intentionally cheap: the prompt is short
and the only output is a label + one sentence.

Supported providers (OpenAI-compatible Groq API):
  from src.evaluation.llm_gate import LLMGate
  gate = LLMGate()                          # uses GROQ_API_KEY, gpt-oss-120b
  gate = LLMGate(model="openai/gpt-oss-20b")
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
    m = re.match(r"(FAITHFUL|UNFAITHFUL)\s*:?\s*(.*)", text, re.IGNORECASE | re.DOTALL)
    if m:
        label = m.group(1).upper()
        reason = m.group(2).strip()
        return JudgeResult(faithful=(label == "FAITHFUL"), reason=reason, raw=raw)
    upper = text.upper()
    # Handle truncated prefixes (API returning partial response under load)
    if upper.startswith("FAITHF"):   # "FAITHF..." → FAITHFUL
        return JudgeResult(faithful=True, reason=f"[truncated] {text[:120]}", raw=raw)
    if upper.startswith("UNFAITHF"):  # "UNFAITHF..." → UNFAITHFUL
        return JudgeResult(faithful=False, reason=f"[truncated] {text[:120]}", raw=raw)
    # Fallback: look for the keyword anywhere in the response
    if "UNFAITHFUL" in upper:
        return JudgeResult(faithful=False, reason=text[:200], raw=raw)
    if "FAITHFUL" in upper:
        return JudgeResult(faithful=True, reason=text[:200], raw=raw)
    # Cannot parse — treat as UNFAITHFUL (conservative)
    return JudgeResult(faithful=False, reason=f"[parse error] {text[:120]}", raw=raw)


class LLMGate:
    """LLM-based faithfulness judge using a Groq-hosted model."""

    def __init__(
        self,
        model: str = "openai/gpt-oss-120b",
        api_key: str | None = None,
    ):
        try:
            import groq  # type: ignore
        except ImportError as exc:
            raise ImportError("groq package required: pip install groq") from exc
        self._client = groq.Groq(api_key=api_key or os.environ.get("GROQ_API_KEY"))
        self.model = model

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
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ]
        raw = ""
        for attempt in range(retries + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    max_tokens=120,
                    temperature=0.0,
                    messages=messages,
                )
                raw = (resp.choices[0].message.content or "").strip()
                upper = raw.upper()
                if raw and ("FAITHFUL" in upper or upper.startswith("FAITHF") or upper.startswith("UNFAITHF")):
                    return _parse(raw)
                if attempt < retries:
                    time.sleep(2.0)  # short wait before retrying empty/truncated response
            except Exception as exc:
                # Extract "retry in Xs" from Groq rate-limit messages
                delay = 15.0
                m = _re.search(r"try again in\s+([\d.]+)s", str(exc), _re.IGNORECASE)
                if m:
                    delay = float(m.group(1)) + 1.0
                if attempt < retries:
                    time.sleep(delay)
                else:
                    return JudgeResult(faithful=False, reason=f"[api error] {str(exc)[:120]}", raw="")
        return _parse(raw)  # return last attempt even if unparseable
