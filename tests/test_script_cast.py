"""The script format: cast block, labelled panel sections, problems, characters."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from manganation.script.formats import canonical
from manganation.script.formats.mangaplay import parse_canonical
from manganation.script.parser import parse

SCRIPT = """\
[CHARACTERS]
MIO: 15, girl, teal bob,
  yellow hairclip.
KAITO (aka Kai, KT): 16, boy, spiky brown hair.

PAGE 1
[SCENE: Pier — dawn]

PANEL 1
[SHOT: Wide shot]
[ACTION]
Mio drags Kaito along the pier.
Note: the boards are wet — a colon in an action line is just text.
[DIALOGUE]
MIO: Come on!
KAI (whisper): It's five a.m.
NARRATOR (narration): It began with a ship.
[SFX]
CREAK
THUD
[NOTES]
Keep the hairclip visible.

PANEL 2
[CHARACTERS: Mio]
[EXPRESSIONS: Mio: wide grin; kai: asleep]
[LOCATION: the boat]
[ACTION]
Close-up. Mio waves at Kaito, who is off-panel.
[DIALOGUE]
KAITO: Leave me here.
"""


def test_sections_put_every_line_in_its_place():
    data = canonical.parse(SCRIPT)
    assert data["problems"] == []
    first, second = data["panels"]
    assert first["action"].startswith("Mio drags Kaito") and "Note: the boards" in first["action"]
    assert first["camera"] == "wide shot"
    assert [(d["speaker"], d["kind"]) for d in first["dialogue"]] == [
        ("Mio", "speech"), ("Kaito", "whisper"), ("Narrator", "narration")]  # alias resolved
    assert first["sfx"] == ["CREAK", "THUD"]
    assert first["notes"] == "Keep the hairclip visible."
    assert first["scene_heading"] == "Pier — dawn"
    assert first["characters"] == ["Mio", "Kaito"]  # speakers, never the narrator
    # explicit fields win: only Mio is drawn though Kaito speaks and is named
    assert second["characters"] == ["Mio"]
    assert second["expressions"] == {"Mio": "wide grin", "Kaito": "asleep"}
    assert second["location"] == "the boat"
    assert second["camera"] == "close-up"  # no [SHOT]: read from the action


def test_cast_block():
    data = canonical.parse(SCRIPT)
    assert data["cast"] == [
        {"name": "Mio", "aliases": [], "description": "15, girl, teal bob, yellow hairclip."},
        {"name": "Kaito", "aliases": ["Kai", "Kt"], "description": "16, boy, spiky brown hair."}]


def test_problems_are_reported_with_line_numbers_not_guessed():
    script = """\
Untitled draft
PAGE 1
PANEL 1
Mio waves.
[ACTION]
Mio waves.
[DIALOGUE]
Mio waves happily
MIO (sings): la la
BOB: Hi.
[SHOT]
[DIALOG]
MIO: ok
[END ACTION]
[CHARACTERS: Mio, Zed]
[EXPRESSIONS: grinning]
[WEATHER: rain]
"""
    data = canonical.parse("[CHARACTERS]\nMIO: girl\n\n" + script)
    messages = {p["line"]: p["message"] for p in data["problems"]}
    offset = 3
    assert "Expected a character" in messages[1 + offset]  # still in the cast block
    assert "outside a section" in messages[4 + offset]
    assert "SPEAKER: text" in messages[8 + offset]
    assert "Unknown kind (sings)" in messages[9 + offset]
    assert "Bob speaks but is not in the [CHARACTERS] block" in messages[10 + offset]
    assert "needs a value" in messages[11 + offset]
    assert "does not close" in messages[14 + offset]
    assert "Zed is not in the [CHARACTERS] block" in messages[15 + offset]
    assert "Name: expression" in messages[16 + offset]
    assert "Unknown header [WEATHER]" in messages[17 + offset]
    assert [d["speaker"] for d in data["panels"][0]["dialogue"]] == ["Bob", "Mio"]  # DIALOG ok


def test_scene_and_flashback_markers_introduce_the_next_panels():
    data = canonical.parse("PAGE 1\n[SCENE: A]\nPANEL 1\n[ACTION]\nx\n[FLASHBACK START]\n"
                           "[SCENE: B]\nPANEL 2\n[ACTION]\ny\n[FLASHBACK END]\n"
                           "PANEL 3\n[ACTION]\nz\n")
    assert [(p["scene_heading"], p["flashback"]) for p in data["panels"]] == [
        ("A", False), ("B", True), ("B", False)]


def test_the_old_free_style_is_not_a_panel_script():
    with pytest.raises(ValueError, match="PANEL n"):
        canonical.parse("PAGE 1\nPanel 1: Wide shot. Mio waves.\nMIO: hi\n")


def test_engine_models_and_prose_path_carry_the_cast():
    s = parse_canonical(SCRIPT)
    assert s.cast[1].name == "Kaito" and s.panels[1].expressions == {
        "Mio": "wide grin", "Kaito": "asleep"}

    class Fake:
        def chat_json(self, messages, *, schema=None):
            assert "[CHARACTERS]" not in messages[-1]["content"]  # the LLM sees the story
            return {"panels": [{"action": "Yuki drags Akira away", "characters": ["Yuki"]}]}

        def unload(self):
            pass

    prose = "[CHARACTERS]\nAKIRA: quiet boy\nYUKI: loud girl\n\nYuki finds Akira and drags him."
    script = parse(prose, client=Fake(), force_llm=True)
    assert [c.name for c in script.cast] == ["Akira", "Yuki"]
    assert script.panels[0].characters == ["Yuki", "Akira"]


def test_the_plug_in_imports_the_same_file_standard_library_only():
    link = Path(__file__).resolve().parents[1] / "gimp/imanganation/script_canonical.py"
    assert link.is_symlink() and link.resolve() == Path(canonical.__file__).resolve()
    spec = importlib.util.spec_from_file_location("script_canonical", link)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    imports = [line for line in link.read_text().splitlines()
               if line.startswith(("import", "from"))]
    assert not any("manganation" in line for line in imports)
    assert module.parse(SCRIPT)["cast"][0]["name"] == "Mio"
