"""Unit tests for recover_processing_exams — exams.list_exams is mocked
(no real Supabase project needed); QueueService is real."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.schemas.models import Exam
from app.services.queue_service import QueueService
from app.services.recovery import recover_processing_exams


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


def test_recovers_all_processing_exams_into_queue():
    queue = QueueService(max_size=10)
    with patch(
        "app.services.recovery.exams.list_exams",
        new=AsyncMock(return_value=[_exam("exam-1"), _exam("exam-2")]),
    ):
        recovered = _run(recover_processing_exams(db=object(), queue=queue))

    assert recovered == 2
    assert queue.is_queued("exam-1") is True
    assert queue.is_queued("exam-2") is True


def test_no_processing_exams_recovers_nothing():
    queue = QueueService(max_size=10)
    with patch("app.services.recovery.exams.list_exams", new=AsyncMock(return_value=[])):
        recovered = _run(recover_processing_exams(db=object(), queue=queue))

    assert recovered == 0
    assert queue.qsize() == 0


def test_queue_full_during_recovery_skips_without_crashing():
    queue = QueueService(max_size=1)
    with patch(
        "app.services.recovery.exams.list_exams",
        new=AsyncMock(return_value=[_exam("exam-1"), _exam("exam-2")]),
    ):
        recovered = _run(recover_processing_exams(db=object(), queue=queue))

    assert recovered == 1
    assert queue.qsize() == 1
