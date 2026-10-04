"""The script's CHARACTERS block and characters named in action text."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from manganation.script.formats import canonical
from manganation.script.formats.mangaplay import parse_canonical
from manganation.script.parser import parse

SCRIPT = """\
CHARACTERS
AKIRA: 17, boy, messy black hair with blue tips, amber eyes,
  school uniform with red tie. Quiet loner, slouches.
YUKI (aka Yuki-chan, Snow): 16, girl, silver bob with pink tips.

PAGE 1
[SCENE: School rooftop]

Panel 1: Wide shot. Yuki drags Akira by the wrist.
YUKI: Come on!

Panel 2: Close-up. Akira's head snaps up. Snow is nowhere; akira is lowercase.

Panel 3: AKIRA alone. Yuki-chan waves from afar, Yukiko does not count.
"""


def test_cast_block_is_parsed_and_removed_from_the_story():
    data = canonical.parse(SCRIPT)
    akira, yuki = data["cast"][:2]
    assert akira == {"name": "Akira", "aliases": [],
                     "description": "17, boy, messy black hair with blue tips, amber eyes, "
                                    "school uniform with red tie. Quiet loner, slouches."}
    assert yuki["aliases"] == ["Yuki-chan", "Snow"]
    assert [p["panel"] for p in data["panels"]] == [1, 2, 3]
    assert data["panels"][0]["scene_heading"] == "School rooftop"


def test_action_text_adds_known_characters_after_speakers():
    panels = canonical.parse(SCRIPT)["panels"]
    assert panels[0]["characters"] == ["Yuki", "Akira"]  # Akira only named, not speaking
    assert panels[1]["characters"] == ["Akira", "Yuki"]  # possessive; alias "Snow"
    assert panels[2]["characters"] == ["Akira", "Yuki"]  # capitals; alias, not "Yukiko"


def test_without_a_cast_block_speakers_are_still_matched_in_action():
    script = ("PAGE 1\nPanel 1: Akira eats.\nAKIRA: Peace.\n"
              "Panel 2: Yuki drags Akira away.\nYUKI: Come on!\n")
    s = parse_canonical(script)
    assert s.cast == []
    assert s.panels[1].characters == ["Yuki", "Akira"]


def test_engine_models_and_prose_path_carry_the_cast():
    s = parse_canonical(SCRIPT)
    assert s.cast[1].name == "Yuki" and s.cast[1].aliases == ["Yuki-chan", "Snow"]

    class Fake:
        unloaded = False

        def chat_json(self, messages, *, schema=None):
            assert "CHARACTERS" not in messages[-1]["content"]  # the LLM sees the story
            return {"panels": [{"action": "Yuki drags Akira away", "characters": ["Yuki"]}]}

        def unload(self):
            self.unloaded = True

    prose = "CHARACTERS\nAKIRA: quiet boy\nYUKI: loud girl\n\nYuki finds Akira and drags him."
    script = parse(prose, client=Fake(), force_llm=True)
    assert [c.name for c in script.cast] == ["Akira", "Yuki"]
    assert script.panels[0].characters == ["Yuki", "Akira"]


def test_the_plug_in_imports_the_same_file_standard_library_only():
    link = Path(__file__).resolve().parents[1] / "gimp/imanganation/script_canonical.py"
    assert link.is_symlink() and link.resolve() == Path(canonical.__file__).resolve()
    spec = importlib.util.spec_from_file_location("script_canonical", link)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = link.read_text()
    assert "manganation" not in "".join(
        line for line in source.splitlines() if line.startswith(("import", "from")))
    assert module.parse(SCRIPT)["cast"][0]["name"] == "Akira"
