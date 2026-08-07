"""Per-page image preprocessing (§2.3 step 2): resize, contrast, deskew.

Uses OpenCV rather than pure Pillow because deskew (minAreaRect on a
thresholded contour) and CLAHE contrast enhancement are both native cv2
operations with no equivalent Pillow one-liner. Resize always runs (it's
the model's input contract, not a quality choice); deskew/contrast are
individually toggleable via PROCESSOR_PREPROCESS_* env vars (settings.py)
since neither has been tuned against real scans yet (see DESIGN_REPORT.md §7).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from app.config.settings import Settings

# Qwen2-VL's vision patch size (14) * spatial merge size (2) — the model
# tiles the image into patches of this many pixels per side, so both output
# dimensions must be a multiple of it regardless of what min/max_pixels are
# set to. This is a property of the model architecture, not a quality knob,
# so it's a constant here rather than an env var (min/max_pixels ARE
# configurable, see PROCESSOR_MIN_PIXELS/MAX_PIXELS).
RESIZE_FACTOR = 28


@dataclass
class PreprocessResult:
    content: bytes
    origin_width: int
    origin_height: int
    input_width: int
    input_height: int


def preprocess_image(image_bytes: bytes, settings: Settings) -> PreprocessResult:
    """`origin_*` is the page's raw size before any resizing (what the
    reformat/export pipeline needs later); `input_*` is the size actually
    sent to the model after smart_resize (what OcrClient needs to
    denormalize `data-bbox` correctly) — see schemas/models.py:OcrPageResult."""
    img = _decode(image_bytes)
    origin_height, origin_width = img.shape[:2]

    img = smart_resize(img, settings.min_pixels, settings.max_pixels)
    input_height, input_width = img.shape[:2]

    if settings.preprocess_deskew:
        img = _deskew(img, settings.preprocess_deskew_max_angle_deg)
    if settings.preprocess_enhance_contrast:
        img = _enhance_contrast(
            img, settings.preprocess_contrast_clip_limit, settings.preprocess_contrast_tile_grid_size
        )

    return PreprocessResult(
        content=_encode_jpeg(img),
        origin_width=origin_width,
        origin_height=origin_height,
        input_width=input_width,
        input_height=input_height,
    )


def _decode(image_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode image — unsupported format or corrupt data")
    return img


def _snap(value: float, factor: int, rounding: Callable[[float], float]) -> int:
    """Rounds `value` to the nearest multiple of `factor`, via whichever of
    round/floor/ceil the caller needs."""
    return int(rounding(value / factor)) * factor


def smart_resize(
    img: np.ndarray, min_pixels: int, max_pixels: int, factor: int = RESIZE_FACTOR
) -> np.ndarray:
    """Ported from Qwen2-VL's image preprocessing (`qwen_vl_utils`) so the
    image dimensions sent to the model match exactly what it was trained to
    expect. Picks target dimensions so that: both are divisible by `factor`,
    the total pixel count lands in [min_pixels, max_pixels], and the aspect
    ratio is preserved as closely as those two constraints allow — then
    resizes `img` to that target (or returns it unchanged if already there).
    """
    height, width = img.shape[:2]
    if max(height, width) / min(height, width) > 200:
        raise ValueError(
            f"absolute aspect ratio must be smaller than 200, got {max(height, width) / min(height, width)}"
        )
    h_bar = max(factor, _snap(height, factor, round))
    w_bar = max(factor, _snap(width, factor, round))
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = max(factor, _snap(height / beta, factor, math.floor))
        w_bar = max(factor, _snap(width / beta, factor, math.floor))
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = _snap(height * beta, factor, math.ceil)
        w_bar = _snap(width * beta, factor, math.ceil)
        if h_bar * w_bar > max_pixels:  # max_pixels first to control the token length
            beta = math.sqrt((h_bar * w_bar) / max_pixels)
            h_bar = max(factor, _snap(h_bar / beta, factor, math.floor))
            w_bar = max(factor, _snap(w_bar / beta, factor, math.floor))

    if (h_bar, w_bar) == (height, width):
        return img
    # Standard OpenCV convention: area averaging when shrinking, cubic when growing.
    interpolation = cv2.INTER_AREA if h_bar * w_bar < height * width else cv2.INTER_CUBIC
    return cv2.resize(img, (w_bar, h_bar), interpolation=interpolation)


def _deskew(img: np.ndarray, max_angle_deg: float) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = cv2.findNonZero(thresh)
    if coords is None:
        return img

    angle = cv2.minAreaRect(coords)[-1]
    # cv2.minAreaRect returns an angle in [-90, 0); normalize to the
    # nearest-to-horizontal correction, e.g. -85 deg means "rotate +5".
    if angle < -45:
        angle = 90 + angle
    if abs(angle) > max_angle_deg or abs(angle) < 0.1:
        # Either not a rotation this heuristic should touch (too large —
        # more likely a bad contour than real skew) or negligible.
        return img

    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        img, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _enhance_contrast(img: np.ndarray, clip_limit: float, tile_grid_size: int) -> np.ndarray:
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_grid_size, tile_grid_size))
    l_channel = clahe.apply(l_channel)
    merged = cv2.merge((l_channel, a_channel, b_channel))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def _encode_jpeg(img: np.ndarray, quality: int = 92) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("Failed to encode preprocessed image as JPEG")
    return buf.tobytes()
