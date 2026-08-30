"""The main OCR loop, run as a long-lived background `asyncio.Task` (not
FastAPI's `BackgroundTasks` — that's for quick post-response work, not an
indefinite loop; see controllers/ocr.py's `POST /processor/ocr/start`,
which launches this via `OcrPipelineState.start`).

`run_pipeline`:
1. Re-admits any exam left `processing` with nothing currently tracking it,
   via `recover_processing_exams` (`app/services/recovery.py`) — the same
   function `app/main.py` calls once at process startup, but here it also
   covers a *previous* call to `run_pipeline` that aborted mid-flight (see
   "abandoned" below) — that case does NOT happen only once at startup.
2. Spawns exactly `settings.max_concurrent_pages` persistent worker
   coroutines sharing one `PagePool` (app/services/page_pool.py) — each
   loops `pop_page()` -> download -> preprocess -> call llama.cpp ->
   `ResultHandler.append()`, across however many exams are admitted, until
   `pop_page()` returns `None` (everything admitted has been fully
   dispatched). This is what lets a worker roll from "the current exam's
   last page" straight into the next exam's first page without waiting for
   every other worker to also finish that exam — the whole point of the
   page tier (see queue_service.py's module docstring for the exam-vs-page
   split, and PagePool's for how pages from multiple exams get interleaved).
3. A separate background task pings llama.cpp every
   `settings.healthcheck_interval_seconds` and flips a shared
   `asyncio.Event` — workers check it (cheap, no HTTP call) before each
   `pop_page()`, so an unhealthy llama.cpp stops every worker without each
   of them redundantly polling it themselves.

Fail-fast, now scoped to the *exam*, not the whole run: a page-level
exception (storage, preprocessing, the model call, a DB write) marks only
that page's exam 'failed' via `ResultHandler.fail_exam` — every OTHER
exam's workers are unaffected, since they're independent coroutines pulling
independent pages. This is a deliberate narrowing from the old
per-exam-sequential design (where ANY exception stopped the entire
background task) — safe now specifically because worker failures no longer
share mutable state beyond `PagePool`/`ResultHandler`, both of which are
built to isolate one exam's conclusion from another's. A page belonging to
an exam some *other* page already failed is not retried or specially
cancelled — it's left to finish (or fail) on its own and its result is
simply discarded (`UnknownExamError` from `ResultHandler.append`), which is
simpler and just as correct as tracking+cancelling that exam's other
in-flight `Task`s would have been (see DESIGN_REPORT discussion this was
weighed against).

A failed health check IS still pipeline-wide (llama.cpp itself being down
is an infrastructure problem, not any one exam's fault): every worker stops
pulling new pages, `OcrPipelineState` records the error via
`GET /processor/queue/progress`, and every exam still tracked at that
moment is *abandoned* (not failed — see `ResultHandler.abandon_all`) so the
next `run_pipeline` call picks them back up via step 1 instead of losing
track of them.
"""
from __future__ import annotations

import asyncio
import logging

from supabase import AsyncClient

from app.config.settings import Settings
from app.services.health_check import check_llamacpp_health
from app.services.ocr_client import OcrClient, OcrConnectivityError
from app.services.page_pool import PagePool
from app.services.preprocessing import preprocess_image
from app.services.queue_service import QueueService
from app.services.recovery import recover_processing_exams
from app.services.result_handler import ResultHandler, UnknownExamError
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
    """Drains everything currently admitted (FIFO-queued exams plus
    whatever's already buffered in the page pool), then returns — one
    "run" is one pass over whatever was enqueued at the time
    `POST /processor/ocr/start` was called, not an indefinitely-idling
    background service. New exams enqueued *during* an active run are still
    picked up (they land in the same `QueueService` `PagePool` keeps
    re-`dequeue()`ing from as it activates exams), but once it drains,
    `OcrPipelineState.running` goes back to False and the operator has to
    explicitly start another run."""
    await recover_processing_exams(db, queue, result_handler)

    # Checked synchronously, once, before any worker is allowed to touch
    # the page pool — same upfront guarantee the old per-exam check gave
    # ("never start OCR-ing anything against a dead llama.cpp"). The
    # periodic monitor below only needs to cover *while* workers are
    # running, not this first instant.
    if not await check_llamacpp_health(settings.llamacpp_base_url):
        raise RuntimeError("llama.cpp health check failed")

    healthy = asyncio.Event()
    healthy.set()
    health_monitor = asyncio.create_task(_monitor_health(settings, healthy))

    pool = PagePool(db, queue, result_handler)
    failed_exam_ids: set[str] = set()

    try:
        workers = [
            asyncio.create_task(
                _worker(pool, storage, ocr_client, result_handler, failed_exam_ids, healthy, settings)
            )
            for _ in range(max(1, settings.max_concurrent_pages))
        ]
        # return_exceptions=True + manual re-raise below, NOT a plain
        # `await asyncio.gather(*workers)` — with the default
        # return_exceptions=False, gather() propagates the FIRST worker
        # exception as soon as it happens but does NOT cancel the other
        # still-running worker tasks, which would keep dispatching pages
        # in the background — unsupervised, past the point run_pipeline
        # itself has already "returned" (raised) to its caller. Waiting
        # for every worker to actually finish first (success or fail)
        # closes that leak; only after they've all settled do we surface
        # whichever exception occurred.
        results = await asyncio.gather(*workers, return_exceptions=True)
    finally:
        health_monitor.cancel()
        try:
            await health_monitor
        except asyncio.CancelledError:
            pass

    for result in results:
        if isinstance(result, BaseException):
            raise result

    if not healthy.is_set():
        abandoned = result_handler.abandon_all()
        if abandoned:
            logger.warning(
                "OCR pipeline stopped (llama.cpp unhealthy) — abandoned in-flight exams, "
                "will be re-admitted on the next run",
                extra={"exam_ids": abandoned},
            )
        raise RuntimeError("llama.cpp health check failed")


