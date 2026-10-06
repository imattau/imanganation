"""Trial renderers (Qwen-Image 2.1, Z-Image): graphs and prose prompts, no GPU."""

from __future__ import annotations

from manganation.config import load_models
from manganation.render import graphs
from manganation.render.panel import prose_prompt, trial_files
from manganation.script.schema import PanelSpec


def test_qwen_graph_passes_every_reference_to_the_encoder():
    g = graphs.qwen_image21(unet="u", clip="c", vae="v", prompt="p", refs=["a.png", "b.png"],
                            width=1408, height=768, seed=5, prefix="x")
    enc = g["encode"]["inputs"]
    assert enc["images.image_1"] == ["ref1", 0] and enc["images.image_2"] == ["ref2", 0]
    assert g["ref2"]["inputs"]["image"] == "b.png"
    assert g["clip"]["inputs"]["type"] == "qwen_image"
    ks = g["5"]["inputs"]
    assert ks["cfg"] == 1.0 and ks["negative"] == ["encode", 1]
    assert (g["4"]["inputs"]["width"], g["4"]["inputs"]["height"]) == (1408, 768)


def test_z_image_graph_follows_the_comfy_template():
    g = graphs.z_image(unet="u", clip="c", vae="v", prompt="p", negative="n", width=1024,
                       height=1024, seed=1, prefix="x")
    assert g["clip"]["inputs"]["type"] == "lumina2"
    assert g["shift"]["class_type"] == "ModelSamplingAuraFlow"
    assert g["4"]["class_type"] == "EmptySD3LatentImage"
    assert g["5"]["inputs"]["model"] == ["shift", 0]


def test_prose_prompt_introduces_each_character_and_keeps_the_script():
    spec = PanelSpec(page=2, panel=3, characters=["Yuki", "Akira"], camera="medium shot",
                     location="the stairwell", scene_heading="School rooftop — cont.",
                     action="Medium shot. Yuki drags Akira by the wrist down the stairs.")
    looks = {"Yuki": ["1girl", "white hair"], "Akira": ["1boy", "brown hair"]}
    text = prose_prompt(spec, looks, {"Yuki": 1, "Akira": 2})
    assert "exactly two people" in text and "Setting: the stairwell." in text
    assert "Medium shot: framed from the waist up." in text
    assert "Yuki is a girl (take only the face, hair and outfit from <image1>" in text
    assert ": white hair." in text
    assert "Akira is a boy (take only the face, hair and outfit from <image2>" in text
    assert text.endswith("What happens: Yuki drags Akira by the wrist down the stairs.")
    assert "1girl" not in text  # count tags become words


def test_trials_are_registered_with_their_files():
    models = load_models()
    assert set(trial_files(models, "qwen_image_21")) == {"model", "text_encoder", "vae"}
    assert set(trial_files(models, "z_anime")) == {"model", "text_encoder", "vae"}
    assert trial_files(models, "illustrious_v2")["model"].endswith(".safetensors")


def test_init_image_replaces_the_empty_latent():
    base = graphs.txt2img(ckpt="m", prompt="p", negative="n", width=1408, height=768, seed=1,
                          prefix="x")
    g = graphs.with_init_image(base, image="qwen.png", width=1408, height=768, denoise=0.4)
    assert g["4"]["class_type"] == "VAEEncode"
    assert g["init_scaled"]["inputs"]["image"] == ["init_image", 0]
    assert g["5"]["inputs"]["denoise"] == 0.4 and g["5"]["inputs"]["latent_image"] == ["4", 0]


def test_shots_become_framing_instructions():
    from manganation.render.panel import shot_prose

    assert shot_prose("Extreme close-up") == "Extreme close-up: the face fills the whole frame."
    assert shot_prose("close-up") == "Close-up: the head and shoulders fill the frame."
    assert shot_prose("tracking shot") == "Shot: tracking shot."
