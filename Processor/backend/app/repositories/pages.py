"""All reads/writes to the `pages` table."""
from __future__ import annotations

from supabase import AsyncClient

from app.schemas.models import OcrPageResult, Page


def _row_to_page(row: dict) -> Page:
    return Page.model_validate(row)


async def save_ocr_text(db: AsyncClient, page_id: str, result: OcrPageResult) -> None:
    await db.table("pages").update({"ocr_text": result.model_dump()}).eq("id", page_id).execute()


async def clear_ocr_text_for_exam(db: AsyncClient, exam_id: str) -> None:
    """Wipes `ocr_text` back to null for every page of an exam — used when
    an exam is pulled back out of the processing queue (cancel), so a
    later fresh enqueue doesn't start from a confusing mix of old and new
    results (see controllers/queue.py's dequeue)."""
    await db.table("pages").update({"ocr_text": None}).eq("exam_id", exam_id).execute()


async def list_pages(db: AsyncClient, exam_id: str) -> list[Page]:
    res = (
        await db.table("pages")
        .select("*")
        .eq("exam_id", exam_id)
        .order("page_order", desc=False)
        .execute()
    )
    return [_row_to_page(r) for r in res.data]


async def count_total(db: AsyncClient, exam_id: str) -> int:
    res = await db.table("pages").select("id", count="exact").eq("exam_id", exam_id).execute()
    return res.count or 0


async def list_file_paths(db: AsyncClient, exam_ids: list[str] | None = None) -> list[str]:
    """Used by the admin reset flow to know which Storage objects to
    delete, since deleting `exams` rows cascades `pages` rows in Postgres
    but does NOT touch Storage (see repositories/exams.py:delete_exams).
    """
    q = db.table("pages").select("file_path")
    if exam_ids is not None:
        if not exam_ids:
            return []
        q = q.in_("exam_id", exam_ids)
    res = await q.execute()
    return [r["file_path"] for r in res.data]
