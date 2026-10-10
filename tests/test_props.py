"""Props as references: the registry, the API and how a render uses them (no GPU)."""

from __future__ import annotations

import io
import time

from fastapi.testclient import TestClient
from PIL import Image

from manganation import props as pr
from manganation.render.panel import prose_prompt
from manganation.script.schema import PanelSpec
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


def _client(tmp_path, monkeypatch, comfy):
    monkeypatch.setattr("manganation.identity.identity_root",
                        lambda project, root=None: tmp_path / project)
    monkeypatch.setattr("manganation.render.panel.trial_files",
                        lambda models, group: {"model": "m", "text_encoder": "t", "vae": "v"})
    monkeypatch.setattr("manganation.web.api.projects_root", lambda: tmp_path)

    def design(identity, name, **kwargs):
        return pr.design(identity, name, client=comfy, **kwargs)

    return TestClient(create_app(design_prop=design))


P = {"project": "prj_abc123"}


def test_a_prop_key_ignores_case_and_leading_articles():
    assert pr.prop_key("The Red  Umbrella") == pr.prop_key("red umbrella") == "red umbrella"


def test_design_a_prop_then_redesign_keeps_the_old_image(tmp_path, monkeypatch):
    comfy = FakeComfy()
    client = _client(tmp_path, monkeypatch, comfy)
    body = {**P, "name": "Red umbrella", "description": "a bright red umbrella, bamboo handle"}
    job = client.post("/props", json=body).json()
    assert job["kind"] == "prop"
    done = _wait(client, job["id"])
    assert done["status"] == "done", done.get("error")
    assert done["result"]["image"].endswith("props/red-umbrella.png")
    assert "Red umbrella. a bright red umbrella, bamboo handle" in str(comfy.graphs[0])
    assert client.post("/props", json=body).status_code == 409  # already designed
    again = _wait(client, client.post("/props", json={**body, "description": "",
                                                      "redesign": True}).json()["id"])
    result = again["result"]
    assert result["image_name"] == "red-umbrella-2.png"
    assert list(result["previous"]) == ["red-umbrella.png"]
    assert result["description"].startswith("a bright red")  # kept
    listed = client.get("/props", params=P).json()
    assert [p["key"] for p in listed] == ["red umbrella"]


def test_images_can_be_made_current_again_or_set_aside(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, FakeComfy())
    body = {**P, "name": "Katana"}
    _wait(client, client.post("/props", json=body).json()["id"])
    _wait(client, client.post("/props", json={**body, "redesign": True}).json()["id"])
    use = {**body, "image": "katana.png"}
    assert client.post("/props/images/default", json=use).json()["image_name"] == "katana.png"
    assert client.post("/props/images/default",
                       json={**body, "image": "nope.png"}).status_code == 404
    # katana.png is current, katana-2.png earlier: delete the current one -> promotes
    gone = client.post("/props/images/delete", json=use).json()
    assert gone["image_name"] == "katana-2.png" and gone["previous"] == {}
    assert ".deleted" in gone["moved_to"]
    last = client.post("/props/images/delete", json={**body, "image": "katana-2.png"})
    assert last.status_code == 409


def test_an_image_from_gimp_and_a_description_without_a_picture(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, FakeComfy())
    painted = tmp_path / "proj.imanga" / "tmp" / "gimp_prop.png"
    painted.parent.mkdir(parents=True)
    Image.new("RGB", (32, 32), "red").save(painted)
    made = client.post("/props/reference", json={**P, "name": "Radio",
                                                 "image_path": str(painted)}).json()
    assert made["image_name"] == "radio.png"
    again = client.post("/props/reference", json={**P, "name": "the radio",
                                                  "image_path": str(painted)}).json()
    assert again["image_name"] == "radio-2.png" and list(again["previous"]) == ["radio.png"]
    note = client.post("/props/description", json={**P, "name": "Satchel",
                                                   "description": "worn leather"}).json()
    assert note["image"] is None and note["description"] == "worn leather"
    assert client.post("/props/description",
                       json={**P, "name": " — ", "description": "x"}).status_code == 422


def test_delete_a_prop_sets_its_images_aside(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, FakeComfy())
    _wait(client, client.post("/props", json={**P, "name": "Lantern"}).json()["id"])
    gone = client.delete("/props", params={**P, "name": "lantern"})
    assert gone.status_code == 200
    assert [p.name for p in (tmp_path / gone.json()["moved_to"]).iterdir()] == ["lantern.png"]
    assert client.delete("/props", params={**P, "name": "lantern"}).status_code == 404


def test_the_prose_prompt_points_at_a_props_picture_or_uses_its_words():
    spec = PanelSpec(page=1, panel=1, props=["red umbrella", "satchel"], action="rain falls")
    text = prose_prompt(spec, {}, prop_refs={"red umbrella": 3},
                        prop_notes={"satchel": "worn leather."})
    assert "The red umbrella is exactly the object shown in <image3>" in text
    assert "The satchel: worn leather." in text
    assert "The satchel is in view." in prose_prompt(spec.model_copy(
        update={"props": ["satchel"]}), {})


def test_a_qwen_render_uploads_each_props_picture_after_the_other_references(tmp_path):
    from manganation.render.panel import _prose_graph, load_style

    registry = pr.PropRegistry.from_path(tmp_path)
    for name in ("Red umbrella", "Katana"):
        source = tmp_path / f"{pr.slug(pr.prop_key(name))}-src.png"
        Image.new("RGB", (8, 8)).save(source)
        registry.set_reference(name, source)
    registry.set_description("Satchel", "worn leather")  # words only: no picture

    class Client:
        def __init__(self):
            self.uploaded = []

        def upload_image(self, path):
            self.uploaded.append(path)
            return {"name": f"up{len(self.uploaded)}.png"}

    client = Client()
    spec = PanelSpec(page=1, panel=1, props=["the red umbrella", "Satchel", "katana"])
    models = {"trials": {"qwen_image_21": {"model": {"id": "m"}, "text_encoder": {"id": "t"},
                                           "vae": {"id": "v"}}}}
    _graph, prompt, _ref = _prose_graph("qwen_image_21", spec, tmp_path, client, models,
                                        1024, 1024, 1, "t", load_style(None))
    assert [p.rsplit("/", 1)[-1] for p in client.uploaded] == ["red-umbrella.png",
                                                               "katana.png"]
    assert "The red umbrella is exactly the object shown in <image1>" in prompt
    assert "katana is exactly the object shown in <image2>" in prompt
    assert "The Satchel: worn leather." in prompt
