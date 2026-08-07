"""Structured logging setup (§3.8 "Giám sát & ghi log").

Logs go to stdout (JSON — easy to ship to any log collector later) and to a
rotating file under `settings.log_dir`, mirroring the User sidecar's
"ghi log lỗi ra file cục bộ" approach since there is no centralized log
system yet.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from pythonjsonlogger.json import JsonFormatter

from app.config.settings import Settings


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "processor.log", maxBytes=10_000_000, backupCount=5
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [stream_handler, file_handler]

    # Quiet down noisy third-party loggers unless we're debugging.
    if level > logging.DEBUG:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
