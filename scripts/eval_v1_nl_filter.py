"""Exact-match accuracy of /v1/nl-filter on the 40-phrasing set (data/eval/nl_filter_40.json).

Run: python -m scripts.eval_v1_nl_filter                      # deterministic rule parser, free
     python -m scripts.eval_v1_nl_filter --model --max-cost-usd 0.5 [--dry-run]   # Claude through the spend guard
     python -m scripts.eval_v1_nl_filter --model --provider ollama                # the local model (free): reports/eval_nl_filter_local.json, a pipeline proof only
The phrasings were written independently of the parser; failures are printed, not tuned away.
"""
import argparse
import json
from pathlib import Path

from src.v1.nl_filter import SYSTEM, nl_filter


def failure_kind(row, got) -> str | None:
    """How a wrong answer is wrong: that matters more than the rate. None when it is right."""
    if row.get("rejected"):
        return None if got["rejected"] else "accepted a request that should be rejected"
    if got["rejected"]:
        return "rejected a valid request"
    exp, flt = row["filter"], got["filter"]
    if got["target"] == row["target"] and flt != exp and all(flt.get(k) == v for k, v in exp.items()):
        return "added constraints the user did not ask for"
    return None if (got["target"] == row["target"] and flt == exp) else "wrong target or values"


def score(rows, llm=None):
    out = []
    for r in rows:
        got = nl_filter(r["q"], llm)
        if r.get("rejected"):
            ok = got["rejected"]
        else:
            ok = (not got["rejected"]) and got["target"] == r["target"] and got["filter"] == r["filter"]
        out.append({"q": r["q"], "ok": bool(ok), "kind": failure_kind(r, got), "expected": r.get("filter", "REJECT"), "got": "REJECT" if got["rejected"] else got["filter"], "source": got["source"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="store_true")
    ap.add_argument("--max-cost-usd", type=float, default=0.5)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--provider", choices=("anthropic", "ollama"), default="anthropic", help="with --model")
    a = ap.parse_args()
    rows = json.loads(Path("data/eval/nl_filter_40.json").read_text(encoding="utf-8"))
    llm = None
    local = a.model and a.provider == "ollama"
    if local:
        from src.v1.llm import OllamaLLM
        from src.v1.spend import SpendGuard

        llm = OllamaLLM(guard=SpendGuard())
        print(f"{len(rows)} calls on {llm.name} (local, free)")
        if a.dry_run:
            return
    elif a.model:
        from src.evaluation.cost_estimator import calculate_cost, estimate_tokens
        from src.v1.llm import AnthropicLLM
        from src.v1.spend import SpendGuard

        llm = AnthropicLLM(guard=SpendGuard(per_request_usd=0.05, per_day_usd=a.max_cost_usd, per_month_usd=a.max_cost_usd))
        est = len(rows) * calculate_cost(estimate_tokens(SYSTEM) + 40, 120, llm.model).total_cost_usd
        print(f"estimated cost ~${est:.3f} for {len(rows)} calls on {llm.model}")
        if a.dry_run:
            return
    res = score(rows, llm)
    acc = sum(r["ok"] for r in res) / len(res)
    for r in res:
        if not r["ok"]:
            print("FAIL", r["q"], "| expected", r["expected"], "| got", r["got"], "|", r["kind"])
    kinds: dict[str, int] = {}
    for r in res:
        if r["kind"]:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print("failures by kind:", kinds or "none")
    print(f"exact-match accuracy: {acc:.1%} ({sum(r['ok'] for r in res)}/{len(res)}) source={'local model' if local else 'model' if llm else 'rules'}")
    Path("reports").mkdir(exist_ok=True)
    name = "local" if local else "model" if llm else "rules"
    extra = {"model": llm.name, "label": "local 7B pipeline proof: a free, weak model run through the real pipeline. Not a headline result and not comparable with a Claude run."} if local else {}
    Path(f"reports/eval_nl_filter_{name}.json").write_text(json.dumps({**extra, "accuracy": acc, "n": len(res), "failures_by_kind": kinds, "rows": res}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
