"""Orchestrates a full exam export into a single .docx — the entry point
`controllers/export.py` calls directly (`create_export_service(title, mode)
.export(pages)`).

Replaces the old prototype's per-page `Document()` + external merge step
(`Reformat_prototype/reformat_controller.py`'s `doc_service.export_docx()`,
never actually present in the reference files — see `Test/PIPELINE_NOTES.md`
§6) with **one shared `Document`** for the whole export, built up page by
page. Every exporter here is deliberately not reusable across requests (it
holds the shared, mutable `doc`) — the controller constructs a fresh
instance per request.

**Two export modes, one class hierarchy**: `BaseExportService` owns
everything mode-independent (the shared `doc`, the export loop, filename
generation); each mode is its own concrete subclass rather than an
if/else sprinkled through one class:
- `LayoutExportService` — "giữ layout": the full pipeline (page-format
  normalization + `LayoutReconstructorV2` column/section reconstruction +
  `DocumentRenderer`). This is the original `ExportService`.
- `PlainTextExportService` — "text thuần": no normalization, no
  reconstruction — every block appended straight into the doc via
  `PlainTextRenderer`, in the order the model produced it. `Table`/
  `List-item` still become real docx structures (see
  `document_renderer.py::BlockRenderer`), everything else is unstyled text.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import BytesIO

from docx import Document

from app.schemas import ExportPageInput, OcrPageResult
from app.services.document_renderer import DocumentRenderer, PlainTextRenderer
from app.services.layout_reconstructor_v2 import LayoutReconstructorV2
from app.services.page_format_normalizer import PageFormatNormalizer

logger = logging.getLogger(__name__)

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Margin derivation tuning (LayoutExportService.reconstruct, see
# _content_margins below) — trims 5% of extreme values off each end before
# taking min/max, then hard-clamps to [50, 200] px (@200dpi: ~0.25in-1in)
# regardless of what trimming produced.
_MARGIN_TRIM_PCT = 0.05
_MARGIN_MIN_PX = 50
_MARGIN_MAX_PX = 200


class UnsupportedPageAspectRatioError(Exception):
    """Raised when a page's aspect ratio doesn't come close enough to any
    supported paper format (`ratio_diff > 0.2`, see
    `PageFormatNormalizer.find_nearest_format`). The old prototype fell back
    to a `ReformatServiceV1` this repo never had a source for — this system
    surfaces the problem instead (see `controllers/export.py`, HTTP 422).
    Only `LayoutExportService` can raise this — `PlainTextExportService`
    never looks at page format at all."""

    def __init__(self, page_order: int, ratio_diff: float):
        super().__init__(f"Không hỗ trợ khổ giấy của trang thứ {page_order} có aspect ratio này")
        self.page_order = page_order
        self.ratio_diff = ratio_diff


@dataclass
class ExportResult:
    content: bytes
    filename: str
    media_type: str


class BaseExportService(ABC):
    """Shared export loop: sort pages by order, hand each to the subclass,
    save the accumulated `doc` to bytes. Subclasses only implement
    `_add_page` — how one page's OCR result gets appended."""

    def __init__(self, title: str):
        self._title = title
        self.doc = Document()
        self._is_first_page = True

    def export(self, pages: list[ExportPageInput]) -> ExportResult:
        for page_input in sorted(pages, key=lambda p: p.order):
            self._add_page(page_input)
            self._is_first_page = False

        buf = BytesIO()
        self.doc.save(buf)
        safe_title = _ascii_filename(self._title) or "de-thi"
        return ExportResult(content=buf.getvalue(), filename=f"{safe_title}.docx", media_type=DOCX_MEDIA_TYPE)

    @abstractmethod
    def _add_page(self, page_input: ExportPageInput) -> None:
        """Append one page's content to `self.doc`. `self._is_first_page`
        is still `True` for the page currently being added — flipped to
        `False` by `export()` right after this returns, so implementations
        needing to know "is this the first page" (to decide whether to
        break to a new page/section) can read it here."""


