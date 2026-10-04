"""Two-pass hi-res fix of an already-rendered panel (Phase 6a).

A finished panel is often composed well at ~1 MP but soft. This module re-renders it
at a larger size so it holds up when the artist enlarges it in GIMP:

1. **Upscale** the existing image with a Real-ESRGAN anime model
   (``ImageUpscaleWithModel``) — fast, detail-preserving, no new hallucination.
2. **Polish** the result with a low-``denoise`` img2img pass on the checkpoint, which
   removes upscaler speckle and re-synthesises fine detail while retaining the source
   latent (so composition and identity survive). ``denoise: 0`` skips the polish.

The refined image is saved as a new take (``{seq:03d}_hires.png``, then
``_hires_takeNN``; an existing take is never overwritten) with its own
sidecar, so it sits alongside the original rather than overwriting it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image

from manganation.config import load_models, load_settings
from manganation.render import graphs
from manganation.render.comfy_client import ComfyClient


class RefineError(RuntimeError):
    pass


@dataclass
class RefineResult:
    path: str
    seq: int | None
    source: str
    width: int
    height: int
    upscaler: str
    denoise: float
    seed: int
    origin: str = ""  # the plain render this take descends from (scale is relative to it)


def upscaler_files(models: dict, name: str) -> str:
    """The filename for a models.yaml ``upscalers`` role."""
    roles = models.get("upscalers", {})
    if name not in roles:
        raise RefineError(f"unknown upscaler {name!r}; models.yaml has {sorted(roles)}")
    return roles[name]["id"]


def fit_target_width(
    width: int, height: int, scale: float, *, multiple: int = 64, max_pixels: int,
) -> tuple[int, int]:
    """Source size × ``scale``, aligned to ``multiple`` and capped at ``max_pixels``.

    Scaling is clamped so the long edge never exceeds the pixel budget; the aspect
    ratio is preserved (the upscaler already produced it), so no skew is introduced.
    """
    if width <= 0 or height <= 0:
        raise RefineError("source image has no size")
    if scale <= 0:
        raise RefineError("scale must be positive")
    tw = max(multiple, round(width * scale / multiple) * multiple)
    th = max(multiple, round(height * scale / multiple) * multiple)
    if tw * th > max_pixels:
        shrink = (max_pixels / (tw * th)) ** 0.5
        tw = max(multiple, int(tw * shrink) // multiple * multiple)
        th = max(multiple, int(th * shrink) // multiple * multiple)
    return tw, th


def render_origin(src: Path, *, max_hops: int = 16) -> Path:
    """The plain render a take descends from.

    Refine and inpaint outputs are derived takes: their sidecars name their ``source``
    (refine: ``upscaler``; inpaint: ``mask``). Follow that chain back to a take that
    isn't derived. Raises if a derived take's ancestor is gone, since its original
    size is then unknown and a refine could compound."""
    path, seen = src, set()
    for _ in range(max_hops):
        if path.resolve() in seen:
            raise RefineError(
                f"{src.name}: its take history loops (an older take file was overwritten), "
                f"so its original render size is unknown; refine a render take instead")
        seen.add(path.resolve())
        try:
            side = json.loads(path.with_suffix(".json").read_text())
        except (OSError, ValueError):
            return path  # no sidecar: treat as a render
        parent = side.get("source")
        if not (("upscaler" in side or "mask" in side) and parent):
            return path
        parent = Path(parent)
        if not parent.is_file():
            raise RefineError(
                f"{path.name} was made from {parent.name}, which no longer exists, so its "
                f"original render size is unknown; refine an existing render instead")
        path = parent
    raise RefineError(f"{src.name}: take history is too deep or circular")


def take_path(project: Path, seq: int) -> Path:
    """Output name for a new hi-res take: ``{seq}_hires.png``, then
    ``{seq}_hires_takeNN.png``.

    Never overwrite an existing take: a placed GIMP layer records the exact file it
    shows, and derived takes (an inpaint of a hi-res) record their ``source``. Replacing
    the file under them silently changes what they point at, and can make the take
    history loop. The plug-in places the newest file, so new names are safe."""
    panels = project / "panels"
    first = panels / f"{seq:03d}_hires.png"
    if not first.exists():
        return first
    n = 2
    while (panels / f"{seq:03d}_hires_take{n:02d}.png").exists():
        n += 1
    return panels / f"{seq:03d}_hires_take{n:02d}.png"


# VAE tiling on 16 GB starts around 1.5 MP; keep the polish below this so the whole
# image encodes/decodes in a single tile (tiled VAE leaves blocky seams).
POLISH_MAX_PIXELS = 1152 * 1152


def _polish_size(width: int, height: int, *, multiple: int = 64) -> tuple[int, int]:
    """Round the source size onto the SDXL grid, clamped to the single-tile budget."""
    w = max(multiple, round(width / multiple) * multiple)
    h = max(multiple, round(height / multiple) * multiple)
    if w * h > POLISH_MAX_PIXELS:
        shrink = (POLISH_MAX_PIXELS / (w * h)) ** 0.5
        w = max(multiple, int(w * shrink) // multiple * multiple)
        h = max(multiple, int(h * shrink) // multiple * multiple)
    return w, h


def refine_panel(
    project: Path, seq: int, *,
    source: Path | None = None, scale: float | None = None, denoise: float | None = None,
    seed: int | None = None, client: ComfyClient | None = None,
) -> RefineResult:
    """Hi-res-fix panel ``seq`` (or an explicit ``source`` image) at ``scale``×.

    Legacy form: the original render and its prompt are found by walking sidecars."""
    project = Path(project)
    if source is None:
        # Prefer the newest existing take (e.g. a _hires rebuild, or the base render).
        src = _newest_panel(project, seq)
        if src is None:
            raise RefineError(f"no rendered image for panel {seq} in {project / 'panels'}")
    else:
        src = Path(source)
        if not src.exists():
            raise RefineError(f"source image not found: {src}")

    # Scale is relative to the *original render*, never to an already-enlarged take
    # (a hi-res, or an inpaint made on one): otherwise refining it again compounds,
    # e.g. 2x of a 2x gave 3840x4352 from a 960x1088 render. Derived edits are kept:
    # the chosen take is what gets enlarged, just only up to render x scale.
    origin = render_origin(src)
    with Image.open(origin) as im:
        origin_size = im.size
    # The panel prompt lives with the render; a derived take's sidecar has none (refine)
    # or only the artist's short patch prompt (inpaint), which must not steer the polish.
    prompt, negative = _panel_prompts(origin)
    result = refine_image(
        src, origin_size=origin_size, origin_label=origin.name, prompt=prompt,
        negative=negative, out=take_path(project, seq), scale=scale, denoise=denoise,
        seed=seed, client=client, prefix=f"imanganation_{seq:03d}_hires",
    )
    result.seq, result.origin = seq, str(origin)
    take_path_json = Path(result.path).with_suffix(".json")
    take_path_json.write_text(json.dumps(asdict(result), indent=2))
    return result


def refine_inline(
    project_id: str, source: Path, *, origin_width: int, origin_height: int,
    prompt: str | None = None, scale: float | None = None, denoise: float | None = None,
    seed: int | None = None, client: ComfyClient | None = None,
    outputs: Path | None = None,
) -> RefineResult:
    """Container form (docs/engine-api.md): the take's history lives in the project,
    so the caller sends the origin take's size and its render prompt. Output goes to
    the engine's ``outputs/<project>/`` cache; the plug-in records it as a take."""
    import uuid

    from manganation.config import REPO_ROOT

    source = Path(source)
    if not source.is_file():
        raise RefineError(f"source image not found: {source}")
    if origin_width <= 0 or origin_height <= 0:
        raise RefineError("origin size must be positive")
    style_prompt, negative = _style_prompts()
    out_dir = (outputs if outputs is not None else REPO_ROOT / "outputs") / project_id
    out = out_dir / f"{source.stem}-hires-{uuid.uuid4().hex[:12]}.png"
    result = refine_image(
        source, origin_size=(origin_width, origin_height),
        origin_label=f"{origin_width}x{origin_height} origin", prompt=prompt or style_prompt,
        negative=negative, out=out, scale=scale, denoise=denoise, seed=seed, client=client,
        prefix="imanganation_inline_hires",
    )
    result.origin = f"{origin_width}x{origin_height}"
    out.with_suffix(".json").write_text(json.dumps(asdict(result), indent=2))
    return result


