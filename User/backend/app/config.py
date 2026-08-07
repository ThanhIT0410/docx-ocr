"""Sidecar settings, read from environment variables (prefix OCR_).

Electron sets these directly on the spawned child process's environment
before launch (see frontend/electron/sidecar.js) — the port is chosen
freshly every run to avoid conflicts, so it must come from *this specific
process's* environment, never a shared/parsed-once default. Env vars only
matter for manual/standalone runs otherwise (see .env.example).
"""
from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OCR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    port: int = 8756
    data_dir: str = "./.devdata"
    allowed_origin: str = "http://localhost:3000"
    log_level: str = "info"


settings = Settings()
os.makedirs(settings.data_dir, exist_ok=True)
