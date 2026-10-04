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
    normalized_mask,
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


def test_inpaint_graph_composites_original_outside_the_mask():
    """Only the masked region may change: the decode is blended over the original."""
    g = graphs.inpaint(ckpt="c", image="a.png", mask="m.png", prompt="p", negative="n",
                       seed=1, prefix="t", grow_mask_by=8)
    comp = g["15"]
    assert comp["class_type"] == "ImageCompositeMasked"
    assert comp["inputs"]["destination"] == ["8", 0]  # the original init image
    assert comp["inputs"]["source"] == ["6", 0]       # the decoded repaint
    assert g["14"]["class_type"] == "ImageToMask" and comp["inputs"]["mask"] == ["14", 0]
    assert g["13"]["inputs"]["image"] == ["12", 0] and g["12"]["inputs"]["mask"] == ["10", 0]
    assert g["7"]["inputs"]["images"] == ["15", 0]   # save the composite, not the decode
    assert graphs.inpaint(ckpt="c", image="a", mask="m", prompt="p", negative="n", seed=1,
                          prefix="t", grow_mask_by=0)["13"]["inputs"]["blur_radius"] == 1


def test_inpaint_graph_mask_channel_override():
    g = graphs.inpaint(
        ckpt="c", image="a.png", mask="m.png", prompt="p", negative="n",
        seed=1, prefix="t", mask_channel="red",
    )
    assert g["9"]["inputs"]["channel"] == "red"


# --- mask channel detection ---------------------------------------------------


def test_normalized_mask_alpha_selection_means_repaint(tmp_path):
    """A transparent export's *opaque* (selected) pixels are the ones to repaint.
    ComfyUI would read alpha inverted, so the engine normalises first."""
    from PIL import Image

    p = tmp_path / "m.png"
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    im.paste((255, 255, 255, 255), (16, 16, 48, 48))
    im.save(p)
    m = normalized_mask(p)
    assert m.mode == "L" and m.getpixel((32, 32)) == 255 and m.getpixel((2, 2)) == 0


def test_normalized_mask_opaque_white_means_repaint(tmp_path):
    from PIL import Image

    p = tmp_path / "m.png"
    im = Image.new("RGB", (64, 64), "black")
    im.paste((255, 255, 255), (0, 0, 32, 64))
    im.save(p)
    m = normalized_mask(p)
    assert m.getpixel((8, 8)) == 255 and m.getpixel((60, 8)) == 0


def test_output_path_uses_inpaint_suffix(tmp_path):
    (tmp_path / "panels").mkdir()
    assert output_path(tmp_path, 3).name == "003_inpaint.png"
    (tmp_path / "panels/003_inpaint.png").touch()
    assert output_path(tmp_path, 3).name == "003_inpaint_take02.png"


# --- inpaint_panel with a fake ComfyUI ----------------------------------------


class FakeComfy:
    """Returns a solid red "repaint" at the size of the uploaded crop."""

    def __init__(self):
        self.graphs = []
        self.uploads = []
        self.sizes = []

    def is_up(self):
        return True

    def upload_image(self, path):
        from PIL import Image

        self.uploads.append(path)
        with Image.open(path) as im:  # the temp crop is gone by run() time
            self.sizes.append(im.size)
        return {"name": Path(path).name}

    def run(self, graph):
        import io

        from PIL import Image

        self.graphs.append(graph)
        buf = io.BytesIO()
        Image.new("RGB", self.sizes[0], (255, 0, 0)).save(buf, "PNG")
        return [buf.getvalue()]


def _project(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "panels").mkdir()
    spec = PanelSpec(page=1, panel=1, characters=["Akira"], action="eats lunch")
    (tmp_path / "panels.json").write_text(Script(panels=[spec]).to_json())
    from PIL import Image

    Image.new("RGB", (128, 128), "white").save(tmp_path / "panels/001.png")
    # a same-size transparent selection mask with a selected square
    m = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    m.paste((255, 255, 255, 255), (48, 48, 80, 80))
    m.save(tmp_path / "mask.png")
    return tmp_path


def test_inpaint_panel_paints_region_and_writes_take(tmp_path):
    project = _project(tmp_path)
    comfy = FakeComfy()
    r = inpaint_panel(project, 1, mask=project / "mask.png", prompt="a red apple",
                      seed=9, denoise=0.9, client=comfy)

    assert Path(r.path).name == "001_inpaint.png"
    assert r.mask.endswith("mask.png")
    g = comfy.graphs[0]
    assert g["8"]["inputs"]["image"].endswith("_inpaint_crop.png")  # crop of newest take
    assert g["9"]["inputs"]["image"].endswith("_inpaint_mask.png")  # the normalised copy
    assert g["9"]["inputs"]["channel"] == "red"
    assert g["2"]["inputs"]["text"].endswith(", a red apple")  # style prefix + prompt
    assert g["2"]["inputs"]["text"] == r.positive and r.prompt == "a red apple"
    assert g["5"]["inputs"]["seed"] == 9
    # both the init and the mask were uploaded
    assert any("_crop.png" in p for p in comfy.uploads)
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


