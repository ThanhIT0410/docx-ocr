"""The page-tier work-pull primitive `app/services/ocr_pipeline.py`'s
worker pool consumes — `pop_page()` hands out one page at a time, spanning
however many exams are currently admitted, instead of draining one exam's
pages to zero before touching the next (see `queue_service.py`'s module
docstring for the exam-tier/page-tier split this implements).

`pop_page()` is called concurrently by every worker coroutine
(`settings.max_concurrent_pages` of them). When the internal buffer of
not-yet-dispatched pages runs dry, whichever worker(s) notice first
activate the next exam(s) off `QueueService`'s FIFO — fetching that exam's
page list from the DB, registering it with `ResultHandler`, and pushing its
remaining pages into the buffer. Multiple workers hitting empty at once
each activate a *different* exam (there's no risk of two workers both
activating the same one — `QueueService.dequeue()` is synchronous, no
`await` in between, so each call pops a distinct exam_id), so a cold-start
burst parallelizes its DB fetches instead of serializing them — the natural
self-limiting bound on "how many exams get activated at once" is just the
worker count, no separate lookahead threshold needed.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque

from supabase import AsyncClient

from app.repositories import exams, pages
from app.schemas.models import Page
from app.services.queue_service import QueueEmptyError, QueueService
from app.services.result_handler import ResultHandler

logger = logging.getLogger(__name__)


class PagePool:
    def __init__(self, db: AsyncClient, queue: QueueService, result_handler: ResultHandler):
        self._db = db
        self._queue = queue
        self._result_handler = result_handler
        self._undispatched: deque[tuple[str, Page]] = deque()
        self._exhausted = False  # QueueService's FIFO has run dry
        self._pending_activations = 0
        self._lock = asyncio.Lock()
        self._cv = asyncio.Condition(self._lock)

    async def pop_page(self) -> tuple[str, Page] | None:
        """Returns `(exam_id, page)` for the next page to process, or
        `None` once every admitted exam's pages have all been handed out —
        the signal a worker uses to stop looping (see ocr_pipeline.py)."""
        while True:
            exam_to_activate: tuple[str, int] | None = None
            async with self._lock:
                if self._undispatched:
                    return self._undispatched.popleft()

                if self._exhausted:
                    if self._pending_activations == 0:
                        return None
                    # FIFO is dry, but another worker is still fetching an
                    # exam it already claimed — that fetch may still yield
                    # pages, so wait rather than returning None early (that
                    # would make this worker quit while work is still on
                    # its way in). Re-checks everything from scratch on
                    # wake, so a spurious wakeup is harmless.
                    await self._cv.wait()
                    continue

                try:
                    exam_to_activate = self._queue.dequeue()
                except QueueEmptyError:
                    self._exhausted = True
                    continue

                self._pending_activations += 1

            exam_id, page_count = exam_to_activate
            try:
                await self._activate(exam_id, page_count)
            finally:
                async with self._lock:
                    self._pending_activations -= 1
                    self._cv.notify_all()

    async def _activate(self, exam_id: str, page_count: int) -> None:
        """Pulls one exam's remaining pages into `_undispatched`. Runs
        outside `_lock` (only the final buffer push is guarded) so the DB
        round-trips here don't serialize other workers doing the same for
        a different exam."""
        exam = await exams.get_exam(self._db, exam_id)
        if exam is None:
            # Deleted (e.g. admin/reset) between enqueue and now — nothing
            # to process, nothing to mark failed (the row is gone). Free
            # its budget and let the caller's loop try the next exam.
            self._queue.release(exam_id)
            logger.warning("pop_page: exam vanished before its pages could be activated", extra={"exam_id": exam_id})
            return

        all_pages = await pages.list_pages(self._db, exam_id)
        existing_results = {p.id: p.ocr_text for p in all_pages if p.ocr_text is not None}
        remaining = [p for p in all_pages if p.ocr_text is None]

        self._result_handler.start_exam(exam_id, exam.title, total_pages=len(all_pages), existing_results=existing_results)
        if await self._result_handler.finalize_if_complete(exam_id):
            return  # every page was already done (fully resumed) — nothing to dispatch

        async with self._lock:
            self._undispatched.extend((exam_id, p) for p in remaining)
