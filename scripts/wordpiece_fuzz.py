"""
Compare gateway/detectors/wordpiece.py with the Hugging Face fast (Rust) tokenizer on EVERY Unicode codepoint.

The student was trained with the fast tokenizer, so serving must reproduce it exactly, not an idealised reference. Per codepoint c three strings are encoded by both (alone, between two letters,
and after a letter, which is where a combining mark acts): any difference in the ids is a mismatch. Codepoints whose behaviour differs are listed with their Unicode category.

    python -X utf8 scripts/wordpiece_fuzz.py data/external/guard_student/runs/hardkd_tkd-seed2/tokenizer.json

Exit status 1 if anything differs. Needs the `tokenizers` package (dev only; the serving path does not).
"""
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tokenizers import Tokenizer  # noqa: E402

from gateway.detectors.wordpiece import WordPieceTokenizer  # noqa: E402


def main() -> int:
    path = Path(sys.argv[1])
    ref = Tokenizer.from_file(str(path))
    ref.no_padding()
    ref.no_truncation()
    mine = WordPieceTokenizer.from_tokenizer_json(path)
    bad: dict[int, list[str]] = defaultdict(list)
    chunk = []
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        c = chr(cp)
        chunk.append((cp, (f"a{c}b", c, f"e{c}e")))
        if len(chunk) == 4000 or cp == 0x10FFFF:
            flat = [s for _, trio in chunk for s in trio]
            for (code, trio), i in zip(chunk, range(0, len(flat), 3)):
                for s, enc in zip(trio, ref.encode_batch(flat[i:i + 3])):
                    if enc.ids != mine.encode(s, 4096):
                        bad[code].append(repr(s))
            chunk = []
    # The category tables come from the running Python: a Python whose Unicode is older than the Rust tokenizer's produces differences that are not real, so say which one this was.
    print(f"python {sys.version.split()[0]}, unicodedata {unicodedata.unidata_version}")
    print(f"codepoints checked: {0x110000 - 0x800}; codepoints with a difference: {len(bad)}")
    by_cat = defaultdict(list)
    for cp in bad:
        by_cat[unicodedata.category(chr(cp))].append(cp)
    for cat, cps in sorted(by_cat.items()):
        shown = ", ".join(f"U+{c:04X}" for c in cps[:8])
        print(f"  category {cat}: {len(cps)} codepoints (e.g. {shown}{' ...' if len(cps) > 8 else ''})")
    if "--emit" in sys.argv:
        # Code points this interpreter's Unicode tables call combining marks, punctuation or symbols but the tokenizer's older tables treat as ordinary characters.
        ordinary = sorted(bad)
        ranges, start = [], None
        for cp in ordinary:
            if start is None:
                start = prev = cp
            elif cp == prev + 1:
                prev = cp
            else:
                ranges.append((start, prev))
                start = prev = cp
        if start is not None:
            ranges.append((start, prev))
        table = "_UNKNOWN_TO_TOKENIZER = (" + ", ".join(f"(0x{a:X}, 0x{b:X})" for a, b in ranges) + ")"
        out_path = Path(sys.argv[sys.argv.index("--emit") + 1])
        out_path.write_text(table, encoding="utf-8")
        print(f"\nwrote {len(ordinary)} code points in {len(ranges)} ranges to {out_path}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
