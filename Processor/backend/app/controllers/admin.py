"""HTTP route for POST /processor/admin/reset (§2.4). Requires BOTH the
operator API key and the admin API key (see app/security.py), and is
refused outside dev/staging regardless of auth (§3.7). Business logic
lives in app/services/reset_service.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from supabase import AsyncClient

from app.config.settings import Settings, get_settings
from app.dependencies import get_db, get_storage
from app.schemas.dto import ResetResponse
from app.security import require_admin_api_key, require_api_key
from app.services.reset_service import DestructiveOpsDisabledError, reset_all
from app.storage.client import StorageHelper

router = APIRouter(
    prefix="/processor/admin",
    tags=["admin"],
    dependencies=[Depends(require_api_key), Depends(require_admin_api_key)],
)


@router.post("/reset", response_model=ResetResponse)
async def reset(
    db: AsyncClient = Depends(get_db),
    storage: StorageHelper = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> ResetResponse:
    try:
        return await reset_all(db, storage, destructive_ops_allowed=settings.is_destructive_ops_allowed)
    except DestructiveOpsDisabledError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
