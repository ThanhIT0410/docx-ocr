"""Layout reconstructor V2 — a trained BiLSTM + cross-attention + CRF gap
classifier, now the engine `export_service.py` calls unconditionally.
`layout_reconstructor.py` (V1, rule-based DP/union-find) is left in the repo
untouched, unused by the export pipeline — kept only as a reference for the
algorithm it implements and as a fallback to restore by hand if V2 turns out
to need it. Both engines consume the same `list[DocumentLayout]` and produce
the same `list[Section]` (this module reuses `layout_reconstructor.py`'s
`Column`/`Section` dataclasses directly rather than redefining them), so
`DocumentRenderer` doesn't know or care which one produced its input.

**Architecture provenance**: ported from `Test/train_bilstm_attention_crf.ipynb`
cell 10 — the model actually saved into `bilstm_attention_crf.pt` (confirmed
by inspecting the checkpoint directly: `config={'feat_dim': 18, 'hidden_dim':
96, 'num_layers': 2, 'num_labels': 3, 'dropout': 0.4, 'num_heads': 4}`, and
`load_state_dict(strict=True)` succeeds against `BiLSTMAttentionCRF` below —
see `tests/test_layout_reconstructor_v2.py`). Deliberately **not** ported
from `Reformat_prototype/layout_reconstructor_v2.py::BiLSTMCRF`, whose
`__init__` doesn't even accept `num_heads` and has no attention head at all
— `Test/PIPELINE_NOTES.md` §8.1 documents why that file can't load this
checkpoint.

**Open questions inherited from the old system, NOT resolved here** (no
access to real `dochienet_dataset/labels*/doc_*.json` samples —
`Test/PIPELINE_NOTES.md` §9 items 5-6 are the same ask): the category
mapping below is a best-effort bridge between this system's OCR taxonomy
(`app/prompts.py::OCR_PROMPT`, 11 labels) and the training taxonomy
(`CATEGORY_CLASSES`, 10 labels, lowercase, doesn't fully match) —
specifically `Section-header → section-title` (train data also has a
distinct `sub-title` class with an undocumented distinction rule) and
`List-item → other` (train data has no `list-item` class at all, so it's
unknown how/whether the model ever saw one) are **guesses**, not confirmed
mappings. Since this engine now runs unconditionally, a wrong guess here
degrades real exports' column/section boundaries silently — worth resolving
before trusting output quality, not just a theoretical concern.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torchcrf import CRF

from app.schemas import DocumentLayout
from app.services.layout_reconstructor import Column, Section

logger = logging.getLogger(__name__)

# --- Category taxonomy bridge ------------------------------------------------
# Exact order matters: this is the one-hot index order the checkpoint was
# trained with (notebook cell 1's CATEGORY_CLASSES/CAT2IDX).
CATEGORY_CLASSES = [
    "text", "other", "caption", "equation",
    "sub-title", "table", "title", "section-title",
    "footnote", "figure",
]
_CAT2IDX = {c: i for i, c in enumerate(CATEGORY_CLASSES)}

# Production label (app/prompts.py::OCR_PROMPT) -> training label. Page-header
# and Page-footer are intentionally absent: both engines strip those before
# gap prediction ever runs (extract_header_footer below, same as V1).
PRODUCTION_TO_TRAIN_CATEGORY = {
    "Text": "text",
    "Caption": "caption",
    "Formula": "equation",
    "Table": "table",
    "Title": "title",
    "Footnote": "footnote",
    "Picture": "figure",
    "Section-header": "section-title",  # guess — see module docstring
    "List-item": "other",  # guess — absent from training taxonomy entirely
}

BBOX_FEAT_DIM = 8
CAT_FEAT_DIM = len(CATEGORY_CLASSES)
FEAT_DIM = BBOX_FEAT_DIM + CAT_FEAT_DIM  # 18 — must match checkpoint config["feat_dim"]

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent.parent / "weights" / "bilstm_attention_crf.pt"


class LayoutReconstructionError(Exception):
    """Raised when the ML engine can't produce a result for a page — model
    file missing, checkpoint/architecture mismatch, or a prediction error.
    Callers (see `export_service.py`) catch this and fall back to V1."""


def _extract_block_features(block: DocumentLayout, page_width: int, page_height: int) -> np.ndarray:
    x1, y1, x2, y2 = block.bbox
    w = x2 - x1
    h = y2 - y1
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    bbox_feat = np.array(
        [
            x1 / page_width, x2 / page_width, cx / page_width,
            y1 / page_height, y2 / page_height, cy / page_height,
            w / page_width, h / page_height,
        ],
        dtype=np.float32,
    )

    cat_feat = np.zeros(CAT_FEAT_DIM, dtype=np.float32)
    train_category = PRODUCTION_TO_TRAIN_CATEGORY.get(block.category)
    idx = _CAT2IDX.get(train_category) if train_category is not None else None
    if idx is not None:
        cat_feat[idx] = 1.0
    else:
        logger.debug("no training-category mapping for %r — leaving its one-hot all-zero", block.category)

    return np.concatenate([bbox_feat, cat_feat])


def _extract_features(blocks: list[DocumentLayout], page_width: int, page_height: int) -> torch.Tensor:
    feats = np.stack([_extract_block_features(b, page_width, page_height) for b in blocks])
    return torch.tensor(feats, dtype=torch.float32)


# --- Model --------------------------------------------------------------------


class BiLSTMAttentionCRF(nn.Module):
    """Exact port of the model class in `Test/train_bilstm_attention_crf.ipynb`
    cell 10. Inference-only here (`predict`) — this system never trains, only
    loads the pre-trained checkpoint, so the training-time `forward` (CRF
    negative log-likelihood loss) isn't ported."""

    def __init__(
        self,
        feat_dim: int = FEAT_DIM,
        hidden_dim: int = 96,
        num_layers: int = 2,
        num_labels: int = 3,
        dropout: float = 0.4,
        num_heads: int = 4,
    ):
        super().__init__()
        self.lstm = nn.LSTM(
            feat_dim,
            hidden_dim // 2,
            num_layers=num_layers,
            bidirectional=True,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0,
        )
        self.dropout = nn.Dropout(dropout)

        self.gap_proj = nn.Linear(2 * hidden_dim, hidden_dim)
        self.pos_embedding = nn.Embedding(512, hidden_dim)
        self.cross_attn = nn.MultiheadAttention(
            embed_dim=hidden_dim,
            num_heads=num_heads,
            dropout=dropout,
            batch_first=True,
        )
        self.attn_norm = nn.LayerNorm(hidden_dim)

        self.ffn = nn.Sequential(
            nn.Linear(hidden_dim, 4 * hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(4 * hidden_dim, hidden_dim),
        )
        self.ffn_norm = nn.LayerNorm(hidden_dim)

        self.emission_head = nn.Linear(hidden_dim, num_labels)
        self.crf = CRF(num_labels, batch_first=True)

    def _encode(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.dropout(out)

    def _gap_emissions(self, h: torch.Tensor, key_padding_mask: torch.Tensor | None = None) -> torch.Tensor:
        h_left = h[:, :-1, :]
        h_right = h[:, 1:, :]

        gap_q = self.gap_proj(torch.cat([h_left, h_right], dim=-1))
        positions = torch.arange(gap_q.size(1), device=h.device).unsqueeze(0)
        gap_q = gap_q + self.pos_embedding(positions)

        attn_out, _ = self.cross_attn(query=gap_q, key=h, value=h, key_padding_mask=key_padding_mask)
        x = self.attn_norm(gap_q + attn_out)
        x = self.ffn_norm(x + self.ffn(x))
        return self.emission_head(x)

    def predict(self, x: torch.Tensor, mask: torch.Tensor) -> list[list[int]]:
        h = self._encode(x)
        block_mask = torch.cat([mask, mask[:, -1:]], dim=1)
        emissions = self._gap_emissions(h, key_padding_mask=~block_mask)
        return self.crf.decode(emissions, mask=mask)


# Model load is slow (checkpoint I/O + module construction) and the weights
# are immutable for the process lifetime, so cache by resolved path instead
# of reloading per page/per export request. Guarded by a lock since FastAPI
# may serve concurrent requests on the same event loop's threadpool.
_cache_lock = threading.Lock()
_model_cache: dict[str, tuple[BiLSTMAttentionCRF, dict[str, int]]] = {}


def _load_model(model_path: Path) -> tuple[BiLSTMAttentionCRF, dict[str, int]]:
    key = str(model_path)
    cached = _model_cache.get(key)
    if cached is not None:
        return cached

    with _cache_lock:
        cached = _model_cache.get(key)
        if cached is not None:
            return cached

        if not model_path.is_file():
            raise LayoutReconstructionError(f"ML layout model not found at {model_path}")

        try:
            checkpoint = torch.load(model_path, map_location="cpu", weights_only=False)
            model = BiLSTMAttentionCRF(**checkpoint["config"])
            model.load_state_dict(checkpoint["model_state_dict"])
        except Exception as exc:
            raise LayoutReconstructionError(f"failed to load ML layout model from {model_path}: {exc}") from exc
        model.eval()

        label2id = checkpoint["label2id"]
        result = (model, label2id)
        _model_cache[key] = result
        logger.info("loaded ML layout model from %s (config=%s)", model_path, checkpoint["config"])
        return result


class LayoutReconstructorV2:
    """Drop-in alternative to `layout_reconstructor.py::LayoutReconstructor`:
    same `extract_header_footer` contract, and `reconstruct()` produces the
    same `list[Section]` shape as chaining V1's `split_into_columns` +
    `split_into_sections` — column/section boundaries come from the gap
    classifier's predictions instead of the DP cost function + union-find.
    """

    def __init__(
        self,
        page_width: int = 1654,
        page_height: int = 2338,
        model_path: str | Path | None = None,
    ):
        if not page_width or page_width <= 0:
            raise ValueError(f"Invalid page_width: {page_width}. Must be greater than 0.")
        if not page_height or page_height <= 0:
            raise ValueError(f"Invalid page_height: {page_height}. Must be greater than 0.")
        self.page_width = page_width
        self.page_height = page_height
        self.model, self.label2id = _load_model(Path(model_path) if model_path else DEFAULT_MODEL_PATH)

    def extract_header_footer(self, blocks: list[DocumentLayout]):
        header = [b for b in blocks if b.category == "Page-header"]
        footer = [b for b in blocks if b.category == "Page-footer"]
        return header, footer

    def reconstruct(self, blocks: list[DocumentLayout]) -> list[Section]:
        """`blocks` must already exclude header/footer (call
        `extract_header_footer` first) and be in reading order — like V1,
        the model only decides *where* to cut into columns/sections, it
        never reorders (see `layout_reconstructor.py`'s module docstring for
        the same inherited assumption)."""
        if not blocks:
            return []
        if len(blocks) == 1:
            return [Section(columns=[Column(blocks)])]

        try:
            gap_labels = self._predict_gap_labels(blocks)
        except Exception as exc:
            raise LayoutReconstructionError(f"ML gap prediction failed: {exc}") from exc

        return self._build_sections(blocks, gap_labels)

    def _predict_gap_labels(self, blocks: list[DocumentLayout]) -> list[int]:
        feats = _extract_features(blocks, self.page_width, self.page_height).unsqueeze(0)
        mask = torch.ones(1, len(blocks) - 1, dtype=torch.bool)
        with torch.no_grad():
            return self.model.predict(feats, mask)[0]

    def _build_sections(self, blocks: list[DocumentLayout], gap_labels: list[int]) -> list[Section]:
        no_break = self.label2id["no-break"]
        section_break = self.label2id["section-break"]

        columns: list[Column] = []
        col_break_types: list[int] = []
        current_blocks = [blocks[0]]
        for i, label in enumerate(gap_labels):
            if label == no_break:
                current_blocks.append(blocks[i + 1])
            else:
                columns.append(Column(current_blocks))
                col_break_types.append(label)
                current_blocks = [blocks[i + 1]]
        columns.append(Column(current_blocks))

        sections: list[Section] = []
        current_columns = [columns[0]]
        for i, break_type in enumerate(col_break_types):
            if break_type == section_break:
                sections.append(Section(columns=current_columns))
                current_columns = [columns[i + 1]]
            else:
                current_columns.append(columns[i + 1])
        sections.append(Section(columns=current_columns))
        return sections