def test_api_inpaint_missing_explicit_source_404(tmp_path):
    """A named source that's gone must fail, not silently inpaint another take."""
    project = _project(tmp_path / "proj")
    client = TestClient(create_app(render=lambda *a, **k: None, root=tmp_path,
                                   inpaint=lambda *a, **k: None))
    resp = client.post("/inpaint", json={"project_dir": str(project), "seq": 1,
                                         "mask": "mask.png", "prompt": "x",
                                         "source": "panels/001_take09.png"})
    assert resp.status_code == 404


# --- crop-and-stitch ----------------------------------------------------------


def _mask(size, box):
    from PIL import Image

    m = Image.new("L", size, 0)
    m.paste(255, box)
    return m


def test_crop_box_adds_context_and_stays_inside():
    from manganation.render.inpaint import crop_box

    m = _mask((2000, 2000), (1000, 1000, 1100, 1060))  # 100x60 selection
    x0, y0, x1, y1 = crop_box(m, context=0.5, min_side=256, grow=8)
    assert x0 <= 1000 - 50 and x1 >= 1100 + 50 and y0 <= 1000 - 50 and y1 >= 1060 + 50
    assert x1 - x0 >= 256 and y1 - y0 >= 256
    assert max((x1 - x0) / (y1 - y0), (y1 - y0) / (x1 - x0)) <= 2.0


def test_crop_box_clamps_at_edges_and_rejects_empty():
    from PIL import Image

    from manganation.render.inpaint import crop_box

    x0, y0, x1, y1 = crop_box(_mask((300, 900), (0, 0, 40, 40)), context=0.5, min_side=256)
    assert (x0, y0) == (0, 0) and x1 <= 300 and y1 <= 900 and x1 - x0 >= 256
    assert crop_box(Image.new("L", (64, 64), 0)) is None


def test_inpaint_paints_a_1mp_crop_and_keeps_outside_pixels_exact(tmp_path):
    """Small selection in a big take: painted at ~1 MP, stitched back; everything
    outside the grown, softened mask is the original, pixel for pixel."""
    from PIL import Image, ImageChops

    from manganation.render.inpaint import blend_mask

    project = _project(tmp_path)
    noise = Image.effect_noise((1400, 1600), 60)  # busy texture: any resample shows
    src = Image.merge("RGB", (noise, noise.rotate(90, expand=False), noise.transpose(0)))
    src.save(project / "panels/001.png")
    sel = Image.new("RGBA", (1400, 1600), (0, 0, 0, 0))
    sel.paste((255, 255, 255, 255), (700, 800, 780, 860))
    sel.save(project / "mask.png")
    comfy = FakeComfy()
    r = inpaint_panel(project, 1, mask=project / "mask.png", prompt="apple", seed=1,
                      grow_mask_by=8, client=comfy)

    w, h = r.work_size
    assert w % 64 == 0 and h % 64 == 0 and 0.85e6 < w * h < 1.2e6  # SDXL scale
    cw, ch = r.crop[2] - r.crop[0], r.crop[3] - r.crop[1]
    assert w > cw  # a small selection is upscaled for the model
    scale = ((w * h) / (cw * ch)) ** 0.5
    assert comfy.graphs[0]["10"]["inputs"]["expand"] == max(1, round(8 * scale))

    out = Image.open(r.path).convert("RGB")
    assert out.size == (1400, 1600)  # full resolution kept
    touched = blend_mask(_mask((1400, 1600), (700, 800, 780, 860)), 8)
    outside = touched.point(lambda v: 255 if v == 0 else 0)
    diff = ImageChops.difference(out, src).convert("L")
    assert ImageChops.multiply(diff, outside).getbbox() is None  # outside: exact
    assert out.getpixel((740, 830)) == (255, 0, 0)  # inside: the repaint


def test_inpaint_rejects_empty_mask(tmp_path):
    from PIL import Image

    project = _project(tmp_path)
    Image.new("RGBA", (128, 128), (0, 0, 0, 0)).save(project / "empty.png")
    with pytest.raises(InpaintError, match="empty"):
        inpaint_panel(project, 1, mask=project / "empty.png", prompt="x", client=FakeComfy())
