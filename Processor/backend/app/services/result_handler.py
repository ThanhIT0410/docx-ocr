"""Tracks per-exam OCR progress as pages finish (§2.3 steps 4-5's
replacement now that concurrency is designed at the *page* level in the
main OCR loop — not built yet, see app/services/ocr_client.py — rather
than one exam being fully processed before the next starts).

Without this, nothing would know "every page of exam X is done" once
pages from several dequeued exams can be in flight at the same time —
each exam has to be watched independently and flipped to 'finished' the
moment *its own* last page lands, not once the whole queue drains (that's
the point: exam completion shouldn't wait on unrelated exams still being
OCR'd).

`start_exam` registers an exam — caller supplies `total_pages` (from
`repositories/pages.py::count_total`) once it decides to start OCR-ing
that exam's pages. `append` is then called once per finished page: the
"4/8 -> 5/8" progress bump lives in memory only (see `progress()`) — the
DB is not touched until the exam is fully done, at which point status and
final progress are written together in one update.

`start_exam` also accepts pages that already have `ocr_text` from a
previous run — the process can be closed mid-exam (some pages OCR'd,
`exams.status` still 'processing'), and after a restart, whatever
resumes that exam needs to seed tracking with what's already done instead
of starting back at 0 (see app/services/recovery.py, which repopulates
the queue for exactly this case at startup).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from supabase import AsyncClient

from app.constants import STATUS_FINISHED
from app.repositories import exams, pages
from app.schemas.models import OcrPageResult


class UnknownExamError(Exception):
    """`append` called for an exam_id `start_exam` was never called for —
    a caller bug (a page finished for an exam nothing registered), not a
    normal runtime condition."""

    def __init__(self, exam_id: str):
        super().__init__(f"Exam {exam_id} is not being tracked (start_exam was never called for it)")
        self.exam_id = exam_id


class ExamAlreadyTrackedError(Exception):
    """`start_exam` called twice for the same exam_id without it having
    completed in between."""

    def __init__(self, exam_id: str):
        super().__init__(f"Exam {exam_id} is already being tracked")
        self.exam_id = exam_id


@dataclass
class _ExamProgress:
    exam_title: str
    total_pages: int
    results: dict[str, OcrPageResult] = field(default_factory=dict)


@dataclass(frozen=True)
class ExamProgressSnapshot:
    """One row of `ResultHandler.list_progress()` — deliberately thin (no
    per-page detail, no OCR content) since the only consumer is a
    frequently-polled "how far along is X" status endpoint, not something
    that needs the full picture `GET /processor/exams/{id}` already gives."""

    exam_id: str
    exam_title: str
    completed_pages: int
    total_pages: int


class ResultHandler:
    def __init__(self, db: AsyncClient):
        self._db = db
        self._exams: dict[str, _ExamProgress] = {}

    def start_exam(
        self,
        exam_id: str,
        exam_title: str,
        total_pages: int,
        existing_results: dict[str, OcrPageResult] | None = None,
    ) -> None:
        if exam_id in self._exams:
            raise ExamAlreadyTrackedError(exam_id)
        self._exams[exam_id] = _ExamProgress(
            exam_title=exam_title,
            total_pages=total_pages,
            results=dict(existing_results) if existing_results else {},
        )

    def is_tracking(self, exam_id: str) -> bool:
        return exam_id in self._exams

    def progress(self, exam_id: str) -> tuple[int, int]:
        """Current (completed, total) page count for an in-flight exam —
        in-memory only, e.g. for a future status endpoint. Not what's in
        the DB right now (see module docstring); the DB catches up in one
        write when the exam completes."""
        progress = self._exams.get(exam_id)
        if progress is None:
            raise UnknownExamError(exam_id)
        return len(progress.results), progress.total_pages

    def list_progress(self) -> list[ExamProgressSnapshot]:
        """Snapshot of every exam currently being OCR'd — meant to be
        polled repeatedly (see ExamProgressSnapshot's docstring), so this
        stays a plain in-memory read: no DB round-trip, no locking."""
        return [
            ExamProgressSnapshot(
                exam_id=exam_id,
                exam_title=p.exam_title,
                completed_pages=len(p.results),
                total_pages=p.total_pages,
            )
            for exam_id, p in self._exams.items()
        ]

    async def append(self, exam_id: str, page_id: str, result: OcrPageResult) -> bool:
        """Records one finished page's result, persists it, and bumps the
        exam's in-memory progress. The instant every page of this exam is
        in, marks it 'finished' in the DB right here — before returning
        True — not waiting for any other exam still in flight elsewhere.

        Returns whether this call was the one that completed the exam.
        """
        progress = self._exams.get(exam_id)
        if progress is None:
            raise UnknownExamError(exam_id)

        await pages.save_ocr_text(self._db, page_id, result)

        # Keyed by page_id (not a counter) so a duplicate append for the
        # same page can't inflate the count. These two lines run with no
        # `await` between them, so they're atomic with respect to any
        # other `append` call for this same exam — whichever call's dict
        # write is the one that brings the count up to total_pages is the
        # only one that will ever see is_complete=True for it, even if
        # two pages finish "at once" (Python only switches coroutines at
        # an `await`, never mid-statement).
        progress.results[page_id] = result
        is_complete = len(progress.results) >= progress.total_pages

        if is_complete:
            await self._mark_finished_in_db(exam_id)
            del self._exams[exam_id]

        return is_complete

    async def finalize_if_complete(self, exam_id: str) -> bool:
        """Covers the one completion path `append` can't: an exam whose
        pages were *all* already done before `start_exam` even registered
        it (fully resumed from a previous run — see `start_exam`'s
        `existing_results`), so there's no remaining page whose `append`
        call would ever trigger the completion check. Callers should call
        this right after `start_exam`; it's a harmless no-op (returns
        False) if there's still work left."""
        progress = self._exams.get(exam_id)
        if progress is None:
            raise UnknownExamError(exam_id)
        if len(progress.results) < progress.total_pages:
            return False
        await self._mark_finished_in_db(exam_id)
        del self._exams[exam_id]
        return True

    async def _mark_finished_in_db(self, exam_id: str) -> None:
        await exams.update_exam(
            self._db,
            exam_id,
            {
                "status": STATUS_FINISHED,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error_message": None,
            },
        )
