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
    ipadapter: dict | None = None,
) -> dict:
    """Repaint only the masked region of an existing image.

    The init image is encoded to a latent, then ``SetLatentNoiseMask`` marks the region
    to regenerate; the sampler runs at high ``denoise`` so the masked area is
    re-synthesised from noise. The mask is dilated (``grow_mask_by``) so the new pixels
    blend into their surroundings.

    Decoding re-renders the *whole* latent, and a VAE round trip shifts line work
    everywhere (measured: 7% of pixels outside the mask moved by >8 levels, up to
    179). So the decoded image is composited back over the original through a blurred
    copy of the grown mask: outside it the original pixels come through untouched,
    and the seam is soft rather than a hard edge.

    ``mask_channel`` selects which channel of the uploaded mask image is the mask. Use a
    colour channel of an opaque white-on-black mask (what ``render.inpaint`` uploads):
    ComfyUI's ``alpha`` reads ``1 - alpha``, i.e. transparent = masked.

    ``ipadapter`` (``ref_image``, ``ipadapter_file``, ``clip_name``, ``weight``) routes
    the model through IP-Adapter with a character's reference, so a repainted face stays
    on-model. Its nodes use ids 20-23 (8-15 are the inpaint chain's).
    """
    graph = {
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
        # Soft blend mask: grown mask -> image -> blur -> mask.
        "12": {"class_type": "MaskToImage", "inputs": {"mask": ["10", 0]}},
        "13": {
            "class_type": "ImageBlur",
            "inputs": {"image": ["12", 0], "blur_radius": max(1, min(31, grow_mask_by // 2)),
                       "sigma": 1.0},
        },
        "14": {"class_type": "ImageToMask", "inputs": {"image": ["13", 0], "channel": "red"}},
        "15": {
            "class_type": "ImageCompositeMasked",
            "inputs": {"destination": ["8", 0], "source": ["6", 0], "x": 0, "y": 0,
                       "resize_source": False, "mask": ["14", 0]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": prefix, "images": ["15", 0]},
        },
    }
    if ipadapter is not None:
        graph["20"] = {"class_type": "LoadImage", "inputs": {"image": ipadapter["ref_image"]}}
        graph["21"] = {"class_type": "IPAdapterModelLoader",
                       "inputs": {"ipadapter_file": ipadapter["ipadapter_file"]}}
        graph["22"] = {"class_type": "CLIPVisionLoader",
                       "inputs": {"clip_name": ipadapter["clip_name"]}}
        graph["23"] = {
            "class_type": "IPAdapterAdvanced",
            "inputs": {
                "model": ["1", 0], "ipadapter": ["21", 0], "image": ["20", 0],
                "clip_vision": ["22", 0], "weight": ipadapter.get("weight", 0.6),
                "weight_type": "linear", "combine_embeds": "concat",
                "start_at": 0.0, "end_at": 1.0, "embeds_scaling": "V only",
            },
        }
        graph["5"]["inputs"]["model"] = ["23", 0]
    return graph


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


def _soft_mask(graph: dict, region: dict, mask_id: str) -> str:
    """Add a region's mask to ``graph``; -> the id of its (feathered) mask node.

    ``region`` has ``mask_image`` (an uploaded canvas-sized placement, white = this
    character) or ``mask`` (a pixel box ``[x, y, w, h]``) with ``canvas_w``/``canvas_h``,
    and ``feather`` (px, default 48)."""
    if region.get("mask_image"):
        # The artist's placement: already canvas-sized, white = this character.
        placed_id = f"{mask_id}_placed"
        graph[placed_id] = {
            "class_type": "LoadImageMask",
            "inputs": {"image": region["mask_image"], "channel": "red"},
        }
    else:
        x, y, w, h = region["mask"]
        # A solid box mask, positioned by MaskComposite onto an empty canvas.
        graph[mask_id] = {
            "class_type": "SolidMask",
            "inputs": {"value": 1.0, "width": w, "height": h},
        }
        empty_id = f"{mask_id}_base"
        placed_id = f"{mask_id}_placed"
        graph[empty_id] = {
            "class_type": "SolidMask",
            "inputs": {"value": 0.0, "width": region["canvas_w"],
                       "height": region["canvas_h"]},
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
    # Soften the region's own edges so neighbouring characters blend instead of
    # meeting at a hard seam. (ComfyUI's FeatherMask can't do this: it fades a mask
    # towards the *canvas* borders, which left the inner boundary a hard cut.)
    feather = region.get("feather", 48)
    if feather:
        as_image, blurred, soft = (f"{mask_id}_img", f"{mask_id}_blur", f"{mask_id}_soft")
        graph[as_image] = {"class_type": "MaskToImage", "inputs": {"mask": [placed_id, 0]}}
        graph[blurred] = {
            "class_type": "ImageBlur",
            "inputs": {"image": [as_image, 0], "blur_radius": max(1, min(31, feather)),
                       "sigma": min(10.0, max(1.0, feather / 3))},  # node max 10
        }
        graph[soft] = {"class_type": "ImageToMask",
                       "inputs": {"image": [blurred, 0], "channel": "red"}}
        placed_id = soft
    return placed_id


def with_regional_ipadapter(
    graph: dict,
    *,
    references: list[dict],
    ipadapter: str,
    clip_vision: str,
    combine_embeds: str = "concat",
    embeds_scaling: str = "V only",
    force_regional: bool = False,
    id_prefix: str = "",
) -> dict:
    """Bind several character references to separate regions of one panel.

    ``references`` is a list of ``{"image": <uploaded name>, "mask": [x, y, w, h]}``
    where the mask is a pixel box (from ``layout.regions``), or ``"mask_image"``: an
    uploaded white-on-black mask at canvas size (an artist's placement layer). Each
    reference becomes an ``IPAdapterRegionalConditioning`` (its own mask + weight); the
    params are concatenated via ``IPAdapterCombineParams`` and applied by
    ``IPAdapterFromParams``.

    Falls back to the single-reference path for a one-entry list, unless
    ``force_regional``: in a multi-character panel where only one character has a
    reference, an unmasked IP-Adapter would pull every face towards that one identity.

    ``id_prefix`` namespaces the added node ids, for graphs whose own ids would clash
    (the inpaint graph uses 8-15). The sampler must still be node ``"5"``.
    """
    if not references:
        raise ValueError("regional IP-Adapter needs at least one reference")
    if len(references) == 1 and not force_regional:
        return with_ipadapter(
            graph,
            ref_image=references[0]["image"],
            ipadapter=ipadapter,
            clip_vision=clip_vision,
            weight=references[0].get("weight", 0.75),
        )

    graph = {k: {**v, "inputs": dict(v["inputs"])} for k, v in graph.items()}
    loader, vision = f"{id_prefix}9", f"{id_prefix}10"
    graph[loader] = {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": ipadapter}}
    graph[vision] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": clip_vision}}

    mask_ids: list[str] = []
    params_ids: list[str] = []
    next_id = 12
    for ref in references:
        img_id, mask_id, cond_id = (f"{id_prefix}{next_id + i}" for i in range(3))
        next_id += 3
        graph[img_id] = {"class_type": "LoadImage", "inputs": {"image": ref["image"]}}
        placed_id = _soft_mask(graph, ref, mask_id)
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
        out_id = f"{id_prefix}combine{i}"
        graph[out_id] = {
            "class_type": "IPAdapterCombineParams",
            "inputs": {"params_1": [combined, 0], "params_2": [pid, 0]},
        }
        combined = out_id

    apply_id = f"{id_prefix}apply"
    graph[apply_id] = {
        "class_type": "IPAdapterFromParams",
        "inputs": {
            "model": ["1", 0],
            "ipadapter": [loader, 0],
            "ipadapter_params": [combined, 0],
            "combine_embeds": combine_embeds,
            "embeds_scaling": embeds_scaling,
            "clip_vision": [vision, 0],
        },
    }
    graph["5"]["inputs"]["model"] = [apply_id, 0]
    return graph


def with_masked_prompts(graph: dict, *, regions: list[dict]) -> dict:
    """Give each character their own prompt, bound to their region.

    ``regions`` is a list of ``{"text", "strength"}`` plus the mask fields of
    ``_soft_mask`` (the same box or placement, and feather, as the character's
    reference). Each text is encoded and masked with ``ConditioningSetMask``, then
    combined with the sampler's positive conditioning (the scene: style, head count,
    shot, shared action, setting).

    With every costume in one global prompt, the only thing keeping Yuki's skirt off
    Akira was their separate bands, so the bands couldn't overlap; masked, each
    character's tags act only where that character is.
    """
    if not regions:
        return graph
    graph = {k: {**v, "inputs": dict(v["inputs"])} for k, v in graph.items()}
    combined = graph["5"]["inputs"]["positive"]
    for i, region in enumerate(regions):
        enc, masked, comb = f"mp{i}_text", f"mp{i}_cond", f"mp{i}_combine"
        graph[enc] = {"class_type": "CLIPTextEncode",
                      "inputs": {"text": region["text"], "clip": ["1", 1]}}
        mask = _soft_mask(graph, region, f"mp{i}_mask")
        graph[masked] = {"class_type": "ConditioningSetMask",
                         "inputs": {"conditioning": [enc, 0], "mask": [mask, 0],
                                    "strength": region.get("strength", 1.0),
                                    "set_cond_area": "default"}}
        graph[comb] = {"class_type": "ConditioningCombine",
                       "inputs": {"conditioning_1": combined, "conditioning_2": [masked, 0]}}
        combined = [comb, 0]
    graph["5"]["inputs"]["positive"] = combined
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


def with_controlnet(
    graph: dict,
    *,
    image: str,
    controlnet: str,
    strength: float = 0.7,
    start: float = 0.0,
    end: float = 0.7,
    low_threshold: float = 0.4,
    high_threshold: float = 0.8,
) -> dict:
    """Guide the render with the edge structure of ``image`` (an uploaded take, already
    at canvas size): ``LoadImage -> Canny -> ControlNetApplyAdvanced``.

    It wraps whatever positive/negative conditioning the sampler already uses (plain,
    or a regional chain), so it composes with regional IP-Adapter and placements."""
    graph = {k: {**v, "inputs": dict(v["inputs"])} for k, v in graph.items()}
    graph["cn_image"] = {"class_type": "LoadImage", "inputs": {"image": image}}
    graph["cn_edges"] = {
        "class_type": "Canny",
        "inputs": {"image": ["cn_image", 0], "low_threshold": low_threshold,
                   "high_threshold": high_threshold},
    }
    graph["cn_model"] = {"class_type": "ControlNetLoader",
                         "inputs": {"control_net_name": controlnet}}
    sampler = graph["5"]["inputs"]
    graph["cn_apply"] = {
        "class_type": "ControlNetApplyAdvanced",
        "inputs": {"positive": sampler["positive"], "negative": sampler["negative"],
                   "control_net": ["cn_model", 0], "image": ["cn_edges", 0],
                   "strength": strength, "start_percent": start, "end_percent": end},
    }
    sampler["positive"] = ["cn_apply", 0]
    sampler["negative"] = ["cn_apply", 1]
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


# --- layered renders: background plate first, characters painted into it -------------
#
# Two-shots split into two pictures because each character's region was drawn as a
# picture of its own, background included. With the background rendered first and
# fixed, the characters are painted into one scene. Everything stays in latent space
# (one decode at the end), so the plate isn't redrawn by VAE round trips.
# Prototype, off by default: docs/quality/2026-10-06_layered.md.


def layered_per_character(
    *, ckpt: str, background: str, background_negative: str, negative: str,
    width: int, height: int, seed: int, prefix: str, passes: list[dict],
    ipadapter: str | None = None, clip_vision: str | None = None,
    sampling: Sampling = Sampling(), denoise: float = 1.0,
) -> dict:
    """Background plate, then one inpaint pass per character, in order.

    Each pass is ``{"prompt", "ref" (uploaded reference or None), "weight"}`` plus the
    mask fields of ``_soft_mask``: only that character's prompt and reference are
    active, painting only inside their mask, so nothing of theirs can land on anyone
    else. A later character sees the earlier ones (they're in the latent already)."""
    graph: dict = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "bg_pos": {"class_type": "CLIPTextEncode",
                   "inputs": {"text": background, "clip": ["1", 1]}},
        "bg_neg": {"class_type": "CLIPTextEncode",
                   "inputs": {"text": background_negative, "clip": ["1", 1]}},
    }

    def sampler(model, positive, latent, step_seed, strength=1.0):
        return {"class_type": "KSampler",
                "inputs": {"seed": step_seed, "steps": sampling.steps, "cfg": sampling.cfg,
                           "sampler_name": sampling.sampler,
                           "scheduler": sampling.scheduler, "denoise": strength,
                           "model": model, "positive": positive, "negative": ["3", 0],
                           "latent_image": latent}}

    graph["bg_sampler"] = sampler(["1", 0], ["bg_pos", 0], ["4", 0], seed)
    graph["bg_sampler"]["inputs"]["negative"] = ["bg_neg", 0]
    latent = ["bg_sampler", 0]
    if ipadapter and any(p.get("ref") for p in passes):
        graph["ipa_loader"] = {"class_type": "IPAdapterModelLoader",
                               "inputs": {"ipadapter_file": ipadapter}}
        graph["ipa_vision"] = {"class_type": "CLIPVisionLoader",
                               "inputs": {"clip_name": clip_vision}}
    for i, p in enumerate(passes):
        pos, noise, ks = f"c{i}_pos", f"c{i}_noise", f"c{i}_sampler"
        graph[pos] = {"class_type": "CLIPTextEncode",
                      "inputs": {"text": p["prompt"], "clip": ["1", 1]}}
        mask = _soft_mask(graph, p, f"c{i}_mask")
        graph[noise] = {"class_type": "SetLatentNoiseMask",
                        "inputs": {"samples": latent, "mask": [mask, 0]}}
        model = ["1", 0]
        if p.get("ref") and ipadapter:
            graph[f"c{i}_ref"] = {"class_type": "LoadImage", "inputs": {"image": p["ref"]}}
            graph[f"c{i}_ipa"] = {
                "class_type": "IPAdapterAdvanced",
                "inputs": {"model": ["1", 0], "ipadapter": ["ipa_loader", 0],
                           "image": [f"c{i}_ref", 0], "clip_vision": ["ipa_vision", 0],
                           "weight": p.get("weight", 0.45), "weight_type": "linear",
                           "combine_embeds": "concat", "start_at": 0.0, "end_at": 1.0,
                           "embeds_scaling": "V only", "attn_mask": [mask, 0]}}
            model = [f"c{i}_ipa", 0]
        graph[ks] = sampler(model, [pos, 0], [noise, 0], seed + 1 + i, denoise)
        latent = [ks, 0]
    graph["6"] = {"class_type": "VAEDecode", "inputs": {"samples": latent, "vae": ["1", 2]}}
    graph["7"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix,
                                                        "images": ["6", 0]}}
    return graph


# --- trial renderers with LLM text encoders (compared with the eval) -------------------


def qwen_image21(*, unet: str, clip: str, vae: str, prompt: str, refs: list[str],
                 width: int, height: int, seed: int, prefix: str, steps: int = 30,
                 resolution: int = 1024) -> dict:
    """Qwen-Image 2.1: text plus reference images (``refs``: uploaded names, referred to
    in the prompt as ``<image1>``, ``<image2>``…). As ComfyUI's template: cfg 1, euler,
    simple; the canvas is ours, not the first reference's."""
    graph: dict = {
        "unet": {"class_type": "UNETLoader",
                 "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "cache": {"class_type": "QwenImage21Cache",
                  "inputs": {"model": ["unet", 0], "device": "auto", "dtype": "default"}},
        "clip": {"class_type": "CLIPLoader",
                 "inputs": {"clip_name": clip, "type": "qwen_image", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "encode": {"class_type": "TextEncodeQwenImage21",
                   "inputs": {"clip": ["clip", 0], "prompt": prompt, "negative_prompt": "",
                              "vae": ["vae", 0], "resolution": resolution}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler",
                         "scheduler": "simple", "denoise": 1.0, "model": ["cache", 0],
                         "positive": ["encode", 0], "negative": ["encode", 1],
                         "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["vae", 0]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix,
                                                    "images": ["6", 0]}},
    }
    for i, name in enumerate(refs, start=1):
        graph[f"ref{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        graph["encode"]["inputs"][f"images.image_{i}"] = [f"ref{i}", 0]
    return graph


def z_image(*, unet: str, clip: str, vae: str, prompt: str, negative: str, width: int,
            height: int, seed: int, prefix: str, steps: int = 30, cfg: float = 4.0,
            sampler: str = "euler_ancestral", scheduler: str = "beta",
            shift: float = 3.0) -> dict:
    """Z-Image (Z-Anime Base): text only. Settings from the Z-Anime card (28-50 steps,
    cfg 3-5, euler_ancestral/beta), shift as in ComfyUI's Z-Image template."""
    return {
        "unet": {"class_type": "UNETLoader",
                 "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "shift": {"class_type": "ModelSamplingAuraFlow",
                  "inputs": {"model": ["unet", 0], "shift": shift}},
        "clip": {"class_type": "CLIPLoader",
                 "inputs": {"clip_name": clip, "type": "lumina2", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["clip", 0]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["clip", 0]}},
        "4": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler,
                         "scheduler": scheduler, "denoise": 1.0, "model": ["shift", 0],
                         "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["vae", 0]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix,
                                                    "images": ["6", 0]}},
    }


def with_init_image(graph: dict, *, image: str, width: int, height: int,
                    denoise: float) -> dict:
    """Start the sampler (node "5") from ``image`` instead of noise: img2img at
    ``denoise``. For a two-stage render (another model composes, this one restyles),
    every other hook on node "5" (references, masked prompts) still applies."""
    graph = {k: {**v, "inputs": dict(v["inputs"])} for k, v in graph.items()}
    graph["init_image"] = {"class_type": "LoadImage", "inputs": {"image": image}}
    graph["init_scaled"] = {"class_type": "ImageScale",
                            "inputs": {"image": ["init_image", 0], "upscale_method": "lanczos",
                                       "width": width, "height": height, "crop": "center"}}
    graph["4"] = {"class_type": "VAEEncode",
                  "inputs": {"pixels": ["init_scaled", 0], "vae": ["1", 2]}}
    graph["5"]["inputs"]["denoise"] = denoise
    return graph
