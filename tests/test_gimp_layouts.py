from __future__ import annotations

from layouts import FRAME_STYLES, LAYOUTS, layouts_for_count, page_panel_count


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
