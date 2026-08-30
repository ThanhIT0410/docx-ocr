"""Covers `_to_png_bytes` — switched from PyMuPDF's own `Pixmap.tobytes("png")`
to Pillow for speed (~100ms -> ~15-18ms per A4@200DPI page, see
requirements.txt), which meant re-doing alpha flattening by hand. That
surfaced a real bug while testing against a real translucent PNG: PyMuPDF's
alpha-carrying pixmaps are *premultiplied*, so naively compositing with
Pillow's `Image.paste(mask=alpha)` (which assumes straight alpha) applies
the alpha weighting twice and produces visibly wrong colors. These tests
pin the correct behavior. PNG is lossless, so — unlike when this wrote
JPEG — pixel values are asserted exactly rather than within a tolerance."""
from __future__ import annotations

import io

import fitz
from PIL import Image

from app.services.preview_service import _to_png_bytes


def test_opaque_rgb_pixmap_roundtrips_color():
    doc = fitz.open()
    page = doc.new_page(width=100, height=100)
    page.draw_rect(fitz.Rect(0, 0, 100, 100), color=(0, 1, 0), fill=(0, 1, 0))
    pix = page.get_pixmap()

    png_bytes = _to_png_bytes(pix)
    img = Image.open(io.BytesIO(png_bytes))
    assert img.mode == "RGB"
    assert img.getpixel((50, 50)) == (0, 255, 0)
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

    png_bytes = _to_png_bytes(pix)
    img = Image.open(io.BytesIO(png_bytes))
    assert img.mode == "RGB"
    r, g, b = img.getpixel((10, 10))
    assert abs(r - 255) <= 1
    assert abs(g - 127) <= 1
    assert abs(b - 127) <= 1


def test_grayscale_pixmap_encodes_without_error():
    doc = fitz.open()
    page = doc.new_page(width=50, height=50)
    pix = page.get_pixmap(colorspace=fitz.csGRAY)
    assert pix.n == 1

    png_bytes = _to_png_bytes(pix)
    img = Image.open(io.BytesIO(png_bytes))
    assert img.size == (50, 50)
    doc.close()
