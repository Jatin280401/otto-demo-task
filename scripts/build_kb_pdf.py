from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph as RLParagraph,
    Spacer,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = Path.home() / "Downloads" / "EarnOut Backend - Project Handover Manual (updated 20 Nov 2026).docx"
DEFAULT_OUT = ROOT / "knowledge" / "earnout-handover.pdf"

TITLE = "EarnOut Backend — Project Handover Manual"
SUBTITLE = "Otto live-meeting knowledge source (PDF edition)"

def escape(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"[ \t]+", " ", text)
    return text

def iter_block_items(parent):
    body = parent.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)

def table_lines(table: Table):
    rows = []
    for row in table.rows:
        cells = []
        seen = set()
        for cell in row.cells:
            if id(cell._tc) in seen:
                continue
            seen.add(id(cell._tc))
            cells.append(" ".join(p.text.strip() for p in cell.paragraphs if p.text.strip()))
        if any(cells):
            rows.append(" | ".join(cells))
    return rows

def build_styles():
    base = getSampleStyleSheet()
    styles = {}
    styles["body"] = ParagraphStyle(
        "body",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=8.6,
        leading=11.4,
        spaceAfter=3.2,
        textColor=colors.HexColor("#1d2433"),
        alignment=TA_LEFT,
    )
    styles["h1"] = ParagraphStyle(
        "h1",
        parent=base["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=17,
        leading=21,
        spaceBefore=0,
        spaceAfter=10,
        textColor=colors.HexColor("#0b1f3a"),
    )
    styles["h2"] = ParagraphStyle(
        "h2",
        parent=base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        spaceBefore=11,
        spaceAfter=5,
        textColor=colors.HexColor("#123b63"),
    )
    styles["h3"] = ParagraphStyle(
        "h3",
        parent=base["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=9.8,
        leading=13,
        spaceBefore=9,
        spaceAfter=3,
        textColor=colors.HexColor("#1f5c8b"),
    )
    styles["h4"] = ParagraphStyle(
        "h4",
        parent=base["Heading4"],
        fontName="Helvetica-Bold",
        fontSize=8.8,
        leading=12,
        spaceBefore=6,
        spaceAfter=2,
        textColor=colors.HexColor("#31506f"),
    )
    styles["table"] = ParagraphStyle(
        "table",
        parent=styles["body"],
        fontSize=7.6,
        leading=9.8,
        leftIndent=6,
        textColor=colors.HexColor("#27364a"),
        backColor=colors.HexColor("#f2f5f9"),
        borderPadding=2,
        spaceAfter=1.2,
    )
    styles["code"] = ParagraphStyle(
        "code",
        parent=styles["body"],
        fontName="Courier",
        fontSize=7.2,
        leading=9.4,
        leftIndent=8,
        textColor=colors.HexColor("#29364a"),
    )
    return styles

class Doc(BaseDocTemplate):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, pagesize=A4, **kwargs)
        self.section = "EarnOut Handover"
        frame = Frame(18 * mm, 17 * mm, A4[0] - 36 * mm, A4[1] - 36 * mm, id="normal")
        self.addPageTemplates(
            PageTemplate(id="main", frames=[frame], onPage=self._footer)
        )

    def _footer(self, canv: canvas.Canvas, doc):
        canv.saveState()
        canv.setStrokeColor(colors.HexColor("#dfe6ef"))
        canv.setLineWidth(0.5)
        canv.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
        canv.setFont("Helvetica", 7)
        canv.setFillColor(colors.HexColor("#708096"))
        canv.drawString(18 * mm, 8.5 * mm, "EarnOut Backend — Project Handover Manual (PDF)")
        canv.drawRightString(A4[0] - 18 * mm, 8.5 * mm, f"page {doc.page}")
        canv.restoreState()

def build(source: Path, out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    docx = Document(str(source))
    styles = build_styles()
    story = []

    story.append(Spacer(1, 55 * mm))
    story.append(RLParagraph(escape(TITLE), ParagraphStyle(
        "cover", fontName="Helvetica-Bold", fontSize=27, leading=33,
        textColor=colors.HexColor("#0b1f3a"), spaceAfter=8)))
    story.append(RLParagraph(escape(SUBTITLE), ParagraphStyle(
        "coversub", fontName="Helvetica", fontSize=13, leading=18,
        textColor=colors.HexColor("#31506f"), spaceAfter=18)))
    story.append(RLParagraph(
        "Prepared for the Otto live-meeting demo. Source: the Task 1 backend "
        "handover documentation. Otto ingests this PDF, chunks it by section, "
        "and cites page numbers in meeting chat.",
        ParagraphStyle("coverbody", fontName="Helvetica", fontSize=9.2, leading=13.5,
                       textColor=colors.HexColor("#4a5b71"))))
    story.append(Spacer(1, 70 * mm))
    story.append(RLParagraph("Knowledge-base edition · searchable text · page numbers preserved",
                             ParagraphStyle("coverfoot", fontName="Helvetica-Oblique", fontSize=8,
                                            textColor=colors.HexColor("#708096"))))
    story.append(PageBreak())

    chapter = 0
    paragraph_count = 0
    table_row_count = 0
    for block in iter_block_items(docx):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            style_name = (block.style.name or "").lower()
            if style_name.startswith("heading 1") or style_name.startswith("title"):
                if chapter:
                    story.append(PageBreak())
                chapter += 1
                story.append(RLParagraph(escape(text), styles["h1"]))
            elif style_name.startswith("heading 2"):
                story.append(RLParagraph(escape(text), styles["h2"]))
            elif style_name.startswith("heading 3"):
                story.append(RLParagraph(escape(text), styles["h3"]))
            elif style_name.startswith("heading 4"):
                story.append(RLParagraph(escape(text), styles["h4"]))
            else:
                looks_like_code = (
                    text.startswith("{")
                    or text.startswith("[")
                    or text.startswith("def ")
                    or text.startswith("class ")
                    or ('"""' in text)
                )
                story.append(RLParagraph(escape(text), styles["code"] if looks_like_code else styles["body"]))
            paragraph_count += 1
        else:
            for line in table_lines(block):
                story.append(RLParagraph(escape(line), styles["table"]))
                table_row_count += 1
            story.append(Spacer(1, 2.5))

    doc = Doc(str(out), title=TITLE, author="EarnOut / Otto")
    doc.build(story)
    size_kb = out.stat().st_size // 1024
    print(f"wrote {out} ({size_kb} KB, {paragraph_count} paragraphs, {table_row_count} table rows)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=os.environ.get("OTTO_SOURCE_DOCX", str(DEFAULT_SOURCE)))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    src = Path(os.path.expanduser(args.source))
    if not src.exists():
        sys.exit(f"source document not found: {src}\nSet OTTO_SOURCE_DOCX or pass --source")
    build(src, Path(args.out))

if __name__ == "__main__":
    main()
