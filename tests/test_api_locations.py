"""Designing one location at a time from the author's description (no GPU)."""

from __future__ import annotations

import io
import time

from fastapi.testclient import TestClient
from PIL import Image

from manganation import locations as lc
from manganation.web.api import create_app


class FakeComfy:
    def __init__(self):
        self.graphs = []

    def run(self, graph):
        self.graphs.append(graph)
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8)).save(buffer, "PNG")
        return [buffer.getvalue()]


def _wait(client, job_id):
    for _ in range(200):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _fake_design(comfy):
    def design(identity, key, name, details, **kwargs):
        return lc.design(identity, key, name, details, client=comfy, **kwargs)
    return design


def _client(tmp_path, monkeypatch, comfy):
    monkeypatch.setattr("manganation.identity.identity_root",
                        lambda project, root=None: tmp_path / project)
    monkeypatch.setattr("manganation.render.panel.trial_files",
                        lambda models, group: {"model": "m", "text_encoder": "t", "vae": "v"})
    return TestClient(create_app(design_location=_fake_design(comfy)))


def test_design_a_location_from_its_description(tmp_path, monkeypatch):
    comfy = FakeComfy()
    client = _client(tmp_path, monkeypatch, comfy)
    job = client.post("/locations", json={
        "project": "prj_abc123", "name": "School rooftop",
        "description": "chain-link fence, rusty water tank, city skyline"}).json()
    assert job["kind"] == "location"
    done = _wait(client, job["id"])
    assert done["status"] == "done", done.get("error")
    assert done["result"]["key"] == "school rooftop"
    prompt = str(comfy.graphs[0])
    assert "School rooftop. chain-link fence, rusty water tank" in prompt
    listed = client.get("/locations", params={"project": "prj_abc123"}).json()
    assert [(loc["key"], loc["description"]) for loc in listed] == [
        ("school rooftop", "chain-link fence, rusty water tank, city skyline")]
    assert listed[0]["image"].endswith("school-rooftop.png")
    # A panel set there at another time finds the same image
    reg = lc.LocationRegistry.from_path(tmp_path / "prj_abc123")
    assert reg.reference_path("School rooftop — late afternoon").name == "school-rooftop.png"


def test_redesign_keeps_the_old_image_and_the_description(tmp_path, monkeypatch):
    comfy = FakeComfy()
    client = _client(tmp_path, monkeypatch, comfy)
    body = {"project": "prj_abc123", "name": "Kitchen", "description": "a cramped kitchen"}
    _wait(client, client.post("/locations", json=body).json()["id"])
    assert client.post("/locations", json=body).status_code == 409  # already designed
    again = client.post("/locations", json={**body, "description": "", "redesign": True})
    done = _wait(client, again.json()["id"])
    assert done["status"] == "done", done.get("error")
    result = done["result"]
    assert result["image"].endswith("kitchen-2.png")
    assert [p.rsplit("/", 1)[-1] for p in result["previous"]] == ["kitchen.png"]
    assert result["description"] == "a cramped kitchen"  # empty keeps the last one
    assert "a cramped kitchen" in str(comfy.graphs[1])


def test_delete_a_location_sets_its_images_aside(tmp_path, monkeypatch):
    comfy = FakeComfy()
    client = _client(tmp_path, monkeypatch, comfy)
    body = {"project": "prj_abc123", "name": "Kitchen"}
    _wait(client, client.post("/locations", json=body).json()["id"])
    _wait(client, client.post("/locations", json={**body, "redesign": True}).json()["id"])
    gone = client.delete("/locations", params={"project": "prj_abc123", "name": "kitchen"})
    assert gone.status_code == 200
    moved = sorted(p.name for p in (tmp_path / gone.json()["moved_to"]).iterdir())
    assert moved == ["kitchen-2.png", "kitchen.png"]
    assert client.get("/locations", params={"project": "prj_abc123"}).json() == []
    assert client.delete("/locations", params={"project": "prj_abc123",
                                               "name": "kitchen"}).status_code == 404


def test_a_location_needs_a_place_name(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, FakeComfy())
    assert client.post("/locations", json={"project": "prj_abc123",
                                           "name": " — "}).status_code == 422


def test_an_image_from_gimp_becomes_the_reference_and_the_design_is_kept(tmp_path,
                                                                         monkeypatch):
    comfy = FakeComfy()
    monkeypatch.setattr("manganation.web.api.projects_root", lambda: tmp_path)
    client = _client(tmp_path, monkeypatch, comfy)
    body = {"project": "prj_abc123", "name": "Kitchen", "description": "a cramped kitchen"}
    _wait(client, client.post("/locations", json=body).json()["id"])
    painted = tmp_path / "proj.imanga" / "tmp" / "gimp_location_kitchen.png"
    painted.parent.mkdir(parents=True)
    Image.new("RGB", (64, 36), "red").save(painted)

    done = client.post("/locations/reference", json={
        "project": "prj_abc123", "name": "Kitchen — night", "image_path": str(painted)})
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["image"].endswith("kitchen-2.png") and result["replaced"].endswith(
        "kitchen.png")
    assert result["description"] == "a cramped kitchen"  # the record is kept
    reg = lc.LocationRegistry.from_path(tmp_path / "prj_abc123")
    with Image.open(reg.reference_path("kitchen")) as im:
        assert im.size == (64, 36) and im.getpixel((0, 0)) == (255, 0, 0)

    # A place never designed gets a record; files outside the roots are refused
    new = client.post("/locations/reference", json={
        "project": "prj_abc123", "name": "Harbor pier", "image_path": str(painted)})
    assert new.json()["key"] == "harbor pier" and new.json()["replaced"] is None
    outside = tmp_path.parent / "elsewhere.png"
    Image.new("RGB", (8, 8)).save(outside)
    assert client.post("/locations/reference", json={
        "project": "prj_abc123", "name": "Kitchen",
        "image_path": str(outside)}).status_code == 400


def test_gather_asks_the_llm_for_a_few_panels_per_place_and_skips_noted_ones(monkeypatch):
    from manganation import locations as lc
    from manganation.render import staging
    from manganation.render.panel import PanelSpec

    calls = []

    class Staged:
        setting = ["tag"]

    monkeypatch.setattr(staging, "stage", lambda spec, place, **kw: calls.append(place) or Staged())
    panels = [PanelSpec(page=1, panel=i + 1, location="School rooftop") for i in range(8)]
    panels += [PanelSpec(page=2, panel=i + 1, location="Kitchen") for i in range(8)]
    found = lc.gather(panels, notes={"kitchen": "a small kitchen"})
    assert calls == ["School rooftop"] * lc.STAGING_PANELS
    assert set(found) == {"school rooftop", "kitchen"} and found["kitchen"]["details"] == []
