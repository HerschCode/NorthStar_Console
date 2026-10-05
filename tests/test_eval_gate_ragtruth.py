"""The pure parts of scripts/eval_gate_ragtruth.py (no download, no model): how a source is assembled, chunked and labelled, and that the metrics are computed correctly."""
import json

import numpy as np
import pytest

from scripts import eval_gate_ragtruth as eg


def test_source_text_handles_each_task_shape():
    assert eg.source_text("a news article") == "a news article"
    assert eg.source_text({"question": "q?", "passages": "passage 1: x"}) == "passage 1: x"
    assert json.loads(eg.source_text({"name": "Café", "stars": 4})) == {"name": "Café", "stars": 4}


def test_chunks_cover_the_text_with_overlap():
    words = [f"w{i}" for i in range(500)]
    chunks = eg.chunk_text(" ".join(words), words=200, overlap=40)
    assert all(len(c.split()) <= 200 for c in chunks)
    assert chunks[0].split()[0] == "w0" and "w499" in chunks[-1].split()
    assert set(chunks[0].split()[-40:]) <= set(chunks[1].split())           # consecutive windows share the overlap
    assert eg.chunk_text("short text", words=200) == ["short text"]


def test_wilson_interval_brackets_the_rate_and_widens_for_small_n():
    lo, hi = eg.wilson(50, 100)
    assert lo < 0.5 < hi and 0.40 < lo and hi < 0.60
    assert eg.wilson(5, 10)[1] - eg.wilson(5, 10)[0] > hi - lo
    assert eg.wilson(0, 0) == [0.0, 0.0]


def test_auc_matches_hand_computed_values():
    y = np.array([1, 1, 0, 0])
    assert eg.auc(np.array([0.9, 0.8, 0.2, 0.1]), y) == 1.0
    assert eg.auc(np.array([0.1, 0.2, 0.8, 0.9]), y) == 0.0
    assert eg.auc(np.array([0.5, 0.5, 0.5, 0.5]), y) == 0.5                   # all ties
    assert eg.auc(np.array([0.9, 0.3, 0.6, 0.1]), y) == 0.75                  # 3 of the 4 positive-negative pairs are ordered correctly
    assert eg.auc(np.array([1.0, 2.0]), np.array([1, 1])) is None             # one class only


def test_metrics_count_the_right_cells():
    y = np.array([1, 1, 1, 0, 0, 0, 0, 0])
    flagged = np.array([True, True, False, True, False, False, False, False])
    m = eg.metrics(flagged, y)
    assert (m["recall"]["k"], m["recall"]["n"]) == (2, 3) and (m["false_positive_rate"]["k"], m["false_positive_rate"]["n"]) == (1, 5)
    assert m["precision"] == pytest.approx(2 / 3, abs=1e-3) and m["balanced_accuracy"] == pytest.approx((2 / 3 + 4 / 5) / 2, abs=1e-3)
    everything = eg.metrics(np.ones(8, dtype=bool), y)
    assert everything["recall"]["rate"] == 1.0 and everything["false_positive_rate"]["rate"] == 1.0 and everything["precision"] == pytest.approx(3 / 8, abs=1e-3)


def test_rows_join_responses_to_sources_and_label_any_span_as_hallucinated(tmp_path, monkeypatch):
    monkeypatch.setattr(eg, "DATA", tmp_path)
    (tmp_path / "source_info.jsonl").write_text("\n".join(json.dumps(s) for s in [
        {"source_id": "s1", "task_type": "Summary", "source_info": "an article", "prompt": "p"},
        {"source_id": "s2", "task_type": "QA", "source_info": {"question": "q", "passages": "the passages"}, "prompt": "p"}]), encoding="utf-8")
    (tmp_path / "response.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"id": "1", "source_id": "s1", "model": "m-a", "labels": [], "split": "test", "quality": "good", "response": "fine"},
        {"id": "2", "source_id": "s2", "model": "m-b", "labels": [{"start": 0, "end": 3, "label_type": "Evident Baseless Info"}], "split": "test", "quality": "good", "response": "invented"},
        {"id": "3", "source_id": "s1", "model": "m-a", "labels": [], "split": "train", "quality": "good", "response": "not in test"}]), encoding="utf-8")
    rows = eg.load_rows("test")
    assert [(r["id"], r["task"], r["hallucinated"], r["source"]) for r in rows] == [("1", "Summary", False, "an article"), ("2", "QA", True, "the passages")]
    assert len(eg.load_rows("test", limit=1)) == 1 and eg.load_rows("test", limit=1) == eg.load_rows("test", limit=1)      # the sample is seeded
