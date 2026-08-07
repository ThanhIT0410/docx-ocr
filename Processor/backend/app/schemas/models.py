"""Pydantic models mirroring DB row shapes — User/supabase/schema.sql. Kept
separate from the HTTP request/response models in dto.py so an
internal-only field can be included in DB reads without necessarily
committing to exposing it externally forever.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentLayout(BaseModel):
    """One layout block within a page — the model's raw HTML response
    (`<div data-bbox="..." data-label="...">`, see app/prompts.py) parsed
    into structured form by services/postprocessing.py. Shape matches
    `DocumentLayout` in Test/ocr.py exactly (the old system's reformat/
    export pipeline) — this is the Processor side formally adopting that
    contract now that the real prompt/model output format is known (see
    DESIGN_REPORT.md §6.3 for the full write-up; this used to be a
    placeholder `OcrBlock` shape before the real prompt existed).

    `bbox` is in **input** pixel space (see `OcrPageResult.input_width/
    height` below) — i.e. relative to the resized image actually sent to
    the model, not the original page image.
    """

    bbox: list[float] = Field(default_factory=list)
    category: str = "Text"
    text: str
    level: int | None = Field(None, ge=1, le=5)

    @model_validator(mode="after")
    def _validate_level(self) -> "DocumentLayout":
        if self.level is not None and self.category != "Section-header":
            raise ValueError(
                f"'level' is only valid when category='Section-header' (got '{self.category}')"
            )
        return self


class OcrPageResult(BaseModel):
    """The full `pages.ocr_text` payload for one page. Carries both the
    original page dimensions (`origin_*` — what the reformat/export
    pipeline needs to reconstruct the real page, e.g. matching a paper
    format) and the dimensions actually shown to the model (`input_*` —
    what `DocumentLayout.bbox` above is normalized against, after
    services/preprocessing.py's smart_resize)."""

    origin_width: int
    origin_height: int
    input_width: int
    input_height: int
    layouts: list[DocumentLayout] = Field(default_factory=list)


class Page(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    exam_id: str
    page_order: int
    file_path: str
    ocr_text: OcrPageResult | None = None


class Exam(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: str
    error_message: str | None = None
    uploaded_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime
