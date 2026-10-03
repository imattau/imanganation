"""Two-pass hi-res fix of an already-rendered panel (Phase 6a).

A finished panel is often composed well at ~1 MP but soft. This module re-renders it
at a larger size so it holds up when the artist enlarges it in GIMP:

1. **Upscale** the existing image with a Real-ESRGAN anime model
   (``ImageUpscaleWithModel``) — fast, detail-preserving, no new hallucination.
2. **Polish** the result with a low-``denoise`` img2img pass on the checkpoint, which
   removes upscaler speckle and re-synthesises fine detail while retaining the source
   latent (so composition and identity survive). ``denoise: 0`` skips the polish.

The refined image is saved as a new take (``{seq:03d}_hires.png``) with its own
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


def take_path(project: Path, seq: int) -> Path:
    """Stable output name for a panel's hi-res fix (the take the plug-in prefers)."""
    return project / "panels" / f"{seq:03d}_hires.png"


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
    """Hi-res-fix panel ``seq`` (or an explicit ``source`` image) at ``scale``×."""
    project = Path(project)
    settings = load_settings()
    models = load_models()
    r = settings.defaults.refiner
    if not r.enabled and scale is None and denoise is None:
        raise RefineError("refiner is disabled in settings.yaml (defaults.refiner.enabled)")

    scale = r.scale if scale is None else scale
    denoise = r.denoise if denoise is None else denoise

    if source is None:
        # Prefer the newest existing take (e.g. a _hires rebuild, or the base render).
        src = _newest_panel(project, seq)
        if src is None:
            raise RefineError(f"no rendered image for panel {seq} in {project / 'panels'}")
    else:
        src = Path(source)
        if not src.exists():
            raise RefineError(f"source image not found: {src}")

    with Image.open(src) as im:
        width, height = im.size

    tw, th = fit_target_width(width, height, scale, max_pixels=r.max_pixels)
    if (tw, th) == (width, height):
        raise RefineError(f"target size {tw}x{th} equals source; nothing to do")

    client = client or ComfyClient(settings.comfyui.base_url)
    if not client.is_up():
        raise RefineError(f"ComfyUI is not reachable at {settings.comfyui.base_url}")

    # Reuse the panel's own prompt so the polish stays on-model (sidecar, if present).
    prompt, negative = _panel_prompts(src)
    seed = seed if seed is not None else 0

    uploaded = client.upload_image(str(src))
    upscaler = upscaler_files(models, r.upscaler)
    # Polish at the source size (rounded to the SDXL grid, capped) so the VAE
    # round-trip stays in one tile; the final enlargement is the model upscale.
    polish_w, polish_h = _polish_size(width, height)
    graph = graphs.upscale_refine(
        ckpt=models["checkpoints"]["primary"]["id"],
        image=uploaded["name"], prompt=prompt, negative=negative,
        width=tw, height=th, seed=seed, prefix=f"imanganation_{seq:03d}_hires",
        upscale_model=upscaler, denoise=denoise,
        polish_width=polish_w, polish_height=polish_h,
        sampling=graphs.Sampling(r.steps, r.cfg),
    )
    blobs = client.run(graph)
    if not blobs:
        raise RefineError("ComfyUI returned no image")

    out = take_path(project, seq)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blobs[0])
    result = RefineResult(
        path=str(out), seq=seq, source=str(src), width=tw, height=th,
        upscaler=r.upscaler, denoise=denoise, seed=seed,
    )
    out.with_suffix(".json").write_text(json.dumps(asdict(result), indent=2))
    return result


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
    from manganation.render.panel import build_negative, load_style

    style = load_style()
    sidecar = src.with_suffix(".json")
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text())
            if data.get("prompt"):
                return data["prompt"], style.get("negative", "")
        except (OSError, ValueError):
            pass
    return style.get("prompt_prefix", "").strip().rstrip(","), build_negative(
        _blank_spec(), style
    )


def _blank_spec():
    from manganation.script.schema import PanelSpec

    return PanelSpec(page=1, panel=1)
