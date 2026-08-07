"""Admin/monitoring API entrypoint (§2.1, §2.2, §2.4, §3.8).

Dev: `python -m app.main`. Production: `uvicorn app.main:app --host ...`
(see the `__main__` block below, mirroring User/backend/app/main.py).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config.logging import configure_logging
from app.config.settings import get_settings
from app.constants import ADMIN_API_KEY_HEADER, API_KEY_HEADER
from app.controllers import admin, dashboard, exams, ocr, queue
from app.services.ocr_client import OcrClient
from app.services.ocr_pipeline import OcrPipelineState
from app.services.queue_service import QueueService
from app.services.recovery import recover_processing_exams
from app.services.result_handler import ResultHandler
from app.supabase_client import get_supabase

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(settings)
    # In-memory, one per process — see services/queue_service.py's and
    # services/result_handler.py's module docstrings for why these aren't
    # persisted/shared across processes.
    db = await get_supabase()
    _app.state.queue_service = QueueService(max_size=settings.max_concurrent_processing)
    _app.state.result_handler = ResultHandler(db)
    # One OcrClient for the process lifetime — see get_ocr_client's
    # docstring for why (pooled AsyncOpenAI connections).
    _app.state.ocr_client = OcrClient(
        base_url=settings.llamacpp_base_url,
        api_key=settings.llamacpp_api_key,
        model=settings.llamacpp_model,
        max_attempts=settings.ocr_max_attempts,
        backoff_base_seconds=settings.ocr_backoff_base_seconds,
        temperature=settings.ocr_temperature,
        max_tokens=settings.ocr_max_tokens,
    )
    _app.state.ocr_pipeline_state = OcrPipelineState()
    # Exams left 'processing' from a run that got closed mid-OCR — put
    # them back in the queue before serving any requests (§ new, see
    # services/recovery.py).
    await recover_processing_exams(db, _app.state.queue_service)
    yield


app = FastAPI(
    title="DocxOCR — Processor backend",
    description="Admin/monitoring API for the OCR Processor (see backend_requirements.md).",
    lifespan=lifespan,
)

# Processor/frontend (Electron) is a real browser (Chromium) renderer calling
# this API with fetch — unlike the rest of this service, that call IS
# same-machine-but-cross-origin, so it needs CORS. Loopback-only regex, not
# `allow_origins=["*"]`: `localhost:3000` covers `nuxt dev`, `127.0.0.1:\d+`
# covers the packaged app's static server (electron/staticServer.js picks a
# random free port every launch, same pattern as User/frontend's sidecar).
# Doesn't weaken §3.7 "internal network only" — both patterns are loopback.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost:3000|127\.0\.0\.1:\d+)$",
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", API_KEY_HEADER, ADMIN_API_KEY_HEADER],
)

app.include_router(exams.router)
app.include_router(admin.router)
app.include_router(dashboard.router)
app.include_router(queue.router)
app.include_router(ocr.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness of this API process itself (no auth — meant for a
    container/process-manager healthcheck). llama.cpp health is a
    worker-internal concern (§3.4), not exposed here."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level,
    )
