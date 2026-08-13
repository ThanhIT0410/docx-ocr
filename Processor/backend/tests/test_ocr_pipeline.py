"""Unit tests for the OCR pipeline — storage/ocr_client/repository calls
are all mocked, no real Supabase/llama.cpp involved. Exercises `run_pipeline`
as a whole (the worker-pool + PagePool + ResultHandler wiring) rather than a
single-exam helper — there is no more `_process_exam`; concurrency now spans
exams, not just pages within one, so these tests care about cross-exam
behavior (isolation on failure, interleaving) that couldn't exist before."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.settings import get_settings
from app.schemas.models import Exam, OcrPageResult, Page
from app.services.ocr_client import OcrConnectivityError
from app.services.ocr_pipeline import OcrPipelineState, run_pipeline
from app.services.preprocessing import PreprocessResult
from app.services.queue_service import QueueService
from app.services.result_handler import ResultHandler

FAKE_RESULT = OcrPageResult(origin_width=1, origin_height=1, input_width=1, input_height=1, layouts=[])
FAKE_PRE = PreprocessResult(content=b"x", origin_width=1, origin_height=1, input_width=1, input_height=1)


def _run(coro, timeout=5):
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


def _exam(exam_id: str) -> Exam:
    return Exam.model_validate(
        {
            "id": exam_id,
            "title": f"Exam {exam_id}",
            "status": "processing",
            "error_message": None,
            "uploaded_at": "2026-01-01T00:00:00Z",
            "started_at": None,
            "finished_at": None,
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )


def _page(page_id: str, exam_id: str, order: int) -> Page:
    return Page.model_validate(
        {
            "id": page_id,
            "exam_id": exam_id,
            "page_order": order,
            "file_path": f"{exam_id}/{page_id}.jpg",
            "ocr_text": None,
        }
    )


def _settings(**overrides):
    return get_settings().model_copy(update=overrides)


def _no_recovery():
    """Patches recovery's DB call so `run_pipeline`'s startup orphan-scan
    finds nothing — every test here manages its own exams explicitly."""
    return patch("app.services.recovery.exams.list_exams", new=AsyncMock(return_value=[]))


def test_run_pipeline_processes_pages_across_multiple_exams_and_finishes_both():
    settings = _settings(max_concurrent_pages=2, healthcheck_interval_seconds=999)
    queue = QueueService(max_size=100)
    result_handler = ResultHandler(db=object(), queue=queue)
    queue.enqueue("exam-1", 2)
    queue.enqueue("exam-2", 1)

    exam_pages = {
        "exam-1": [_page("p1", "exam-1", 1), _page("p2", "exam-1", 2)],
        "exam-2": [_page("p3", "exam-2", 1)],
    }

    storage = MagicMock()
    storage.download = AsyncMock(return_value=b"raw")
    ocr_client = MagicMock()
    ocr_client.process_image = AsyncMock(return_value=FAKE_RESULT)

    async def fake_get_exam(db, exam_id):
        return _exam(exam_id)

    async def fake_list_pages(db, exam_id):
        return exam_pages[exam_id]

    with _no_recovery(), patch("app.services.ocr_pipeline.check_llamacpp_health", new=AsyncMock(return_value=True)), patch(
        "app.services.page_pool.exams.get_exam", new=fake_get_exam
    ), patch("app.services.page_pool.pages.list_pages", new=fake_list_pages), patch(
        "app.services.ocr_pipeline.preprocess_image", return_value=FAKE_PRE
    ), patch(
        "app.services.result_handler.pages.save_ocr_text", new=AsyncMock()
    ) as mock_save, patch(
        "app.services.result_handler.exams.update_exam", new=AsyncMock()
    ) as mock_update:
        _run(run_pipeline(object(), storage, ocr_client, queue, result_handler, settings))

    assert mock_save.await_count == 3
    statuses = {call.args[1]: call.args[2]["status"] for call in mock_update.await_args_list}
    assert statuses == {"exam-1": "finished", "exam-2": "finished"}
    assert queue.admitted_pages == 0
    assert result_handler.list_progress() == []


def test_one_exam_failing_does_not_stop_or_fail_a_concurrently_processing_exam():
    """The core behavior change from the old sequential design: fail-fast
    is now exam-scoped. exam-1's one bad page must not touch exam-2 at all."""
    settings = _settings(max_concurrent_pages=2, healthcheck_interval_seconds=999)
    queue = QueueService(max_size=100)
    result_handler = ResultHandler(db=object(), queue=queue)
    queue.enqueue("exam-1", 1)
    queue.enqueue("exam-2", 2)

    exam_pages = {
        "exam-1": [_page("p1", "exam-1", 1)],
        "exam-2": [_page("p2", "exam-2", 1), _page("p3", "exam-2", 2)],
    }

    storage = MagicMock()

    async def fake_download(file_path: str) -> bytes:
        if "exam-1" in file_path:
            raise RuntimeError("boom")
        return b"raw"

    storage.download = AsyncMock(side_effect=fake_download)
    ocr_client = MagicMock()
    ocr_client.process_image = AsyncMock(return_value=FAKE_RESULT)

    async def fake_get_exam(db, exam_id):
        return _exam(exam_id)

    async def fake_list_pages(db, exam_id):
        return exam_pages[exam_id]

    with _no_recovery(), patch("app.services.ocr_pipeline.check_llamacpp_health", new=AsyncMock(return_value=True)), patch(
        "app.services.page_pool.exams.get_exam", new=fake_get_exam
    ), patch("app.services.page_pool.pages.list_pages", new=fake_list_pages), patch(
        "app.services.ocr_pipeline.preprocess_image", return_value=FAKE_PRE
    ), patch(
        "app.services.result_handler.pages.save_ocr_text", new=AsyncMock()
    ), patch(
        "app.services.result_handler.exams.update_exam", new=AsyncMock()
    ) as mock_update:
        _run(run_pipeline(object(), storage, ocr_client, queue, result_handler, settings))

    fields_by_exam = {call.args[1]: call.args[2] for call in mock_update.await_args_list}
    assert fields_by_exam["exam-1"]["status"] == "failed"
    assert "boom" in fields_by_exam["exam-1"]["error_message"]
    assert fields_by_exam["exam-2"]["status"] == "finished"
    assert queue.admitted_pages == 0


