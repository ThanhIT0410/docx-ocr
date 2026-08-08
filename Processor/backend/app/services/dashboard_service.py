"""Assembles GET /processor/dashboard — the consolidated ops view that
replaces the old GET /processor/worker/status (deleted, see
controllers/worker_status.py's removal) plus new health/DB/storage stats.
Kept as a service function (not inline in the controller) per this repo's
controllers-have-no-business-logic rule.

`db` (supabase-py `AsyncClient`), the llama.cpp health check, and the 2
Management API calls below are all independent of each other (none needs
another's result) — 9 network round-trips in total if awaited one after
another. `asyncio.gather` runs them concurrently instead, cutting this
endpoint's latency to roughly its slowest single call.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone

from supabase import AsyncClient

from app.config.settings import Settings
from app.constants import ALL_STATUSES, STATUS_FAILED, STATUS_FINISHED
from app.repositories import exams
from app.schemas.dto import DashboardResponse, StatusCounts
from app.services import supabase_management
from app.services.health_check import check_llamacpp_health
from app.services.queue_service import QueueService


async def get_dashboard(db: AsyncClient, settings: Settings, queue: QueueService) -> DashboardResponse:
    today_start = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)

    (
        llamacpp_healthy,
        finished_today,
        failed_today,
        status_counts,
        db_size_bytes,
        storage_size_bytes,
    ) = await asyncio.gather(
        check_llamacpp_health(settings.llamacpp_base_url),
        exams.count_by_status_since(db, STATUS_FINISHED, today_start.isoformat()),
        exams.count_by_status_since(db, STATUS_FAILED, today_start.isoformat()),
        _gather_status_counts(db),
        supabase_management.get_db_size_bytes(settings),
        supabase_management.get_storage_size_bytes(settings, settings.supabase_storage_bucket),
    )

    return DashboardResponse(
        llamacpp_healthy=llamacpp_healthy,
        finished_today=finished_today,
        failed_today=failed_today,
        counts=status_counts,
        db_size_bytes=db_size_bytes,
        storage_size_bytes=storage_size_bytes,
        processing_pages=queue.admitted_pages,
        processing_pages_limit=queue.max_size,
    )


async def _gather_status_counts(db: AsyncClient) -> StatusCounts:
    counts = await asyncio.gather(*(exams.count_by_status(db, status) for status in ALL_STATUSES))
    return StatusCounts(**dict(zip(ALL_STATUSES, counts)))
