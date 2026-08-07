"""PyInstaller entrypoint — kept separate from app/main.py because PyInstaller
needs a plain top-level script, not a `python -m app.main` invocation.
`electron/sidecar.js` spawns the built `ocr-backend[.exe]` binary produced
from this file; dev mode instead runs `python -m app.main` directly.
"""
import uvicorn

from app.config import settings
from app.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=settings.port, log_level=settings.log_level)
