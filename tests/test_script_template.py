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
    assert data["problems"] == []
    cast = {c["name"]: c for c in data["cast"]}
    assert set(cast) == {"Mio", "Kaito", "Grandpa Sato"}
    assert cast["Kaito"]["aliases"] == ["Kai"]
    assert "silver earring" in cast["Kaito"]["description"]  # continuation line
    panels = {(p["page"], p["panel"]): p for p in data["panels"]}
    assert sorted({page for page, _ in panels}) == [0, 1, 2, 3]
    cover = panels[0, 1]
    assert cover["cover"] and not panels[1, 1]["cover"]
    assert cover["scene_heading"] == "Harbor pier — sunrise"
    assert cover["characters"] == ["Mio", "Kaito"]
    assert cover["camera"] == "low angle"
    assert cover["dialogue"] == [] and cover["sfx"] == []
    assert "title" in cover["notes"]
    places = {loc["name"]: loc["description"] for loc in data["locations"]}
    assert list(places) == ["Harbor pier", "Grandpa's house"]
    assert "lighthouse" in places["Harbor pier"]  # continuation line
    assert panels[1, 1]["characters"] == []  # [CHARACTERS: ] = nobody
    assert panels[1, 2]["characters"] == ["Mio", "Kaito"]
    assert panels[1, 2]["expressions"] == {"Mio": "excited grin", "Kaito": "half asleep"}
    assert panels[2, 3]["characters"] == ["Mio", "Kaito", "Grandpa Sato"]  # alias resolved
    assert "Note: the hull" in panels[2, 1]["action"]  # a colon in action is action
    kinds = {d["kind"] for p in data["panels"] for d in p["dialogue"]}
    assert kinds == {"speech", "thought", "whisper", "shout", "narration"}
    assert panels[2, 1]["sfx"] == ["VRRRMMMM"]
    assert "raincoat" in panels[2, 2]["notes"]
    assert panels[2, 4]["location"] == "Grandpa's house"
    assert panels[3, 1]["flashback"] and not panels[3, 2]["flashback"]
    assert panels[3, 1]["scene_heading"] == "Harbor pier — fifty years ago"
    assert panels[3, 2]["scene_heading"] == "Grandpa's house — morning"
    assert all(p["camera"] for p in data["panels"])
    document = project_from_script(data, title="Template", script_file="script/script.md",
                                   script_text=text, script_format="canonical")
    assert [loc["name"] for loc in document["locations"]] == [
        "Harbor pier", "Grandpa's house"]
    assert "lighthouse" in document["locations"][0]["notes"]
    assert document["panels"][0]["label"] == {"page": 0, "panel": 1}
    assert document["panels"][0]["cover"] is True
    first = document["panels"][2]
    assert first["expressions"] == {"Mio": "excited grin", "Kaito": "half asleep"}


def test_the_guide_skeleton_parses():
    guide = (DOCS / "script-template.md").read_text()
    skeleton = re.search(r"## The skeleton\n\n```\n(.*?)```", guide, re.S).group(1)
    data = canonical.parse(skeleton)
    assert data["problems"] == []
    assert [c["name"] for c in data["cast"]] == ["Name", "Other Name"]
    assert [(p["page"], p["panel"]) for p in data["panels"]] == [
        (0, 1), (1, 1), (1, 2), (2, 1)]
    assert [loc["name"] for loc in data["locations"]] == ["Place", "Another place"]
    assert data["panels"][0]["cover"]
    assert data["panels"][1]["characters"] == ["Name", "Other Name"]
    assert data["panels"][1]["sfx"] == ["BANG"]


def test_the_guide_lists_the_real_problem_messages():
    guide = (DOCS / "script-template.md").read_text()
    script = ("[CHARACTERS]\nKAITO: boy\n\nPAGE 1\nPANEL 1\nstray\n[DIALOGUE]\nno colon here\n"
              "KAITO (sings): la\nKIATO: hi\n[WEATHER: rain]\n")
    for problem in canonical.parse(script)["problems"]:
        assert f"`{problem['message']}`" in guide, problem["message"]
    cover = "COVER\n[DIALOGUE]\nMIO: hi\n\nPAGE 1\nPANEL 1\n[ACTION]\nx\n"
    for problem in canonical.parse(cover)["problems"]:
        assert f"`{problem['message']}`" in guide, problem["message"]
