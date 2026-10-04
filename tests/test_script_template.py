"""docs/script-template.txt and the guide's skeleton parse as docs/script-template.md says."""

from __future__ import annotations

import re
from pathlib import Path

from gimp.imanganation.project_store import project_from_script
from manganation.script.formats import canonical

DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_the_example_script_uses_every_feature_as_documented():
    text = (DOCS / "script-template.txt").read_text()
    data = canonical.parse(text)
    cast = {c["name"]: c for c in data["cast"]}
    assert set(cast) == {"Mio", "Kaito", "Grandpa Sato"}
    assert cast["Kaito"]["aliases"] == ["Kai"]
    assert "silver earring" in cast["Kaito"]["description"]  # continuation line
    panels = {(p["page"], p["panel"]): p for p in data["panels"]}
    assert sorted({page for page, _ in panels}) == [1, 2, 3]
    assert panels[1, 1]["characters"] == []  # the narrator is not in the picture
    assert panels[1, 2]["characters"] == ["Mio", "Kaito"]
    assert panels[1, 3]["characters"] == ["Kaito"]  # "Kai" is an alias
    assert panels[2, 1]["characters"] == ["Mio", "Kaito"]  # named in the action
    kinds = {d["kind"] for p in data["panels"] for d in p["dialogue"]}
    assert kinds == {"speech", "thought", "whisper", "shout", "narration"}
    assert panels[2, 1]["sfx"] == ["VRRRMMMM"]
    assert "raincoat" in panels[2, 2]["notes"]
    assert panels[2, 4]["location"] == "Grandpa's house"  # CUT TO: the next panel
    assert panels[3, 1]["flashback"] and not panels[3, 2]["flashback"]
    assert panels[3, 1]["scene_heading"] == "Harbor pier — fifty years ago"
    assert panels[3, 2]["scene_heading"] == "Grandpa's house — morning"
    assert all(p["camera"] for p in data["panels"])  # every panel opens with a shot
    document = project_from_script(data, title="Template", script_file="script/script.md",
                                   script_text=text, script_format="canonical")
    assert [loc["name"] for loc in document["locations"]] == [
        "Harbor pier", "Grandpa's house"]


def test_the_guide_skeleton_parses():
    guide = (DOCS / "script-template.md").read_text()
    skeleton = re.search(r"## The skeleton\n\n```\n(.*?)```", guide, re.S).group(1)
    data = canonical.parse(skeleton)
    assert [c["name"] for c in data["cast"]] == ["Name", "Other Name"]
    assert [(p["page"], p["panel"]) for p in data["panels"]] == [(1, 1), (1, 2), (2, 1)]
    assert data["panels"][0]["sfx"] == ["BANG"]


def test_documented_rules_hold():
    # a colon in an action line is read as dialogue (the guide's first common mistake)
    data = canonical.parse("PAGE 1\nPanel 1: Wide shot.\nNote: the sky is red\n")
    assert data["panels"][0]["dialogue"][0]["speaker"] == "Note"
    # a scene heading between panels starts the next panels' scene
    data = canonical.parse("PAGE 1\n[SCENE: A]\nPanel 1: x.\n[SCENE: B]\nPanel 2: y.\n")
    assert [p["scene_heading"] for p in data["panels"]] == ["A", "B"]
