"""Render one panel of a project at a caller-chosen frame size.

The caller (the GIMP plug-in, or the CLI) supplies the target frame in pixels; the
renderer picks the nearest SDXL-friendly resolution with the same aspect ratio, so
the result drops into the frame with minimal cropping. Output goes to
``panels/{seq:03d}.png`` (retakes: ``{seq:03d}_take02.png``…), where ``seq`` is the
panel's 1-based position in panels.json.
"""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from manganation.config import CONFIG_DIR, load_models, load_settings
from manganation.layout.regions import assign_regions, regions_for
from manganation.render import graphs
from manganation.render.comfy_client import ComfyClient
from manganation.script.schema import PanelSpec, ReadingOrder, Script

SDXL_PIXELS = 1024 * 1024
MAX_ASPECT = 3.0  # beyond this SDXL composes badly; the frame mask crops the rest


class RenderError(RuntimeError):
    pass


@dataclass
class RenderResult:
    path: str
    seq: int | None
    seed: int
    width: int
    height: int
    prompt: str
    reference: str | None = None
    panel_id: str | None = None  # container panel id, for inline renders
    references: dict[str, str] = field(default_factory=dict)  # character -> version used
    placements: dict[str, str] = field(default_factory=dict)  # character -> mask file used
    guide: str | None = None  # the take whose composition this render kept (ControlNet)


def fit_resolution(
    frame_w: float, frame_h: float, *, pixels: int = SDXL_PIXELS, multiple: int = 64,
) -> tuple[int, int]:
    """~1 MP size, in multiples of 64, closest to the frame's aspect ratio.

    Rounding width and height independently drifts the ratio (1.65 -> 1.75), which
    the frame mask then crops, so search the grid for the best ratio instead.
    """
    if frame_w <= 0 or frame_h <= 0:
        raise ValueError("frame must have a positive size")
    aspect = min(max(frame_w / frame_h, 1 / MAX_ASPECT), MAX_ASPECT)
    best: tuple[float, int, int] | None = None
    for w in range(multiple, 4096 + 1, multiple):
        h = max(multiple, round(w / aspect / multiple) * multiple)
        if not 0.85 * pixels <= w * h <= 1.15 * pixels:
            continue
        score = abs(w / h - aspect) / aspect + abs(w * h - pixels) / pixels * 0.1
        if best is None or score < best[0]:
            best = (score, w, h)
    assert best is not None
    return best[1], best[2]


def load_style() -> dict:
    """The single colour style. B&W/screentone is the artist's job in GIMP, so a
    panel's ``color_mode`` is recorded but never changes the render."""
    return yaml.safe_load((CONFIG_DIR / "styles" / "default_color.yaml").read_text())


def build_prompt(
    spec: PanelSpec, style: dict, character_tags: dict[str, list[str]] | None = None,
) -> str:
    parts = [style.get("prompt_prefix", "").strip().rstrip(",")]
    # Without a head count, wide frames tempt SDXL to add a second copy of the figure.
    if len(spec.characters) == 1:
        parts.append("solo")
    # Names mean nothing to SDXL; their registered appearance tags do.
    for name in spec.characters:
        parts += (character_tags or {}).get(name, [])
    parts += [spec.camera, spec.action, spec.scene_heading or spec.location]
    parts += [f"{who} {face}" for who, face in spec.expressions.items()]
    if spec.flashback:
        parts.append("flashback, soft focus")
    return ", ".join(p.strip() for p in parts if p and p.strip())


def build_negative(spec: PanelSpec, style: dict) -> str:
    negative = style.get("negative", "")
    if len(spec.characters) == 1:
        negative += ", multiple views, 2boys, 2girls, multiple boys, multiple girls, clone"
    return negative


def reference_for(project: Path, name: str, spec: PanelSpec | None = None) -> Path | None:
    """Resolve one character's reference image (registry, override, then legacy flat)."""
    # 1. Explicit per-panel override.
    if spec is not None and name in spec.refs:
        ref = Path(spec.refs[name])
        ref = ref if ref.is_absolute() else project / ref
        if ref.exists():
            return ref

    # 2. Character registry (structured img-memory).
    try:
        from manganation.characters.registry import CharacterRegistry

        ref = CharacterRegistry.from_path(project).reference_path(name)
        if ref is not None and ref.exists():
            return ref
    except Exception:  # noqa: BLE001 - registry is best-effort here
        pass

    # 3. Legacy flat layout.
    for ext in ("png", "webp", "jpg"):
        ref = project / "characters" / f"{name.lower()}.{ext}"
        if ref.exists():
            return ref
    return None


