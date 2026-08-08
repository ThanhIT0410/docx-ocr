"""Two-tier admission queue for OCR processing.

**Exam tier** (this class): admission control + FIFO ordering of which
exam's pages get activated next. Mirrors what existed before this file was
split — `enqueue`/`dequeue`/`cancel` still operate on exam_ids, still back
`controllers/queue.py`'s HTTP routes 1:1 — but `max_size` now bounds a
**page budget**, not an exam count (see `_admitted_pages` below), because
exams vary wildly in page count and a per-exam cap doesn't reflect real
load.

**Page tier**: `app/services/page_pool.py::PagePool.pop_page()` — the OCR
pipeline's actual work-pull primitive, one page at a time, spanning
multiple exams so llama.cpp always has work queued even when the "current"
exam is down to its last page. `PagePool` is a separate class layered on
top of this one (calls `dequeue()` to activate the next exam's pages) —
kept here as the one place page-budget bookkeeping lives, so admission
control (this file) and work distribution (page_pool.py) stay decoupled.

Backed by a plain `dict[str, int]` (insertion-ordered, like every dict
since Python 3.7) — exam_id -> the page_count it was admitted with — instead
of `asyncio.Queue`. That started out as an `asyncio.Queue` wrapper, but
`cancel()` — removing one specific exam_id out of the middle of the queue,
needed once an operator can pull an exam back out of processing (see
controllers/queue.py's dequeue) — has no equivalent on `asyncio.Queue` (it
only supports popping the front). A tombstone-based workaround (mark
cancelled, skip over it on dequeue) was considered and rejected: the
cancelled entry would keep occupying a slot in the underlying queue until
something eventually pops it, so `max_size` enforcement would stay wrong
until that happens. A dict gives O(1) enqueue/dequeue-from-front/cancel-by-
key, and `len()`/`_admitted_pages` as always-accurate sources of truth.

None of this needs a lock: every method here does its dict/bookkeeping
mutations synchronously (no `await` in between), so on the single-threaded
asyncio event loop each call is atomic with respect to every other
coroutine touching the same instance.
"""
from __future__ import annotations


