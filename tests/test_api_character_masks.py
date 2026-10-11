"""Engine API: POST /character-masks (stub detector and segmenter, no ONNX models)."""

from __future__ import annotations

import io

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from manganation import models_setup
from manganation.evaluate.detector import Box
from manganation.web.api import create_app


class StubDetector:
    loads = 0

    def __init__(self, boxes):
        self.boxes = boxes

    def figures(self, image):
        return self.boxes


class StubSegmenter:
    loads = 0

    def __init__(self, model):
        StubSegmenter.loads += 1

    def mask(self, image):
        return Image.new("L", image.size, 255)


def _png(size=(100, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    return buf.getvalue()


def _client(tmp_path, monkeypatch, boxes, present=True):
    StubSegmenter.loads = 0
    StubDetector.loads = 0
    monkeypatch.setattr(models_setup, "state",
                        lambda model, root: "present" if present else "missing")
    def load_detector(name=None):
        StubDetector.loads += 1
        return StubDetector(boxes)

    monkeypatch.setattr("manganation.evaluate.detector.default_detector", load_detector)
    monkeypatch.setattr("manganation.evaluate.masks.AnimeSegmenter", StubSegmenter)
    return TestClient(create_app(outputs=tmp_path, root=tmp_path))


def _post(client, data):
    return client.post("/character-masks", files={"file": ("layer.png", data, "image/png")})


def test_masks_are_full_canvas_alpha_pngs_and_json_serialisable(tmp_path, monkeypatch):
    # detectors hand back numpy scalars; the response must still serialise
    box = Box(np.float32(10), np.float32(5), np.float32(60), np.float32(70), np.float32(0.9))
    client = _client(tmp_path, monkeypatch, [box])
    response = _post(client, _png())
    assert response.status_code == 200
    body = response.json()
    assert (body["width"], body["height"]) == (100, 80)
    (figure,) = body["figures"]
    assert figure["score"] == np.float32(0.9)
    assert figure["box"] == [10.0, 5.0, 60.0, 70.0]
    mask = Image.open(figure["path"])
    assert mask.mode == "RGBA" and mask.size == (100, 80)
    alpha = mask.getchannel("A")
    assert alpha.getpixel((30, 30)) == 255  # inside the padded box
    assert alpha.getpixel((95, 75)) == 0  # outside it


def test_no_figures_returns_empty_list(tmp_path, monkeypatch):
    response = _post(_client(tmp_path, monkeypatch, []), _png())
    assert response.status_code == 200
    assert response.json()["figures"] == []


def test_missing_models_is_409(tmp_path, monkeypatch):
    response = _post(_client(tmp_path, monkeypatch, [], present=False), _png())
    assert response.status_code == 409


def test_unreadable_upload_is_400(tmp_path, monkeypatch):
    assert _post(_client(tmp_path, monkeypatch, []), b"not an image").status_code == 400


def test_oversized_image_is_413(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [])
    assert _post(client, _png((7000, 6000))).status_code == 413


def test_models_load_once_across_requests(tmp_path, monkeypatch):
    box = Box(10, 5, 60, 70, 0.9)
    client = _client(tmp_path, monkeypatch, [box])
    for _ in range(3):
        assert _post(client, _png()).status_code == 200
    assert (StubDetector.loads, StubSegmenter.loads) == (1, 1)


class CentreSegmenter(StubSegmenter):
    """Foreground is the middle of the crop; the rest is background."""

    def mask(self, image):
        matte = Image.new("L", image.size, 0)
        w, h = image.size
        matte.paste(255, (w // 4, h // 4, 3 * w // 4, 3 * h // 4))
        return matte


def _reference_project(root):
    from manganation.characters.registry import CharacterRegistry
    from manganation.script.schema import PanelSpec, Script

    project = root / "proj"
    project.mkdir(parents=True)
    (project / "panels.json").write_text(
        Script(panels=[PanelSpec(page=1, panel=1, characters=["Yuki"])]).to_json())
    Image.new("RGB", (64, 64), "white").save(project / "base_src.png")
    CharacterRegistry.from_path(project).add_user_reference(
        "Yuki", str(project / "base_src.png"), "base")
    (project / "tmp").mkdir()
    Image.new("RGB", (64, 64), (200, 40, 40)).save(project / "tmp" / "ref.png")
    return project


def _set_reference(client, project, **extra):
    return client.post("/characters/reference", json={
        "project_dir": str(project), "name": "Yuki",
        "image_path": str(project / "tmp" / "ref.png"), **extra})


def test_clean_background_flattens_and_keeps_the_original(tmp_path, monkeypatch):
    project = _reference_project(tmp_path)
    client = _client(tmp_path, monkeypatch, [Box(0, 0, 64, 64, 0.9)])
    monkeypatch.setattr("manganation.evaluate.masks.AnimeSegmenter", CentreSegmenter)
    response = _set_reference(client, project, clean_background=True, version_id="clean")
    assert response.status_code == 200
    assert response.json()["cleaned"] is True
    from manganation.characters.registry import CharacterRegistry
    reg = CharacterRegistry.from_path(project)
    character = reg.get("Yuki")
    assert character.default_version == "clean"
    assert character.version("clean-original") is not None
    cleaned = Image.open(reg.reference_path("Yuki")).convert("RGB")
    assert cleaned.getpixel((32, 32)) == (200, 40, 40)  # figure kept
    assert cleaned.getpixel((1, 1)) == (255, 255, 255)  # background flattened
    original = Image.open(reg.reference_path("Yuki", "clean-original")).convert("RGB")
    assert original.getpixel((1, 1)) == (200, 40, 40)
    assert not list((project / "tmp").glob("*-clean.png"))


def test_clean_background_without_a_figure_registers_the_image_as_is(tmp_path, monkeypatch):
    project = _reference_project(tmp_path)
    client = _client(tmp_path, monkeypatch, [])
    response = _set_reference(client, project, clean_background=True, version_id="plain")
    assert response.status_code == 200 and response.json()["cleaned"] is False
    from manganation.characters.registry import CharacterRegistry
    character = CharacterRegistry.from_path(project).get("Yuki")
    assert character.version("plain") is not None
    assert character.version("plain-original") is None


def test_clean_background_needs_the_models(tmp_path, monkeypatch):
    project = _reference_project(tmp_path)
    client = _client(tmp_path, monkeypatch, [], present=False)
    assert _set_reference(client, project, clean_background=True).status_code == 409
