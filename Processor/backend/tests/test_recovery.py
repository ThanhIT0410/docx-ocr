"""Unit tests for recover_processing_exams — exams.list_exams/pages.count_total
are mocked (no real Supabase project needed); QueueService/ResultHandler are
real."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.schemas.models import Exam
from app.services.queue_service import QueueService
from app.services.recovery import recover_processing_exams
from app.services.result_handler import ResultHandler


def _exam(exam_id: str) -> Exam:
    return Exam.model_validate(
        {
            "id": exam_id,
            "title": f"Exam {exam_id}",
            "status": "processing",
            "error_message": None,
            "uploaded_at": "2026-01-01T00:00:00Z",
            "started_at": "2026-01-01T00:01:00Z",
            "finished_at": None,
            "updated_at": "2026-01-01T00:01:00Z",
        }
    )


def _run(coro):
    return asyncio.run(coro)


def _patched(exam_list, page_count=2):
    return patch("app.services.recovery.exams.list_exams", new=AsyncMock(return_value=exam_list)), patch(
        "app.services.recovery.pages.count_total", new=AsyncMock(return_value=page_count)
    )


def test_recovers_all_processing_exams_into_queue():
    queue = QueueService(max_size=10)
    p1, p2 = _patched([_exam("exam-1"), _exam("exam-2")], page_count=3)
    with p1, p2:
        recovered = _run(recover_processing_exams(db=object(), queue=queue))

    assert recovered == 2
    assert queue.is_queued("exam-1") is True
    assert queue.is_queued("exam-2") is True
    # readmit() deliberately does NOT charge admitted_pages — see
    # QueueService.readmit's docstring (this is the process-restart case,
    # where the fresh ledger never counted these to begin with).
    assert queue.admitted_pages == 0


def test_no_processing_exams_recovers_nothing():
    queue = QueueService(max_size=10)
    p1, p2 = _patched([])
    with p1, p2:
        recovered = _run(recover_processing_exams(db=object(), queue=queue))

    assert recovered == 0
    assert queue.qsize() == 0


def test_recovery_is_not_blocked_by_a_full_ledger():
    """readmit() (not enqueue()) is used for recovery specifically so a
    ledger that's already "full" (or under-counting, post-restart — see
    QueueService.readmit's docstring) never refuses to recover an orphan
    that's already really 'processing' in the DB."""
    queue = QueueService(max_size=1)
    queue.enqueue("already-admitted", 1)  # ledger genuinely full
    p1, p2 = _patched([_exam("exam-1"), _exam("exam-2")], page_count=5)
    with p1, p2:
        recovered = _run(recover_processing_exams(db=object(), queue=queue))

    assert recovered == 2
    assert queue.is_queued("exam-1") is True
    assert queue.is_queued("exam-2") is True


def test_recovery_does_not_double_charge_admitted_pages():
    """The core bug this is guarding against: an exam recovered mid-process
    (its budget already charged once, never released) must NOT get charged
    again just because it's being re-admitted into the FIFO."""
    queue = QueueService(max_size=10)
    queue.enqueue("exam-1", 5)
    queue.dequeue()  # PagePool "activated" it — budget stays held, exam-1 no longer in the FIFO
    assert queue.admitted_pages == 5

    p1, p2 = _patched([_exam("exam-1")], page_count=5)
    with p1, p2:
        recovered = _run(recover_processing_exams(db=object(), queue=queue))

    assert recovered == 1
    assert queue.admitted_pages == 5  # unchanged — not double-charged


def test_exam_already_in_fifo_is_skipped():
    """A previous recovery pass (or a currently-running one) may already
    have this exam queued — must not double-admit it (would double-count
    its page budget)."""
    queue = QueueService(max_size=10)
    queue.enqueue("exam-1", 5)
    p1, p2 = _patched([_exam("exam-1")], page_count=5)
    with p1, p2:
        recovered = _run(recover_processing_exams(db=object(), queue=queue))

    assert recovered == 0
    assert queue.admitted_pages == 5  # not double-counted


def test_exam_already_tracked_by_result_handler_is_skipped():
    """The case run_pipeline's re-run of this (not just app startup) exists
    for: an exam mid-flight from a run that hasn't aborted is NOT an
    orphan, even though it's not sitting in the FIFO anymore (PagePool
    already activated it) — recovery must leave it alone."""
    queue = QueueService(max_size=10)
    handler = ResultHandler(db=object(), queue=queue)
    queue.enqueue("exam-1", 4)
    queue.dequeue()
    handler.start_exam("exam-1", "Exam One", total_pages=4)

    p1, p2 = _patched([_exam("exam-1")], page_count=4)
    with p1, p2:
        recovered = _run(recover_processing_exams(db=object(), queue=queue, result_handler=handler))

    assert recovered == 0
    assert queue.is_queued("exam-1") is False  # still not re-added to the FIFO
