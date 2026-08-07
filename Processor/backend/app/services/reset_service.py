"""POST /processor/admin/reset (§2.4) — irreversible, dev/staging only.

Order: collect everything that needs deleting *before* deleting anything
(so a failure partway through doesn't leave us unable to compute what's
left), delete Storage objects, then delete DB rows. Storage is deleted
first here (opposite of enqueue's "DB reflects reality" ordering) because
there's no resume concern for a full wipe — worst case a retry finds fewer
objects/rows than last time, which is exactly what "delete everything"
should converge to.
"""
from __future__ import annotations

import asyncio

from supabase import AsyncClient

from app.repositories import exams, pages
from app.schemas.dto import ResetResponse
from app.storage.client import StorageHelper


class DestructiveOpsDisabledError(Exception):
    """POST /processor/admin/reset attempted outside dev/staging."""


async def reset_all(db: AsyncClient, storage: StorageHelper, *, destructive_ops_allowed: bool) -> ResetResponse:
    if not destructive_ops_allowed:
        raise DestructiveOpsDisabledError(
            "admin/reset is disabled outside dev/staging (PROCESSOR_ENV)"
        )

    # Independent reads — collect both concurrently before deleting anything.
    file_paths, exam_ids = await asyncio.gather(
        pages.list_file_paths(db), exams.list_all_exam_ids(db)
    )

    storage_deleted, storage_failed = await storage.remove(file_paths) if file_paths else (0, [])

    # pages rows cascade-delete via the FK in User/supabase/schema.sql.
    exams_deleted = await exams.delete_exams(db, exam_ids)

    return ResetResponse(
        exams_deleted=exams_deleted,
        pages_deleted=len(file_paths),
        storage_objects_deleted=storage_deleted,
        storage_objects_failed=storage_failed,
    )
