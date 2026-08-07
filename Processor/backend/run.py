"""PyInstaller entrypoint for the admin API (uvicorn). One process now —
OCR runs as a background task inside this same process, started via
POST /processor/ocr/start (see app/services/ocr_pipeline.py), not a
separate worker process/binary mode like an earlier version of this
backend had. `Processor/frontend/electron/sidecar.js` spawns the built
`ocr-processor-backend[.exe]` with no arguments.

Imports the `app`/`main` objects directly rather than uvicorn's
`"app.main:app"` string form — same reasoning as
`User/backend/run.py`: string-based module resolution is less reliable
inside a frozen PyInstaller binary than a plain Python import.
"""
from __future__ import annotations


def main() -> None:
    import uvicorn

    from app.config.settings import get_settings
    from app.main import app

    settings = get_settings()
    uvicorn.run(app, host=settings.api_host, port=settings.api_port, log_level=settings.log_level)


if __name__ == "__main__":
    main()
