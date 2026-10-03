"""Generate a synthetic per-character training set from the design sheet (Phase 6b).

The design sheet is the identity seed; each planned variant (see
:mod:`manganation.characters.dataset`) is rendered from it with img2img at a
moderate denoise, so the *person* stays fixed while pose/expression/background vary.
Output is a kohya/sd-scripts-style folder of image + caption pairs plus our own
``dataset.json`` manifest.

Nothing here trains a LoRA — this is the dataset-building half of the spike. The
goal is to measure how much usable identity variance can be produced from a single
generated reference.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

from manganation.characters.dataset import VariantSpec, variant_specs
from manganation.characters.registry import CharacterRegistry, slugify
from manganation.characters.schema import Character
from manganation.config import Settings, load_models, load_settings
from manganation.render.comfy_client import ComfyClient
from manganation.render.graphs import Sampling, img2img


@dataclass
class DatasetItem:
    index: int
    image: str  # path relative to the dataset dir
    caption: str
    positive: str
    negative: str
    note: str
    seed: int
    denoise: float


@dataclass
class DatasetResult:
    character: str
    root: Path
    seed_image: Path
    count: int
    items: list[DatasetItem] = field(default_factory=list)


def dataset_dir(project_root: Path, character: str) -> Path:
    return Path(project_root) / "datasets" / slugify(character)


def _checkpoint_id() -> str:
    return load_models()["checkpoints"]["primary"]["id"]


def build_dataset(
    character: Character,
    *,
    project_root: Path,
    seed_image: Path | None = None,
    count: int | None = None,
    denoise: float | None = None,
    base_seed: int | None = None,
    settings: Settings | None = None,
    client: ComfyClient | None = None,
    specs: list[VariantSpec] | None = None,
) -> DatasetResult:
    """Render a varied training set for ``character`` from its seed image.

    ``seed_image`` defaults to the registry's active reference (the design sheet).
    Images land in ``projects/<name>/datasets/<slug>/NN.png`` with ``NN.txt`` captions
    and a ``dataset.json`` manifest.
    """
    settings = settings or load_settings()
    d = settings.defaults.dataset
    count = d.count if count is None else count
    denoise = d.denoise if denoise is None else denoise

    if seed_image is None:
        registry = CharacterRegistry.from_path(project_root)
        seed_image = registry.reference_path(character.name)
    if seed_image is None or not Path(seed_image).exists():
        raise ValueError(f"no seed/design image for {character.name!r}")

    client = client or ComfyClient(settings.comfyui.base_url)
    if not client.is_up():
        raise RuntimeError(f"ComfyUI not reachable at {settings.comfyui.base_url}")

    specs = specs if specs is not None else variant_specs(character, count)
    out_dir = dataset_dir(project_root, character.name)
    out_dir.mkdir(parents=True, exist_ok=True)

    uploaded = client.upload_image(str(seed_image))
    base_seed = base_seed if base_seed is not None else random.randint(0, 2**31 - 1)
    ckpt = _checkpoint_id()

    items: list[DatasetItem] = []
    for spec in specs:
        seed = base_seed + spec.index
        graph = img2img(
            ckpt=ckpt, image=uploaded["name"], prompt=spec.positive,
            negative=spec.negative, seed=seed,
            prefix=f"dataset_{slugify(character.name)}_{spec.index:02d}",
            denoise=denoise,
            sampling=Sampling(steps=d.steps, cfg=d.cfg),
        )
        blobs = client.run(graph)
        if not blobs:
            raise RuntimeError(f"ComfyUI returned no image for variant {spec.index}")
        name = f"{spec.index:02d}"
        (out_dir / f"{name}.png").write_bytes(blobs[0])
        (out_dir / f"{name}.txt").write_text(spec.caption + "\n")
        items.append(DatasetItem(
            index=spec.index, image=f"{name}.png", caption=spec.caption,
            positive=spec.positive, negative=spec.negative, note=spec.note,
            seed=seed, denoise=denoise,
        ))

    result = DatasetResult(
        character=character.name, root=out_dir, seed_image=Path(seed_image),
        count=len(items), items=items,
    )
    (out_dir / "dataset.json").write_text(json.dumps({
        "character": character.name,
        "seed_image": str(seed_image),
        "count": len(items),
        "denoise": denoise,
        "base_seed": base_seed,
        "items": [asdict(it) for it in items],
    }, indent=2))
    return result
