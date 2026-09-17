"""Render a project markdown doc as .docx, for sending to someone outside.

Usage:
    .venv/bin/python scripts/md2docx.py out.docx [docs/ARCHITECTURE.md]

Built 2026-09-17 because Ishay needed to hand the architecture review to
an auditor and an artifact link was awkward to pass on.

Deliberately small: headings, paragraphs, bullets, tables and code blocks
are the only shapes the document actually uses. Inline **bold**, `code`
and | tables | are handled; nothing else is invented.

Hebrew appears inside otherwise-English sentences, so paragraphs are left
LTR and the font is one that carries both scripts. Forcing RTL would
break the English, which is the bulk of it.
"""
import re
import sys

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

SRC = sys.argv[2] if len(sys.argv) > 2 else "docs/ARCHITECTURE.md"
OUT = sys.argv[1] if len(sys.argv) > 1 else "architecture.docx"

doc = Document()
base = doc.styles["Normal"]
base.font.name = "Calibri"
base.font.size = Pt(10.5)

def inline(par, text, bold=False):
    """**bold**, `code` and plain runs, including `code` inside **bold**.

    Nesting matters here: section 6 is a numbered list of bold headlines
    that each name a module in backticks, and treating bold as opaque put
    raw backticks in front of the reader.
    """
    for piece in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**"):
            inline(par, piece[2:-2], bold=True)
        elif piece.startswith("`") and piece.endswith("`"):
            run = par.add_run(piece[1:-1])
            run.font.name = "Consolas"; run.font.size = Pt(9.5)
            run.font.color.rgb = RGBColor(0xB0, 0x30, 0x60)
            run.bold = bold
        else:
            run = par.add_run(piece)
            run.bold = bold

lines = open(SRC, encoding="utf-8").read().split("\n")
i, in_code, table = 0, False, []

def flush_table():
    global table
    if not table:
        return
    rows = [r for r in table if not re.match(r"^\s*\|[\s|:-]+\|\s*$", r)]
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    width = max(len(c) for c in cells)
    t = doc.add_table(rows=0, cols=width)
    t.style = "Light Grid Accent 1"
    for n, row in enumerate(cells):
        cs = t.add_row().cells
        for j in range(width):
            cs[j].text = ""
            p = cs[j].paragraphs[0]
            inline(p, row[j] if j < len(row) else "")
            if n == 0:
                for r in p.runs:
                    r.bold = True
    table = []
    doc.add_paragraph()

while i < len(lines):
    line = lines[i]
    if line.startswith("```"):
        in_code = not in_code
        if in_code:
            block = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i]); i += 1
            in_code = False
            p = doc.add_paragraph()
            run = p.add_run("\n".join(block))
            run.font.name = "Consolas"; run.font.size = Pt(9)
            p.paragraph_format.left_indent = Pt(18)
            p.paragraph_format.space_after = Pt(10)
        i += 1
        continue

    if line.startswith("|"):
        table.append(line); i += 1; continue
    flush_table()

    if line.startswith("#"):
        level = len(line) - len(line.lstrip("#"))
        doc.add_heading(line.lstrip("# ").strip(), level=min(level, 4))
    elif line.strip() == "---":
        doc.add_page_break()
    elif re.match(r"^\s*[-*] ", line) or re.match(r"^\s*\d+\. ", line):
        bullet = bool(re.match(r"^\s*[-*] ", line))
        buf = [re.sub(r"^\s*([-*]|\d+\.) ", "", line).strip()]
        while (i + 1 < len(lines) and lines[i + 1].startswith("  ")
               and lines[i + 1].strip()
               and not re.match(r"^\s*([-*]|\d+\.) ", lines[i + 1])
               and not lines[i + 1].lstrip().startswith(("|", "```"))):
            i += 1
            buf.append(lines[i].strip())
        p = doc.add_paragraph(style="List Bullet" if bullet else "List Number")
        inline(p, " ".join(buf))
    elif line.startswith(">"):
        buf = [line.lstrip("> ").strip()]
        while i + 1 < len(lines) and lines[i + 1].startswith(">"):
            i += 1
            buf.append(lines[i].lstrip("> ").strip())
        p = doc.add_paragraph()
        inline(p, " ".join(buf))
        p.paragraph_format.left_indent = Pt(24)
        for r in p.runs:
            r.italic = True
    elif line.strip():
        # Markdown hard-wraps, so **bold** and `code` routinely open on one
        # line and close on the next. Joining the paragraph before parsing
        # is the difference between bold text and a literal "**" in the
        # reader's face.
        buf = [line.strip()]
        while (i + 1 < len(lines) and lines[i + 1].strip()
               and not lines[i + 1].startswith(("#", "|", "```", ">"))
               and not re.match(r"^\s*([-*]|\d+\.) ", lines[i + 1])
               and lines[i + 1].strip() != "---"):
            i += 1
            buf.append(lines[i].strip())
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        inline(p, " ".join(buf))
    i += 1

flush_table()
doc.save(OUT)
print("wrote", OUT)
