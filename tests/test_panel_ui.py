"""Host panel row models use stable project identities, never display labels."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from gimp.imanganation.panel_ui import build_docks, character_row_id


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
