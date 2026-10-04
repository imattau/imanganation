"""Engine API: GET /status (no GPU, no real ComfyUI)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from manganation.render.panel import RenderResult
from manganation.script.schema import PanelSpec, Script
from manganation.web.api import create_app, required_models


def _project(root: Path) -> Path:
    project = root / "proj"
    project.mkdir(parents=True)
    (project / "panels.json").write_text(Script(panels=[PanelSpec(page=1, panel=1)]).to_json())
    return project


def _wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met")


def test_status_reports_running_queued_recent_and_errors(tmp_path):
    project = _project(tmp_path)
    gate = threading.Event()

    def render(path, seq, fw, fh, seed=None):
        if seq == 1:
            gate.wait(5)  # hold the single worker so job 2 stays queued
        if seq == 3:
            raise RuntimeError("ComfyUI exploded")
        return RenderResult(path="x.png", seq=seq, seed=0, width=8, height=8, prompt="p")

    app = create_app(render=render, root=tmp_path,
                     comfy_stats=lambda url: {"up": True, "version": "t", "gpus": [
                         {"name": "GPU", "vram_total_gb": 16.0, "vram_free_gb": 9.0}]},
                     models_check=lambda: [{"role": "checkpoint", "file": "c", "present": True,
                                            "note": ""}])
    client = TestClient(app)
    for seq in (1, 2, 3):
        client.post("/jobs", json={"project_dir": str(project), "seq": seq,
                                   "frame_width": 1, "frame_height": 1})
    _wait_for(lambda: client.get("/status").json()["running"])
    st = client.get("/status").json()
    assert [j["seq"] for j in st["running"]] == [1] and "elapsed_s" in st["running"][0]
    assert [j["seq"] for j in st["queued"]] == [2, 3]
    assert st["running"][0]["project"] == "proj"
    assert st["comfyui"]["gpus"][0]["vram_free_gb"] == 9.0

    gate.set()
    _wait_for(lambda: client.get("/status").json()["counts"]["queued"] == 0
              and not client.get("/status").json()["running"])
    st = client.get("/status").json()
    assert st["counts"] == {"queued": 0, "running": 0, "done": 2, "error": 1}
    assert st["recent"][0]["seq"] == 3 and st["recent"][0]["error"] == "ComfyUI exploded"
    assert all("took_s" in j for j in st["recent"])


def test_status_when_comfyui_is_down(tmp_path):
    def down(url):
        raise ConnectionError("refused")

    st = TestClient(create_app(root=tmp_path, comfy_stats=down,
                               models_check=lambda: [])).get("/status").json()
    assert st["comfyui"]["up"] is False and "refused" in st["comfyui"]["error"]
    assert st["comfyui"]["url"].startswith("http")


def test_required_models_flags_missing_files(tmp_path):
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "checkpoints/ck.safetensors").touch()
    (tmp_path / "ipadapter").mkdir()
    (tmp_path / "ipadapter/noob.safetensors").touch()
    settings = SimpleNamespace(defaults=SimpleNamespace(
        ipadapter=SimpleNamespace(adapter="noob_mark1"),
        refiner=SimpleNamespace(upscaler="realesrgan")))
    models = {
        "checkpoints": {"primary": {"id": "ck.safetensors", "subdir": "checkpoints"}},
        "ipadapter": {"noob_mark1": {"id": "noob.safetensors", "encoder": "clip_vision_g",
                                     "subdir": "ipadapter"},
                      "clip_vision_g": {"id": "bigG.safetensors"}},
        "upscalers": {"realesrgan": {"id": "esrgan.pth", "subdir": "upscale_models"}},
    }
    got = {m["role"]: m["present"] for m in required_models(settings, models, tmp_path)}
    assert got == {"checkpoint": True, "ip-adapter (noob_mark1)": True,
                   "clip vision": False, "upscaler (realesrgan)": False}
