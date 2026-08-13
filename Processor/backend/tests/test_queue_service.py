"""Unit tests for QueueService — pure in-memory, no mocking needed."""
from __future__ import annotations

import pytest

from app.services.queue_service import (
    ExamAlreadyQueuedError,
    ExamNotQueuedError,
    QueueEmptyError,
    QueueFullError,
    QueueService,
)


def test_enqueue_dequeue_is_fifo():
    queue = QueueService(max_size=10)
    queue.enqueue("a", 1)
    queue.enqueue("b", 1)
    queue.enqueue("c", 1)

    assert queue.dequeue() == ("a", 1)
    assert queue.dequeue() == ("b", 1)
    assert queue.dequeue() == ("c", 1)


def test_dequeue_empty_raises():
    queue = QueueService(max_size=10)
    with pytest.raises(QueueEmptyError):
        queue.dequeue()


def test_enqueue_duplicate_raises():
    queue = QueueService(max_size=10)
    queue.enqueue("a", 1)
    with pytest.raises(ExamAlreadyQueuedError):
        queue.enqueue("a", 1)


def test_enqueue_beyond_max_size_raises():
    """max_size bounds total PAGES admitted, not exam count."""
    queue = QueueService(max_size=5)
    queue.enqueue("a", 3)
    queue.enqueue("b", 2)
    with pytest.raises(QueueFullError):
        queue.enqueue("c", 1)
    assert queue.admitted_pages == 5


def test_cancel_removes_from_middle_without_disturbing_order():
    queue = QueueService(max_size=10)
    queue.enqueue("a", 1)
    queue.enqueue("b", 1)
    queue.enqueue("c", 1)

    queue.cancel("b")

    assert queue.is_queued("b") is False
    assert queue.dequeue() == ("a", 1)
    assert queue.dequeue() == ("c", 1)


def test_cancel_unknown_exam_raises():
    queue = QueueService(max_size=10)
    with pytest.raises(ExamNotQueuedError):
        queue.cancel("does-not-exist")


def test_cancel_frees_capacity_immediately():
    """Regression test for the exact bug an asyncio.Queue + tombstone
    approach would have: cancelling must free budget right away, not only
    once something eventually pops the physical queue."""
    queue = QueueService(max_size=5)
    queue.enqueue("a", 5)
    with pytest.raises(QueueFullError):
        queue.enqueue("b", 1)

    queue.cancel("a")

    queue.enqueue("b", 5)  # must not raise QueueFullError
    assert queue.admitted_pages == 5
    assert queue.dequeue() == ("b", 5)


def test_admitted_pages_stays_held_after_dequeue_until_release():
    """The core admission-vs-FIFO distinction: dequeue() (PagePool
    activating an exam's pages) must NOT free its budget — only release()
    (the exam concluding) does. Otherwise the cap would stop reflecting
    real 'processing' load the moment work starts, not when it ends."""
    queue = QueueService(max_size=5)
    queue.enqueue("a", 5)
    assert queue.admitted_pages == 5

    queue.dequeue()
    assert queue.admitted_pages == 5  # still held
    with pytest.raises(QueueFullError):
        queue.enqueue("b", 1)  # no room — "a" hasn't concluded yet

    queue.release("a")
    assert queue.admitted_pages == 0
    queue.enqueue("b", 5)  # now fits


def test_readmit_does_not_charge_budget_and_bypasses_max_size():
    queue = QueueService(max_size=1)
    queue.enqueue("a", 1)  # ledger genuinely full
    queue.readmit("b", 100)  # must not raise, must not touch admitted_pages
    assert queue.admitted_pages == 1
    assert queue.dequeue() == ("a", 1)
    assert queue.dequeue() == ("b", 100)


def test_release_after_readmit_is_a_noop_and_does_not_corrupt_other_budget():
    """Regression test for the exact bug considered and rejected: releasing
    a readmitted (never-charged) exam must not eat into a DIFFERENT exam's
    legitimately-held budget."""
    queue = QueueService(max_size=10)
    queue.enqueue("real", 5)
    queue.readmit("phantom", 100)

    queue.release("phantom")

    assert queue.admitted_pages == 5  # "real"'s budget untouched


def test_cancel_a_readmitted_exam_does_not_touch_admitted_pages():
    queue = QueueService(max_size=10)
    queue.enqueue("real", 3)
    queue.readmit("phantom", 100)

    queue.cancel("phantom")

    assert queue.admitted_pages == 3
    assert queue.is_queued("phantom") is False