class QueueFullError(Exception):
    """Raised by `enqueue` when admitting `page_count` more pages would
    push total admitted pages past `max_size` — the frontend
    selected/enqueued more exams than currently fit; the caller should
    surface this per-exam rather than failing everything."""

    def __init__(self, max_size: int):
        super().__init__(f"Processing capacity is full (max {max_size} pages admitted at once) — try again later")
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
    already dequeued (its pages may already be flowing through
    PagePool/being worked on — cancelling that is not supported, same
    limitation as before this file was split), already cancelled, or never
    enqueued."""

    def __init__(self, exam_id: str):
        super().__init__(f"Exam {exam_id} is not in the queue")
        self.exam_id = exam_id


class QueueService:
    def __init__(self, max_size: int):
        self._items: dict[str, int] = {}  # exam_id -> page_count, FIFO order
        self._max_size = max_size
        # Which exams actually had their pages charged against the budget,
        # and how many — a SUBSET of what's ever passed through `_items`
        # (readmit()'d exams sit in `_items` but never appear here, see its
        # docstring). `release()` looks an exam up here, not the caller-
        # supplied count, specifically so releasing a never-charged
        # (readmitted) exam is a no-op instead of corrupting the ledger for
        # unrelated exams that ARE genuinely holding budget right now.
        self._charged: dict[str, int] = {}
        # Total pages across every exam admitted into 'processing' that
        # hasn't yet *concluded* (finished/failed) — NOT the same as the
        # sum of `_items.values()`, since an exam's pages stay counted here
        # after `dequeue()` pulls it out of the FIFO (PagePool has taken it
        # over) all the way until `release()` is called for it. This is
        # what `max_size` actually bounds.
        self._admitted_pages = 0

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def admitted_pages(self) -> int:
        return self._admitted_pages

    def qsize(self) -> int:
        """Number of exams still sitting in the FIFO, not yet dequeued for
        work — NOT the same as `admitted_pages` (see that property).
        Mainly useful for `POST /processor/ocr/start`'s "is there anything
        to do" check."""
        return len(self._items)

    def is_queued(self, exam_id: str) -> bool:
        return exam_id in self._items

    def enqueue(self, exam_id: str, page_count: int) -> None:
        if exam_id in self._items:
            raise ExamAlreadyQueuedError(exam_id)
        if self._admitted_pages + page_count > self._max_size:
            raise QueueFullError(self._max_size)
        self._items[exam_id] = page_count
        self._charged[exam_id] = page_count
        self._admitted_pages += page_count

    def readmit(self, exam_id: str, page_count: int) -> None:
        """Puts `exam_id` back in the FIFO WITHOUT charging its budget —
        used only by `app/services/recovery.py` for an exam that's already
        `status='processing'`. Two situations land here, and this method is
        deliberately a no-op on `_admitted_pages` for both:

        - **Mid-process recovery** (a run aborted — llama.cpp went
          unhealthy — while this exam was active): its budget was already
          charged by the original `enqueue()` call and never released
          (`ResultHandler.abandon_all` intentionally leaves it held). Not
          charging again here is what avoids double-counting it — critical,
          since unlike a one-time restart this can repeat every time a run
          aborts and recovers the same exam, and would otherwise compound
          without limit until `max_size` is permanently unusable.
        - **Process-restart recovery**: the fresh `QueueService` never
          charged this exam at all (its ledger starts at 0). Not charging
          it here under-counts real capacity until it concludes — a much
          smaller, self-correcting imprecision (see `release()`) than the
          double-count the other case would otherwise cause, so the same
          no-op behavior is used for both rather than trying to distinguish
          them."""
        if exam_id in self._items:
            raise ExamAlreadyQueuedError(exam_id)
        self._items[exam_id] = page_count

    def dequeue(self) -> tuple[str, int]:
        """Pops the oldest queued exam_id (and the page_count it was
        admitted with) — the primitive `PagePool` uses to activate the next
        exam's pages, NOT the HTTP endpoint of the same name (which cancels
        a specific exam instead; see controllers/queue.py). Does not touch
        `_admitted_pages`/`_charged` — that budget (if any was charged)
        stays held until `release()`."""
        try:
            exam_id = next(iter(self._items))
        except StopIteration as exc:
            raise QueueEmptyError() from exc
        page_count = self._items.pop(exam_id)
        return exam_id, page_count

    def cancel(self, exam_id: str) -> None:
        """Removes one specific exam_id from wherever it sits in the FIFO
        — not necessarily the front — and frees its admitted-page budget
        (if it had any charged — a `readmit()`'d exam might not, see that
        method's docstring). Only works while the exam is still in the FIFO
        (not yet dequeued for work); see `ExamNotQueuedError`."""
        if exam_id not in self._items:
            raise ExamNotQueuedError(exam_id)
        del self._items[exam_id]
        charged = self._charged.pop(exam_id, None)
        if charged is not None:
            self._admitted_pages -= charged

    def release(self, exam_id: str) -> None:
        """Frees whatever budget was charged for `exam_id` (if any — see
        `readmit()`) once it *concludes* (finished or failed) — called by
        `app/services/result_handler.py` at the same point it writes that
        status to the DB (see `ResultHandler._mark_finished_in_db`/
        `fail_exam`), and by `PagePool` for the one case that never reaches
        `ResultHandler` at all (the exam row vanished — e.g. deleted via
        admin/reset — between enqueue and activation). Looks the charged
        amount up internally rather than taking it as a parameter — a
        caller passing the wrong number here (e.g. a readmitted exam's full
        original page count) would otherwise silently corrupt the ledger
        for unrelated exams."""
        charged = self._charged.pop(exam_id, None)
        if charged is not None:
            self._admitted_pages = max(0, self._admitted_pages - charged)
