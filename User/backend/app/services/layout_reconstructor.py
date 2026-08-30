"""Infers reading-order structure (columns, then sections of columns) from a
flat, unordered-looking list of layout blocks — ported near-verbatim from
`Reformat_prototype/reformat_service_v2.py`'s `LayoutReconstructor` (+
`Column`/`Section` from `Reformat_prototype/ocr.py`). Algorithm/constants
unchanged (see `Test/PIPELINE_NOTES.md` §4.1 for the full write-up of the
dynamic-programming column split + union-find section grouping this
implements) — this file only swaps the import of `DocumentLayout` for our
own (app/schemas.py) and `union_bbox` for our own (page_format_normalizer.py).

Key assumption inherited from the prototype (not verified independently,
same open question `Test/PIPELINE_NOTES.md` §4.1 raised about the old
system): `blocks` must already be in reading order — this only decides
*where to cut* into columns, never reorders.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from app.schemas import DocumentLayout
from app.services.page_format_normalizer import SUPPORTED_FORMATS, union_bbox
from app.services.preview_service import RENDER_DPI

logger = logging.getLogger(__name__)


@dataclass
class Column:
    blocks: list[DocumentLayout] = field(default_factory=list)
    section_parent: int | None = None

    @property
    def bbox(self) -> list[float]:
        if not self.blocks:
            return [0.0, 0.0, 0.0, 0.0]
        return union_bbox([b.bbox for b in self.blocks])


@dataclass
class Section:
    columns: list[Column]
    num_columns: int = 1


class LayoutReconstructor:
    def __init__(
        self,
        page_width: int = SUPPORTED_FORMATS["a4"][0],
        page_height: int = SUPPORTED_FORMATS["a4"][1],
        left_margin: int = round(0.75 * RENDER_DPI),
        right_margin: int = round(0.5 * RENDER_DPI),
        coverage_weight: float = 0.95,
        alignment_weight: float = 0.05,
        lmbda: float = 0.65,
    ):
        if not page_height or page_height <= 0:
            raise ValueError(f"Invalid page_height: {page_height}. Must be greater than 0.")
        if not page_width or page_width <= 0:
            raise ValueError(f"Invalid page_width: {page_width}. Must be greater than 0.")
        self.page_height = page_height
        self.page_width = page_width
        self.left_margin = left_margin
        self.right_margin = right_margin
        self.coverage_weight = coverage_weight
        self.alignment_weight = alignment_weight
        self.lmbda = lmbda

    def extract_header_footer(self, blocks: list[DocumentLayout]):
        header = [b for b in blocks if b.category == "Page-header"]
        footer = [b for b in blocks if b.category == "Page-footer"]
        return header, footer

    def _column_cost(self, blocks: list[DocumentLayout]) -> float:
        if not blocks:
            return 0
        x1_min = min(b.bbox[0] for b in blocks)
        x2_max = max(b.bbox[2] for b in blocks)

        span = x2_max - x1_min
        span = span if span > 0 else 1e-6

        coverage_loss = [1 - (b.width / span) for b in blocks]
        x1_alignment = np.var([b.bbox[0] / self.page_width for b in blocks])
        center_alignment = np.var([(b.bbox[2] + b.bbox[0]) * 0.5 / self.page_width for b in blocks])
        alignment_loss = 2 * x1_alignment * center_alignment / (x1_alignment + center_alignment + 1e-6)

        return sum(coverage_loss) * self.coverage_weight + alignment_loss * self.alignment_weight

    def split_into_columns(self, blocks: list[DocumentLayout]) -> list[Column]:
        n = len(blocks)
        if n == 0:
            return []

        segmentation_cost = [float("inf")] * (n + 1)
        segmentation_cost[0] = 0
        track = [0] * (n + 1)

        for i in range(1, n + 1):
            for j in range(i):
                total_cost = segmentation_cost[j] + self._column_cost(blocks[j:i]) + self.lmbda
                if total_cost < segmentation_cost[i]:
                    segmentation_cost[i] = total_cost
                    track[i] = j

        columns = []
        current = n
        while current > 0:
            prev = track[current]
            col = Column(blocks[prev:current])
            xs = [b.bbox[0] for b in col.blocks]
            xe = [b.bbox[2] for b in col.blocks]
            logger.debug(
                "column size=%d from %d to %d x1=%s x2=%s cost=%.3f",
                len(col.blocks), prev, current - 1,
                min(xs) if xs else None, max(xe) if xe else None,
                self._column_cost(col.blocks) + self.lmbda,
            )
            columns.append(col)
            current = prev
        return columns[::-1]

    def _is_same_section(self, col1: Column, col2: Column) -> bool:
        overlap_x = min(col1.bbox[2], col2.bbox[2]) - max(col1.bbox[0], col2.bbox[0])
        min_width = min(col1.bbox[2] - col1.bbox[0], col2.bbox[2] - col2.bbox[0])
        overlap_x_ratio = overlap_x / min_width if min_width > 0 else 0
        if overlap_x_ratio > 0.1:
            return False

        overlap_y = min(col1.bbox[3], col2.bbox[3]) - max(col1.bbox[1], col2.bbox[1])
        min_height = min(col1.bbox[3] - col1.bbox[1], col2.bbox[3] - col2.bbox[1])
        overlap_y_ratio = overlap_y / min_height if min_height > 0 else 0
        return overlap_y_ratio >= 0.6

    def _find(self, columns: list[Column], i: int) -> int:
        curr_parent = columns[i].section_parent
        if curr_parent == i or curr_parent is None:
            return i
        root = self._find(columns, curr_parent)
        columns[i].section_parent = root
        return root

    def _union(self, columns: list[Column], i: int, j: int):
        root_i = self._find(columns, i)
        root_j = self._find(columns, j)
        if root_i != root_j:
            columns[root_i].section_parent = root_j

    def split_into_sections(self, columns: list[Column]) -> list[Section]:
        n = len(columns)
        for i in range(1, n):
            if self._is_same_section(columns[i - 1], columns[i]):
                self._union(columns, i, i - 1)

        sections_map: dict[int, list[Column]] = {}
        for idx in range(n):
            root_idx = self._find(columns, idx)
            sections_map.setdefault(root_idx, []).append(columns[idx])

        return [Section(columns=cols) for cols in sections_map.values() if cols]
