"""Polarity check for the claim-support gate: catches an answer that uses one side of an antonym pair when the evidence uses the other.

The lexical gate cannot see 'included' -> 'excluded' because both are ordinary content words. Rule: a pair (x, y) conflicts when the
sentence contains x, the evidence never contains x, and the evidence does contain y. That is deliberately one-directional and conservative:
it needs the evidence to *positively* use the opposite term, so an answer that merely paraphrases does not trip it.

It does NOT catch a flipped leading 'Yes.'/'No.' (a short sentence that carries no content words and whose meaning depends on the
question); that remains a documented gap for an LLM or NLI judge, measured in scripts/polarity_eval.py.
"""
from __future__ import annotations

import re

PAIRS = [
    ("included", "excluded"), ("include", "exclude"), ("includes", "excludes"), ("including", "excluding"),
    ("required", "optional"), ("mandatory", "optional"), ("allowed", "prohibited"), ("permitted", "prohibited"),
    ("approved", "rejected"), ("increase", "decrease"), ("increases", "decreases"), ("before", "after"),
    ("above", "below"), ("more", "fewer"), ("higher", "lower"), ("earlier", "later"), ("counts", "excluded"),
    ("starts", "stops"), ("paused", "continues"), ("shorter", "longer"), ("minimum", "maximum"),
    ("open", "closed"), ("enabled", "disabled"), ("always", "never"),
]
_W = re.compile(r"[a-z]+")


def _words(text: str) -> set[str]:
    return set(_W.findall(text.lower().replace("‑", "-")))


def polarity_conflicts(sentence: str, chunks: list[str]) -> list[tuple[str, str]]:
    s, ev = _words(sentence), _words("\n".join(chunks))
    out = []
    for a, b in PAIRS:
        for x, y in ((a, b), (b, a)):
            if x in s and x not in ev and y in ev:
                out.append((x, y))
    return sorted(set(out))
