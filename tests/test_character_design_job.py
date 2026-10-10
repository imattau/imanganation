"""New characters from the author's description: traits, LLM unload, design sheet."""

from __future__ import annotations

import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from manganation.characters.cast import assemble_cast, design_character
from manganation.characters.registry import CharacterRegistry
from manganation.script.formats.mangaplay import parse_canonical
from manganation.web.api import create_app


class FakeLLM:
    def __init__(self):
        self.messages, self.unloaded = [], False

    def chat_json(self, messages, *, schema=None):
        self.unloaded = False  # a call loads it again
        self.messages.append(messages)
        return {"gender": "1girl", "hair_color": "bleached blonde hair", "hair_style": "bob",
                "eye_color": "grey eyes", "outfit": "school blazer worn open",
                "distinguishing": ["scar on chin"]}

    def unload(self):
        self.unloaded = True


class FakeComfy:
    def __init__(self, llm):
        self.llm, self.graphs, self.freed = llm, [], False

    def free(self):
        assert not self.llm.messages, "ComfyUI must free the GPU before the LLM runs"
        self.freed = True

    def is_up(self):
        return True

    def upload_image(self, path, subfolder="", overwrite=True):
        self.uploads = [*getattr(self, "uploads", []), path]
        return {"name": "uploaded.png"}

    def run(self, graph):
        assert self.llm.unloaded or not self.llm.messages, \
            "diffusion must wait for the LLM to release the GPU"
        self.graphs.append(graph)
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8)).save(buffer, "PNG")
        return [buffer.getvalue()]


def test_design_character_derives_from_the_description_then_renders(tmp_path):
    reg = CharacterRegistry.from_path(tmp_path)
    llm = FakeLLM()
    comfy = FakeComfy(llm)
    result = design_character(reg, "Rin", "tall delinquent girl, scar on chin",
                              aliases=["Rin-san"], seed=7, llm=llm, comfy=comfy)
    assert result.created and result.version_id == "base" and result.seed == 7
    assert "scar on chin" in llm.messages[0][-1]["content"]  # description is the input
    assert "authoritative" in llm.messages[0][-1]["content"]
    rin = CharacterRegistry.from_path(tmp_path).get("Rin-san")
    assert rin.appearance.hair_color == "bleached blonde hair"
    assert rin.notes == "tall delinquent girl, scar on chin"
    assert rin.default_version == "base"
    assert CharacterRegistry.from_path(tmp_path).reference_path("Rin").is_file()
    assert "bleached blonde hair" in comfy.graphs[0]["2"]["inputs"]["text"]
    assert comfy.freed


def test_redesign_adds_a_version_and_keeps_traits_without_a_description(tmp_path):
    reg = CharacterRegistry.from_path(tmp_path)
    llm = FakeLLM()
    design_character(reg, "Rin", "girl", llm=llm, comfy=FakeComfy(llm))
    llm2 = FakeLLM()
    again = design_character(reg, "Rin", "", llm=llm2, comfy=FakeComfy(llm2), redesign=True)
    assert llm2.messages == []  # no description: keep the traits, only redesign
    rin = CharacterRegistry.from_path(tmp_path).get("Rin")
    assert again.version_id == "design-02" and rin.default_version == "design-02"
    assert [v.id for v in rin.versions] == ["base", "design-02"]  # the first is kept
    with pytest.raises(ValueError, match="describe"):
        design_character(reg, "Nobody", "", llm=FakeLLM(), comfy=FakeComfy(FakeLLM()))


def test_assemble_cast_uses_the_scripts_cast_block(tmp_path, monkeypatch):
    seen = {}

    def fake_derive(name, script_text, *, existing=None, settings=None, description=""):
        seen[name] = description
        from manganation.characters.schema import AppearanceSpec
        return AppearanceSpec(gender="1girl", hair_color="red hair")

    monkeypatch.setattr("manganation.characters.cast.derive_appearance", fake_derive)
    script = parse_canonical("[CHARACTERS]\nRIN (aka Rinny): red hair, sharp eyes\n"
                             "AKIRA:\n\nPAGE 1\nPANEL 1\n[ACTION]\nAkira looks at Rin.\n"
                             "[DIALOGUE]\nAKIRA: Hi.\n")
    reg = CharacterRegistry.from_path(tmp_path)
    plan = assemble_cast(script, "x", registry=reg, script_text="...")
    assert plan.names == ["Rin", "Akira"]
    assert seen == {"Rin": "red hair, sharp eyes", "Akira": ""}
    rin = CharacterRegistry.from_path(tmp_path).get("Rinny")
    assert rin.notes == "red hair, sharp eyes"


