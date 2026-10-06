"""Masked repaint of a region of a panel (GIMP "Inpaint Selection").

The GIMP plug-in exports two images: the **init** (the current layer / panel take) and
a **mask** (the selection). This module uploads both, runs the ComfyUI inpaint graph so
only the masked region is re-synthesised from a short prompt, and saves the result as a
new take.

**Crop-and-stitch.** SDXL paints well at ~1 MP. Painting a small selection inside a
whole take (often a ~4 MP hi-res) gives incoherent patches, so only a crop around the
mask, with context, is inpainted: resized to ~1 MP (small selections are upscaled,
huge takes downscaled), then resized back and stitched into the full-resolution take
through a grown, softened copy of the mask. Outside that mask the original pixels are
kept exactly, guaranteed here rather than by the graph (a bare VAE round trip would
redraw line work across the whole panel).

Output: ``panels/{seq:03d}_inpaint.png`` (plus ``_takeNN`` when repeated) with its own
sidecar, so the original render and the artist's iterations all sit side by side.
"""

from __future__ import annotations

import io
import json
import random
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image, ImageFilter

from manganation.config import load_models, load_settings
from manganation.render import graphs
from manganation.render.comfy_client import ComfyClient


class InpaintError(RuntimeError):
    pass


@dataclass
class InpaintResult:
    path: str
    seq: int | None
    source: str
    mask: str
    prompt: str
    width: int
    height: int
    denoise: float
    grow_mask_by: int
    seed: int
    positive: str = ""  # the full prompt sent (style prefix + the artist's prompt)
    crop: list[int] = field(default_factory=list)  # [x0, y0, x1, y1] in source px
    characters: dict[str, str] = field(default_factory=dict)  # name -> version used
    work_size: list[int] = field(default_factory=list)  # [w, h] the crop was painted at


def resolve_characters(
    identity: Path, characters: list[dict] | None,
) -> tuple[list[str], dict[str, Path], dict[str, str]]:
    """Traits for the characters named in a patch, and their reference images.
    -> (prompt tags, {name: reference} for those who have one, {name: version used}),
    names in the order given.

    With several characters the tags are grouped like a panel prompt: one head count
    ("1boy, 1girl"), then each character's traits together, so they don't blend.

    Unknown characters or versions fail, listing what exists: a typo must not quietly
    produce an off-model face."""
    if not characters:
        return [], {}, {}
    from manganation.characters.registry import CharacterRegistry
    from manganation.render.panel import COUNT_TAG, head_count

    reg = CharacterRegistry.from_path(identity)
    known = ", ".join(c.name for c in reg.cast.characters) or "none"
    traits: dict[str, list[str]] = {}
    refs: dict[str, Path] = {}
    used: dict[str, str] = {}
    for c in characters:
        name, version = c["name"], c.get("version")
        character = reg.get(name)
        if character is None:
            raise InpaintError(f"no character {name!r} in this project (known: {known})")
        # Appearance only: the artist's prompt decides expression and pose. (Stored as
        # identity, Yuki's "wide toothed grin" beat "surprised face, open mouth".)
        traits[character.name] = character.appearance.appearance_tags()
        ref = reg.reference_path(character.name, version)
        if version and (ref is None or not ref.exists()):
            versions = ", ".join(v.id for v in character.versions) or "none"
            raise InpaintError(f"{character.name} has no reference version {version!r} "
                               f"(known: {versions})")
        if ref is not None and ref.exists():
            refs[character.name] = ref
            used[character.name] = version or character.default_version or "active"
        else:
            used[character.name] = "traits only"
    counts = head_count(list(traits), traits) if len(traits) > 1 else []
    tags = list(counts)
    for group in traits.values():
        if counts and group and COUNT_TAG.match(group[0].strip()):
            group = group[1:]
        tags += [t for t in group if t not in tags]
    return tags, refs, used


def normalized_mask(path: Path) -> Image.Image:
    """The mask as an opaque greyscale image, **white = repaint**.

    Accepts a transparent PNG (opaque/selected = repaint, e.g. the GIMP plug-in's
    selection export) or an opaque black/white one. It's normalised here because
    ComfyUI's ``LoadImageMask`` reads the alpha channel *inverted* (``1 - alpha``:
    transparent = masked), which repainted everything except the selection."""
    with Image.open(path) as im:
        if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
            return im.convert("RGBA").getchannel("A")
        return im.convert("L")


