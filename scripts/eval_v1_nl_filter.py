"""Exact-match accuracy of /v1/nl-filter on the 40-phrasing set (data/eval/nl_filter_40.json).

Run: python -m scripts.eval_v1_nl_filter                      # deterministic rule parser, free
     python -m scripts.eval_v1_nl_filter --model --max-cost-usd 0.5 [--dry-run]   # Claude through the spend guard
The phrasings were written independently of the parser; failures are printed, not tuned away.
"""
import argparse
import json
from pathlib import Path

from src.v1.nl_filter import SYSTEM, nl_filter


def score(rows, llm=None):
    out = []
    for r in rows:
        got = nl_filter(r["q"], llm)
        if r.get("rejected"):
            ok = got["rejected"]
        else:
            ok = (not got["rejected"]) and got["target"] == r["target"] and got["filter"] == r["filter"]
        out.append({"q": r["q"], "ok": bool(ok), "expected": r.get("filter", "REJECT"), "got": "REJECT" if got["rejected"] else got["filter"], "source": got["source"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="store_true")
    ap.add_argument("--max-cost-usd", type=float, default=0.5)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    rows = json.loads(Path("data/eval/nl_filter_40.json").read_text(encoding="utf-8"))
    llm = None
    if a.model:
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
            print("FAIL", r["q"], "| expected", r["expected"], "| got", r["got"])
    print(f"exact-match accuracy: {acc:.1%} ({sum(r['ok'] for r in res)}/{len(res)}) source={'model' if llm else 'rules'}")
    Path("reports").mkdir(exist_ok=True)
    Path(f"reports/eval_nl_filter_{'model' if llm else 'rules'}.json").write_text(json.dumps({"accuracy": acc, "n": len(res), "rows": res}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