def test_api_queues_a_character_job(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    calls = []

    def fake_design(reg, name, description, *, aliases, seed, redesign):
        calls.append((name, description, aliases, redesign))
        from manganation.characters.cast import CharacterDesign
        return CharacterDesign(name=name, created=True, appearance={}, version_id="base",
                               image="x.png", seed=1, prompt="p")

    client = TestClient(create_app(root=projects, outputs=tmp_path / "out",
                                   design_character=fake_design))
    body = {"project": "prj_test01", "name": "Rin", "description": "red hair"}
    job = client.post("/characters", json=body).json()
    assert job["kind"] == "character"
    for _ in range(100):
        state = client.get(f"/jobs/{job['id']}").json()
        if state["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert state["status"] == "done" and state["result"]["version_id"] == "base"
    assert calls == [("Rin", "red hair", [], False)]
    assert client.post("/characters", json={**body, "description": ""}).status_code == 422
    assert client.post("/characters", json={"name": "Rin"}).status_code == 422


def test_api_parses_a_script_into_plain_dicts(tmp_path):
    client = TestClient(create_app(root=tmp_path, outputs=tmp_path / "out"))
    job = client.post("/scripts/parse", json={
        "text": "[CHARACTERS]\nRIN: red hair\n\nPAGE 1\nPANEL 1\n[ACTION]\nRin waves.\n"}).json()
    assert job["kind"] == "parse"
    for _ in range(100):
        state = client.get(f"/jobs/{job['id']}").json()
        if state["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    result = state["result"]
    assert result["format"] == "canonical"
    assert result["cast"] == [{"name": "Rin", "aliases": [], "description": "red hair"}]
    assert result["panels"][0]["characters"] == ["Rin"]
    assert result["problems"] == []


@pytest.mark.parametrize("engine", ["qwen_image_21", "z_anime"])
def test_design_character_renders_with_the_chosen_engine(tmp_path, engine):
    reg = CharacterRegistry(tmp_path / "chars")
    llm = FakeLLM()
    comfy = FakeComfy(llm)
    result = design_character(reg, "Rin", "tall delinquent girl", llm=llm, comfy=comfy,
                              engine=engine)
    assert "exactly one person and nobody else" in result.prompt
    assert any(n["class_type"] in ("TextEncodeQwenImage21", "CLIPTextEncode")
               for n in comfy.graphs[-1].values())


def test_another_reference_adds_a_variant_and_leaves_the_default_and_traits(tmp_path):
    reg = CharacterRegistry.from_path(tmp_path)
    llm = FakeLLM()
    design_character(reg, "Rin", "girl", seed=7, llm=llm, comfy=FakeComfy(llm))
    llm2, comfy2 = FakeLLM(), None
    comfy2 = FakeComfy(llm2)
    result = design_character(reg, "Rin", "ignored", llm=llm2, comfy=comfy2,
                              variant={"id": "summer", "description": "white sundress, straw hat"})
    assert llm2.messages == []  # traits are not re-derived
    assert result.version_id == "summer" and result.seed == 7  # the default's seed
    assert "white sundress, straw hat" in comfy2.graphs[0]["2"]["inputs"]["text"]
    # the default reference steers it: same person, not a lookalike
    assert comfy2.uploads == [str(tmp_path / "characters/rin/base.png")]
    graph = comfy2.graphs[0]
    assert graph["8"]["inputs"]["image"] == "uploaded.png"
    assert graph["11"]["class_type"] == "IPAdapterAdvanced"
    assert graph["5"]["inputs"]["model"] == ["11", 0]
    rin = CharacterRegistry.from_path(tmp_path).get("Rin")
    assert rin.default_version == "base" and rin.notes == "girl"
    assert [v.id for v in rin.versions] == ["base", "summer"]
    assert rin.version("summer").kind == "variant"
    with pytest.raises(ValueError):  # a name is used once
        design_character(reg, "Rin", "", llm=llm2, comfy=comfy2,
                         variant={"id": "summer", "description": "again"})
    with pytest.raises(ValueError):  # needs a first design
        design_character(reg, "Nobody", "", llm=llm2, comfy=comfy2,
                         variant={"id": "x", "description": "y"})


def test_api_variant_reference_rules(tmp_path):
    from manganation.script.schema import PanelSpec, Script

    projects = tmp_path / "projects"
    (projects / "p").mkdir(parents=True)
    (projects / "p" / "panels.json").write_text(
        Script(panels=[PanelSpec(page=1, panel=1, characters=["Rin"])]).to_json())
    reg = CharacterRegistry.from_path(projects / "p")
    reg.ensure("Rin")
    client = TestClient(create_app(root=projects, outputs=tmp_path / "out",
                                   design_character=lambda *a, **k: None))
    body = {"project_dir": str(projects / "p"), "name": "Rin", "variant_id": "summer",
            "variant_description": "sundress"}
    assert client.post("/characters", json=body).status_code == 409  # no design yet
    assert client.post("/characters", json={**body, "variant_description": ""}).status_code == 422


def test_qwen_variant_prompt_points_at_the_existing_design():
    from manganation.characters.design import build_prose_design_prompt
    from manganation.characters.schema import AppearanceSpec

    plain = build_prose_design_prompt(AppearanceSpec(), variation="a sundress")
    assert "<image1>" not in plain.positive and "For this image: a sundress." in plain.positive
    tied = build_prose_design_prompt(AppearanceSpec(), variation="a sundress",
                                     has_reference=True)
    assert "same character as in <image1>" in tied.positive
