"""Maps a page's original pixel dimensions to the nearest standard paper
format, and resizes its layout bboxes to match — ported near-verbatim from
`Reformat_prototype/page_resize_utils.py` (old system's
`PageFormatNormalizer`). Algorithm unchanged; only the type hint for
`resize()`'s `page` argument points at our own `OcrPageResult` (see
app/schemas.py) instead of the old system's `OCRResult`, and — unlike the
prototype — `SUPPORTED_FORMATS`' pixel dimensions are now *derived* from
each paper size's physical dimensions (inches) at `RENDER_DPI`, rather than
hardcoded pixel pairs computed once by hand at whatever DPI
`preview_service.py` used at the time. See ../../../DPI_DEPENDENCIES.md
finding #1 — this was previously a silent "these numbers happen to agree
with RENDER_DPI's output" coupling with no shared source, exactly the kind
of thing that goes wrong the next time DPI changes.
"""
from __future__ import annotations

from app.schemas import OcrPageResult
from app.services.preview_service import RENDER_DPI

# Physical paper sizes in inches (portrait) — the "envelope" entry isn't a
# real standard envelope size, it's the exact ratio/size the original
# prototype's (866, 1732)-at-200-DPI constant implied (866/200, 1732/200);
# kept as-is rather than "corrected" to a real envelope spec, since this
# migration is only about following RENDER_DPI, not changing behavior.
_PAPER_SIZES_IN = {
    "a4": (8.2677, 11.6929),  # 210mm x 297mm
    "letter": (8.5, 11.0),
    "legal": (8.5, 14.0),
    "envelope": (4.33, 8.66),
}

SUPPORTED_FORMATS = {
    name: (round(width_in * RENDER_DPI), round(height_in * RENDER_DPI))
    for name, (width_in, height_in) in _PAPER_SIZES_IN.items()
}

SUPPORTED_ORIENTATIONS = {
    "portrait",
    "landscape",
}


class PageFormatNormalizer:
    def __init__(
        self,
        allowed_formats: list[str] | None = None,
        allowed_orientations: list[str] | None = None,
    ):
        self.allowed_formats = set(allowed_formats or SUPPORTED_FORMATS.keys())
        self.allowed_orientations = set(allowed_orientations or SUPPORTED_ORIENTATIONS)
        self._validate()

    def _validate(self):
        if not self.allowed_formats or not self.allowed_formats.issubset(SUPPORTED_FORMATS):
            raise ValueError(f"Unsupported formats: {self.allowed_formats}")
        if not self.allowed_orientations or not self.allowed_orientations.issubset(SUPPORTED_ORIENTATIONS):
            raise ValueError(f"Unsupported orientations: {self.allowed_orientations}")

    def resize(
        self,
        page: OcrPageResult,
        target_width: int = SUPPORTED_FORMATS["a4"][0],
        target_height: int = SUPPORTED_FORMATS["a4"][1],
    ) -> OcrPageResult:
        if not page.origin_width or not page.origin_height:
            return page
        x_scale = target_width / page.origin_width
        y_scale = target_height / page.origin_height
        for layout in page.layouts:
            x_min, y_min, x_max, y_max = layout.bbox
            layout.bbox = [
                int(round(x_min * x_scale)),
                int(round(y_min * y_scale)),
                int(round(x_max * x_scale)),
                int(round(y_max * y_scale)),
            ]
        page.origin_width = target_width
        page.origin_height = target_height
        return page

    def find_nearest_format(self, width: int, height: int):
        orientation = self.check_orientation(width, height)
        input_ratio = min(width, height) / max(width, height)
        best_format = min(
            self.allowed_formats,
            key=lambda fmt: abs(input_ratio - (min(SUPPORTED_FORMATS[fmt]) / max(SUPPORTED_FORMATS[fmt]))),
        )
        target_width, target_height = SUPPORTED_FORMATS[best_format]
        ratio_diff = abs(input_ratio - target_width / target_height)
        if orientation == "landscape":
            target_width, target_height = target_height, target_width
        return best_format, orientation, target_width, target_height, ratio_diff

    def check_orientation(self, width: int, height: int):
        if len(self.allowed_orientations) == 1:
            return next(iter(self.allowed_orientations))
        elif height < width:
            return "landscape"
        else:
            return "portrait"


def union_bbox(bboxes: list[list[float]]) -> list[float] | None:
    if not bboxes:
        return None
    x1 = min(b[0] for b in bboxes)
    y1 = min(b[1] for b in bboxes)
    x2 = max(b[2] for b in bboxes)
    y2 = max(b[3] for b in bboxes)
    return [x1, y1, x2, y2]
