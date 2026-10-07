"""Engine choice: the catalogue, the API (/engines, installs, engine on a render job,
location design), and the per-render override."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from manganation import engines
from manganation.config import load_models, load_settings
from manganation.render.panel import RenderResult
from manganation.web.api import create_app
from tests.test_inline_render import PANEL, _identity
from tests.test_render_panel import FakeComfy


def _wait(client, job_id):
    for _ in range(200):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_catalogue_flags_the_non_commercial_engine(tmp_path):
    rows = engines.report(load_models(), load_settings(), tmp_path)  # nothing installed
    by_id = {r["id"]: r for r in rows}
    assert list(by_id) == ["sdxl", "qwen_image_21", "z_anime", "face_pass"]
    assert by_id["qwen_image_21"]["commercial"] is False
    assert all(by_id[e]["commercial"] for e in ("sdxl", "z_anime", "face_pass"))
    qwen = by_id["qwen_image_21"]
    assert not qwen["installed"] and "qwen_image_2.1_int8_convrot.safetensors" in qwen["missing"]
    assert qwen["missing_bytes"] > 15e9
    # the face pass needs its painter, both detectors and the tagger
    assert {"animagine-xl-4.0-opt.safetensors", "anime-face-detect-v1.4-s.onnx",
            "wd-swinv2-tagger-v3.onnx"} <= set(by_id["face_pass"]["missing"])


def test_render_job_carries_the_projects_engine():
    calls = []

    def fake_inline(panel, project, fw, fh, reading_order="rtl", seed=None, **extra):
        calls.append(extra)
        return RenderResult(path="x.png", seq=None, seed=1, width=8, height=8, prompt="p")

    client = TestClient(create_app(render_inline=fake_inline))
    job = client.post("/jobs", json={"project": "prj_abc123", "panel": PANEL,
                                     "frame_width": 500, "frame_height": 560,
                                     "engine": "qwen_image_21", "face_pass": True}).json()
    assert _wait(client, job["id"])["status"] == "done"
    assert calls == [{"engine": "qwen_image_21", "face_pass": True}]
    bad = client.post("/jobs", json={"project": "prj_abc123", "panel": PANEL,
                                     "frame_width": 1, "frame_height": 1, "engine": "dalle"})
    assert bad.status_code == 422


def test_engines_endpoint_and_unknown_install():
    rows = [{"id": "sdxl", "installed": True}, {"id": "qwen_image_21", "installed": False}]
    client = TestClient(create_app(engines_report=lambda: [dict(r) for r in rows]))
    got = client.get("/engines").json()["engines"]
    assert [r["id"] for r in got] == ["sdxl", "qwen_image_21"]
    assert all(r["install"] is None for r in got)  # nothing installing
    assert client.post("/engines/dalle/install").status_code == 404


def test_locations_design_job(tmp_path, monkeypatch):
    seen = {}

    def fake_design(identity, specs, force):
        seen.update(identity=identity, places=[s.location or s.scene_heading for s in specs],
                    force=force)
        from manganation.web.api import LocationsResult

        return LocationsResult(designed=[{"key": "school rooftop"}], existing=[])

    monkeypatch.setattr("manganation.identity.identity_root",
                        lambda project, root=None: tmp_path / project)
    client = TestClient(create_app(design_locations=fake_design))
    panel = {**PANEL, "scene_heading": "School rooftop — late afternoon"}
    job = client.post("/locations/design", json={"project": "prj_abc123",
                                                 "panels": [panel]}).json()
    done = _wait(client, job["id"])
    assert done["status"] == "done" and done["result"]["designed"][0]["key"] == "school rooftop"
    assert seen["identity"] == tmp_path / "prj_abc123" and seen["force"] is False
    assert client.post("/locations/design", json={"project": "prj_abc123",
                                                  "panels": []}).status_code == 422


def test_render_override_switches_engine_for_one_render(tmp_path):
    from manganation.render.panel import render_inline

    comfy, identity = FakeComfy(), _identity(tmp_path)
    render_inline(PANEL, "prj_abc123", 500, 560, client=comfy, identity=identity,
                  outputs=tmp_path / "out", engine="qwen_image_21")
    assert any(n["class_type"] == "TextEncodeQwenImage21" for n in comfy.graphs[0].values())
    render_inline(PANEL, "prj_abc123", 500, 560, client=comfy, identity=identity,
                  outputs=tmp_path / "out")  # back to the settings' engine
    assert not any(n["class_type"] == "TextEncodeQwenImage21"
                   for n in comfy.graphs[1].values())
