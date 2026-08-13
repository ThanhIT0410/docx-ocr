"""HTTP route for GET /processor/dashboard (§3.8 monitoring + new ops
overview). Replaces GET /processor/worker/status (deleted) — business logic
lives in app/services/dashboard_service.py, this module only translates
HTTP <-> that service."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from supabase import AsyncClient

from app.config.settings import Settings, get_settings
from app.dependencies import get_db, get_queue_service
from app.schemas.dto import DashboardResponse
from app.security import require_api_key
from app.services.dashboard_service import get_dashboard
from app.services.queue_service import QueueService

router = APIRouter(prefix="/processor/dashboard", tags=["dashboard"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=DashboardResponse)
async def dashboard(
    db: AsyncClient = Depends(get_db),
    settings: Settings = Depends(get_settings),
    queue: QueueService = Depends(get_queue_service),
) -> DashboardResponse:
    return await get_dashboard(db, settings, queue)
