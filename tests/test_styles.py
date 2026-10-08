"""A project's overall look: presets on top of the colour style (no GPU)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from manganation import locations as lc
from manganation import styles
from manganation.characters.cast import design_character
from manganation.characters.registry import CharacterRegistry
from manganation.render.panel import build_prompt, load_style, prose_prompt
from manganation.script.schema import PanelSpec
from manganation.web.api import create_app
from tests.test_character_design_job import FakeComfy, FakeLLM


def test_the_default_look_is_the_colour_style_unchanged():
    base = load_style()
    assert base["prompt_prefix"] == "manga panel, anime illustration, "
    assert load_style({"preset": "default", "text": ""})["prompt_prefix"] == base[
        "prompt_prefix"]
    assert base["tags"] == [] and base["id"] == "default"


def test_a_preset_and_the_authors_words_join_the_prompt():
    style = load_style({"preset": "retro_90s", "text": "  heavy   shadows "})
    assert style["prompt_prefix"] == ("manga panel, anime illustration, retro artstyle, "
                                      "1990s (style), heavy shadows, ")
    assert style["tags"] == ["retro artstyle", "1990s (style)", "heavy shadows"]
    assert "1990s cel-animated" in style["prose"] and style["prose"].endswith(
        "; heavy shadows")
    spec = PanelSpec(page=1, panel=1, action="Yuki waves.")
    assert build_prompt(spec, style).startswith(
        "manga panel, anime illustration, retro artstyle, 1990s (style), heavy shadows")
    text = prose_prompt(spec, {}, style=style["prose"])
    assert text.startswith("A full-colour anime illustration for a single manga panel, "
                           "drawn like a 1990s")
    assert "monochrome" in load_style({"preset": "seinen_ink"})["negative"]  # still colour


def test_every_preset_loads_and_names_a_check():
    listed = styles.presets()
    assert listed[0]["id"] == "default"
    for entry in listed:
        style = load_style({"preset": entry["id"]})
        assert entry["label"] and style["prose"]
        assert entry["measured"]  # GIMP shows what measuring it found
        if entry["id"] not in ("default", "seinen_ink"):  # seinen: no tag for its look
            assert style["tags"] and styles.check_tags({"preset": entry["id"]})
    with pytest.raises(styles.StyleError, match="unknown style"):
        load_style({"preset": "vaporwave"})


def test_locations_and_characters_are_drawn_in_the_projects_look(tmp_path):
    assert "Style: painted in soft watercolour" in lc.location_prompt(
        "the pier", [], "", load_style({"preset": "watercolor"})["prose"])
    assert "Clean line art" in lc.location_prompt("the pier", [])

    reg = CharacterRegistry.from_path(tmp_path)
    llm = FakeLLM()
    comfy = FakeComfy(llm)
    design_character(reg, "Rin", "a girl", llm=llm, comfy=comfy,
                     style={"preset": "soft_shoujo", "text": ""})
    assert "pastel colors" in comfy.graphs[0]["2"]["inputs"]["text"]


def test_the_engine_lists_styles_and_refuses_an_unknown_one():
    client = TestClient(create_app(render=lambda *a, **k: None))
    assert [s["id"] for s in client.get("/styles").json()][:2] == ["default", "retro_90s"]
    bad = client.post("/jobs", json={
        "project": "prj_abc123", "panel": {"id": "pnl_a", "label": {"page": 1, "panel": 1},
                                           "characters": [], "action": "x"},
        "frame_width": 100, "frame_height": 100, "style": {"preset": "vaporwave"}})
    assert bad.status_code == 422 and "unknown style" in bad.text


def test_a_looks_negative_reaches_character_designs(tmp_path):
    reg = CharacterRegistry.from_path(tmp_path)
    llm = FakeLLM()
    comfy = FakeComfy(llm)
    design_character(reg, "Rin", "a girl", llm=llm, comfy=comfy,
                     style={"preset": "seinen_ink"})
    graph = comfy.graphs[0]
    negatives = [n["inputs"].get("text", "") for n in graph.values()
                 if n.get("class_type") == "CLIPTextEncode"]
    assert any("tentacles" in text for text in negatives)
