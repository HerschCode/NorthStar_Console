"""Retrieval-only evaluation on FinanceBench: does P2's retriever find the page a question's evidence comes from? No model, no key.

FinanceBench (PatronusAI/financebench, Islam et al., 2023; CC BY-NC 4.0, non-commercial; not gated): 150 questions over 84 real 10-K / 10-Q / 8-K filings, each with the human-selected evidence
page(s). Data is downloaded once to data/external/financebench/ (git-ignored), pinned to the dataset revision and recorded with SHA-256 in MANIFEST.json.

THIS IS A CLOSED-SET PROXY, and optimistic. The corpus is only the evidence pages of the 150 questions (about 190 pages from the 84 filings), cut into overlapping chunks, not the filings
themselves (thousands of pages). A question has to find its page among those, where other questions' pages from the same company, and often the same filing, are the distractors. The real task
(retrieve from a whole filing) is harder, and its hit rates will be lower. Hit@k here means: one of the top-k chunks comes from a page the dataset marks as evidence for that question.

P2's own functions run, unchanged: semantic_search (dense, chromadb's default embedding), bm25_search and hybrid_search (reciprocal-rank fusion of the two), against a throw-away collection in a
temporary Chroma directory (CHROMA_PERSIST_DIR), so the running service's store is untouched. min_similarity is 0 so this ranks rather than abstains.

    python -X utf8 -m scripts.eval_financebench_retrieval

Writes reports/financebench_retrieval.json. Answer accuracy needs a model and a key and is NOT measured here.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.eval_gate_ragtruth import wilson  # noqa: E402

DATA = REPO_ROOT / "data" / "external" / "financebench"
REPORT = REPO_ROOT / "reports" / "financebench_retrieval.json"
DATASET, FILE = "PatronusAI/financebench", "financebench_merged.jsonl"
COLLECTION = "financebench_retrieval_eval"
CHUNK_CHARS, CHUNK_OVERLAP = 1000, 200
KS = (1, 3, 5, 10)


def fetch() -> dict:
    manifest_path = DATA / "MANIFEST.json"
    path = DATA / FILE
    if manifest_path.exists() and path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if hashlib.sha256(path.read_bytes()).hexdigest() == manifest["sha256"]:
            return manifest
    DATA.mkdir(parents=True, exist_ok=True)
    revision = json.load(urllib.request.urlopen(f"https://huggingface.co/api/datasets/{DATASET}", timeout=30))["sha"]                                    # nosec B310 - fixed https URL
    data = urllib.request.urlopen(f"https://huggingface.co/datasets/{DATASET}/resolve/{revision}/{FILE}", timeout=120).read()                           # nosec B310 - fixed https URL
    path.write_bytes(data)
    manifest = {"dataset": DATASET, "revision": revision, "licence": "CC BY-NC 4.0", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def load_questions() -> list[dict]:
    rows = []
    for line in (DATA / FILE).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        evidence = r["evidence"]
        rows.append({"id": r["financebench_id"], "question": r["question"], "type": r["question_type"], "company": r["company"], "doc": r["doc_name"],
                     "gold": sorted({page_id(e["doc_name"], e["evidence_page_num"]) for e in evidence}),
                     "pages": {page_id(e["doc_name"], e["evidence_page_num"]): e["evidence_text_full_page"] for e in evidence}})
    return rows


def page_id(doc: str, page) -> str:
    return f"{doc}#p{page}"


def chunk_page(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= size:
        return [text]
    step = size - overlap
    return [text[i:i + size] for i in range(0, max(len(text) - overlap, 1), step)]


def build_chunks(questions: list[dict]) -> list[dict]:
    pages: dict[str, str] = {}
    for q in questions:
        pages.update(q["pages"])
    chunks = []
    for pid in sorted(pages):
        for i, text in enumerate(chunk_page(pages[pid])):
            chunks.append({"id": f"{pid}#c{i}", "page": pid, "doc": pid.split("#p")[0], "index": i, "text": text})
    return chunks


def first_gold_rank(retrieved_pages: list[str], gold: list[str]) -> int | None:
    """1-based rank of the first retrieved chunk that comes from a gold page, or None."""
    for rank, pid in enumerate(retrieved_pages, 1):
        if pid in gold:
            return rank
    return None


def summarise(ranks: list[int | None]) -> dict:
    n = len(ranks)
    out = {"n": n}
    for k in KS:
        hits = sum(1 for r in ranks if r is not None and r <= k)
        out[f"hit@{k}"] = {"k": hits, "n": n, "rate": round(hits / n, 3) if n else None, "ci95": wilson(hits, n)}
    out["mrr@10"] = round(float(np.mean([1 / r if r is not None and r <= 10 else 0.0 for r in ranks])), 3) if n else None
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    manifest = fetch()
    tmp = tempfile.mkdtemp(prefix="financebench-chroma-")
    os.environ["CHROMA_PERSIST_DIR"] = tmp                       # before the vector store is imported: the service's own store is not touched
    from src.retrieval.embeddings import embed_texts
    from src.retrieval.search import bm25_search, hybrid_search, semantic_search
    from src.retrieval.vector_store import upsert_chunks

    questions = load_questions()
    chunks = build_chunks(questions)
    print(f"{len(questions)} questions, {len({c['page'] for c in chunks})} evidence pages, {len(chunks)} chunks; indexing ...", flush=True)
    for i in range(0, len(chunks), 100):
        batch = chunks[i:i + 100]
        upsert_chunks(COLLECTION, [c["id"] for c in batch], [c["text"] for c in batch], embed_texts([c["text"] for c in batch]),
                      [{"document_id": c["page"], "title": c["doc"], "section_title": "", "chunk_index": c["index"]} for c in batch])

    methods = {"dense (semantic_search)": lambda q: semantic_search(q, top_k=max(KS), min_similarity=0.0, collection_name=COLLECTION),
               "BM25 (bm25_search)": lambda q: bm25_search(q, top_k=max(KS), collection_name=COLLECTION),
               "hybrid, RRF (hybrid_search)": lambda q: hybrid_search(q, top_k=max(KS), min_similarity=0.0, collection_name=COLLECTION)}
    ranks = {name: [] for name in methods}
    for n, q in enumerate(questions, 1):
        for name, fn in methods.items():
            ranks[name].append(first_gold_rank([r.document_id for r in fn(q["question"])], q["gold"]))
        if n % 50 == 0:
            print(f"  {n}/{len(questions)}", flush=True)

    types = sorted({q["type"] for q in questions})
    results = {"source": {**manifest, "note": "closed-set proxy: the corpus is the evidence pages of the 150 questions only"},
               "label": "retrieval only; no model was called; answer accuracy is not measured",
               "corpus": {"questions": len(questions), "pages": len({c["page"] for c in chunks}), "chunks": len(chunks), "chunk_chars": CHUNK_CHARS, "overlap": CHUNK_OVERLAP},
               "overall": {name: summarise(r) for name, r in ranks.items()},
               "by_question_type": {t: {name: summarise([r for r, q in zip(rs, questions) if q["type"] == t]) for name, rs in ranks.items()} for t in types}}
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\n{'method':<30}" + "".join(f"{'Hit@' + str(k):>10}" for k in KS) + f"{'MRR@10':>9}")
    for name, s in results["overall"].items():
        print(f"{name:<30}" + "".join(f"{s[f'hit@{k}']['rate']:>10.3f}" for k in KS) + f"{s['mrr@10']:>9.3f}")
    print(f"wrote {REPORT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
