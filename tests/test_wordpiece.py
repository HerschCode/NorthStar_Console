"""The pure-Python WordPiece tokenizer (gateway/detectors/wordpiece.py) must reproduce the Hugging Face fast tokenizer the student was trained with.

CI cannot import the reference (it would pull in a dozen packages), so tests/fixtures/wordpiece_golden.json holds 473 texts and the ids the reference gave them: ordinary prompts from the project's data
and hand-written edge cases (zero-width and format characters, private-use and unassigned code points, combining marks, CJK, emoji, a 150-character word, a JSON blob, control characters).
scripts/wordpiece_fuzz.py is the exhaustive check, run where the reference is installed: it compares every Unicode code point in three contexts and reports zero differences under
Python 3.12 (Unicode 15.0). The golden cases are the part of that evidence CI can re-run.
"""
import json
from pathlib import Path

import pytest

from gateway.detectors.wordpiece import WordPieceTokenizer

REPO = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((REPO / "tests" / "fixtures" / "wordpiece_golden.json").read_text(encoding="utf-8"))["cases"]
TOKENIZER_JSON = REPO / "models" / "guard_student" / "tokenizer.json"
needs_tokenizer = pytest.mark.skipif(not TOKENIZER_JSON.exists(), reason="models/guard_student/tokenizer.json not present")


@pytest.fixture(scope="module")
def tok():
    return WordPieceTokenizer.from_tokenizer_json(TOKENIZER_JSON)


@needs_tokenizer
def test_every_golden_case_matches_the_reference_ids(tok):
    wrong = [(c["text"][:60], len(c["ids"])) for c in GOLDEN if tok.encode(c["text"], 256) != c["ids"]]
    assert not wrong, f"{len(wrong)} of {len(GOLDEN)} golden cases differ, e.g. {wrong[:3]}"


@needs_tokenizer
def test_the_fixture_is_not_trivially_easy():
    texts = [c["text"] for c in GOLDEN]
    assert len(texts) >= 400
    assert any("​" in t for t in texts) and any("" in t for t in texts) and any(len(t) > 1000 for t in texts) and any(ord(ch) > 0xFFFF for t in texts for ch in t)


@needs_tokenizer
def test_a_literal_special_token_in_the_text_is_ordinary_text_not_structure(tok):
    ids = tok.encode("hello [SEP] [CLS] [MASK] world", 256)
    assert ids.count(tok.sep) == 1 and ids.count(tok.cls) == 1, ids        # only the real framing tokens; the bracketed words are plain characters
    assert ids[0] == tok.cls and ids[-1] == tok.sep


@needs_tokenizer
def test_truncation_keeps_the_framing_tokens_and_the_limit(tok):
    ids = tok.encode("word " * 5000, 256)
    assert len(ids) == 256 and ids[0] == tok.cls and ids[-1] == tok.sep
    assert tok.encode("", 256) == [tok.cls, tok.sep]


def test_a_tokenizer_json_with_unsupported_settings_is_refused(tmp_path):
    bad = json.loads(TOKENIZER_JSON.read_text(encoding="utf-8")) if TOKENIZER_JSON.exists() else None
    if bad is None:
        pytest.skip("no tokenizer.json to modify")
    bad["normalizer"]["lowercase"] = False
    path = tmp_path / "tokenizer.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        WordPieceTokenizer.from_tokenizer_json(path)
