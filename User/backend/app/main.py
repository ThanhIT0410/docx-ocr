from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.cleanup import purge_old_previews
from app.config import settings
from app.controllers import export, preview

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    purge_old_previews(settings.data_dir)
    yield


app = FastAPI(title="DocxOCR Sidecar", version="0.1.0", lifespan=lifespan)

# Bind is 127.0.0.1-only at the uvicorn.run() level (see __main__ below);
# this CORS rule is the second layer, restricting *which* local page may
# call in (architecture_and_requirements.md §10).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.allowed_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(preview.router)
app.include_router(export.router)


@app.get("/health")
def health() -> dict:
    """Polled by electron/sidecar.js right after spawn to know when the
    process is actually ready to serve requests (model/deps import time can
    dominate startup — see architecture_and_requirements.md §10)."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=settings.port, log_level=settings.log_level)
