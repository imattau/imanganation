"""Tests for frame-driven panel rendering and the engine job API (no GPU needed)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manganation.render.panel import (
    RenderError,
    RenderResult,
    build_prompt,
    fit_resolution,
    ipadapter_files,
    output_path,
    render_panel,
)
from manganation.script.schema import PanelSpec, Script
from manganation.web.api import create_app

# --- resolution ---------------------------------------------------------------


@pytest.mark.parametrize(
    "frame, expected",
    [((1000, 1000), (1024, 1024)), ((1056, 640), (1280, 768)), ((520, 630), (896, 1088))],
)
def test_fit_resolution_matches_frame_aspect(frame, expected):
    assert fit_resolution(*frame) == expected


def test_fit_resolution_is_sdxl_friendly():
    for fw, fh in [(1056, 300), (200, 1500), (777, 333), (1, 1)]:
        w, h = fit_resolution(fw, fh)
        assert w % 64 == 0 and h % 64 == 0
        assert 0.85 * 1024**2 <= w * h <= 1.15 * 1024**2
        assert abs(w / h - min(max(fw / fh, 1 / 3), 3)) / (w / h) < 0.03
        assert max(w / h, h / w) <= 3.1  # extreme strips are clamped; mask crops the rest


def test_single_colour_style():
    """Policy: one render style, no colour mode input (docs/color-policy.md)."""
    from manganation.render.panel import load_style

    style = load_style()
    assert style["color_mode"] == "color"


def test_fit_resolution_rejects_empty_frame():
    with pytest.raises(ValueError):
        fit_resolution(0, 100)


# --- prompt / output naming ---------------------------------------------------


def test_build_prompt_uses_appearance_tags_not_just_names():
    spec = PanelSpec(page=1, panel=1, characters=["Akira"], camera="close-up",
                     action="Akira looks up", flashback=True)
    prompt = build_prompt(spec, {"prompt_prefix": "manga panel, anime illustration, "},
                          {"Akira": ["1boy", "amber eyes"]})
    assert prompt.startswith("manga panel, anime illustration, solo, 1boy, amber eyes, close-up")
    assert prompt.endswith("flashback, soft focus")


def test_single_character_panels_forbid_duplicates():
    from manganation.render.panel import build_negative

    style = {"prompt_prefix": "", "negative": "text"}
    solo = PanelSpec(page=1, panel=1, characters=["Akira"], action="sighs")
    pair = PanelSpec(page=1, panel=1, characters=["Akira", "Yuki"], action="talk")
    assert "solo" in build_prompt(solo, style) and "solo" not in build_prompt(pair, style)
    assert "2boys" in build_negative(solo, style)
    assert build_negative(pair, style) == "text, solo, multiple views, clone, twins"


def test_multi_character_prompt_leads_with_a_head_count():
    style = {"prompt_prefix": "manga panel"}
    pair = PanelSpec(page=1, panel=1, characters=["Yuki", "Akira"], action="talk",
                     expressions={"Yuki": "surprised", "Akira": "sighs"})
    tags = {"Yuki": ["1girl", "silver hair"], "Akira": ["1boy", "brown hair"]}
    prompt = build_prompt(pair, style, tags)
    # one count group, each character's own count dropped, faces beside their owner
    assert prompt.startswith("manga panel, 1boy, 1girl, silver hair, surprised, "
                             "brown hair, sighs, talk")
    girls = PanelSpec(page=1, panel=1, characters=["A", "B"], action="talk")
    assert "2girls" in build_prompt(girls, style, {"A": ["1girl"], "B": ["1girl"]})
    # a character with no count tag: no partial count (it would ask for too few)
    prompt = build_prompt(pair, style, {"Yuki": ["1girl", "silver hair"]})
    assert prompt.startswith("manga panel, 1girl, silver hair, surprised, Akira sighs")


def test_panel_location_wins_over_the_scene_heading():
    from manganation.render.panel import setting

    moved = PanelSpec(page=2, panel=3, scene_heading="School rooftop — cont.",
                      location="the stairwell", action="drags him down the stairs")
    assert setting(moved) == "the stairwell"
    assert "rooftop" not in build_prompt(moved, {})


@pytest.mark.parametrize("heading, place", [
    ("School rooftop — cont.", "School rooftop"),
    ("Kitchen (CONT'D)", "Kitchen"),
    ("Harbor - continued", "Harbor"),
    ("School rooftop — late afternoon", "School rooftop — late afternoon"),
    ("Contest hall", "Contest hall"),
])
def test_continuation_markers_are_not_places(heading, place):
    from manganation.render.panel import setting

    assert setting(PanelSpec(page=1, panel=1, scene_heading=heading)) == place


def test_shot_is_not_repeated_from_the_action():
    spec = PanelSpec(page=1, panel=1, camera="medium shot",
                     action="Medium shot. Yuki drags Akira by the wrist.")
    prompt = build_prompt(spec, {})
    assert prompt == "medium shot, Yuki drags Akira by the wrist."
    # a shot word inside the sentence is left alone
    spec = PanelSpec(page=1, panel=1, camera="close-up", action="A close-up of the key.")
    assert build_prompt(spec, {}) == "close-up, A close-up of the key."


def test_output_path_adds_takes(tmp_path):
    (tmp_path / "panels").mkdir()
    assert output_path(tmp_path, 3).name == "003.png"
    (tmp_path / "panels/003.png").touch()
    assert output_path(tmp_path, 3).name == "003_take02.png"
    (tmp_path / "panels/003_take02.png").touch()
    assert output_path(tmp_path, 3).name == "003_take03.png"
    # Lexical order keeps the newest take last (what the GIMP plug-in picks).
    assert sorted(p.name for p in (tmp_path / "panels").iterdir())[-1] == "003_take02.png"


def test_ipadapter_files_pairs_adapter_with_its_encoder():
    models = {"ipadapter": {
        "noob_mark1": {"id": "noob.safetensors", "encoder": "clip_vision_g"},
        "plus": {"id": "plus.safetensors", "encoder": "clip_vision"},
        "clip_vision": {"id": "vit-h.safetensors"},
        "clip_vision_g": {"id": "vit-bigg.safetensors"},
    }}
    assert ipadapter_files(models, "noob_mark1") == ("noob.safetensors", "vit-bigg.safetensors")
    assert ipadapter_files(models, "plus") == ("plus.safetensors", "vit-h.safetensors")
    with pytest.raises(RenderError, match="unknown IP-Adapter"):
        ipadapter_files(models, "nope")


def test_shipped_config_uses_noob_adapter_with_bigg():
    from manganation.config import load_models, load_settings

    adapter = load_settings().defaults.ipadapter.adapter
    assert adapter == "noob_mark1"
    assert "bigG" in ipadapter_files(load_models(), adapter)[1]


# --- render_panel with a fake ComfyUI ------------------------------------------


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


def _project(tmp_path: Path, **panel) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    spec = {"page": 1, "panel": 1, "action": "Akira eats lunch", **panel}
    (tmp_path / "panels.json").write_text(Script(panels=[PanelSpec(**spec)]).to_json())
    return tmp_path


def test_render_panel_sizes_latent_to_frame_and_writes_sidecar(tmp_path):
    project = _project(tmp_path)
    comfy = FakeComfy()
    r = render_panel(project, 1, 1056, 640, seed=7, client=comfy)

    latent = comfy.graphs[0]["4"]["inputs"]
    assert (latent["width"], latent["height"]) == (1280, 768) == (r.width, r.height)
    assert comfy.graphs[0]["5"]["inputs"]["seed"] == 7
    assert Path(r.path) == project / "panels/001.png"
    assert json.loads((project / "panels/001.json").read_text())["seed"] == 7


def test_render_panel_always_renders_colour(tmp_path):
    # A B&W-default script still renders colour: B&W is done by the artist in GIMP.
    project = _project(tmp_path, color_mode="bw")
    comfy = FakeComfy()
    r = render_panel(project, 1, 100, 100, client=comfy)
    assert "monochrome" not in r.prompt and "greyscale" not in r.prompt
    assert "monochrome" in comfy.graphs[0]["3"]["inputs"]["text"]  # negative prompt


def test_render_panel_uses_single_character_reference(tmp_path):
    project = _project(tmp_path, characters=["Akira"])
    (project / "characters").mkdir()
    (project / "characters/akira.png").write_bytes(b"ref")
    comfy = FakeComfy()
    r = render_panel(project, 1, 100, 100, client=comfy)
    assert r.reference and r.reference.endswith("akira.png")
    assert comfy.graphs[0]["5"]["inputs"]["model"] == ["11", 0]  # routed via IP-Adapter


def test_render_warns_about_characters_without_an_appearance(tmp_path):
    # No registry: Akira would render as nobody in particular, and the artist should know.
    project = _project(tmp_path, characters=["Akira"])
    r = render_panel(project, 1, 100, 100, client=FakeComfy())
    assert len(r.warnings) == 1 and "Akira" in r.warnings[0]
    assert json.loads(Path(r.path).with_suffix(".json").read_text())["warnings"] == r.warnings


# --- job API ------------------------------------------------------------------


def _wait(client, job_id):
    for _ in range(100):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_api_job_roundtrip(tmp_path):
    project = _project(tmp_path / "proj")
    calls = []

    def fake_render(path, seq, fw, fh, seed=None):
        calls.append((path, seq, fw, fh))
        return RenderResult(path=str(path / "panels/001.png"), seq=seq, seed=1,
                            width=1344, height=832, prompt="p")

    client = TestClient(create_app(render=fake_render, root=tmp_path))
    resp = client.post("/jobs", json={"project_dir": str(project), "seq": 1,
                                      "frame_width": 1056, "frame_height": 640})
    assert resp.status_code == 202
    job = _wait(client, resp.json()["id"])
    assert job["status"] == "done" and job["result"]["width"] == 1344
    assert calls == [(project.resolve(), 1, 1056, 640)]


def test_api_reports_render_errors(tmp_path):
    project = _project(tmp_path / "proj")

    def boom(*a, **k):
        raise RuntimeError("ComfyUI is not reachable")

    client = TestClient(create_app(render=boom, root=tmp_path))
    job_id = client.post("/jobs", json={"project_dir": str(project), "seq": 1,
                                        "frame_width": 1, "frame_height": 1}).json()["id"]
    job = _wait(client, job_id)
    assert job["status"] == "error" and "not reachable" in job["error"]


def test_api_refuses_projects_outside_root(tmp_path):
    outside = _project(tmp_path / "elsewhere")
    client = TestClient(create_app(render=lambda *a, **k: None, root=tmp_path / "root"))
    resp = client.post("/jobs", json={"project_dir": str(outside), "seq": 1,
                                      "frame_width": 1, "frame_height": 1})
    assert resp.status_code == 400
