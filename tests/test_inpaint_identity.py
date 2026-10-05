"""Inpaint keeps characters on-model: traits in the prompt, IP-Adapter for one character."""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from manganation.characters.registry import CharacterRegistry
from manganation.characters.schema import AppearanceSpec
from manganation.render import graphs
from manganation.render.inpaint import InpaintError, InpaintResult, inpaint_inline
from manganation.web.api import create_app


class FakeComfy:
    def __init__(self):
        self.graphs, self.uploads, self.sizes = [], [], []

    def is_up(self):
        return True

    def upload_image(self, path):
        self.uploads.append(Path(path).name)
        with Image.open(path) as im:
            self.sizes.append(im.size)
        return {"name": Path(path).name}

    def run(self, graph):
        self.graphs.append(graph)
        buf = io.BytesIO()
        Image.new("RGB", self.sizes[0], (255, 0, 0)).save(buf, "PNG")
        return [buf.getvalue()]


def _identity(tmp_path: Path) -> Path:
    root = tmp_path / "cast"
    reg = CharacterRegistry.from_path(root)
    for name, versions, traits in (
        ("Yuki", ("base", "summer"), AppearanceSpec(
            gender="1girl", hair_color="silver hair",
            distinguishing=["wide toothed grin", "snowflake pin"],
            descriptors=["energetic stance"])),
        ("Akira", ("base",), AppearanceSpec(gender="1boy", hair_color="brown hair")),
    ):
        for v in versions:
            src = tmp_path / f"{name}-{v}.png"
            Image.new("RGB", (32, 32), "white").save(src)
            reg.add_user_reference(name, str(src), v)
        reg.get(name).appearance = traits
    reg.set_default("Yuki", "base")
    reg.save()
    return root


