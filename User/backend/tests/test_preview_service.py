"""Covers `_to_jpeg_bytes` — switched from PyMuPDF's own `Pixmap.tobytes("jpg")`
to Pillow for speed (~100ms -> ~15-18ms per A4@200DPI page, see
requirements.txt), which meant re-doing alpha flattening by hand. That
surfaced a real bug while testing against a real translucent PNG: PyMuPDF's
alpha-carrying pixmaps are *premultiplied*, so naively compositing with
Pillow's `Image.paste(mask=alpha)` (which assumes straight alpha) applies
the alpha weighting twice and produces visibly wrong colors. These tests
pin the correct behavior."""
from __future__ import annotations

import io

import fitz
from PIL import Image

from app.services.preview_service import _to_jpeg_bytes


def test_opaque_rgb_pixmap_roundtrips_color():
    doc = fitz.open()
    page = doc.new_page(width=100, height=100)
    page.draw_rect(fitz.Rect(0, 0, 100, 100), color=(0, 1, 0), fill=(0, 1, 0))
    pix = page.get_pixmap()

    jpeg_bytes = _to_jpeg_bytes(pix)
    img = Image.open(io.BytesIO(jpeg_bytes))
    assert img.mode == "RGB"
    r, g, b = img.getpixel((50, 50))
    # JPEG is lossy even on a flat color (chroma subsampling) — allow a
    # couple of units of drift rather than requiring a bit-exact match.
    assert (r, g, b) != (0, 0, 0)
    assert max(abs(r - 0), abs(g - 255), abs(b - 0)) <= 3
    doc.close()


def test_translucent_png_composites_correctly_onto_white():
    """A (255, 0, 0, 128) source pixel — 50% red over white should come out
    ~(255, 127, 127), not the ~(191, 127, 127) the premultiplied-alpha bug
    produced (verified by hand against real PyMuPDF output before fixing)."""
    src = Image.new("RGBA", (20, 20), (255, 0, 0, 128))
    buf = io.BytesIO()
    src.save(buf, format="PNG")

    pix = fitz.Pixmap(buf.getvalue())
    assert pix.alpha  # sanity: this PNG really does carry alpha

    jpeg_bytes = _to_jpeg_bytes(pix)
    img = Image.open(io.BytesIO(jpeg_bytes))
    assert img.mode == "RGB"
    r, g, b = img.getpixel((10, 10))
    assert abs(r - 255) <= 3
    assert abs(g - 127) <= 3
    assert abs(b - 127) <= 3


def test_grayscale_pixmap_encodes_without_error():
    doc = fitz.open()
    page = doc.new_page(width=50, height=50)
    pix = page.get_pixmap(colorspace=fitz.csGRAY)
    assert pix.n == 1

    jpeg_bytes = _to_jpeg_bytes(pix)
    img = Image.open(io.BytesIO(jpeg_bytes))
    assert img.size == (50, 50)
    doc.close()
