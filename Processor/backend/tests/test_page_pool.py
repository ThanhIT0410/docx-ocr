"""Unit tests for PagePool — the page-tier work-pull primitive. exams/pages
repository calls are mocked; QueueService/ResultHandler are real (both pure
in-memory)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.schemas.models import Exam, Page
from app.services.page_pool import PagePool
from app.services.queue_service import QueueService
from app.services.result_handler import ResultHandler


def _exam(exam_id: str, title: str = "Exam") -> Exam:
    return Exam.model_validate(
        {
            "id": exam_id,
            "title": title,
            "status": "processing",
            "error_message": None,
            "uploaded_at": "2026-01-01T00:00:00Z",
            "started_at": None,
            "finished_at": None,
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )


def _page(page_id: str, exam_id: str, order: int, ocr_text=None) -> Page:
    return Page.model_validate(
        {
            "id": page_id,
            "exam_id": exam_id,
            "page_order": order,
            "file_path": f"{exam_id}/{page_id}.jpg",
            "ocr_text": ocr_text,
        }
    )


def _run(coro):
    return asyncio.run(coro)


def _pool(queue: QueueService, exam_pages: dict[str, list[Page]], exam_rows: dict[str, Exam | None]):
    result_handler = ResultHandler(db=object(), queue=queue)
    pool = PagePool(db=object(), queue=queue, result_handler=result_handler)

    async def fake_get_exam(db, exam_id):
        return exam_rows.get(exam_id)

    async def fake_list_pages(db, exam_id):
        return exam_pages.get(exam_id, [])

    return pool, result_handler, fake_get_exam, fake_list_pages


def test_pop_page_drains_one_exam_then_moves_to_the_next():
    queue = QueueService(max_size=100)
    queue.enqueue("exam-1", 2)
    queue.enqueue("exam-2", 1)
    exam_pages = {
        "exam-1": [_page("p1", "exam-1", 1), _page("p2", "exam-1", 2)],
        "exam-2": [_page("p3", "exam-2", 1)],
    }
    exam_rows = {"exam-1": _exam("exam-1"), "exam-2": _exam("exam-2")}
    pool, _, fake_get_exam, fake_list_pages = _pool(queue, exam_pages, exam_rows)

    async def scenario():
        popped = []
        with patch("app.services.page_pool.exams.get_exam", new=fake_get_exam), patch(
            "app.services.page_pool.pages.list_pages", new=fake_list_pages
        ):
            while True:
                item = await pool.pop_page()
                if item is None:
                    break
                popped.append(item[0])
        return popped

    popped = _run(scenario())
    assert popped == ["exam-1", "exam-1", "exam-2"]


def test_pop_page_skips_already_fully_resumed_exam():
    queue = QueueService(max_size=100)
    queue.enqueue("exam-1", 1)
    queue.enqueue("exam-2", 1)
    fake_result = {"origin_width": 1, "origin_height": 1, "input_width": 1, "input_height": 1, "layouts": []}
    exam_pages = {
        "exam-1": [_page("p1", "exam-1", 1, ocr_text=fake_result)],  # already done
        "exam-2": [_page("p2", "exam-2", 1)],
    }
    exam_rows = {"exam-1": _exam("exam-1"), "exam-2": _exam("exam-2")}
    pool, result_handler, fake_get_exam, fake_list_pages = _pool(queue, exam_pages, exam_rows)

    async def scenario():
        with patch("app.services.page_pool.exams.get_exam", new=fake_get_exam), patch(
            "app.services.page_pool.pages.list_pages", new=fake_list_pages
        ), patch("app.services.result_handler.exams.update_exam", new=AsyncMock()) as mock_update:
            item = await pool.pop_page()
        return item, mock_update

    item, mock_update = _run(scenario())
    assert item is not None and item[0] == "exam-2"
    mock_update.assert_awaited_once()  # exam-1 finalized straight away
    assert mock_update.await_args.args[2]["status"] == "finished"
    assert result_handler.is_tracking("exam-1") is False
    assert queue.admitted_pages == 1  # exam-1's budget was released


def test_pop_page_skips_vanished_exam_and_releases_its_budget():
    queue = QueueService(max_size=100)
    queue.enqueue("exam-1", 5)
    queue.enqueue("exam-2", 1)
    exam_pages = {"exam-2": [_page("p1", "exam-2", 1)]}
    exam_rows = {"exam-1": None, "exam-2": _exam("exam-2")}  # exam-1 deleted between enqueue and now
    pool, _, fake_get_exam, fake_list_pages = _pool(queue, exam_pages, exam_rows)

    async def scenario():
        with patch("app.services.page_pool.exams.get_exam", new=fake_get_exam), patch(
            "app.services.page_pool.pages.list_pages", new=fake_list_pages
        ):
            return await pool.pop_page()

    item = _run(scenario())
    assert item is not None and item[0] == "exam-2"
    assert queue.admitted_pages == 1  # exam-1's 5-page budget was freed, not left stuck


def test_pop_page_returns_none_once_everything_is_dispatched():
    queue = QueueService(max_size=100)
    queue.enqueue("exam-1", 1)
    exam_pages = {"exam-1": [_page("p1", "exam-1", 1)]}
    exam_rows = {"exam-1": _exam("exam-1")}
    pool, _, fake_get_exam, fake_list_pages = _pool(queue, exam_pages, exam_rows)

    async def scenario():
        with patch("app.services.page_pool.exams.get_exam", new=fake_get_exam), patch(
            "app.services.page_pool.pages.list_pages", new=fake_list_pages
        ):
            first = await pool.pop_page()
            second = await pool.pop_page()
        return first, second

    first, second = _run(scenario())
    assert first is not None
    assert second is None


def test_activate_error_before_start_exam_releases_budget_and_does_not_crash_pop_page():
    """A transient failure fetching an exam's page list (e.g. a Supabase
    blip) must not propagate out of pop_page() — that would crash the
    entire run via asyncio.gather, taking every other exam's in-flight
    work down with it over a problem scoped to one exam. Must instead
    release the budget (start_exam was never reached) and let the caller's
    loop move on to the next exam."""
    queue = QueueService(max_size=100)
    queue.enqueue("bad-exam", 3)
    queue.enqueue("good-exam", 1)
    exam_rows = {"bad-exam": _exam("bad-exam"), "good-exam": _exam("good-exam")}
    good_pages = [_page("p1", "good-exam", 1)]

    result_handler = ResultHandler(db=object(), queue=queue)
    pool = PagePool(db=object(), queue=queue, result_handler=result_handler)

    async def fake_get_exam(db, exam_id):
        return exam_rows[exam_id]

    async def flaky_list_pages(db, exam_id):
        if exam_id == "bad-exam":
            raise RuntimeError("supabase network blip")
        return good_pages

    async def scenario():
        with patch("app.services.page_pool.exams.get_exam", new=fake_get_exam), patch(
            "app.services.page_pool.pages.list_pages", new=flaky_list_pages
        ):
            return await pool.pop_page()  # must not raise, despite bad-exam failing first

    item = _run(scenario())

    assert item is not None and item[0] == "good-exam"  # moved on past the broken exam
    assert result_handler.is_tracking("bad-exam") is False
    assert queue.admitted_pages == 1  # bad-exam's 3-page budget released, good-exam's 1 still held


def test_activate_error_after_start_exam_fails_the_exam_not_just_releases():
    """If something fails AFTER start_exam() already registered the exam
    (e.g. finalize_if_complete has a bug), a bare release() would leave it
    tracked-but-orphaned for the rest of the run — nothing would ever
    dispatch its pages again. Must go through fail_exam() instead, which
    both releases the budget AND drops tracking."""
    queue = QueueService(max_size=100)
    queue.enqueue("exam-1", 1)
    exam_rows = {"exam-1": _exam("exam-1")}
    exam_pages = {"exam-1": [_page("p1", "exam-1", 1)]}

    result_handler = ResultHandler(db=object(), queue=queue)
    pool = PagePool(db=object(), queue=queue, result_handler=result_handler)

    async def fake_get_exam(db, exam_id):
        return exam_rows[exam_id]

    async def fake_list_pages(db, exam_id):
        return exam_pages[exam_id]

    async def scenario():
        with patch("app.services.page_pool.exams.get_exam", new=fake_get_exam), patch(
            "app.services.page_pool.pages.list_pages", new=fake_list_pages
        ), patch.object(
            result_handler, "finalize_if_complete", new=AsyncMock(side_effect=RuntimeError("bug"))
        ), patch(
            "app.services.result_handler.exams.update_exam", new=AsyncMock()
        ) as mock_update:
            item = await pool.pop_page()
            return item, mock_update

    item, mock_update = _run(scenario())

    assert item is None  # nothing left to dispatch — exam-1 was failed, not silently dropped
    assert result_handler.is_tracking("exam-1") is False
    assert queue.admitted_pages == 0
    assert mock_update.await_args.args[2]["status"] == "failed"


def test_concurrent_workers_do_not_return_none_while_a_sibling_is_still_activating():
    """Regression test for the exact race pop_page's Condition variable
    guards against: worker B must not see 'FIFO empty' and quit while
    worker A is still mid-fetch for the exam it already claimed — that
    fetch may still produce pages for B to pick up."""
    queue = QueueService(max_size=100)
    queue.enqueue("exam-1", 1)
    exam_rows = {"exam-1": _exam("exam-1")}
    exam_pages = {"exam-1": [_page("p1", "exam-1", 1)]}
    pool, _, fake_get_exam, _real_list_pages = _pool(queue, exam_pages, exam_rows)

    activation_started = asyncio.Event()
    release_activation = asyncio.Event()

    async def slow_list_pages(db, exam_id):
        activation_started.set()
        await release_activation.wait()
        return exam_pages[exam_id]

    async def scenario():
        with patch("app.services.page_pool.exams.get_exam", new=fake_get_exam), patch(
            "app.services.page_pool.pages.list_pages", new=slow_list_pages
        ):
            worker_a = asyncio.create_task(pool.pop_page())  # claims exam-1, blocks in slow_list_pages
            await activation_started.wait()

            worker_b = asyncio.create_task(pool.pop_page())  # FIFO now empty — must wait, not return None
            await asyncio.sleep(0.05)
            assert not worker_b.done(), "worker B returned before worker A's activation could deliver a page"

            release_activation.set()
            result_a = await worker_a
            result_b = await worker_b
        return result_a, result_b

    result_a, result_b = _run(scenario())
    # Exactly one of them got the page, the other correctly saw "nothing
    # left" once the activation genuinely finished with no pages leftover
    # for it.
    non_none = [r for r in (result_a, result_b) if r is not None]
    assert len(non_none) == 1
    assert non_none[0][0] == "exam-1"