def _images(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "take.png"
    Image.new("RGB", (400, 300), "blue").save(src)
    mask = Image.new("L", (400, 300), 0)
    mask.paste(255, (180, 130, 220, 170))
    mpath = tmp_path / "mask.png"
    mask.save(mpath)
    return src, mpath


def _inpaint(tmp_path, characters, **kw):
    tmp_path.mkdir(parents=True, exist_ok=True)
    src, mask = _images(tmp_path)
    comfy = FakeComfy()
    r = inpaint_inline("prj_test01", src, mask, prompt="surprised face", client=comfy,
                       outputs=tmp_path / "out", characters=characters,
                       identity=_identity(tmp_path), **kw)
    return r, comfy


def test_graph_routes_the_model_through_ipadapter_only_when_asked():
    plain = graphs.inpaint(ckpt="c", image="a", mask="m", prompt="p", negative="n", seed=1,
                           prefix="t")
    assert plain["5"]["inputs"]["model"] == ["1", 0] and "23" not in plain
    g = graphs.inpaint(ckpt="c", image="a", mask="m", prompt="p", negative="n", seed=1,
                       prefix="t", ipadapter={"ref_image": "yuki.png", "ipadapter_file": "i",
                                              "clip_name": "v", "weight": 0.6})
    assert g["5"]["inputs"]["model"] == ["23", 0]
    assert g["23"]["inputs"]["image"] == ["20", 0] and g["23"]["inputs"]["weight"] == 0.6
    assert g["20"]["inputs"]["image"] == "yuki.png"
    assert g["8"]["class_type"] == "LoadImage" and g["11"]["class_type"] == "SetLatentNoiseMask"


def test_one_character_adds_traits_and_their_reference(tmp_path):
    r, comfy = _inpaint(tmp_path, [{"name": "yuki"}])
    text = comfy.graphs[0]["2"]["inputs"]["text"]
    assert "surprised face, 1girl, silver hair" in text and "brown hair" not in text  # prompt leads
    assert "Yuki-base.png" not in comfy.uploads  # registry copies are named by version
    assert "base.png" in comfy.uploads  # the reference was uploaded for IP-Adapter
    assert comfy.graphs[0]["5"]["inputs"]["model"] == ["23", 0]
    assert r.characters == {"Yuki": "base"}


def test_expression_and_pose_traits_dont_override_the_prompt(tmp_path):
    """Yuki's stored "wide toothed grin" beat "surprised face" live; it's dropped."""
    _, comfy = _inpaint(tmp_path, [{"name": "Yuki"}])
    text = comfy.graphs[0]["2"]["inputs"]["text"]
    assert "grin" not in text and "stance" not in text
    assert "snowflake pin" in text  # a physical distinguishing trait stays


def test_version_selects_that_reference(tmp_path):
    r, comfy = _inpaint(tmp_path, [{"name": "Yuki", "version": "summer"}])
    assert "summer.png" in comfy.uploads and r.characters == {"Yuki": "summer"}


def test_two_characters_each_get_a_masked_reference(tmp_path):
    r, comfy = _inpaint(tmp_path, [{"name": "Yuki"}, {"name": "Akira"}])
    g = comfy.graphs[0]
    text = g["2"]["inputs"]["text"]
    # one head count, each character's traits kept together
    assert "surprised face, 1boy, 1girl, silver hair, snowflake pin, brown hair" in text
    assert "23" not in g  # no single reference pulling both faces
    conds = {k: n for k, n in g.items() if n["class_type"] == "IPAdapterRegionalConditioning"}
    assert len(conds) == 2 and all(k.startswith("ipa_") for k in conds)
    assert g["5"]["inputs"]["model"] == ["ipa_apply", 0]
    # the inpaint chain's own nodes are untouched by the regional ids
    assert g["9"]["class_type"] == "LoadImageMask" and g["10"]["class_type"] == "GrowMask"
    # RTL: Yuki (first) on the right half of the work canvas, Akira on the left
    work_w = comfy.sizes[0][0]
    xs = [g[n["inputs"]["mask"][0].removesuffix("_soft") + "_placed"]["inputs"]["x"]
          for n in conds.values()]
    assert xs[0] >= work_w // 2 > xs[1]
    assert set(r.characters) == {"Yuki", "Akira"}


def test_two_characters_one_reference_keeps_its_band(tmp_path):
    identity = _identity(tmp_path)
    reg = CharacterRegistry.from_path(identity)
    reg.ensure("Mio")  # no reference image
    reg.save()
    src, mask = _images(tmp_path)
    comfy = FakeComfy()
    r = inpaint_inline("prj_test01", src, mask, prompt="x", client=comfy,
                       outputs=tmp_path / "out", identity=identity, reading_order="ltr",
                       characters=[{"name": "Mio"}, {"name": "Akira"}])
    g = comfy.graphs[0]
    conds = [n for n in g.values() if n["class_type"] == "IPAdapterRegionalConditioning"]
    assert len(conds) == 1 and "23" not in g
    placed = g[conds[0]["inputs"]["mask"][0].removesuffix("_soft") + "_placed"]
    assert placed["inputs"]["x"] >= comfy.sizes[0][0] // 2  # LTR: Akira second = right
    assert r.characters == {"Mio": "traits only", "Akira": "base"}


def test_no_characters_is_unchanged_behaviour(tmp_path):
    src, mask = _images(tmp_path)
    comfy = FakeComfy()
    r = inpaint_inline("prj_test01", src, mask, prompt="red apple", client=comfy,
                       outputs=tmp_path / "out")
    assert comfy.graphs[0]["2"]["inputs"]["text"].endswith(", red apple")
    assert "23" not in comfy.graphs[0] and r.characters == {}


def test_unknown_character_or_version_fails_with_what_exists(tmp_path):
    with pytest.raises(InpaintError, match="no character 'Bob'.*Yuki"):
        _inpaint(tmp_path / "a", [{"name": "Bob"}])
    with pytest.raises(InpaintError, match="no reference version 'winter'.*base, summer"):
        _inpaint(tmp_path / "b", [{"name": "Yuki", "version": "winter"}])


def test_api_passes_characters_through(tmp_path):
    seen = {}

    def fake(project, source, mask, **kw):
        seen.update(kw)
        return InpaintResult(path="i.png", seq=None, source=str(source), mask=str(mask),
                             prompt=kw["prompt"], width=2, height=2, denoise=0.85,
                             grow_mask_by=8, seed=0)

    projects = tmp_path / "projects"
    take = projects / "p.imanga/takes/t.png"
    take.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(take)
    client = TestClient(create_app(root=projects, outputs=tmp_path / "outputs",
                                   inpaint_inline=fake))
    job_id = client.post("/inpaint", json={
        "project": "prj_test01", "source": str(take), "mask": str(take), "prompt": "x",
        "characters": ["Akira", {"name": "Yuki", "version": "summer"}]}).json()["id"]
    for _ in range(100):
        if client.get(f"/jobs/{job_id}").json()["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert seen["characters"] == [{"name": "Akira"}, {"name": "Yuki", "version": "summer"}]
