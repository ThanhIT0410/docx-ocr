"""Unit tests for ResultHandler — run against mocked repository calls, not
a real Supabase project (append() writes `status='finished'` for real, so
it must never touch the actual DB in a test)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.models import OcrPageResult
from app.services.result_handler import (
    ExamAlreadyTrackedError,
    ResultHandler,
    UnknownExamError,
)

FAKE_RESULT = OcrPageResult(origin_width=100, origin_height=200, input_width=100, input_height=200, layouts=[])


def _handler() -> ResultHandler:
    return ResultHandler(db=object())  # never dereferenced — repo calls are mocked


def _run(coro):
    return asyncio.run(coro)


def test_append_marks_exam_finished_only_once_all_pages_in():
    handler = _handler()
    handler.start_exam("exam-1", "Exam One", total_pages=3)

    with patch("app.services.result_handler.pages.save_ocr_text", new=AsyncMock()), patch(
        "app.services.result_handler.exams.update_exam", new=AsyncMock()
    ) as mock_update:

        async def scenario():
            first = await handler.append("exam-1", "page-1", FAKE_RESULT)
            assert handler.progress("exam-1") == (1, 3)
            second = await handler.append("exam-1", "page-2", FAKE_RESULT)
            assert handler.progress("exam-1") == (2, 3)
            third = await handler.append("exam-1", "page-3", FAKE_RESULT)
            return first, second, third

        first, second, third = _run(scenario())

    assert (first, second, third) == (False, False, True)
    mock_update.assert_awaited_once()
    assert mock_update.await_args.args[2]["status"] == "finished"
    assert handler.is_tracking("exam-1") is False


def test_duplicate_page_append_does_not_double_count():
    handler = _handler()
    handler.start_exam("exam-1", "Exam One", total_pages=2)

    with patch("app.services.result_handler.pages.save_ocr_text", new=AsyncMock()), patch(
        "app.services.result_handler.exams.update_exam", new=AsyncMock()
    ) as mock_update:

        async def scenario():
            return [await handler.append("exam-1", "page-1", FAKE_RESULT) for _ in range(3)]

        results = _run(scenario())

    assert results == [False, False, False]
    mock_update.assert_not_awaited()
    assert handler.is_tracking("exam-1") is True


def test_concurrent_last_two_pages_complete_exactly_once():
    """The two ordering scenarios this is guarding against: two pages of
    the same exam finishing "at the same time" must never both observe
    is_complete=True, and the DB must only be flipped to 'finished' once."""
    handler = _handler()
    handler.start_exam("exam-1", "Exam One", total_pages=2)

    with patch("app.services.result_handler.pages.save_ocr_text", new=AsyncMock()), patch(
        "app.services.result_handler.exams.update_exam", new=AsyncMock()
    ) as mock_update:

        async def scenario():
            return await asyncio.gather(
                handler.append("exam-1", "page-1", FAKE_RESULT),
                handler.append("exam-1", "page-2", FAKE_RESULT),
            )

        results = _run(scenario())

    assert sorted(results) == [False, True]
    mock_update.assert_awaited_once()


def test_append_unknown_exam_raises():
    handler = _handler()
    with pytest.raises(UnknownExamError):
        _run(handler.append("never-started", "page-1", FAKE_RESULT))


def test_start_exam_twice_raises():
    handler = _handler()
    handler.start_exam("exam-1", "Exam One", total_pages=1)
    with pytest.raises(ExamAlreadyTrackedError):
        handler.start_exam("exam-1", "Exam One", total_pages=1)


def test_list_progress_reflects_in_flight_exams_only():
    handler = _handler()
    handler.start_exam("exam-1", "Exam One", total_pages=2)
    handler.start_exam("exam-2", "Exam Two", total_pages=1)

    with patch("app.services.result_handler.pages.save_ocr_text", new=AsyncMock()), patch(
        "app.services.result_handler.exams.update_exam", new=AsyncMock()
    ):
        snapshot = {s.exam_id: s for s in handler.list_progress()}
        assert snapshot["exam-1"].exam_title == "Exam One"
        assert (snapshot["exam-1"].completed_pages, snapshot["exam-1"].total_pages) == (0, 2)
        assert (snapshot["exam-2"].completed_pages, snapshot["exam-2"].total_pages) == (0, 1)

        # exam-2 completes and drops out of tracking -> out of list_progress too.
        _run(handler.append("exam-2", "page-1", FAKE_RESULT))
        exam_ids = {s.exam_id for s in handler.list_progress()}
        assert exam_ids == {"exam-1"}


def test_start_exam_resumes_from_existing_results():
    """Crash-recovery case: a page already has ocr_text from a previous
    run (see app/services/recovery.py) — start_exam must seed progress
    with it instead of starting at 0, and completion must fire off just
    the remaining pages."""
    handler = _handler()
    handler.start_exam(
        "exam-1",
        "Exam One",
        total_pages=3,
        existing_results={"page-1": FAKE_RESULT, "page-2": FAKE_RESULT},
    )

    assert handler.progress("exam-1") == (2, 3)

    with patch("app.services.result_handler.pages.save_ocr_text", new=AsyncMock()), patch(
        "app.services.result_handler.exams.update_exam", new=AsyncMock()
    ) as mock_update:
        is_complete = _run(handler.append("exam-1", "page-3", FAKE_RESULT))

    assert is_complete is True
    mock_update.assert_awaited_once()
    assert handler.is_tracking("exam-1") is False


def test_finalize_if_complete_handles_exam_fully_resumed_already():
    """Edge case start_exam alone can't resolve: every page already had
    ocr_text before start_exam was ever called, so append() — the only
    other place completion is detected — will never be called at all."""
    handler = _handler()
    handler.start_exam(
        "exam-1",
        "Exam One",
        total_pages=2,
        existing_results={"page-1": FAKE_RESULT, "page-2": FAKE_RESULT},
    )

    with patch("app.services.result_handler.exams.update_exam", new=AsyncMock()) as mock_update:
        is_complete = _run(handler.finalize_if_complete("exam-1"))

    assert is_complete is True
    mock_update.assert_awaited_once()
    assert mock_update.await_args.args[2]["status"] == "finished"
    assert handler.is_tracking("exam-1") is False


def test_finalize_if_complete_is_a_noop_when_pages_remain():
    handler = _handler()
    handler.start_exam("exam-1", "Exam One", total_pages=2, existing_results={"page-1": FAKE_RESULT})

    with patch("app.services.result_handler.exams.update_exam", new=AsyncMock()) as mock_update:
        is_complete = _run(handler.finalize_if_complete("exam-1"))

    assert is_complete is False
    mock_update.assert_not_awaited()
    assert handler.is_tracking("exam-1") is True


def test_finalize_if_complete_unknown_exam_raises():
    handler = _handler()
    with pytest.raises(UnknownExamError):
        _run(handler.finalize_if_complete("never-started"))
