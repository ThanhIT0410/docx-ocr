"""Startup housekeeping for the local preview directory (§10 "Dọn dẹp")."""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger("ocr_backend.cleanup")

DEFAULT_MAX_AGE_DAYS = 7


def purge_old_previews(data_dir: str, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> None:
    root = Path(data_dir) / "previews"
    if not root.exists():
        return

    cutoff = time.time() - max_age_days * 86400
    for child in root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                logger.info("Purged stale preview %s", child.name)
        except OSError:
            continue
