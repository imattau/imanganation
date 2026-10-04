"""Legacy projects/<name>/ -> project container importer."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from PIL import Image

from gimp.imanganation.project_store import load_project
from manganation.project_container import integrity_errors
from manganation.project_import import ImportError_, import_project
from manganation.script.schema import PanelSpec, Script

SCHEMA = json.loads((Path(__file__).resolve().parents[1]
                     / "docs/project-container.schema.json").read_text())


def _png(path: Path, size=(64, 48), mtime=None):
    Image.new("RGB", size, "white").save(path)
    if mtime:
        import os

        os.utime(path, (mtime, mtime))


def _legacy(tmp_path: Path) -> Path:
    src = tmp_path / "rooftop"
    (src / "panels").mkdir(parents=True)
    (src / "tmp").mkdir()
    panels = [PanelSpec(page=1, panel=1, characters=["Akira"], action="eats"),
              PanelSpec(page=2, panel=1, characters=["Yuki"], action="grins"),
              PanelSpec(page=2, panel=1, characters=["Yuki", "Akira"], action="drags")]
    (src / "panels.json").write_text(Script(title="Rooftop", panels=panels).to_json())
    (src / "script.md").write_text("PAGE 1\n\nPanel 1: Akira eats.\n")
    (src / "characters.json").write_text(json.dumps({"characters": [
        {"name": "Akira", "aliases": [], "appearance": {"hair_color": "brown"}},
        {"name": "Yuki", "aliases": ["Yu"]}]}))
    (src / "gimp_cursor.json").write_text(json.dumps({"next": 3}))
    p = src / "panels"
    # panel 1: render -> refine -> inpaint (with a mask)
    _png(p / "001.png", mtime=1000)
    (p / "001.json").write_text(json.dumps({"seed": 5, "prompt": "akira", "reference":
                                            str(src / "characters/akira/base.png")}))
    _png(p / "001_hires.png", (128, 96), mtime=2000)
    (p / "001_hires.json").write_text(json.dumps({"upscaler": "realesrgan", "denoise": 0.25,
                                                  "seed": 0, "source": str(p / "001.png")}))
    _png(src / "tmp" / "m.png")
    _png(p / "001_inpaint.png", (128, 96), mtime=3000)
    (p / "001_inpaint.json").write_text(json.dumps({
        "mask": str(src / "tmp" / "m.png"), "prompt": "apple", "seed": 9,
        "source": str(p / "001_hires.png")}))
    # panel 2: no sidecar; a refine whose source is gone
    _png(p / "002.png", mtime=1000)
    _png(p / "002_hires.png", mtime=1500)
    (p / "002_hires.json").write_text(json.dumps({"upscaler": "x", "source": str(p / "gone.png")}))
    # panel 3: a looping pair (overwritten files) + a stray file for a panel that's gone
    _png(p / "003_hires.png", mtime=1000)
    _png(p / "003_inpaint.png", mtime=1100)
    (p / "003_hires.json").write_text(json.dumps({"upscaler": "x",
                                                  "source": str(p / "003_inpaint.png")}))
    (p / "003_inpaint.json").write_text(json.dumps({"mask": str(src / "tmp" / "m.png"),
                                                    "source": str(p / "003_hires.png")}))
    _png(p / "009.png")
    return src


def test_import_builds_a_valid_container(tmp_path):
    src = _legacy(tmp_path)
    dest = tmp_path / "rooftop.imanga"
    doc = import_project(src, dest)

    assert integrity_errors(doc, root=dest) == []
    jsonschema.Draft202012Validator(SCHEMA).validate(doc)
    assert load_project(dest) == doc  # the plug-in's own loader accepts it
    assert not (tmp_path / "rooftop.imanga.importing").exists()

    p1, p2, p3 = doc["panels"]
    assert [(p["label"]["page"], p["label"]["panel"]) for p in doc["panels"]] == [
        (1, 1), (2, 1), (2, 1)]  # duplicate labels, distinct ids
    assert len({p["id"] for p in doc["panels"]}) == 3
    chain = [doc["takes"][t] for t in p1["takes"]]
    assert [t["kind"] for t in chain] == ["render", "refine", "inpaint"]
    assert chain[1]["parent"] == p1["takes"][0] and chain[2]["parent"] == p1["takes"][1]
    assert all(t["origin"] == p1["takes"][0] for t in chain)
    assert chain[2]["engine"]["mask"].startswith("masks/")
    assert (dest / chain[2]["engine"]["mask"]).is_file()
    assert p1["active_take"] == p1["takes"][-1]  # newest file
    assert all((dest / t["file"]).is_file() for t in doc["takes"].values())

    assert [doc["takes"][t]["kind"] for t in p2["takes"]] == ["import", "import"]
    assert "no longer exists" in doc["takes"][p2["takes"][1]]["engine"]["import_note"]
    kinds3 = sorted(doc["takes"][t]["kind"] for t in p3["takes"])
    assert "import" in kinds3  # the loop was broken, not trusted

    assert doc["cast"] == [{"name": "Akira", "aliases": [], "notes": ""},
                           {"name": "Yuki", "aliases": ["Yu"], "notes": ""}]  # no traits
    assert doc["cursor"]["next_panel"] == p3["id"]
    assert doc["project"]["import_skipped"] == ["009.png"]
    assert doc["script"]["file"] == "script/script.md" and len(doc["script"]["sha256"]) == 64
    # the source is untouched
    assert (src / "panels/001_hires.json").exists() and (src / "panels.json").exists()


def test_import_refuses_existing_destination_and_non_projects(tmp_path):
    src = _legacy(tmp_path)
    (tmp_path / "taken").mkdir()
    with pytest.raises(ImportError_, match="already exists"):
        import_project(src, tmp_path / "taken")
    with pytest.raises(ImportError_, match="no panels.json"):
        import_project(tmp_path, tmp_path / "x.imanga")
