"""Tests for Phase 6a: two-pass hi-res panel refinement (no GPU needed)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manganation.render import graphs
from manganation.render.refiner import (
    RefineError,
    fit_target_width,
    refine_panel,
    take_path,
    upscaler_files,
)
from manganation.script.schema import PanelSpec, Script
from manganation.web.api import create_app

# --- target sizing ------------------------------------------------------------


def test_fit_target_width_scales_to_multiple_of_64():
    tw, th = fit_target_width(1024, 768, 2.0, max_pixels=10**9)
    assert (tw, th) == (2048, 1536)
    assert tw % 64 == 0 and th % 64 == 0


def test_fit_target_width_caps_at_pixel_budget():
    tw, th = fit_target_width(1024, 1024, 4.0, max_pixels=2048 * 2048)
    assert tw * th <= 2048 * 2048
    assert tw % 64 == 0 and th % 64 == 0
    # aspect ratio is preserved
    assert abs(tw / th - 1.0) < 1e-6


def test_fit_target_width_rejects_bad_input():
    with pytest.raises(RefineError):
        fit_target_width(0, 100, 2.0, max_pixels=10**9)
    with pytest.raises(RefineError):
        fit_target_width(100, 100, 0, max_pixels=10**9)


def test_upscaler_files_resolves_role():
    models = {"upscalers": {"realesrgan": {"id": "RealESRGAN_x4plus_anime_6B.pth"}}}
    assert upscaler_files(models, "realesrgan") == "RealESRGAN_x4plus_anime_6B.pth"
    with pytest.raises(RefineError, match="unknown upscaler"):
        upscaler_files(models, "nope")


def test_shipped_config_declares_realesrgan():
    from manganation.config import load_models, load_settings

    assert load_settings().defaults.refiner.upscaler == "realesrgan"
    assert upscaler_files(load_models(), "realesrgan").endswith(".pth")


# --- graph shape --------------------------------------------------------------


def test_upscale_refine_graph_polishes_then_upscales():
    g = graphs.upscale_refine(
        ckpt="noobaiXL.safetensors", image="a.png", prompt="p", negative="n",
        width=2048, height=1536, seed=1, prefix="t",
        upscale_model="RealESRGAN_x4plus_anime_6B.pth", denoise=0.2,
        polish_width=1024, polish_height=768,
    )
    kinds = {n["class_type"] for n in g.values()}
    assert {"UpscaleModelLoader", "ImageUpscaleWithModel", "ImageScale"} <= kinds
    assert {"VAEEncode", "KSampler", "VAEDecode"} <= kinds
    assert g["5"]["inputs"]["denoise"] == 0.2
    assert g["5"]["inputs"]["latent_image"] == ["4", 0]
    # polish runs at the polish size, not the final size
    assert g["10"]["inputs"]["width"] == 1024
    # the upscaler consumes the polished decode, and the final scale is the target
    assert g["21"]["inputs"]["image"] == ["6", 0]
    assert (g["22"]["inputs"]["width"], g["22"]["inputs"]["height"]) == (2048, 1536)
    # single image out, from the final scale
    assert g["7"]["inputs"]["images"] == ["22", 0]


def test_upscale_refine_graph_skips_polish_at_zero_denoise():
    g = graphs.upscale_refine(
        ckpt="c", image="a.png", prompt="p", negative="n",
        width=1024, height=1024, seed=1, prefix="t",
        upscale_model="u.pth", denoise=0.0,
    )
    kinds = {n["class_type"] for n in g.values()}
    assert "KSampler" not in kinds
    assert "VAEEncode" not in kinds
    # the upscaler takes the raw source; still exactly one image out
    assert g["21"]["inputs"]["image"] == ["8", 0]
    assert g["7"]["inputs"]["images"] == ["22", 0]


# --- refine_panel with a fake ComfyUI -----------------------------------------


class FakeComfy:
    def __init__(self):
        self.graphs = []

    def is_up(self):
        return True

    def upload_image(self, path):
        return {"name": Path(path).name}

    def run(self, graph):
        self.graphs.append(graph)
        return [b"\x89PNG fake"]


def _project(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "panels").mkdir()
    spec = PanelSpec(page=1, panel=1, characters=["Akira"], action="eats lunch")
    (tmp_path / "panels.json").write_text(Script(panels=[spec]).to_json())
    # a real (tiny) PNG so PIL can read its size, plus the render sidecar
    from PIL import Image

    Image.new("RGB", (100, 80), "white").save(tmp_path / "panels/001.png")
    (tmp_path / "panels/001.json").write_text(json.dumps({"prompt": "manga panel, 1boy"}))
    return tmp_path


def test_refine_panel_upscales_and_writes_hires_take(tmp_path):
    project = _project(tmp_path)
    comfy = FakeComfy()
    r = refine_panel(project, 1, scale=2.0, denoise=0.25, seed=3, client=comfy)

    assert Path(r.path).name == "001_hires.png"
    assert take_path(project, 1).name == "001_hires_take02.png"  # next one: new name
    assert (r.width, r.height) != (100, 80)
    g = comfy.graphs[0]
    assert g["22"]["inputs"]["width"] == r.width  # final scale to the target size
    assert g["5"]["inputs"]["denoise"] == 0.25
    assert g["2"]["inputs"]["text"] == "manga panel, 1boy"  # reuses the panel prompt
    assert json.loads(Path(r.path).with_suffix(".json").read_text())["upscaler"] == "realesrgan"


def test_refine_panel_source_must_exist(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(RefineError, match="no rendered image"):
        refine_panel(project, 5, client=FakeComfy())


def test_refine_panel_starts_from_render_not_previous_hires(tmp_path):
    """Re-refining must use the render, not a prior enlargement (no scale compounding)."""
    from manganation.render.refiner import _newest_panel

    project = _project(tmp_path)
    (project / "panels/003_hires.png").write_bytes(b"prior enlargement")
    (project / "panels/003.png").write_bytes(b"render")
    assert _newest_panel(project, 3) == project / "panels/003.png"
    # a newer render take still wins over _hires
    (project / "panels/003_take02.png").write_bytes(b"render take 2")
    assert _newest_panel(project, 3) == project / "panels/003_take02.png"


def _derived(project, name, size, **sidecar):
    from PIL import Image

    Image.new("RGB", size, "white").save(project / "panels" / name)
    (project / "panels" / name).with_suffix(".json").write_text(json.dumps(sidecar))
    return project / "panels" / name


def test_refine_does_not_compound_on_an_enlarged_take(tmp_path):
    """2x of a 2x used to give 4x the render; scale is now relative to the render."""
    project = _project(tmp_path)  # 001.png is 100x80, the render
    render = project / "panels/001.png"
    hires = _derived(project, "001_hires.png", (192, 192), upscaler="realesrgan",
                     source=str(render))
    inpaint = _derived(project, "001_inpaint.png", (192, 192), mask="m.png",
                       source=str(hires), prompt="red apple")
    for take in (hires, inpaint):
        with pytest.raises(RefineError, match="already 192x192, at or beyond 2x its render"):
            refine_panel(project, 1, source=take, scale=2.0, client=FakeComfy())


def test_refine_of_an_inpainted_hires_enlarges_relative_to_the_render(tmp_path):
    """A larger scale still works on a derived take (keeping its edits), and the polish
    uses the panel prompt, not the inpaint's short patch prompt."""
    project = _project(tmp_path)
    render = project / "panels/001.png"
    hires = _derived(project, "001_hires.png", (192, 192), upscaler="realesrgan",
                     source=str(render))
    inpaint = _derived(project, "001_inpaint.png", (192, 192), mask="m.png",
                       source=str(hires), prompt="red apple")
    comfy = FakeComfy()
    r = refine_panel(project, 1, source=inpaint, scale=4.0, seed=1, client=comfy)
    assert (r.width, r.height) == (384, 320)  # 4x the 100x80 render, on the 64 grid
    assert r.origin == str(render) and r.source == str(inpaint)
    assert comfy.graphs[0]["2"]["inputs"]["text"] == "manga panel, 1boy"  # render's prompt


