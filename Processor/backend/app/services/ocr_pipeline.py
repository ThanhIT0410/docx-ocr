"""The main OCR loop, run as a long-lived background `asyncio.Task` (not
FastAPI's `BackgroundTasks` — that's for quick post-response work, not an
indefinite loop; see controllers/ocr.py's `POST /processor/ocr/start`,
which launches this via `OcrPipelineState.start`).

One iteration of `run_pipeline`:
1. Health-check llama.cpp.
2. Pull the next exam_id off `QueueService` (`dequeue()` — the raw method;
   not the same as `POST /processor/queue/dequeue`, which cancels an exam
   instead, see queue_service.py's module docstring). Nothing queued ->
   the loop ends and the background task completes normally (see below —
   this is intentional, not a bug: one `POST /processor/ocr/start` call
   processes whatever's in the queue *right now* and then stops, rather
   than idling forever waiting for more work).
3. Process every remaining page of that exam concurrently, bounded by
   `PROCESSOR_MAX_CONCURRENT_PAGES` coroutines: download -> preprocess ->
   call llama.cpp -> `ResultHandler.append` (which itself does the
   "write ocr_text, then check/flip to finished" cluster atomically — see
   result_handler.py; there's no real cross-system transaction to be had
   between Postgres and this process's memory, so `append` being a single
   coroutine with no `await` between its bookkeeping steps *is* the
   atomicity guarantee here, not a DB `BEGIN`/`COMMIT`).

Fail-fast by design: ANY exception anywhere in this loop — health check,
storage, preprocessing, the model call, a DB write — cancels every other
in-flight page task for the current exam, marks that exam 'failed' with
the error message, and propagates out of `run_pipeline` entirely, stopping
the whole background task (not just skipping one page or one exam). This
is a deliberate simplification versus the old per-page-retry/isolate-and-
continue design: the caller (`OcrPipelineState`) records the error so
`GET /processor/queue/progress` can surface "OCR stopped, here's why" to
the frontend, and `POST /processor/ocr/start` must be called again to
resume — nothing here silently reduces throughput and keeps going.
"""
from __future__ import annotations

import asyncio
import logging

from supabase import AsyncClient

from app.config.settings import Settings
from app.constants import STATUS_FAILED
from app.repositories import exams, pages
from app.schemas.models import Page
from app.services.health_check import check_llamacpp_health
from app.services.ocr_client import OcrClient
from app.services.preprocessing import preprocess_image
from app.services.queue_service import QueueEmptyError, QueueService
from app.services.result_handler import ResultHandler
from app.storage.client import StorageHelper

logger = logging.getLogger(__name__)


class OcrPipelineState:
    """Tracks whether the background OCR task is running and, if it
    stopped due to an error, what that error was — read by
    `GET /processor/queue/progress` (see controllers/queue.py) so the
    frontend can tell "still going" apart from "stopped, needs attention"
    without a separate polling target.
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, pipeline_coro) -> bool:
        """Launches `pipeline_coro` (an already-constructed coroutine) as
        a background task if nothing is running yet. Returns whether this
        call actually started it — False (a no-op) if already running, so
        `POST /processor/ocr/start` is safe to call repeatedly."""
        if self.running:
            pipeline_coro.close()  # never awaited — must be closed to avoid a "coroutine was never awaited" warning
            return False
        self.error = None
        self._task = asyncio.create_task(self._run(pipeline_coro))
        return True

    async def _run(self, pipeline_coro) -> None:
        try:
            await pipeline_coro
        except Exception as exc:  # noqa: BLE001 — last resort: capture *any* reason the pipeline died
            self.error = str(exc)
            logger.exception("OCR pipeline stopped due to an error")


async def run_pipeline(
    db: AsyncClient,
    storage: StorageHelper,
    ocr_client: OcrClient,
    queue: QueueService,
    result_handler: ResultHandler,
    settings: Settings,
) -> None:
    """Drains `queue` completely, then returns — by design, a "run" is one
    pass over whatever was enqueued at the time `POST /processor/ocr/start`
    was called, not an indefinitely-idling background service. New exams
    enqueued *during* an active run are still picked up (they land in the
    same `QueueService` this loop keeps re-`dequeue()`ing from), but once
    it drains, `OcrPipelineState.running` goes back to False and the
    operator has to explicitly start another run (enqueue -> "Bật xử lý
    OCR" again) — no `worker_poll_interval_seconds` sleep-and-retry here
    anymore, since there's no "wait for more work" state to poll through."""
    while True:
        if not await check_llamacpp_health(settings.llamacpp_base_url):
            raise RuntimeError("llama.cpp health check failed")

        try:
            exam_id = queue.dequeue()
        except QueueEmptyError:
            return

        await _process_exam(db, storage, ocr_client, result_handler, exam_id, settings)


async def _process_exam(
    db: AsyncClient,
    storage: StorageHelper,
    ocr_client: OcrClient,
    result_handler: ResultHandler,
    exam_id: str,
    settings: Settings,
) -> None:
    exam = await exams.get_exam(db, exam_id)
    if exam is None:
        # Dequeued but gone from the DB (e.g. deleted via admin/reset
        # between enqueue and now) — nothing to mark failed, just stop.
        raise RuntimeError(f"exam {exam_id} was dequeued but no longer exists")

    try:
        all_pages = await pages.list_pages(db, exam_id)
        existing_results = {p.id: p.ocr_text for p in all_pages if p.ocr_text is not None}
        remaining = [p for p in all_pages if p.ocr_text is None]

        result_handler.start_exam(exam_id, exam.title, total_pages=len(all_pages), existing_results=existing_results)
        if await result_handler.finalize_if_complete(exam_id):
            return  # every page was already done (fully resumed) — nothing left to process

        await _process_pages(storage, ocr_client, result_handler, exam_id, remaining, settings)
    except Exception as exc:
        await exams.update_exam(db, exam_id, {"status": STATUS_FAILED, "error_message": str(exc)})
        raise


async def _process_pages(
    storage: StorageHelper,
    ocr_client: OcrClient,
    result_handler: ResultHandler,
    exam_id: str,
    remaining: list[Page],
    settings: Settings,
) -> None:
    semaphore = asyncio.Semaphore(max(1, settings.max_concurrent_pages))

    async def process_one(page: Page) -> None:
        async with semaphore:
            raw = await storage.download(page.file_path)
            # preprocess_image is CPU-bound (OpenCV) — off the event loop
            # so it doesn't stall every other page's I/O while it runs.
            pre = await asyncio.to_thread(preprocess_image, raw, settings)
            result = await ocr_client.process_image(
                pre.content, pre.origin_width, pre.origin_height, pre.input_width, pre.input_height
            )
            await result_handler.append(exam_id, page.id, result)

    tasks = [asyncio.create_task(process_one(p)) for p in remaining]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    if pending:
        # One page failed — stop the others for this exam immediately
        # rather than let them keep burning llama.cpp calls for an exam
        # that's about to be marked failed anyway.
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    for task in done:
        exc = task.exception()
        if exc is not None:
            raise exc
