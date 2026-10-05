"""How often does a model's executive brief survive P2's traceability check, and why does it fail when it does? No key needed with --provider ollama.

The brief (src/v1/briefing.py) is built from numbered facts served by P1. A model's output is used only if it is JSON of the right shape, every sentence cites known fact ids, and every figure in
a sentence appears in the facts it cites; otherwise the deterministic template is shown, labelled. This runs the model on the full fact set and on every leave-one-fact-out variant, twice each (to
see whether a run repeats), and classifies each result. P1 must be reachable (OPS_PERFORMANCE_API_URL).

    python -X utf8 -m scripts.eval_v1_briefing --provider ollama

Writes reports/eval_v1_briefing_local.json (provider ollama) or reports/eval_v1_briefing.json (anthropic, needs ANTHROPIC_API_KEY): a local run says what it is and is a pipeline proof.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.v1.ask import _parse_json  # noqa: E402
from src.v1.briefing import SYSTEM  # noqa: E402
from src.v1.evidence import number_supported, numbers_in  # noqa: E402

LOCAL_LABEL = "local 7B pipeline proof: a free, weak model run through the real pipeline. Not a headline result and not comparable with a Claude run."


def classify(text: str, facts: list[dict]) -> str:
    """Why a model's brief would or would not be used. Mirrors briefing._valid, but says which check failed."""
    parsed = _parse_json(text)
    if parsed is None or not isinstance(parsed, dict):
        return "not JSON"
    items = parsed.get("items")
    if not isinstance(items, list) or not items or len(items) > 3:
        return "wrong shape (items missing, empty or more than 3)"
    by = {f["id"]: f for f in facts}
    for it in items:
        sents = it.get("sentences") if isinstance(it, dict) else None
        if not sents:
            return "wrong shape (an item without sentences)"
        for s in sents:
            ids = s.get("fact_ids") or []
            if not ids:
                return "a sentence cites no fact"
            if any(i not in by for i in ids):
                return "cites an unknown fact id"
            pool = [n for i in ids for n in numbers_in(by[i]["fact"])]
            if any(not number_supported(n, pool) for n in numbers_in(s.get("text", ""))):
                return "a figure is not in the facts it cites"
    return "used"


def variants(facts: list[dict]) -> list[tuple[str, list[dict]]]:
    out = [("all facts", facts)]
    out += [(f"without {f['id']}", [g for g in facts if g["id"] != f["id"]]) for f in facts]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=("anthropic", "ollama"), default="ollama")
    ap.add_argument("--repeats", type=int, default=2)
    a = ap.parse_args()
    from src.v1.llm import AnthropicLLM, OllamaLLM
    from src.v1.p1 import P1
    from src.v1.spend import SpendGuard

    local = a.provider == "ollama"
    guard = SpendGuard(Path(tempfile.gettempdir()) / "p2_eval_briefing_spend.db")
    llm = OllamaLLM(guard=guard) if local else AnthropicLLM(guard=guard)
    facts = P1().get("/v1/briefing")["facts"]
    rows = []
    for name, fs in variants(facts):
        user = "FACTS:\n" + "\n".join(f"[{f['id']}] {f['fact']}" for f in fs)
        outcomes = []
        for _ in range(a.repeats):
            res = llm.complete(SYSTEM, user, max_tokens=700, label="briefing")
            outcomes.append(classify(res.text, fs))
        rows.append({"variant": name, "n_facts": len(fs), "outcomes": outcomes})
        print(f"{name:<24} {outcomes}", flush=True)
    flat = [o for r in rows for o in r["outcomes"]]
    summary = {"model": llm.name if local else llm.model, "provider": a.provider, **({"label": LOCAL_LABEL} if local else {}), "runs": len(flat), "used": flat.count("used"),
               "used_rate": round(flat.count("used") / len(flat), 3), "failures_by_reason": {k: flat.count(k) for k in sorted(set(flat) - {"used"})}, "identical_across_repeats": sum(len(set(r["outcomes"])) == 1 for r in rows), "variants": len(rows)}
    print(json.dumps(summary, indent=1))
    Path("reports").mkdir(exist_ok=True)
    Path("reports/eval_v1_briefing_local.json" if local else "reports/eval_v1_briefing.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