def crop_box(
    mask: Image.Image, *, context: float = 0.5, min_side: int = 256, grow: int = 0,
    max_aspect: float = 2.0,
) -> tuple[int, int, int, int] | None:
    """The region to inpaint: the mask's bounding box plus context, inside the image.

    Each side gains ``context`` x the mask's larger dimension (plus the blend growth);
    the box is at least ``min_side`` per side and no thinner than ``max_aspect``, so the
    model sees enough surroundings and the ~1 MP resize doesn't distort it. ``None`` if
    the mask is empty."""
    bbox = mask.point(lambda v: 255 if v > 0 else 0).getbbox()
    if bbox is None:
        return None
    iw, ih = mask.size
    x0, y0, x1, y1 = bbox
    pad = int(max(x1 - x0, y1 - y0) * context) + 2 * grow
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad

    def widen(lo: int, hi: int, want: int, limit: int) -> tuple[int, int]:
        want = min(max(want, hi - lo), limit)
        lo -= (want - (hi - lo)) // 2
        lo = max(0, min(lo, limit - want))
        return lo, lo + want

    w, h = x1 - x0, y1 - y0
    w_want = max(w, min_side, int(h / max_aspect))
    h_want = max(h, min_side, int(w / max_aspect))
    x0, x1 = widen(max(0, x0), min(iw, x1), w_want, iw)
    y0, y1 = widen(max(0, y0), min(ih, y1), h_want, ih)
    return x0, y0, x1, y1


def blend_mask(mask: Image.Image, grow: int) -> Image.Image:
    """Grown, softened mask for stitching: covers the repaint plus its blend seam."""
    m = mask
    if grow > 0:
        m = m.filter(ImageFilter.MaxFilter(2 * grow + 1))
    return m.filter(ImageFilter.GaussianBlur(max(1.0, grow / 2)))


def _newest_panel(project: Path, seq: int) -> Path | None:
    """Newest take for a panel: base, then _takeNN, then a prior _inpaint last."""
    panels = project / "panels"
    candidates = [panels / f"{seq:03d}.png", panels / f"{seq:03d}_inpaint.png"]
    candidates += sorted(panels.glob(f"{seq:03d}_take*.png"))
    existing = [p for p in candidates if p.exists()]
    return existing[-1] if existing else None


def output_path(project: Path, seq: int) -> Path:
    """``{seq}_inpaint.png``, or ``{seq}_inpaint_takeNN.png`` if that exists."""
    panels = project / "panels"
    first = panels / f"{seq:03d}_inpaint.png"
    if not first.exists():
        return first
    take = 2
    while (panels / f"{seq:03d}_inpaint_take{take:02d}.png").exists():
        take += 1
    return panels / f"{seq:03d}_inpaint_take{take:02d}.png"


def _resolve(path: str | Path, project: Path, what: str) -> Path:
    p = Path(path)
    p = p if p.is_absolute() else (project / p)
    p = p.resolve()
    if project.resolve() not in p.parents:
        raise InpaintError(f"{what} must be inside the project ({project})")
    if not p.is_file():
        raise InpaintError(f"{what} not found: {p}")
    return p


def inpaint_panel(
    project: Path, seq: int, *, mask: str | Path, prompt: str,
    source: str | Path | None = None, denoise: float | None = None,
    grow_mask_by: int | None = None, seed: int | None = None,
    negative: str | None = None, client: ComfyClient | None = None,
    characters: list[dict] | None = None, character_weight: float | None = None,
    reading_order: str = "rtl",
) -> InpaintResult:
    """Repaint the masked region of panel ``seq`` (or an explicit ``source``) from
    ``prompt``. ``mask`` and ``source`` must live inside ``project``. Legacy form."""
    project = Path(project)
    if source is None:
        src = _newest_panel(project, seq)
        if src is None:
            raise InpaintError(f"no rendered image for panel {seq} in {project / 'panels'}")
    else:
        src = _resolve(source, project, "source")
    mask_path = _resolve(mask, project, "mask")
    result = inpaint_image(
        src, mask_path, prompt=prompt, out=output_path(project, seq), denoise=denoise,
        grow_mask_by=grow_mask_by, seed=seed, negative=negative, client=client,
        tag=f"{project.name}_{seq:03d}", characters=characters, identity=project,
        character_weight=character_weight, reading_order=reading_order,
    )
    result.seq = seq
    Path(result.path).with_suffix(".json").write_text(json.dumps(asdict(result), indent=2))
    return result


