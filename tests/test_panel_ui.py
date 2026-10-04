"""Host panel row models use stable project identities, never display labels."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from gimp.imanganation.panel_ui import build_docks


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