async def _monitor_health(settings: Settings, healthy: asyncio.Event) -> None:
    """Background task: pings llama.cpp every `healthcheck_interval_seconds`
    and clears `healthy` the moment it fails. Sleeps first — `run_pipeline`
    already did the initial check synchronously before this task was even
    created, so an immediate re-check here would be redundant.

    Must never raise: `run_pipeline`'s `finally` block awaits this task to
    clean it up, and an exception escaping here (instead of a normal
    return/cancellation) would surface at that `await` and could mask
    whatever `run_pipeline` was already in the middle of reporting. If the
    health check itself is broken for some unexpected reason, the safest
    call is to treat that the same as "unhealthy" (stop every worker)
    rather than let this task die silently — a genuinely dead monitor task
    would otherwise leave `healthy` permanently set, so workers would never
    find out anything is wrong."""
    while True:
        await asyncio.sleep(settings.healthcheck_interval_seconds)
        try:
            ok = await check_llamacpp_health(settings.llamacpp_base_url)
        except Exception:  # noqa: BLE001 — see docstring
            logger.exception("health monitor: unexpected error checking llama.cpp health — treating as unhealthy")
            healthy.clear()
            return
        if not ok:
            healthy.clear()
            return


async def _worker(
    pool: PagePool,
    storage: StorageHelper,
    ocr_client: OcrClient,
    result_handler: ResultHandler,
    failed_exam_ids: set[str],
    healthy: asyncio.Event,
    settings: Settings,
) -> None:
    while healthy.is_set():
        item = await pool.pop_page()
        if item is None:
            return
        exam_id, page = item
        if exam_id in failed_exam_ids:
            continue  # a sibling page already failed this exam — discard, see module docstring

        try:
            raw = await storage.download(page.file_path)
            # preprocess_image is CPU-bound (OpenCV) — off the event loop
            # so it doesn't stall every other worker's I/O while it runs.
            pre = await asyncio.to_thread(preprocess_image, raw, settings)
            result = await ocr_client.process_image(
                pre.content, pre.origin_width, pre.origin_height, pre.input_width, pre.input_height
            )
            await result_handler.append(exam_id, page.id, result)
        except UnknownExamError:
            pass  # exam concluded (finished/failed) via another page while this one was in flight
        except OcrConnectivityError:
            # llama.cpp itself is unreachable — an infrastructure problem
            # shared by every worker, not this one page's exam's fault (see
            # ocr_client.py's OcrConnectivityError docstring). Don't fail
            # the exam over it: just signal unhealthy immediately (every
            # worker stops on its next loop check, same as the periodic
            # monitor catching it) so a single dead-server blip during a
            # burst of concurrent calls can't spuriously fail several
            # unrelated exams before the next scheduled health check would
            # have caught it. This page's result was never saved, so it's
            # naturally retried once recovery re-admits this exam.
            healthy.clear()
        except Exception as exc:  # noqa: BLE001 — any OTHER failure here is this one exam's problem, not the run's
            failed_exam_ids.add(exam_id)
            try:
                await result_handler.fail_exam(exam_id, str(exc))
            except UnknownExamError:
                pass  # another page for this exam already failed it first
