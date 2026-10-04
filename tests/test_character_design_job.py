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
        assert not self.unloaded, "the LLM must not run after it was unloaded"
        self.messages.append(messages)
        return {"gender": "1girl", "hair_color": "bleached blonde hair", "hair_style": "bob",
                "eye_color": "grey eyes", "outfit": "school blazer worn open",
                "distinguishing": ["scar on chin"]}

    def unload(self):
        self.unloaded = True


class FakeComfy:
    def __init__(self, llm):
        self.llm, self.graphs = llm, []

    def is_up(self):
        return True

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


def test_existing_traits_are_kept_unless_replaced(tmp_path):
    reg = CharacterRegistry.from_path(tmp_path)
    llm = FakeLLM()
    design_character(reg, "Rin", "girl", llm=llm, comfy=FakeComfy(llm))
    llm2 = FakeLLM()
    design_character(reg, "Rin", "", llm=llm2, comfy=FakeComfy(llm2), replace=True)
    assert llm2.messages == []  # no description: keep the traits, only redesign
    with pytest.raises(ValueError, match="describe"):
        design_character(reg, "Nobody", "", llm=FakeLLM(), comfy=FakeComfy(FakeLLM()))


def test_assemble_cast_uses_the_scripts_cast_block(tmp_path, monkeypatch):
    seen = {}

    def fake_derive(name, script_text, *, existing=None, settings=None, description=""):
        seen[name] = description
        from manganation.characters.schema import AppearanceSpec
        return AppearanceSpec(gender="1girl", hair_color="red hair")

    monkeypatch.setattr("manganation.characters.cast.derive_appearance", fake_derive)
    script = parse_canonical("CHARACTERS\nRIN (aka Rinny): red hair, sharp eyes\n\n"
                             "PAGE 1\nPanel 1: Akira looks at Rin.\nAKIRA: Hi.\n")
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

    def fake_design(reg, name, description, *, aliases, seed, replace):
        calls.append((name, description, aliases, replace))
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
