"""Compare fine-tuned Qwen2.5-0.5B vs Haiku 4.5 on tool-selection (181 rows).

181 rows = 112 held-out template rows (data/finetune/tool_test.jsonl)
         + 69 agent questions with expected_tools (data/evaluation/agent_questions_v2.json)

Metrics (with bootstrap 95% CIs):
  exact_tool_match   -- correct tool name(s)
  json_valid         -- output is parseable JSON
  p50_latency_ms     -- median per-example latency
  cost_per_1k        -- API cost (Haiku 4.5 only; Qwen is local)

Usage:
  python -m scripts.eval_tool_selection_api --dry-run
  python -m scripts.eval_tool_selection_api --sample 20
  python -m scripts.eval_tool_selection_api --full
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from dotenv import load_dotenv
load_dotenv()

TEST_FILE  = REPO_ROOT / "data" / "finetune" / "tool_test.jsonl"
AGENT_FILE = REPO_ROOT / "data" / "evaluation" / "agent_questions_v2.json"
OUT        = REPO_ROOT / "data" / "evaluation" / "tool_selection_api_results.json"

_N_BOOTSTRAP = 5_000
_SEED = 42

# Haiku 4.5 tool-routing prompt (same structure the fine-tune was trained on)
_SYSTEM = (
    "You are a tool-routing assistant for an operations analytics platform. "
    "Given a user question, output ONLY a JSON object or JSON array specifying "
    "which tool(s) to call and with what arguments. Do not add any explanation.\n\n"
    "Available tools:\n{tools}"
)

# canonical tool list (same as used in finetune training data)
_TOOLS = [
    "get_cycle_time", "get_bottlenecks", "get_sla_metrics", "get_supplier_performance",
    "get_management_report", "predict_sla_risk", "get_pipeline_status",
    "get_conformance", "search_policy_documents", "get_control_exceptions",
    "get_control_summary", "get_working_capital_summary",
    "propose_payment_hold", "propose_payment_release",
]
_TOOL_LIST_STR = "\n".join(f"- {t}" for t in _TOOLS)


# ── data loading ──────────────────────────────────────────────────────────────

def _load_test_rows() -> list[dict]:
    """112 held-out rows from tool_test.jsonl."""
    rows = []
    for line in TEST_FILE.open(encoding="utf-8"):
        d = json.loads(line)
        msgs = d["messages"]
        # Last user turn is the question; assistant turn is the gold output
        user_turn = next((m for m in reversed(msgs) if m["role"] == "user"), None)
        asst_turn = next((m for m in reversed(msgs) if m["role"] == "assistant"), None)
        if not (user_turn and asst_turn):
            continue
        try:
            gold = json.loads(asst_turn["content"])
        except json.JSONDecodeError:
            continue
        rows.append({
            "source": "template",
            "question": user_turn["content"],
            "gold_json": asst_turn["content"],
            "gold_tools": _extract_tools(gold),
        })
    return rows


def _load_agent_rows() -> list[dict]:
    """69 agent questions that have expected_tools."""
    qs = json.loads(AGENT_FILE.read_text(encoding="utf-8"))
    return [
        {
            "source": "agent_v2",
            "question": q["question"],
            "gold_json": json.dumps({"tool": q["expected_tools"][0]} if len(q["expected_tools"]) == 1
                                    else [{"tool": t} for t in q["expected_tools"]]),
            "gold_tools": q["expected_tools"],
        }
        for q in qs
        if q.get("expected_tools")
    ]


def _extract_tools(parsed) -> list[str]:
    """Extract tool names from a parsed JSON output (obj or list)."""
    if isinstance(parsed, dict):
        return [parsed.get("tool", parsed.get("name", ""))]
    if isinstance(parsed, list):
        return [item.get("tool", item.get("name", "")) for item in parsed if isinstance(item, dict)]
    return []


# ── scoring ───────────────────────────────────────────────────────────────────

def _score(candidate_text: str, gold_tools: list[str]) -> dict:
    try:
        parsed = json.loads(candidate_text.strip())
        json_valid = True
        pred_tools = _extract_tools(parsed)
    except json.JSONDecodeError:
        json_valid = False
        pred_tools = []
    exact = sorted(pred_tools) == sorted(gold_tools) if gold_tools else False
    return {"json_valid": json_valid, "exact_tool_match": exact, "pred_tools": pred_tools}


def _bootstrap_ci(values: list[bool], n_boot: int = _N_BOOTSTRAP) -> tuple[float, float]:
    rng = random.Random(_SEED)
    n = len(values)
    means = [sum(rng.choices(values, k=n)) / n for _ in range(n_boot)]
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot)]
    return lo, hi


# ── Haiku 4.5 inference ───────────────────────────────────────────────────────

def _haiku_predict(client, question: str) -> tuple[str, float]:
    t0 = time.time()
    text = client.text(
        system=_SYSTEM.format(tools=_TOOL_LIST_STR),
        user=question,
        max_tokens=200,
    )
    return text, (time.time() - t0) * 1000


# ── Qwen inference (local) ────────────────────────────────────────────────────

_QWEN_MODEL = None
_QWEN_TOKENIZER = None

def _load_qwen(adapter_path: str):
    global _QWEN_MODEL, _QWEN_TOKENIZER
    if _QWEN_MODEL is not None:
        return
    from peft import AutoPeftModelForCausalLM
    from transformers import AutoTokenizer
    _QWEN_TOKENIZER = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct")
    _QWEN_MODEL = AutoPeftModelForCausalLM.from_pretrained(adapter_path)
    _QWEN_MODEL.eval()
    print(f"  Loaded fine-tuned Qwen from {adapter_path}")


def _qwen_predict(adapter_path: str, question: str) -> tuple[str, float]:
    _load_qwen(adapter_path)
    from transformers import AutoTokenizer
    import torch
    messages = [
        {"role": "system", "content": _SYSTEM.format(tools=_TOOL_LIST_STR)},
        {"role": "user", "content": question},
    ]
    inp = _QWEN_TOKENIZER.apply_chat_template(
        messages, add_generation_prompt=True, return_tensors="pt"
    )
    t0 = time.time()
    with torch.no_grad():
        out = _QWEN_MODEL.generate(
            inp, max_new_tokens=200, do_sample=False, pad_token_id=_QWEN_TOKENIZER.eos_token_id
        )
    text = _QWEN_TOKENIZER.decode(out[0][inp.shape[-1]:], skip_special_tokens=True)
    return text, (time.time() - t0) * 1000


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--sample", type=int, metavar="N")
    group.add_argument("--full", action="store_true")
    ap.add_argument("--max-cost-usd", type=float, default=1.5)
    ap.add_argument("--adapter", default=str(REPO_ROOT / "models/tool_selection_adapter/adapter"))
    ap.add_argument("--no-qwen", action="store_true", help="Skip local Qwen eval (no torch needed)")
    args = ap.parse_args()

    rows = _load_test_rows() + _load_agent_rows()
    if args.sample:
        rows = rows[:args.sample]
    print(f"Total rows: {len(rows)} ({sum(1 for r in rows if r['source']=='template')} template, "
          f"{sum(1 for r in rows if r['source']=='agent_v2')} agent_v2)")

    from src.evaluation.claude_client import ClaudeClient
    est_haiku = ClaudeClient.estimate_cost("claude-haiku-4-5", 400, 100, len(rows))
    print(f"Haiku 4.5 cost estimate: ${est_haiku:.4f}")

    if args.dry_run:
        print("Dry run complete.")
        return

    from src.evaluation.claude_client import ClaudeClient

    haiku_results = []
    qwen_results  = []

    # Haiku 4.5
    print("\nRunning Haiku 4.5 …")
    with ClaudeClient("claude-haiku-4-5", max_cost_usd=args.max_cost_usd,
                      run_label="tool-sel-haiku") as client:
        for i, r in enumerate(rows):
            text, lat_ms = _haiku_predict(client, r["question"])
            sc = _score(text, r["gold_tools"])
            haiku_results.append({**sc, "latency_ms": lat_ms, "source": r["source"]})
            if (i + 1) % 20 == 0:
                print(f"  {i+1}/{len(rows)}")
        haiku_cost = client.cumulative_cost_usd
    print(f"  Haiku cost: ${haiku_cost:.4f} | cost/1k: ${haiku_cost/len(rows)*1000:.2f}")

    # Qwen (local)
    if not args.no_qwen:
        print("\nRunning fine-tuned Qwen2.5-0.5B …")
        for i, r in enumerate(rows):
            text, lat_ms = _qwen_predict(args.adapter, r["question"])
            sc = _score(text, r["gold_tools"])
            qwen_results.append({**sc, "latency_ms": lat_ms, "source": r["source"]})
            if (i + 1) % 20 == 0:
                print(f"  {i+1}/{len(rows)}")

    def _summarize(results: list[dict]) -> dict:
        if not results:
            return {}
        tool_vals = [r["exact_tool_match"] for r in results]
        json_vals = [r["json_valid"] for r in results]
        lats = sorted(r["latency_ms"] for r in results)
        lo_t, hi_t = _bootstrap_ci(tool_vals)
        lo_j, hi_j = _bootstrap_ci(json_vals)
        n = len(results)
        return {
            "n": n,
            "exact_tool_match": round(sum(tool_vals) / n, 4),
            "exact_tool_match_ci95": [round(lo_t, 4), round(hi_t, 4)],
            "json_valid": round(sum(json_vals) / n, 4),
            "json_valid_ci95": [round(lo_j, 4), round(hi_j, 4)],
            "p50_latency_ms": round(lats[n // 2]),
            "p95_latency_ms": round(lats[min(int(n * 0.95), n - 1)]),
        }

    summary = {
        "n": len(rows),
        "haiku": _summarize(haiku_results),
        "qwen_finetuned": _summarize(qwen_results),
    }
    if haiku_results:
        summary["haiku"]["cost_per_1k_usd"] = round(haiku_cost / len(rows) * 1000, 3)

    print("\nSummary:")
    for k, v in summary.items():
        if isinstance(v, dict):
            print(f"  {k}: tool_match={v.get('exact_tool_match')} "
                  f"CI=[{v.get('exact_tool_match_ci95')}] "
                  f"p50={v.get('p50_latency_ms')}ms")

    out = {
        "run_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": summary,
        "haiku_rows": haiku_results,
        "qwen_rows": qwen_results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Results written to {OUT}")


if __name__ == "__main__":
    main()
