"""The GIMP plug-in's stdlib-only project persistence boundary."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from gimp.imanganation.panel_ui import character_row_id, location_row_id
from gimp.imanganation.project_store import (
    ProjectFileError,
    apply_field_edit,
    delete_character,
    delete_location,
    load_project,
    new_id,
    panels_at_location,
    record_take,
    save_project,
)
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

    edit(f"{panel['id']}.aspect_ratio", "Wide")  # a [FRAME] word, stored as its ratio
    assert panel["aspect_ratio"] == "2:1"
    edit(f"{panel['id']}.size", "Large")
    assert panel["size"] == "large"
    jsonschema.validate(document, SCHEMA)
    edit(f"{panel['id']}.size", "")
    assert "size" not in panel
    with pytest.raises(ProjectFileError, match="3:2"):
        edit(f"{panel['id']}.aspect_ratio", "widish")
    with pytest.raises(ProjectFileError, match="small, large, splash"):
        edit(f"{panel['id']}.size", "huge")
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
            "PANEL 1\n[SHOT: wide shot]\n[FRAME: wide, large]\n[ACTION]\n"
            "Yuki drags Akira by the wrist.\n"
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
    # frame hints are carried over; a panel without one gets no made-up shape
    assert (first["aspect_ratio"], first["size"]) == ("2:1", "large")
    assert "aspect_ratio" not in document["panels"][1] and "size" not in document["panels"][1]
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


def test_deleting_a_character_takes_them_out_of_the_cast_and_their_panels(tmp_path):
    document = copy.deepcopy(EXAMPLE)
    yuki = next(c for c in document["cast"] if c["name"] == "Yuki")
    yuki["aliases"] = ["Snow"]
    two_shot = document["panels"][2]
    two_shot["characters"][0]["version"] = "summer"
    two_shot["expressions"] = {"snow": "grin", "Akira": "sighs"}
    takes = {p["id"]: list(p["takes"]) for p in document["panels"]}

    removed, changed = delete_character(document, "SNOW")  # by alias, any case
    assert removed["name"] == "Yuki"
    assert [c["name"] for c in document["cast"]] == ["Akira"]
    assert changed == [document["panels"][1]["id"], two_shot["id"], document["panels"][4]["id"]]
    assert two_shot["characters"] == [{"name": "Akira", "version": None}]
    assert two_shot["expressions"] == {"Akira": "sighs"}
    assert document["panels"][1]["characters"] == []
    assert {p["id"]: p["takes"] for p in document["panels"]} == takes  # takes are kept
    assert all(line.get("speaker") for p in document["panels"]
               for line in p.get("dialogue", []))  # dialogue is script text, untouched
    save_project(tmp_path, document)
    jsonschema.validate(load_project(tmp_path), SCHEMA)
    assert integrity_errors(load_project(tmp_path)) == []

    with pytest.raises(ProjectFileError, match="not in this project's cast"):
        delete_character(document, "Yuki")


def test_location_notes_are_edited_in_context_and_deleting_keeps_panel_text(tmp_path):
    document = copy.deepcopy(EXAMPLE)
    rooftop = location_row_id("School rooftop")
    apply_field_edit(document, f"{rooftop}.notes", "  rusty   water tank ",
                     character_row_id, location_row_id)
    assert document["locations"][0] == {"name": "School rooftop",
                                        "notes": "rusty water tank"}
    apply_field_edit(document, f"{rooftop}.notes", "", character_row_id, location_row_id)
    assert "notes" not in document["locations"][0]
    with pytest.raises(ProjectFileError, match="no editable field"):
        apply_field_edit(document, f"{rooftop}.name", "Roof", character_row_id,
                         location_row_id)

    at_rooftop = panels_at_location(document, "the School Rooftop — dusk")
    assert len(at_rooftop) == 4  # scene headings "School rooftop — late afternoon/cont."
    headings = {p["id"]: (p["scene_heading"], p["location"]) for p in document["panels"]}
    removed = delete_location(document, "school rooftop")
    assert removed["name"] == "School rooftop"
    assert [loc["name"] for loc in document["locations"]] == ["Stairwell"]
    assert {p["id"]: (p["scene_heading"], p["location"])
            for p in document["panels"]} == headings  # script text stays
    with pytest.raises(ProjectFileError, match="not one of"):
        delete_location(document, "School rooftop")
    save_project(tmp_path, document)
    jsonschema.validate(load_project(tmp_path), SCHEMA)


_SCRIPT = ("[CHARACTERS]\nYUKI: silver bob\n\nPAGE 1\n[SCENE: School rooftop — dusk]\n"
           "PANEL 1\n[ACTION]\nYuki waves.\n"
           "PANEL 2\n[ACTION]\nAkira sighs.\n"
           "PANEL 3\n[ACTION]\nThey sit.\n")


def _script_project(text=_SCRIPT):
    from gimp.imanganation.project_store import project_from_script
    from manganation.script.formats import canonical

    return project_from_script(canonical.parse(text), title="Roof",
                               script_file="script/script.md", script_text=text,
                               script_format="canonical")


def _reparse(document, text):
    from gimp.imanganation.project_store import reparse_script
    from manganation.script.formats import canonical

    return reparse_script(document, canonical.parse(text), script_file="script/script.md",
                          script_text=text, script_format="canonical")


def test_reloading_an_edited_script_keeps_unchanged_panels_and_their_work(tmp_path):
    document = _script_project()
    waves, sighs, sit = document["panels"]
    waves["takes"], waves["active_take"] = [], None
    sighs["placement"] = None
    sit["takes"] = ["tk_aaaaaaaaaaaa"]  # work on a panel the edit changes
    document["takes"]["tk_aaaaaaaaaaaa"] = {
        "panel": sit["id"], "file": "takes/a.png", "width": 8, "height": 8,
        "kind": "render", "parent": None, "origin": "tk_aaaaaaaaaaaa",
        "created": "2026-10-08T10:00:00+11:00"}
    waves["location"] = "the stairwell"  # a Context edit: no longer the script's words
    edited = _SCRIPT.replace("PANEL 1\n[ACTION]\nYuki waves.\n",
                             "PANEL 1\n[ACTION]\nYuki runs in.\n"
                             "PANEL 2\n[ACTION]\nYuki waves.\n") \
                    .replace("PANEL 2\n[ACTION]\nAkira sighs.", "PANEL 3\n[ACTION]\nAkira sighs.") \
                    .replace("PANEL 3\n[ACTION]\nThey sit.", "PANEL 4\n[ACTION]\nThey sit down.") \
                    .replace("YUKI: silver bob", "YUKI: silver bob\nHANA: twin tails")
    summary = _reparse(document, edited)

    assert summary["kept"] == [waves["id"], sighs["id"]]  # moved down, kept whole
    assert waves["location"] == "the stairwell" and waves["label"]["panel"] == 2
    assert summary["orphaned"] == [sit["id"]] and sit["status"] == "orphaned"
    assert summary["removed"] == [] and len(summary["added"]) == 2
    reading = [p for p in document["panels"] if p["status"] != "orphaned"]
    assert [p["action"] for p in reading] == ["Yuki runs in.", "Yuki waves.",
                                              "Akira sighs.", "They sit down."]
    assert document["panels"][-1] is sit  # Needs matching, after the reading order
    assert {"name": "Hana", "aliases": [], "notes": "twin tails"} in document["cast"]
    (tmp_path / "script").mkdir()
    (tmp_path / "script/script.md").write_text(edited)
    (tmp_path / "takes").mkdir()
    (tmp_path / "takes/a.png").write_bytes(b"png")
    save_project(tmp_path, document)
    jsonschema.validate(load_project(tmp_path), SCHEMA)


def test_reloading_drops_changed_panels_that_had_no_work_and_keeps_notes():
    document = _script_project()
    document["cast"][0]["notes"] = "my own notes"
    summary = _reparse(document, _SCRIPT.replace("They sit.", "They stand.")
                       .replace("silver bob", "gold bob"))
    assert len(summary["kept"]) == 2 and len(summary["added"]) == 1
    assert len(summary["removed"]) == 1 and summary["orphaned"] == []
    assert document["cast"][0]["notes"] == "my own notes"  # the project's words win
    assert document["cursor"]["next_panel"] in {p["id"] for p in document["panels"]}
    unchanged = _reparse(document, _SCRIPT.replace("They sit.", "They stand."))
    assert unchanged["added"] == [] and len(unchanged["kept"]) == 3


def test_an_older_project_adopts_fingerprints_from_its_unchanged_script():
    from gimp.imanganation.project_store import adopt_script_fingerprints
    from manganation.script.formats import canonical

    document = _script_project()
    for panel in document["panels"]:  # as an older parser or version left them
        del panel["source"]
        panel["action"] = "Wide shot. " + panel["action"]
    assert adopt_script_fingerprints(document, canonical.parse(_SCRIPT))
    assert not adopt_script_fingerprints(document, canonical.parse(_SCRIPT))  # done once
    summary = _reparse(document, _SCRIPT.replace("They sit.", "They stand."))
    assert len(summary["kept"]) == 2 and len(summary["added"]) == 1
    shifted = _script_project()
    for panel in shifted["panels"]:
        del panel["source"]
    one_more = _SCRIPT + "PANEL 4\n[ACTION]\nThe bell rings.\n"
    assert not adopt_script_fingerprints(shifted, canonical.parse(one_more))  # no guessing


def test_a_hand_added_panel_goes_at_the_end_of_its_page_and_survives_reload(tmp_path):
    from gimp.imanganation.project_store import add_panel, delete_panel

    document = _script_project()
    last = document["panels"][-1]
    page = last["label"]["page"]
    panel = add_panel(document, page, action="  A cat   watches. ", location="The roof",
                      characters=["Yuki, Mochi"], camera="low angle")
    assert panel["manual"] and panel["status"] == "unplaced"
    assert panel["label"] == {"page": page, "panel": last["label"]["panel"] + 1}
    assert panel["action"] == "A cat watches."
    assert document["panels"][-1] is panel
    assert any(c["name"] == "Mochi" for c in document["cast"])
    assert any(loc["name"] == "The roof" for loc in document["locations"])

    summary = _reparse(document, _SCRIPT)
    assert panel["id"] not in summary["removed"] + summary["orphaned"]
    assert document["panels"][-1] is panel and panel["status"] == "unplaced"
    assert [p["id"] for p in document["panels"]].count(panel["id"]) == 1

    delete_panel(document, panel["id"])
    assert panel not in document["panels"]
    with pytest.raises(ProjectFileError):
        delete_panel(document, document["panels"][0]["id"])  # script panels stay


def test_an_added_panel_needs_an_action_and_a_page(tmp_path):
    from gimp.imanganation.project_store import add_panel

    document = _script_project()
    with pytest.raises(ProjectFileError):
        add_panel(document, 1, action="  ")
    with pytest.raises(ProjectFileError):
        add_panel(document, 0, action="x")


def test_a_hand_added_cover_is_first_single_and_survives_reload():
    from gimp.imanganation.project_store import add_cover, add_panel, script_page_number

    document = _script_project()
    cover = add_cover(document, action="Yuki on the roof at dusk", characters=["Yuki"])
    assert cover["cover"] and cover["manual"] and cover["label"] == {"page": 0, "panel": 1}
    assert document["panels"][0] is cover
    with pytest.raises(ProjectFileError):
        add_cover(document, action="another")
    with pytest.raises(ProjectFileError):
        add_panel(document, 0, action="x")
    document["pages"].append({"id": "pg_cover01", "label": "Cover", "file": "pages/cover.xcf"})
    assert script_page_number(document, "pg_cover01") == 0

    _reparse(document, _SCRIPT)
    assert document["panels"][0] is cover

    # a script that gains its own COVER replaces an unworked manual one
    summary = _reparse(document, "COVER\n[ACTION]\nThe title art.\n\n" + _SCRIPT)
    assert cover["id"] in summary["removed"]
    assert sum(1 for p in document["panels"] if p.get("cover")) == 1


def test_a_panel_can_pin_one_of_a_characters_reference_images():
    from gimp.imanganation.project_store import set_character_version

    document = _script_project()
    panel = next(p for p in document["panels"] if p["characters"])
    entry = set_character_version(document, panel["id"], 0, "summer")
    assert entry["version"] == "summer" and panel["characters"][0]["version"] == "summer"
    set_character_version(document, panel["id"], 0, "")  # back to the default
    assert panel["characters"][0]["version"] is None
    with pytest.raises(ProjectFileError):
        set_character_version(document, panel["id"], 99, "x")
    with pytest.raises(ProjectFileError):
        set_character_version(document, "pnl_gone00", 0, "x")


def test_dialogue_and_sfx_are_added_edited_and_removed_from_context():
    from gimp.imanganation.project_store import apply_field_edit

    document = _script_project()
    panel = document["panels"][0]
    panel["dialogue"], panel["sfx"] = [], []
    pid = panel["id"]
    apply_field_edit(document, f"{pid}.dialogue_new", "Yuki: Hi there!")
    apply_field_edit(document, f"{pid}.dialogue_new", "Akira (thought): Not again.")
    assert panel["dialogue"] == [
        {"speaker": "Yuki", "text": "Hi there!", "kind": "speech"},
        {"speaker": "Akira", "text": "Not again.", "kind": "thought"}]
    apply_field_edit(document, f"{pid}.dialogue_0", "Yuki (shout): Hi there!!")
    assert panel["dialogue"][0]["kind"] == "shout" and panel["dialogue"][0]["text"] == "Hi there!!"
    apply_field_edit(document, f"{pid}.dialogue_1", "")  # blank removes
    assert len(panel["dialogue"]) == 1
    apply_field_edit(document, f"{pid}.dialogue_new", "")  # blank new adds nothing
    assert len(panel["dialogue"]) == 1
    for bad in ("no speaker here", "Yuki (sings): la"):
        with pytest.raises(ProjectFileError):
            apply_field_edit(document, f"{pid}.dialogue_new", bad)
    apply_field_edit(document, f"{pid}.sfx_new", "BANG")
    apply_field_edit(document, f"{pid}.sfx_0", "BOOM")
    assert panel["sfx"] == ["BOOM"]
    apply_field_edit(document, f"{pid}.sfx_0", "")
    assert panel["sfx"] == []
    with pytest.raises(ProjectFileError):
        apply_field_edit(document, f"{pid}.dialogue_5", "Yuki: x")