def inpaint_inline(
    project_id: str, source: Path, mask: Path, *, prompt: str,
    denoise: float | None = None, grow_mask_by: int | None = None, seed: int | None = None,
    client: ComfyClient | None = None, outputs: Path | None = None,
    characters: list[dict] | None = None, identity: Path | None = None,
    character_weight: float | None = None, reading_order: str = "rtl",
) -> InpaintResult:
    """Container form (docs/engine-api.md): ``source`` is the exact take, ``mask`` the
    selection export. Output goes to the engine's ``outputs/<project>/`` cache; the
    plug-in records it as a take (kind ``inpaint``, parent = the source take)."""
    import uuid

    from manganation.config import outputs_root

    source, mask = Path(source), Path(mask)
    for path, what in ((source, "source"), (mask, "mask")):
        if not path.is_file():
            raise InpaintError(f"{what} not found: {path}")
    out_dir = (outputs if outputs is not None else outputs_root()) / project_id
    out = out_dir / f"{source.stem}-inpaint-{uuid.uuid4().hex[:12]}.png"
    if characters and identity is None:
        from manganation.identity import identity_root

        identity = identity_root(project_id)
    result = inpaint_image(source, mask, prompt=prompt, out=out, denoise=denoise,
                           grow_mask_by=grow_mask_by, seed=seed, client=client,
                           tag=project_id, characters=characters, identity=identity,
                           character_weight=character_weight, reading_order=reading_order)
    out.with_suffix(".json").write_text(json.dumps(asdict(result), indent=2))
    return result


