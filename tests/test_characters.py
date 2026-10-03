"""Tests for the Phase 2 character layer."""

from __future__ import annotations

import pytest

from manganation.characters.cast import collect_names
from manganation.characters.design import DESIGN_NEGATIVE, build_design_prompt, design_prompt_for
from manganation.characters.registry import CharacterRegistry, slugify
from manganation.characters.schema import (
    AppearanceSpec,
    Cast,
    Character,
    CharacterVersion,
    VersionKind,
)
from manganation.characters.traits import derive_appearance
from manganation.script.schema import PanelSpec, Script

# --- appearance spec --------------------------------------------------------


def test_prompt_tags_are_filled_and_deduped():
    a = AppearanceSpec(
        gender="1girl",
        hair_color="silver hair",
        hair_style="long straight",
        eye_color="red eyes",
        outfit="school uniform",
        accessories=["red ribbon", "red ribbon"],
        descriptors=["stoic"],
    )
    tags = a.prompt_tags()
    assert tags[0] == "1girl"
    assert "silver hair" in tags
    assert tags.count("red ribbon") == 1
    assert tags[-1] == "stoic"


def test_prompt_tags_skip_empty():
    a = AppearanceSpec(gender="1boy")  # everything else blank
    assert a.prompt_tags() == ["1boy"]


# --- character / cast -------------------------------------------------------


def test_character_matches_name_and_alias():
    c = Character(name="Akira", aliases=["Aki"])
    assert c.matches("akira")
    assert c.matches("Aki")
    assert not c.matches("Yuki")


def test_character_versions_and_default():
    c = Character(name="Akira")
    assert c.active_version() is None
    c.add_version(CharacterVersion(id="base", image="a/base.png"))
    assert c.default_version == "base"
    assert c.active_version() is not None
    assert c.active_version().id == "base"

    c.add_version(CharacterVersion(id="school", image="a/school.png", kind=VersionKind.VARIANT))
    c.default_version = "school"
    assert c.active_version() is not None
    assert c.active_version().id == "school"


def test_add_duplicate_version_raises():
    c = Character(name="Akira")
    c.add_version(CharacterVersion(id="base", image="a/base.png"))
    with pytest.raises(ValueError):
        c.add_version(CharacterVersion(id="base", image="a/other.png"))


def test_cast_ensure_is_idempotent():
    cast = Cast()
    a = cast.ensure("Akira")
    b = cast.ensure("Akira")
    assert a is b
    assert cast.names() == ["Akira"]


def test_cast_json_roundtrip():
    cast = Cast(characters=[Character(name="Akira", aliases=["Aki"])])
    restored = Cast.from_json(cast.to_json())
    assert restored.get("Aki").name == "Akira"


# --- registry ---------------------------------------------------------------


def test_slugify():
    assert slugify("Akira Tanaka") == "akira_tanaka"
    assert slugify("Yuki!!") == "yuki"
    assert slugify("") == "character"


def test_registry_add_version_and_save(tmp_path, monkeypatch):
    monkeypatch.setattr("manganation.characters.registry.project_dir", lambda name: tmp_path)
    reg = CharacterRegistry("demo")
    reg.add_version(
        "Akira", CharacterVersion(id="base", image="characters/akira/base.png", seed=42)
    )
    assert reg.cast_file.exists()
    assert (tmp_path / "characters" / "akira" / "manifest.json").exists()

    # reload from disk
    reg2 = CharacterRegistry("demo")
    c = reg2.get("Akira")
    assert c is not None
    assert c.default_version == "base"
    assert c.active_version().seed == 42


def test_registry_add_user_reference(tmp_path, monkeypatch):
    monkeypatch.setattr("manganation.characters.registry.project_dir", lambda name: tmp_path)
    src = tmp_path / "external.png"
    src.write_bytes(b"fake-png")

    reg = CharacterRegistry("demo")
    c = reg.add_user_reference("Akira", str(src))
    assert c.source == "user"
    ref = reg.reference_path("Akira")
    assert ref is not None and ref.exists()
    assert ref.read_bytes() == b"fake-png"


def test_registry_resolve_references_skips_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("manganation.characters.registry.project_dir", lambda name: tmp_path)
    reg = CharacterRegistry("demo")
    reg.ensure("Akira")
    reg.ensure("Yuki")  # no reference image
    resolved = reg.resolve_references(["Akira", "Yuki"])
    assert resolved == {}


