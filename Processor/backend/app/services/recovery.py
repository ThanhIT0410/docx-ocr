"""Startup recovery: if the process was closed mid-OCR, any exam left at
`status='processing'` in the DB is an orphan — some of its pages may
already have `ocr_text` (see repositories/pages.py), but nothing is
tracking it anymore since app/services/queue_service.py's queue and
app/services/result_handler.py's tracking are both in-memory and reset on
restart. Called once from app/main.py's lifespan, before the app starts
serving requests, to put every such exam back into the queue exactly as
it was — so whatever eventually dequeues it resumes via
`ResultHandler.start_exam`'s `existing_results` instead of starting over.

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
from app.repositories import exams
from app.services.queue_service import ExamAlreadyQueuedError, QueueFullError, QueueService

logger = logging.getLogger(__name__)


async def recover_processing_exams(db: AsyncClient, queue: QueueService) -> int:
    """Returns how many exams were recovered, for a startup log line."""
    orphaned = await exams.list_exams(db, STATUS_PROCESSING)
    recovered = 0
    for exam in orphaned:
        try:
            queue.enqueue(exam.id)
        except QueueFullError:
            # Shouldn't happen under normal operation (queue's max_size is
            # what bounded how many could become 'processing' in the first
            # place) — but config can change between restarts, so log and
            # keep going rather than crash startup over it.
            logger.warning("recovery: queue full, exam left un-recovered", extra={"exam_id": exam.id})
            continue
        except ExamAlreadyQueuedError:
            # Can't actually happen — the queue is freshly constructed
            # right before this runs — but guard anyway rather than trust it.
            continue
        recovered += 1

    if recovered:
        logger.info("recovery: requeued exams left 'processing' from a previous run", extra={"count": recovered})

    return recovered
