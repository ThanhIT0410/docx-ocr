"""FastAPI dependency providers shared across controllers."""
from __future__ import annotations

from fastapi import Depends, Request
from supabase import AsyncClient

from app.config.settings import Settings, get_settings
from app.services.ocr_client import OcrClient
from app.services.ocr_pipeline import OcrPipelineState
from app.services.queue_service import QueueService
from app.services.result_handler import ResultHandler
from app.storage.client import StorageHelper
from app.supabase_client import get_supabase


async def get_db() -> AsyncClient:
    return await get_supabase()


def get_storage(
    db: AsyncClient = Depends(get_db), settings: Settings = Depends(get_settings)
) -> StorageHelper:
    return StorageHelper(db, settings.supabase_storage_bucket)


def get_queue_service(request: Request) -> QueueService:
    """The single `QueueService` instance created in app/main.py's
    lifespan and stored on `app.state` — not a per-request object, unlike
    `get_storage` above (state is the whole point of a queue)."""
    return request.app.state.queue_service


def get_result_handler(request: Request) -> ResultHandler:
    """Same singleton-on-`app.state` pattern as `get_queue_service` — one
    `ResultHandler` for the process lifetime."""
    return request.app.state.result_handler


def get_ocr_client(request: Request) -> OcrClient:
    """One `OcrClient` for the process lifetime — it wraps `AsyncOpenAI`,
    whose pooled connections are meant to be reused across many calls, not
    rebuilt per request (see app/services/ocr_client.py)."""
    return request.app.state.ocr_client


def get_ocr_pipeline_state(request: Request) -> OcrPipelineState:
    return request.app.state.ocr_pipeline_state
