"""Orchestrates a full exam export into a single .docx — the entry point
`controllers/export.py` calls directly (`ExportService(title).export(pages)`).

Replaces the old prototype's per-page `Document()` + external merge step
(`Reformat_prototype/reformat_controller.py`'s `doc_service.export_docx()`,
never actually present in the reference files — see `Test/PIPELINE_NOTES.md`
§6) with **one shared `Document`** for the whole export, built up page by
page. `ExportService` is deliberately not reusable across requests (it holds
the shared, mutable `doc`) — the controller constructs a fresh instance per
request.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO

from docx import Document

from app.schemas import ExportPageInput, OcrPageResult
from app.services.document_renderer import DocumentRenderer
from app.services.layout_reconstructor_v2 import LayoutReconstructorV2
from app.services.page_format_normalizer import PageFormatNormalizer

logger = logging.getLogger(__name__)

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class UnsupportedPageAspectRatioError(Exception):
    """Raised when a page's aspect ratio doesn't come close enough to any
    supported paper format (`ratio_diff > 0.2`, see
    `PageFormatNormalizer.find_nearest_format`). The old prototype fell back
    to a `ReformatServiceV1` this repo never had a source for — this system
    surfaces the problem instead (see `controllers/export.py`, HTTP 422)."""

    def __init__(self, page_order: int, ratio_diff: float):
        super().__init__(f"Không hỗ trợ khổ giấy của trang thứ {page_order} có aspect ratio này")
        self.page_order = page_order
        self.ratio_diff = ratio_diff


@dataclass
class ExportResult:
    content: bytes
    filename: str
    media_type: str


class ExportService:
    def __init__(self, title: str):
        self._title = title
        self.doc = Document()
        self._normalizer = PageFormatNormalizer()
        self._is_first_page = True

    def export(self, pages: list[ExportPageInput]) -> ExportResult:
        for page_input in sorted(pages, key=lambda p: p.order):
            self._add_page(page_input)

        buf = BytesIO()
        self.doc.save(buf)
        safe_title = _ascii_filename(self._title) or "de-thi"
        return ExportResult(content=buf.getvalue(), filename=f"{safe_title}.docx", media_type=DOCX_MEDIA_TYPE)

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
        self._is_first_page = False

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

        renderer = DocumentRenderer(
            self.doc, sections, header, footer,
            page_width=page_width, page_height=page_height, orientation=orientation,
            start_new_page=not self._is_first_page,
        )
        renderer.render()


def _ascii_filename(title: str) -> str:
    """Transliterates a Vietnamese title into a readable ASCII-safe
    filename, since `Content-Disposition`'s plain `filename` param is
    latin-1-only regardless (see controllers/export.py's `filename*` RFC
    5987 header for the real unicode name)."""
    title = title.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", title)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in stripped if c.isascii() and (c.isalnum() or c in " -_")).strip()
