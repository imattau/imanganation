from __future__ import annotations

from gimp.imanganation.layouts import (
    FRAME_STYLES,
    LAYOUTS,
    for_reading_order,
    frame_rings,
    layout_preview_rgb,
    layout_style_combinations,
    layouts_for_count,
    page_layout_availability,
    page_panel_count,
    rank_layouts,
)

A4 = 1 / 2 ** 0.5  # portrait page width / height


def _names(panels, count, order="rtl"):
    layouts = [for_reading_order(layout, order) for layout in layouts_for_count(count)]
    ranked, recommended = rank_layouts(layouts, panels, A4)
    return [layout["name"] for layout in ranked], recommended


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
    relayout = page_layout_availability(manifest, "pg_test01")
    assert relayout["available"]  # a placed page can be re-laid out
    assert relayout["placed"] == 1


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


def test_rtl_mirrors_regions_so_the_opener_is_on_the_right():
    tall = next(layout for layout in LAYOUTS if layout["name"] == "Three: tall opener")
    assert tall["regions"][0][0] < 0.5  # authored left-to-right
    rtl = for_reading_order(tall, "rtl")
    assert rtl["regions"][0][0] > 0.5 and rtl["name"] == tall["name"]
    assert for_reading_order(tall, "ltr") is tall
    for x, _, w, _ in rtl["regions"]:
        assert 0 <= x and x + w <= 1 + 1e-9


def test_frames_are_matched_to_panels_in_reading_order():
    """A big wide opener and a big wide closer used to score the same (sorted lists)."""
    opener = [{"camera": "establishing shot"}, {"camera": "close-up"},
              {"camera": "close-up"}, {"camera": "close-up"}]
    names, recommended = _names(opener, 4)
    assert recommended == names[0] == "Four: wide opener"
    names, _ = _names(list(reversed(opener)), 4)
    assert names[0] != "Four: wide opener"


def test_frame_shape_and_size_hints_choose_the_layout():
    names, recommended = _names([{"aspect_ratio": "1:2", "size": "large"}, {}, {}], 3)
    assert recommended == "Three: tall opener"
    names, recommended = _names([{"aspect_ratio": "2:1", "size": "large"}, {}, {}], 3)
    assert recommended == "Three: wide opener"
    # an explicit shape beats the wide shot's lean
    names, recommended = _names([{"camera": "wide shot", "aspect_ratio": "1:2"}, {}, {}], 3)
    assert recommended == "Three: tall opener"


def test_no_hints_means_no_recommendation():
    names, recommended = _names([{}, {"camera": "medium shot"}, {}], 3)
    # every three-panel layout is still offered, just none singled out
    assert recommended is None
    assert sorted(names) == sorted(layout["name"] for layout in layouts_for_count(3))


def test_page_availability_ranks_by_script_order_and_project_reading_order():
    manifest = {"project": {"reading_order": "ltr"},
                "pages": [{"id": "pg_test01", "label": "Page 1"}],
                "panels": [
                    {"id": "pnl_b", "label": {"page": 1, "panel": 2}, "status": "unplaced"},
                    {"id": "pnl_c", "label": {"page": 1, "panel": 3}, "status": "unplaced"},
                    {"id": "pnl_a", "label": {"page": 1, "panel": 1}, "status": "unplaced",
                     "aspect_ratio": "1:2", "size": "large"},
                ]}
    available = page_layout_availability(manifest, "pg_test01", A4)
    assert available["recommendation"] == "Three: tall opener"
    first = available["layouts"][0]
    assert first["regions"][0][0] < 0.5  # ltr: the opener stays on the left
