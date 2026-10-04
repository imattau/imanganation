"""Container (project id) forms of /refine, /inpaint and the character endpoints."""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from manganation.characters.registry import CharacterRegistry
from manganation.identity import register
from manganation.render.inpaint import InpaintResult, inpaint_inline
from manganation.render.refiner import RefineError, RefineResult, refine_inline
from manganation.web.api import create_app

PRJ = "prj_test01"


def _png(path: Path, size=(64, 48), color="white") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def _wait(client, job_id):
    for _ in range(200):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


class FakeComfy:
    def __init__(self):
        self.graphs, self.sizes = [], []

    def is_up(self):
        return True

    def upload_image(self, path):
        with Image.open(path) as im:
            self.sizes.append(im.size)
        return {"name": Path(path).name}

    def run(self, graph):
        self.graphs.append(graph)
        buf = io.BytesIO()
        Image.new("RGB", self.sizes[0], (255, 0, 0)).save(buf, "PNG")
        return [buf.getvalue()]


# --- engine functions ---------------------------------------------------------------


def test_refine_inline_scales_against_the_given_origin(tmp_path):
    take = _png(tmp_path / "rooftop.imanga/takes/t.png", (192, 192))
    with pytest.raises(RefineError, match="already 192x192, at or beyond 2x its render"):
        refine_inline(PRJ, take, origin_width=100, origin_height=80, scale=2.0,
                      client=FakeComfy(), outputs=tmp_path / "outputs")
    r = refine_inline(PRJ, take, origin_width=100, origin_height=80, scale=4.0,
                      prompt="manga panel, 1boy", client=FakeComfy(),
                      outputs=tmp_path / "outputs")
    assert (r.width, r.height) == (384, 320) and r.origin == "100x80"
    assert Path(r.path).parent == tmp_path / "outputs" / PRJ  # never the container


def test_inpaint_inline_writes_to_outputs_and_keeps_outside_pixels(tmp_path):
    src = _png(tmp_path / "rooftop.imanga/takes/t.png", (400, 300), "blue")
    mask = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    mask.paste((255, 255, 255, 255), (180, 130, 220, 170))
    mask_path = tmp_path / "rooftop.imanga/masks/m.png"
    mask_path.parent.mkdir(parents=True)
    mask.save(mask_path)
    r = inpaint_inline(PRJ, src, mask_path, prompt="apple", client=FakeComfy(),
                       outputs=tmp_path / "outputs")
    out = Image.open(r.path).convert("RGB")
    assert Path(r.path).parent == tmp_path / "outputs" / PRJ and r.seq is None
    assert out.getpixel((200, 150)) == (255, 0, 0) and out.getpixel((5, 5)) == (0, 0, 255)


# --- API ----------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path):
    projects, outputs = tmp_path / "projects", tmp_path / "outputs"
    calls = {}

    def fake_refine(project, source, **kw):
        calls["refine"] = (project, source, kw)
        return RefineResult(path=str(outputs / project / "r.png"), seq=None,
                            source=str(source), width=2, height=2, upscaler="u",
                            denoise=0.2, seed=0, origin="1x1")

    def fake_inpaint(project, source, mask, **kw):
        calls["inpaint"] = (project, source, mask, kw)
        return InpaintResult(path="i.png", seq=None, source=str(source), mask=str(mask),
                             prompt=kw["prompt"], width=2, height=2, denoise=0.85,
                             grow_mask_by=8, seed=0)

    client = TestClient(create_app(root=projects, outputs=outputs,
                                   refine_inline=fake_refine, inpaint_inline=fake_inpaint))
    take = _png(projects / "rooftop.imanga/takes/t.png")
    fresh = _png(outputs / PRJ / "fresh.png")  # rendered, not yet copied into takes/
    return client, calls, projects, outputs, take, fresh


def test_api_refine_container_form(env):
    client, calls, _, _, take, fresh = env
    for src in (take, fresh):
        job = _wait(client, client.post("/refine", json={
            "project": PRJ, "source": str(src), "origin_width": 64, "origin_height": 48,
            "prompt": "manga panel"}).json()["id"])
        assert job["status"] == "done" and job["kind"] == "refine"
        project, source, kw = calls["refine"]
        assert project == PRJ and source == src.resolve()
        assert kw["origin_width"] == 64 and kw["prompt"] == "manga panel"


def test_api_inpaint_container_form(env):
    client, calls, projects, _, take, _ = env
    mask = _png(projects / "rooftop.imanga/masks/m.png")
    job = _wait(client, client.post("/inpaint", json={
        "project": PRJ, "source": str(take), "mask": str(mask), "prompt": "apple"}).json()["id"])
    assert job["status"] == "done" and job["kind"] == "inpaint"
    assert calls["inpaint"][:3] == (PRJ, take.resolve(), mask.resolve())


@pytest.mark.parametrize("path,body", [
    ("/refine", {"project": PRJ, "source": "SRC"}),                       # no origin size
    ("/refine", {"project": PRJ, "origin_width": 1, "origin_height": 1}),  # no source
    ("/refine", {"project": PRJ, "project_dir": "/x", "seq": 1, "source": "SRC",
                 "origin_width": 1, "origin_height": 1}),                  # both forms
    ("/inpaint", {"project": PRJ, "mask": "SRC", "prompt": "x"}),          # no source
])
def test_api_container_forms_reject_incomplete_requests(env, path, body):
    client, _, _, _, take, _ = env
    body = {k: (str(take) if v == "SRC" else v) for k, v in body.items()}
    assert client.post(path, json=body).status_code == 422


def test_api_container_forms_confine_paths(env, tmp_path):
    client, _, projects, _, take, _ = env
    outside = _png(tmp_path / "elsewhere.png")
    body = {"project": PRJ, "source": str(outside), "origin_width": 1, "origin_height": 1}
    assert client.post("/refine", json=body).status_code == 400
    body["source"] = str(projects / "rooftop.imanga/takes/missing.png")
    assert client.post("/refine", json=body).status_code == 404
    assert client.post("/inpaint", json={"project": PRJ, "source": str(take),
                                         "mask": str(outside), "prompt": "x"}).status_code == 400


def test_api_characters_by_project_id(env):
    client, _, projects, outputs, _, _ = env
    cast = projects / "rooftop"
    reg = CharacterRegistry.from_path(cast)
    reg.add_user_reference("Yuki", str(_png(projects / "src.png")), "base")
    register(PRJ, "rooftop", root=projects)

    chars = client.get("/characters", params={"project": PRJ}).json()
    assert [c["name"] for c in chars] == ["Yuki"]
    assert client.get("/characters", params={"project": "prj_unmapped"}).json() == []
    assert client.get("/characters", params={"project": "rooftop"}).status_code == 400
    assert client.get("/characters").status_code == 422

    crop = _png(outputs / PRJ / "crop.png")
    resp = client.post("/characters/reference", json={"project": PRJ, "name": "yuki",
                                                      "image_path": str(crop)}).json()
    assert resp["version"] == "gimp-01" and resp["previous"] == "base"
    assert CharacterRegistry.from_path(cast).reference_path("Yuki").name == "gimp-01.png"
