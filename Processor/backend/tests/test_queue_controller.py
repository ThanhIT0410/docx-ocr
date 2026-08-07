"""HTTP-level tests for controllers/queue.py's enqueue/dequeue routes.

Uses an isolated FastAPI app (just this router, not app.main's) with
dependency overrides for get_db/get_queue_service/require_api_key — so
this doesn't need a real Supabase project, a fully-filled-in .env, or
network access. Repository calls (exams.get_exam/update_exam,
pages.clear_ocr_text_for_exam) are mocked at the module level, same
pattern as tests/test_result_handler.py.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app.controllers import queue as queue_controller
from app.dependencies import get_db, get_queue_service
from app.schemas.models import Exam
from app.security import require_api_key
from app.services.queue_service import QueueService


def _exam(exam_id: str, status: str) -> Exam:
    return Exam.model_validate(
        {
            "id": exam_id,
            "title": f"Exam {exam_id}",
            "status": status,
            "error_message": None,
            "uploaded_at": "2026-01-01T00:00:00Z",
            "started_at": None,
            "finished_at": None,
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )


def _make_app(queue: QueueService) -> FastAPI:
    app = FastAPI()
    app.include_router(queue_controller.router)
    app.dependency_overrides[get_db] = lambda: object()  # unused directly — repo calls are mocked
    app.dependency_overrides[get_queue_service] = lambda: queue
    app.dependency_overrides[require_api_key] = lambda: None
    return app


def _post(app: FastAPI, path: str, json: dict):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(path, json=json)

    return asyncio.run(scenario())


def test_enqueue_flips_pending_exam_to_processing_in_db():
    queue = QueueService(max_size=10)
    app = _make_app(queue)

    with patch(
        "app.controllers.queue.exams.get_exam", new=AsyncMock(return_value=_exam("exam-1", "pending"))
    ), patch("app.controllers.queue.exams.update_exam", new=AsyncMock()) as mock_update:
        r = _post(app, "/processor/queue/enqueue", {"exam_ids": ["exam-1"]})

    assert r.status_code == 200
    assert r.json()["results"] == [{"exam_id": "exam-1", "queued": True, "reason": None}]
    assert queue.is_queued("exam-1") is True
    mock_update.assert_awaited_once()
    assert mock_update.await_args.args[1] == "exam-1"
    assert mock_update.await_args.args[2]["status"] == "processing"


def test_enqueue_rejects_non_pending_exam_without_db_write():
    queue = QueueService(max_size=10)
    app = _make_app(queue)

    with patch(
        "app.controllers.queue.exams.get_exam", new=AsyncMock(return_value=_exam("exam-1", "finished"))
    ), patch("app.controllers.queue.exams.update_exam", new=AsyncMock()) as mock_update:
        r = _post(app, "/processor/queue/enqueue", {"exam_ids": ["exam-1"]})

    body = r.json()
    assert body["results"][0]["queued"] is False
    assert "pending" in body["results"][0]["reason"]
    assert queue.is_queued("exam-1") is False
    mock_update.assert_not_awaited()


def test_dequeue_reverts_to_pending_and_wipes_ocr_text():
    queue = QueueService(max_size=10)
    queue.enqueue("exam-1")
    app = _make_app(queue)

    with patch("app.controllers.queue.pages.clear_ocr_text_for_exam", new=AsyncMock()) as mock_clear, patch(
        "app.controllers.queue.exams.update_exam", new=AsyncMock()
    ) as mock_update:
        r = _post(app, "/processor/queue/dequeue", {"exam_ids": ["exam-1"]})

    assert r.status_code == 200
    assert r.json()["results"] == [{"exam_id": "exam-1", "dequeued": True, "reason": None}]
    assert queue.is_queued("exam-1") is False

    mock_clear.assert_awaited_once()
    assert mock_clear.await_args.args[1] == "exam-1"

    mock_update.assert_awaited_once()
    assert mock_update.await_args.args[1] == "exam-1"
    fields = mock_update.await_args.args[2]
    assert fields["status"] == "pending"


def test_dequeue_exam_not_in_queue_is_reported_without_touching_db():
    queue = QueueService(max_size=10)
    app = _make_app(queue)

    with patch("app.controllers.queue.pages.clear_ocr_text_for_exam", new=AsyncMock()) as mock_clear, patch(
        "app.controllers.queue.exams.update_exam", new=AsyncMock()
    ) as mock_update:
        r = _post(app, "/processor/queue/dequeue", {"exam_ids": ["not-queued"]})

    body = r.json()
    assert body["results"][0]["dequeued"] is False
    mock_clear.assert_not_awaited()
    mock_update.assert_not_awaited()
