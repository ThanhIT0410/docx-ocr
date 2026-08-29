"""HTTP request/response bodies — the API's public wire format. Kept
separate from models.py (DB row shapes) so an internal-only DB field isn't
automatically exposed externally just by reusing the same class.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.models import Page


class ExamListItem(BaseModel):
    """GET /processor/exams response entry — no page list, matches §2.1."""

    id: str
    title: str
    status: str
    error_message: str | None = None
    uploaded_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ExamDetail(ExamListItem):
    """GET /processor/exams/{examId} response — includes pages, per §2.1
    "Trả về danh sách/chi tiết đề (kèm danh sách trang)"."""

    pages: list[Page]


class PagePreview(BaseModel):
    """One row of GET /processor/exams/{examId}/preview — `url` is a
    short-lived Supabase Storage signed URL, `None` if signing that
    specific page failed (e.g. object missing), which shouldn't blank out
    preview for the rest of the exam's pages."""

    page_id: str
    url: str | None


class ExamPreviewResponse(BaseModel):
    pages: list[PagePreview]


class EnqueueRequest(BaseModel):
    """POST /processor/queue/enqueue body — a batch, since the frontend
    lets an operator select several exams at once."""

    exam_ids: list[str]


class EnqueueItemResult(BaseModel):
    """Per-exam_id outcome within an EnqueueResponse — a bulk request
    partially succeeding (e.g. the queue fills up partway through) is not
    an error for the exams that did fit, so this isn't just a plain
    success/fail HTTP status for the whole batch."""

    exam_id: str
    queued: bool
    reason: str | None = None


class EnqueueResponse(BaseModel):
    """`queue_size`/`queue_max_size` are page counts, not exam counts — see
    `app/services/queue_service.py::QueueService.admitted_pages`. Exams
    differ wildly in page count, so bounding admission by exam count didn't
    reflect real load; this is the total pages currently admitted into
    'processing' (queued + actively being OCR'd) against the configured cap."""

    results: list[EnqueueItemResult]
    queue_size: int
    queue_max_size: int


class DequeueRequest(BaseModel):
    """POST /processor/queue/dequeue body — pulls a batch of exams back
    OUT of the processing queue (operator cancel), symmetric with
    EnqueueRequest. Reverts each to 'pending' in the DB and wipes its
    pages' `ocr_text` — see controllers/queue.py."""

    exam_ids: list[str]


class DequeueItemResult(BaseModel):
    exam_id: str
    dequeued: bool
    reason: str | None = None


class DequeueResponse(BaseModel):
    """`queue_size` — see EnqueueResponse's docstring: pages, not exams."""

    results: list[DequeueItemResult]
    queue_size: int


class RetryRequest(BaseModel):
    """POST /processor/exams/retry body — moves a batch of 'failed' exams
    back to 'pending' (NOT straight to 'processing'/the OCR queue — the
    operator still enqueues separately via POST /processor/queue/enqueue,
    same as any other pending exam, see controllers/exams.py). Deliberately
    does NOT wipe `pages.ocr_text` (unlike DequeueRequest's cancel, which
    does) — pages that already OCR'd successfully before some OTHER page
    failed the exam are left alone, so PagePool's existing resume logic
    (app/services/page_pool.py) only re-processes the pages that never
    finished, instead of redoing already-correct work."""

    exam_ids: list[str]


class RetryItemResult(BaseModel):
    exam_id: str
    retried: bool
    reason: str | None = None


class RetryResponse(BaseModel):
    results: list[RetryItemResult]


class ExamProgressItem(BaseModel):
    """GET /processor/queue/progress entry — deliberately thin (just
    enough for a frontend to render "ExamName 4/8"), not the full
    ExamDetail shape. Meant to be polled frequently while OCR is running."""

    exam_id: str
    exam_title: str
    completed_pages: int
    total_pages: int


class ProgressResponse(BaseModel):
    """`pipeline_running`/`pipeline_error` reflect
    app/services/ocr_pipeline.py's `OcrPipelineState` — the pipeline stops
    itself on any error (see that module's docstring), so this is how the
    frontend finds out "still going" vs "stopped, here's why" without a
    separate endpoint."""

    pipeline_running: bool
    pipeline_error: str | None
    items: list[ExamProgressItem]


class OcrStartResponse(BaseModel):
    started: bool
    already_running: bool


class ResetResponse(BaseModel):
    exams_deleted: int
    pages_deleted: int
    storage_objects_deleted: int
    storage_objects_failed: list[str] = []


class StatusCounts(BaseModel):
    """Total exams per status (all-time, not "today") — GET
    /processor/dashboard's pending/processing/done/failed counters."""

    pending: int
    processing: int
    finished: int
    failed: int


class DashboardResponse(BaseModel):
    """GET /processor/dashboard — replaces the old GET /processor/worker/status
    (superseded, deleted) plus new stats. `db_size_bytes`/`storage_size_bytes`
    are `None` when `PROCESSOR_SUPABASE_ACCESS_TOKEN` isn't configured or the
    Management API call failed — see services/supabase_management.py."""

    llamacpp_healthy: bool
    finished_today: int
    failed_today: int
    counts: StatusCounts
    db_size_bytes: int | None
    storage_size_bytes: int | None
    # Total pages currently admitted into 'processing' (queued + actively
    # being OCR'd) against the configured cap — see
    # app/services/queue_service.py::QueueService.admitted_pages. Not part
    # of StatusCounts since that's purely exam counts per status; this is a
    # page-level capacity metric, shown alongside the "processing" stat on
    # the frontend's Dashboard (see Processor/DESIGN_REPORT.md).
    processing_pages: int
    processing_pages_limit: int