class LayoutExportService(BaseExportService):
    """"Giữ layout" mode — was the only `ExportService` before plain-text
    mode existed. See module docstring."""

    def __init__(self, title: str):
        super().__init__(title)
        self._normalizer = PageFormatNormalizer()

    def _add_page(self, page_input: ExportPageInput) -> None:
        ocr = page_input.ocrText
        if ocr is None:
            raise ValueError(f"Trang thứ {page_input.order} chưa có kết quả OCR — không thể export.")

        valid_layouts = [layout for layout in ocr.layouts if len(layout.bbox) == 4]
        if len(valid_layouts) != len(ocr.layouts):
            logger.warning(
                "page %d: dropped %d layout(s) with malformed bbox",
                page_input.order, len(ocr.layouts) - len(valid_layouts),
            )
        ocr.layouts = valid_layouts

        best_format, orientation, target_width, target_height, ratio_diff = self._normalizer.find_nearest_format(
            ocr.origin_width, ocr.origin_height
        )
        logger.info(
            "page %d: format=%s orientation=%s target=%dx%d origin=%dx%d ratio_diff=%.2f%%",
            page_input.order, best_format, orientation, target_width, target_height,
            ocr.origin_width, ocr.origin_height, ratio_diff * 100,
        )
        if ratio_diff > 0.2:
            raise UnsupportedPageAspectRatioError(page_input.order, ratio_diff)

        resized = self._normalizer.resize(ocr, target_width, target_height)
        self.reconstruct(resized, target_width, target_height, orientation)

    def reconstruct(self, page: OcrPageResult, page_width: int, page_height: int, orientation: str) -> None:
        """Mirrors the prototype's `ReformatServiceV2.reconstruct()`: builds
        a `LayoutReconstructorV2` + a `DocumentRenderer` for this one page —
        except the renderer now appends into `self.doc` instead of
        returning a new `Document`. `layout_reconstructor.py` (V1,
        rule-based) is no longer called here — kept in the repo for
        reference only, see `layout_reconstructor_v2.py`'s module docstring."""
        reconstructor = LayoutReconstructorV2(page_width=page_width, page_height=page_height)
        blocks = page.layouts
        header, footer = reconstructor.extract_header_footer(blocks)
        main_blocks = [b for b in blocks if b not in header and b not in footer]
        sections = reconstructor.reconstruct(main_blocks)

        # Only left/right are derived from content — top/bottom stay the
        # fixed `DocumentRenderer` default. Those axes are entangled with
        # Page-header/Page-footer detection (whether a block near the top/
        # bottom edge should count as body content or as header/footer is
        # itself an open question upstream, see extract_header_footer above)
        # in a way left/right aren't, so deriving them from `main_blocks`
        # would be building on unsettled ground — deliberately left alone.
        left_margin, right_margin = self._content_margins(
            [b.bbox[0] for b in main_blocks], [b.bbox[2] for b in main_blocks], page_width
        )

        renderer = DocumentRenderer(
            self.doc, sections, header, footer,
            page_width=page_width, page_height=page_height, orientation=orientation,
            start_new_page=not self._is_first_page,
            left_margin=left_margin, right_margin=right_margin,
        )
        renderer.render()

    def _content_margins(self, starts: list[float], ends: list[float], extent: int) -> tuple[float, float]:
        """Derives a (start_margin, end_margin) pair — e.g. (left, right) —
        from where a page's own content actually begins/ends along one
        axis, instead of the fixed pixel value used before. `bbox` lives in
        full-page pixel space (see PageFormatNormalizer's module docstring:
        it scales to the target paper's full physical dimensions, not a
        margin-aware content area), so a fixed margin routinely didn't
        match where content really sat, letting rendered columns spill past
        it (see document_renderer.py's `_column_widths_px`, added for the
        same underlying mismatch).

        Naively using the true min/max would let a single mispredicted bbox
        (one stray block near the page edge) collapse a margin to ~0, so
        this trims `_MARGIN_TRIM_PCT` of the extreme values off each end
        first — for a typical page with a handful to a few dozen blocks,
        that drops at most the single worst outlier per side while still
        tracking where most of the real content sits. Pages with too few
        blocks for that trim to remove anything (`n <= 2 * trim_n`) fall
        back to plain min/max, and the result is clamped to
        [_MARGIN_MIN_PX, _MARGIN_MAX_PX] regardless — the hard safety net
        for both the "too few blocks to trim" case and any remaining
        extreme.
        """
        if not starts:
            return _MARGIN_MIN_PX, _MARGIN_MIN_PX
        starts = sorted(starts)
        ends = sorted(ends)
        n = len(starts)
        trim_n = int(n * _MARGIN_TRIM_PCT)
        start_edge = starts[trim_n] if n > 2 * trim_n else starts[0]
        end_edge = ends[n - 1 - trim_n] if n > 2 * trim_n else ends[-1]
        start_margin = max(_MARGIN_MIN_PX, min(_MARGIN_MAX_PX, start_edge))
        end_margin = max(_MARGIN_MIN_PX, min(_MARGIN_MAX_PX, extent - end_edge))
        return start_margin, end_margin


class PlainTextExportService(BaseExportService):
    """"Text thuần" mode — see module docstring. No `PageFormatNormalizer`,
    no `LayoutReconstructorV2`: a page's blocks go straight to
    `PlainTextRenderer` in their original order."""

    def _add_page(self, page_input: ExportPageInput) -> None:
        ocr = page_input.ocrText
        if ocr is None:
            raise ValueError(f"Trang thứ {page_input.order} chưa có kết quả OCR — không thể export.")

        if not self._is_first_page:
            self.doc.add_page_break()

        renderer = PlainTextRenderer(self.doc)
        for block in ocr.layouts:
            renderer.render_block(block)


def create_export_service(title: str, mode: str = "layout") -> BaseExportService:
    """Picks the concrete exporter for `mode` (see `schemas.ExportRequest.mode`).
    `controllers/export.py` and tests call this rather than instantiating a
    subclass directly, so the HTTP contract's `mode` string only needs to be
    interpreted in one place."""
    if mode == "plain":
        return PlainTextExportService(title)
    return LayoutExportService(title)


def _ascii_filename(title: str) -> str:
    """Transliterates a Vietnamese title into a readable ASCII-safe
    filename, since `Content-Disposition`'s plain `filename` param is
    latin-1-only regardless (see controllers/export.py's `filename*` RFC
    5987 header for the real unicode name)."""
    title = title.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", title)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in stripped if c.isascii() and (c.isalnum() or c in " -_")).strip()
