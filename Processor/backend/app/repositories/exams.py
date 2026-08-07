"""All reads/writes to the `exams` table."""
from __future__ import annotations

from supabase import AsyncClient

from app.schemas.models import Exam


def _row_to_exam(row: dict) -> Exam:
    return Exam.model_validate(row)


async def list_exams(db: AsyncClient, status: str | None = None) -> list[Exam]:
    q = db.table("exams").select("*")
    if status:
        q = q.eq("status", status)
    res = await q.order("uploaded_at", desc=True).execute()
    return [_row_to_exam(r) for r in res.data]


async def get_exam(db: AsyncClient, exam_id: str) -> Exam | None:
    res = await db.table("exams").select("*").eq("id", exam_id).limit(1).execute()
    return _row_to_exam(res.data[0]) if res.data else None


async def update_exam(db: AsyncClient, exam_id: str, fields: dict) -> Exam | None:
    res = await db.table("exams").update(fields).eq("id", exam_id).execute()
    return _row_to_exam(res.data[0]) if res.data else None


async def count_by_status(db: AsyncClient, status: str) -> int:
    res = await db.table("exams").select("id", count="exact").eq("status", status).execute()
    return res.count or 0


async def count_by_status_since(db: AsyncClient, status: str, since_iso: str) -> int:
    res = (
        await db.table("exams")
        .select("id", count="exact")
        .eq("status", status)
        .gte("updated_at", since_iso)
        .execute()
    )
    return res.count or 0


async def list_all_exam_ids(db: AsyncClient) -> list[str]:
    res = await db.table("exams").select("id").execute()
    return [r["id"] for r in res.data]


async def delete_exams(db: AsyncClient, exam_ids: list[str]) -> int:
    """Deletes the given exams; `pages` rows cascade via the FK in
    User/supabase/schema.sql (`on delete cascade`) — Storage objects do NOT
    cascade (Storage is a separate system), see repositories/pages.py.
    """
    if not exam_ids:
        return 0
    res = await db.table("exams").delete().in_("id", exam_ids).execute()
    return len(res.data)
