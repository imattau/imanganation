"""Generate character design sheets and register them as reference versions.

Ties together: appearance traits -> design prompt -> ComfyUI render -> registry.
The rendered sheet becomes the character's ``base`` version (the identity anchor
reused by every later panel via IP-Adapter).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from manganation.characters.design import (
    DesignPrompt,
    build_prose_design_prompt,
    design_prompt_for,
)
from manganation.characters.registry import CharacterRegistry
from manganation.characters.schema import Character, CharacterVersion, VersionKind
from manganation.config import Settings, load_models, load_settings
from manganation.render.comfy_client import ComfyClient
from manganation.render import graphs
from manganation.render.graphs import Sampling, txt2img


@dataclass
class DesignResult:
    character: str
    version_id: str
    image: Path
    seed: int
    prompt: str


def _checkpoint_id(settings: Settings) -> str:
    models = load_models()
    return models["checkpoints"]["primary"]["id"]


def design_engine_prompt(character: Character, engine: str,
                         extra: list[str] | None = None, style: str | None = None) -> DesignPrompt:
    """The design prompt in the engine's own language: tags for SDXL, prose otherwise."""
    if engine == "sdxl":
        return design_prompt_for(character, extra=extra)
    return build_prose_design_prompt(character.appearance,
                                     **({"style": style} if style else {}))


def trial_text_graph(engine: str, prompt: str, negative: str, *, width: int, height: int,
                     seed: int, prefix: str) -> dict:
    """A text-only graph for a trial engine (Qwen-Image 2.1 or Z-Anime), no references."""
    from manganation.render.panel import trial_files

    files = trial_files(load_models(), engine)
    if engine == "qwen_image_21":
        return graphs.qwen_image21(unet=files["model"], clip=files["text_encoder"],
                                   vae=files["vae"], prompt=prompt, refs=[], width=width,
                                   height=height, seed=seed, prefix=prefix)
    return graphs.z_image(unet=files["model"], clip=files["text_encoder"], vae=files["vae"],
                          prompt=prompt, negative=negative, width=width, height=height,
                          seed=seed, prefix=prefix)


def generate_design(
    character: Character,
    registry: CharacterRegistry,
    *,
    version_id: str = "base",
    kind: VersionKind = VersionKind.BASE,
    seed: int | None = None,
    width: int = 1024,  # square: CLIP vision centre-crops references to a square
    height: int = 1024,
    steps: int = 28,
    cfg: float = 6.0,
    settings: Settings | None = None,
    client: ComfyClient | None = None,
    prompt: DesignPrompt | None = None,
    replace: bool = False,
    engine: str | None = None,
) -> DesignResult:
    """Render a design sheet for ``character`` and register it as a version."""
    settings = settings or load_settings()
    client = client or ComfyClient(settings.comfyui.base_url)
    if not client.is_up():
        raise RuntimeError(f"ComfyUI not reachable at {settings.comfyui.base_url}")

    engine = engine or settings.defaults.renderer.engine
    prompt = prompt or design_engine_prompt(character, engine)
    seed = seed if seed is not None else random.randint(0, 2**31 - 1)
    prefix = f"design_{character.name.replace(' ', '_')}"

    if engine == "sdxl":
        graph = txt2img(
            ckpt=_checkpoint_id(settings),
            prompt=prompt.positive,
            negative=prompt.negative,
            width=width,
            height=height,
            seed=seed,
            prefix=prefix,
            sampling=Sampling(steps=steps, cfg=cfg),
        )
    else:
        graph = trial_text_graph(engine, prompt.positive, prompt.negative, width=width,
                                 height=height, seed=seed, prefix=prefix)
    blobs = client.run(graph)
    if not blobs:
        raise RuntimeError("ComfyUI returned no image")

    cdir = registry.ensure_dir(character.name)
    dest = cdir / f"{version_id}.png"
    dest.write_bytes(blobs[0])

    registry.add_version(
        character.name,
        CharacterVersion(
            id=version_id,
            kind=kind,
            image=str(dest.relative_to(registry.root)),
            prompt=prompt.positive,
            seed=seed,
            note=f"auto design sheet ({prompt.tags[0]})",
        ),
        make_default=(kind == VersionKind.BASE),
        replace=replace,
    )
    return DesignResult(
        character=character.name,
        version_id=version_id,
        image=dest,
        seed=seed,
        prompt=prompt.positive,
    )
