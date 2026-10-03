"""Generate character design sheets and register them as reference versions.

Ties together: appearance traits -> design prompt -> ComfyUI render -> registry.
The rendered sheet becomes the character's ``base`` version (the identity anchor
reused by every later panel via IP-Adapter).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from manganation.characters.design import DesignPrompt, design_prompt_for
from manganation.characters.registry import CharacterRegistry
from manganation.characters.schema import Character, CharacterVersion, VersionKind
from manganation.config import Settings, load_models, load_settings
from manganation.render.comfy_client import ComfyClient
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
) -> DesignResult:
    """Render a design sheet for ``character`` and register it as a version."""
    settings = settings or load_settings()
    client = client or ComfyClient(settings.comfyui.base_url)
    if not client.is_up():
        raise RuntimeError(f"ComfyUI not reachable at {settings.comfyui.base_url}")

    prompt = prompt or design_prompt_for(character)
    seed = seed if seed is not None else random.randint(0, 2**31 - 1)

    graph = txt2img(
        ckpt=_checkpoint_id(settings),
        prompt=prompt.positive,
        negative=prompt.negative,
        width=width,
        height=height,
        seed=seed,
        prefix=f"design_{character.name.replace(' ', '_')}",
        sampling=Sampling(steps=steps, cfg=cfg),
    )
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
