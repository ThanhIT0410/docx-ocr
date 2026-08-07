"""Thin wrapper around the supabase-py Storage client (`storage3`).

Only the operations Processor needs are exposed here, so call sites don't
depend on storage3 internals directly.
"""
from __future__ import annotations

import logging

from storage3.exceptions import StorageApiError
from supabase import AsyncClient

logger = logging.getLogger(__name__)


class StorageHelper:
    def __init__(self, db: AsyncClient, bucket: str):
        self._bucket = db.storage.from_(bucket)

    async def exists(self, path: str) -> bool:
        return await self._bucket.exists(path)

    async def download(self, path: str) -> bytes:
        return await self._bucket.download(path)

    async def create_signed_urls(self, paths: list[str], expires_in: int) -> dict[str, str | None]:
        """One batched call to Supabase Storage for however many paths are
        given (its own `create_signed_urls` — plural — already accepts a
        list), instead of one request per page — see
        controllers/exams.py's preview endpoint. `None` for a path means
        that one file's signing failed (e.g. object missing); doesn't
        raise, since one bad page shouldn't blank out preview for the rest
        of the exam."""
        if not paths:
            return {}
        results = await self._bucket.create_signed_urls(paths, expires_in)
        return {item["path"]: item["signedURL"] for item in results if item.get("path") is not None}

    async def remove(self, paths: list[str]) -> tuple[int, list[str]]:
        """Deletes in batches of 100 (Supabase Storage's per-request cap on
        `remove`). Returns (deleted_count, failed_paths) instead of raising,
        since admin reset (§2.4) needs an accurate count even on partial
        failure ("trả về xác nhận rõ ràng số lượng ... đã xóa")."""
        deleted = 0
        failed: list[str] = []
        for i in range(0, len(paths), 100):
            chunk = paths[i : i + 100]
            try:
                result = await self._bucket.remove(chunk)
                deleted += len(result)
                returned_paths = {item.get("name") for item in result}
                failed.extend(p for p in chunk if p not in returned_paths)
            except StorageApiError as exc:
                logger.error("storage remove failed for chunk", extra={"error": str(exc)})
                failed.extend(chunk)
        return deleted, failed
