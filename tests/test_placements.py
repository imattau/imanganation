"""Artist placement layers as regional reference masks (instead of reading-order bands)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from manganation.characters.registry import CharacterRegistry
from manganation.render import graphs
from manganation.render import panel as P
from manganation.render.panel import RenderError, RenderResult
from manganation.web.api import create_app


class Fake:
    def __init__(self):
        self.graphs, self.uploads = [], {}

    def is_up(self):
        return True

    def upload_image(self, path):
        with Image.open(path) as im:
            self.uploads[Path(path).name] = im.copy()
        return {"name": Path(path).name}

    def run(self, graph):
        self.graphs.append(graph)
        return [b"x"]


def _cast(tmp_path, who=("Yuki", "Akira")):
    reg = CharacterRegistry.from_path(tmp_path / "cast")
    for name in who:
        Image.new("RGB", (8, 8)).save(tmp_path / f"{name}.png")
        reg.add_user_reference(name, str(tmp_path / f"{name}.png"), "base")
    reg.ensure("Extra")  # in the cast, no reference
    reg.save()
    return tmp_path / "cast"


def _blob(path, size, box, alpha=True):
    if alpha:  # a placement layer exported from GIMP: transparent, painted blob
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        im.paste((255, 0, 0, 255), box)
    else:
        im = Image.new("L", size, 0)
        im.paste(255, box)
    im.save(path)
    return path


def _render(tmp_path, characters, placements=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    fake = Fake()
    panel = {"id": "pnl_abc123", "characters": [{"name": c} for c in characters],
             "action": "Yuki stands over Akira"}
    r = P.render_inline(panel, "prj_test01", 1040, 640, seed=1, client=fake,
                        identity=_cast(tmp_path), outputs=tmp_path / "out",
                        placements=placements)
    return r, fake.graphs[0], fake


def test_graph_loads_mask_images_instead_of_boxes():
    base = {"5": {"class_type": "KSampler", "inputs": {"model": ["1", 0]}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {}}}
    g = graphs.with_regional_ipadapter(
        base, references=[{"image": "y.png", "mask_image": "m.png", "feather": 32},
                          {"image": "a.png", "mask": [0, 0, 64, 64], "canvas_w": 128,
                           "canvas_h": 128}],
        ipadapter="i", clip_vision="v")
    loaded = [n for n in g.values() if n["class_type"] == "LoadImageMask"]
    assert len(loaded) == 1 and loaded[0]["inputs"] == {"image": "m.png", "channel": "red"}
    assert sum(n["class_type"] == "MaskComposite" for n in g.values()) == 1  # Akira's box


def test_single_reference_can_stay_regional():
    base = {"5": {"class_type": "KSampler", "inputs": {"model": ["1", 0]}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {}}}
    g = graphs.with_regional_ipadapter(
        base, references=[{"image": "y.png", "mask": [0, 0, 64, 64], "canvas_w": 128,
                           "canvas_h": 128}],
        ipadapter="i", clip_vision="v", force_regional=True)
    assert g["5"]["inputs"]["model"] == ["apply", 0]
    assert not any(n["class_type"] == "IPAdapterAdvanced" for n in g.values())


def test_placements_replace_bands_and_are_scaled_to_the_canvas(tmp_path):
    # Frame-shaped masks at the frame's own size (520x320), Yuki on the LEFT this time,
    # against the default right-to-left band order.
    yuki = _blob(tmp_path / "yuki.png", (520, 320), (20, 10, 200, 310))
    akira = _blob(tmp_path / "akira.png", (520, 320), (300, 40, 500, 320), alpha=False)
    r, g, fake = _render(tmp_path, ["Yuki", "Akira"], {"yuki": yuki, "Akira": akira})
    masks = [n["inputs"]["image"] for n in g.values() if n["class_type"] == "LoadImageMask"]
    assert len(masks) == 2
    canvas = (r.width, r.height)
    for m in masks:
        up = fake.uploads[m]
        assert up.size == canvas and up.mode == "L"
    left = fake.uploads[masks[0]]
    assert left.getpixel((int(0.1 * r.width), r.height // 2)) > 200  # Yuki's blob, left
    assert left.getpixel((int(0.9 * r.width), r.height // 2)) == 0
    assert r.placements == {"Yuki": str(yuki), "Akira": str(akira)}


def test_characters_without_a_placement_keep_their_band(tmp_path):
    yuki = _blob(tmp_path / "yuki.png", (520, 320), (20, 10, 200, 310))
    _, g, _ = _render(tmp_path, ["Yuki", "Akira"], {"Yuki": yuki})
    assert sum(n["class_type"] == "LoadImageMask" for n in g.values()) == 1
    assert sum(n["class_type"] == "MaskComposite" for n in g.values()) == 1


def test_one_referenced_character_in_a_two_shot_stays_masked(tmp_path):
    """Extra has no reference: Yuki's reference must not pull Extra's face too."""
    _, g, _ = _render(tmp_path, ["Yuki", "Extra"])
    assert g["5"]["inputs"]["model"] == ["apply", 0]
    assert not any(n["class_type"] == "IPAdapterAdvanced" for n in g.values())


def test_bad_placements_fail_clearly(tmp_path):
    good = _blob(tmp_path / "g.png", (52, 32), (2, 2, 20, 30))
    empty = tmp_path / "empty.png"
    Image.new("RGBA", (52, 32), (0, 0, 0, 0)).save(empty)
    with pytest.raises(RenderError, match="isn't in this panel"):
        _render(tmp_path / "a", ["Yuki", "Akira"], {"Bob": good})
    with pytest.raises(RenderError, match="empty"):
        _render(tmp_path / "b", ["Yuki", "Akira"], {"Yuki": empty})


def test_text_bands_follow_the_placement_when_enabled(tmp_path, monkeypatch):
    from manganation.config import load_settings

    settings = load_settings().model_copy(deep=True)
    settings.defaults.ipadapter.regional_text = 0.5
    monkeypatch.setattr(P, "load_settings", lambda: settings)
    yuki = _blob(tmp_path / "yuki.png", (520, 320), (0, 0, 260, 320))
    _, g, _ = _render(tmp_path, ["Yuki", "Akira"], {"Yuki": yuki})
    areas = [n["inputs"] for n in g.values() if n["class_type"] == "ConditioningSetAreaPercentage"]
    yuki_area = min(areas, key=lambda a: a["x"])
    assert yuki_area["x"] == 0 and abs(yuki_area["width"] - 0.5) < 0.02


def test_api_passes_placements_through_both_forms(tmp_path):
    projects, outputs = tmp_path / "projects", tmp_path / "outputs"
    (projects / "rooftop").mkdir(parents=True)
    (projects / "rooftop/panels.json").write_text('{"panels": [{"page": 1, "panel": 1}]}')
    (projects / "rooftop.imanga/tmp").mkdir(parents=True)
    mask = _blob(projects / "rooftop.imanga/tmp/yuki.png", (10, 10), (0, 0, 5, 10))
    seen = {}

    def fake(*args, **kw):
        seen.setdefault("calls", []).append(kw.get("placements"))
        return RenderResult(path="x.png", seq=None, seed=1, width=8, height=8, prompt="p")

    client = TestClient(create_app(root=projects, outputs=outputs, render=fake,
                                   render_inline=fake))
    bodies = [
        {"project_dir": str(projects / "rooftop"), "seq": 1},
        {"project": "prj_test01", "panel": {"id": "pnl_abc123", "characters": []}},
    ]
    for body in bodies:
        job = client.post("/jobs", json={**body, "frame_width": 1, "frame_height": 1,
                                         "placements": {"Yuki": str(mask)}}).json()
        for _ in range(100):
            if client.get(f"/jobs/{job['id']}").json()["status"] in ("done", "error"):
                break
            time.sleep(0.02)
    assert seen["calls"] == [{"Yuki": mask.resolve()}, {"Yuki": mask.resolve()}]
    outside = _blob(tmp_path / "outside.png", (10, 10), (0, 0, 5, 10))
    bad = client.post("/jobs", json={**bodies[1], "frame_width": 1, "frame_height": 1,
                                     "placements": {"Yuki": str(outside)}})
    assert bad.status_code == 400
