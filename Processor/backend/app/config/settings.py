"""Runtime settings, loaded from environment variables (prefix PROCESSOR_).

Uses pydantic-settings so every value is validated and typed at startup
instead of failing deep inside a request/worker iteration. See
`.env.example` for the meaning of each field and which ones are unresolved
placeholders.
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PROCESSOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Supabase ---------------------------------------------------------
    supabase_url: str
    supabase_service_role_key: str
    supabase_storage_bucket: str = "exam-pages"
    # Personal access token (account-scoped, NOT the project-scoped
    # service_role key above) — only used by services/supabase_management.py
    # for GET /processor/dashboard's DB/Storage size stats, since PostgREST
    # (what service_role talks to) has no "run arbitrary SQL" endpoint.
    # Optional: dashboard_service.py degrades those 2 stats to None rather
    # than failing the whole endpoint if this is blank.
    supabase_access_token: str = ""

    # --- llama.cpp ----------------------------------------------------------
    llamacpp_base_url: str
    llamacpp_api_key: str = "not-needed"
    llamacpp_model: str

    # --- Auth ---------------------------------------------------------------
    api_key: str
    admin_api_key: str

    # --- Environment gating ---------------------------------------------------
    env: str = Field(default="dev", pattern="^(dev|staging|production)$")

    # --- Worker tuning --------------------------------------------------------
    worker_id: str = ""
    # Cap on how many pages a worker OCRs concurrently within one exam —
    # one `asyncio` coroutine per page, this bounds an `asyncio.Semaphore`
    # (see services/worker_service.py), not a thread pool. No more
    # "batch size" (grouping pages to share one model call) — each page is
    # always its own model call now (see services/ocr_client.py).
    max_concurrent_pages: int = 8
    # Cap on the total number of PAGES admitted into status='processing' at
    # once (checked at enqueue time, see controllers/queue.py — NOT a count
    # of exams: exams vary wildly in page count, so an exam-count cap
    # didn't reflect real load, see app/services/queue_service.py's module
    # docstring). Stays held for an admitted exam's whole 'processing'
    # lifetime, not just while it's waiting in the FIFO — released only
    # when it finishes or fails (QueueService.release). Keeps a burst of
    # enqueue calls from admitting more work than the page-tier worker pool
    # can realistically get through.
    max_concurrent_processing_pages: int = 100
    worker_poll_interval_seconds: float = 5
    stale_claim_timeout_seconds: float = 900
    healthcheck_interval_seconds: float = 10
    ocr_max_attempts: int = 3
    ocr_backoff_base_seconds: float = 2

    # Sent as `temperature` on every chat.completions.create call (see
    # services/ocr_client.py). The prompt itself lives in code
    # (app/prompts.py) rather than here or a YAML file — this is no longer
    # something an operator iterates on live without a restart, since
    # getting the HTML layout-block format right is a prompt-engineering
    # problem best reviewed/tested like any other code change.
    #
    # 0.8, not something closer to greedy: confirmed by direct experiment
    # that low temperature (tried 0.0 and 0.1) makes this model fall into
    # repetition loops on dense/degenerate pages — burning through the
    # entire per-slot context window (ocr_client.py has no `max_tokens`
    # cap, see its module docstring) without ever reaching a natural stop,
    # surfacing as OcrTruncatedError even on pages that should have
    # finished in a fraction of that budget. 0.8 was the value that
    # actually stopped it in testing; not a default picked in the abstract.
    ocr_temperature: float = 0.8

    # Per-image pixel budget enforced by services/preprocessing.py's
    # smart_resize (ported from Qwen2-VL's own image preprocessing) —
    # bounds how many vision tokens each page costs the model. min_pixels =
    # 2048 * 28² patches, max_pixels = 4096 * 28² patches (2048/4096 vision
    # tokens) — raised from Qwen2-VL's own stock defaults (4/14400 patches)
    # after moving to 300 DPI source pages (see ../../../DPI_DEPENDENCIES.md):
    # a 300 DPI A4 page has far more real detail than the old 200 DPI one,
    # so the token budget needed raising to actually use it instead of
    # smart_resize immediately downscaling most of that detail back away.
    min_pixels: int = 1605632
    max_pixels: int = 3211264

    # --- Preprocessing (image quality, §2.3 step 2) ----------------------------
    # No external YAML file for these anymore (§ new) — like everything
    # else in this class, tunable via env/`.env` without a code change.
    preprocess_deskew: bool = True
    preprocess_deskew_max_angle_deg: float = 15.0
    preprocess_enhance_contrast: bool = True
    preprocess_contrast_clip_limit: float = 2.0
    preprocess_contrast_tile_grid_size: int = 8

    # --- Logging --------------------------------------------------------------
    log_level: str = "info"
    log_dir: str = "./.logs"

    # --- Admin HTTP API ---------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8800

    @property
    def resolved_worker_id(self) -> str:
        if self.worker_id:
            return self.worker_id
        return f"{socket.gethostname()}-{__import__('os').getpid()}"

    @property
    def is_destructive_ops_allowed(self) -> bool:
        """Gate for POST /processor/admin/reset — dev/staging only (§3.7, §2.4)."""
        return self.env in ("dev", "staging")

    @property
    def supabase_project_ref(self) -> str:
        """Parsed out of `supabase_url` (`https://<ref>.supabase.co`) rather
        than a separate setting — the Management API's URLs are path-scoped
        by project ref (`/v1/projects/{ref}/...`), and this is the only
        place that ref is ever needed."""
        return urlparse(self.supabase_url).hostname.split(".")[0]


settings = Settings()


def get_settings() -> Settings:
    """Kept as a function (rather than importing `settings` directly) only
    because FastAPI's `Depends(get_settings)` is used across controllers/
    security.py/dependencies.py — that pattern needs a callable. It's not a
    cache: `Settings()` is constructed exactly once, right above, when this
    module is first imported."""
    return settings
