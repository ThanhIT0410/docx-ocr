"""PDF page-splitting + preview business logic (§9.2 of architecture_and_requirements.md).

Disk layout: {data_dir}/previews/{previewId}/
  index.json   -- { previewId, title, createdAt, pages: [{id, order, source, file}] }
  {page.file}  -- always a `{uuid}.jpg` — every page is normalized to real
                  JPEG on the way in (split PDF pages are rendered straight
                  to JPEG; picked images are re-encoded unless already
                  JPEG), so the on-disk extension is never a lie and the
                  Supabase object key built in stores/upload.ts (which is
                  always `.jpg`) matches the actual bytes.

One JSON file per preview keeps this crash-safe and trivially recoverable
(GET /preview, §9.2 "phục vụ trường hợp người dùng tắt ứng dụng giữa chừng")
without needing a database for what is, by design, purely transient state —
everything here is deleted once the exam is submitted to Supabase.
"""
from __future__ import annotations

import io
import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from PIL import Image

from app.config import settings

RENDER_DPI = 200
JPEG_QUALITY = 90

# Pixmap.n (bytes/pixel, alpha included) -> matching Pillow mode. Anything
# else (e.g. CMYK, which get_pixmap() never produces without an explicit
# colorspace= argument — this file never passes one) falls back to letting
# PyMuPDF itself normalize to RGB first, rather than guessing a Pillow mode.
_PIL_MODE_BY_N = {1: "L", 2: "LA", 3: "RGB", 4: "RGBA"}


def _to_jpeg_bytes(pix: fitz.Pixmap) -> bytes:
    """Encodes via Pillow, not PyMuPDF's own `Pixmap.tobytes("jpg")` — same
    input pixels, measured ~5-7x faster (~100ms vs ~15-18ms per A4 @ 200 DPI
    page after warmup) — this was the actual bottleneck behind slow PDF
    splitting (`create()` below calls this once per page, sequentially).
    JPEG has no alpha channel, so an alpha-carrying pixmap (e.g. a PNG
    upload with transparency) is flattened onto white rather than left to
    error out inside Pillow's JPEG encoder. PyMuPDF's `pix.samples` for an
    alpha-carrying pixmap is *premultiplied* (verified directly: a
    (255,0,0,128) source pixel comes back as raw samples (128,0,0,128), i.e.
    RGB already scaled by alpha/255) — compositing that with Pillow's
    `Image.paste(mask=alpha)`, which assumes straight (non-premultiplied)
    alpha, applies the alpha weighting twice and produces visibly wrong
    colors in translucent areas. The correct "premultiplied over background"
    formula is `premult_rgb + bg * (1 - alpha)` (no un-premultiplying
    needed), done here via numpy since PIL has no built-in for it."""
    mode = _PIL_MODE_BY_N.get(pix.n)
    if mode is None:
        pix = fitz.Pixmap(fitz.csRGB, pix)
        mode = "RGBA" if pix.alpha else "RGB"

    img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    if "A" in mode:
        arr = np.asarray(img).astype(np.float32)
        premult_rgb, alpha = arr[..., :-1], arr[..., -1:] / 255.0
        composited = np.clip(premult_rgb + 255.0 * (1.0 - alpha), 0, 255).astype(np.uint8)
        img = Image.fromarray(composited, mode=mode[:-1])

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


@dataclass
class PreviewPage:
    id: str
    order: int
    source: str
    file: str


@dataclass
class Preview:
    previewId: str
    title: str
    createdAt: str
    pages: list[PreviewPage] = field(default_factory=list)


class PreviewNotFoundError(Exception):
    pass