def refine_image(
    src: Path, *, origin_size: tuple[int, int], origin_label: str, prompt: str,
    negative: str, out: Path, scale: float | None, denoise: float | None,
    seed: int | None, client: ComfyClient | None, prefix: str,
) -> RefineResult:
    """The hi-res fix itself: enlarge ``src`` to ``origin_size`` x ``scale``."""
    settings = load_settings()
    models = load_models()
    r = settings.defaults.refiner
    if not r.enabled and scale is None and denoise is None:
        raise RefineError("refiner is disabled in settings.yaml (defaults.refiner.enabled)")
    scale = r.scale if scale is None else scale
    denoise = r.denoise if denoise is None else denoise

    with Image.open(src) as im:
        width, height = im.size
    base_w, base_h = origin_size
    tw, th = fit_target_width(base_w, base_h, scale, max_pixels=r.max_pixels)
    if tw <= width and th <= height:
        raise RefineError(
            f"{src.name} is already {width}x{height}, at or beyond {scale:g}x its render "
            f"({origin_label}, {base_w}x{base_h}); nothing to do. Use a larger scale to "
            f"go further")

    client = client or ComfyClient(settings.comfyui.base_url)
    if not client.is_up():
        raise RefineError(f"ComfyUI is not reachable at {settings.comfyui.base_url}")
    seed = seed if seed is not None else 0

    uploaded = client.upload_image(str(src))
    upscaler = upscaler_files(models, r.upscaler)
    # Polish at the source size (rounded to the SDXL grid, capped) so the VAE
    # round-trip stays in one tile; the final enlargement is the model upscale.
    polish_w, polish_h = _polish_size(width, height)
    graph = graphs.upscale_refine(
        ckpt=models["checkpoints"]["primary"]["id"],
        image=uploaded["name"], prompt=prompt, negative=negative,
        width=tw, height=th, seed=seed, prefix=prefix,
        upscale_model=upscaler, denoise=denoise,
        polish_width=polish_w, polish_height=polish_h,
        sampling=graphs.Sampling(r.steps, r.cfg),
    )
    blobs = client.run(graph)
    if not blobs:
        raise RefineError("ComfyUI returned no image")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blobs[0])
    return RefineResult(
        path=str(out), seq=None, source=str(src), width=tw, height=th,
        upscaler=r.upscaler, denoise=denoise, seed=seed, origin="",
    )


