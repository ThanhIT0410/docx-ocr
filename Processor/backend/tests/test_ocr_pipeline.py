"""Unit tests for the OCR pipeline — storage/ocr_client/repository calls
are all mocked, no real Supabase/llama.cpp involved."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.settings import get_settings
from app.schemas.models import Exam, OcrPageResult, Page
from app.services.ocr_pipeline import OcrPipelineState, _process_exam, run_pipeline
from app.services.preprocessing import PreprocessResult
from app.services.queue_service import QueueService
from app.services.result_handler import ResultHandler

FAKE_RESULT = OcrPageResult(origin_width=1, origin_height=1, input_width=1, input_height=1, layouts=[])
FAKE_PRE = PreprocessResult(content=b"x", origin_width=1, origin_height=1, input_width=1, input_height=1)


def _run(coro):
    return asyncio.run(coro)


def _exam(exam_id: str) -> Exam:
    return Exam.model_validate(
        {
            "id": exam_id,
            "title": "Exam",
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


def test_process_exam_happy_path_processes_all_pages_and_finishes():
    settings = get_settings()
    exam_pages = [_page("p1", "exam-1", 1), _page("p2", "exam-1", 2), _page("p3", "exam-1", 3)]
    result_handler = ResultHandler(db=object())

    storage = MagicMock()
    storage.download = AsyncMock(return_value=b"raw")
    ocr_client = MagicMock()
    ocr_client.process_image = AsyncMock(return_value=FAKE_RESULT)

    with patch("app.services.ocr_pipeline.exams.get_exam", new=AsyncMock(return_value=_exam("exam-1"))), patch(
        "app.services.ocr_pipeline.pages.list_pages", new=AsyncMock(return_value=exam_pages)
    ), patch("app.services.ocr_pipeline.preprocess_image", return_value=FAKE_PRE), patch(
        "app.services.result_handler.pages.save_ocr_text", new=AsyncMock()
    ) as mock_save, patch(
        "app.services.result_handler.exams.update_exam", new=AsyncMock()
    ) as mock_finish:
        _run(_process_exam(object(), storage, ocr_client, result_handler, "exam-1", settings))

    assert storage.download.await_count == 3
    assert ocr_client.process_image.await_count == 3
    assert mock_save.await_count == 3
    mock_finish.assert_awaited_once()
    assert mock_finish.await_args.args[2]["status"] == "finished"
    assert result_handler.is_tracking("exam-1") is False


def test_process_exam_already_fully_resumed_skips_processing():
    """All pages already have ocr_text (crash-recovery case) — must
    finalize straight away without touching storage/ocr_client at all."""
    settings = get_settings()
    exam_pages = [Page.model_validate({**_page("p1", "exam-1", 1).model_dump(), "ocr_text": FAKE_RESULT})]
    result_handler = ResultHandler(db=object())

    storage = MagicMock()
    storage.download = AsyncMock()
    ocr_client = MagicMock()
    ocr_client.process_image = AsyncMock()

    with patch("app.services.ocr_pipeline.exams.get_exam", new=AsyncMock(return_value=_exam("exam-1"))), patch(
        "app.services.ocr_pipeline.pages.list_pages", new=AsyncMock(return_value=exam_pages)
    ), patch("app.services.result_handler.exams.update_exam", new=AsyncMock()) as mock_finish:
        _run(_process_exam(object(), storage, ocr_client, result_handler, "exam-1", settings))

    storage.download.assert_not_awaited()
    ocr_client.process_image.assert_not_awaited()
    mock_finish.assert_awaited_once()


def test_process_exam_one_page_failing_cancels_siblings_and_marks_failed():
    settings = get_settings()
    exam_pages = [_page("p1", "exam-1", 1), _page("p2", "exam-1", 2), _page("p3", "exam-1", 3)]
    result_handler = ResultHandler(db=object())

    async def download(file_path: str) -> bytes:
        if "p2" in file_path:
            raise RuntimeError("boom")
        await asyncio.sleep(5)  # would only complete if NOT cancelled in time
        return b"raw"

    storage = MagicMock()
    storage.download = AsyncMock(side_effect=download)
    ocr_client = MagicMock()
    ocr_client.process_image = AsyncMock(return_value=FAKE_RESULT)

    with patch("app.services.ocr_pipeline.exams.get_exam", new=AsyncMock(return_value=_exam("exam-1"))), patch(
        "app.services.ocr_pipeline.pages.list_pages", new=AsyncMock(return_value=exam_pages)
    ), patch("app.services.ocr_pipeline.exams.update_exam", new=AsyncMock()) as mock_mark_failed, patch(
        "app.services.result_handler.pages.save_ocr_text", new=AsyncMock()
    ) as mock_save:
        with pytest.raises(RuntimeError, match="boom"):
            _run(_process_exam(object(), storage, ocr_client, result_handler, "exam-1", settings))

    mock_mark_failed.assert_awaited_once()
    fields = mock_mark_failed.await_args.args[2]
    assert fields["status"] == "failed"
    assert "boom" in fields["error_message"]
    # p1/p3 were cancelled mid-sleep — neither should have reached append().
    mock_save.assert_not_awaited()


def test_run_pipeline_stops_immediately_on_health_check_failure():
    settings = get_settings()
    with patch("app.services.ocr_pipeline.check_llamacpp_health", new=AsyncMock(return_value=False)):
        with pytest.raises(RuntimeError, match="health check"):
            _run(
                run_pipeline(
                    object(), MagicMock(), MagicMock(), QueueService(max_size=10), ResultHandler(object()), settings
                )
            )


def test_run_pipeline_returns_immediately_when_queue_starts_empty():
    """A "run" drains whatever's queued right now and stops — it must NOT
    sleep-and-retry waiting for more work to show up (that would mean
    `POST /processor/ocr/start` never really finishes on its own, which is
    exactly the "always shows running" behavior this was changed to avoid;
    see the conversation this was changed in)."""
    settings = get_settings()
    with patch("app.services.ocr_pipeline.check_llamacpp_health", new=AsyncMock(return_value=True)), patch(
        "app.services.ocr_pipeline.asyncio.sleep", new=AsyncMock(side_effect=AssertionError("must not sleep"))
    ):
        _run(run_pipeline(object(), MagicMock(), MagicMock(), QueueService(max_size=10), ResultHandler(object()), settings))


def test_run_pipeline_drains_everything_queued_then_stops():
    settings = get_settings()
    queue = QueueService(max_size=10)
    queue.enqueue("exam-1")
    queue.enqueue("exam-2")
    processed: list[str] = []

    async def fake_process_exam(db, storage, ocr_client, result_handler, exam_id, settings):
        processed.append(exam_id)

    with patch("app.services.ocr_pipeline.check_llamacpp_health", new=AsyncMock(return_value=True)), patch(
        "app.services.ocr_pipeline._process_exam", new=AsyncMock(side_effect=fake_process_exam)
    ):
        _run(run_pipeline(object(), MagicMock(), MagicMock(), queue, ResultHandler(object()), settings))

    assert processed == ["exam-1", "exam-2"]


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
