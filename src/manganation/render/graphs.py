"""ComfyUI workflow graphs (API format) for single-panel renders.

Lifted from the Phase 0 spike and parameterised; model files are referenced by the
roles in config/models.yaml, resolved by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Sampling:
    steps: int = 28
    cfg: float = 6.0
    sampler: str = "dpmpp_2m"
    scheduler: str = "karras"


def txt2img(
    *,
    ckpt: str,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int,
    prefix: str,
    sampling: Sampling = Sampling(),
) -> dict:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": sampling.steps,
                "cfg": sampling.cfg,
                "sampler_name": sampling.sampler,
                "scheduler": sampling.scheduler,
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        },
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": prefix, "images": ["6", 0]},
        },
    }


def img2img(
    *,
    ckpt: str,
    image: str,
    prompt: str,
    negative: str,
    seed: int,
    prefix: str,
    denoise: float = 0.6,
    sampling: Sampling = Sampling(),
) -> dict:
    """Re-render an existing image with a new prompt (LoadImage -> VAEEncode -> KSampler).

    Used to derive character dataset variants from a locked design sheet: at moderate
    ``denoise`` the identity/anatomy of the source is kept while the prompt changes the
    pose, expression or background. Output resolution follows the source image.
    """
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "8": {"class_type": "LoadImage", "inputs": {"image": image}},
        "4": {"class_type": "VAEEncode", "inputs": {"pixels": ["8", 0], "vae": ["1", 2]}},
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": sampling.steps,
                "cfg": sampling.cfg,
                "sampler_name": sampling.sampler,
                "scheduler": sampling.scheduler,
                "denoise": denoise,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        },
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": prefix, "images": ["6", 0]},
        },
    }


def inpaint(
    *,
    ckpt: str,
    image: str,
    mask: str,
    prompt: str,
    negative: str,
    seed: int,
    prefix: str,
    denoise: float = 0.85,
    grow_mask_by: int = 8,
    mask_channel: str = "alpha",
    sampling: Sampling = Sampling(),
) -> dict:
    """Repaint only the masked region of an existing image.

    The init image is encoded to a latent, then ``SetLatentNoiseMask`` marks the region
    to regenerate; the sampler runs at high ``denoise`` so the masked area is
    re-synthesised from noise while everything outside the mask is preserved. The mask
    is dilated (``grow_mask_by``) so the new pixels blend into their surroundings.

    ``mask_channel`` selects which channel of the uploaded mask image is the mask:
    ``alpha`` (a transparent selection export), or a colour channel for an opaque
    black/white mask.
    """
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "8": {"class_type": "LoadImage", "inputs": {"image": image}},
        "9": {
            "class_type": "LoadImageMask",
            "inputs": {"image": mask, "channel": mask_channel},
        },
        "10": {
            "class_type": "GrowMask",
            "inputs": {
                "mask": ["9", 0], "expand": grow_mask_by, "tapered_corners": True,
            },
        },
        "4": {"class_type": "VAEEncode", "inputs": {"pixels": ["8", 0], "vae": ["1", 2]}},
        "11": {
            "class_type": "SetLatentNoiseMask",
            "inputs": {"samples": ["4", 0], "mask": ["10", 0]},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": sampling.steps,
                "cfg": sampling.cfg,
                "sampler_name": sampling.sampler,
                "scheduler": sampling.scheduler,
                "denoise": denoise,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["11", 0],
            },
        },
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": prefix, "images": ["6", 0]},
        },
    }


def with_ipadapter(
    graph: dict,
    *,
    ref_image: str,
    ipadapter: str,
    clip_vision: str,
    weight: float = 0.75,
) -> dict:
    """Route the model through IP-Adapter Plus using one uploaded reference image."""
    graph = {k: {**v, "inputs": dict(v["inputs"])} for k, v in graph.items()}
    graph["8"] = {"class_type": "LoadImage", "inputs": {"image": ref_image}}
    graph["9"] = {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": ipadapter}}
    graph["10"] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": clip_vision}}
    graph["11"] = {
        "class_type": "IPAdapterAdvanced",
        "inputs": {
            "model": ["1", 0],
            "ipadapter": ["9", 0],
            "image": ["8", 0],
            "clip_vision": ["10", 0],
            "weight": weight,
            "weight_type": "linear",
            "combine_embeds": "concat",
            "start_at": 0.0,
            "end_at": 1.0,
            "embeds_scaling": "V only",
        },
    }
    graph["5"]["inputs"]["model"] = ["11", 0]
    return graph


def with_regional_ipadapter(
    graph: dict,
    *,
    references: list[dict],
    ipadapter: str,
    clip_vision: str,
    combine_embeds: str = "concat",
    embeds_scaling: str = "V only",
) -> dict:
    """Bind several character references to separate regions of one panel.

    ``references`` is a list of ``{"image": <uploaded name>, "mask": [x, y, w, h]}``
    where the mask is a pixel box (from ``layout.regions``). Each reference becomes
    an ``IPAdapterRegionalConditioning`` (its own mask + weight); the params are
    concatenated via ``IPAdapterCombineParams`` and applied by ``IPAdapterFromParams``.

    Falls back to the single-reference path for a one-entry list.
    """
    if not references:
        raise ValueError("regional IP-Adapter needs at least one reference")
    if len(references) == 1:
        return with_ipadapter(
            graph,
            ref_image=references[0]["image"],
            ipadapter=ipadapter,
            clip_vision=clip_vision,
            weight=references[0].get("weight", 0.75),
        )

    graph = {k: {**v, "inputs": dict(v["inputs"])} for k, v in graph.items()}
    graph["9"] = {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": ipadapter}}
    graph["10"] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": clip_vision}}

    mask_ids: list[str] = []
    params_ids: list[str] = []
    next_id = 12
    for ref in references:
        img_id, mask_id, cond_id = str(next_id), str(next_id + 1), str(next_id + 2)
        next_id += 3
        x, y, w, h = ref["mask"]
        graph[img_id] = {"class_type": "LoadImage", "inputs": {"image": ref["image"]}}
        # A solid box mask: full value, positioned by MaskComposite onto an empty canvas.
        graph[mask_id] = {
            "class_type": "SolidMask",
            "inputs": {"value": 1.0, "width": w, "height": h},
        }
        # Place the solid mask at (x, y) on a full-canvas empty mask.
        empty_id = f"{mask_id}_base"
        placed_id = f"{mask_id}_placed"
        graph[empty_id] = {
            "class_type": "SolidMask",
            "inputs": {"value": 0.0, "width": ref["canvas_w"], "height": ref["canvas_h"]},
        }
        graph[placed_id] = {
            "class_type": "MaskComposite",
            "inputs": {
                "destination": [empty_id, 0],
                "source": [mask_id, 0],
                "x": x,
                "y": y,
                "operation": "add",
            },
        }
        # Feather the region edges so neighbouring characters blend instead of
        # meeting at a hard seam.
        feather = ref.get("feather", 48)
        if feather:
            feathered_id = f"{mask_id}_feather"
            graph[feathered_id] = {
                "class_type": "FeatherMask",
                "inputs": {
                    "mask": [placed_id, 0],
                    "left": feather,
                    "top": feather,
                    "right": feather,
                    "bottom": feather,
                },
            }
            placed_id = feathered_id
        graph[cond_id] = {
            "class_type": "IPAdapterRegionalConditioning",
            "inputs": {
                "image": [img_id, 0],
                "image_weight": ref.get("weight", 0.75),
                "prompt_weight": 1.0,
                "weight_type": ref.get("weight_type", "linear"),
                "start_at": ref.get("start_at", 0.0),
                "end_at": ref.get("end_at", 1.0),
                "mask": [placed_id, 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
            },
        }
        mask_ids.append(placed_id)
        params_ids.append(cond_id)

    # Concatenate the per-character params into one IPADAPTER_PARAMS.
    combined = params_ids[0]
    for i, pid in enumerate(params_ids[1:], start=1):
        out_id = f"combine{i}"
        graph[out_id] = {
            "class_type": "IPAdapterCombineParams",
            "inputs": {"params_1": [combined, 0], "params_2": [pid, 0]},
        }
        combined = out_id

    graph["apply"] = {
        "class_type": "IPAdapterFromParams",
        "inputs": {
            "model": ["1", 0],
            "ipadapter": ["9", 0],
            "ipadapter_params": [combined, 0],
            "combine_embeds": combine_embeds,
            "embeds_scaling": embeds_scaling,
            "clip_vision": ["10", 0],
        },
    }
    graph["5"]["inputs"]["model"] = ["apply", 0]
    return graph


def with_regional_conditioning(
    graph: dict,
    *,
    regions: list[dict],
    combine: str = "combine",
) -> dict:
    """Place each character's prompt tags in their own spatial region.

    ``regions`` is a list of ``{"text": <tags>, "box": (x, y, w, h)}`` as fractions
    (0..1, from ``layout.regions.assign_regions``). Each is encoded with the base
    checkpoint CLIP and constrained via ``ConditioningSetAreaPercentage``, then folded
    into the positive conditioning with ``ConditioningCombine`` (or ``Concat``).

    Without this, a multi-character prompt makes SDXL stack or merge the figures; with
    it, each character's tokens attend to their band of the canvas.
    """
    if not regions:
        return graph
    graph = {k: {**v, "inputs": dict(v["inputs"])} for k, v in graph.items()}
    combined_id = "2"
    next_id = 20
    for region in regions:
        enc_id, area_id = str(next_id), str(next_id + 1)
        next_id += 2
        x, y, w, h = region["box"]
        graph[enc_id] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": region["text"], "clip": ["1", 1]},
        }
        graph[area_id] = {
            "class_type": "ConditioningSetAreaPercentage",
            "inputs": {
                "conditioning": [enc_id, 0],
                "width": w,
                "height": h,
                "x": x,
                "y": y,
                "strength": region.get("strength", 1.0),
            },
        }
        out_id = f"cond_{next_id}"
        graph[out_id] = {
            "class_type": "ConditioningCombine" if combine == "combine" else "ConditioningConcat",
            "inputs": {"conditioning_1": [combined_id, 0], "conditioning_2": [area_id, 0]},
        }
        combined_id = out_id
    graph["5"]["inputs"]["positive"] = [combined_id, 0]
    return graph


def upscale_refine(
    *,
    ckpt: str,
    image: str,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int,
    prefix: str,
    upscale_model: str,
    denoise: float = 0.2,
    polish_width: int | None = None,
    polish_height: int | None = None,
    sampling: Sampling = Sampling(),
) -> dict:
    """Two-pass hi-res fix of an existing panel (docs/phase6a.md).

    Pass 1 polishes the source with a low-``denoise`` img2img pass — most of the
    signal is kept in the source latent, so composition and identity survive while
    fine detail is re-synthesised. The polish runs at ``polish_width``/``polish_height``
    (defaults to the source size): sampling and the VAE round-trip stay within one
    VAE tile, which avoids the blocky seams a tiled VAE leaves at high resolution.

    Pass 2 upscales the polished image to the final ``width``×``height`` with the
    model upscaler, so the last resample is a real super-resolution step rather than
    an interpolated one.

    When ``denoise`` is 0 the polish is skipped and the pure upscale is saved, so the
    graph still returns exactly one image.
    """
    polish_w = polish_width or width
    polish_h = polish_height or height
    graph: dict = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "8": {"class_type": "LoadImage", "inputs": {"image": image}},
        "20": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscale_model}},
    }

    # Polish the source at (or near) its native size, then upscale the result.
    if denoise > 0:
        graph["10"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["8", 0], "upscale_method": "lanczos",
                "width": polish_w, "height": polish_h, "crop": "disabled",
            },
        }
        graph["4"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["1", 2]}}
        graph["5"] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": sampling.steps,
                "cfg": sampling.cfg,
                "sampler_name": sampling.sampler,
                "scheduler": sampling.scheduler,
                "denoise": denoise,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        }
        graph["6"] = {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}}
        to_upscale = "6"
    else:
        to_upscale = "8"

    graph["21"] = {
        "class_type": "ImageUpscaleWithModel",
        "inputs": {"upscale_model": ["20", 0], "image": [to_upscale, 0]},
    }
    graph["22"] = {
        "class_type": "ImageScale",
        "inputs": {
            "image": ["21", 0], "upscale_method": "lanczos",
            "width": width, "height": height, "crop": "disabled",
        },
    }
    graph["7"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": prefix, "images": ["22", 0]},
    }
    return graph
