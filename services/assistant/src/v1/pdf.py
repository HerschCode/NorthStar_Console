"""Dependency-free text PDF (Helvetica, A4, wrapped lines) for investigation export.

Deliberately minimal: headings and paragraphs only, Latin-1 text (anything else is replaced). No fonts are embedded
(the 14 standard PDF fonts are guaranteed by every reader).
"""
from __future__ import annotations

import textwrap

PAGE_W, PAGE_H, MARGIN, LEADING = 595, 842, 56, 14


def _esc(s: str) -> str:
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(blocks: list[tuple[str, str]]) -> bytes:
    """blocks: ("h1"|"h2"|"p", text). Returns PDF bytes."""
    lines: list[tuple[str, int, str]] = []          # (font, size, text)
    for kind, text in blocks:
        size, font, width = (16, "F2", 60) if kind == "h1" else (12, "F2", 75) if kind == "h2" else (10, "F1", 95)
        for para in str(text).split("\n"):
            wrapped = textwrap.wrap(para, width=width) or [""]
            for w in wrapped:
                lines.append((font, size, w))
        lines.append(("F1", 10, ""))
    per_page = (PAGE_H - 2 * MARGIN) // LEADING
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    objs: list[bytes] = []

    def add(b: str | bytes) -> int:
        objs.append(b.encode("latin-1") if isinstance(b, str) else b)
        return len(objs)

    add("<< /Type /Catalog /Pages 2 0 R >>")
    kids_placeholder = len(objs) + 1
    add("")                                           # pages object, filled below
    f1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    f2 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    kids = []
    for pg in pages:
        y = PAGE_H - MARGIN
        ops = []
        for font, size, text in pg:
            ops.append(f"BT /{font} {size} Tf {MARGIN} {y} Td ({_esc(text)}) Tj ET")
            y -= LEADING
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page = add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] /Contents {content} 0 R "
                   f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> >> >>")
        kids.append(page)
    objs[kids_placeholder - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode("latin-1")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
