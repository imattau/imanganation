"""The GIMP plug-in's stdlib-only project persistence boundary."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from gimp.imanganation.project_store import (
    ProjectFileError,
    apply_field_edit,
    load_project,
    new_id,
    record_take,
    save_project,
)
from gimp.imanganation.panel_ui import character_row_id
from manganation.project_container import integrity_errors


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / "docs/project-container.example.json").read_text())
SCHEMA = json.loads((ROOT / "docs/project-container.schema.json").read_text())


def test_example_round_trips_with_unknown_keys_and_schema(tmp_path):
    document = copy.deepcopy(EXAMPLE)
    document["future_extension"] = {"retained": [1, 2, 3]}
    save_project(tmp_path, document)

    loaded = load_project(tmp_path)
    assert loaded["future_extension"] == {"retained": [1, 2, 3]}
    jsonschema.validate(loaded, SCHEMA)
    assert integrity_errors(loaded) == []
    assert not (tmp_path / "project.json.tmp").exists()


@pytest.mark.parametrize("bad_path", ["/etc/passwd", "../outside.png", "takes\\image.png", "C:/image.png"])
def test_manifest_rejects_non_project_relative_paths(tmp_path, bad_path):
    document = copy.deepcopy(EXAMPLE)
    document["takes"]["tk_000001"]["file"] = bad_path
    with pytest.raises(ProjectFileError, match="relative path|stay inside"):
        save_project(tmp_path, document)


def test_generated_ids_match_the_manifest_prefixes():
    for prefix in ("prj_", "pnl_", "pg_", "tk_"):
        identifier = new_id(prefix)
        assert identifier.startswith(prefix)
        assert len(identifier[len(prefix):]) >= 6
        assert identifier[len(prefix):].islower()
        assert identifier[len(prefix):].isalnum()


def test_load_rejects_unknown_versions(tmp_path):
    document = copy.deepcopy(EXAMPLE)
    document["version"] = 2
    (tmp_path / "project.json").write_text(json.dumps(document))
    with pytest.raises(ProjectFileError, match="unsupported project version"):
        load_project(tmp_path)


def test_record_take_copies_to_a_new_immutable_file_and_preserves_graph(tmp_path):
    document = copy.deepcopy(EXAMPLE)
    document["future_extension"] = {"untouched": True}
    panel_id = document["panels"][2]["id"]  # no existing takes
    source = tmp_path / "engine-render.png"
    source.write_bytes(b"image bytes")

    take, path = record_take(
        tmp_path, document, panel_id, source, kind="import", width=16, height=8,
        engine={"source": "legacy panels/003.png"})

    assert path.read_bytes() == b"image bytes"
    assert take["file"] == path.relative_to(tmp_path).as_posix()
    take_id = document["panels"][2]["takes"][-1]
    assert take["origin"] == take_id
    stored = document["takes"][take_id]
    assert stored["file"] == take["file"]
    assert document["future_extension"] == {"untouched": True}
    jsonschema.validate(document, SCHEMA)
    assert integrity_errors(document) == []
    assert load_project(tmp_path)["panels"][2]["active_take"] == take_id


def test_derived_take_has_new_file_and_links_to_parent(tmp_path):
    document = copy.deepcopy(EXAMPLE)
    panel = document["panels"][2]
    source = tmp_path / "base.png"
    source.write_bytes(b"base image")
    _base, original_path = record_take(
        tmp_path, document, panel["id"], source, kind="import", width=10, height=12)
    original_id = document["panels"][2]["takes"][-1]
    refined_source = tmp_path / "refined.png"
    refined_source.write_bytes(b"refined image")

    refined, refined_path = record_take(
        tmp_path, document, panel["id"], refined_source, kind="refine", width=20, height=24,
        parent=original_id, engine={"upscaler": "example"})

    assert original_path.read_bytes() == b"base image"
    assert refined_path.read_bytes() == b"refined image"
    assert refined["parent"] == original_id
    assert refined["origin"] == original_id
    assert refined_path != original_path
    assert integrity_errors(document) == []
    jsonschema.validate(document, SCHEMA)


def test_context_field_edits_change_only_their_field_and_stay_valid(tmp_path):
    document = copy.deepcopy(EXAMPLE)
    edit = lambda key, value: apply_field_edit(document, key, value, character_row_id)  # noqa: E731
    panel = document["panels"][4]
    panel["characters"][1]["version"] = "winter"

    # The parser-miss fix: names match the cast case-insensitively, keep their pinned
    # version, and a new name joins the cast.
    assert edit(f"{panel['id']}.characters", "akira, Yuki,  Hana , yuki") == panel["id"]
    assert panel["characters"] == [{"name": "Akira", "version": "winter"},
                                   {"name": "Yuki", "version": None},
                                   {"name": "Hana", "version": None}]
    assert {"name": "Hana"} in document["cast"]
    edit(f"{panel['id']}.expressions", "Yuki: wide grin; Akira: sighs")
    assert panel["expressions"] == {"Yuki": "wide grin", "Akira": "sighs"}
    edit(f"{panel['id']}.expressions", "")
    assert "expressions" not in panel
    edit(f"{panel['id']}.action", "Yuki drags Akira\nby the wrist")
    assert panel["action"] == "Yuki drags Akira by the wrist"
    edit(f"{panel['id']}.camera", "")
    assert "camera" not in panel
    edit("pg_a1b2c3.label", "Opening")
    assert document["pages"][0]["label"] == "Opening"
    edit(f"{character_row_id('Yuki')}.aliases", "Yu, Snow")
    assert document["cast"][1]["aliases"] == ["Yu", "Snow"]

    save_project(tmp_path, document)  # still a valid container
    jsonschema.validate(load_project(tmp_path), SCHEMA)
    assert integrity_errors(load_project(tmp_path)) == []

    with pytest.raises(ProjectFileError, match="3:2"):
        edit(f"{panel['id']}.aspect_ratio", "wide")
    with pytest.raises(ProjectFileError, match="Name: expression"):
        edit(f"{panel['id']}.expressions", "grinning")
    with pytest.raises(ProjectFileError, match="no editable field"):
        edit(f"{panel['id']}.status", "placed")
    with pytest.raises(ProjectFileError, match="no longer in the project"):
        edit("pnl_gone00.action", "x")


def test_project_from_a_script_is_a_valid_container(tmp_path):
    from gimp.imanganation.project_store import project_from_script
    from manganation.script.formats import canonical

    text = ("[CHARACTERS]\nYUKI (aka Snow): silver bob\n\nPAGE 1\n"
            "[SCENE: School rooftop — late afternoon]\n"
            "PANEL 1\n[SHOT: wide shot]\n[ACTION]\nYuki drags Akira by the wrist.\n"
            "[DIALOGUE]\nAKIRA: Hey!\n"
            "PANEL 2\n[LOCATION: the stairwell]\n[ACTION]\nAkira trips.\n"
            "PAGE 2\n[SCENE: School rooftop - cont.]\nPANEL 1\n[ACTION]\nYuki laughs.\n")
    document = project_from_script(canonical.parse(text), title="Rooftop",
                                   script_file="script/script.md", script_text=text,
                                   script_format="canonical")
    assert document["cast"] == [{"name": "Yuki", "aliases": ["Snow"], "notes": "silver bob"},
                                {"name": "Akira", "aliases": []}]
    assert [l["name"] for l in document["locations"]] == ["School rooftop", "the stairwell"]
    first = document["panels"][0]
    assert [c["name"] for c in first["characters"]] == ["Akira", "Yuki"]
    assert document["cursor"]["next_panel"] == first["id"]
    assert [p["label"] for p in document["panels"]] == [
        {"page": 1, "panel": 1}, {"page": 1, "panel": 2}, {"page": 2, "panel": 1}]
    (tmp_path / "script").mkdir()
    (tmp_path / "script/script.md").write_text(text)
    save_project(tmp_path, document)
    jsonschema.validate(load_project(tmp_path), SCHEMA)
    assert integrity_errors(load_project(tmp_path), root=tmp_path) == []


def test_pages_reorder_and_delete_keep_the_project_valid(tmp_path):
    from gimp.imanganation.project_store import delete_page, reorder_pages

    document = copy.deepcopy(EXAMPLE)
    first = document["pages"][0]
    document["pages"] = [first] + [
        {"id": f"pg_extra{n}x", "label": f"Page {n}", "file": f"pages/page-00{n}.xcf"}
        for n in (2, 3)]
    document["pages"].append({"id": "pg_custom1", "label": "Cover", "file": "pages/c.xcf"})
    order = lambda: [(p["id"], p["label"]) for p in document["pages"]]  # noqa: E731

    reorder_pages(document, "pg_extra3x", first["id"])  # dropped on an earlier page
    assert order() == [("pg_extra3x", "Page 1"), (first["id"], "Page 2"),
                       ("pg_extra2x", "Page 3"), ("pg_custom1", "Cover")]
    reorder_pages(document, "pg_extra3x", "pg_custom1")  # on a later one: after it
    assert [p["id"] for p in document["pages"]] == [
        first["id"], "pg_extra2x", "pg_custom1", "pg_extra3x"]

    placed = [p["id"] for p in document["panels"]
              if (p.get("placement") or {}).get("page") == first["id"]]
    assert placed
    file, unplaced = delete_page(document, first["id"])
    assert file == first["file"] and unplaced == placed
    assert all(p["status"] == "unplaced" and p["placement"] is None
               for p in document["panels"] if p["id"] in placed)
    assert all(p["takes"] for p in document["panels"] if p["id"] in placed)  # takes kept
    assert order() == [("pg_extra2x", "Page 1"), ("pg_custom1", "Cover"),
                       ("pg_extra3x", "Page 2")]
    save_project(tmp_path, document)
    jsonschema.validate(load_project(tmp_path), SCHEMA)
    with pytest.raises(ProjectFileError, match="no longer"):
        delete_page(document, first["id"])
