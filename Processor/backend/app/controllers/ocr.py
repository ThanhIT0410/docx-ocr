"""HTTP route to switch on the background OCR pipeline
(app/services/ocr_pipeline.py). Starting is idempotent — calling this
while the pipeline is already running is a no-op, not an error (see
OcrPipelineState.start). There's no `/stop`: a run drains the queue and
stops itself once empty, or the moment anything goes wrong (see
ocr_pipeline.py's module docstring) — check `GET /processor/queue/progress`
for whether it's currently running and why it last stopped.

Calling this with nothing queued IS an error (409), not a silent no-op —
`run_pipeline` would otherwise start a background task just to have it
immediately see `QueueEmptyError` and end, which is a confusing "started
successfully" response for an action that did nothing. Enqueue at least
one exam first.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from supabase import AsyncClient

from app.config.settings import Settings, get_settings
from app.dependencies import (
    get_db,
    get_ocr_client,
    get_ocr_pipeline_state,
    get_queue_service,
    get_result_handler,
    get_storage,
)
from app.schemas.dto import OcrStartResponse
from app.security import require_api_key
from app.services.ocr_client import OcrClient
from app.services.ocr_pipeline import OcrPipelineState, run_pipeline
from app.services.queue_service import QueueService
from app.services.result_handler import ResultHandler
from app.storage.client import StorageHelper

router = APIRouter(prefix="/processor/ocr", tags=["ocr"], dependencies=[Depends(require_api_key)])


@router.post("/start", response_model=OcrStartResponse)
async def start(
    db: AsyncClient = Depends(get_db),
    storage: StorageHelper = Depends(get_storage),
    ocr_client: OcrClient = Depends(get_ocr_client),
    queue: QueueService = Depends(get_queue_service),
    result_handler: ResultHandler = Depends(get_result_handler),
    settings: Settings = Depends(get_settings),
    state: OcrPipelineState = Depends(get_ocr_pipeline_state),
) -> OcrStartResponse:
    # admitted_pages (not qsize()) — a previous run may have aborted
    # (llama.cpp unhealthy) and abandoned some exams mid-flight: their
    # page budget is still held (see QueueService.release's docstring) even
    # though they've already been popped out of the FIFO, so qsize() alone
    # would wrongly report "nothing to do" and refuse to let the operator
    # resume them (run_pipeline re-admits them into the FIFO on start, see
    # app/services/recovery.py).
    if not state.running and queue.admitted_pages == 0:
        raise HTTPException(status_code=409, detail="Hàng đợi đang trống — chưa có đề nào để xử lý.")
    started = state.start(run_pipeline(db, storage, ocr_client, queue, result_handler, settings))
    return OcrStartResponse(started=started, already_running=not started)
