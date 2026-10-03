"""Engine API: listing characters and setting a reference from GIMP (no GPU)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from manganation.characters.registry import CharacterRegistry
from manganation.script.schema import PanelSpec, Script
from manganation.web.api import create_app


def _project(root: Path) -> Path:
    project = root / "proj"
    project.mkdir(parents=True)
    (project / "panels.json").write_text(
        Script(panels=[PanelSpec(page=1, panel=1, characters=["Yuki"])]).to_json())
    Image.new("RGB", (64, 64), "white").save(project / "base_src.png")
    reg = CharacterRegistry.from_path(project)
    reg.add_user_reference("Yuki", str(project / "base_src.png"), "base")
    return project


def _client(root: Path) -> TestClient:
    return TestClient(create_app(render=lambda *a, **k: None, root=root))


def _export(project: Path, name: str = "gimp_ref.png") -> Path:
    (project / "tmp").mkdir(exist_ok=True)
    path = project / "tmp" / name
    Image.new("RGB", (32, 32), "red").save(path)
    return path


def test_list_characters(tmp_path):
    project = _project(tmp_path)
    chars = _client(tmp_path).get("/characters", params={"project_dir": str(project)}).json()
    assert [c["name"] for c in chars] == ["Yuki"]
    assert chars[0]["default_version"] == "base"
    assert chars[0]["reference"].endswith("characters/yuki/base.png")


def test_set_reference_adds_a_new_active_version_and_keeps_the_old(tmp_path):
    project = _project(tmp_path)
    client = _client(tmp_path)
    image = _export(project)
    body = {"project_dir": str(project), "name": "yuki", "image_path": str(image)}

    first = client.post("/characters/reference", json=body).json()
    assert first == {"name": "Yuki", "version": "gimp-01", "previous": "base",
                     "reference": str(project / "characters/yuki/gimp-01.png")}
    second = client.post("/characters/reference", json=body).json()
    assert second["version"] == "gimp-02" and second["previous"] == "gimp-01"

    reg = CharacterRegistry.from_path(project)
    assert [v.id for v in reg.get("Yuki").versions] == ["base", "gimp-01", "gimp-02"]
    assert reg.reference_path("Yuki") == project / "characters/yuki/gimp-02.png"
    assert (project / "characters/yuki/base.png").exists()  # never overwritten


def test_set_reference_rejects_bad_requests(tmp_path):
    project = _project(tmp_path)
    client = _client(tmp_path)
    image = _export(project)
    outside = tmp_path / "outside.png"
    Image.new("RGB", (8, 8)).save(outside)
    not_png = project / "tmp" / "notes.png"
    not_png.write_text("not an image")

    def post(**over):
        body = {"project_dir": str(project), "name": "Yuki", "image_path": str(image)}
        return client.post("/characters/reference", json={**body, **over}).status_code

    assert post(name="Akira") == 404  # unknown character: never auto-created
    assert post(image_path=str(outside)) == 400
    assert post(image_path=str(project / "tmp/missing.png")) == 404
    assert post(image_path=str(not_png)) == 400
    assert post(version_id="base") == 409  # would overwrite the original
    assert post(version_id="../escape") == 422
    assert CharacterRegistry.from_path(project).get("Akira") is None
