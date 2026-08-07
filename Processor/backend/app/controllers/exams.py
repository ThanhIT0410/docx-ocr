"""HTTP routes for viewing exams (§2.1). Business logic is trivial enough
here (plain reads) to live directly in the handlers — no separate service
module, matching preview.py/export.py's "controller translates HTTP <->
service, service does the work" split only where there IS a non-trivial
service."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import AsyncClient

from app.constants import ALL_STATUSES
from app.dependencies import get_db, get_storage
from app.repositories import exams, pages
from app.schemas.dto import ExamDetail, ExamListItem, ExamPreviewResponse, PagePreview
from app.security import require_api_key
from app.storage.client import StorageHelper

router = APIRouter(prefix="/processor/exams", tags=["exams"], dependencies=[Depends(require_api_key)])

# Long enough that an operator browsing an exam's pages for a while doesn't
# hit an expired link mid-look; short enough that a signed URL leaking
# somewhere (a log line, a screenshot) isn't useful for long.
PREVIEW_URL_EXPIRES_IN_SECONDS = 3600


@router.get("", response_model=list[ExamListItem])
async def list_exams(
    status: str | None = Query(default=None, pattern=f"^({'|'.join(ALL_STATUSES)})$"),
    db: AsyncClient = Depends(get_db),
) -> list[ExamListItem]:
    exam_list = await exams.list_exams(db, status)
    return [ExamListItem.model_validate(e.model_dump()) for e in exam_list]


@router.get("/{exam_id}", response_model=ExamDetail)
async def get_exam(exam_id: str, db: AsyncClient = Depends(get_db)) -> ExamDetail:
    # exam_id is known upfront, so the pages query doesn't need to wait on
    # the exam query — fire both concurrently instead of one after another
    # (wasted if the exam turns out missing, but that's the rare path).
    exam, exam_pages = await asyncio.gather(
        exams.get_exam(db, exam_id),
        pages.list_pages(db, exam_id),
    )
    if exam is None:
        raise HTTPException(status_code=404, detail=f"Exam {exam_id} not found")
    return ExamDetail.model_validate({**exam.model_dump(), "pages": exam_pages})


@router.get("/{exam_id}/preview", response_model=ExamPreviewResponse)
async def get_exam_preview(
    exam_id: str,
    db: AsyncClient = Depends(get_db),
    storage: StorageHelper = Depends(get_storage),
) -> ExamPreviewResponse:
    """Signed image URLs for every page of an exam, one batched call to
    Supabase Storage — deliberately separate from GET /{exam_id} (which
    gets polled every few seconds while an exam is processing): nothing
    needs a fresh signed URL that often, so this stays an explicit,
    on-demand fetch instead of riding along on the polling loop."""
    exam_pages = await pages.list_pages(db, exam_id)
    urls = await storage.create_signed_urls(
        [p.file_path for p in exam_pages], PREVIEW_URL_EXPIRES_IN_SECONDS
    )
    return ExamPreviewResponse(
        pages=[PagePreview(page_id=p.id, url=urls.get(p.file_path)) for p in exam_pages]
    )