def test_registry_set_default_missing_version(tmp_path, monkeypatch):
    monkeypatch.setattr("manganation.characters.registry.project_dir", lambda name: tmp_path)
    reg = CharacterRegistry("demo")
    with pytest.raises(KeyError):
        reg.set_default("Ghost", "base")


def test_registry_replace_version(tmp_path, monkeypatch):
    monkeypatch.setattr("manganation.characters.registry.project_dir", lambda name: tmp_path)
    reg = CharacterRegistry("demo")
    reg.add_version("Akira", CharacterVersion(id="base", image="a/base.png", seed=1))
    reg.add_version(
        "Akira",
        CharacterVersion(id="base", image="a/base.png", seed=2),
        replace=True,
    )
    c = reg.get("Akira")
    assert c is not None
    assert len(c.versions) == 1
    assert c.active_version() is not None
    assert c.active_version().seed == 2


# --- design prompt builder --------------------------------------------------


def test_build_design_prompt_has_quality_and_default_gender():
    p = build_design_prompt(AppearanceSpec(hair_color="black hair"))
    assert "1person" in p.tags  # default gender injected
    assert "cowboy shot" in p.tags
    # Multi-view sheets make IP-Adapter render grids of repeated figures.
    assert not any("sheet" in t for t in p.tags)
    assert "character sheet" in p.negative and "multiple views" in p.negative
    assert p.negative == DESIGN_NEGATIVE


def test_build_design_prompt_dedupes_and_extra():
    p = build_design_prompt(AppearanceSpec(gender="1girl", hair_color="black hair"),
                            extra=["masterpiece"])
    assert p.tags.count("masterpiece") == 1
    assert p.positive.count("cowboy shot") == 1


def test_design_prompt_for_character():
    c = Character(name="Akira", appearance=AppearanceSpec(gender="1boy", hair_color="black hair"))
    p = design_prompt_for(c)
    assert "1boy" in p.tags
    assert "1person" not in p.tags


def test_design_prompt_leads_with_single_figure_control():
    """solo-focus must come first, or SDXL splits the trait list into a multi-figure
    sheet (docs/phase6b.md). This is a regression guard for that failure."""
    p = build_design_prompt(
        AppearanceSpec(gender="1boy", outfit="white shirt, navy pleated trousers, "
                        "grey blazer and red necktie", accessories=["watch"])
    )
    assert p.positive.startswith("solo focus, solo, 1 character, 1boy")
    assert p.tags.index("solo focus") < p.tags.index("1boy")


def test_design_negative_bans_second_figure():
    assert "2people" in DESIGN_NEGATIVE
    assert "2boys" in DESIGN_NEGATIVE and "2girls" in DESIGN_NEGATIVE


# --- trait derivation (stubbed) ---------------------------------------------


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def chat_json(self, messages, *, schema=None):
        return self.payload


def test_derive_appearance_from_llm():
    fake = _FakeClient(
        {
            "gender": "1girl",
            "hair_color": "silver hair",
            "hair_style": "long straight",
            "eye_color": "red eyes",
            "outfit": "school uniform",
            "accessories": ["red ribbon"],
        }
    )
    spec = derive_appearance("Yuki", "Yuki bursts in.", client=fake)
    assert spec.gender == "1girl"
    assert spec.hair_color == "silver hair"
    assert "red ribbon" in spec.accessories


def test_derive_appearance_keeps_known_traits():
    fake = _FakeClient(
        {
            "gender": "1girl",
            "hair_color": "",
            "hair_style": "",
            "eye_color": "",
            "outfit": "",
        }
    )
    known = AppearanceSpec(hair_color="pink hair")
    spec = derive_appearance("Yuki", "text", existing=known, client=fake)
    assert spec.hair_color == "pink hair"  # preserved from existing


def test_derive_appearance_ignores_unknown_fields():
    fake = _FakeClient(
        {
            "gender": "1boy",
            "hair_color": "black hair",
            "hair_style": "short",
            "eye_color": "brown eyes",
            "outfit": "uniform",
            "secret_field": "should be dropped",
        }
    )
    spec = derive_appearance("Akira", "text", client=fake)
    assert not hasattr(spec, "secret_field")


# --- cast assembly ----------------------------------------------------------


def test_collect_names_first_appearance_order():
    script = Script(
        panels=[
            PanelSpec(page=1, panel=1, characters=["Akira"]),
            PanelSpec(page=1, panel=2, characters=["Yuki", "Akira"]),
            PanelSpec(page=2, panel=1, characters=["Yuki"]),
        ]
    )
    assert collect_names(script) == ["Akira", "Yuki"]