def find_reference(project: Path, spec: PanelSpec) -> Path | None:
    """Reference for a single-character panel (multi-character uses find_references)."""
    if len(spec.characters) != 1:
        return None
    return reference_for(project, spec.characters[0], spec)


def find_references(project: Path, spec: PanelSpec) -> dict[str, Path]:
    """Every panel character that has a usable reference image, in script order."""
    out: dict[str, Path] = {}
    for name in spec.characters:
        ref = reference_for(project, name, spec)
        if ref is not None:
            out[name] = ref
    return out


def character_tags(
    project: Path, names: list[str], expressions: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Tags per character from the project's registry (best-effort): their appearance,
    plus their default expression *unless the panel gives one* (the panel wins; Yuki
    grins by default, but "surprised" means surprised). Mannerisms are left out: the
    panel's action decides the pose."""
    try:
        from manganation.characters.registry import CharacterRegistry

        registry = CharacterRegistry.from_path(project)
        out = {}
        for name in names:
            character = registry.get(name)
            if character is not None:
                out[name] = character.appearance.prompt_tags(
                    expression=name not in (expressions or {}))
        return out
    except Exception:  # noqa: BLE001 - no registry yet just means bare names
        return {}


def ipadapter_files(models: dict, adapter: str) -> tuple[str, str]:
    """(adapter file, CLIP-vision file) for a models.yaml ipadapter role.

    The encoder must be the one the adapter was trained with (ViT-H vs ViT-bigG), so
    it is looked up from the adapter's ``encoder`` key rather than chosen separately.
    """
    roles = models["ipadapter"]
    if adapter not in roles:
        raise RenderError(f"unknown IP-Adapter {adapter!r}; models.yaml has {sorted(roles)}")
    entry = roles[adapter]
    return entry["id"], roles[entry.get("encoder", "clip_vision")]["id"]


def output_path(project: Path, seq: int) -> Path:
    panels = project / "panels"
    first = panels / f"{seq:03d}.png"
    if not first.exists():
        return first
    take = 2
    while (panels / f"{seq:03d}_take{take:02d}.png").exists():
        take += 1
    return panels / f"{seq:03d}_take{take:02d}.png"


def render_panel(
    project: Path, seq: int, frame_w: float, frame_h: float, *,
    seed: int | None = None, client: ComfyClient | None = None,
    placements: dict[str, Path] | None = None, guide: Path | None = None,
    guide_strength: float | None = None,
) -> RenderResult:
    """Legacy form: panel ``seq`` of ``project/panels.json``, written to its panels/."""
    project = Path(project)
    try:
        script = Script.from_json((project / "panels.json").read_text())
    except (OSError, ValueError) as exc:
        raise RenderError(f"cannot read {project / 'panels.json'}: {exc}") from exc
    if not 1 <= seq <= len(script.panels):
        raise RenderError(f"panel {seq} out of range (script has {len(script.panels)})")
    return _render(
        script.panels[seq - 1], project, frame_w, frame_h,
        reading_order=script.reading_order, seed=seed, client=client,
        out=output_path(project, seq), seq=seq, placements=placements,
        guide=guide, guide_strength=guide_strength,
    )


def render_inline(
    panel: dict, project_id: str, frame_w: float, frame_h: float, *,
    reading_order: str = "rtl", seed: int | None = None,
    client: ComfyClient | None = None, identity: Path | None = None,
    outputs: Path | None = None, placements: dict[str, Path] | None = None,
    guide: Path | None = None, guide_strength: float | None = None,
) -> RenderResult:
    """Container form: the panel spec travels in the request (docs/engine-api.md).

    Character identity comes from the project's identity store (``identity.py``); each
    character's ``version`` selects that reference version. The image goes to the
    engine's own ``outputs/<project id>/`` cache. The engine never writes into the
    container: the plug-in copies the result in as a take."""
    from manganation.config import REPO_ROOT
    from manganation.identity import identity_root
    from manganation.project_container import panel_to_spec

    spec, versions = panel_to_spec(panel)
    root = identity if identity is not None else identity_root(project_id)
    used: dict[str, str] = {}
    if versions:
        from manganation.characters.registry import CharacterRegistry

        reg = CharacterRegistry.from_path(root)
        for name, version in versions.items():
            ref = reg.reference_path(name, version)
            if ref is None or not ref.exists():
                character = reg.get(name)
                known = [v.id for v in character.versions] if character else []
                raise RenderError(f"{name} has no reference version {version!r} "
                                  f"(known: {', '.join(known) or 'none'})")
            spec.refs[name] = str(ref)  # the per-panel override path in reference_for
            used[name] = version
    out_dir = (outputs if outputs is not None else REPO_ROOT / "outputs") / project_id
    out = out_dir / f"{panel.get('id', 'panel')}-{uuid.uuid4().hex[:12]}.png"
    result = _render(spec, root, frame_w, frame_h, reading_order=ReadingOrder(reading_order),
                     seed=seed, client=client, out=out, seq=None, placements=placements,
                     guide=guide, guide_strength=guide_strength)
    result.panel_id = panel.get("id")
    result.references = {**{n: "active" for n in spec.characters}, **used}
    out.with_suffix(".json").write_text(json.dumps(asdict(result), indent=2))
    return result


def _check_placements(spec: PanelSpec, placements: dict | None) -> dict[str, Path]:
    """Placement names -> the panel's character names; reject strangers and empties."""
    if not placements:
        return {}
    by_lower = {c.lower(): c for c in spec.characters}
    out: dict[str, Path] = {}
    for name, path in placements.items():
        canonical = by_lower.get(str(name).lower())
        if canonical is None:
            raise RenderError(f"placement for {name!r}, who isn't in this panel "
                              f"({', '.join(spec.characters) or 'no characters'})")
        from manganation.render.inpaint import normalized_mask

        path = Path(path)
        if not path.is_file():
            raise RenderError(f"placement mask for {canonical} not found: {path}")
        if normalized_mask(path).getbbox() is None:
            raise RenderError(f"placement mask for {canonical} is empty: {path.name}")
        out[canonical] = path
    return out


def _upload_placement(client, path: Path, width: int, height: int):
    """Normalise a placement mask to the canvas; -> (uploaded name, fractional box)."""
    import tempfile

    from PIL import Image

    from manganation.render.inpaint import normalized_mask

    mask = normalized_mask(path).resize((width, height), Image.LANCZOS)
    x0, y0, x1, y1 = mask.getbbox() or (0, 0, width, height)
    with tempfile.TemporaryDirectory() as tmp:
        canvas = Path(tmp) / f"placement-{path.stem}.png"
        mask.save(canvas)
        name = client.upload_image(str(canvas))["name"]
    return name, (x0 / width, y0 / height, (x1 - x0) / width, (y1 - y0) / height)


def controlnet_file(models: dict, role: str) -> str:
    """The file for a models.yaml ``controlnets`` role."""
    roles = models.get("controlnets", {})
    if role not in roles:
        raise RenderError(f"unknown ControlNet {role!r}; models.yaml has {sorted(roles)}")
    return roles[role]["id"]


def _with_guide(graph, client, settings, models, guide: Path, width: int, height: int,
                strength: float | None):
    """Resize the guide take to the canvas, upload it, and add the ControlNet."""
    import tempfile

    from PIL import Image

    if not guide.is_file():
        raise RenderError(f"guide image not found: {guide}")
    c = settings.defaults.controlnet
    with Image.open(guide) as im:
        canvas = im.convert("RGB").resize((width, height), Image.LANCZOS)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"guide-{guide.stem}.png"
        canvas.save(path)
        name = client.upload_image(str(path))["name"]
    return graphs.with_controlnet(
        graph, image=name, controlnet=controlnet_file(models, c.model),
        strength=c.strength if strength is None else strength, start=c.start, end=c.end,
        low_threshold=c.low_threshold, high_threshold=c.high_threshold)


def _render(
    spec: PanelSpec, identity: Path, frame_w: float, frame_h: float, *,
    reading_order: ReadingOrder, seed: int | None, client: ComfyClient | None,
    out: Path, seq: int | None, placements: dict[str, Path] | None = None,
    guide: Path | None = None, guide_strength: float | None = None,
) -> RenderResult:
    """Render ``spec`` with characters from ``identity`` (a character registry folder).

    ``placements`` maps characters to mask images (the artist's placement layers, any
    size with the frame's proportions; white or opaque = this character). They replace
    the default reading-order bands as each character's regional reference mask.

    ``guide`` is an existing take whose composition (layout, poses) the render keeps:
    its edges steer the early steps through ControlNet, while the prompt (an edited
    expression, outfit, …) decides the details."""
    placements = _check_placements(spec, placements)
    settings = load_settings()
    models = load_models()
    client = client or ComfyClient(settings.comfyui.base_url)
    if not client.is_up():
        raise RenderError(f"ComfyUI is not reachable at {settings.comfyui.base_url}")

    style = load_style()
    prompt = build_prompt(spec, style,
                          character_tags(identity, spec.characters, spec.expressions))
    width, height = fit_resolution(frame_w, frame_h)
    seed = seed if seed is not None else (spec.seed if spec.seed is not None
                                          else random.randrange(2**32))
    d = settings.defaults.panel

    graph = graphs.txt2img(
        ckpt=models["checkpoints"]["primary"]["id"], prompt=prompt,
        negative=build_negative(spec, style), width=width, height=height, seed=seed,
        prefix=f"imanganation_{seq:03d}" if seq else "imanganation_inline",
        sampling=graphs.Sampling(d.steps, d.cfg, d.sampler, d.scheduler),
    )
    refs = find_references(identity, spec)
    ref_used: str | None = None
    ipa = settings.defaults.ipadapter
    ipa_file, clip_file = ipadapter_files(models, ipa.adapter)
    if len(refs) == 1 and len(spec.characters) == 1:
        name, ref = next(iter(refs.items()))
        uploaded = client.upload_image(str(ref))
        graph = graphs.with_ipadapter(
            graph, ref_image=uploaded["name"], ipadapter=ipa_file,
            clip_vision=clip_file, weight=ipa.weight_single,
        )
        ref_used = str(ref)
    elif refs and (len(refs) > 1 or len(spec.characters) > 1):
        # Multi-character: every reference stays masked to its character's region,
        # even when only one character has one (unmasked, it would pull both faces).
        ordered = [n for n in spec.characters if n in refs]
        boxes = regions_for(width, height, len(ordered), order=reading_order)
        tags_by_char = character_tags(identity, ordered, spec.expressions)
        norm = assign_regions(len(ordered), order=reading_order, margin=0.05)
        references = []
        region_text = []
        for name, (x, y, w, h), region in zip(ordered, boxes, norm, strict=False):
            uploaded = client.upload_image(str(refs[name]))
            entry = {"image": uploaded["name"], "mask": [x, y, w, h],
                     "canvas_w": width, "canvas_h": height,
                     "weight": ipa.weight_regional, "feather": ipa.feather}
            box = (region.x, region.y, region.w, region.h)
            if name in placements:
                mask_name, box = _upload_placement(client, placements[name], width, height)
                entry["mask_image"] = mask_name
            references.append(entry)
            text = ", ".join(tags_by_char.get(name, [])) or name
            region_text.append({"text": text, "box": box})
        # Optional per-character text bands (off by default: they split two-shots into
        # side-by-side pictures; the regional references hold each face instead).
        if ipa.regional_text > 0:
            graph = graphs.with_regional_conditioning(
                graph, regions=[{**r, "strength": ipa.regional_text} for r in region_text])
        graph = graphs.with_regional_ipadapter(
            graph, references=references, ipadapter=ipa_file, clip_vision=clip_file,
            force_regional=True,
        )
        ref_used = ",".join(ordered)

    if guide is not None:
        graph = _with_guide(graph, client, settings, models, Path(guide), width, height,
                            guide_strength)

    blobs = client.run(graph)
    if not blobs:
        raise RenderError("ComfyUI returned no image")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blobs[0])

    result = RenderResult(
        path=str(out), seq=seq, seed=seed, width=width, height=height,
        prompt=prompt, reference=ref_used,
        placements={n: str(p) for n, p in placements.items()},
        guide=str(guide) if guide is not None else None,
    )
    out.with_suffix(".json").write_text(json.dumps(asdict(result), indent=2))
    return result
