"""Score the claim-support gate and the NLI baseline on RAGTruth's human labels, with no model call and no key.

RAGTruth (ParticleMedia/RAGTruth, MIT; Niu et al., 2024): 2,700 test responses (6 models x 450) to retrieval-augmented QA, data-to-text and summarisation prompts, each with the source
text it was given and span-level hallucination labels written by human annotators. A response counts as HALLUCINATED here when it has at least one labelled span; the question for a gate is
whether it flags those. Data is downloaded once to data/external/ragtruth/ (git-ignored), pinned to a commit and recorded with SHA-256 in MANIFEST.json.

Two gates, as deployed (not tuned to this data):
  lexical  src/evaluation/claim_support.py  answer_supported(response, [source], min_recall=0.65): every sentence's numbers, key terms and content words must appear in the source.
  nli      src/evaluation/faithfulness.py   cross-encoder/nli-deberta-v3-small: every response sentence needs entailment >= 0.5 against some chunk of the source (200-word windows).
Both give a verdict at their deployed threshold and a continuous score for ROC-AUC (lexical: the weakest sentence's content recall, minus 1 if its numbers or key terms fail; nli: the weakest
sentence's best entailment). "flag everything" is the trivial baseline: precision equals the share of hallucinated responses.

What this does not say: that the gates work on procurement answers (RAGTruth is news, Yelp business data and MS MARCO passages); a response-level label from span annotations is coarser than the
claim-level checks the gates make; and LLM-AggreFact, the other public set planned for this, is gated on Hugging Face (CC BY-ND 4.0, needs an account that accepted its terms) and is NOT included.

    python -X utf8 -m scripts.eval_gate_ragtruth [--limit N] [--no-nli]

Writes reports/gate_ragtruth_nokey.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DATA = REPO_ROOT / "data" / "external" / "ragtruth"
REPORT = REPO_ROOT / "reports" / "gate_ragtruth_nokey.json"
REPO = "ParticleMedia/RAGTruth"
FILES = ("dataset/response.jsonl", "dataset/source_info.jsonl")
MIN_RECALL = 0.65            # the deployed lexical threshold
NLI_ENTAILMENT = 0.5         # the deployed NLI threshold (src/evaluation/faithfulness.py)
CHUNK_WORDS, CHUNK_OVERLAP = 200, 40


def fetch() -> dict:
    manifest_path = DATA / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    DATA.mkdir(parents=True, exist_ok=True)
    if manifest and all((DATA / Path(f).name).exists() and hashlib.sha256((DATA / Path(f).name).read_bytes()).hexdigest() == manifest["files"][f]["sha256"] for f in FILES):
        return manifest
    sha = json.load(urllib.request.urlopen(f"https://api.github.com/repos/{REPO}/commits/main", timeout=30))["sha"]                      # nosec B310 - fixed https URL
    manifest = {"repo": REPO, "commit": sha, "licence": "MIT", "files": {}}
    for f in FILES:
        data = urllib.request.urlopen(f"https://raw.githubusercontent.com/{REPO}/{sha}/{f}", timeout=120).read()                       # nosec B310 - fixed https URL
        (DATA / Path(f).name).write_bytes(data)
        manifest["files"][f] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def source_text(info) -> str:
    """The text a response was written from. QA and summarisation sources are strings (or {question, passages}); data-to-text sources are a JSON record."""
    if isinstance(info, str):
        return info
    if isinstance(info, dict) and "passages" in info:
        return str(info["passages"])
    return json.dumps(info, ensure_ascii=False)


def load_rows(split: str = "test", limit: int | None = None) -> list[dict]:
    sources = {}
    for line in (DATA / "source_info.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            sources[r["source_id"]] = r
    rows = []
    for line in (DATA / "response.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["split"] != split:
            continue
        s = sources[r["source_id"]]
        rows.append({"id": r["id"], "task": s["task_type"], "model": r["model"], "quality": r.get("quality"), "response": r["response"], "source": source_text(s["source_info"]),
                     "hallucinated": bool(r["labels"])})
    if limit:
        rng = np.random.default_rng(0)
        rows = [rows[i] for i in sorted(rng.choice(len(rows), min(limit, len(rows)), replace=False))]
    return rows


def chunk_text(text: str, words: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    toks = text.split()
    if len(toks) <= words:
        return [text]
    step = words - overlap
    return [" ".join(toks[i:i + words]) for i in range(0, max(len(toks) - overlap, 1), step)]


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 0.0]
    p, den = k / n, 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return [round(centre - half, 3), round(centre + half, 3)]


def auc(scores_flagged_high: np.ndarray, y: np.ndarray) -> float | None:
    """ROC-AUC where a HIGHER score means more likely hallucinated (rank-based, ties averaged)."""
    pos, neg = scores_flagged_high[y == 1], scores_flagged_high[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(np.r_[pos, neg], kind="mergesort")
    ranks = np.empty(len(order))
    allv = np.r_[pos, neg]
    sorted_v = allv[order]
    i = 0
    while i < len(sorted_v):
        j = i
        while j + 1 < len(sorted_v) and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return round(float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))), 3)


def metrics(flagged: np.ndarray, y: np.ndarray, score: np.ndarray | None = None) -> dict:
    """Positive class = hallucinated. recall = hallucinated responses flagged; false-positive rate = supported responses flagged."""
    tp, fp = int((flagged & (y == 1)).sum()), int((flagged & (y == 0)).sum())
    fn, tn = int((~flagged & (y == 1)).sum()), int((~flagged & (y == 0)).sum())
    n_pos, n_neg = tp + fn, fp + tn
    out = {"n": int(len(y)), "hallucinated": n_pos,
           "recall": {"k": tp, "n": n_pos, "rate": round(tp / n_pos, 3) if n_pos else None, "ci95": wilson(tp, n_pos)},
           "false_positive_rate": {"k": fp, "n": n_neg, "rate": round(fp / n_neg, 3) if n_neg else None, "ci95": wilson(fp, n_neg)},
           "precision": round(tp / (tp + fp), 3) if tp + fp else None,
           "balanced_accuracy": round((tp / n_pos + tn / n_neg) / 2, 3) if n_pos and n_neg else None}
    if score is not None:
        out["roc_auc"] = auc(score, y)
    return out


def lexical_scores(rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """(flagged, score) with score high = more likely hallucinated."""
    from src.evaluation.claim_support import answer_supported
    flagged, score = [], []
    for r in rows:
        ok, detail = answer_supported(r["response"], [r["source"]], MIN_RECALL)
        flagged.append(not ok)
        weakest = min((d["content_recall"] - (0 if d["numbers_supported"] and d["key_terms_supported"] else 1) for d in detail), default=-1.0)
        score.append(-weakest)
    return np.array(flagged), np.array(score)


NLI_CACHE = DATA / "nli_cache.json"


def nli_scores(rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Resumable: each response's result is kept in data/external/ragtruth/nli_cache.json, so a crash (a CUDA 'unknown error' ended one 40-minute run at 900 of 2,700) is re-run, not lost.
    The cache is keyed by response id, and is only valid for the model and thresholds named in its header."""
    from src.evaluation import faithfulness
    header = {"model": faithfulness.NLI_MODEL, "entailment": faithfulness.ENTAILMENT_THRESHOLD, "chunk_words": CHUNK_WORDS, "chunk_overlap": CHUNK_OVERLAP, "pair_order": "evidence first"}
    cache = {}
    if NLI_CACHE.exists():
        stored = json.loads(NLI_CACHE.read_text(encoding="utf-8"))
        if stored.get("header") == header:
            cache = stored["rows"]
    done = 0
    for i, r in enumerate(rows):
        if r["id"] not in cache:
            res = faithfulness.score_faithfulness(r["response"], chunk_text(r["source"]))
            if res.backend_used.startswith("error"):
                NLI_CACHE.write_text(json.dumps({"header": header, "rows": cache}), encoding="utf-8")
                raise RuntimeError(res.backend_used)
            cache[r["id"]] = [bool(res.n_sentences == 0 or res.n_grounded < res.n_sentences), -min((s.max_entailment for s in res.sentence_scores), default=0.0)]
            done += 1
            if done % 100 == 0:
                NLI_CACHE.write_text(json.dumps({"header": header, "rows": cache}), encoding="utf-8")
        if (i + 1) % 300 == 0:
            print(f"  nli {i + 1}/{len(rows)}", flush=True)
    NLI_CACHE.write_text(json.dumps({"header": header, "rows": cache}), encoding="utf-8")
    return np.array([cache[r["id"]][0] for r in rows]), np.array([cache[r["id"]][1] for r in rows])


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-nli", action="store_true")
    a = ap.parse_args()
    manifest = fetch()
    rows = load_rows("test", a.limit)
    y = np.array([r["hallucinated"] for r in rows]).astype(int)
    gates = {"flag everything (baseline)": (np.ones(len(rows), dtype=bool), None), "lexical claim-support gate (r=0.65, deployed)": lexical_scores(rows)}
    if not a.no_nli:
        gates["NLI baseline (nli-deberta-v3-small, entailment >= 0.5)"] = nli_scores(rows)

    def cut(mask):
        return {name: metrics(flag[mask], y[mask], None if sc is None else sc[mask]) for name, (flag, sc) in gates.items()}

    tasks = sorted({r["task"] for r in rows})
    models = sorted({r["model"] for r in rows})
    results = {"source": {"dataset": "RAGTruth test split", "repo": manifest["repo"], "commit": manifest["commit"], "licence": manifest["licence"], "files": manifest["files"]},
               "label": "no model was called to produce these numbers; human span labels collapsed to a response-level hallucinated / supported label",
               "n": len(rows), "hallucinated_share": round(float(y.mean()), 3), "overall": cut(np.ones(len(rows), dtype=bool)),
               "by_task": {t: cut(np.array([r["task"] == t for r in rows])) for t in tasks}, "by_model": {m: cut(np.array([r["model"] == m for r in rows])) for m in models}}
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"RAGTruth test: {len(rows)} responses, {results['hallucinated_share']:.1%} hallucinated")
    for name, m in results["overall"].items():
        print(f"{name:<56} recall {m['recall']['rate']}  FPR {m['false_positive_rate']['rate']}  precision {m['precision']}  bal.acc {m['balanced_accuracy']}  AUC {m.get('roc_auc')}")
    print(f"wrote {REPORT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
