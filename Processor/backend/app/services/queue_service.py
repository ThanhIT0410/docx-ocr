"""In-process FIFO queue of exam IDs waiting to be picked up for OCR
processing (§2.3 step 1's replacement for DB-based claiming). One
`QueueService` instance lives on `app.state` for the whole API process
lifetime (see app/main.py's lifespan) — in-memory only, not persisted, not
shared across processes; a restart drops whatever was queued but not yet
dequeued (recovered separately at startup — see app/services/recovery.py).

Backed by a plain `dict[str, None]` (insertion-ordered, like every dict
since Python 3.7) instead of `asyncio.Queue`. That started out as an
`asyncio.Queue` wrapper, but `cancel()` — removing one specific exam_id
out of the middle of the queue, needed once an operator can pull an exam
back out of processing (see controllers/queue.py's dequeue) — has no
equivalent on `asyncio.Queue` (it only supports popping the front). A
tombstone-based workaround (mark cancelled, skip over it on dequeue) was
considered and rejected: the cancelled entry would keep occupying a slot
in the underlying queue until something eventually pops it, so `max_size`
enforcement (`put_nowait` raising `QueueFull`) would stay wrong — falsely
reporting "full" — until that pop happens, which nothing guarantees will
be soon. A dict gives O(1) enqueue/dequeue-from-front/cancel-by-key and
`len()` as a single, always-accurate source of truth for both size and
fullness, so `max_size` is enforced correctly the instant something is
cancelled.

None of this needs a lock: every method here does its dict/bookkeeping
mutations synchronously (no `await` in between), so on the single-threaded
asyncio event loop each call is atomic with respect to every other
coroutine touching the same instance.
"""
from __future__ import annotations


class QueueFullError(Exception):
    """Raised by `enqueue` when the queue is already at `max_size` —
    the frontend selected/enqueued more exams than currently fit; the
    caller should surface this per-exam rather than failing everything."""

    def __init__(self, max_size: int):
        super().__init__(f"Queue is full (max {max_size}) — try again later")
        self.max_size = max_size


class QueueEmptyError(Exception):
    """Raised by `dequeue` when there's nothing waiting."""

    def __init__(self):
        super().__init__("Queue is empty")


class ExamAlreadyQueuedError(Exception):
    """Raised by `enqueue` when `exam_id` is already sitting in the queue
    (not yet dequeued/cancelled) — guards against the same exam being
    queued twice by an overlapping bulk-enqueue call or a double click."""

    def __init__(self, exam_id: str):
        super().__init__(f"Exam {exam_id} is already queued")
        self.exam_id = exam_id


class ExamNotQueuedError(Exception):
    """Raised by `cancel` when `exam_id` isn't currently in the queue —
    already dequeued, already cancelled, or never enqueued."""

    def __init__(self, exam_id: str):
        super().__init__(f"Exam {exam_id} is not in the queue")
        self.exam_id = exam_id


class QueueService:
    def __init__(self, max_size: int):
        self._items: dict[str, None] = {}
        self._max_size = max_size

    @property
    def max_size(self) -> int:
        return self._max_size

    def qsize(self) -> int:
        return len(self._items)

    def is_queued(self, exam_id: str) -> bool:
        return exam_id in self._items

    def enqueue(self, exam_id: str) -> None:
        if exam_id in self._items:
            raise ExamAlreadyQueuedError(exam_id)
        if len(self._items) >= self._max_size:
            raise QueueFullError(self._max_size)
        self._items[exam_id] = None

    def dequeue(self) -> str:
        """Pops the oldest queued exam_id — the primitive whatever
        actually consumes the queue for OCR processing calls (not the
        HTTP endpoint of the same name, which cancels a specific exam
        instead; see controllers/queue.py)."""
        try:
            exam_id = next(iter(self._items))
        except StopIteration as exc:
            raise QueueEmptyError() from exc
        del self._items[exam_id]
        return exam_id

    def cancel(self, exam_id: str) -> None:
        """Removes one specific exam_id from wherever it sits in the
        queue — not necessarily the front."""
        if exam_id not in self._items:
            raise ExamNotQueuedError(exam_id)
        del self._items[exam_id]
