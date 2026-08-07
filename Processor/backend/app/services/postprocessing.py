"""Turns the model's raw HTML response (per app/prompts.py's OCR_PROMPT)
into `list[DocumentLayout]`. Ported from the old system's
`post_process_html_response`/`clean_html` — trimmed to this system's needs:
one image per model call (no more `prompt_mode` branch for a non-layout
plain-OCR mode), no PIL dependency (this system decodes with OpenCV, see
preprocessing.py, so callers already have plain width/height ints instead
of a `PIL.Image`).
"""
from __future__ import annotations

import logging
import re
from html import unescape

from bs4 import BeautifulSoup

from app.schemas.models import DocumentLayout

logger = logging.getLogger(__name__)

# Every value here is one of app/prompts.py's own allowed labels — this is a
# defensive normalizer (models don't always follow instructions to the
# letter), not a translation from some other OCR system's vocabulary, so
# entries the old system carried for other backends (Dots-OCR/Chandra-OCR
# specific spellings) are kept only where they map onto our label set.
CATEGORY_MAP = {
    "caption": "Caption",
    "footnote": "Footnote",
    "formula": "Formula",
    "equationblock": "Formula",
    "chemicalblock": "Formula",
    "listitem": "List-item",
    "listgroup": "List-item",
    "pageheader": "Page-header",
    "pagefooter": "Page-footer",
    "picture": "Picture",
    "image": "Picture",
    "figure": "Picture",
    "diagram": "Picture",
    "sectionheader": "Section-header",
    "table": "Table",
    "text": "Text",
    "tableofcontents": "Text",
    "complexblock": "Text",
    "codeblock": "Text",
    "form": "Text",
    "bibliography": "Text",
    "blankpage": "Text",
    "title": "Title",
}


def clean_html(html: str, category: str | None = None) -> tuple[str, str | None, int | None]:
    """Returns (cleaned_text, final_category, heading_level). `category` in,
    `final_category` out may differ: an `<h1>`-`<h6>` tag found inside the
    block overrides whatever `data-label` said, since the structural tag is
    a stronger signal than the label attribute (the prompt asks the model
    to set both, redundantly, precisely for this cross-check)."""
    heading_level = None
    final_category = category

    if not html:
        return "", final_category, heading_level

    # Bold / italic / underline -> markdown-lite, matching the convention
    # User/Test's DocumentRenderer already expects in block text.
    html = re.sub(r"<(b|strong)\b[^>]*>(.*?)</\1>", r"**\2**", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<(i|em)\b[^>]*>(.*?)</\1>", r"*\2*", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<u\b[^>]*>(.*?)</u>", r"__\1__", html, flags=re.IGNORECASE | re.DOTALL)

    if category in ("Table", "List-item"):
        # Table/list markup is meaningful structure, not prose — leave the
        # HTML intact for the export pipeline's own table/list renderer
        # (see Test/reformat_service_v2.py) instead of flattening to text.
        return html.strip(), final_category, heading_level

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(("script", "style")):
        tag.decompose()

    for level in range(1, 7):
        heading = soup.find(f"h{level}")
        if heading:
            final_category = "Title" if level == 1 else "Section-header"
            heading_level = None if level == 1 else level - 1
            heading.insert_after("\n")
            heading.unwrap()
            break

    for img in soup.find_all("img"):
        # `alt` is where the prompt tells the model to put the image/chart/
        # diagram description (app/prompts.py's OCR_PROMPT) — `get_text()`
        # below only extracts text nodes, so without this an <img> tag's
        # entire description would silently disappear.
        img.replace_with(img.get("alt", ""))

    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(("p", "div")):
        tag.append("\n")

    text = unescape(soup.get_text())
    text = re.sub("[​-‏⁠﻿]", "", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text).strip()

    return text, final_category, heading_level


def parse_layout_response(html_response: str, input_width: int, input_height: int) -> list[DocumentLayout]:
    """`input_width`/`input_height` are the dimensions of the image actually
    sent to the model (post smart_resize) — `data-bbox` is normalized 0-1000
    against whatever the model was looking at, so denormalizing against
    anything else would misplace every block."""
    soup = BeautifulSoup(html_response, "html.parser")
    layouts: list[DocumentLayout] = []

    for div in soup.find_all("div", attrs={"data-bbox": True}):
        raw_label = div.get("data-label")
        if isinstance(raw_label, list):
            raw_label = raw_label[0] if raw_label else None
        if not raw_label:
            class_attr = div.get("class")
            raw_label = class_attr[0] if class_attr else "text"
        label = CATEGORY_MAP.get(re.sub(r"[^a-z0-9]", "", raw_label.lower()), "Text")

        bbox = _parse_bbox(div.get("data-bbox", ""), input_width, input_height)
        cleaned_text, final_category, heading_level = clean_html(div.decode_contents().strip(), category=label)

        layouts.append(
            DocumentLayout(bbox=bbox, category=final_category or "Text", text=cleaned_text, level=heading_level)
        )

    if not layouts:
        # Model ignored the layout-block instructions entirely — fall back
        # to treating the whole response as one Text block rather than
        # losing the page's content outright.
        cleaned_text, _, _ = clean_html(html_response)
        layouts.append(DocumentLayout(bbox=[], category="Text", text=cleaned_text))

    return layouts


def _parse_bbox(bbox_str: str, input_width: int, input_height: int) -> list[float]:
    try:
        parts = [int(x) for x in bbox_str.split()]
        if len(parts) != 4:
            return []
        x1, y1, x2, y2 = parts
        return [
            max(0, min(input_width, x1 * input_width / 1000)),
            max(0, min(input_height, y1 * input_height / 1000)),
            max(0, min(input_width, x2 * input_width / 1000)),
            max(0, min(input_height, y2 * input_height / 1000)),
        ]
    except (ValueError, TypeError) as exc:
        logger.warning("could not parse data-bbox %r: %s", bbox_str, exc)
        return []
