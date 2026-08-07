"""Renders reconstructed page structure (sections/columns + header/footer)
into a `python-docx` document — ported from
`Reformat_prototype/reformat_service_v2.py`'s `DocumentRenderer`. Table/
list-item/markdown-lite/centering-detection logic is unchanged from the
prototype; the multi-column XML plumbing (`_set_section_layout`) is
unchanged too.

**What actually changed vs. the prototype** (see `export_service.py` for
why): the prototype's `render()` called `Document()` itself and returned a
brand-new document *per page*, then something outside these 5 files merged
N documents into one — the anti-pattern the export redesign explicitly
avoids. This version instead takes an existing, shared `doc` and a
`start_new_page` flag:
- `start_new_page=False` (the export's first page): reuse `doc.sections[0]`,
  same as the prototype's "first section of a fresh Document" branch.
- `start_new_page=True` (every page after the first): start with
  `doc.add_section(WD_SECTION.NEW_PAGE)` instead of reusing `sections[0]` —
  this is the one real structural change. Additional same-page column-group
  "sections" (`self.sections` here — an unfortunate name collision with
  python-docx's own `Section`/`WD_SECTION`, inherited from the prototype)
  still use `WD_SECTION.CONTINUOUS` exactly as before.
- Header/footer must attach to *this page's own first section*, not always
  `doc.sections[0]` (which the prototype could assume because `sections[0]`
  was always that page's only fresh section) — `_render_header_footer` now
  takes the target section explicitly, called once per page.

Also fixed one latent bug found while porting: the prototype set
`section.orientation = WD_ORIENT.LANDSCAPE` where `section` was the loop
variable over `self.sections` (a plain dataclass with no `orientation`
attribute) instead of the real docx `current_section` object — silently a
no-op, landscape was never actually applied to the output. Fixed to target
`current_section`.
"""
from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup
from docx.document import Document as DocumentObject
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, Twips
from lxml import etree

from app.schemas import DocumentLayout
from app.services.layout_reconstructor import Section

import logging

logger = logging.getLogger(__name__)

default_font_sizes = {
    "Title": 22,
    "Section-header": 15,
    "Text": 11,
    "List-item": 11,
    "Formula": 11,
    "Table": 9,
    "Page-header": 9,
    "Page-footer": 9,
    "Caption": 9,
    "Footnote": 9,
}

FONT = "Times New Roman"

# Recursively-applied markdown-lite tokenizer (bold/italic/underline set by
# `postprocessing.py::clean_html` before this text ever reaches us). Each
# alternative has its own capture group so the match tells us which style it
# is; group order below (***, **, *, __) is also match-priority order, since
# `*` must not be allowed to eat into a `**`/`***` delimiter it's part of.
# The lone-`*` branch additionally requires no whitespace touching the
# delimiters (`(?!\s)...(?<!\s)`) — unlike `**`/`__`, a single `*` collides
# with ordinary multiplication in exam text ("2 * 3 * 4"), which would
# otherwise be misread as italicizing " 3 ". DOTALL lets a span cross a `\n`
# (e.g. a `<br>` inside a `<b>...</b>` block), since `paragraph.add_run()`
# turns embedded `\n` into a real line break anyway.
_MD_TOKEN = re.compile(
    r"\*\*\*((?:(?!\*\*\*).)+?)\*\*\*"
    r"|\*\*((?:(?!\*\*).)+?)\*\*"
    r"|\*(?!\s)((?:(?!\*).)+?)(?<!\s)\*"
    r"|__((?:(?!__).)+?)__",
    re.DOTALL,
)