class PreviewService:
    def __init__(self, data_dir: str):
        self.root = Path(data_dir) / "previews"
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, preview_id: str) -> Path:
        return self.root / preview_id

    def _index_path(self, preview_id: str) -> Path:
        return self._dir(preview_id) / "index.json"

    def _load(self, preview_id: str) -> Preview:
        path = self._index_path(preview_id)
        if not path.exists():
            raise PreviewNotFoundError(preview_id)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["pages"] = [PreviewPage(**p) for p in raw["pages"]]
        return Preview(**raw)

    def _save(self, preview: Preview) -> None:
        self._index_path(preview.previewId).write_text(
            json.dumps(asdict(preview), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def create(self, title: str, uploads: list[tuple[str, bytes, str]]) -> Preview:
        """uploads: list of (filename, content_bytes, content_type)."""
        preview_id = str(uuid.uuid4())
        preview_dir = self._dir(preview_id)
        preview_dir.mkdir(parents=True, exist_ok=True)

        pages: list[PreviewPage] = []
        order = 1
        for filename, content, content_type in uploads:
            is_pdf = content_type == "application/pdf" or filename.lower().endswith(".pdf")
            if is_pdf:
                doc = fitz.open(stream=content, filetype="pdf")
                zoom = RENDER_DPI / 72
                matrix = fitz.Matrix(zoom, zoom)
                for page in doc:
                    jpeg_bytes = _to_jpeg_bytes(page.get_pixmap(matrix=matrix))
                    page_id = str(uuid.uuid4())
                    file_name = f"{page_id}.jpg"
                    (preview_dir / file_name).write_bytes(jpeg_bytes)
                    pages.append(PreviewPage(id=page_id, order=order, source=filename, file=file_name))
                    order += 1
                doc.close()
            else:
                # Already JPEG: keep the original bytes as-is (no lossy
                # re-encode for nothing). Anything else (PNG today — the
                # only other type the upload UI accepts) gets decoded and
                # re-encoded to JPEG so every page on disk is the same
                # lightweight format regardless of what was uploaded.
                is_jpeg = content_type == "image/jpeg" or filename.lower().endswith((".jpg", ".jpeg"))
                jpeg_bytes = content if is_jpeg else _to_jpeg_bytes(fitz.Pixmap(content))
                page_id = str(uuid.uuid4())
                file_name = f"{page_id}.jpg"
                (preview_dir / file_name).write_bytes(jpeg_bytes)
                pages.append(PreviewPage(id=page_id, order=order, source=filename, file=file_name))
                order += 1

        preview = Preview(
            previewId=preview_id,
            title=title,
            createdAt=datetime.now(timezone.utc).isoformat(),
            pages=pages,
        )
        self._save(preview)
        return preview

    def page_file(self, preview_id: str, page_id: str) -> Path:
        preview = self._load(preview_id)
        for p in preview.pages:
            if p.id == page_id:
                return self._dir(preview_id) / p.file
        raise PreviewNotFoundError(f"{preview_id}/{page_id}")

    def patch(
        self,
        preview_id: str,
        title: str | None,
        page_order: list[str] | None,
        delete_page_ids: list[str] | None,
    ) -> Preview:
        preview = self._load(preview_id)

        if delete_page_ids:
            to_delete = set(delete_page_ids)
            kept = []
            for p in preview.pages:
                if p.id in to_delete:
                    (self._dir(preview_id) / p.file).unlink(missing_ok=True)
                else:
                    kept.append(p)
            preview.pages = kept

        if page_order:
            by_id = {p.id: p for p in preview.pages}
            ordered = [by_id[i] for i in page_order if i in by_id]
            remaining = [p for p in preview.pages if p.id not in set(page_order)]
            preview.pages = ordered + remaining
            for i, p in enumerate(preview.pages, start=1):
                p.order = i

        if title is not None:
            preview.title = title

        self._save(preview)
        return preview

    def delete(self, preview_id: str) -> None:
        shutil.rmtree(self._dir(preview_id), ignore_errors=True)

    def list_all(self) -> list[Preview]:
        if not self.root.exists():
            return []
        result = []
        for child in self.root.iterdir():
            if (child / "index.json").exists():
                try:
                    result.append(self._load(child.name))
                except Exception:
                    continue
        return result


# One process = one sidecar = one instance, so a plain module-level
# singleton is enough — no app.state/DI plumbing needed for a single-user
# local process. Imported by app/controllers/preview.py.
service = PreviewService(settings.data_dir)