def test_ocr_connectivity_error_stops_the_run_without_failing_the_exam():
    """OcrConnectivityError means llama.cpp itself is unreachable — a
    shared infrastructure problem, not this exam's fault. Unlike a normal
    per-page exception, it must NOT call fail_exam (the exam stays
    'processing', its budget stays held, and it's picked back up by
    recovery on the next run) — it should behave like a health-check
    failure instead."""
    settings = _settings(max_concurrent_pages=1, healthcheck_interval_seconds=999)
    queue = QueueService(max_size=100)
    result_handler = ResultHandler(db=object(), queue=queue)
    queue.enqueue("exam-1", 1)

    exam_pages = {"exam-1": [_page("p1", "exam-1", 1)]}

    storage = MagicMock()
    storage.download = AsyncMock(return_value=b"raw")
    ocr_client = MagicMock()
    ocr_client.process_image = AsyncMock(side_effect=OcrConnectivityError("llama.cpp unreachable"))

    async def fake_get_exam(db, exam_id):
        return _exam(exam_id)

    async def fake_list_pages(db, exam_id):
        return exam_pages[exam_id]

    with _no_recovery(), patch("app.services.ocr_pipeline.check_llamacpp_health", new=AsyncMock(return_value=True)), patch(
        "app.services.page_pool.exams.get_exam", new=fake_get_exam
    ), patch("app.services.page_pool.pages.list_pages", new=fake_list_pages), patch(
        "app.services.ocr_pipeline.preprocess_image", return_value=FAKE_PRE
    ), patch(
        "app.services.result_handler.exams.update_exam", new=AsyncMock()
    ) as mock_update:
        with pytest.raises(RuntimeError, match="health check"):
            _run(run_pipeline(object(), storage, ocr_client, queue, result_handler, settings))

    mock_update.assert_not_awaited()  # exam-1 never marked failed
    assert result_handler.is_tracking("exam-1") is False  # abandoned instead
    assert queue.admitted_pages == 1  # budget still held


def test_run_pipeline_calls_recovery_before_processing():
    settings = _settings(max_concurrent_pages=1, healthcheck_interval_seconds=999)
    queue = QueueService(max_size=100)
    result_handler = ResultHandler(db=object(), queue=queue)
    db = object()

    with patch("app.services.ocr_pipeline.check_llamacpp_health", new=AsyncMock(return_value=True)), patch(
        "app.services.ocr_pipeline.recover_processing_exams", new=AsyncMock(return_value=0)
    ) as mock_recover:
        _run(run_pipeline(db, MagicMock(), MagicMock(), queue, result_handler, settings))

    mock_recover.assert_awaited_once_with(db, queue, result_handler)


