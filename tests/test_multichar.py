"""Tests for Phase 4: regional layout + multi-character IP-Adapter graphs."""

from __future__ import annotations

import pytest

from manganation.layout.regions import Region, assign_regions, regions_for
from manganation.render import graphs
from manganation.script.schema import ReadingOrder

# --- region assignment ------------------------------------------------------


def test_assign_regions_count_and_coverage():
    regions = assign_regions(2, margin=0.0)
    assert len(regions) == 2
    assert abs(regions[0].w - 0.5) < 1e-9
    assert abs(regions[1].w - 0.5) < 1e-9
    assert all(r.h == 1.0 and r.y == 0.0 for r in regions)


def test_assign_regions_reading_order_rtl_puts_first_on_right():
    # RTL (manga): the first character occupies the rightmost band.
    regions = assign_regions(2, order=ReadingOrder.RTL)
    assert regions[0].x > regions[1].x
    # LTR: first character on the left.
    ltr = assign_regions(2, order=ReadingOrder.LTR)
    assert ltr[0].x < ltr[1].x


def test_assign_regions_margin_shrinks_bands():
    regions = assign_regions(2, margin=0.1)
    assert all(r.w < 0.5 for r in regions)
    assert all(r.x >= 0.0 for r in regions)


def test_assign_regions_three_characters():
    regions = assign_regions(3, margin=0.0)
    assert len(regions) == 3
    assert abs(sum(r.w for r in regions) - 1.0) < 1e-9


def test_assign_regions_rejects_zero():
    with pytest.raises(ValueError):
        assign_regions(0)


def test_regions_for_pixels_are_clipped_and_aligned():
    boxes = regions_for(1024, 1024, 2)
    assert len(boxes) == 2
    for x, y, w, h in boxes:
        assert 0 <= x and 0 <= y
        assert x + w <= 1024 and y + h <= 1024
        assert w % 64 == 0 and h % 64 == 0


def test_region_scaled_rounds_to_multiple():
    x, y, w, h = Region(0.0, 0.0, 0.5, 1.0).scaled(1024, 1024, multiple=64)
    assert (x, y, w, h) == (0, 0, 512, 1024)
    # a non-multiple canvas is clipped to the canvas, not rounded past it
    x, y, w, h = Region(0.0, 0.0, 0.5, 1.0).scaled(1000, 1000, multiple=64)
    assert w % 64 == 0 and x + w <= 1000 and y + h <= 1000


# --- multi-reference graph shape --------------------------------------------


def _base_graph() -> dict:
    return graphs.txt2img(
        ckpt="noobaiXL.safetensors", prompt="a", negative="b",
        width=1024, height=1024, seed=1, prefix="t",
    )


def test_single_reference_uses_simple_path():
    g = graphs.with_regional_ipadapter(
        _base_graph(),
        references=[{"image": "a.png", "mask": [0, 0, 512, 1024]}],
        ipadapter="ipa", clip_vision="clip",
    )
    assert g["11"]["class_type"] == "IPAdapterAdvanced"
    assert g["5"]["inputs"]["model"] == ["11", 0]


def test_two_references_build_regional_graph():
    refs = [
        {"image": "a.png", "mask": [0, 0, 512, 1024], "canvas_w": 1024, "canvas_h": 1024},
        {"image": "b.png", "mask": [512, 0, 512, 1024], "canvas_w": 1024, "canvas_h": 1024},
    ]
    g = graphs.with_regional_ipadapter(
        _base_graph(), references=refs, ipadapter="ipa", clip_vision="clip",
    )
    kinds = {n["class_type"] for n in g.values()}
    assert "IPAdapterRegionalConditioning" in kinds
    assert "IPAdapterCombineParams" in kinds
    assert "IPAdapterFromParams" in kinds
    assert g["apply"]["class_type"] == "IPAdapterFromParams"
    assert g["5"]["inputs"]["model"] == ["apply", 0]
    # two regional conditionings present
    conds = [n for n in g.values() if n["class_type"] == "IPAdapterRegionalConditioning"]
    assert len(conds) == 2
    # each mask points at a placed MaskComposite (feathered by default)
    for c in conds:
        placed = c["inputs"]["mask"][0]
        assert g[placed]["class_type"] in ("MaskComposite", "FeatherMask")
    # at least one feather node exists when feathering is on
    assert any(n["class_type"] == "FeatherMask" for n in g.values())


def test_regional_conditioning_binds_text_to_regions():
    g = graphs.with_regional_conditioning(
        _base_graph(),
        regions=[
            {"text": "1girl, white hair", "box": (0.0, 0.0, 0.5, 1.0)},
            {"text": "1boy, dark hair", "box": (0.5, 0.0, 0.5, 1.0)},
        ],
    )
    kinds = [n["class_type"] for n in g.values()]
    assert kinds.count("ConditioningSetAreaPercentage") == 2
    assert kinds.count("CLIPTextEncode") >= 3  # base + 2 characters
    # the KSampler positive now comes from the last combine node
    assert g["5"]["inputs"]["positive"][0] != "2"


def test_regional_graph_rejects_empty():
    with pytest.raises(ValueError):
        graphs.with_regional_ipadapter(
            _base_graph(), references=[], ipadapter="ipa", clip_vision="clip",
        )


def test_regional_graph_does_not_mutate_input():
    base = _base_graph()
    before = set(base.keys())
    graphs.with_regional_ipadapter(
        base,
        references=[
            {"image": "a.png", "mask": [0, 0, 512, 1024], "canvas_w": 1024, "canvas_h": 1024},
            {"image": "b.png", "mask": [512, 0, 512, 1024], "canvas_w": 1024, "canvas_h": 1024},
        ],
        ipadapter="ipa", clip_vision="clip",
    )
    assert set(base.keys()) == before
