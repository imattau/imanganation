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
    assert parse_canonical("Panel 1: a scene").default_color_mode is ColorMode.COLOR


# --- project helpers --------------------------------------------------------


def test_ensure_project_creates_subdirs(tmp_path, monkeypatch):
    from manganation import project

    monkeypatch.setattr(project, "projects_root", lambda: tmp_path)
    root = project.ensure_project("demo")
    assert (root / "characters").is_dir()
    assert (root / "panels").is_dir()
