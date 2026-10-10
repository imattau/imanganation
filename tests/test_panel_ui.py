"""Host panel row models use stable project identities, never display labels."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from gimp.imanganation.panel_ui import (
    build_docks,
    character_row_id,
    location_row_id,
    prop_row_id,
    rgb_png,
)

EXAMPLE = json.loads((Path(__file__).resolve().parents[1] /
                      "docs/project-container.example.json").read_text())


def test_docks_use_panel_and_page_ids_even_for_repeated_labels():
    manifest = copy.deepcopy(EXAMPLE)
    # The example intentionally repeats the page 2, panel 1 display label.
    second_page_two_panel = manifest["panels"][4]
    second_page_two_panel["action"] = "A visibly different action"

    docks = build_docks(manifest, second_page_two_panel["id"])

    assert f"{second_page_two_panel['id']}\tPage 2 · Panel 1" in docks["project"]
    assert docks["project_selected"] == second_page_two_panel["id"]
    assert docks["selected_id"] == second_page_two_panel["id"]
    assert docks["inspector"].find("A visibly different action") >= 0
    assert docks["filmstrip_selected"] == ""


def test_filmstrip_selects_page_id_and_orphans_remain_separate():
    manifest = copy.deepcopy(EXAMPLE)
    manifest["panels"][0]["status"] = "orphaned"
    selected_page = manifest["pages"][0]["id"]

    docks = build_docks(manifest, selected_page)

    assert f"{selected_page}\tPage 1" in docks["filmstrip"]
    assert docks["filmstrip_selected"] == selected_page
    assert "# Needs matching" in docks["project"]
    assert manifest["panels"][0]["id"] in docks["project"]
    assert "Page 1 · Panel 1" not in docks["project"].split("# Needs matching")[0]


def test_filmstrip_shows_placed_count_and_missing_document(tmp_path):
    manifest = copy.deepcopy(EXAMPLE)

    docks = build_docks(manifest, manifest["pages"][0]["id"], tmp_path)

    tile = next(row for row in docks["filmstrip"].splitlines()
                if row.startswith(manifest["pages"][0]["id"] + "\t"))
    assert "2 panels" in tile
    assert "! XCF missing" in tile
    assert "XCF missing" in docks["inspector"]


def test_panel_inspector_shows_take_lineage_and_lettering_summary():
    manifest = copy.deepcopy(EXAMPLE)
    panel = manifest["panels"][0]

    inspector = build_docks(manifest, panel["id"])["inspector"]

    assert "frame 522 × 498" in inspector
    assert "Dialogue lines" not in inspector  # Edit dialogue already lists them
    assert "\nPlaced\t" in inspector and "Placed page" not in inspector
    assert "tk_000003 · active" in inspector
    assert "from tk_000001" in inspector


def test_cast_rows_select_a_character_context_by_engine_name():
    manifest = copy.deepcopy(EXAMPLE)
    character = manifest["cast"][0]
    selected = character_row_id(character["name"])

    docks = build_docks(manifest, selected)

    assert f"{selected}\t{character['name']}" in docks["project"]
    assert docks["project_selected"] == selected
    assert f"Name\t{character['name']}" in docks["inspector"]


def test_script_rows_use_panel_ids_and_keep_repeated_script_labels_distinct():
    manifest = copy.deepcopy(EXAMPLE)
    panel = manifest["panels"][4]

    docks = build_docks(manifest, panel["id"])

    assert f"{panel['id']}\tPage 2 · Panel 1" in docks["script"]
    assert docks["script_selected"] == panel["id"]
    assert "Page 2 · Panel 1" in docks["script"]
    assert sum("Page 2 · Panel 1" in row for row in docks["script"].splitlines()) == 2


def test_rgb_thumbnail_png_encoding_and_filmstrip_reference():
    manifest = copy.deepcopy(EXAMPLE)
    page = manifest["pages"][0]
    preview_path = "/tmp/imanganation-thumbnails/page.png"

    docks = build_docks(manifest, page["id"], previews={page["id"]: preview_path})
    png = rgb_png(1, 1, b"\xff\x00\x00")

    assert docks["filmstrip"].splitlines()[0].endswith("\t" + preview_path)
    assert png is not None and png.startswith(b"\x89PNG\r\n\x1a\n")
    assert rgb_png(2, 1, b"\xff\x00\x00") is None


def test_project_tree_groups_story_assets_and_shows_notes():
    manifest = copy.deepcopy(EXAMPLE)

    project_tree = build_docks(manifest)["project"]

    rooftop = location_row_id("School rooftop")
    assert "# Assets\n\t# Characters" in project_tree
    assert (f"\t# Locations\n\t\t{rooftop}\tSchool rooftop · Chain-link fence, late "
            "afternoon light") in project_tree  # selectable, like a character
    assert (f"\t# Props\n\t\t{prop_row_id(manifest['props'][0]['name'])}\t"
            "Akira's lunchbox") in project_tree  # selectable, like a location


def test_locations_heading_and_rows_carry_right_click_menus():
    manifest = copy.deepcopy(EXAMPLE)
    tree = build_docks(manifest, new_location_action="new-proc",
                       design_location_menu="design-proc",
                       delete_location_menu="delete-proc")["project"]
    assert "\t# Locations\t!new-proc:New location…" in tree
    rooftop = location_row_id("School rooftop")
    assert (f"\t\t{rooftop}\tSchool rooftop · Chain-link fence, late afternoon light"
            "\t!design-proc:Design location|delete-proc:Delete location…") in tree
    manifest["locations"] = []  # the heading stays, so the first place can be added
    assert "\t# Locations\t!new-proc:New location…" in build_docks(
        manifest, new_location_action="new-proc")["project"]


def test_a_selected_location_shows_its_notes_and_design_button():
    manifest = copy.deepcopy(EXAMPLE)
    rooftop = location_row_id("School rooftop")
    manifest["panels"][0]["location"] = "School rooftop — late afternoon"
    manifest["panels"][1]["location"] = "the school rooftop, cont."
    docks = build_docks(manifest, rooftop, design_location_action="design-proc")
    assert docks["selected_id"] == rooftop
    inspector = docks["inspector"]
    assert "# Location\nName\tSchool rooftop" in inspector
    assert f"@{rooftop}.notes\tNotes\tChain-link fence, late afternoon light" in inspector
    assert "!design-proc\tDesign location" in inspector
    assert "Panels set here\t4" in inspector  # by location, else scene heading
    assert "Panels set here\t1" in build_docks(manifest, location_row_id("Stairwell"))[
        "inspector"]


def test_context_rows_are_editable_fields_keyed_by_row_id():
    manifest = copy.deepcopy(EXAMPLE)
    panel = manifest["panels"][0]
    panel["expressions"] = {"Akira": "bored"}
    panel["characters"][0]["version"] = "winter"
    inspector = build_docks(manifest, panel["id"], open_page_action="proc-open")["inspector"]
    pid = panel["id"]
    assert f"@{pid}.characters\tCharacters\tAkira" in inspector
    assert f"@{pid}.expressions\tExpressions\tAkira: bored" in inspector
    assert f"@{pid}.aspect_ratio\tAspect ratio\t1:1" in inspector
    assert f"@{pid}.action\tAction\t{panel['action']}" in inspector
    assert "Akira\twinter" in inspector  # pinned version stays a read-only row
    assert "!proc-open\tOpen page" in inspector  # placed panel
    for row in inspector.splitlines():  # every field is exactly key, title, value
        if row.startswith("@"):
            assert row.count("\t") == 2

    unplaced = manifest["panels"][2]["id"]
    assert "Open page" not in build_docks(manifest, unplaced,
                                          open_page_action="p")["inspector"]
    page = build_docks(manifest, "pg_a1b2c3")["inspector"]
    assert "@pg_a1b2c3.label\tLabel\tPage 1" in page
    who = character_row_id("Yuki")
    assert f"@{who}.notes\tNotes\tEnergetic, always grinning." in build_docks(
        manifest, who)["inspector"]


def test_page_context_offers_layout_generation_when_script_count_matches():
    manifest = copy.deepcopy(EXAMPLE)
    selected_page = manifest["pages"][0]["id"]
    for panel in manifest["panels"]:
        if (panel.get("placement") or {}).get("page") == selected_page:
            panel.pop("placement")
            panel["status"] = "unplaced"

    inspector = build_docks(
        manifest, selected_page, generate_layout_action="proc-layout")["inspector"]

    assert "Layout status\t2 script panels · 10 preview choices" in inspector
    assert "!proc-layout\tChoose layout…" in inspector


def test_page_context_explains_when_label_has_no_script_page():
    manifest = copy.deepcopy(EXAMPLE)
    selected_page = manifest["pages"][0]["id"]
    manifest["pages"][0]["label"] = "Bonus spread"
    for panel in manifest["panels"]:
        if (panel.get("placement") or {}).get("page") == selected_page:
            panel.pop("placement")
            panel["status"] = "unplaced"

    inspector = build_docks(
        manifest, selected_page, generate_layout_action="proc-layout")["inspector"]

    assert "Layout status\tRename this page to Page N to match its script page." in inspector
    assert "!proc-layout\tChoose layout…" not in inspector


def test_page_context_offers_change_layout_when_a_panel_is_placed():
    manifest = copy.deepcopy(EXAMPLE)
    selected_page = manifest["pages"][0]["id"]

    inspector = build_docks(
        manifest, selected_page, generate_layout_action="proc-layout")["inspector"]

    assert "!proc-layout\tChange layout…" in inspector
    assert "Layout warning\tChanging the layout unplaces its 2 panel(s)" in inspector


def test_characters_heading_and_rows_carry_right_click_menus():
    manifest = copy.deepcopy(EXAMPLE)
    tree = build_docks(manifest, new_character_action="new-proc",
                       design_character_menu="design-proc")["project"]
    assert "\t# Characters\t!new-proc:New character…" in tree
    who = character_row_id("Yuki")
    assert f"\t\t{who}\tYuki\t!design-proc:Design character" in tree
    manifest["cast"] = []  # the heading stays, so the first character can be added
    assert "\t# Characters\t!new-proc:New character…" in build_docks(
        manifest, new_character_action="new-proc")["project"]
    assert "!" not in build_docks(copy.deepcopy(EXAMPLE))["project"]  # no actions, no menus


def test_character_rows_offer_delete_in_the_tree_and_the_character_bible():
    docks = build_docks(copy.deepcopy(EXAMPLE), design_character_menu="design-proc",
                        delete_character_menu="delete-proc")
    who = character_row_id("Yuki")
    assert (f"\t\t{who}\tYuki\t!design-proc:Design character|"
            "delete-proc:Delete character…") in docks["project"]
    assert f"{who}\tYuki\t!delete-proc:Delete character…" in docks["characters"]
    only_delete = build_docks(copy.deepcopy(EXAMPLE), delete_character_menu="delete-proc")
    assert f"\t\t{who}\tYuki\t!delete-proc:Delete character…" in only_delete["project"]


def test_pages_carry_a_delete_menu_and_the_strip_can_be_reordered():
    manifest = copy.deepcopy(EXAMPLE)
    docks = build_docks(manifest, delete_page_action="del", reorder_pages_action="move")
    strip = docks["filmstrip"].splitlines()
    assert strip[0] == "!!reorder\tmove"  # a directive, not a tile
    assert strip[1].startswith("pg_a1b2c3\t") and strip[1].endswith("\t!del:Delete page…")
    assert "pg_a1b2c3\tPage 1" in docks["project"]
    assert "\t!del:Delete page…" in docks["project"]
    plain = build_docks(manifest)
    assert "!!reorder" not in plain["filmstrip"] and "Delete page" not in plain["project"]


def test_dialogue_lines_get_bubble_buttons_in_context():
    manifest = copy.deepcopy(EXAMPLE)
    panel = next(p for p in manifest["panels"] if p.get("dialogue"))
    panel["sfx"] = ["BANG"]
    pid = panel["id"]
    inspector = build_docks(manifest, pid, bubble_line_action="bub",
                            bubbled=frozenset({f"{pid}:0"}))["inspector"]
    first = panel["dialogue"][0]
    assert "# Dialogue" in inspector
    assert f"\t{first['text']}\t!bub:{pid}:0:Select bubble" in inspector
    assert f"SFX\tBANG\t!bub:{pid}:sfx0:Bubble…" in inspector
    page = build_docks(manifest, "pg_a1b2c3", new_bubble_action="new")["inspector"]
    assert "!new\tBubble…" in page


def test_the_project_title_offers_close_project():
    manifest = copy.deepcopy(EXAMPLE)
    tree = build_docks(manifest, close_project_action="close-proc")["project"]
    assert tree.splitlines()[0] == "# Rooftop\t!close-proc:Close project"
    assert build_docks(manifest)["project"].splitlines()[0] == "# Rooftop"


def test_reload_script_is_on_the_project_titles_menu():
    docks = build_docks(copy.deepcopy(EXAMPLE), close_project_action="close",
                        reload_script_action="reload", load_script_action="load")
    assert docks["project"].splitlines()[0] == (
        "# Rooftop\t!reload:Reload script|load:Load script from file…|close:Close project")
    assert docks["script"].splitlines()[0] == "# Reading order"  # a list: no menus


def test_panel_shows_the_place_its_scene_heading_names_when_it_has_no_location():
    manifest = copy.deepcopy(EXAMPLE)
    panel = manifest["panels"][0]
    panel["location"], panel["scene_heading"] = "", "Small bridge — morning"

    docks = build_docks(manifest, panel["id"])
    inspector, brief = docks["inspector"], docks["panel"]

    assert "Location\tSmall bridge" in brief
    assert "Scene heading\tSmall bridge — morning" in brief
    assert "Location\tUnspecified" not in brief
    assert "@%s.location\tLocation\tSmall bridge" % panel["id"] in inspector


def test_add_panel_menus_sit_on_the_heading_pages_and_panels():
    manifest = copy.deepcopy(EXAMPLE)
    manifest["panels"][0]["manual"] = True
    project = build_docks(manifest, add_panel_action="add",
                          delete_panel_action="rm")["project"].splitlines()
    assert "# Script panels\t!add:Add panel…" in project
    assert any(l.startswith("pg_a1b2c3\t") and "add:Add panel to this page…" in l
               for l in project)
    first = next(l for l in project if l.startswith(manifest["panels"][0]["id"] + "\t"))
    assert first.endswith("\t!add:Add panel to this page…|rm:Delete panel…")
    other = next(l for l in project if l.startswith(manifest["panels"][1]["id"] + "\t"))
    assert "Delete panel" not in other
    assert "# Script panels" in build_docks(manifest)["project"].splitlines()


def test_add_cover_is_offered_until_there_is_a_cover_and_the_cover_takes_no_panels():
    manifest = copy.deepcopy(EXAMPLE)
    for panel in manifest["panels"]:
        panel.pop("cover", None)
    heading = lambda m: next(l for l in build_docks(
        m, add_panel_action="add", add_cover_action="cov")["project"].splitlines()
        if l.startswith("# Script panels"))
    assert heading(manifest) == "# Script panels\t!add:Add panel…|cov:Add cover…"
    manifest["panels"][0]["cover"] = True
    assert heading(manifest) == "# Script panels\t!add:Add panel…"
    row = next(l for l in build_docks(manifest, add_panel_action="add")["project"].splitlines()
               if l.startswith(manifest["panels"][0]["id"] + "\t"))
    assert "Add panel" not in row


def test_each_panel_character_gets_a_reference_button_in_context():
    manifest = copy.deepcopy(EXAMPLE)
    panel = next(p for p in manifest["panels"] if len(p.get("characters", [])) >= 1)
    panel["characters"][0]["version"] = "summer"
    pid, who = panel["id"], panel["characters"][0]["name"]
    inspector = build_docks(manifest, pid, character_version_action="ver")["inspector"]
    assert f"{who}\tsummer\t!ver:{pid}:0:Reference…" in inspector
    if len(panel["characters"]) > 1:
        assert f"\tDefault reference\t!ver:{pid}:1:Reference…" in inspector
    plain = build_docks(manifest, pid)["inspector"]
    assert f"{who}\tsummer" in plain and "Reference…" not in plain


def test_characters_offer_designing_another_reference():
    docks = build_docks(copy.deepcopy(EXAMPLE), design_character_menu="d",
                        design_variant_menu="v", delete_character_menu="x")
    who = character_row_id("Yuki")
    assert (f"\t\t{who}\tYuki\t!d:Design character|v:Design another reference…|"
            "x:Delete character…") in docks["project"]


def test_add_cover_page_sits_on_the_pages_heading_until_there_is_one():
    manifest = copy.deepcopy(EXAMPLE)
    heading = lambda: next(l for l in build_docks(
        manifest, add_cover_page_action="cp")["project"].splitlines()
        if l.startswith("# Pages"))
    manifest["pages"][0]["label"] = "Page 1"
    assert heading() == "# Pages\t!cp:Add cover page…"
    manifest["pages"].insert(0, {"id": "pg_cover01", "label": "Cover",
                                 "file": "pages/cover.xcf"})
    assert heading() == "# Pages"


def test_context_has_editable_dialogue_and_sfx_rows_plus_add_rows():
    manifest = copy.deepcopy(EXAMPLE)
    panel = next(p for p in manifest["panels"] if p.get("dialogue"))
    panel["sfx"] = ["BANG"]
    pid = panel["id"]
    inspector = build_docks(manifest, pid)["inspector"]
    first = panel["dialogue"][0]
    assert f"@{pid}.dialogue_0\tLine 1\t{first['speaker']}" in inspector
    assert f"@{pid}.dialogue_new\tAdd dialogue\t" in inspector
    assert f"@{pid}.sfx_0\tSFX 1\tBANG" in inspector
    assert f"@{pid}.sfx_new\tAdd SFX\t" in inspector


def test_context_offers_make_active_on_every_take_but_the_active_one():
    manifest = copy.deepcopy(EXAMPLE)
    panel = manifest["panels"][0]
    assert len(panel["takes"]) > 1
    inspector = build_docks(manifest, panel["id"], take_action="proc-take")["inspector"]
    active = panel["active_take"]
    for take_id in panel["takes"]:
        button = f"!proc-take:{panel['id']}:{take_id}:Make active"
        assert (button in inspector) == (take_id != active)
    assert "Make active" not in build_docks(manifest, panel["id"])["inspector"]


def test_take_rows_keep_the_seed_and_the_engines_render_warnings():
    manifest = copy.deepcopy(EXAMPLE)
    panel = manifest["panels"][0]
    take = manifest["takes"][panel["active_take"]]
    take["engine"] = {"seed": 4242, "warnings": ["Akira has no reference image"]}
    inspector = build_docks(manifest, panel["id"])["inspector"]
    assert "seed 4242" in inspector
    assert f"⚠ {panel['active_take']}\tAkira has no reference image" in inspector


def test_a_page_offers_to_generate_its_panels_that_have_no_render():
    manifest = copy.deepcopy(EXAMPLE)
    page = manifest["pages"][0]
    waiting = manifest["panels"][0]
    waiting["takes"], waiting["active_take"] = [], None
    waiting["placement"] = {"page": page["id"], "frame": [0, 0, 100, 100]}
    for other in manifest["panels"][1:]:
        other["placement"] = None
    inspector = build_docks(manifest, page["id"], generate_page_action="gen")["inspector"]
    assert "!gen\tGenerate all 1 waiting panel" in inspector
    panel_context = build_docks(manifest, waiting["id"], open_page_action="open",
                                generate_page_action="gen")["inspector"]
    assert "!gen\tGenerate all 1 waiting panel on this page" in panel_context
    waiting["takes"] = ["tk_x"]
    assert "Generate all" not in build_docks(
        manifest, page["id"], generate_page_action="gen")["inspector"]


def test_gallery_shows_takes_with_thumbnails_and_the_active_one_marked(tmp_path):
    from gimp.imanganation.panel_ui import build_gallery

    manifest = copy.deepcopy(EXAMPLE)
    panel = manifest["panels"][0]
    first = panel["takes"][0]
    (tmp_path / "takes").mkdir()
    image = tmp_path / manifest["takes"][first]["file"]
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"png")

    rows = build_gallery(manifest, panel["id"], tmp_path).splitlines()

    assert rows[0].startswith("# Takes")
    assert f"take:{panel['id']}:{first}\t" in rows[1] and rows[1].endswith(f"\t{image}")
    active = next(r for r in rows if r.startswith(f"take:{panel['id']}:{panel['active_take']}"))
    assert "✓" in active and active.count("\t") == 1  # no file on disk: no preview
    assert sum("✓" in r for r in rows) == 1


def test_gallery_shows_reference_images_for_characters_and_locations():
    from gimp.imanganation.panel_ui import build_gallery

    manifest = copy.deepcopy(EXAMPLE)
    who = character_row_id(manifest["cast"][0]["name"])
    rows = build_gallery(manifest, who, None, {
        "versions": {"base": "/p/base.png", "gimp-01": "/p/g1.png"}, "default": "gimp-01"})
    assert "ref:base\tbase\t/p/base.png" in rows
    assert "ref:gimp-01\t✓ gimp-01\t/p/g1.png" in rows
    assert "Not designed yet" in build_gallery(manifest, who, None, {"versions": {}})
    place = location_row_id("School rooftop")
    assert "loc:image\tSchool rooftop\t/p/roof.png" in build_gallery(
        manifest, place, None, {"image": "/p/roof.png"})
    assert build_gallery(manifest, None).startswith("# Gallery")


def test_every_panel_but_the_cover_can_be_duplicated_from_the_project_tree():
    manifest = copy.deepcopy(EXAMPLE)
    tree = build_docks(manifest, duplicate_panel_action="dup")["project"]
    assert tree.count("dup:Duplicate panel") == len(manifest["panels"])
    manifest["panels"][0]["cover"] = True
    tree = build_docks(manifest, duplicate_panel_action="dup")["project"]
    assert tree.count("dup:Duplicate panel") == len(manifest["panels"]) - 1


def test_empty_project_lists_say_what_to_do_next():
    manifest = copy.deepcopy(EXAMPLE)
    manifest["panels"], manifest["pages"] = [], []
    docks = build_docks(manifest)
    assert "No pages yet" in docks["project"]
    assert "# No panels yet" in docks["script"]


def test_export_page_range_is_read_in_project_order():
    import pytest

    from gimp.imanganation.export_formats import parse_page_range

    assert parse_page_range("", 4) == parse_page_range("ALL", 4) == [0, 1, 2, 3]
    assert parse_page_range("1-2, 4", 5) == [0, 1, 3]
    assert parse_page_range("3", 5) == [2]
    for bad in ("0", "2-1", "6", "a", "1-2-3", "1,,2"):
        with pytest.raises(ValueError):
            parse_page_range(bad, 5)


def test_blank_starting_picture_follows_the_frames_proportions():
    from gimp.imanganation.panel_ui import blank_size

    assert blank_size(500, 500) == (1024, 1024)
    w, h = blank_size(522, 640)
    assert w % 64 == 0 and h % 64 == 0 and w < h and 0.9e6 < w * h < 1.2e6
    w, h = blank_size(3000, 100)  # an extreme strip still gets a usable height
    assert h >= 512 and w > h


def test_reference_tiles_carry_a_right_click_menu():
    from gimp.imanganation.panel_ui import build_gallery

    manifest = copy.deepcopy(EXAMPLE)
    who = character_row_id(manifest["cast"][0]["name"])
    rows = build_gallery(manifest, who, None, {"versions": {"base": "/p/base.png"},
                                               "default": "base"},
                         (("open", "Open"), ("def", "Make default")))
    assert "right-click" in rows.splitlines()[0]
    assert "ref:base\t✓ base\t/p/base.png\t!open:Open|def:Make default" in rows


def test_props_have_menus_a_context_and_a_field_on_panels():
    manifest = copy.deepcopy(EXAMPLE)
    prop = manifest["props"][0]
    row = prop_row_id(prop["name"])
    docks = build_docks(manifest, row, new_prop_action="new", design_prop_menu="design",
                        delete_prop_menu="del", design_prop_action="btn")
    tree = docks["project"]
    assert "\t# Props\t!new:New prop…" in tree
    assert f"{row}\t" in tree and "!design:Design prop|del:Delete prop…" in tree
    assert docks["project_selected"] == row
    inspector = docks["inspector"]
    assert "# Prop" in inspector and "Panels showing it\t0" in inspector
    assert f"@{row}.notes\tNotes\t" in inspector and "!btn\tDesign prop" in inspector

    panel = manifest["panels"][0]
    panel["props"] = [prop["name"]]
    context = build_docks(manifest, panel["id"])["inspector"]
    assert f"@{panel['id']}.props\tProps\t{prop['name']}" in context
    assert "Panels showing it\t1" in build_docks(manifest, row)["inspector"]


def test_gallery_shows_a_props_images_with_the_current_one_ticked():
    from gimp.imanganation.panel_ui import build_gallery

    manifest = copy.deepcopy(EXAMPLE)
    row = prop_row_id(manifest["props"][0]["name"])
    rows = build_gallery(manifest, row, None,
                         {"images": {"a.png": "/p/a.png", "a-2.png": "/p/a-2.png"},
                          "current": "a-2.png"}, (("o", "Open"),))
    assert "img:a-2.png\t✓ a-2.png\t/p/a-2.png\t!o:Open" in rows
    assert "img:a.png\ta.png\t/p/a.png\t!o:Open" in rows
    assert "Not designed yet" in build_gallery(manifest, row, None, {"images": {}})


def test_gallery_says_when_the_selected_asset_is_being_designed():
    from gimp.imanganation.panel_ui import build_gallery

    manifest = copy.deepcopy(EXAMPLE)
    who = character_row_id(manifest["cast"][0]["name"])
    rows = build_gallery(manifest, who, None, {"versions": {}}, pending="Rendering · 34s")
    lines = rows.splitlines()
    assert lines[0].startswith("# References") and lines[1] == "# ⏳ Designing: Rendering · 34s"
    assert "Designing" not in build_gallery(manifest, who, None, {"versions": {}})


def test_job_status_text_shows_the_queue_or_the_comfyui_step():
    from gimp.imanganation.panel_ui import job_fraction, job_status_text, progress_bar

    assert progress_bar(0, 10) == "▱" * 10 and progress_bar(10, 10) == "▰" * 10
    assert progress_bar(3, 10) == "▰▰▰" + "▱" * 7 and progress_bar(5, 0) == "▱" * 10
    queued = {"status": "queued", "queue_position": 2}
    assert job_status_text(queued, 12) == "Queued behind 2 jobs · 12s"
    assert job_status_text({"status": "queued", "queue_position": 1}, 1).startswith(
        "Queued behind 1 job ·")
    assert job_status_text({"status": "queued", "queue_position": 0}, 3) == (
        "Next in the queue · 3s")
    loading = {"status": "running", "progress": None}
    assert job_status_text(loading, 5) == "Rendering · 5s" and job_fraction(loading) is None
    running = {"status": "running", "progress": {"pass": 1, "step": 7, "steps": 28}}
    assert job_status_text(running, 34) == "Rendering · ▰▰▱▱▱▱▱▱▱▱ 7/28 · 34s"
    assert job_fraction(running) == 0.25
    second = {"status": "running", "progress": {"pass": 2, "step": 10, "steps": 10}}
    assert "10/10 · pass 2 · 9s" in job_status_text(second, 9) and job_fraction(second) == 1.0


def test_list_fields_become_choice_rows_with_the_projects_own_options():
    manifest = copy.deepcopy(EXAMPLE)
    panel = manifest["panels"][0]
    typed = build_docks(manifest, panel["id"])["inspector"]
    assert f"@{panel['id']}.camera\tShot" in typed and "\t|" not in typed

    rows = build_docks(manifest, panel["id"], inline_choices=True)["inspector"].splitlines()
    by_key = {r.split("\t")[0][1:]: r.split("\t") for r in rows if r[:1] in "?+"}
    pid = panel["id"]
    assert set(by_key) == {f"{pid}.{f}" for f in ("characters", "props", "location", "camera",
                                                  "aspect_ratio", "size")}
    who = by_key[f"{pid}.characters"]
    assert who[0].startswith("+")  # several: a check list
    assert who[1] == "Characters" and who[2] == ", ".join(
        c["name"] for c in panel["characters"])
    assert set(who[3].split("|")) == {c["name"] for c in manifest["cast"]}
    assert who[3].split("|")[: len(panel["characters"])] == [c["name"] for c in
                                                            panel["characters"]]
    shot = by_key[f"{pid}.camera"]
    assert "close-up" in shot[3].split("|") and shot[0].startswith("?")  # one: a drop-down
    assert f"@{pid}.action\t" in "\n".join(rows)  # free text stays free text


def test_choice_options_cannot_break_the_row_format():
    manifest = copy.deepcopy(EXAMPLE)
    manifest["props"].append({"name": "Odd|prop\tname"})
    row = next(r for r in build_docks(manifest, manifest["panels"][0]["id"],
                                      inline_choices=True)["inspector"].splitlines()
               if r.startswith("+") and ".props\t" in r)
    assert len(row.split("\t")) == 4 and "Odd/prop name" in row.split("\t")[3].split("|")
