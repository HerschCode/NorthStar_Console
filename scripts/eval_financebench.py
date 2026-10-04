"""Evaluate P2 agent on FinanceBench — 150 questions over real 10-K filings.

FinanceBench (Islam et al., 2023) provides questions, human-verified answers,
and supporting evidence excerpts from real SEC 10-K filings.  We ingest the
evidence text into a separate ChromaDB collection (not mixed with the synthetic
docs), run the agent's document tools, then score against the human answer.

Scoring:
  numeric_exact  -- answer contains the exact number/percentage (±1% tolerance)
  llm_judge      -- Haiku 4.5 judges against reference answer (1=correct/2=partial/3=wrong)
  retrieval_hit  -- evidence excerpt appears in retrieved chunks (Hit@5)

Usage:
  python -m scripts.eval_financebench --dry-run         # estimate cost, no API calls
  python -m scripts.eval_financebench --sample 5        # smoke test on 5 questions
  python -m scripts.eval_financebench --full            # all 150 questions (~$7)

ANTHROPIC_API_KEY must be set; GROQ_API_KEY used for the agent if --agent-provider groq.
Dataset: PatronusAI/financebench (Apache 2.0).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from dotenv import load_dotenv
load_dotenv()

OUT      = REPO_ROOT / "data" / "evaluation" / "financebench_results.json"
REPORT   = REPO_ROOT / "docs" / "financebench-eval.md"
COLL_NAME = "financebench"
HF_DATASET = os.environ.get("HF_FINANCEBENCH", "PatronusAI/financebench")

_JUDGE_SYSTEM = (
    "You are scoring a financial question-answering system. "
    "Given a REFERENCE answer and a CANDIDATE answer, output JSON: "
    '{\"score\": 1 or 2 or 3, \"reason\": \"one sentence\"}\n'
    "Scores: 1=correct (same number/fact), 2=partially correct (right direction, wrong detail), "
    "3=wrong or hallucinated."
)
_JUDGE_USER = (
    "QUESTION: {question}\n"
    "REFERENCE: {reference}\n"
    "CANDIDATE: {candidate}\n\n"
    'Output JSON only: {{"score": <1-3>, "reason": "<one sentence>"}}'
)


# ── dataset loading ───────────────────────────────────────────────────────────

def _load_financebench(n: int | None) -> list[dict]:
    from datasets import load_dataset
    try:
        ds = load_dataset(HF_DATASET, split="train", trust_remote_code=True)
    except Exception as e:
        raise RuntimeError(
            f"Failed to load {HF_DATASET}: {e}\n"
            "Set HF_FINANCEBENCH env var to the correct HuggingFace dataset id."
        ) from e
    rows = list(ds)
    sample = rows[0]
    # Find key columns
    q_col    = next((c for c in ("question", "query") if c in sample), None)
    ans_col  = next((c for c in ("answer", "gold_answer", "reference") if c in sample), None)
    evid_col = next((c for c in ("evidence", "context", "passages", "evidence_text") if c in sample), None)
    co_col   = next((c for c in ("company", "company_name") if c in sample), None)
    yr_col   = next((c for c in ("year", "fiscal_year") if c in sample), None)
    if not all([q_col, ans_col, evid_col]):
        raise ValueError(
            f"Cannot find question/answer/evidence columns in {HF_DATASET}. "
            f"Found: {list(sample.keys())}. "
            "Set HF_FINANCEBENCH env var to the correct dataset id."
        )
    out = [
        {
            "id": i,
            "question": r[q_col],
            "reference": str(r[ans_col]),
            "evidence": str(r[evid_col])[:4000],
            "company": r.get(co_col, ""),
            "year": str(r.get(yr_col, "")),
        }
        for i, r in enumerate(rows)
    ]
    if n:
        out = out[:n]
    return out


# ── ChromaDB ingestion ────────────────────────────────────────────────────────

_CHROMA_CLIENT = None

def _get_collection(items: list[dict]):
    """Ingest evidence text into a temporary ChromaDB collection."""
    global _CHROMA_CLIENT
    import chromadb
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    if _CHROMA_CLIENT is None:
        _CHROMA_CLIENT = chromadb.Client()  # in-memory, per-run
    ef = SentenceTransformerEmbeddingFunction(model_name="BAAI/bge-small-en-v1.5")
    coll = _CHROMA_CLIENT.get_or_create_collection(COLL_NAME, embedding_function=ef)
    if coll.count() == 0:
        print(f"  Ingesting {len(items)} evidence chunks into '{COLL_NAME}' collection …")
        docs = [r["evidence"] for r in items]
        ids  = [f"fb_{r['id']}" for r in items]
        metas = [{"question_id": r["id"]} for r in items]
        coll.add(documents=docs, ids=ids, metadatas=metas)
    return coll


def _retrieve(coll, question: str, k: int = 5) -> list[str]:
    results = coll.query(query_texts=[question], n_results=min(k, coll.count()))
    return results["documents"][0] if results["documents"] else []


# ── scoring ───────────────────────────────────────────────────────────────────

def _numeric_match(candidate: str, reference: str) -> bool:
    """True if every number in reference appears in candidate (±1% tolerance)."""
    nums = re.findall(r"[\d,]+\.?\d*", reference.replace(",", ""))
    if not nums:
        return False
    for n in nums:
        try:
            ref_val = float(n.replace(",", ""))
        except ValueError:
            continue
        cand_nums = re.findall(r"[\d,]+\.?\d*", candidate.replace(",", ""))
        found = any(
            abs(float(c.replace(",", "")) - ref_val) / (abs(ref_val) + 1e-9) <= 0.01
            for c in cand_nums
            if c.replace(",", "").replace(".", "").isdigit()
        )
        if not found:
            return False
    return True


def _retrieval_hit(chunks: list[str], evidence: str, k: int = 5) -> bool:
    """True if any retrieved chunk overlaps significantly with the reference evidence."""
    evidence_words = set(re.findall(r"\b\w+\b", evidence.lower()))
    for chunk in chunks[:k]:
        chunk_words = set(re.findall(r"\b\w+\b", chunk.lower()))
        overlap = len(evidence_words & chunk_words) / max(len(evidence_words), 1)
        if overlap >= 0.4:
            return True
    return False


# ── LLM judge ────────────────────────────────────────────────────────────────

def _judge_score(client, question: str, reference: str, candidate: str) -> dict:
    prompt = _JUDGE_USER.format(
        question=question[:400],
        reference=reference[:400],
        candidate=candidate[:800],
    )
    try:
        text = client.text(user=prompt, system=_JUDGE_SYSTEM, max_tokens=80)
        data = json.loads(text.strip())
        return {"score": int(data["score"]), "reason": data.get("reason", "")}
    except Exception as e:
        return {"score": None, "reason": f"[parse error] {e}"}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--sample", type=int, metavar="N")
    group.add_argument("--full", action="store_true")
    ap.add_argument("--max-cost-usd", type=float, default=10.0)
    ap.add_argument("--agent-provider", choices=["groq", "anthropic"], default="groq",
                    help="Provider for the agent (default: groq for lower cost)")
    ap.add_argument("--no-judge", action="store_true", help="Skip LLM judge (saves cost)")
    args = ap.parse_args()

    n = 3 if args.dry_run else args.sample if args.sample else None
    print("Loading FinanceBench …")
    items = _load_financebench(n)
    print(f"  {len(items)} questions loaded")

    if args.dry_run:
        judge_est = 0 if args.no_judge else ClaudeClient_estimate(
            "claude-haiku-4-5", 800, 60, len(items), batch=False)
        # Agent calls via Groq are ~$0 on free tier; via Anthropic ~$0.06/q on Sonnet 5.5
        agent_est = ClaudeClient_estimate("claude-sonnet-5-5", 2000, 500, len(items)) \
                    if args.agent_provider == "anthropic" else 0
        print(f"\nCost estimate for {len(items)} questions:")
        print(f"  Agent ({args.agent_provider}): ~${agent_est:.2f}")
        print(f"  Haiku judge: ~${judge_est:.2f}")
        print(f"  Total: ~${agent_est + judge_est:.2f}")
        print("\nDry run complete.")
        return

    from src.agent.agent import run_agent, load_agent_config
    from src.evaluation.claude_client import ClaudeClient

    # Ingest evidence into temp collection
    coll = _get_collection(items)

    config = load_agent_config()
    config["provider"] = args.agent_provider

    rows = []
    judge_client = None if args.no_judge else ClaudeClient(
        "claude-haiku-4-5", max_cost_usd=args.max_cost_usd, run_label="financebench-judge")

    print(f"\nRunning {len(items)} questions (agent={args.agent_provider}) …")
    t_run = time.time()
    try:
        for i, item in enumerate(items):
            print(f"[{i+1}/{len(items)}] {item['question'][:70]}")
            t0 = time.time()
            retrieved = _retrieve(coll, item["question"], k=5)
            ret_hit = _retrieval_hit(retrieved, item["evidence"])

            try:
                resp = run_agent(item["question"], config_override=config)
                candidate = resp.answer
                latency = round(time.time() - t0, 2)
            except Exception as e:
                candidate = f"[agent error] {e}"
                latency = round(time.time() - t0, 2)

            num_match = _numeric_match(candidate, item["reference"])
            judge_result = {}
            if judge_client and candidate and not candidate.startswith("[agent error]"):
                judge_result = _judge_score(
                    judge_client, item["question"], item["reference"], candidate)

            row = {
                "id": item["id"],
                "question": item["question"],
                "company": item["company"],
                "year": item["year"],
                "reference": item["reference"][:300],
                "candidate": candidate[:400],
                "numeric_match": num_match,
                "retrieval_hit": ret_hit,
                "judge_score": judge_result.get("score"),
                "judge_reason": judge_result.get("reason", ""),
                "latency_s": latency,
            }
            rows.append(row)
            print(f"  numeric={num_match} retrieval={ret_hit} judge={judge_result.get('score')} [{latency:.1f}s]")

            if not args.dry_run:
                time.sleep(1.0)
    finally:
        if judge_client:
            entry = judge_client.close(script="eval_financebench")
            print(f"\nJudge cost: ${entry['cost_usd']:.4f}")

    # Summary
    ok = [r for r in rows if not str(r.get("candidate", "")).startswith("[agent error]")]
    n_ok = len(ok)
    summary = {
        "n_total": len(rows),
        "n_answered": n_ok,
        "numeric_match_rate": round(sum(1 for r in ok if r["numeric_match"]) / n_ok, 3) if n_ok else None,
        "retrieval_hit_rate": round(sum(1 for r in rows if r["retrieval_hit"]) / len(rows), 3),
        "avg_judge_score": round(
            sum(r["judge_score"] for r in ok if r["judge_score"]) /
            max(1, sum(1 for r in ok if r["judge_score"])), 2
        ) if any(r["judge_score"] for r in ok) else None,
        "elapsed_s": round(time.time() - t_run, 1),
    }
    print("\nSummary:", summary)

    out = {
        "run_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": HF_DATASET,
        "agent_provider": args.agent_provider,
        "summary": summary,
        "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Results written to {OUT}")

    _write_report(out)
    print(f"Report written to {REPORT}")


def _write_report(out: dict) -> None:
    s = out["summary"]
    lines = [
        "# FinanceBench evaluation",
        "",
        f"**Dataset:** {out['dataset']} (Apache 2.0, Patronus AI)",
        f"**Questions:** {s['n_total']} (150 total; sampled if smaller)",
        f"**Agent provider:** {out['agent_provider']}",
        f"**Run date:** {out['run_date'][:10]}",
        "",
        "FinanceBench provides questions with human-verified answers over real 10-K filings.",
        "This measures whether the retrieval and generation stack works on **real financial**",
        "documents, not just the 12 synthetic Northstar policy docs.",
        "",
        "## Results",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Numeric match rate | {s['numeric_match_rate']:.1%} |" if s.get("numeric_match_rate") is not None else "| Numeric match rate | N/A |",
        f"| Retrieval Hit@5 | {s['retrieval_hit_rate']:.1%} |",
        f"| Avg judge score (1=correct, 3=wrong) | {s['avg_judge_score']:.2f} |" if s.get("avg_judge_score") is not None else "| Avg judge score | N/A |",
        f"| Questions answered | {s['n_answered']}/{s['n_total']} |",
        "",
        "Numeric match: answer contains the reference value ±1%.",
        "Retrieval hit: evidence excerpt overlaps ≥40% with retrieved chunks.",
        "Judge: Haiku 4.5 scores each answer 1 (correct) / 2 (partial) / 3 (wrong).",
        "",
        "Full per-question results: `data/evaluation/financebench_results.json`",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ClaudeClient_estimate(model, in_tok, out_tok, n, batch=False):
    from src.evaluation.claude_client import ClaudeClient
    return ClaudeClient.estimate_cost(model, in_tok, out_tok, n, batch=batch)


if __name__ == "__main__":
    main()
