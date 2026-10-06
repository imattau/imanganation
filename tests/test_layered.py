"""Layered renders (prototype): background plate first, characters painted into it."""

from __future__ import annotations

from manganation.render import graphs
from manganation.render.panel import background_prompt
from manganation.render.staging import Staging
from manganation.script.schema import PanelSpec

AREA = {"mask": [0, 0, 512, 1024], "canvas_w": 1024, "canvas_h": 1024, "feather": 32}


def _sampler_chain(g: dict, node: str) -> list[str]:
    """Samplers from the decoded one back to the plate, following latent inputs."""
    chain = []
    while node in g:
        if g[node]["class_type"] == "KSampler":
            chain.append(node)
        node = (g[node]["inputs"].get("latent_image") or g[node]["inputs"].get("samples")
                or [None])[0]
    return chain


def test_per_character_paints_each_one_into_the_plate_in_turn():
    g = graphs.layered_per_character(
        ckpt="m", background="no humans, rooftop", background_negative="1girl",
        negative="text", width=1024, height=1024, seed=7, prefix="p",
        passes=[{**AREA, "prompt": "solo, 1girl", "ref": "yuki.png", "weight": 0.4},
                {**AREA, "mask": [512, 0, 512, 1024], "prompt": "solo, 1boy", "ref": None}],
        ipadapter="ipa", clip_vision="vit", denoise=0.85)
    assert _sampler_chain(g, g["6"]["inputs"]["samples"][0]) == [
        "c1_sampler", "c0_sampler", "bg_sampler"]
    assert g["bg_sampler"]["inputs"]["denoise"] == 1.0
    assert g["c0_sampler"]["inputs"]["denoise"] == 0.85
    # only Yuki's pass has her reference, masked to her area
    assert g["c0_sampler"]["inputs"]["model"] == ["c0_ipa", 0]
    assert g["c0_ipa"]["inputs"]["attn_mask"][0].startswith("c0_mask")
    assert g["c1_sampler"]["inputs"]["model"] == ["1", 0]
    assert g["c0_pos"]["inputs"]["text"] == "solo, 1girl"


def test_background_prompt_is_the_place_without_people():
    spec = PanelSpec(page=1, panel=1, characters=["Yuki", "Akira"], camera="low angle shot",
                     scene_heading="School rooftop — cont.")
    staged = Staging(setting=["rooftop", "outdoors"])
    assert background_prompt(spec, {"prompt_prefix": "manga, "}, staged) == \
        "manga, no humans, scenery, rooftop, outdoors, from below"
    assert background_prompt(spec, {}, None) == "no humans, scenery, School rooftop, from below"
