"""Character traits keep expression and body language apart from physical identity."""

from __future__ import annotations

import json

import pytest

from manganation.characters.dataset import variant_specs
from manganation.characters.registry import CharacterRegistry
from manganation.characters.schema import AppearanceSpec, Character, classify_trait
from manganation.characters.traits import APPEARANCE_SCHEMA, SYSTEM_PROMPT
from manganation.render.panel import build_prompt, character_tags
from manganation.script.schema import PanelSpec

YUKI_OLD = {  # as stored before the split (projects/rooftop/characters/yuki)
    "gender": "1girl", "hair_color": "messy white bob with pink tips", "eye_color": "amber eyes",
    "outfit": "navy sailor uniform",
    "accessories": ["white wristwatch"],
    "distinguishing": ["wide toothed grin", "snowflake pin on blazer lapel"],
    "descriptors": ["energetic stance", "slightly messy strands flying"],
}


@pytest.mark.parametrize("tag,kind", [
    ("wide toothed grin", "expression"), ("stoic expression", "expression"),
    ("quiet demeanor", "mannerism"), ("relaxed slouch", "mannerism"),
    ("energetic stance", "mannerism"), ("serious demeanor", "mannerism"),
    ("small scar above left eyebrow", "appearance"), ("slightly messy strands flying",
                                                       "appearance"),
    ("snowflake pin on blazer lapel", "appearance"),
])
def test_classify_trait(tag, kind):
    assert classify_trait(tag) == kind


def test_old_traits_migrate_on_load():
    a = AppearanceSpec(**YUKI_OLD)
    assert a.default_expression == "wide toothed grin"
    assert a.mannerisms == ["energetic stance"]
    assert a.distinguishing == ["snowflake pin on blazer lapel"]
    assert a.descriptors == ["slightly messy strands flying"]
    assert "wide toothed grin" not in a.appearance_tags()
    assert "energetic stance" not in a.appearance_tags()
    assert a.prompt_tags()[-1] == "wide toothed grin"
    assert "energetic stance" in a.prompt_tags(mannerisms=True)
    assert "wide toothed grin" not in a.prompt_tags(expression=False)


def test_migration_is_stable_on_round_trip():
    a = AppearanceSpec(**YUKI_OLD)
    again = AppearanceSpec(**json.loads(a.model_dump_json()))
    assert again == a


def test_manifest_files_migrate_when_the_registry_loads_and_saves(tmp_path):
    reg = CharacterRegistry.from_path(tmp_path)
    reg.ensure("Yuki")
    reg.save()
    raw = json.loads((tmp_path / "characters.json").read_text())
    raw["characters"][0]["appearance"] = YUKI_OLD  # an old-style file on disk
    (tmp_path / "characters.json").write_text(json.dumps(raw))
    reg = CharacterRegistry.from_path(tmp_path)
    assert reg.get("Yuki").appearance.default_expression == "wide toothed grin"
    reg.save()
    saved = json.loads((tmp_path / "characters.json").read_text())["characters"][0]
    assert saved["appearance"]["default_expression"] == "wide toothed grin"
    assert "wide toothed grin" not in saved["appearance"]["distinguishing"]


def _registry(tmp_path):
    reg = CharacterRegistry.from_path(tmp_path)
    reg.ensure("Yuki")
    reg.get("Yuki").appearance = AppearanceSpec(**YUKI_OLD)
    reg.save()
    return tmp_path


def test_render_uses_the_default_expression_only_when_the_panel_has_none(tmp_path):
    root = _registry(tmp_path)
    style = {"prompt_prefix": "manga panel, "}
    plain = PanelSpec(page=1, panel=1, characters=["Yuki"], action="waves")
    surprised = PanelSpec(page=1, panel=1, characters=["Yuki"], action="looks up",
                          expressions={"Yuki": "surprised, open mouth"})
    p1 = build_prompt(plain, style, character_tags(root, ["Yuki"], plain.expressions))
    p2 = build_prompt(surprised, style,
                      character_tags(root, ["Yuki"], surprised.expressions))
    assert "wide toothed grin" in p1
    assert "wide toothed grin" not in p2 and "Yuki surprised, open mouth" in p2
    assert "energetic stance" not in p1 + p2  # the action decides the pose


def test_dataset_identity_captions_are_appearance_only():
    yuki = Character(name="Yuki", appearance=AppearanceSpec(**YUKI_OLD))
    for spec in variant_specs(yuki, 3):
        identity = spec.caption.split(", ")
        assert "wide toothed grin" not in identity and "energetic stance" not in identity
        assert "amber eyes" in identity


def test_llm_is_asked_for_the_new_fields():
    props = APPEARANCE_SCHEMA["properties"]
    assert "default_expression" in props and "mannerisms" in props
    assert "never an expression or a pose" in SYSTEM_PROMPT.replace("\\\n", "")
