"""Tests for the Phase 1 script layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from manganation.script.formats.mangaplay import looks_canonical, parse_canonical
from manganation.script.parser import ParseError, _panels_to_script, parse
from manganation.script.schema import ColorMode, PanelSpec, ReadingOrder, Script

FIXTURES = Path(__file__).parent / "fixtures"


# --- schema -----------------------------------------------------------------


def test_panelspec_dedupes_characters():
    p = PanelSpec(page=1, panel=1, characters=["Akira", "Yuki", "Akira", ""])
    assert p.characters == ["Akira", "Yuki"]


def test_script_json_roundtrip():
    s = Script(title="T", panels=[PanelSpec(page=1, panel=1, action="x")])
    assert Script.from_json(s.to_json()).panels[0].action == "x"


def test_script_page_helpers():
    s = Script(
        panels=[
            PanelSpec(page=2, panel=1, action="a"),
            PanelSpec(page=1, panel=1, action="b"),
            PanelSpec(page=2, panel=2, action="c"),
        ]
    )
    assert s.pages() == [1, 2]
    assert [p.action for p in s.panels_for_page(2)] == ["a", "c"]


# --- canonical tokenizer ----------------------------------------------------


@pytest.fixture
def canonical_text() -> str:
    return (FIXTURES / "rooftop_canonical.md").read_text()


def test_looks_canonical(canonical_text: str):
    assert looks_canonical(canonical_text)
    assert not looks_canonical("Just some prose with no structure at all.")


def test_parse_canonical_panels(canonical_text: str):
    s = parse_canonical(canonical_text, title="Rooftop")
    assert s.title == "Rooftop"
    assert s.reading_order is ReadingOrder.RTL
    # 2 + 2 + 1 (after CUT TO) panels
    assert len(s.panels) == 5


def test_parse_canonical_pages_and_scene(canonical_text: str):
    s = parse_canonical(canonical_text)
    assert s.pages() == [1, 2]
    assert "School rooftop" in s.panels[0].scene_heading


def test_parse_canonical_characters_and_dialogue(canonical_text: str):
    s = parse_canonical(canonical_text)
    p1 = s.panels[0]
    assert p1.characters == ["Akira"]
    assert p1.dialogue[0].speaker == "Akira"
    assert p1.dialogue[0].kind == "speech"

    # thought line
    thought = next(d for p in s.panels for d in p.dialogue if d.kind == "thought")
    assert thought.speaker == "Akira"


def test_parse_canonical_camera_and_sfx(canonical_text: str):
    s = parse_canonical(canonical_text)
    assert s.panels[0].camera == "wide shot"
    assert s.panels[1].camera == "close-up"
    assert "BANG" in s.panels[1].sfx


def test_parse_canonical_notes_and_cut(canonical_text: str):
    s = parse_canonical(canonical_text)
    noted = [p for p in s.panels if p.notes]
    assert noted and "character sheet" in noted[0].notes
    # CUT TO created a new panel with a location hint
    assert any("stairwell" in p.location for p in s.panels)


def test_parse_canonical_no_panels_raises():
    with pytest.raises(ValueError):
        parse_canonical("PAGE 1\njust some words\n")


# --- LLM path (stubbed, no network) ----------------------------------------


class _FakeClient:
    """Mimics OllamaClient.chat_json, returning a queued response."""

    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.unloaded = False

    def chat_json(self, messages, *, schema=None):
        self.calls += 1
        return self.response

    def unload(self):
        self.unloaded = True


def test_parse_uses_tokenizer_when_canonical(canonical_text: str, monkeypatch):
    # No client should be constructed at all.
    def _boom(*a, **k):
        raise AssertionError("LLM should not be used for canonical scripts")

    monkeypatch.setattr("manganation.script.parser.OllamaClient", _boom)
    s = parse(canonical_text, title="Rooftop")
    assert len(s.panels) == 5


def test_parse_prose_via_llm():
    fake = _FakeClient(
        {
            "panels": [
                {
                    "page": 1,
                    "panel": 1,
                    "action": "Akira eats lunch alone on the rooftop",
                    "characters": ["Akira"],
                    "camera": "wide shot",
                    "dialogue": [{"speaker": "Akira", "text": "Peace at last.", "kind": "speech"}],
                },
                {
                    "panel": 2,
                    "action": "Yuki bursts through the door",
                    "characters": ["Yuki"],
                },
            ]
        }
    )
    s = parse("Akira eats alone. Yuki arrives.", client=fake, force_llm=True)
    assert len(s.panels) == 2
    assert s.panels[0].characters == ["Akira"]
    assert s.panels[1].panel == 2  # defaulted sequentially
    assert fake.unloaded  # VRAM released before render


def test_panels_to_script_rejects_empty():
    with pytest.raises(ParseError):
        _panels_to_script({"panels": []}, title="", reading_order=ReadingOrder.RTL,
                          default_color_mode=ColorMode.BW)


def test_panels_to_script_applies_defaults():
    s = _panels_to_script(
        {"panels": [{"panel": 1, "action": "x", "camera": "close-up"}]},
        title="T",
        reading_order=ReadingOrder.LTR,
        default_color_mode=ColorMode.COLOR,
    )
    assert s.reading_order is ReadingOrder.LTR
    assert s.panels[0].color_mode is ColorMode.COLOR


def test_engine_defaults_to_colour():
    """Policy: the engine is colour-only (docs/color-policy.md)."""
    assert Script().default_color_mode is ColorMode.COLOR
    assert parse_canonical("PAGE 1\nPANEL 1\n[ACTION]\na scene").default_color_mode is ColorMode.COLOR


# --- project helpers --------------------------------------------------------


def test_ensure_project_creates_subdirs(tmp_path, monkeypatch):
    from manganation import project

    monkeypatch.setattr(project, "projects_root", lambda: tmp_path)
    root = project.ensure_project("demo")
    assert (root / "characters").is_dir()
    assert (root / "panels").is_dir()


def test_cover_is_page_zero_with_no_text():
    from manganation.script.formats import canonical

    data = canonical.parse(
        "COVER\n[SCENE: Pier — dawn]\n[CHARACTERS: Mio]\n[ACTION]\nMio on the pier.\n"
        "[NOTES]\ntitle goes on top\n\nPAGE 1\nPANEL 1\n[ACTION]\nA gull.\n")
    assert data["problems"] == []
    cover, first = data["panels"]
    assert (cover["page"], cover["panel"], cover["cover"]) == (0, 1, True)
    assert cover["scene_heading"] == "Pier — dawn" and cover["characters"] == ["Mio"]
    assert cover["action"] == "Mio on the pier."
    assert first["cover"] is False and first["scene_heading"] == ""
    assert canonical.looks_canonical("COVER\n[ACTION]\nx\n")


def test_cover_problems_are_reported():
    from manganation.script.formats import canonical

    messages = [p["message"] for p in canonical.parse(
        "COVER\n[DIALOGUE]\nMIO: hi\n[SFX]\nBANG\n[FRAME: wide]\nPANEL 1\n[ACTION]\nx\n"
        "COVER\n")["problems"]]
    assert any("no [DIALOGUE]" in m for m in messages)
    assert any("no [SFX]" in m for m in messages)
    assert any("no [FRAME]" in m for m in messages)
    assert any("no PANEL lines" in m for m in messages)
    assert any("already used" in m for m in messages)
    empty = canonical.parse("COVER\n[SHOT: close-up]\nPAGE 1\nPANEL 1\n[ACTION]\nx\n")
    assert any("needs an [ACTION]" in p["message"] for p in empty["problems"])


def test_cover_prompt_asks_for_no_text():
    from manganation.render.panel import build_negative, build_prompt
    from manganation.script.schema import PanelSpec

    spec = PanelSpec(page=0, panel=1, cover=True, action="Mio on the pier.")
    assert "no text" in build_prompt(spec, {})
    assert "logo" in build_negative(spec, {})
    plain = spec.model_copy(update={"cover": False})
    assert "no text" not in build_prompt(plain, {})


def test_locations_block_declares_places_with_descriptions():
    from manganation.script.formats import canonical

    data = canonical.parse(
        "[LOCATIONS]\nSchool rooftop: open, chain-link fence,\n  water tower, city beyond.\n"
        "The Kitchen — morning: small, steamy.\n\n"
        "PAGE 1\n[SCENE: School rooftop — dusk]\nPANEL 1\n[ACTION]\nx\n"
        "PANEL 2\n[LOCATION: Kitchen (cont'd)]\n[ACTION]\ny\n")
    assert data["problems"] == []
    assert data["locations"] == [
        {"name": "School rooftop",
         "description": "open, chain-link fence, water tower, city beyond."},
        {"name": "The Kitchen", "description": "small, steamy."}]


def test_locations_problems_are_reported():
    from manganation.script.formats import canonical

    data = canonical.parse(
        "[LOCATIONS]\nRoof: a\nRoof — dusk: again\nStairs: b\nnot a location line\n\n"
        "PAGE 1\n[SCENE: Roof — dusk]\nPANEL 1\n[ACTION]\nx\n")
    messages = [p["message"] for p in data["problems"]]
    assert any("Roof is declared twice" in m for m in messages)
    assert any("Stairs is declared but no [SCENE] or [LOCATION] uses it" in m
               for m in messages)
    assert any("Expected a location" in m for m in messages)
    # a scene with no declaration is fine: scripts without the block keep working
    assert canonical.parse("PAGE 1\n[SCENE: Roof]\nPANEL 1\n[ACTION]\nx\n")["locations"] == []


def test_declared_locations_become_project_locations_with_notes():
    from gimp.imanganation.project_store import project_from_script
    from manganation.script.formats import canonical

    text = ("[LOCATIONS]\nHarbor pier: stone pier, fog.\n\nPAGE 1\n"
            "[SCENE: Harbor pier — dawn]\nPANEL 1\n[ACTION]\nx\n"
            "PANEL 2\n[LOCATION: Cellar]\n[ACTION]\ny\n")
    document = project_from_script(canonical.parse(text), title="T", script_file="s.txt",
                                   script_text=text, script_format="canonical")
    assert document["locations"] == [{"name": "Harbor pier", "notes": "stone pier, fog."},
                                     {"name": "Cellar"}]


def test_serialized_cover_drops_its_frame_hint_and_reparses():
    from manganation.script.formats.canonical import parse, serialize

    text = serialize({"panels": [
        {"cover": True, "label": {"page": 0, "panel": 1}, "scene_heading": "Sky",
         "action": "Title art", "aspect_ratio": "3:4", "size": "large"},
        {"label": {"page": 1, "panel": 1}, "scene_heading": "Sky", "action": "Rain",
         "aspect_ratio": "1:1"}]})
    assert text.count("[FRAME") == 1
    assert parse(text)["problems"] == []