def _safe_span(value: Optional[str], default: int = 1, maximum: int = 50) -> int:
    """Parses a `colspan`/`rowspan` attribute defensively — this is
    LLM-produced HTML, not a trusted browser DOM, so a missing/non-numeric/
    absurd value falls back to 1 instead of raising or building a
    pathologically large table."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if 1 <= n <= maximum else default


class DocumentRenderer:
    def __init__(
        self,
        doc: DocumentObject,
        sections: list[Section],
        header: list[DocumentLayout],
        footer: list[DocumentLayout],
        page_width: int = 1654,
        page_height: int = 2338,
        orientation: str = "portrait",
        start_new_page: bool = False,
        left_margin: int = 150,
        right_margin: int = 100,
        top_margin: int = 100,
        bottom_margin: int = 100,
        dpi: int = 200,
    ):
        self.doc = doc
        self.sections = sections
        self.header = header
        self.footer = footer
        self.dpi = dpi
        self.page_height = page_height
        self.page_width = page_width
        self.orientation = orientation
        self.start_new_page = start_new_page
        self.left_margin = left_margin
        self.right_margin = right_margin
        self.top_margin = top_margin
        self.bottom_margin = bottom_margin
        self.content_width = self.page_width - self.left_margin - self.right_margin

    def render(self) -> None:
        doc = self.doc
        is_first_logical_section = True

        for section in self.sections:
            if is_first_logical_section:
                if self.start_new_page:
                    current_section = doc.add_section(WD_SECTION.NEW_PAGE)
                    current_section.header.is_linked_to_previous = False
                    current_section.footer.is_linked_to_previous = False
                else:
                    current_section = doc.sections[0]
                page_first_section = current_section
            else:
                current_section = doc.add_section(WD_SECTION.CONTINUOUS)
                current_section.header.is_linked_to_previous = True
                current_section.footer.is_linked_to_previous = True
            is_first_logical_section = False

            current_section.page_width = Twips(self._px_to_twips(self.page_width))
            current_section.page_height = Twips(self._px_to_twips(self.page_height))
            current_section.left_margin = Twips(self._px_to_twips(self.left_margin))
            current_section.right_margin = Twips(self._px_to_twips(self.right_margin))
            current_section.top_margin = Twips(self._px_to_twips(self.top_margin))
            current_section.bottom_margin = Twips(self._px_to_twips(self.bottom_margin))

            if self.orientation == "landscape":
                current_section.orientation = WD_ORIENT.LANDSCAPE

            widths = [c.bbox[2] - c.bbox[0] for c in section.columns]
            self._set_section_layout(current_section._sectPr, len(section.columns), widths)

            is_single_col = len(section.columns) == 1

            if page_first_section is current_section:
                self._render_header_footer(page_first_section)

            for col in section.columns:
                if not col.blocks:
                    continue
                for block in col.blocks:
                    if block.category == "Table":
                        self._render_table(block.text, doc)
                        continue
                    if block.category == "List-item":
                        self._render_list_item(block.text, doc)
                        continue
                    if block.category == "Picture":
                        doc.add_paragraph("\n")
                        continue
                    if block.category == "Title":
                        p = doc.add_paragraph(style="Title")
                        p.paragraph_format.space_before = Pt(6)
                        p.paragraph_format.space_after = Pt(6)
                        self._add_styled_run(p, True, block.category, block.text)
                        continue
                    if block.category == "Section-header":
                        p = doc.add_paragraph(style=f"Heading {block.level if block.level is not None else 3}")
                        p.paragraph_format.space_before = Pt(3)
                        p.paragraph_format.space_after = Pt(3)
                        self._add_styled_run(
                            p, self._is_centered_block(block.bbox, col.bbox, is_single_col), block.category, block.text
                        )
                        continue
                    if len(doc.paragraphs) == 1 and not doc.paragraphs[0].text:
                        p = doc.paragraphs[0]
                    else:
                        p = doc.add_paragraph()
                        p.paragraph_format.space_before = Pt(3)
                    self._add_styled_run(
                        p, self._is_centered_block(block.bbox, col.bbox, is_single_col), block.category, block.text
                    )

    def _px_to_twips(self, px: int):
        return int(px * 1440 / self.dpi)

    def _render_header_footer(self, section) -> None:
        content_width_twips = self._px_to_twips(self.content_width)

        def process_container(container, blocks):
            if not blocks:
                return
            table = container.add_table(rows=1, cols=len(blocks), width=Twips(content_width_twips))
            table.autofit = False
            tbl = table._tbl
            tblPr = tbl.xpath("w:tblPr")[0]
            tblBorders = OxmlElement("w:tblBorders")
            for tag in ["top", "left", "bottom", "right", "insideH", "insideV"]:
                border_node = OxmlElement(f"w:{tag}")
                border_node.set(qn("w:val"), "none")
                tblBorders.append(border_node)
            tblPr.append(tblBorders)
            total_bbox_width = sum(b.bbox[2] - b.bbox[0] for b in blocks)
            for i, block in enumerate(blocks):
                cell = table.cell(0, i)
                cell_width_twips = int(((block.bbox[2] - block.bbox[0]) / total_bbox_width) * content_width_twips)
                cell.width = Twips(cell_width_twips)
                cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
                p = cell.paragraphs[0]
                self._add_styled_run(p, False, block.category, block.text)

        process_container(section.header, self.header)
        process_container(section.footer, self.footer)

    def _set_section_layout(self, sectPr, num_columns, col_widths=None):
        cols_xml = sectPr.find(qn("w:cols"))
        if cols_xml is None:
            cols_xml = OxmlElement("w:cols")
            sectPr.append(cols_xml)
        cols_xml.set(qn("w:num"), str(num_columns))
        for c in cols_xml.findall(qn("w:col")):
            cols_xml.remove(c)
        scale = 7.2
        if col_widths:
            cols_xml.set(qn("w:equalWidth"), "0")
            for i, width in enumerate(col_widths):
                col_el = OxmlElement("w:col")
                col_el.set(qn("w:w"), str(int(width * scale)))
                if i < len(col_widths) - 1:
                    col_el.set(qn("w:space"), "240")
                cols_xml.append(col_el)
        else:
            cols_xml.set(qn("w:equalWidth"), "1")
            cols_xml.set(qn("w:space"), "240")

    def _is_centered_block(self, block_bbox, col_bbox, is_single_col: bool = False):
        ref_bbox = (
            [self.left_margin, col_bbox[1], self.page_width - self.right_margin, col_bbox[3]]
            if is_single_col
            else col_bbox
        )
        left_indent = max(0, block_bbox[0] - ref_bbox[0])
        right_indent = max(0, ref_bbox[2] - block_bbox[2])
        block_width = block_bbox[2] - block_bbox[0]
        ref_width = ref_bbox[2] - ref_bbox[0]
        distance_threshold = max(15, ref_width * 0.1)

        result = abs(left_indent - right_indent) <= distance_threshold and block_width / ref_width <= 0.9
        logger.debug(
            "centered? single_col=%s block=%s ratio=%.2f threshold=%.1f result=%s",
            is_single_col, block_bbox, block_width / ref_width, distance_threshold, result,
        )
        return result

    def _add_styled_run(self, paragraph, is_centered: bool, category: str, text: Optional[str] = None):
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if is_centered else WD_ALIGN_PARAGRAPH.LEFT

        font_size = default_font_sizes.get(category, 11)
        force_bold = category == "Title"
        for content, is_bold, is_italic, is_underline in self._iter_markdown_runs(text or "", force_bold, False, False):
            if not content:
                continue
            run = paragraph.add_run(content)
            run.bold = is_bold
            run.italic = is_italic
            run.underline = is_underline
            run.font.name = FONT
            run.font.size = Pt(font_size)

    def _iter_markdown_runs(self, text: str, bold: bool, italic: bool, underline: bool):
        """Splits `text` into (content, bold, italic, underline) runs,
        recursing into each matched span's own content so combined/nested
        markers (`**bold with *nested italic* inside**`, `**__bold underline__**`)
        apply every style instead of only the outermost one — a flat
        single-pass split left inner `*`/`__` markers as literal characters
        in the rendered output."""
        pos = 0
        for m in _MD_TOKEN.finditer(text):
            if m.start() > pos:
                yield (text[pos:m.start()], bold, italic, underline)
            if m.group(1) is not None:
                inner, b, i, u = m.group(1), True, True, underline
            elif m.group(2) is not None:
                inner, b, i, u = m.group(2), True, italic, underline
            elif m.group(3) is not None:
                inner, b, i, u = m.group(3), bold, True, underline
            else:
                inner, b, i, u = m.group(4), bold, italic, True
            yield from self._iter_markdown_runs(inner, b, i, u)
            pos = m.end()
        if pos < len(text):
            yield (text[pos:], bold, italic, underline)

    def _render_table(self, table_html, doc: DocumentObject):
        try:
            root = etree.HTML(table_html)
            table_el = root.find(".//table")
            if table_el is None:
                return
            rows_html = table_el.findall(".//tr")
            if not rows_html:
                return

            # Pass 1 — walk the HTML grid to find each cell's (row, col)
            # origin. A rowspan from an earlier row occupies column slots in
            # later rows even though those rows have no <td> of their own
            # there, so placement must skip already-occupied slots rather
            # than just counting each row's own cell count (the previous
            # colspan-only version did the latter, which silently produced
            # the wrong column for every cell after a rowspan).
            occupied: set[tuple[int, int]] = set()
            placements: list[tuple[int, int, int, int, object]] = []
            max_cols = 0
            for row_idx, r_html in enumerate(rows_html):
                col_idx = 0
                for c_html in r_html:
                    if c_html.tag not in ("td", "th"):
                        continue
                    while (row_idx, col_idx) in occupied:
                        col_idx += 1
                    colspan = _safe_span(c_html.get("colspan"))
                    rowspan = _safe_span(c_html.get("rowspan"))
                    placements.append((row_idx, col_idx, colspan, rowspan, c_html))
                    for dr in range(rowspan):
                        for dc in range(colspan):
                            occupied.add((row_idx + dr, col_idx + dc))
                    col_idx += colspan
                max_cols = max(max_cols, col_idx)

            if max_cols == 0 or not placements:
                return

            total_rows = len(rows_html)
            table = doc.add_table(rows=total_rows, cols=max_cols)
            table.autofit = False
            tbl = table._element
            tblPr = tbl.tblPr
            tblW = OxmlElement("w:tblW")
            tblW.set(qn("w:type"), "pct")
            tblW.set(qn("w:w"), "5000")
            tblPr.append(tblW)
            tblLayout = OxmlElement("w:tblLayout")
            tblLayout.set(qn("w:type"), "fixed")
            tblPr.append(tblLayout)

            # Pass 2 — render. Cells are merged over the rectangle
            # (row_idx, col_idx) .. (end_row, end_col) in one call, which
            # python-docx supports for an arbitrary rectangular span, so
            # colspan and rowspan merge together correctly instead of only
            # ever merging sideways.
            for row_idx, col_idx, colspan, rowspan, c_html in placements:
                end_row = min(row_idx + rowspan - 1, total_rows - 1)
                end_col = min(col_idx + colspan - 1, max_cols - 1)
                main_cell = table.cell(row_idx, col_idx)
                if end_row != row_idx or end_col != col_idx:
                    main_cell = main_cell.merge(table.cell(end_row, end_col))

                parts = []
                for node in c_html.iter():
                    if node.tag == "br":
                        parts.append("\n")
                    if node.text:
                        parts.append(node.text)
                    if node.tail:
                        parts.append(node.tail)
                text = "".join(parts).strip()

                p = main_cell.paragraphs[0]
                p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
                # A single call: `paragraph.add_run()` already converts
                # embedded "\n" into a real line break, and doing it this way
                # (rather than splitting on "\n" and styling each line
                # separately, as before) lets a bold/italic/underline span
                # cross a line break intact instead of being cut in two.
                self._add_styled_run(p, False, "Table", text)

        except Exception:
            logger.exception("table render error")
            doc.add_paragraph(table_html)

    def _render_list_item(self, list_html, doc: DocumentObject):
        soup = BeautifulSoup(list_html, "html.parser")
        stack = []

        for root in soup.find_all(["ul", "ol"], recursive=False):
            stack.append((root, 0, root.name == "ol"))

        for node in soup.find_all(["p", "span"], recursive=False):
            if node.find_parent(["li"]):
                continue
            stack.append((node, 0, None))

        if not stack:
            raw = soup.get_text("\n", strip=True)
            if raw:
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(3)
                self._add_styled_run(p, False, "List-item", raw)
            return

        while stack:
            element, level, _ordered = stack.pop(0)

            if element.name in ["p", "span"]:
                inner_html = element.decode_contents()
                raw_items = re.split(r"<br\s*/?>", inner_html, flags=re.IGNORECASE)
                items = [("p", x) for x in raw_items]
            else:
                items = [("li", x) for x in element.find_all("li", recursive=False)]

            for tag, item in items:
                if tag == "p":
                    raw = BeautifulSoup(item, "html.parser").get_text(" ", strip=True)
                else:
                    texts = []
                    for child in item.contents:
                        if getattr(child, "name", None) in ["ul", "ol"]:
                            continue
                        if hasattr(child, "get_text"):
                            texts.append(child.get_text(" ", strip=True))
                        else:
                            texts.append(str(child).strip())
                    raw = " ".join(t for t in texts if t).strip()
                if not raw:
                    continue
                prefix = ""
                content = raw
                m_bullet = re.match(r"^\s*[-*•▪◦‣⁃]\s+(.*)", raw)

                if m_bullet:
                    prefix = "-"
                    content = m_bullet.group(1)
                else:
                    m_num = re.match(
                        r"^\s*((?:\d{1,2}(?:\.\d{1,2}){0,2}|[a-zA-Z]|[ivxlcdmIVXLCDM]{1,5})(?:[\.\)])?)\s+(.*)", raw
                    )
                    if m_num:
                        prefix = m_num.group(1)
                        content = m_num.group(2)
                        num = prefix.replace(".", "").rstrip("):")
                        if num.isdigit() and int(num) > 100:
                            prefix = ""
                            content = raw

                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(3)
                text = f"{prefix}\t{content}" if prefix else content
                self._add_styled_run(p, False, "List-item", text)

                base_indent = 18
                dynamic = min(len(prefix), 4) * 2 if prefix else 0
                indent = base_indent + dynamic
                total_indent = indent * (level + 1)
                p.paragraph_format.left_indent = Pt(total_indent)
                p.paragraph_format.first_line_indent = Pt(-indent)

                if tag == "li":
                    for child in item.find_all(["ul", "ol"], recursive=False):
                        stack.append((child, level + 1, child.name == "ol"))
