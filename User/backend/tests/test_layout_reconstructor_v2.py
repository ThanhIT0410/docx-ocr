"""Verifies `layout_reconstructor_v2.py` — most importantly that
`BiLSTMAttentionCRF` actually matches `app/weights/bilstm_attention_crf.pt`
(the old `Reformat_prototype/layout_reconstructor_v2.py` did NOT match its
checkpoint, see `Test/PIPELINE_NOTES.md` §8.1; this test is the regression
guard against repeating that)."""
from __future__ import annotations

import pytest

from app.schemas import DocumentLayout
from app.services.layout_reconstructor_v2 import (
    CATEGORY_CLASSES,
    FEAT_DIM,
    LayoutReconstructionError,
    LayoutReconstructorV2,
    PRODUCTION_TO_TRAIN_CATEGORY,
    _extract_features,
)

PAGE_WIDTH = 1654
PAGE_HEIGHT = 2338


def _block(x1, y1, x2, y2, category="Text", text="lorem"):
    return DocumentLayout(bbox=[x1, y1, x2, y2], category=category, text=text)


def test_checkpoint_loads_and_matches_architecture():
    """The real acceptance criterion from the upgrade plan: load_state_dict
    against the actual shipped checkpoint must not raise — this is what
    confirms BiLSTMAttentionCRF's architecture matches what was trained,
    not just that the class is syntactically valid."""
    reconstructor = LayoutReconstructorV2(page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
    assert set(reconstructor.label2id.keys()) == {"no-break", "column-break", "section-break"}


def test_missing_model_file_raises_layout_reconstruction_error(tmp_path):
    bogus_path = tmp_path / "does-not-exist.pt"
    with pytest.raises(LayoutReconstructionError):
        LayoutReconstructorV2(page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT, model_path=bogus_path)


def test_feature_extraction_shape_and_dim():
    blocks = [_block(100, 100, 400, 150, "Title"), _block(100, 200, 400, 260, "Text")]
    feats = _extract_features(blocks, PAGE_WIDTH, PAGE_HEIGHT)
    assert feats.shape == (2, FEAT_DIM)
    assert FEAT_DIM == 18


def test_category_mapping_covers_every_non_header_footer_prompt_label():
    """app/prompts.py::OCR_PROMPT's 11 labels, minus Page-header/Page-footer
    (stripped before reconstruction runs in both engines) — every remaining
    label must have *some* entry here, even if it's a guess (see module
    docstring), so a real page never silently gets an all-zero category
    one-hot for a label the OCR pipeline actually emits."""
    prompt_labels = {
        "Caption", "Footnote", "Formula", "List-item", "Picture",
        "Section-header", "Table", "Text", "Title",
    }
    assert prompt_labels <= PRODUCTION_TO_TRAIN_CATEGORY.keys()
    for train_category in PRODUCTION_TO_TRAIN_CATEGORY.values():
        assert train_category in CATEGORY_CLASSES


def test_reconstruct_empty_page_returns_no_sections():
    reconstructor = LayoutReconstructorV2(page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
    assert reconstructor.reconstruct([]) == []


def test_reconstruct_single_block_short_circuits_without_model():
    reconstructor = LayoutReconstructorV2(page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
    block = _block(100, 100, 400, 150, "Title")
    sections = reconstructor.reconstruct([block])
    assert len(sections) == 1
    assert len(sections[0].columns) == 1
    assert sections[0].columns[0].blocks == [block]


def test_reconstruct_multi_block_preserves_every_block_exactly_once():
    """Smoke test against the real model: whatever column/section boundaries
    it predicts, every input block must appear in the output exactly once —
    the gap-label-to-Column/Section rebuild must not drop or duplicate
    blocks regardless of what the model predicts."""
    blocks = [
        _block(150, 200, 800, 260, "Title"),
        _block(150, 300, 800, 900, "Text"),
        _block(150, 950, 800, 1600, "Text"),
        _block(850, 300, 1500, 900, "Text"),
        _block(850, 950, 1500, 1600, "Text"),
    ]
    reconstructor = LayoutReconstructorV2(page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
    sections = reconstructor.reconstruct(blocks)

    seen = [b for section in sections for column in section.columns for b in column.blocks]
    assert len(seen) == len(blocks)
    assert set(id(b) for b in seen) == set(id(b) for b in blocks)


def test_extract_header_footer_matches_v1_semantics():
    header = _block(100, 50, 800, 90, "Page-header")
    footer = _block(100, 2250, 800, 2290, "Page-footer")
    body = _block(100, 200, 800, 900, "Text")
    reconstructor = LayoutReconstructorV2(page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
    got_header, got_footer = reconstructor.extract_header_footer([header, footer, body])
    assert got_header == [header]
    assert got_footer == [footer]
