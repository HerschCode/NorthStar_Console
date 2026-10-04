"""POST /v1/briefing: the executive brief, every sentence linked to the P1 fact ids it uses.

Input is P1's `/v1/briefing` facts ({id, fact, metric}). Output: up to 3 prioritised items. Deterministic template when no
model is available (labelled). With a model, the prose is checked: every sentence must cite known fact ids and any figure
in it must appear in a cited fact; otherwise the template is used instead. Cached per as_of.
"""
from __future__ import annotations

import json

from src.v1.ask import _parse_json
from src.v1.evidence import number_supported, numbers_in
from src.v1.llm import LLM, LLMUnavailable

_CACHE: dict = {}
SYSTEM = """You write a 3-item executive brief for a procurement control tower from numbered facts ONLY. Return ONLY JSON:
{"items": [{"title": "...", "sentences": [{"text": "...", "fact_ids": ["open", "late"]}]}]}.
Rules: at most 3 items, most urgent first; every sentence cites fact ids; copy figures exactly; no new facts, no causes."""
PRIORITY = ["late", "risk", "loss", "top1", "open"]


def template_brief(facts: list[dict]) -> list[dict]:
    by = {f["id"]: f for f in facts}
    items = []
    order = [i for i in PRIORITY if i in by] + [f["id"] for f in facts if f["id"] not in PRIORITY]
    titles = {"late": "Cases already past their target", "risk": "Open cases at risk", "loss": "Simulated exposure",
              "top1": "Highest expected-loss case", "open": "Open workload"}
    for fid in order[:3]:
        items.append({"title": titles.get(fid, fid), "sentences": [{"text": by[fid]["fact"], "fact_ids": [fid]}]})
    return items


def _valid(items, facts: list[dict]) -> bool:
    by = {f["id"]: f for f in facts}
    if not isinstance(items, list) or not items or len(items) > 3:
        return False
    for it in items:
        sents = it.get("sentences") if isinstance(it, dict) else None
        if not sents:
            return False
        for s in sents:
            ids = s.get("fact_ids") or []
            if not ids or any(i not in by for i in ids):
                return False
            pool = [n for i in ids for n in numbers_in(by[i]["fact"])]
            if any(not number_supported(n, pool) for n in numbers_in(s.get("text", ""))):
                return False
    return True


def make_brief(p1_facts: dict, llm: LLM, use_cache: bool = True) -> dict:
    facts = p1_facts.get("facts") or []
    as_of = p1_facts.get("as_of")
    if use_cache and as_of in _CACHE:
        return {**_CACHE[as_of], "cached": True}
    items, source, cost, model = None, "template", 0.0, "template-fallback"
    try:
        res = llm.complete(SYSTEM, "FACTS:\n" + "\n".join(f"[{f['id']}] {f['fact']}" for f in facts), max_tokens=700, label="briefing")
        parsed = _parse_json(res.text) or {}
        if _valid(parsed.get("items"), facts):
            items, source, cost, model = parsed["items"], "model", res.cost_usd, res.model
    except LLMUnavailable:
        pass
    if items is None:
        items = template_brief(facts)
    out = {"as_of": as_of, "items": items, "source": source, "model": model, "cost_usd": cost, "fact_ids_used": sorted({i for it in items for s in it["sentences"] for i in s["fact_ids"]}),
           "facts": facts, "cached": False}
    if source == "template":
        out["label"] = "Deterministic template (no model, or the model output failed the fact-id check)."
    _CACHE[as_of] = {k: v for k, v in out.items() if k != "cached"}
    return out
