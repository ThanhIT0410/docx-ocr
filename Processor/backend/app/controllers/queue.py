"""HTTP routes for the in-process exam queue (app/services/queue_service.py).

POST .../enqueue takes a batch of exam IDs (the frontend lets an operator
select several exams at once), validates+queues each independently — one
exam being invalid or the queue filling up partway through doesn't fail
the whole batch, it's reported per exam_id in the response — and flips
each queued exam's DB status to 'processing'.

POST .../dequeue is the inverse: an operator pulling exams back OUT of the
processing queue (cancel), not the OCR loop's internal work-pull (that's
`QueueService.dequeue()`, the raw method — see app/services/queue_service.py
and app/services/recovery.py for its actual callers). Reverts each
cancelled exam to 'pending' and wipes its pages' `ocr_text`, so a later
fresh enqueue doesn't resume from a stale partial result.

GET .../progress is a thin, frequent-polling-friendly view of whatever's
currently being OCR'd — backed entirely by
app/services/result_handler.py's in-memory state (no DB round-trip).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from postgrest.exceptions import APIError
from supabase import AsyncClient

from app.constants import STATUS_PENDING, STATUS_PROCESSING
from app.dependencies import get_db, get_ocr_pipeline_state, get_queue_service, get_result_handler
from app.repositories import exams, pages
from app.schemas.dto import (
    DequeueItemResult,
    DequeueRequest,
    DequeueResponse,
    EnqueueItemResult,
    EnqueueRequest,
    EnqueueResponse,
    ExamProgressItem,
    ProgressResponse,
)
from app.security import require_api_key
from app.services.ocr_pipeline import OcrPipelineState
from app.services.queue_service import (
    ExamAlreadyQueuedError,
    ExamNotQueuedError,
    QueueFullError,
    QueueService,
)
from app.services.result_handler import ResultHandler

router = APIRouter(prefix="/processor/queue", tags=["queue"], dependencies=[Depends(require_api_key)])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/enqueue", response_model=EnqueueResponse)
async def enqueue(
    body: EnqueueRequest,
    db: AsyncClient = Depends(get_db),
    queue: QueueService = Depends(get_queue_service),
) -> EnqueueResponse:
    results: list[EnqueueItemResult] = []
    queue_full = False

    for exam_id in body.exam_ids:
        if queue_full:
            # Already know the cap is hit — skip the DB round-trip for the
            # rest of the batch, they'd fail for the same reason.
            results.append(EnqueueItemResult(exam_id=exam_id, queued=False, reason="queue is full"))
            continue

        try:
            exam = await exams.get_exam(db, exam_id)
        except APIError:
            # PostgREST rejects a malformed id (e.g. not a UUID) with a
            # 400 before it ever gets to "no matching row" — same outward
            # meaning as not found, from an enqueue caller's point of view.
            results.append(EnqueueItemResult(exam_id=exam_id, queued=False, reason="not found"))
            continue
        if exam is None:
            results.append(EnqueueItemResult(exam_id=exam_id, queued=False, reason="not found"))
            continue
        if exam.status != STATUS_PENDING:
            results.append(
                EnqueueItemResult(
                    exam_id=exam_id, queued=False, reason=f"status is '{exam.status}', expected 'pending'"
                )
            )
            continue

        try:
            queue.enqueue(exam_id)
        except ExamAlreadyQueuedError:
            results.append(EnqueueItemResult(exam_id=exam_id, queued=False, reason="already queued"))
            continue
        except QueueFullError as exc:
            queue_full = True
            results.append(EnqueueItemResult(exam_id=exam_id, queued=False, reason=str(exc)))
            continue

        await exams.update_exam(db, exam_id, {"status": STATUS_PROCESSING, "started_at": _now_iso()})
        results.append(EnqueueItemResult(exam_id=exam_id, queued=True))

    return EnqueueResponse(results=results, queue_size=queue.qsize(), queue_max_size=queue.max_size)


@router.post("/dequeue", response_model=DequeueResponse)
async def dequeue(
    body: DequeueRequest,
    db: AsyncClient = Depends(get_db),
    queue: QueueService = Depends(get_queue_service),
) -> DequeueResponse:
    results: list[DequeueItemResult] = []

    for exam_id in body.exam_ids:
        try:
            queue.cancel(exam_id)
        except ExamNotQueuedError as exc:
            results.append(DequeueItemResult(exam_id=exam_id, dequeued=False, reason=str(exc)))
            continue

        await pages.clear_ocr_text_for_exam(db, exam_id)
        await exams.update_exam(
            db,
            exam_id,
            {"status": STATUS_PENDING, "started_at": None, "error_message": None},
        )
        results.append(DequeueItemResult(exam_id=exam_id, dequeued=True))

    return DequeueResponse(results=results, queue_size=queue.qsize())


@router.get("/progress", response_model=ProgressResponse)
def progress(
    handler: ResultHandler = Depends(get_result_handler),
    pipeline: OcrPipelineState = Depends(get_ocr_pipeline_state),
) -> ProgressResponse:
    return ProgressResponse(
        pipeline_running=pipeline.running,
        pipeline_error=pipeline.error,
        items=[
            ExamProgressItem(
                exam_id=s.exam_id,
                exam_title=s.exam_title,
                completed_pages=s.completed_pages,
                total_pages=s.total_pages,
            )
            for s in handler.list_progress()
        ],
    )
