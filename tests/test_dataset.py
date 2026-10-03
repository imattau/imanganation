"""Tests for Phase 6b: synthetic per-character training-set generation (no GPU)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manganation.characters.dataset import VariantSpec, variant_specs
from manganation.characters.dataset_generator import build_dataset, dataset_dir
from manganation.characters.schema import AppearanceSpec, Character
from manganation.render import graphs

# --- variant planning ---------------------------------------------------------


def _character() -> Character:
    return Character(
        name="Akira",
        appearance=AppearanceSpec(
            gender="1boy", hair_color="charcoal", hair_style="messy",
            eye_color="amber eyes", outfit="school uniform",
        ),
    )


def test_variant_specs_count_and_determinism():
    c = _character()
    a = variant_specs(c, 12)
    b = variant_specs(c, 12)
    assert len(a) == 12
    assert [s.positive for s in a] == [s.positive for s in b]  # deterministic


def test_variant_specs_keep_identity_tags_first():
    c = _character()
    for spec in variant_specs(c, 6):
        # appearance tags must lead the prompt so the LoRA binds to the person
        assert spec.positive.startswith("1boy")
        assert "charcoal" in spec.positive
        assert "school uniform" in spec.positive
        # caption leads with the identity too
        assert spec.caption.startswith("1boy")


def test_variant_specs_actually_vary():
    specs = variant_specs(_character(), 12)
    positives = {s.positive for s in specs}
    notes = {s.note for s in specs}
    assert len(positives) == 12  # every variant is distinct
    assert len(notes) == 12
    # the variation axes show up as different notes/tags across the set
    assert any("front" in s.note for s in specs)
    assert any("profile" in s.note for s in specs)


def test_variant_specs_zero_count():
    assert variant_specs(_character(), 0) == []


def test_variant_specs_carry_no_extra_people():
    neg = variant_specs(_character(), 3)[0].negative
    assert "multiple characters" in neg and "2people" in neg


# --- img2img graph ------------------------------------------------------------


def test_img2img_graph_encodes_source_with_denoise():
    g = graphs.img2img(
        ckpt="noobaiXL.safetensors", image="a.png", prompt="p", negative="n",
        seed=5, prefix="t", denoise=0.6,
    )
    assert g["8"]["class_type"] == "LoadImage"
    assert g["4"]["class_type"] == "VAEEncode"
    assert g["5"]["inputs"]["denoise"] == 0.6
    assert g["5"]["inputs"]["latent_image"] == ["4", 0]
    assert g["5"]["inputs"]["seed"] == 5


# --- build_dataset with a fake ComfyUI ----------------------------------------


class FakeComfy:
    def __init__(self):
        self.graphs = []

    def is_up(self):
        return True

    def upload_image(self, path):
        return {"name": Path(path).name}

    def run(self, graph):
        self.graphs.append(graph)
        return [b"\x89PNG fake"]


def test_build_dataset_writes_images_captions_manifest(tmp_path):
    project = tmp_path
    cdir = project / "characters/akira"
    cdir.mkdir(parents=True)
    (cdir / "base.png").write_bytes(b"seed")
    (project / "characters.json").write_text("{}")  # registry marker; cast empty

    comfy = FakeComfy()
    c = _character()
    result = build_dataset(
        c, project_root=project, seed_image=cdir / "base.png",
        count=4, base_seed=100, client=comfy,
    )

    out = dataset_dir(project, "Akira")
    assert result.count == 4
    for i in range(1, 5):
        assert (out / f"{i:02d}.png").exists()
        caption = (out / f"{i:02d}.txt").read_text().strip()
        assert caption.startswith("1boy")
    manifest = json.loads((out / "dataset.json").read_text())
    assert len(manifest["items"]) == 4
    assert manifest["items"][0]["seed"] == 101  # base_seed + index
    # every render was an img2img of the seed
    assert all(g["8"]["inputs"]["image"] == "base.png" for g in comfy.graphs)


def test_build_dataset_requires_seed_image(tmp_path):
    (tmp_path / "characters.json").write_text("{}")
    with pytest.raises(ValueError, match="no seed/design image"):
        build_dataset(_character(), project_root=tmp_path, client=FakeComfy())


def test_build_dataset_requires_comfy(tmp_path):
    class Down(FakeComfy):
        def is_up(self):
            return False

    cdir = tmp_path / "characters/akira"
    cdir.mkdir(parents=True)
    (cdir / "base.png").write_bytes(b"seed")
    with pytest.raises(RuntimeError, match="not reachable"):
        build_dataset(_character(), project_root=tmp_path,
                      seed_image=cdir / "base.png", client=Down())


def test_variant_spec_dataclass_defaults():
    s = VariantSpec(index=1, positive="p", negative="n", note="x", caption="c")
    assert s.denoise == 0.6 and s.tags == []
