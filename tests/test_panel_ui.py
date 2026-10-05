"""Host panel row models use stable project identities, never display labels."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from gimp.imanganation.panel_ui import build_docks, character_row_id, rgb_png


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

    assert "Frame size\t522 × 498" in inspector
    assert "Dialogue lines\t1" in inspector
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


def test_project_tree_groups_story_assets_and_shows_notes_without_fake_ids():
    manifest = copy.deepcopy(EXAMPLE)

    project_tree = build_docks(manifest)["project"]

    assert "# Assets\n\t# Characters" in project_tree
    assert "\t# Locations\n\t\t# School rooftop · Chain-link fence, late afternoon light" in project_tree
    assert "\t# Props\n\t\t# Akira's lunchbox" in project_tree
    assert "asset:location" not in project_tree
    assert "asset:prop" not in project_tree


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


def test_page_context_locks_layout_when_a_panel_is_placed():
    manifest = copy.deepcopy(EXAMPLE)
    selected_page = manifest["pages"][0]["id"]

    inspector = build_docks(
        manifest, selected_page, generate_layout_action="proc-layout")["inspector"]

    assert "Layout status\tLayout locked · 2 panels are already placed" in inspector
    assert "!proc-layout\tChoose layout…" not in inspector


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
