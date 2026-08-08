"""Pydantic request/response DTOs, shared by preview.py and export.py.

No SQLAlchemy models here: this process never talks to a database — Nuxt
owns all Supabase reads/writes directly (§9.1). These are pure wire-format
types for the local sidecar's own HTTP contract.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PreviewPageDto(BaseModel):
    id: str
    order: int
    source: str


class PreviewDto(BaseModel):
    previewId: str
    title: str
    pages: list[PreviewPageDto]
    createdAt: str


class PreviewPatchRequest(BaseModel):
    title: str | None = None
    pageOrder: list[str] | None = None
    deletePageIds: list[str] | None = None


class DocumentLayout(BaseModel):
    """One layout block within a page — mirrors
    `Processor/backend/app/schemas/models.py::DocumentLayout` exactly (same
    contract already used by `User/frontend/app/types/models.ts`). `center`/
    `width`/`height` are the only additions here, needed by
    `services/layout_reconstructor.py`'s column/section-splitting
    math (ported from `Reformat_prototype/reformat_service_v2.py`, which
    expects these as properties on the layout object itself)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    bbox: list[float] = Field(default_factory=list)
    category: str = "Text"
    text: str = ""
    level: int | None = Field(None, ge=1, le=5)

    @model_validator(mode="after")
    def _validate_level(self) -> "DocumentLayout":
        if self.level is not None and self.category != "Section-header":
            raise ValueError(
                f"'level' is only valid when category='Section-header' (got '{self.category}')"
            )
        return self

    @property
    def center(self) -> tuple[float, float]:
        return 0.5 * (self.bbox[0] + self.bbox[2]), 0.5 * (self.bbox[1] + self.bbox[3])

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]


class OcrPageResult(BaseModel):
    """The `pages.ocr_text` payload for one page — mirrors
    `Processor/backend/app/schemas/models.py::OcrPageResult` exactly."""

    origin_width: int
    origin_height: int
    input_width: int
    input_height: int
    layouts: list[DocumentLayout] = Field(default_factory=list)


class ExportPageInput(BaseModel):
    pageId: str
    order: int
    ocrText: OcrPageResult | None = None
    originalImageUrl: str


class ExportRequest(BaseModel):
    examId: str
    title: str
    pages: list[ExportPageInput]
    mode: Literal["layout", "plain"] = "layout"
