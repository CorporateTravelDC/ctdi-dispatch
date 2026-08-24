"""
second_brain.doc_generation -- turn vault content (markdown) into real
documents: .docx, .pptx, and PDF (via LibreOffice headless conversion).

General-purpose second-brain tooling, not investor-doc-specific: any vault
note or synthesis (a 04-Syntheses/ digest, a research write-up, a weekly
compile) can go through this to become a real handoff-able document instead
of staying markdown-only. The investor-materials use case (2026-08-24) is
the first caller, not the reason this exists.

Deliberately minimal for a first pass -- a real markdown parser (headings,
bullet lists, simple tables, bold/italic inline runs) mapped onto
python-docx/python-pptx primitives, not a full CommonMark implementation.
Nested lists, code blocks, images, and footnotes are NOT handled; call
render_docx()/render_pptx() directly with a structured spec instead of
going through the markdown parser if you need something the parser can't
express. A more polished version (real slide templates/branding, chart
embedding) is planned as a follow-up once this minimal version has proven
out the content-mapping approach -- see the operator conversation this
was built from, 2026-08-24.

Three entry points:
  markdown_to_docx(markdown_text, output_path, title=None) -> path
  markdown_to_pptx(markdown_text, output_path, title=None) -> path
      (H1/H2 headings start a new slide; bullet lines under a heading
      become that slide's bullets -- the common "outline as deck"
      convention, not a full markdown-to-slides heuristic.)
  to_pdf(path) -> pdf_path
      (LibreOffice headless conversion -- works on the .docx/.pptx this
      module produces, or any other docx/pptx/odt/etc. LibreOffice opens.)

CLI:
  python3 -m second_brain.doc_generation docx input.md output.docx
  python3 -m second_brain.doc_generation pptx input.md output.pptx
  python3 -m second_brain.doc_generation pdf output.docx
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.shared import Pt
from pptx import Presentation
from pptx.util import Pt as PptxPt

# ---------------------------------------------------------------------------
# Minimal markdown parsing -- headings, bullets, tables, plain paragraphs.
# Inline bold (**x**) and italic (*x*) are handled per-run within a
# paragraph; nothing else inline (links render as literal text, which is
# an acceptable minimal-pass tradeoff -- most vault content doesn't lean
# on markdown links for meaning).
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _parse_inline_runs(text: str) -> list[tuple[str, bool, bool]]:
    """Split a line into (text, bold, italic) runs on **bold** / *italic*
    markers. Not a real tokenizer -- doesn't handle nested or overlapping
    emphasis, which is fine for the vault content this targets."""
    runs: list[tuple[str, bool, bool]] = []
    pos = 0
    pattern = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*")
    for m in pattern.finditer(text):
        if m.start() > pos:
            runs.append((text[pos:m.start()], False, False))
        if m.group(1) is not None:
            runs.append((m.group(1), True, False))
        else:
            runs.append((m.group(2), False, True))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], False, False))
    return runs or [(text, False, False)]


def _parse_blocks(markdown_text: str) -> list[dict]:
    """Parse markdown into a flat list of block dicts:
    {"type": "heading", "level": int, "text": str}
    {"type": "bullet", "text": str}
    {"type": "table", "rows": list[list[str]]}
    {"type": "paragraph", "text": str}
    Blank lines are separators only, not emitted as blocks."""
    blocks: list[dict] = []
    lines = markdown_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _HEADING_RE.match(line)
        if m:
            blocks.append({"type": "heading", "level": len(m.group(1)), "text": m.group(2).strip()})
            i += 1
            continue
        m = _BULLET_RE.match(line)
        if m:
            blocks.append({"type": "bullet", "text": m.group(1).strip()})
            i += 1
            continue
        if _TABLE_ROW_RE.match(line):
            rows = []
            while i < len(lines) and _TABLE_ROW_RE.match(lines[i]):
                if not _TABLE_SEP_RE.match(lines[i]):
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    rows.append(cells)
                i += 1
            if rows:
                blocks.append({"type": "table", "rows": rows})
            continue
        # Plain paragraph -- collect contiguous non-blank, non-special lines.
        para_lines = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not _HEADING_RE.match(lines[i]) \
                and not _BULLET_RE.match(lines[i]) and not _TABLE_ROW_RE.match(lines[i]):
            para_lines.append(lines[i].strip())
            i += 1
        blocks.append({"type": "paragraph", "text": " ".join(para_lines)})
    return blocks


# ---------------------------------------------------------------------------
# docx rendering
# ---------------------------------------------------------------------------

def render_docx(blocks: list[dict], output_path: str, title: str | None = None) -> str:
    """Render parsed blocks (see _parse_blocks) into a .docx file."""
    doc = Document()
    if title:
        doc.add_heading(title, level=0)

    for block in blocks:
        if block["type"] == "heading":
            doc.add_heading(block["text"], level=min(block["level"], 4))
        elif block["type"] == "bullet":
            p = doc.add_paragraph(style="List Bullet")
            for text, bold, italic in _parse_inline_runs(block["text"]):
                run = p.add_run(text)
                run.bold = bold
                run.italic = italic
        elif block["type"] == "table":
            rows = block["rows"]
            if not rows:
                continue
            table = doc.add_table(rows=len(rows), cols=len(rows[0]))
            table.style = "Light Grid Accent 1"
            for r, row in enumerate(rows):
                for c, cell_text in enumerate(row):
                    if c < len(table.rows[r].cells):
                        table.rows[r].cells[c].text = cell_text
        elif block["type"] == "paragraph":
            p = doc.add_paragraph()
            for text, bold, italic in _parse_inline_runs(block["text"]):
                run = p.add_run(text)
                run.bold = bold
                run.italic = italic
                run.font.size = Pt(11)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    return output_path


def markdown_to_docx(markdown_text: str, output_path: str, title: str | None = None) -> str:
    return render_docx(_parse_blocks(markdown_text), output_path, title=title)


# ---------------------------------------------------------------------------
# pptx rendering -- outline convention: each H1/H2 starts a new slide;
# bullets and paragraphs under it become that slide's body content.
# ---------------------------------------------------------------------------

def render_pptx(slides: list[dict], output_path: str, title: str | None = None) -> str:
    """slides: list of {"title": str, "bullets": list[str]}.
    Uses the default template's Title Slide + Title-and-Content layouts --
    no custom branding/theme in this minimal pass (see module docstring)."""
    prs = Presentation()

    if title:
        title_layout = prs.slide_layouts[0]
        slide = prs.slides.add_slide(title_layout)
        slide.shapes.title.text = title

    content_layout = prs.slide_layouts[1]
    for spec in slides:
        slide = prs.slides.add_slide(content_layout)
        slide.shapes.title.text = spec.get("title", "")
        body = slide.placeholders[1].text_frame
        bullets = spec.get("bullets", [])
        if not bullets:
            continue
        body.text = bullets[0]
        body.paragraphs[0].font.size = PptxPt(18)
        for b in bullets[1:]:
            p = body.add_paragraph()
            p.text = b
            p.font.size = PptxPt(18)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    prs.save(output_path)
    return output_path


def _blocks_to_slides(blocks: list[dict]) -> list[dict]:
    """Outline convention: an H1/H2 starts a new slide; bullets and
    paragraphs accumulate as that slide's bullet points until the next
    H1/H2. H3+ headings are demoted to a bold-leading bullet rather than
    a new slide (a deck this shallow shouldn't nest that deep)."""
    slides: list[dict] = []
    current: dict | None = None
    for block in blocks:
        if block["type"] == "heading" and block["level"] <= 2:
            current = {"title": block["text"], "bullets": []}
            slides.append(current)
            continue
        if current is None:
            current = {"title": "", "bullets": []}
            slides.append(current)
        if block["type"] == "bullet":
            current["bullets"].append(block["text"])
        elif block["type"] == "heading":
            current["bullets"].append(block["text"])
        elif block["type"] == "paragraph":
            current["bullets"].append(block["text"])
        elif block["type"] == "table":
            for row in block["rows"]:
                current["bullets"].append(" | ".join(row))
    return slides


def markdown_to_pptx(markdown_text: str, output_path: str, title: str | None = None) -> str:
    blocks = _parse_blocks(markdown_text)
    slides = _blocks_to_slides(blocks)
    return render_pptx(slides, output_path, title=title)


# ---------------------------------------------------------------------------
# PDF conversion -- LibreOffice headless. Works on the .docx/.pptx this
# module produces, or any other file format LibreOffice can open.
# ---------------------------------------------------------------------------

def to_pdf(path: str, timeout: int = 120) -> str:
    """Convert a docx/pptx/odt/etc. to PDF via `soffice --headless`.
    Returns the output PDF path (same directory, same basename, .pdf)."""
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(path)
    outdir = src.parent
    result = subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(src)],
        capture_output=True, text=True, timeout=timeout,
    )
    pdf_path = outdir / (src.stem + ".pdf")
    if result.returncode != 0 or not pdf_path.exists():
        raise RuntimeError(
            f"soffice conversion failed (rc={result.returncode}): "
            f"{result.stdout}\n{result.stderr}"
        )
    return str(pdf_path)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    mode = sys.argv[1]
    if mode == "pdf":
        print(to_pdf(sys.argv[2]))
        return 0
    if mode not in ("docx", "pptx"):
        print(f"unknown mode: {mode}", file=sys.stderr)
        return 2
    if len(sys.argv) < 4:
        print(f"usage: python3 -m second_brain.doc_generation {mode} input.md output.{mode}", file=sys.stderr)
        return 2
    input_path, output_path = sys.argv[2], sys.argv[3]
    text = Path(input_path).read_text()
    title = Path(input_path).stem
    if mode == "docx":
        print(markdown_to_docx(text, output_path, title=title))
    else:
        print(markdown_to_pptx(text, output_path, title=title))
    return 0


if __name__ == "__main__":
    sys.exit(main())
