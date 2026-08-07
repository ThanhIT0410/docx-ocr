# DocxOCR — Backend sidecar

FastAPI service handling PDF page-splitting (PyMuPDF) and result export.
Never runs standalone in production — Electron's main process spawns it
(`../frontend/electron/sidecar.js`) and owns its whole lifecycle. See
`../architecture_and_requirements.md` §9.2 and §10 for the full contract.

## Setup (dev)

Pin the interpreter to **Python 3.11 or 3.12**. Do not use the system's
3.13 here — see the design report's "Dependency versions" section for why
(PyTorch wheel availability at the time this was written).

```
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run standalone (without Electron)

Config is read from environment variables (`app/config.py`, prefix `OCR_`) —
copy `.env.example` to `.env`, or set them inline:

```
OCR_PORT=8756 OCR_DATA_DIR=.devdata OCR_ALLOWED_ORIGIN=http://localhost:3000 python -m app.main
```

```
python -m app.main
```

## Build the packaged binary

```
pyinstaller pyinstaller.spec
```

Produces `dist/ocr-backend/` (onedir), which `frontend/electron-builder.yml`
copies into the Electron installer's `resources/backend/`. Run this before
`npm run electron:build` in `../frontend`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Readiness probe polled right after spawn |
| POST | `/preview` | Split a PDF / stage images for one "đề" |
| GET | `/preview` | List previews left over from a killed session |
| GET | `/preview/{id}/pages/{pageId}` | Page image bytes (thumbnail/full) |
| PATCH | `/preview/{id}` | Rename / reorder / delete pages |
| DELETE | `/preview/{id}` | Discard a preview entirely |
| POST | `/export` | Compose the final export file (stubbed pipeline) |
