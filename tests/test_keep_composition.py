"""Keep composition: renders guided by an existing take's edges (ControlNet canny)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from manganation.characters.registry import CharacterRegistry
from manganation.render import graphs
from manganation.render import panel as P
from manganation.render.panel import RenderError, RenderResult, controlnet_file
from manganation.web.api import create_app, required_models


class Fake:
    def __init__(self):
        self.graphs, self.uploads = [], {}

    def is_up(self):
        return True

    def upload_image(self, path):
        with Image.open(path) as im:
            self.uploads[Path(path).name] = im.size
        return {"name": Path(path).name}

    def run(self, graph):
        self.graphs.append(graph)
        return [b"x"]


def _base():
    return {"2": {"class_type": "CLIPTextEncode", "inputs": {}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {}},
            "5": {"class_type": "KSampler",
                  "inputs": {"positive": ["2", 0], "negative": ["3", 0], "model": ["1", 0]}}}


def test_controlnet_wraps_the_existing_conditioning():
    g = graphs.with_controlnet(_base(), image="take.png", controlnet="cn.safetensors",
                               strength=0.6, end=0.65)
    assert g["cn_edges"]["class_type"] == "Canny"
    assert g["cn_edges"]["inputs"]["image"] == ["cn_image", 0]
    apply = g["cn_apply"]["inputs"]
    assert apply["positive"] == ["2", 0] and apply["negative"] == ["3", 0]
    assert apply["image"] == ["cn_edges", 0] and apply["strength"] == 0.6
    assert apply["end_percent"] == 0.65
    assert g["5"]["inputs"]["positive"] == ["cn_apply", 0]
    assert g["5"]["inputs"]["negative"] == ["cn_apply", 1]


def test_controlnet_composes_with_a_regional_chain():
    base = _base()
    base["5"]["inputs"]["positive"] = ["cond_24", 0]  # e.g. after regional conditioning
    g = graphs.with_controlnet(base, image="t.png", controlnet="cn")
    assert g["cn_apply"]["inputs"]["positive"] == ["cond_24", 0]


def _render(tmp_path, guide=None, strength=None, characters=("Yuki",)):
    reg = CharacterRegistry.from_path(tmp_path / "cast")
    for name in characters:
        Image.new("RGB", (8, 8)).save(tmp_path / f"{name}.png")
        reg.add_user_reference(name, str(tmp_path / f"{name}.png"), "base")
    fake = Fake()
    panel = {"id": "pnl_abc123", "characters": [{"name": c} for c in characters],
             "action": "close-up", "expressions": {"Yuki": "surprised"}}
    r = P.render_inline(panel, "prj_test01", 1040, 640, seed=1, client=fake,
                        identity=tmp_path / "cast", outputs=tmp_path / "out", guide=guide,
                        guide_strength=strength)
    return r, fake.graphs[0], fake


def test_guide_take_is_resized_to_the_canvas_and_wired(tmp_path):
    take = tmp_path / "take.png"
    Image.new("RGB", (1920, 1184), "white").save(take)  # e.g. a hi-res take
    r, g, fake = _render(tmp_path, guide=take)
    guide_name = g["cn_image"]["inputs"]["image"]
    assert fake.uploads[guide_name] == (r.width, r.height)
    assert g["cn_model"]["inputs"]["control_net_name"].startswith("noob_sdxl_controlnet_canny")
    assert g["cn_apply"]["inputs"]["strength"] == 0.7  # settings default
    assert r.guide == str(take)


def test_guide_strength_override_and_multi_character(tmp_path):
    take = tmp_path / "take.png"
    Image.new("RGB", (64, 40)).save(take)
    _, g, _ = _render(tmp_path, guide=take, strength=0.4, characters=("Yuki", "Akira"))
    assert g["cn_apply"]["inputs"]["strength"] == 0.4
    assert g["5"]["inputs"]["model"] == ["apply", 0]  # regional IP-Adapter still there


def test_no_guide_no_controlnet(tmp_path):
    r, g, _ = _render(tmp_path)
    assert "cn_apply" not in g and r.guide is None


def test_missing_guide_or_unknown_model_fails(tmp_path):
    with pytest.raises(RenderError, match="guide image not found"):
        _render(tmp_path, guide=tmp_path / "gone.png")
    with pytest.raises(RenderError, match="unknown ControlNet"):
        controlnet_file({"controlnets": {}}, "canny")


def test_api_passes_the_guide_and_status_checks_the_model(tmp_path):
    projects = tmp_path / "projects"
    take = projects / "rooftop.imanga/takes/t.png"
    take.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(take)
    seen = []

    def fake(*args, **kw):
        seen.append((kw.get("guide"), kw.get("guide_strength")))
        return RenderResult(path="x.png", seq=None, seed=1, width=8, height=8, prompt="p")

    client = TestClient(create_app(root=projects, outputs=tmp_path / "out",
                                   render_inline=fake))
    job = client.post("/jobs", json={"project": "prj_test01", "panel": {"id": "pnl_abc123"},
                                     "frame_width": 1, "frame_height": 1,
                                     "guide": str(take), "guide_strength": 0.5}).json()
    for _ in range(100):
        if client.get(f"/jobs/{job['id']}").json()["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert seen == [(take.resolve(), 0.5)]
    outside = tmp_path / "o.png"
    Image.new("RGB", (8, 8)).save(outside)
    assert client.post("/jobs", json={"project": "prj_test01", "panel": {"id": "pnl_abc123"},
                                      "frame_width": 1, "frame_height": 1,
                                      "guide": str(outside)}).status_code == 400

    from manganation.config import load_models, load_settings

    roles = {m["role"] for m in required_models(load_settings(), load_models(), tmp_path)}
    assert "controlnet (canny)" in roles
