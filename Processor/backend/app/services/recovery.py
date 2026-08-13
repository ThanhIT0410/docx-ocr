"""Orphan recovery: re-admits any exam sitting at `status='processing'`
that nothing is currently tracking, back into `QueueService`'s FIFO.

Two distinct situations produce this state, and both are handled by the
same pass:
1. **Process restart** — the process was closed mid-OCR; some pages may
   already have `ocr_text` (see repositories/pages.py), but nothing is
   tracking any of it anymore since `QueueService`/`ResultHandler` are both
   in-memory and reset on restart. Called once from app/main.py's lifespan,
   before the app starts serving requests.
2. **A previous `run_pipeline` call aborted mid-flight** (llama.cpp health
   check failed while several exams were concurrently active — see
   `app/services/ocr_pipeline.py`'s `ResultHandler.abandon_all`) — those
   exams are still `status='processing'` in the DB and still legitimately
   admitted (their page budget was never released), but the `PagePool`
   that was feeding them workers is gone along with that failed run.
   `run_pipeline` calls this function again at the start of every run,
   not just once at startup, specifically to catch this case too.

`is_queued`/`is_tracking` checks distinguish "genuinely orphaned" from
"already accounted for" (still sitting in the FIFO, or an exam from THIS
same run that's mid-flight for a legitimate reason unrelated to recovery)
— without them, calling this at the start of every run would try to
re-admit exams that are already being worked on, which would blow up
`ResultHandler.start_exam`'s `ExamAlreadyTrackedError` once `PagePool`
activated them a second time.

Goes straight to `QueueService.enqueue` (the raw method), not through
controllers/queue.py's business rules — those require `status='pending'`
before admitting an exam, which is wrong here: these exams are already
'processing' on purpose, being put back exactly where they were, not
newly admitted.
"""
from __future__ import annotations

import logging

from supabase import AsyncClient

from app.constants import STATUS_PROCESSING
from app.repositories import exams, pages
from app.services.queue_service import ExamAlreadyQueuedError, QueueService
from app.services.result_handler import ResultHandler

logger = logging.getLogger(__name__)


async def recover_processing_exams(
    db: AsyncClient, queue: QueueService, result_handler: ResultHandler | None = None
) -> int:
    """Returns how many exams were recovered, for a log line.

    `result_handler` is optional only because app/main.py's startup call
    passes a freshly-constructed one that can't possibly be tracking
    anything yet — every other caller (`ocr_pipeline.py`) must pass the
    live one so already-active exams aren't double-admitted."""
    orphaned = await exams.list_exams(db, STATUS_PROCESSING)
    recovered = 0
    for exam in orphaned:
        if queue.is_queued(exam.id):
            continue
        if result_handler is not None and result_handler.is_tracking(exam.id):
            continue

        page_count = await pages.count_total(db, exam.id)
        try:
            # readmit, not enqueue — this exam's budget was already
            # charged once (or, after a restart, never will be in this
            # fresh ledger either way) — see QueueService.readmit's
            # docstring for why charging again here would be a bug, not
            # just redundant.
            queue.readmit(exam.id, page_count)
        except ExamAlreadyQueuedError:
            # Guarded against by the is_queued() check above already —
            # kept as a defensive fallback, not expected to trigger.
            continue
        recovered += 1

    if recovered:
        logger.info("recovery: requeued exams left 'processing' with nothing tracking them", extra={"count": recovered})

    return recovered
