"""HTTP-level tests for controllers/ocr.py's POST /processor/ocr/start.

Isolated FastAPI app + dependency overrides, same pattern as
test_queue_controller.py — no real Supabase/llama.cpp needed. The pipeline
coroutine itself is never actually awaited to completion here (state.start
just schedules it as a background task); these tests only care about the
route's own decision (409 vs starting it), not what the task does after.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app.controllers import ocr as ocr_controller
from app.dependencies import (
    get_db,
    get_ocr_client,
    get_ocr_pipeline_state,
    get_queue_service,
    get_result_handler,
    get_storage,
)
from app.security import require_api_key
from app.services.ocr_pipeline import OcrPipelineState
from app.services.queue_service import QueueService
from app.services.result_handler import ResultHandler


def _make_app(queue: QueueService, state: OcrPipelineState) -> FastAPI:
    app = FastAPI()
    app.include_router(ocr_controller.router)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_storage] = lambda: object()
    app.dependency_overrides[get_ocr_client] = lambda: object()
    app.dependency_overrides[get_queue_service] = lambda: queue
    app.dependency_overrides[get_result_handler] = lambda: ResultHandler(db=object())
    app.dependency_overrides[get_ocr_pipeline_state] = lambda: state
    app.dependency_overrides[require_api_key] = lambda: None
    return app


def _post_start(app: FastAPI, before_request=None):
    async def scenario():
        if before_request is not None:
            await before_request()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/processor/ocr/start")

    return asyncio.run(scenario())


def test_start_with_empty_queue_and_not_running_returns_409():
    app = _make_app(QueueService(max_size=10), OcrPipelineState())

    r = _post_start(app)

    assert r.status_code == 409
    assert "trống" in r.json()["detail"]


def test_start_with_queued_exam_starts_the_pipeline():
    queue = QueueService(max_size=10)
    queue.enqueue("exam-1")
    state = OcrPipelineState()

    # run_pipeline itself is patched out — this test only checks the route
    # actually calls state.start (i.e. doesn't 409) when there's work queued.
    with patch("app.controllers.ocr.run_pipeline", new=AsyncMock(return_value=None)):
        r = _post_start(_make_app(queue, state))

    assert r.status_code == 200
    body = r.json()
    assert body == {"started": True, "already_running": False}


def test_start_while_already_running_is_a_no_op_even_with_empty_queue():
    """Idempotent-start behavior must survive the new empty-queue guard —
    calling /start again while a run is already in flight (even if it has
    since drained the queue down to zero) must still just report
    already_running, not 409."""
    state = OcrPipelineState()

    async def never_ending() -> None:
        await asyncio.sleep(10)

    async def start_it():
        state.start(never_ending())  # needs a running loop for asyncio.create_task

    r = _post_start(_make_app(QueueService(max_size=10), state), before_request=start_it)

    assert r.status_code == 200
    assert r.json() == {"started": False, "already_running": True}