def test_refine_never_overwrites_an_existing_hires(tmp_path):
    """Placed layers and derived takes reference files: a re-refine gets a new name."""
    project = _project(tmp_path)
    first = refine_panel(project, 1, scale=2.0, client=FakeComfy())
    before = Path(first.path).read_bytes()
    second = refine_panel(project, 1, scale=3.0, client=FakeComfy())
    assert Path(second.path).name == "001_hires_take02.png"
    assert Path(first.path).read_bytes() == before


def test_refine_reports_a_looping_take_history(tmp_path):
    project = _project(tmp_path)
    a = project / "panels/001_hires.png"
    b = project / "panels/001_inpaint.png"
    _derived(project, a.name, (192, 192), upscaler="realesrgan", source=str(b))
    _derived(project, b.name, (192, 192), mask="m.png", source=str(a))
    with pytest.raises(RefineError, match="history loops"):
        refine_panel(project, 1, source=b, client=FakeComfy())


def test_refine_refuses_when_a_derived_takes_render_is_gone(tmp_path):
    project = _project(tmp_path)
    orphan = _derived(project, "001_hires.png", (192, 192), upscaler="realesrgan",
                      source=str(project / "panels/missing.png"))
    with pytest.raises(RefineError, match="no longer exists"):
        refine_panel(project, 1, source=orphan, client=FakeComfy())


# --- job API ------------------------------------------------------------------


def test_api_refine_endpoint(tmp_path):
    project = _project(tmp_path / "proj")
    client = TestClient(create_app(render=lambda *a, **k: None, root=tmp_path,
                                   refine=_fake_refine(project)))
    resp = client.post("/refine", json={"project_dir": str(project), "seq": 1,
                                        "scale": 2.0, "denoise": 0.3})
    assert resp.status_code == 202
    job = _wait(client, resp.json()["id"])
    assert job["status"] == "done"
    assert job["kind"] == "refine"
    assert job["result"]["width"] == 2000


def test_api_refine_exact_source(tmp_path):
    """The GIMP plug-in refines the take a layer shows, not just the newest render."""
    project = _project(tmp_path / "proj")
    (project / "panels/001_take02.png").write_bytes(b"take 2")
    fake = _fake_refine(project)
    client = TestClient(create_app(render=lambda *a, **k: None, root=tmp_path, refine=fake))
    take = project / "panels/001_take02.png"
    job = _wait(client, client.post("/refine", json={
        "project_dir": str(project), "seq": 1, "source": str(take)}).json()["id"])
    assert job["status"] == "done" and fake.sources == [take.resolve()]

    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(b"x")
    for bad, code in ((outside, 400), (project / "panels/missing.png", 404)):
        resp = client.post("/refine", json={"project_dir": str(project), "seq": 1,
                                            "source": str(bad)})
        assert resp.status_code == code


def _fake_refine(project):
    from manganation.render.refiner import RefineResult

    def _f(path, seq, source=None, scale=None, denoise=None, seed=None):
        _f.sources.append(source)
        return RefineResult(path=str(project / "panels/001_hires.png"), seq=seq,
                            source=str(source or project / "panels/001.png"),
                            width=2000, height=1600,
                            upscaler="realesrgan", denoise=denoise or 0.2, seed=seed or 0)

    _f.sources = []
    return _f


def _wait(client, job_id):
    import time

    for _ in range(100):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")
