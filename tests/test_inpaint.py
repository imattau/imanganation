"""Tests for masked panel inpaint (the GIMP "Inpaint Selection" engine path)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manganation.render import graphs
from manganation.render.inpaint import (
    InpaintError,
    inpaint_panel,
    mask_channel_for,
    output_path,
)
from manganation.script.schema import PanelSpec, Script
from manganation.web.api import create_app

# --- graph shape --------------------------------------------------------------


def test_inpaint_graph_masks_the_latent():
    g = graphs.inpaint(
        ckpt="noobaiXL.safetensors", image="a.png", mask="m.png",
        prompt="fix hand", negative="bad", seed=3, prefix="t",
        denoise=0.85, grow_mask_by=8,
    )
    kinds = {n["class_type"] for n in g.values()}
    assert {"LoadImage", "LoadImageMask", "GrowMask", "VAEEncode",
            "SetLatentNoiseMask", "KSampler", "VAEDecode"} <= kinds
    assert g["9"]["inputs"]["channel"] == "alpha"
    assert g["10"]["inputs"]["expand"] == 8
    assert g["11"]["class_type"] == "SetLatentNoiseMask"
    # the sampler denoises the noise-masked latent, not the raw encode
    assert g["5"]["inputs"]["latent_image"] == ["11", 0]
    assert g["5"]["inputs"]["denoise"] == 0.85


def test_inpaint_graph_mask_channel_override():
    g = graphs.inpaint(
        ckpt="c", image="a.png", mask="m.png", prompt="p", negative="n",
        seed=1, prefix="t", mask_channel="red",
    )
    assert g["9"]["inputs"]["channel"] == "red"


# --- mask channel detection ---------------------------------------------------


def test_mask_channel_alpha_for_transparent_png(tmp_path):
    from PIL import Image

    p = tmp_path / "m.png"
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(p)
    assert mask_channel_for(p) == "alpha"


def test_mask_channel_red_for_opaque_mask(tmp_path):
    from PIL import Image

    p = tmp_path / "m.png"
    Image.new("RGB", (64, 64), (255, 255, 255)).save(p)
    assert mask_channel_for(p) == "red"


# --- output naming ------------------------------------------------------------


def test_output_path_uses_inpaint_suffix(tmp_path):
    (tmp_path / "panels").mkdir()
    assert output_path(tmp_path, 3).name == "003_inpaint.png"
    (tmp_path / "panels/003_inpaint.png").touch()
    assert output_path(tmp_path, 3).name == "003_inpaint_take02.png"


# --- inpaint_panel with a fake ComfyUI ----------------------------------------


class FakeComfy:
    def __init__(self):
        self.graphs = []
        self.uploads = []

    def is_up(self):
        return True

    def upload_image(self, path):
        self.uploads.append(path)
        return {"name": Path(path).name}

    def run(self, graph):
        self.graphs.append(graph)
        return [b"\x89PNG fake"]


def _project(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "panels").mkdir()
    spec = PanelSpec(page=1, panel=1, characters=["Akira"], action="eats lunch")
    (tmp_path / "panels.json").write_text(Script(panels=[spec]).to_json())
    from PIL import Image

    Image.new("RGB", (128, 128), "white").save(tmp_path / "panels/001.png")
    # a same-size transparent selection mask
    Image.new("RGBA", (128, 128), (0, 0, 0, 0)).save(tmp_path / "mask.png")
    return tmp_path


def test_inpaint_panel_paints_region_and_writes_take(tmp_path):
    project = _project(tmp_path)
    comfy = FakeComfy()
    r = inpaint_panel(project, 1, mask=project / "mask.png", prompt="a red apple",
                      seed=9, denoise=0.9, client=comfy)

    assert Path(r.path).name == "001_inpaint.png"
    assert r.mask.endswith("mask.png")
    g = comfy.graphs[0]
    assert g["8"]["inputs"]["image"] == "001.png"       # init = newest panel
    assert g["9"]["inputs"]["image"] == "mask.png"
    assert g["2"]["inputs"]["text"] == "a red apple"
    assert g["5"]["inputs"]["seed"] == 9
    # both the init and the mask were uploaded
    assert any("001.png" in p for p in comfy.uploads)
    assert any("mask.png" in p for p in comfy.uploads)
    sidecar = json.loads(Path(r.path).with_suffix(".json").read_text())
    assert sidecar["mask"].endswith("mask.png")


def test_inpaint_panel_uses_explicit_source(tmp_path):
    project = _project(tmp_path)
    comfy = FakeComfy()
    r = inpaint_panel(project, 1, mask=project / "mask.png", prompt="x",
                      source=project / "panels/001.png", client=comfy)
    assert r.source.endswith("001.png")
    assert r.path.endswith("001_inpaint.png")


def test_inpaint_panel_rejects_mask_size_mismatch(tmp_path):
    project = _project(tmp_path)
    from PIL import Image

    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(project / "bad.png")
    with pytest.raises(InpaintError, match="mask size"):
        inpaint_panel(project, 1, mask=project / "bad.png", prompt="x",
                      client=FakeComfy())


def test_inpaint_panel_rejects_mask_outside_project(tmp_path):
    project = _project(tmp_path / "proj")
    outside = tmp_path / "outside.png"
    from PIL import Image

    Image.new("RGBA", (128, 128), (0, 0, 0, 0)).save(outside)
    with pytest.raises(InpaintError, match="inside the project"):
        inpaint_panel(project, 1, mask=outside, prompt="x", client=FakeComfy())


def test_inpaint_panel_rejects_bad_denoise(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(InpaintError, match="denoise"):
        inpaint_panel(project, 1, mask=project / "mask.png", prompt="x",
                      denoise=0.0, client=FakeComfy())


def test_inpaint_panel_requires_a_render(tmp_path):
    project = _project(tmp_path)
    (project / "panels/001.png").unlink()
    with pytest.raises(InpaintError, match="no rendered image"):
        inpaint_panel(project, 1, mask=project / "mask.png", prompt="x",
                      client=FakeComfy())


# --- job API ------------------------------------------------------------------


def _wait(client, job_id):
    import time

    for _ in range(100):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_api_inpaint_endpoint(tmp_path):
    from manganation.render.inpaint import InpaintResult

    project = _project(tmp_path / "proj")

    def fake(path, seq, *, mask, prompt, source=None, denoise=None, grow_mask_by=None,
             seed=None):
        return InpaintResult(path=str(project / "panels/001_inpaint.png"), seq=seq,
                             source=str(project / "panels/001.png"), mask=str(mask),
                             prompt=prompt, width=128, height=128, denoise=denoise or 0.85,
                             grow_mask_by=grow_mask_by or 8, seed=seed or 0)

    client = TestClient(create_app(render=lambda *a, **k: None, root=tmp_path,
                                   inpaint=fake))
    resp = client.post("/inpaint", json={"project_dir": str(project), "seq": 1,
                                         "mask": "mask.png", "prompt": "a red apple",
                                         "denoise": 0.9})
    assert resp.status_code == 202
    job = _wait(client, resp.json()["id"])
    assert job["status"] == "done" and job["kind"] == "inpaint"
    assert job["result"]["prompt"] == "a red apple"


def test_api_inpaint_rejects_mask_outside_project(tmp_path):
    project = _project(tmp_path / "proj")
    client = TestClient(create_app(render=lambda *a, **k: None, root=tmp_path,
                                   inpaint=lambda *a, **k: None))
    resp = client.post("/inpaint", json={"project_dir": str(project), "seq": 1,
                                         "mask": "/etc/passwd", "prompt": "x"})
    assert resp.status_code == 400


def test_api_inpaint_missing_mask_404(tmp_path):
    project = _project(tmp_path / "proj")
    client = TestClient(create_app(render=lambda *a, **k: None, root=tmp_path,
                                   inpaint=lambda *a, **k: None))
    resp = client.post("/inpaint", json={"project_dir": str(project), "seq": 1,
                                         "mask": "nope.png", "prompt": "x"})
    assert resp.status_code == 404