def inpaint_image(
    src: Path, mask_path: Path, *, prompt: str, out: Path, denoise: float | None = None,
    grow_mask_by: int | None = None, seed: int | None = None, negative: str | None = None,
    client: ComfyClient | None = None, tag: str = "panel",
    characters: list[dict] | None = None, identity: Path | None = None,
    character_weight: float | None = None, reading_order: str = "rtl",
    checkpoint: str | None = None,
) -> InpaintResult:
    """Crop-and-stitch repaint of ``src`` inside ``mask_path``, saved to ``out``.
    ``checkpoint`` (a file name) paints with another SDXL model than the primary one.

    ``characters`` (``[{"name", "version"}]``, from the ``identity`` registry folder)
    are the people in the patch: their traits join the prompt, and IP-Adapter guides
    the patch with their references so faces stay on-model. One character's reference
    covers the patch; with several, each is masked to their band of the crop in
    ``reading_order`` (as in a panel render), so no one reference pulls every face."""
    settings = load_settings()
    models = load_models()
    d = settings.defaults.inpaint
    denoise = d.denoise if denoise is None else denoise
    grow_mask_by = d.grow_mask_by if grow_mask_by is None else grow_mask_by

    if not (0.0 < denoise <= 1.0):
        raise InpaintError("denoise must be in (0, 1]")

    with Image.open(src) as im:
        source_im = im.convert("RGB")
    width, height = source_im.size
    with Image.open(mask_path) as im:
        mw, mh = im.size
    if (mw, mh) != (width, height):
        raise InpaintError(
            f"mask size {mw}x{mh} must match the source {width}x{height}"
        )
    mask_im = normalized_mask(mask_path)
    box = crop_box(mask_im, context=d.context, min_side=d.min_crop, grow=grow_mask_by)
    if box is None:
        raise InpaintError("the mask is empty: nothing selected to repaint")

    client = client or ComfyClient(settings.comfyui.base_url)
    if not client.is_up():
        raise InpaintError(f"ComfyUI is not reachable at {settings.comfyui.base_url}")

    from manganation.render.panel import fit_resolution

    crop_w, crop_h = box[2] - box[0], box[3] - box[1]
    work_w, work_h = fit_resolution(crop_w, crop_h)
    scale = ((work_w * work_h) / (crop_w * crop_h)) ** 0.5

    neg = negative if negative is not None else _style_negative()
    tags, refs, used = resolve_characters(identity, characters) if characters else ([], {}, {})
    # The artist's prompt leads: it's what to paint. Traits follow to keep it on-model;
    # placed first they win, e.g. a "wide toothed grin" trait overrode "open mouth".
    positive = _with_style(", ".join([prompt.strip(), *tags]) if tags else prompt)
    seed = seed if seed is not None else random.randrange(2**32)
    with tempfile.TemporaryDirectory() as tmp:
        stem = f"{tag}_inpaint"
        crop_path, cmask_path = Path(tmp) / f"{stem}_crop.png", Path(tmp) / f"{stem}_mask.png"
        source_im.crop(box).resize((work_w, work_h), Image.LANCZOS).save(crop_path)
        mask_im.crop(box).resize((work_w, work_h), Image.LANCZOS).save(cmask_path)
        source_up = client.upload_image(str(crop_path))
        mask_up = client.upload_image(str(cmask_path))
    ipa = None
    if refs:
        from manganation.render.panel import ipadapter_files

        ipa_file, clip_file = ipadapter_files(models, settings.defaults.ipadapter.adapter)
    if len(refs) == 1 and len(used) == 1:
        ipa = {"ref_image": client.upload_image(str(next(iter(refs.values()))))["name"],
               "ipadapter_file": ipa_file, "clip_name": clip_file,
               "weight": d.ipadapter_weight if character_weight is None else character_weight}
    graph = graphs.inpaint(
        ckpt=checkpoint or models["checkpoints"]["primary"]["id"],
        image=source_up["name"], mask=mask_up["name"],
        prompt=positive, negative=neg, seed=seed,
        prefix=f"imanganation_{tag}_inpaint",
        denoise=denoise, grow_mask_by=max(1, round(grow_mask_by * scale)),
        mask_channel="red",  # normalised: opaque greyscale, white = repaint
        sampling=graphs.Sampling(d.steps, d.cfg), ipadapter=ipa,
    )
    if refs and ipa is None:
        graph = graphs.with_regional_ipadapter(
            graph, references=_regional_references(
                client, list(used), refs, work_w, work_h, reading_order,
                character_weight),
            ipadapter=ipa_file, clip_vision=clip_file, force_regional=True,
            id_prefix="ipa_")
    blobs = client.run(graph)
    if not blobs:
        raise InpaintError("ComfyUI returned no image")

    # Stitch: patch back at crop size, pasted only through the (grown, soft) mask.
    patch = Image.open(io.BytesIO(blobs[0])).convert("RGB").resize((crop_w, crop_h),
                                                                   Image.LANCZOS)
    stitched = source_im.copy()
    stitched.paste(patch, box[:2], blend_mask(mask_im.crop(box), grow_mask_by))

    out.parent.mkdir(parents=True, exist_ok=True)
    stitched.save(out)
    return InpaintResult(
        path=str(out), seq=None, source=str(src), mask=str(mask_path), prompt=prompt,
        width=width, height=height, denoise=denoise, grow_mask_by=grow_mask_by, seed=seed,
        positive=positive, crop=list(box), work_size=[work_w, work_h], characters=used,
    )


def _regional_references(
    client, names: list[str], refs: dict[str, Path], width: int, height: int,
    reading_order: str, weight: float | None,
) -> list[dict]:
    """Each referenced character's reference, masked to their band of the work canvas.
    Bands are laid out over *every* named character, so one who has no reference
    still keeps their part of the crop free of the others' faces."""
    from manganation.layout.regions import assign_regions
    from manganation.script.schema import ReadingOrder

    ipa = load_settings().defaults.ipadapter
    regions = assign_regions(len(names), order=ReadingOrder(reading_order), margin=0.05)
    return [{"image": client.upload_image(str(refs[name]))["name"],
             "mask": list(region.scaled(width, height)), "canvas_w": width,
             "canvas_h": height, "weight": ipa.weight_regional if weight is None else weight,
             "feather": ipa.feather}
            for name, region in zip(names, regions, strict=True) if name in refs]


def _with_style(prompt: str) -> str:
    """The patch is drawn into a panel rendered with the colour style, so give it the
    same style prefix; a bare "red apple" drifts from the surrounding art."""
    from manganation.render.panel import load_style

    prefix = load_style().get("prompt_prefix", "").strip().rstrip(",")
    return f"{prefix}, {prompt.strip()}" if prefix else prompt.strip()


def _style_negative() -> str:
    from manganation.render.panel import load_style

    return load_style().get("negative", "")
