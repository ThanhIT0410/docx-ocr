"""`app/config/settings.py` constructs `Settings()` eagerly at module import
time, so anything that imports `app.controllers.*`/`app.dependencies`
(even indirectly) fails to collect unless every required env var is set —
including ones `.env` currently leaves as TODO placeholders (see
`.env.example`). These are dummy values for import-time validation only;
`os.environ.setdefault` won't override real values if they're ever set in
the actual environment. Must run before any test module imports those
settings, which is exactly what a top-level conftest.py guarantees.
"""
from __future__ import annotations

import os

os.environ.setdefault("PROCESSOR_LLAMACPP_BASE_URL", "http://localhost:8080/v1")
os.environ.setdefault("PROCESSOR_API_KEY", "test-key")
os.environ.setdefault("PROCESSOR_ADMIN_API_KEY", "test-admin-key")
