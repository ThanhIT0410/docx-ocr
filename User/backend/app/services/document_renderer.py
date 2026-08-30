"""Renders page content into a `python-docx` document. Two renderers share
one ancestor:

- `BlockRenderer` — the primitives neither cares about column/section
  layout: markdown-lite styled runs, and turning `Table`/`List-item` HTML
  into a real docx table/list. Both subclasses below inherit these as
  methods (not free functions) so the class hierarchy stays the single
  source of truth for "how do we render one block's content" — page-level
  arrangement is the only thing that differs between them.
- `DocumentRenderer(BlockRenderer)` — "giữ layout" mode: renders reconstructed
  page structure (sections/columns + header/footer), ported from
  `Reformat_prototype/reformat_service_v2.py`'s `DocumentRenderer`. Table/
  list-item/markdown-lite/centering-detection logic is unchanged from the
  prototype; the multi-column XML plumbing (`_set_section_layout`) is
  unchanged too.
- `PlainTextRenderer(BlockRenderer)` — "text thuần" mode: appends every
  block straight into the shared `doc`, in original order, with no column/
  section reconstruction and no per-category heading styling. See
  `export_service.py::PlainTextExportService`.

**What actually changed in `DocumentRenderer` vs. the prototype** (see
`export_service.py` for why): the prototype's `render()` called `Document()`
itself and returned a brand-new document *per page*, then something outside
these files merged N documents into one — the anti-pattern the export
redesign explicitly avoids. This version instead takes an existing, shared
`doc` and a `start_new_page` flag:
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
from app.services.layout_reconstructor import Column, Section
from app.services.preview_service import RENDER_DPI

import logging

logger = logging.getLogger(__name__)

# DocumentRenderer's page/margin defaults, in physical inches rather than
# hardcoded pixels — so they scale automatically if RENDER_DPI changes
# instead of silently assuming whatever DPI they were last hand-computed
# at (see ../../../DPI_DEPENDENCIES.md). page_width/page_height default to
# A4; left/top/bottom/right are the prototype's original 0.75in/0.5in.
# `export_service.py::LayoutExportService.reconstruct` always passes its
# own page_width/page_height/left_margin/right_margin explicitly — these
# defaults only matter for top_margin/bottom_margin (which it does NOT
# override, see that method's comment) and any other/future caller.
_DEFAULT_PAGE_WIDTH_PX = round(8.2677 * RENDER_DPI)
_DEFAULT_PAGE_HEIGHT_PX = round(11.6929 * RENDER_DPI)
_DEFAULT_LEFT_MARGIN_PX = round(0.75 * RENDER_DPI)
_DEFAULT_MARGIN_PX = round(0.5 * RENDER_DPI)

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

# List-item marker detection (see `BlockRenderer._split_list_marker`) — a
# marker the OCR text already carries, either a bullet char or a
# number/letter/roman-numeral counter followed by "."/")" . The numeric
# branch also accepts a directly-appended 1-2 letter suffix with no
# separator ("3a", "3.1a", "12b") — a sub-part label, not just "3.1"-style
# dotted numbering.
_LIST_BULLET_RE = re.compile(r"^\s*[-*•▪◦‣⁃]\s+(.*)", re.DOTALL)
_LIST_NUM_RE = re.compile(
    r"^\s*((?:\d{1,2}(?:\.\d{1,2}){0,2}[a-zA-Z]{0,2}|[a-zA-Z]|[ivxlcdmIVXLCDM]{1,5})(?:[.)])?)\s+(.*)", re.DOTALL
)
_LIST_BULLET_CHAR = "-"  # normalizes every bullet style (•, ▪, *, ...) to one look, same as before


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


class BlockRenderer:
    """Common ancestor for `DocumentRenderer` and `PlainTextRenderer`: the
    per-block rendering primitives that don't know or care about page-level
    column/section arrangement. Both `Table` and `List-item` are rendered
    from their raw HTML the same way regardless of mode — only how blocks
    get *arranged* on the page differs between the two subclasses."""

    def _add_styled_run(self, paragraph, alignment, category: str, text: Optional[str] = None) -> None:
        paragraph.alignment = alignment

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
            # `add_table()` uses python-docx's default "Normal Table" style,
            # which draws no visible grid lines at all — unlike the
            # header/footer table above (which explicitly wants borders
            # *off*), a real content table needs them explicitly turned on
            # or it renders as invisible cell boundaries.
            tblBorders = OxmlElement("w:tblBorders")
            for tag in ["top", "left", "bottom", "right", "insideH", "insideV"]:
                border_node = OxmlElement(f"w:{tag}")
                border_node.set(qn("w:val"), "single")
                border_node.set(qn("w:sz"), "4")
                border_node.set(qn("w:space"), "0")
                border_node.set(qn("w:color"), "000000")
                tblBorders.append(border_node)
            tblPr.append(tblBorders)

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
                self._add_styled_run(p, WD_ALIGN_PARAGRAPH.LEFT, "Table", text)

        except Exception:
            logger.exception("table render error")
            doc.add_paragraph(table_html)

    def _render_list_item(self, list_html, doc: DocumentObject):
        """Entry point: finds every top-level `<ul>/<ol>` and every
        top-level `<p>/<span>` not already inside an `<li>` (the latter is
        an OCR formatting quirk — see `_render_loose_list_lines`), and
        renders each in source order. Falls back to the block's plain text
        if none of that structure is present at all."""
        soup = BeautifulSoup(list_html, "html.parser")
        roots = soup.find_all(["ul", "ol"], recursive=False)
        loose_nodes = [n for n in soup.find_all(["p", "span"], recursive=False) if not n.find_parent(["li"])]

        if not roots and not loose_nodes:
            raw = soup.get_text("\n", strip=True)
            if raw:
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(3)
                self._add_styled_run(p, WD_ALIGN_PARAGRAPH.LEFT, "List-item", raw)
            return

        for root in roots:
            self._render_list_element(doc, root, level=0)
        for node in loose_nodes:
            self._render_loose_list_lines(doc, node)

    def _render_list_element(self, doc: DocumentObject, list_el, level: int) -> None:
        """Renders one `<ul>`/`<ol>` and recurses into nested sub-lists
        depth-first — a nested `<ul>/<ol>` inside an `<li>` is rendered
        immediately after that `<li>`'s own line, before its next sibling,
        so nested content lands in natural reading order (a prior
        breadth-first version queued *all* nested lists to render only
        after every top-level item, which read out of order)."""
        ordered = list_el.name == "ol"
        for position, li in enumerate(list_el.find_all("li", recursive=False), start=1):
            texts = []
            for child in li.contents:
                if getattr(child, "name", None) in ("ul", "ol"):
                    continue
                if hasattr(child, "get_text"):
                    texts.append(child.get_text(" ", strip=True))
                else:
                    texts.append(str(child).strip())
            raw = " ".join(t for t in texts if t).strip()
            if raw:
                self._render_list_line(doc, raw, level, ordered, position)

            for nested in li.find_all(["ul", "ol"], recursive=False):
                self._render_list_element(doc, nested, level + 1)

    def _render_loose_list_lines(self, doc: DocumentObject, node) -> None:
        """A `<p>`/`<span>` list-item block not wrapped in a real
        `<ul>/<ol>` — an OCR quirk where the model emits `<br>`-separated
        lines instead of proper list markup. Each line is still a genuine
        list item semantically, just flat (no nesting is possible without
        real `<li>` structure) and with no tag to say ordered-vs-unordered,
        so numbering/bullet comes entirely from whatever marker (if any)
        the line's own text already carries."""
        inner_html = node.decode_contents()
        for raw_html in re.split(r"<br\s*/?>", inner_html, flags=re.IGNORECASE):
            raw = BeautifulSoup(raw_html, "html.parser").get_text(" ", strip=True)
            if raw:
                self._render_list_line(doc, raw, level=0, ordered=False, position=None)

    def _render_list_line(self, doc: DocumentObject, raw: str, level: int, ordered: bool, position: int | None) -> None:
        """Renders one list line with a hanging-indent marker: reuses a
        marker the OCR text already carries (bullet char, or a
        number/letter/roman-numeral counter — e.g. "a)", "iv.", "1.1" —
        which may not be a plain sequential number, so it's trusted as-is
        rather than clobbered); only synthesizes one from the `<ul>/<ol>`
        tag itself (`position` for `<ol>`, a plain bullet for `<ul>`) when
        the text has none, which a bare `<li>text</li>` with no marker
        baked into the OCR'd text left with no visible marker at all
        before."""
        prefix, content = self._split_list_marker(raw)
        if prefix is None:
            content = raw
            prefix = f"{position}." if ordered and position is not None else _LIST_BULLET_CHAR

        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(3)
        self._add_styled_run(p, WD_ALIGN_PARAGRAPH.LEFT, "List-item", f"{prefix}\t{content}")

        base_indent = 18
        dynamic = min(len(prefix), 4) * 2
        hang = base_indent + dynamic
        total_indent = hang * (level + 1)
        p.paragraph_format.left_indent = Pt(total_indent)
        p.paragraph_format.first_line_indent = Pt(-hang)
        # Without an explicit tab stop here, the "\t" between marker and
        # content falls back to the document's default tab grid (usually
        # every 0.5in) instead of lining up with `total_indent` — since
        # `total_indent` varies with marker length and nesting level, it
        # routinely doesn't land on that grid, throwing the hanging
        # indent visibly out of alignment.
        p.paragraph_format.tab_stops.add_tab_stop(Pt(total_indent))

    def _split_list_marker(self, raw: str) -> tuple[str | None, str]:
        """Detects a marker the OCR text already carries. Returns
        `(None, raw)` when there isn't one — the caller then synthesizes a
        marker from the `<ul>/<ol>` tag itself instead."""
        m_bullet = _LIST_BULLET_RE.match(raw)
        if m_bullet:
            return _LIST_BULLET_CHAR, m_bullet.group(1)

        m_num = _LIST_NUM_RE.match(raw)
        if m_num:
            prefix, content = m_num.group(1), m_num.group(2)
            num = prefix.replace(".", "").rstrip("):")
            if num.isdigit() and int(num) > 100:
                # An implausibly large "counter" is almost certainly not a
                # real list marker (e.g. a sentence starting with a year
                # or a big quantity) — treat as plain text instead.
                return None, raw
            return prefix, content

        return None, raw


