"""Evidence ledger and claim verification for /v1/ask.

Every fact the model may use is registered here with an id (e1, e2, ...) before the prompt is built; the model must cite
ids; every claim is then checked against what it cites:

- policy evidence -> the existing claim-support gate (numbers with units, key terms, content-word recall);
- live P1 evidence -> every number in the claim must appear in the cited facts (percent and hours/days conversions allowed).

A claim that cites nothing, cites an unknown id, or fails these checks is returned with supported=false: it is shown, not
hidden, so the UI can mark it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.evaluation.claim_support import sentence_support

_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*\.?\d*")
CITATION = re.compile(r"\s*\(Source:[^)]*\)\s*$")
MAX_FACTS_PER_OBJECT = 40


def numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUM.findall(text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.05, 0.005 * max(abs(a), abs(b)))


def number_supported(x: float, pool: list[float]) -> bool:
    """x is supported if it equals a pool number, or its percent/fraction or hours/days form (x*100, x/100, x*24, x/24)."""
    if abs(x) < 3 and float(x).is_integer():           # "1", "2": counts of things in prose, not figures to verify
        return True
    for p in pool:
        for form in (p, p * 100, p / 100, p * 24, p / 24):
            if _close(x, form):
                return True
    return False


@dataclass
class Evidence:
    id: str
    type: str                     # p1_metric | policy
    text: str                     # what claim verification compares against
    meta: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {"id": self.id, "type": self.type, **self.meta}


class EvidenceBook:
    def __init__(self):
        self.items: dict[str, Evidence] = {}
        self._n = 0

    def _next(self) -> str:
        self._n += 1
        return f"e{self._n}"

    def add_p1(self, endpoint: str, obj: dict, retrieved_at: str | None = None) -> list[Evidence]:
        """Register the scalar facts and metric objects of one P1 /v1 response."""
        now = retrieved_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        added: list[Evidence] = []

        def put(label: str, value, unit: str, extra: dict):
            if len(added) >= MAX_FACTS_PER_OBJECT or value is None:
                return
            ev = Evidence(self._next(), "p1_metric", f"{label} {value} {unit}".strip(),
                          {"endpoint": endpoint, "label": label, "value": value, "unit": unit, "retrieved_at": now, **extra})
            self.items[ev.id] = ev
            added.append(ev)

        def walk(node, path):
            if len(added) >= MAX_FACTS_PER_OBJECT:
                return
            if isinstance(node, dict):
                if {"value", "unit", "provenance"} <= set(node):
                    put(path, node["value"], node.get("unit", ""), {"provenance": node["provenance"], "as_of": node.get("as_of")})
                    return
                for k, v in node.items():
                    if k in ("snapshot", "timeline", "rows", "points", "nodes", "edges", "model", "policy_sections"):
                        continue
                    walk(v, f"{path}.{k}" if path else k)
            elif isinstance(node, list):
                for i, v in enumerate(node[:5]):
                    walk(v, f"{path}[{i}]")
            elif isinstance(node, (int, float, str)) and not isinstance(node, bool):
                if isinstance(node, str) and len(node) > 80:
                    return
                put(path, node, "", {"provenance": "descriptive"})

        walk(obj, "")
        return added

    def add_policy(self, hit) -> Evidence:
        ev = Evidence(self._next(), "policy", hit.text,
                      {"doc_id": hit.document_id, "title": hit.title, "section": hit.section_title, "version": None,
                       "citation": hit.citation, "excerpt": hit.text[:400]})
        self.items[ev.id] = ev
        return ev

    def prompt_block(self) -> str:
        lines = []
        for e in self.items.values():
            if e.type == "p1_metric":
                lines.append(f"[{e.id}] LIVE DATA ({e.meta['endpoint']}) {e.meta['label']} = {e.meta['value']} {e.meta['unit']} ({e.meta.get('provenance')})")
            else:
                lines.append(f"[{e.id}] POLICY ({e.meta['citation']}): {e.text[:600]}")
        return "\n".join(lines)


def verify_claim(text: str, ids: list[str], book: EvidenceBook, min_recall: float = 0.65) -> dict:
    cited = [book.items[i] for i in ids if i in book.items]
    if not ids:
        return {"supported": False, "reason": "claim cites no evidence"}
    if len(cited) != len(ids):
        return {"supported": False, "reason": "claim cites an unknown evidence id"}
    clean = CITATION.sub("", text).strip()
    pool = [n for e in cited for n in numbers_in(e.text)]
    pool += [float(e.meta["value"]) for e in cited if isinstance(e.meta.get("value"), (int, float))]
    bad = [n for n in numbers_in(clean) if not number_supported(n, pool)]
    if bad:
        return {"supported": False, "reason": f"figure(s) not found in the cited evidence: {sorted(set(bad))}"}
    policy = [e.text for e in cited if e.type == "policy"]
    if policy:
        s = sentence_support(clean, policy + [e.text for e in cited if e.type != "policy"])
        if not (s["numbers_supported"] and s["key_terms_supported"] and s["content_recall"] >= min_recall):
            return {"supported": False, "reason": f"not supported by the cited policy text (recall {s['content_recall']}, missing {s['missing_key_terms'][:3]})"}
    return {"supported": True, "reason": "figures match the cited evidence" if not policy else "supported by the cited policy text"}
