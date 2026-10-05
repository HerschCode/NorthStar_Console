"""The pure parts of scripts/eval_financebench_retrieval.py (no download, no index): how pages become gold labels and chunks, and how ranks become hit rates."""
import json

import pytest

from scripts import eval_financebench_retrieval as fb


def record(i, evidence, qtype="metrics-generated"):
    return {"financebench_id": f"id_{i}", "company": "Acme", "doc_name": "Acme_2020_10K", "question_type": qtype, "question": f"question {i}?", "answer": "a",
            "evidence": [{"evidence_text": "t", "doc_name": "Acme_2020_10K", "evidence_page_num": p, "evidence_text_full_page": text} for p, text in evidence]}


def test_questions_get_one_gold_page_per_distinct_evidence_page(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "DATA", tmp_path)
    (tmp_path / fb.FILE).write_text("\n".join(json.dumps(r) for r in [record(1, [(10, "page ten"), (10, "page ten again"), (12, "page twelve")]), record(2, [(10, "page ten")], "novel-generated")]), encoding="utf-8")
    qs = fb.load_questions()
    assert qs[0]["gold"] == ["Acme_2020_10K#p10", "Acme_2020_10K#p12"] and qs[1]["gold"] == ["Acme_2020_10K#p10"] and qs[1]["type"] == "novel-generated"


def test_chunks_cover_each_page_once_per_distinct_page():
    long_page = "x" * 2500
    qs = [{"pages": {"D#p1": "short page", "D#p2": long_page}}, {"pages": {"D#p2": long_page}}]
    chunks = fb.build_chunks(qs)
    assert {c["page"] for c in chunks} == {"D#p1", "D#p2"}
    assert [c["index"] for c in chunks if c["page"] == "D#p1"] == [0]
    p2 = [c for c in chunks if c["page"] == "D#p2"]
    assert len(p2) == len(fb.chunk_page(long_page)) > 2 and all(len(c["text"]) <= fb.CHUNK_CHARS for c in p2)
    assert len({c["id"] for c in chunks}) == len(chunks)


def test_chunk_windows_overlap_and_reach_the_end():
    text = "".join(chr(97 + i % 26) for i in range(2300))
    chunks = fb.chunk_page(text, size=1000, overlap=200)
    assert chunks[0] == text[:1000] and chunks[1][:200] == chunks[0][-200:] and text.endswith(chunks[-1][-100:])
    assert fb.chunk_page("tiny", size=1000, overlap=200) == ["tiny"]


def test_first_gold_rank_is_one_based_and_none_when_absent():
    assert fb.first_gold_rank(["x", "y", "G", "G"], ["G"]) == 3
    assert fb.first_gold_rank(["G"], ["G", "H"]) == 1
    assert fb.first_gold_rank(["x", "y"], ["G"]) is None


def test_summary_counts_hits_at_each_k_and_the_reciprocal_rank():
    s = fb.summarise([1, 3, None, 12, 5])
    assert (s["hit@1"]["k"], s["hit@3"]["k"], s["hit@5"]["k"], s["hit@10"]["k"]) == (1, 2, 3, 3) and s["n"] == 5
    assert s["mrr@10"] == pytest.approx((1 + 1 / 3 + 0 + 0 + 1 / 5) / 5, abs=1e-3)       # a rank beyond 10 scores 0, like a miss
    lo, hi = s["hit@5"]["ci95"]
    assert lo < 0.6 < hi
