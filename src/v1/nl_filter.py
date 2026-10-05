"""POST /v1/nl-filter: natural language -> a validated filter for P1's /v1/queue or /v1/suppliers.

The model (or, with no model, a deterministic rule parser) only produces parameters; they are validated against a JSON
schema and anything outside it is REJECTED, never forwarded. The LLM never touches the data: it translates words to
parameters that the P1 API then enforces.
"""
from __future__ import annotations

import json
import re

import jsonschema

from src.v1.ask import _parse_json
from src.v1.llm import LLM, LLMUnavailable

STAGES = ["order", "approval", "changes", "receipt", "invoicing", "block", "payment"]
SCHEMAS = {
    "queue": {"type": "object", "additionalProperties": False, "properties": {
        "min_value": {"type": "number", "minimum": 0}, "supplier": {"type": "string", "pattern": r"^vendorID_\d{1,6}$"},      # P1's supplier id shape: any other word is not a supplier
        "stage": {"enum": STAGES}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    "suppliers": {"type": "object", "additionalProperties": False, "properties": {
        "sort": {"enum": ["ci_lower", "breach_rate", "expected_loss", "volume"]}, "min_n": {"type": "integer", "minimum": 1, "maximum": 1000}}},
}
P1_ENDPOINT = {"queue": "/v1/queue", "suppliers": "/v1/suppliers"}

SYSTEM = f"""Translate the user's request into a filter. Return ONLY JSON: {{"target": "queue"|"suppliers", "filter": {{...}}}}
or {{"rejected": true, "reason": "..."}} when the request needs anything outside these schemas (dates, owners, regions,
assignees, etc.). Schemas: {json.dumps(SCHEMAS)}. Money is euros. Never invent fields."""

_NUMWORDS = {"ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
             "hundred": 100, "five": 5, "fifteen": 15, "twenty-five": 25}
_UNIT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}
_VALUE = re.compile(r"(?:above|over|more than|greater than|at least|worth more than|worth at least|>=?|exceeding)\s*€?\s*"
                    r"(\d[\d,]*\.?\d*|[a-z\-]+)\s*(k|m|thousand|million)?\b", re.I)
_STAGE_WORDS = [("block", r"payment block|blocked"), ("invoicing", r"invoic"), ("receipt", r"goods receipt|receipt|delivery|at receipt"),
                ("approval", r"approval|\bsrm\b"), ("changes", r"changed|renegotiat|change order|order change"),
                ("payment", r"clearing|payment stage|paid"), ("order", r"order creation|requisition|creation stage")]
UNSUPPORTED = re.compile(r"\b(due|next week|this week|yesterday|today|assigned|owner|maria|region|german|europe|asia|country|"
                         r"created in|in (january|february|march|april|may|june|july|august|september|october|november|december)|"
                         r"by date|deadline|from suppliers in)\b", re.I)


def _to_number(tok: str, unit: str | None) -> float | None:
    tok = tok.replace(",", "")
    try:
        n = float(tok)
    except ValueError:
        n = _NUMWORDS.get(tok.lower())
        if n is None:
            return None
        n = float(n)
        if unit is None and tok.lower() in ("fifty", "ten", "twenty", "thirty", "forty", "sixty", "seventy", "eighty", "ninety"):
            # "fifty thousand" is handled by the unit word; a bare "fifty" is not a euro amount
            return None
    return n * _UNIT.get((unit or "").lower(), 1)


def validate(target: str, flt: dict) -> list[str]:
    if target not in SCHEMAS:
        return [f"unknown target {target!r}"]
    v = jsonschema.Draft202012Validator(SCHEMAS[target])
    return [e.message for e in v.iter_errors(flt)]


def parse_rules(question: str) -> dict:
    """Deterministic parser: transparent, no model. Reports `rejected` for requests outside the schema."""
    q = question.strip()
    if UNSUPPORTED.search(q):
        return {"rejected": True, "reason": "the request needs a filter the API does not support (dates, owners, regions)"}
    low = q.lower()
    target = "suppliers" if re.search(r"\bsuppliers?\b", low) and not re.search(r"vendorid_\d+", low) and not re.search(r"for supplier", low) else "queue"
    flt: dict = {}
    if target == "queue":
        m = _VALUE.search(q)
        if m:
            tok = m.group(1)
            nxt = re.search(rf"{re.escape(tok)}\s+(thousand|million)", low)
            unit = m.group(2) or (nxt.group(1) if nxt else None)
            val = _to_number(tok, unit)
            if val is not None:
                flt["min_value"] = val
        else:
            m2 = re.search(r"worth (?:at least )?€?(\d[\d,]*\.?\d*)\s*(k|m)?", low)
            if m2:
                flt["min_value"] = _to_number(m2.group(1), m2.group(2))
        sup = re.search(r"\b(vendorID_\d+)\b", q)
        if sup:
            flt["supplier"] = sup.group(1)
        for stage, pat in _STAGE_WORDS:
            if re.search(pat, low):
                flt["stage"] = stage
                break
        for pat in (r"\b(?:top|first)\s+(\d+|[a-z\-]+)\b", r"\b(\d+|[a-z\-]+)\s+(?:worst|biggest|largest|riskiest)\b",
                    r"\b(?:worst|biggest|largest|riskiest)\s+(\d+|[a-z\-]+)\b"):
            lim = re.search(pat, low)
            tok = lim.group(1) if lim else None
            n = None if tok is None else (int(tok) if tok.isdigit() else _NUMWORDS.get(tok))
            if n:
                flt["limit"] = int(n)
                break
    else:
        if re.search(r"breach rate|worst", low):
            flt["sort"] = "breach_rate"
        elif re.search(r"expected loss|exposure|loss", low):
            flt["sort"] = "expected_loss"
        elif re.search(r"volume|busiest|most orders", low):
            flt["sort"] = "volume"
        elif re.search(r"statistic|confidence|interval|rank", low) and not re.search(r"breach", low):
            flt["sort"] = "ci_lower"
        mn = re.search(r"(?:at least|more than|over|above)\s+(\d+)\s+(?:closed\s+)?(?:cases|orders)", low)
        if mn:
            flt["min_n"] = int(mn.group(1))
    return {"target": target, "filter": flt}


def restate(target: str, flt: dict) -> str:
    where = "open cases ranked by expected loss" if target == "queue" else "suppliers"
    parts = []
    if "min_value" in flt:
        parts.append(f"order value of at least EUR {flt['min_value']:,.0f}")
    if "supplier" in flt:
        parts.append(f"supplier {flt['supplier']}")
    if "stage" in flt:
        parts.append(f"currently in the '{flt['stage']}' stage")
    if "limit" in flt:
        parts.append(f"top {flt['limit']}")
    if "sort" in flt:
        parts.append(f"sorted by {flt['sort'].replace('_', ' ')}")
    if "min_n" in flt:
        parts.append(f"with at least {flt['min_n']} cases")
    return f"Showing {where}" + (": " + ", ".join(parts) if parts else " (no filters)") + "."


def nl_filter(question: str, llm: LLM | None = None) -> dict:
    source, out = "rules", None
    if UNSUPPORTED.search(question):                      # dates, owners, regions: refused before any model sees them, so a model cannot turn "German" into a supplier name
        return {"rejected": True, "reason": "the request needs a filter the API does not support (dates, owners, regions)", "source": "rules"}
    if llm is not None:
        try:
            res = llm.complete(SYSTEM, question, max_tokens=300, label="nl-filter")
            out, source = _parse_json(res.text), "model"
        except LLMUnavailable:
            out = None
    if out is None:
        out, source = parse_rules(question), "rules"
    if out.get("rejected"):
        return {"rejected": True, "reason": out.get("reason", "outside the filter schema"), "source": source}
    target, flt = out.get("target"), out.get("filter") or {}
    errors = validate(target, flt)
    if errors:
        return {"rejected": True, "reason": "filter does not match the schema: " + "; ".join(errors[:3]), "source": source}
    return {"rejected": False, "target": target, "endpoint": P1_ENDPOINT[target], "filter": flt, "restatement": restate(target, flt), "source": source}
