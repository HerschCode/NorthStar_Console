"""Evaluate the claim-support gate and LLM judge against human-labelled public datasets.

Compares:
  lexical   -- claim_support gate (r=0.65, deployed)
  nli       -- cross-encoder/nli-deberta-v3-small (baseline to show domain mismatch)
  haiku     -- Haiku 4.5 LLM judge (via Anthropic Batch API)
  sonnet    -- Sonnet 5.5 LLM judge (via Anthropic Batch API, optional)

Datasets (sampled to ~1 000 each, fixed seed 42, documented in reports/):
  LLM-AggreFact  -- human-labelled claim-level factuality (Tang, Laban and Durrett, 2024; gated, see below)
  RAGTruth       -- human-labelled hallucination in RAG answers (Niu et al., 2024)

Metrics: balanced accuracy, precision, recall on "unsupported" class, per-subset,
cost per 1 000 items. All saved in reports/gate_public_eval.json.
Report: docs/gate-public-eval.md

Usage:
  python -m scripts.eval_gate_public --dry-run          # token/cost estimate, no API calls
  python -m scripts.eval_gate_public --sample 20        # smoke test on 20 items
  python -m scripts.eval_gate_public --full             # full ~1 000-item run
  python -m scripts.eval_gate_public --full --no-sonnet # skip Sonnet 5.5 (saves ~3x cost)

ANTHROPIC_API_KEY must be set.
Dataset licences: LLM-AggreFact is CC BY-ND 4.0 and GATED on Hugging Face (an account that accepted its terms, HF_TOKEN set by the owner; without it the AggreFact half cannot run).
RAGTruth is MIT and is read from GitHub at a pinned commit (scripts/eval_gate_ragtruth.py).
The no-key half of this comparison (lexical gate and NLI baseline on RAGTruth) is scripts/eval_gate_ragtruth.py and has been run; this script adds the LLM-judge columns and needs a key.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

OUT = REPO_ROOT / "reports" / "gate_public_eval.json"
REPORT = REPO_ROOT / "docs" / "gate-public-eval.md"
LICENCE_NOTE = REPO_ROOT / "docs" / "gate-public-eval-licences.md"

# HuggingFace dataset IDs — override with env vars if needed
HF_AGGREFACT = os.environ.get("HF_AGGREFACT", "lytang/LLM-AggreFact")     # gated: needs HF_TOKEN from an account that accepted the dataset's terms

_JUDGE_SYSTEM = (
    "You are a faithfulness judge. Decide whether a CLAIM is supported by the SOURCE.\n"
    "The SOURCE is a retrieved document excerpt; the CLAIM is part of a generated answer.\n"
    "Respond with exactly one word: SUPPORTED or UNSUPPORTED."
)

_JUDGE_USER = "SOURCE:\n{source}\n\nCLAIM:\n{claim}\n\nVerdict:"

_SAMPLE_SIZE = 1000
_SEED = 42


# ── dataset loading ──────────────────────────────────────────────────────────

def _load_aggrefact(n: int | None) -> list[dict]:
    """Load LLM-AggreFact. Expected columns: claim/document/label (1=consistent)."""
    from datasets import load_dataset
    try:
        ds = load_dataset(HF_AGGREFACT, split="test", trust_remote_code=True)
    except Exception:
        ds = load_dataset(HF_AGGREFACT, split="train", trust_remote_code=True)
    rows = list(ds)
    # normalise: find claim/source/label column names
    sample = rows[0]
    claim_col = next((c for c in ("claim", "sentence", "summary") if c in sample), None)
    src_col   = next((c for c in ("document", "context", "source", "passage") if c in sample), None)
    label_col = next((c for c in ("label", "annotation", "factual") if c in sample), None)
    if not all([claim_col, src_col, label_col]):
        raise ValueError(
            f"Cannot find claim/source/label columns in {HF_AGGREFACT}. "
            f"Found: {list(sample.keys())}. "
            "Set HF_AGGREFACT env var to the correct dataset id or check the column names."
        )
    out = [
        {
            "dataset": "LLM-AggreFact",
            "subset": r.get("dataset", r.get("source_name", "unknown")),
            "claim": r[claim_col],
            "source": r[src_col],
            "human_label": bool(r[label_col]),  # True = grounded/supported
        }
        for r in rows
    ]
    if n:
        rng = random.Random(_SEED)
        rng.shuffle(out)
        out = out[:n]
    return out


def _load_ragtruth(n: int | None) -> list[dict]:
    """RAGTruth test split from GitHub (MIT, pinned commit, see scripts/eval_gate_ragtruth.py): the whole response is the claim, the source it was written from is the source,
    and a response with no human-labelled hallucination span counts as supported."""
    from scripts import eval_gate_ragtruth as rt

    rt.fetch()
    out = [{"dataset": "RAGTruth", "subset": r["task"], "claim": r["response"][:1500], "source": r["source"][:3000], "human_label": not r["hallucinated"]} for r in rt.load_rows("test")]
    if n:
        rng = random.Random(_SEED)
        rng.shuffle(out)
        out = out[:n]
    return out


# ── lexical gate ─────────────────────────────────────────────────────────────

def _lex_score(claim: str, source: str) -> bool:
    from src.evaluation.claim_support import answer_supported
    ok, _ = answer_supported(claim, [source], min_recall=0.65)
    return ok


# ── NLI gate ─────────────────────────────────────────────────────────────────

_NLI_PIPELINE = None

def _nli_score(claim: str, source: str) -> bool:
    global _NLI_PIPELINE
    if _NLI_PIPELINE is None:
        from transformers import pipeline
        _NLI_PIPELINE = pipeline(
            "text-classification",
            model="cross-encoder/nli-deberta-v3-small",
            device=-1,
        )
    result = _NLI_PIPELINE(f"{source} [SEP] {claim}", truncation=True, max_length=512)
    label = result[0]["label"].upper()
    return label != "CONTRADICTION"


# ── LLM judge via batch API ───────────────────────────────────────────────────

def _prepare_batch_requests(rows: list[dict], batch_id_prefix: str) -> list[dict]:
    return [
        {
            "custom_id": f"{batch_id_prefix}_{i}",
            "messages": [{"role": "user", "content": _JUDGE_USER.format(
                source=r["source"][:3000],
                claim=r["claim"][:1500],
            )}],
            "system": _JUDGE_SYSTEM,
            "max_tokens": 10,
        }
        for i, r in enumerate(rows)
    ]


def _parse_judge_text(text: str) -> bool:
    t = text.strip().upper()
    if "UNSUPPORTED" in t:
        return False
    if "SUPPORTED" in t:
        return True
    return False   # conservative default


# ── scoring ───────────────────────────────────────────────────────────────────

def _metrics(rows: list[dict], gate_key: str) -> dict:
    """Balanced accuracy, precision, recall. Positive class = grounded/supported."""
    tp = sum(1 for r in rows if r["human_label"] and r.get(gate_key))
    fp = sum(1 for r in rows if not r["human_label"] and r.get(gate_key))
    tn = sum(1 for r in rows if not r["human_label"] and not r.get(gate_key))
    fn = sum(1 for r in rows if r["human_label"] and not r.get(gate_key))
    n = len(rows)
    sens = tp / (tp + fn) if (tp + fn) else 0.0   # recall on supported
    spec = tn / (tn + fp) if (tn + fp) else 0.0   # recall on unsupported
    bal_acc = (sens + spec) / 2
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = 2 * prec * sens / (prec + sens) if (prec + sens) else 0.0
    acc = (tp + tn) / n if n else 0.0
    return {
        "n": n, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "accuracy": round(acc, 4),
        "balanced_accuracy": round(bal_acc, 4),
        "precision": round(prec, 4),
        "recall": round(sens, 4),
        "f1": round(f1, 4),
        "specificity": round(spec, 4),
    }


def _per_subset(rows: list[dict], gate_key: str) -> dict:
    subsets: dict[str, list] = {}
    for r in rows:
        subsets.setdefault(r["subset"], []).append(r)
    return {s: _metrics(rs, gate_key) for s, rs in sorted(subsets.items())}


# ── report writer ─────────────────────────────────────────────────────────────

def _write_report(out: dict) -> None:
    def _pct(v):
        return f"{v:.1%}" if v is not None else "N/A"

    lines = [
        "# Gate evaluation on public human-labelled datasets",
        "",
        "Compares the deployed claim-support gate (lexical, r=0.65) and LLM judges against",
        "**human-labelled** factuality data — an independent external measure that does not share",
        "the same author as the existing 32-question in-domain eval.",
        "",
        "## Datasets",
        "",
        "| Dataset | Licence | N sampled | Sampling |",
        "|---|---|---|---|",
        f"| LLM-AggreFact | CC BY-ND 4.0 (gated) | {out['n_aggrefact']} | random seed 42 |",
        f"| RAGTruth | MIT | {out['n_ragtruth']} | random seed 42 |",
        "",
        "Dataset licences recorded in `docs/gate-public-eval-licences.md`.",
        "",
        "## Results",
        "",
        "### LLM-AggreFact",
        "",
        "| Gate | Balanced acc | F1 | Precision | Recall | Specificity |",
        "|---|---|---|---|---|---|",
    ]
    for g in ["lexical", "nli", "haiku", "sonnet"]:
        m = out.get("aggrefact_metrics", {}).get(g)
        if m is None:
            continue
        lines.append(
            f"| {g} | {_pct(m['balanced_accuracy'])} | {m['f1']:.3f} "
            f"| {_pct(m['precision'])} | {_pct(m['recall'])} | {_pct(m['specificity'])} |"
        )

    lines += ["", "### RAGTruth", "",
              "| Gate | Balanced acc | F1 | Precision | Recall | Specificity |",
              "|---|---|---|---|---|---|"]
    for g in ["lexical", "nli", "haiku", "sonnet"]:
        m = out.get("ragtruth_metrics", {}).get(g)
        if m is None:
            continue
        lines.append(
            f"| {g} | {_pct(m['balanced_accuracy'])} | {m['f1']:.3f} "
            f"| {_pct(m['precision'])} | {_pct(m['recall'])} | {_pct(m['specificity'])} |"
        )

    lines += ["", "## Cost", "", "| Gate | Model | N | Cost USD |", "|---|---|---|---|"]
    for entry in out.get("cost_entries", []):
        lines.append(
            f"| {entry['gate']} | {entry['model']} | {entry['n']} | ${entry['cost_usd']:.4f} |"
        )

    lines += [
        "", "## Notes",
        "",
        "- LLM-AggreFact aggregates multiple claim-level factuality datasets with human labels.",
        "- RAGTruth contains human-labelled hallucinations in RAG-generated answers.",
        "- The lexical gate and NLI baseline run without API calls; costs are $0.",
        "- LLM judge calls go through the Anthropic Batch API (50% discount).",
        "- See `reports/gate_public_eval.json` for per-row results and per-subset breakdown.",
    ]

    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Estimate cost, no API calls")
    group.add_argument("--sample", type=int, metavar="N", help="Run on first N items (smoke test)")
    group.add_argument("--full", action="store_true", help="Full ~1 000-item run")
    ap.add_argument("--max-cost-usd", type=float, default=6.0)
    ap.add_argument("--no-nli", action="store_true", help="Skip NLI baseline (faster, needs torch)")
    ap.add_argument("--no-sonnet", action="store_true", help="Skip Sonnet 5.5 judge (saves ~5x)")
    ap.add_argument("--dataset", choices=["aggrefact", "ragtruth", "both"], default="both")
    args = ap.parse_args()

    n_per_dataset = (
        3 if args.dry_run
        else args.sample if args.sample
        else _SAMPLE_SIZE
    )

    print(f"Loading datasets (n={n_per_dataset} each) …")
    rows_all: list[dict] = []
    if args.dataset in ("aggrefact", "both"):
        try:
            agg = _load_aggrefact(n_per_dataset)
            rows_all.extend(agg)
            print(f"  LLM-AggreFact: {len(agg)} rows")
        except Exception as e:
            print(f"  LLM-AggreFact FAILED: {e}")
            print(f"  Try: HF_AGGREFACT=<correct-id> python -m scripts.eval_gate_public --dry-run")
            if not args.dry_run:
                sys.exit(1)

    if args.dataset in ("ragtruth", "both"):
        try:
            rag = _load_ragtruth(n_per_dataset)
            rows_all.extend(rag)
            print(f"  RAGTruth: {len(rag)} rows")
        except Exception as e:
            print(f"  RAGTruth FAILED: {e}")
            print(f"  Try: HF_RAGTRUTH=<correct-id> python -m scripts.eval_gate_public --dry-run")
            if not args.dry_run:
                sys.exit(1)

    n_total = len(rows_all)

    # Cost estimate
    haiku_est = ClaudeClient_estimate("claude-haiku-4-5", 600, 5, n_total, batch=True)
    sonnet_est = ClaudeClient_estimate("claude-sonnet-5-5", 600, 5, n_total, batch=True)
    print(f"\nCost estimate for {n_total} rows:")
    print(f"  Haiku 4.5 batch: ${haiku_est:.3f}")
    if not args.no_sonnet:
        print(f"  Sonnet 5.5 batch: ${sonnet_est:.3f}")
        print(f"  Total: ${haiku_est + sonnet_est:.3f}")
    else:
        print(f"  Total: ${haiku_est:.3f}")

    if args.dry_run:
        print("\nDry run complete — no API calls made.")
        return

    if haiku_est + (0 if args.no_sonnet else sonnet_est) > args.max_cost_usd:
        print(f"Estimated cost exceeds --max-cost-usd {args.max_cost_usd:.2f}. "
              "Use --no-sonnet or reduce --sample size.")
        sys.exit(1)

    from src.evaluation.claude_client import ClaudeClient, BudgetExceededError

    # Lexical gate
    print("\nRunning lexical gate …")
    for r in rows_all:
        r["lexical"] = _lex_score(r["claim"], r["source"])

    # NLI baseline
    if not args.no_nli:
        print("Running NLI baseline …")
        for i, r in enumerate(rows_all):
            r["nli"] = _nli_score(r["claim"], r["source"])
            if (i + 1) % 100 == 0:
                print(f"  {i+1}/{n_total}")

    # Haiku judge (batch)
    print("Submitting Haiku 4.5 batch …")
    cost_entries = []
    with ClaudeClient("claude-haiku-4-5", max_cost_usd=args.max_cost_usd,
                      run_label="gate-public-haiku", batch=True) as haiku_client:
        reqs = _prepare_batch_requests(rows_all, "haiku")
        batch_id = haiku_client.batch_submit(reqs)
        print(f"  Batch ID: {batch_id} — polling …")
        results = haiku_client.batch_poll(batch_id, poll_interval=15.0)
        by_id = {r["custom_id"]: r for r in results}
        for i, r in enumerate(rows_all):
            res = by_id.get(f"haiku_{i}", {})
            r["haiku"] = _parse_judge_text(res.get("text", ""))
        cost_entries.append({
            "gate": "haiku", "model": "claude-haiku-4-5",
            "n": n_total, "cost_usd": round(haiku_client.cumulative_cost_usd, 4),
        })
        print(f"  Haiku cost: ${haiku_client.cumulative_cost_usd:.4f}")

    # Sonnet judge (batch, optional)
    if not args.no_sonnet:
        print("Submitting Sonnet 5.5 batch …")
        with ClaudeClient("claude-sonnet-5-5", max_cost_usd=args.max_cost_usd,
                          run_label="gate-public-sonnet", batch=True) as sonnet_client:
            reqs = _prepare_batch_requests(rows_all, "sonnet")
            batch_id = sonnet_client.batch_submit(reqs)
            print(f"  Batch ID: {batch_id} — polling …")
            results = sonnet_client.batch_poll(batch_id, poll_interval=15.0)
            by_id = {r["custom_id"]: r for r in results}
            for i, r in enumerate(rows_all):
                res = by_id.get(f"sonnet_{i}", {})
                r["sonnet"] = _parse_judge_text(res.get("text", ""))
            cost_entries.append({
                "gate": "sonnet", "model": "claude-sonnet-5-5",
                "n": n_total, "cost_usd": round(sonnet_client.cumulative_cost_usd, 4),
            })
            print(f"  Sonnet cost: ${sonnet_client.cumulative_cost_usd:.4f}")

    # Score
    agg_rows = [r for r in rows_all if r["dataset"] == "LLM-AggreFact"]
    rag_rows = [r for r in rows_all if r["dataset"] == "RAGTruth"]

    gates = ["lexical"] + ([] if args.no_nli else ["nli"]) + ["haiku"] + ([] if args.no_sonnet else ["sonnet"])
    result = {
        "run_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_aggrefact": len(agg_rows),
        "n_ragtruth": len(rag_rows),
        "sampling": {"seed": _SEED, "n_per_dataset": n_per_dataset},
        "aggrefact_metrics": {g: _metrics(agg_rows, g) for g in gates if agg_rows},
        "aggrefact_by_subset": {g: _per_subset(agg_rows, g) for g in gates if agg_rows},
        "ragtruth_metrics": {g: _metrics(rag_rows, g) for g in gates if rag_rows},
        "ragtruth_by_subset": {g: _per_subset(rag_rows, g) for g in gates if rag_rows},
        "cost_entries": cost_entries,
        "rows": rows_all,
    }

    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults written to {OUT}")

    _write_report(result)
    print(f"Report written to {REPORT}")

    # Licence note
    if not LICENCE_NOTE.exists():
        LICENCE_NOTE.write_text(
            "# Dataset licences\n\n"
            "- **LLM-AggreFact** (`lytang/LLM-AggreFact`): CC BY-ND 4.0, gated on Hugging Face (an account that accepted its terms). "
            "Tang et al., 2024. https://huggingface.co/datasets/lytang/LLM-AggreFact\n"
            "- **RAGTruth** (`ParticleMedia/RAGTruth` on GitHub, read at a pinned commit): MIT Licence. "
            "Niu et al., 2024. https://github.com/ParticleMedia/RAGTruth\n",
            encoding="utf-8",
        )


# helper so dry-run can import without a client
def ClaudeClient_estimate(model, in_tok, out_tok, n, batch=False):
    from src.evaluation.claude_client import ClaudeClient
    return ClaudeClient.estimate_cost(model, in_tok, out_tok, n, batch=batch)


if __name__ == "__main__":
    main()