def _newest_panel(project: Path, seq: int) -> Path | None:
    """Newest *render* take for a panel: base first, then later takes.

    The hi-res output (``{seq}_hires.png``) is deliberately excluded so re-refining
    always starts from the render, not from a previous enlargement (which would
    compound the scale).
    """
    panels = project / "panels"
    candidates = [panels / f"{seq:03d}.png"]
    candidates += sorted(panels.glob(f"{seq:03d}_take*.png"))
    existing = [p for p in candidates if p.exists()]
    return existing[-1] if existing else None


def _panel_prompts(src: Path) -> tuple[str, str]:
    """Read the sidecar prompt for the source panel; fall back to a bare prompt."""
    from manganation.render.panel import load_style

    sidecar = src.with_suffix(".json")
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text())
            if data.get("prompt"):
                return data["prompt"], load_style().get("negative", "")
        except (OSError, ValueError):
            pass
    return _style_prompts()


def _style_prompts() -> tuple[str, str]:
    """The colour style's bare prompt and negative, when no panel prompt is known."""
    from manganation.render.panel import build_negative, load_style

    style = load_style()
    return style.get("prompt_prefix", "").strip().rstrip(","), build_negative(
        _blank_spec(), style
    )


def _blank_spec():
    from manganation.script.schema import PanelSpec

    return PanelSpec(page=1, panel=1)
