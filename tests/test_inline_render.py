"""Inline (project-container) renders: identity index, panel_to_spec, render_inline, API."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from manganation.characters.registry import CharacterRegistry
from manganation.identity import IdentityError, identity_root, register
from manganation.project_container import panel_to_spec
from manganation.render.panel import RenderError, RenderResult, render_inline
from manganation.web.api import create_app

PANEL = {
    "id": "pnl_abc123",
    "label": {"page": 2, "panel": 1},
    "characters": [{"name": "Yuki", "version": "summer"}, {"name": "Akira", "version": None}],
    "action": "Yuki drags Akira down the stairs", "camera": "medium shot",
    "expressions": {}, "dialogue": [], "sfx": [], "notes": "", "flashback": False,
    "aspect_ratio": "1:1", "seed": None, "status": "unplaced", "placement": None,
    "takes": [], "active_take": None,
}


class FakeComfy:
    def __init__(self):
        self.graphs, self.uploads = [], []

    def is_up(self):
        return True

    def upload_image(self, path):
        self.uploads.append(Path(path).name)
        return {"name": Path(path).name}

    def run(self, graph):
        self.graphs.append(graph)
        return [b"\x89PNG fake"]


# --- identity index -------------------------------------------------------------


def test_identity_index_maps_ids_to_registry_folders(tmp_path):
    register("prj_aaaaaa", "rooftop", root=tmp_path)
    assert identity_root("prj_aaaaaa", root=tmp_path) == (tmp_path / "rooftop").resolve()
    assert identity_root("prj_bbbbbb", root=tmp_path) == tmp_path / "_identity/prj_bbbbbb"
    register("prj_aaaaaa", "rooftop", root=tmp_path)  # idempotent
    with pytest.raises(IdentityError, match="already mapped"):
        register("prj_aaaaaa", "other", root=tmp_path)
    with pytest.raises(IdentityError, match="inside"):
        register("prj_cccccc", "../escape", root=tmp_path)
    with pytest.raises(IdentityError, match="not a project id"):
        identity_root("rooftop", root=tmp_path)


# --- panel_to_spec ----------------------------------------------------------------


def test_panel_to_spec_maps_container_panels():
    spec, versions = panel_to_spec(PANEL)
    assert (spec.page, spec.panel) == (2, 1)
    assert spec.characters == ["Yuki", "Akira"] and versions == {"Yuki": "summer"}
    assert spec.action.startswith("Yuki drags")


# --- render_inline ----------------------------------------------------------------


def _identity(tmp_path: Path) -> Path:
    root = tmp_path / "rooftop"
    root.mkdir()
    reg = CharacterRegistry.from_path(root)
    for name, versions in (("Yuki", ("base", "summer")), ("Akira", ("base",))):
        for v in versions:
            src = tmp_path / f"{name}-{v}.png"
            Image.new("RGB", (32, 32), "white").save(src)
            reg.add_user_reference(name, str(src), v)
    reg.set_default("Yuki", "base")
    return root


def test_render_inline_uses_versions_and_writes_to_engine_outputs(tmp_path):
    identity = _identity(tmp_path)
    comfy = FakeComfy()
    r = render_inline(PANEL, "prj_abc123", 800, 600, seed=4, client=comfy,
                      identity=identity, outputs=tmp_path / "outputs")
    assert Path(r.path).parent == tmp_path / "outputs/prj_abc123"  # never the container
    assert Path(r.path).name.startswith("pnl_abc123-")
    assert r.panel_id == "pnl_abc123" and r.seq is None
    assert r.references == {"Yuki": "summer", "Akira": "active"}
    assert "summer.png" in comfy.uploads  # Yuki's summer version, not her default base
    assert json.loads(Path(r.path).with_suffix(".json").read_text())["panel_id"] == "pnl_abc123"


def test_render_inline_refuses_unknown_versions(tmp_path):
    identity = _identity(tmp_path)
    bad = {**PANEL, "characters": [{"name": "Yuki", "version": "winter"}]}
    with pytest.raises(RenderError, match="no reference version 'winter'.*base, summer"):
        render_inline(bad, "prj_abc123", 100, 100, client=FakeComfy(), identity=identity,
                      outputs=tmp_path / "out")


# --- API --------------------------------------------------------------------------


def test_api_inline_render_job(tmp_path):
    calls = []

    def fake_inline(panel, project, fw, fh, reading_order="rtl", seed=None):
        calls.append((panel["id"], project, fw, fh, reading_order, seed))
        return RenderResult(path="x.png", seq=None, seed=1, width=8, height=8, prompt="p",
                            panel_id=panel["id"])

    client = TestClient(create_app(root=tmp_path, render_inline=fake_inline))
    resp = client.post("/jobs", json={"project": "prj_abc123", "panel": PANEL,
                                      "frame_width": 500, "frame_height": 560,
                                      "reading_order": "ltr", "seed": 3})
    assert resp.status_code == 202
    job_id = resp.json()["id"]
    for _ in range(100):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert job["status"] == "done" and job["result"]["panel_id"] == "pnl_abc123"
    assert calls == [("pnl_abc123", "prj_abc123", 500, 560, "ltr", 3)]


@pytest.mark.parametrize("body", [
    {"frame_width": 1, "frame_height": 1},                                   # neither form
    {"project": "prj_abc123", "panel": PANEL, "project_dir": "/x", "seq": 1,
     "frame_width": 1, "frame_height": 1},                                   # both forms
    {"project": "prj_abc123", "frame_width": 1, "frame_height": 1},          # half inline
    {"project": "rooftop", "panel": PANEL, "frame_width": 1, "frame_height": 1},  # bad id
    {"project": "prj_abc123", "panel": {"label": {"page": 0}},               # invalid spec
     "frame_width": 1, "frame_height": 1},
])
def test_api_rejects_malformed_render_requests(tmp_path, body):
    client = TestClient(create_app(root=tmp_path, render_inline=lambda *a, **k: None))
    assert client.post("/jobs", json=body).status_code == 422