def test_run_pipeline_stops_immediately_on_health_check_failure():
    settings = _settings(max_concurrent_pages=2, healthcheck_interval_seconds=999)
    queue = QueueService(max_size=100)
    result_handler = ResultHandler(db=object(), queue=queue)

    with _no_recovery(), patch("app.services.ocr_pipeline.check_llamacpp_health", new=AsyncMock(return_value=False)):
        with pytest.raises(RuntimeError, match="health check"):
            _run(run_pipeline(object(), MagicMock(), MagicMock(), queue, result_handler, settings))


def test_run_pipeline_returns_quickly_when_queue_starts_empty():
    """A "run" drains whatever's queued right now and stops — must not
    idle forever waiting for more work to show up."""
    settings = _settings(max_concurrent_pages=4, healthcheck_interval_seconds=999)
    queue = QueueService(max_size=100)
    result_handler = ResultHandler(db=object(), queue=queue)

    with _no_recovery(), patch("app.services.ocr_pipeline.check_llamacpp_health", new=AsyncMock(return_value=True)):
        _run(run_pipeline(object(), MagicMock(), MagicMock(), queue, result_handler, settings), timeout=2)

    assert result_handler.list_progress() == []


def test_health_failure_mid_run_abandons_tracked_exam_without_releasing_its_budget():
    """A page-level failure releases budget (see test above); an
    infrastructure-level failure must NOT — the exam is still legitimately
    'processing', just orphaned until the next run's recovery pass picks it
    back up (see app/services/recovery.py)."""
    # Two pages, one worker: the first page is left to finish naturally
    # even after health goes down (same "let in-flight work complete"
    # philosophy as a per-exam failure) — it's the SECOND page, never even
    # dispatched, that's what makes this exam actually abandoned (still
    # tracked, incomplete) rather than legitimately finished.
    settings = _settings(max_concurrent_pages=1, healthcheck_interval_seconds=0.02)
    queue = QueueService(max_size=100)
    result_handler = ResultHandler(db=object(), queue=queue)
    queue.enqueue("exam-1", 2)

    exam_pages = {"exam-1": [_page("p1", "exam-1", 1), _page("p2", "exam-1", 2)]}

    health_calls = 0

    async def fake_health(base_url):
        nonlocal health_calls
        health_calls += 1
        return health_calls == 1  # the synchronous upfront check passes, the monitor's first check fails

    download_started = asyncio.Event()
    keep_blocking = asyncio.Event()

    async def slow_download(file_path: str) -> bytes:
        download_started.set()
        await keep_blocking.wait()
        return b"raw"

    storage = MagicMock()
    storage.download = AsyncMock(side_effect=slow_download)
    ocr_client = MagicMock()
    ocr_client.process_image = AsyncMock(return_value=FAKE_RESULT)

    async def fake_get_exam(db, exam_id):
        return _exam(exam_id)

    async def fake_list_pages(db, exam_id):
        return exam_pages[exam_id]

    async def scenario():
        with _no_recovery(), patch("app.services.ocr_pipeline.check_llamacpp_health", new=fake_health), patch(
            "app.services.page_pool.exams.get_exam", new=fake_get_exam
        ), patch("app.services.page_pool.pages.list_pages", new=fake_list_pages), patch(
            "app.services.ocr_pipeline.preprocess_image", return_value=FAKE_PRE
        ), patch(
            "app.services.result_handler.pages.save_ocr_text", new=AsyncMock()
        ), patch(
            "app.services.result_handler.exams.update_exam", new=AsyncMock()
        ):
            task = asyncio.create_task(run_pipeline(object(), storage, ocr_client, queue, result_handler, settings))
            await asyncio.wait_for(download_started.wait(), timeout=2)
            await asyncio.sleep(0.1)  # let the health monitor's periodic check fire and fail
            keep_blocking.set()
            with pytest.raises(RuntimeError, match="health check"):
                await asyncio.wait_for(task, timeout=2)

    _run(scenario())

    assert result_handler.is_tracking("exam-1") is False  # abandoned, not left dangling
    assert queue.admitted_pages == 2  # budget still held — NOT released


def test_pipeline_state_start_is_idempotent_while_running():
    state = OcrPipelineState()

    async def never_ending() -> None:
        await asyncio.sleep(10)

    async def scenario():
        first = state.start(never_ending())
        second = state.start(never_ending())  # must not raise "never awaited" and must be a no-op
        return first, second

    first, second = _run(scenario())
    assert (first, second) == (True, False)


def test_pipeline_state_captures_error_and_clears_running_flag():
    state = OcrPipelineState()

    async def failing() -> None:
        raise RuntimeError("pipeline exploded")

    async def scenario():
        state.start(failing())
        for _ in range(200):
            if not state.running:
                break
            await asyncio.sleep(0.01)
        return state.running, state.error

    running, error = _run(scenario())
    assert running is False
    assert error == "pipeline exploded"