class DocumentRenderer(BlockRenderer):
    def __init__(
        self,
        doc: DocumentObject,
        sections: list[Section],
        header: list[DocumentLayout],
        footer: list[DocumentLayout],
        page_width: int = _DEFAULT_PAGE_WIDTH_PX,
        page_height: int = _DEFAULT_PAGE_HEIGHT_PX,
        orientation: str = "portrait",
        start_new_page: bool = False,
        left_margin: int = _DEFAULT_LEFT_MARGIN_PX,
        right_margin: int = _DEFAULT_MARGIN_PX,
        top_margin: int = _DEFAULT_MARGIN_PX,
        bottom_margin: int = _DEFAULT_MARGIN_PX,
        dpi: int = RENDER_DPI,
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

            widths = self._column_widths_px(section.columns)
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
                        self._add_styled_run(p, WD_ALIGN_PARAGRAPH.CENTER, block.category, block.text)
                        continue
                    if block.category == "Section-header":
                        p = doc.add_paragraph(style=f"Heading {block.level if block.level is not None else 3}")
                        p.paragraph_format.space_before = Pt(3)
                        p.paragraph_format.space_after = Pt(3)
                        self._add_styled_run(
                            p, self._block_alignment(block.bbox, col.bbox, is_single_col), block.category, block.text
                        )
                        continue
                    if len(doc.paragraphs) == 1 and not doc.paragraphs[0].text:
                        p = doc.paragraphs[0]
                    else:
                        p = doc.add_paragraph()
                        p.paragraph_format.space_before = Pt(3)
                    self._add_styled_run(
                        p, self._block_alignment(block.bbox, col.bbox, is_single_col), block.category, block.text
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
                self._add_styled_run(p, WD_ALIGN_PARAGRAPH.LEFT, block.category, block.text)

        process_container(section.header, self.header)
        process_container(section.footer, self.footer)

    def _column_widths_px(self, columns: list[Column]) -> list[float]:
        """`col.bbox` is the union of that column's blocks' bboxes, which
        live in full-page pixel space — a scanned page's text routinely
        starts/ends close to the physical page edges, since the source
        image reserves no margin of its own. Using those widths verbatim
        as docx column widths, on a section that *also* reserves
        `self.left_margin`/`right_margin`, made the columns (plus the fixed
        240-twip gap between them) add up to more than `self.content_width`
        — Word then pushes the rightmost column's content past the right
        margin. This scales all columns down proportionally (same relative
        widths, e.g. a narrow sidebar stays narrower than the body column)
        so the row always fits — the same proportional-distribution
        approach `_render_header_footer` already uses for header/footer
        cell widths.

        Before scaling, every non-last column's raw width is also widened
        to at least "distance to where the next column starts" (minus the
        gap). A column's own bbox can be narrower than the space it's
        actually meant to occupy — e.g. a lone Title/Section-header block
        renders at a much bigger fixed font size (`default_font_sizes`)
        than its bbox in the original scan implied, so a width taken
        straight from that bbox can make the rendered text overflow its
        column. The next column's start is a more trustworthy lower bound
        for "how much room this column really has" than this column's own
        (possibly narrow) content — the last column has no next column to
        borrow that bound from, so it's left as-is."""
        twips_per_px = 1440 / self.dpi  # same ratio `_set_section_layout`'s `scale` and `_px_to_twips` use
        gap_per_col_px = 240 / twips_per_px

        raw = []
        for i, c in enumerate(columns):
            width = c.bbox[2] - c.bbox[0]
            if i < len(columns) - 1:
                next_start_width = columns[i + 1].bbox[0] - c.bbox[0] - gap_per_col_px
                width = max(width, next_start_width)
            raw.append(width)

        total_raw = sum(raw)
        if total_raw <= 0:
            return raw
        gap_px = gap_per_col_px * (len(columns) - 1)
        available = max(self.content_width - gap_px, 1)
        return [w / total_raw * available for w in raw]

    def _set_section_layout(self, sectPr, num_columns, col_widths=None):
        cols_xml = sectPr.find(qn("w:cols"))
        if cols_xml is None:
            cols_xml = OxmlElement("w:cols")
            sectPr.append(cols_xml)
        cols_xml.set(qn("w:num"), str(num_columns))
        for c in cols_xml.findall(qn("w:col")):
            cols_xml.remove(c)
        scale = 1440 / self.dpi  # same ratio _px_to_twips/_column_widths_px use
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

    def _block_alignment(self, block_bbox, col_bbox, is_single_col: bool = False):
        """Replaces the old `_is_centered_block` (bool, defaulted anything
        non-centered to LEFT — RIGHT never existed). Same geometry, three
        possible verdicts instead of two:
        - narrow (not full-width) + roughly equal indent on both sides -> CENTER
        - narrow + flush against the right edge, real gap on the left -> RIGHT
        - anything else (full-width, or flush left, or no clear signal) -> LEFT
        """
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
        is_narrow = block_width / ref_width <= 0.9

        if is_narrow and abs(left_indent - right_indent) <= distance_threshold:
            alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif is_narrow and right_indent <= distance_threshold and left_indent > distance_threshold:
            alignment = WD_ALIGN_PARAGRAPH.RIGHT
        else:
            alignment = WD_ALIGN_PARAGRAPH.LEFT

        logger.debug(
            "alignment? single_col=%s block=%s ratio=%.2f threshold=%.1f result=%s",
            is_single_col, block_bbox, block_width / ref_width, distance_threshold, alignment,
        )
        return alignment


class PlainTextRenderer(BlockRenderer):
    """"Text thuần" mode's per-page renderer (see
    `export_service.py::PlainTextExportService`): no column/section
    reconstruction, no page-format normalization, no per-category font/
    heading styling — every block is appended straight into the shared
    `doc` in the order it appears in `layouts`. `Table`/`List-item` are the
    one deliberate exception (still built as a real docx table/list via the
    inherited `BlockRenderer` methods) since leaving their raw HTML as
    literal text would be unreadable, not "plain"."""

    def __init__(self, doc: DocumentObject):
        self.doc = doc

    def render_block(self, block: DocumentLayout) -> None:
        if block.category == "Table":
            self._render_table(block.text, self.doc)
            return
        if block.category == "List-item":
            self._render_list_item(block.text, self.doc)
            return
        if block.category == "Picture" or not block.text.strip():
            return
        self.doc.add_paragraph(self._strip_markdown(block.text))

    def _strip_markdown(self, text: str) -> str:
        """"Plain" means no bold/italic/underline *runs* applied — but the
        `**`/`*`/`__` delimiters themselves are markup, not content
        (`postprocessing.py::clean_html` writes them, same as layout mode
        reads them). Leaving them in would make "text thuần" show literal
        asterisks instead of clean text, so this reuses the same tokenizer
        `_add_styled_run` uses, just discarding the style flags and keeping
        only each span's content."""
        return "".join(content for content, *_ in self._iter_markdown_runs(text, False, False, False))
