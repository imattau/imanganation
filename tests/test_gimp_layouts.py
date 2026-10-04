from __future__ import annotations

from gimp.imanganation.layouts import (
    FRAME_STYLES,
    LAYOUTS,
    frame_rings,
    layout_preview_rgb,
    layout_style_combinations,
    layouts_for_count,
    page_layout_availability,
    page_panel_count,
)


def test_layout_presets_are_filtered_to_exact_frame_count():
    for count in range(1, 7):
        choices = layouts_for_count(count)
        assert choices
        assert all(len(layout["regions"]) == count for layout in choices)
    assert layouts_for_count(7) == []


def test_layout_regions_fit_page_and_have_positive_area():
    for layout in LAYOUTS:
        for x, y, width, height in layout["regions"]:
            assert 0 <= x < 1
            assert 0 <= y < 1
            assert width > 0 and height > 0
            assert x + width <= 1
            assert y + height <= 1


def test_frame_styles_offer_distinct_border_treatments():
    assert {style["name"] for style in FRAME_STYLES} == {
        "Fine ink", "Classic ink", "Bold ink", "Slanted frames", "Double rule",
    }
    assert len({style["weight"] for style in FRAME_STYLES}) >= 3
    assert any(style["slant"] for style in FRAME_STYLES)
    assert any(style["double"] for style in FRAME_STYLES)


def test_gallery_combinations_include_every_matching_layout_and_style():
    for count in range(1, 7):
        choices = layout_style_combinations(count)
        assert len(choices) == len(layouts_for_count(count)) * len(FRAME_STYLES)
        assert all(len(layout["regions"]) == count for layout, _ in choices)


def test_preview_rgb_reflects_style_and_page_aspect():
    layout = layouts_for_count(2)[0]
    fine, bold = FRAME_STYLES[0], FRAME_STYLES[2]
    portrait = layout_preview_rgb(layout, fine, 480, 800)
    bold_portrait = layout_preview_rgb(layout, bold, 480, 800)
    landscape = layout_preview_rgb(layout, fine, 800, 480)

    assert len(portrait) == 480 * 800 * 3
    assert portrait != bold_portrait
    assert portrait != landscape


def test_frame_geometry_exposes_double_rule_and_slanted_style():
    region = layouts_for_count(1)[0]["regions"][0]
    width, height = 120, 180
    plain = frame_rings(region, width, height, FRAME_STYLES[1])
    double = frame_rings(region, width, height, FRAME_STYLES[4])
    slanted = frame_rings(region, width, height, FRAME_STYLES[3])

    assert len(plain) == 1
    assert len(double) == 2
    assert plain[0][0] != slanted[0][0]


def test_page_layout_availability_reports_mapping_and_panel_locks():
    manifest = {"pages": [{"id": "pg_test01", "label": "Page 2"}], "panels": [
        {"id": "pnl_test01", "label": {"page": 2}, "status": "unplaced"},
    ]}
    available = page_layout_availability(manifest, "pg_test01")
    assert available["available"]
    assert available["panel_count"] == 1
    assert len(available["combinations"]) == 5

    manifest["panels"][0]["placement"] = {"page": "pg_test01"}
    locked = page_layout_availability(manifest, "pg_test01")
    assert not locked["available"]
    assert locked["placed"] == 1
    assert "1 panel is already placed" in locked["reason"]


def test_page_panel_count_matches_label_and_excludes_orphans():
    manifest = {"panels": [
        {"label": {"page": 2}},
        {"label": {"page": 2}, "status": "unplaced"},
        {"label": {"page": 2}, "status": "orphaned"},
        {"label": {"page": 3}},
    ]}
    assert page_panel_count(manifest, "Page 2") == (2, 2)
    assert page_panel_count(manifest, "chapter page 3") == (3, 1)
    assert page_panel_count(manifest, "Untitled") == (None, None)
