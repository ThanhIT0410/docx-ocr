"""End-to-end smoke test for the export pipeline now that
`ExportService.reconstruct()` calls `LayoutReconstructorV2` unconditionally
(no more rule-based/ML switch — see `export_service.py`'s module docstring
update)."""
from __future__ import annotations

from docx import Document

from app.schemas import DocumentLayout, ExportPageInput, OcrPageResult
from app.services.export_service import ExportService


def _page(order: int) -> ExportPageInput:
    layouts = [
        DocumentLayout(bbox=[100, 80, 1200, 160], category="Title", text="De thi mau"),
        DocumentLayout(bbox=[100, 200, 1200, 900], category="Text", text="Cau 1: 2 * 3 * 4 = ?"),
    ]
    ocr = OcrPageResult(origin_width=1654, origin_height=2338, input_width=1654, input_height=2338, layouts=layouts)
    return ExportPageInput(
        pageId=f"page-{order}", order=order, ocrText=ocr, originalImageUrl=f"https://example.test/{order}.jpg"
    )


def test_export_produces_a_valid_docx_via_v2():
    result = ExportService("De thi thu").export([_page(1), _page(2)])
    assert result.filename == "De thi thu.docx"
    assert result.media_type.endswith("wordprocessingml.document")

    from io import BytesIO

    doc = Document(BytesIO(result.content))
    # 2 pages, each starting its own w:sectPr — this is what
    # start_new_page=not self._is_first_page (document_renderer.py) produces.
    assert len(doc.sections) >= 2
