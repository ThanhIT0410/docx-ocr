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
    queue.enqueue("a")
    queue.enqueue("b")
    queue.enqueue("c")

    assert queue.dequeue() == "a"
    assert queue.dequeue() == "b"
    assert queue.dequeue() == "c"


def test_dequeue_empty_raises():
    queue = QueueService(max_size=10)
    with pytest.raises(QueueEmptyError):
        queue.dequeue()


def test_enqueue_duplicate_raises():
    queue = QueueService(max_size=10)
    queue.enqueue("a")
    with pytest.raises(ExamAlreadyQueuedError):
        queue.enqueue("a")


def test_enqueue_beyond_max_size_raises():
    queue = QueueService(max_size=2)
    queue.enqueue("a")
    queue.enqueue("b")
    with pytest.raises(QueueFullError):
        queue.enqueue("c")
    assert queue.qsize() == 2


def test_cancel_removes_from_middle_without_disturbing_order():
    queue = QueueService(max_size=10)
    queue.enqueue("a")
    queue.enqueue("b")
    queue.enqueue("c")

    queue.cancel("b")

    assert queue.is_queued("b") is False
    assert queue.dequeue() == "a"
    assert queue.dequeue() == "c"


def test_cancel_unknown_exam_raises():
    queue = QueueService(max_size=10)
    with pytest.raises(ExamNotQueuedError):
        queue.cancel("does-not-exist")


def test_cancel_frees_capacity_immediately():
    """Regression test for the exact bug an asyncio.Queue + tombstone
    approach would have: cancelling must free a slot right away, not only
    once something eventually pops the physical queue."""
    queue = QueueService(max_size=1)
    queue.enqueue("a")
    with pytest.raises(QueueFullError):
        queue.enqueue("b")

    queue.cancel("a")

    queue.enqueue("b")  # must not raise QueueFullError
    assert queue.qsize() == 1
    assert queue.dequeue() == "b"
