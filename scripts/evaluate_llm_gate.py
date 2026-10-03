"""Evaluate the LLM faithfulness judge against the existing labeled answer set.

Compares:
  lexical (support@0.65)   — the deployed gate (claim_support.py)
  llm_judge                — the new LLM-based judge (src/evaluation/llm_gate.py)
  ensemble                 — lexical AND llm_judge (both must pass)

The labeled set contains up to 96 rows (32 IDs × 3 types):
  correct     — the stored in-domain answer (should PASS the gate)
  wrong_fact  — same answer with one claim mutated (should be BLOCKED)
  off_context — correct answer scored against a partner question's chunks (should be BLOCKED)

Chunks are retrieved fresh from the live index via hybrid_search.
Off-context rows use their recorded `partner` question's chunks.

Usage:
  python -m scripts.evaluate_llm_gate                   # full run (Groq)
  python -m scripts.evaluate_llm_gate --dry-run         # no LLM calls
  python -m scripts.evaluate_llm_gate --provider gemini # AI Studio Gemini key
  python -m scripts.evaluate_llm_gate --provider gemini --model gemini-3.8-flash
  python -m scripts.evaluate_llm_gate --model openai/gpt-oss-20b
  python -m scripts.evaluate_llm_gate --freeze-contexts data/evaluation/p2_gate_contexts.json
  python -m scripts.evaluate_llm_gate --provider gemini --context-snapshot data/evaluation/p2_gate_contexts.json

Output:
  reports/llm_gate_eval.json   — per-row results
  docs/llm-gate-eval.md        — comparison report
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

LABELED = REPO_ROOT / "reports" / "gate_labeled_eval.json"
FAITH = REPO_ROOT / "data" / "evaluation" / "faithfulness_results.json"
OUT = REPO_ROOT / "reports" / "llm_gate_eval.json"
REPORT = REPO_ROOT / "docs" / "llm-gate-eval.md"

_SUPPORT_MIN_RECALL = 0.65


# ── lexical gate ─────────────────────────────────────────────────────────────

def _lexical_pass(answer: str, chunks: list[str]) -> bool:
    from src.evaluation.claim_support import answer_supported
    ok, _ = answer_supported(answer, chunks, min_recall=_SUPPORT_MIN_RECALL)
    return ok


# ── retrieve chunks ──────────────────────────────────────────────────────────

_chunk_cache: dict[int, list[str]] = {}
_frozen_contexts: dict[int, list[str]] | None = None


def _get_chunks(qid: int, question: str) -> list[str]:
    if _frozen_contexts is not None:
        if qid not in _frozen_contexts:
            raise ValueError(f"Frozen context snapshot is missing question id {qid}")
        return _frozen_contexts[qid]
    if qid not in _chunk_cache:
        from src.retrieval.search import hybrid_search
        hits = hybrid_search(question, top_k=5)
        _chunk_cache[qid] = [h.text for h in hits]
    return _chunk_cache[qid]


def _source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_context_snapshot(path: Path, contexts: dict[int, list[str]]) -> None:
    snapshot = {
        "schema_version": 1,
        "top_k": 5,
        "source_sha256": {
            "labeled_answers": _source_hash(LABELED),
            "faithfulness_questions": _source_hash(FAITH),
        },
        "contexts": {str(qid): chunks for qid, chunks in sorted(contexts.items())},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_context_snapshot(path: Path) -> dict[int, list[str]]:
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    expected_hashes = {
        "labeled_answers": _source_hash(LABELED),
        "faithfulness_questions": _source_hash(FAITH),
    }
    if snapshot.get("schema_version") != 1 or snapshot.get("top_k") != 5:
        raise ValueError(f"Unsupported context snapshot format: {path}")
    if snapshot.get("source_sha256") != expected_hashes:
        raise ValueError(
            f"Context snapshot does not match the current labeled dataset: {path}"
        )
    contexts = snapshot.get("contexts")
    if not isinstance(contexts, dict):
        raise ValueError(f"Invalid contexts in snapshot: {path}")
    return {int(qid): chunks for qid, chunks in contexts.items()}


# ── dry-run stub ─────────────────────────────────────────────────────────────

class _DryLLMGate:
    model = "dry-run"

    def judge(self, question, answer, chunks):
        from src.evaluation.llm_gate import JudgeResult
        # Mimic simple lexical pass as a stand-in — just for testing the plumbing
        from src.evaluation.claim_support import answer_supported
        ok, _ = answer_supported(answer, chunks, min_recall=_SUPPORT_MIN_RECALL)
        label = "FAITHFUL" if ok else "UNFAITHFUL"
        return JudgeResult(faithful=ok, reason=f"[dry-run] {label}", raw=f"{label}: dry run")


# ── scoring ───────────────────────────────────────────────────────────────────

def _expected_faithful(label: str) -> bool:
    return label == "correct"


def _is_judge_error(row: dict) -> bool:
    return row.get("judge_error", False)


def _confusion(rows: list[dict], gate_key: str) -> dict:
    """TP/FP/TN/FN where positive = FAITHFUL (pass), negative = UNFAITHFUL (block)."""
    evaluated = [r for r in rows if gate_key == "lexical" or not _is_judge_error(r)]
    tp = sum(1 for r in evaluated if r["expected_faithful"] and r[gate_key])
    fp = sum(1 for r in evaluated if not r["expected_faithful"] and r[gate_key])
    tn = sum(1 for r in evaluated if not r["expected_faithful"] and not r[gate_key])
    fn = sum(1 for r in evaluated if r["expected_faithful"] and not r[gate_key])
    n = len(evaluated)
    positives = sum(1 for r in evaluated if r["expected_faithful"])
    negatives = n - positives
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    acc = (tp + tn) / n if n else 0.0
    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "n_evaluated": n,
        "n_operational_errors": len(rows) - n,
        "precision": round(precision, 3), "recall": round(recall, 3),
        "f1": round(f1, 3), "accuracy": round(acc, 3),
        "correct_pass_rate": round(tp / positives, 3) if positives else None,
        "wrong_block_rate": round(tn / negatives, 3) if negatives else None,
    }


def _by_type(rows: list[dict], gate_key: str) -> dict:
    types = {"correct": [], "wrong_fact": [], "off_context": []}
    for r in rows:
        if r["label"] in types and (gate_key == "lexical" or not _is_judge_error(r)):
            types[r["label"]].append(r)
    return {
        t: {"pass_rate": round(sum(1 for r in rs if r[gate_key]) / len(rs), 3) if rs else None, "n": len(rs)}
        for t, rs in types.items()
    }


# ── main ─────────────────────────────────────────────────────────────────────

def _write_partial(results: list[dict], model: str, provider: str) -> None:
    summary = {g: {"confusion": _confusion(results, g), "by_type": _by_type(results, g)}
               for g in ["lexical", "llm", "ensemble"]}
    partial = {"provider": provider, "model": model, "n": len(results), "summary": summary, "rows": results}
    OUT.write_text(json.dumps(partial, indent=2, ensure_ascii=False), encoding="utf-8")


def _store_result(results: list[dict], positions: dict[tuple, int], result: dict) -> None:
    key = (result["id"], result["label"])
    if key in positions:
        results[positions[key]] = result
    else:
        positions[key] = len(results)
        results.append(result)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--provider", choices=["groq", "gemini"], default="groq")
    parser.add_argument("--model", default=None)
    parser.add_argument("--n", type=int, default=None, help="Limit to first N rows")
    parser.add_argument("--resume", action="store_true", help="Skip rows already in OUT")
    contexts = parser.add_mutually_exclusive_group()
    contexts.add_argument(
        "--freeze-contexts",
        type=Path,
        metavar="PATH",
        help="Snapshot retrieved evidence for this labeled set; exits without calling an LLM",
    )
    contexts.add_argument(
        "--context-snapshot",
        type=Path,
        metavar="PATH",
        help="Evaluate using a previously frozen evidence snapshot",
    )
    args = parser.parse_args()

    labeled = json.loads(LABELED.read_text(encoding="utf-8"))
    faith_map = {r["id"]: r for r in json.loads(FAITH.read_text(encoding="utf-8"))["results"]}
    rows_in = labeled["rows"]
    if args.n:
        rows_in = rows_in[:args.n]

    required_ids = {row["id"] for row in rows_in}
    required_ids.update(
        row.get("partner", row["id"])
        for row in rows_in
        if row["label"] == "off_context"
    )
    if args.freeze_contexts:
        frozen = {}
        for qid in sorted(required_ids):
            faith_row = faith_map.get(qid)
            if faith_row is None:
                raise ValueError(f"No question found for id {qid} in {FAITH}")
            frozen[qid] = _get_chunks(qid, faith_row["question"])
        _write_context_snapshot(args.freeze_contexts, frozen)
        print(f"Frozen retrieval contexts for {len(frozen)} questions: {args.freeze_contexts}")
        return
    if args.context_snapshot:
        global _frozen_contexts
        _frozen_contexts = _load_context_snapshot(args.context_snapshot)
        missing_ids = required_ids - _frozen_contexts.keys()
        if missing_ids:
            raise ValueError(f"Frozen context snapshot is missing ids: {sorted(missing_ids)}")

    expected_model = args.model or (
        "openai/gpt-oss-120b" if args.provider == "groq" else "gemini-3.8-flash"
    )

    # Never mix results from different providers or models in one benchmark run.
    already_done: set[tuple] = set()
    prior_results: list[dict] = []
    if args.resume and OUT.exists():
        prior = json.loads(OUT.read_text(encoding="utf-8"))
        if prior.get("provider") != args.provider or prior.get("model") != expected_model:
            raise ValueError(
                "Cannot resume with a different or unspecified provider/model; "
                f"results use {prior.get('provider')}/{prior.get('model')}, "
                f"requested {args.provider}/{expected_model}"
            )
        prior_results = prior.get("rows", [])
        already_done = {
            (r["id"], r["label"])
            for r in prior_results
            if not _is_judge_error(r)
        }
        print(f"Resuming: {len(already_done)} rows already done.")
    results = list(prior_results)
    result_positions = {
        (r["id"], r["label"]): index
        for index, r in enumerate(results)
    }

    if args.dry_run:
        llm_gate = _DryLLMGate()
        print("DRY RUN — no LLM calls.")
    else:
        from src.evaluation.llm_gate import LLMGate
        llm_gate = LLMGate(model=args.model, provider=args.provider)

    provider = getattr(llm_gate, "provider", args.provider)
    print(f"Evaluating {len(rows_in)} rows | provider={provider} model={llm_gate.model}")

    for i, row in enumerate(rows_in):
        qid = row["id"]
        label = row["label"]
        if (qid, label) in already_done:
            print(f"  [{i+1:03d}] id={qid} {label:<12} | skip (already done)", flush=True)
            continue
        answer = row["answer"]
        faith_row = faith_map.get(qid, {})
        question = faith_row.get("question", f"question_{qid}")

        # Correct chunks = this question's chunks; off_context = partner's chunks
        if label == "off_context":
            partner_id = row.get("partner", qid)
            partner_q = faith_map.get(partner_id, {}).get("question", question)
            chunks = _get_chunks(partner_id, partner_q)
        else:
            chunks = _get_chunks(qid, question)

        t0 = time.time()
        lex = _lexical_pass(answer, chunks)
        t_lex = time.time() - t0

        t0 = time.time()
        llm_result = llm_gate.judge(question, answer, chunks)
        t_llm = time.time() - t0

        expected = _expected_faithful(label)
        ensemble = lex and llm_result.faithful
        judge_error = (
            llm_result.reason.startswith("[api error]")
            or llm_result.reason.startswith("[parse error]")
        )

        status_parts = []
        for k, v, exp in [("lex", lex, expected), ("llm", llm_result.faithful, expected), ("ens", ensemble, expected)]:
            correct = v == exp
            status_parts.append(f"{k}:{'OK' if correct else 'FAIL'}")

        print(f"  [{i+1:03d}] id={qid} {label:<12} | {' '.join(status_parts)} | {llm_result.reason[:60]!r} [{t_llm:.1f}s]", flush=True)

        _store_result(results, result_positions, {
            "id": qid,
            "label": label,
            "question": question,
            "answer": answer[:120],
            "expected_faithful": expected,
            "lexical": lex,
            "llm": llm_result.faithful,
            "ensemble": ensemble,
            "judge_error": judge_error,
            "llm_reason": llm_result.reason,
            "llm_raw": llm_result.raw[:200],
            "t_lex_s": round(t_lex, 3),
            "t_llm_s": round(t_llm, 3),
        })

        # Persist each paid/API call so an interruption does not lose completed work.
        _write_partial(results, llm_gate.model, provider)

        # Pace requests to stay within TPM limits (~8k TPM for 120b; each call ~600 tokens)
        if not args.dry_run:
            time.sleep(1.5)

    print(flush=True)
    gates = ["lexical", "llm", "ensemble"]
    summary = {}
    for g in gates:
        conf = _confusion(results, g)
        by_type = _by_type(results, g)
        summary[g] = {"confusion": conf, "by_type": by_type}
        print(f"{g:>10}: acc={conf['accuracy']:.1%}  f1={conf['f1']:.3f}  "
              f"correct_pass={conf['correct_pass_rate']:.1%}  "
              f"wrong_block={conf['wrong_block_rate']:.1%}")

    out = {
        "provider": provider,
        "model": llm_gate.model,
        "n": len(results),
        "summary": summary,
        "rows": results,
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults written to {OUT}")

    _write_report(out)
    print(f"Report written to {REPORT}")


def _write_report(out: dict) -> None:
    model = out["model"]
    n = out["n"]
    s = out["summary"]

    def _pct(v):
        return f"{v:.1%}" if v is not None else "N/A"

    # Compute per-type totals from the data
    _bt = s["lexical"]["by_type"]
    n_correct = _bt.get("correct", {}).get("n", 0)
    n_wrong = _bt.get("wrong_fact", {}).get("n", 0)
    n_off = _bt.get("off_context", {}).get("n", 0)

    lines = [
        "# LLM gate evaluation",
        "",
        f"**Provider:** {out.get('provider', 'unknown')}  ",
        f"**Model:** {model}  ",
        f"**Rows evaluated:** {n} ({n_correct} correct, {n_wrong} wrong_fact, {n_off} off_context)",
        "",
        "The lexical gate (`claim_support.py`, `support@0.65`) cannot detect polarity flips",
        "(`included` vs `excluded`, `Yes` vs `No`, `required` vs `optional`).",
        "The LLM judge is intended to assess semantic entailment rather than term overlap.",
        "",
        "## Overall scores",
        "",
        "| Gate | Accuracy | F1 | Correct pass rate | Wrong blocked rate |",
        "|---|---|---|---|---|",
    ]
    for g in ["lexical", "llm", "ensemble"]:
        c = s[g]["confusion"]
        lines.append(
            f"| {g} | {_pct(c['accuracy'])} | {c['f1']:.3f} "
            f"| {_pct(c['correct_pass_rate'])} | {_pct(c['wrong_block_rate'])} |"
        )
        if g != "lexical":
            lines.append(
                f"\n{g} scored {c['n_evaluated']} rows; "
                f"{c['n_operational_errors']} API/parse errors were excluded from classification metrics.\n"
            )

    lines += ["", "## Per-type pass rates (lower = gate firing more)", ""]
    for g in ["lexical", "llm", "ensemble"]:
        bt = s[g]["by_type"]
        lines += [
            f"### {g}",
            "",
            "| Type | Pass rate | n |",
            "|---|---|---|",
        ]
        for t in ["correct", "wrong_fact", "off_context"]:
            v = bt.get(t, {})
            lines.append(f"| {t} | {_pct(v.get('pass_rate'))} | {v.get('n', 0)} |")
        lines.append("")

    # Flip analysis: rows where lexical and LLM disagree
    rows = out["rows"]
    disagreements = [
        r for r in rows
        if not _is_judge_error(r) and r["lexical"] != r["llm"]
    ]
    operational_errors = [r for r in rows if _is_judge_error(r)]
    lines += [
        "## Disagreements (lexical ≠ LLM judge)",
        "",
        f"{len(disagreements)} rows where the two gates disagree:",
        "",
    ]
    for r in disagreements:
        lex_label = "PASS" if r["lexical"] else "BLOCK"
        llm_label = "PASS" if r["llm"] else "BLOCK"
        expected_str = "FAITHFUL" if r["expected_faithful"] else "UNFAITHFUL"
        lex_correct = (r["lexical"] == r["expected_faithful"])
        llm_correct = (r["llm"] == r["expected_faithful"])
        lines += [
            f"**id={r['id']} ({r['label']})** — expected {expected_str}",
            f"- Lexical: {lex_label} ({'✓' if lex_correct else '✗'})",
            f"- LLM: {llm_label} ({'✓' if llm_correct else '✗'}) — {r['llm_reason'][:120]}",
            f"- Answer: {r['answer'][:100]}",
            "",
        ]

    if not disagreements:
        lines.append("None — gates agree on all rows.\n")
    lines += [
        f"Operational API/parse errors excluded from judge comparisons: {len(operational_errors)}.",
        "",
    ]

    lines += [
        "## Design notes",
        "",
        "- **Same evaluation set**: compared on the existing labeled set",
        "  (`reports/gate_labeled_eval.json`), not a new holdout. Correct answers were",
        "  labeled by one author; results measure within-sample performance. An independent",
        "  holdout and second annotator would be stronger.",
        "- **Chunks**: retrieved fresh from the live index via `hybrid_search(top_k=5)`",
        "  at eval time unless `--context-snapshot` is supplied. Use `--freeze-contexts`",
        "  once to save exact evidence; the snapshot is bound to the labeled source files",
        "  with SHA-256 hashes. Off-context rows use the recorded `partner` question's chunks.",
        "- **Labeling**: `correct` answers were hand-checked against the policy documents",
        "  (same author as this eval — disclosed in `data/evaluation/gate_labels.json`).",
        "- **Polarity flips**: `wrong_fact` rows include mutated numbers, swapped team",
        "  names, and yes/no inversions. Results are reported separately by label; neither",
        "  gate is assumed to catch a mutation without measured evidence.",
        "- **Provider**: Groq and Gemini are selectable (`--provider groq|gemini`).",
        "  API errors and unparseable/truncated responses fail closed and are excluded from",
        "  classification metrics, with their counts reported separately.",
    ]

    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
