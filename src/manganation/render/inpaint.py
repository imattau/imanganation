"""Masked repaint of a region of a panel (GIMP "Inpaint Selection").

The GIMP plug-in exports two images: the **init** (the current layer / panel take) and
a **mask** (the selection). This module uploads both, runs the ComfyUI inpaint graph so
only the masked region is re-synthesised from a short prompt, and saves the result as a
new take. The result is composited back over the original through a soft copy of the
grown mask, so pixels outside it are the original's, untouched (a bare VAE round trip
would redraw line work across the whole panel).

Output: ``panels/{seq:03d}_inpaint.png`` (plus ``_takeNN`` when repeated) with its own
sidecar, so the original render and the artist's iterations all sit side by side.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image

from manganation.config import load_models, load_settings
from manganation.render import graphs
from manganation.render.comfy_client import ComfyClient


class InpaintError(RuntimeError):
    pass


@dataclass
class InpaintResult:
    path: str
    seq: int
    source: str
    mask: str
    prompt: str
    width: int
    height: int
    denoise: float
    grow_mask_by: int
    seed: int
    positive: str = ""  # the full prompt sent (style prefix + the artist's prompt)


def mask_channel_for(path: Path) -> str:
    """Pick the mask channel: a transparent PNG uses ``alpha``; an opaque one is
    treated as a white-on-black mask via the red channel."""
    with Image.open(path) as im:
        has_alpha = im.mode in ("RGBA", "LA") or "transparency" in im.info
    return "alpha" if has_alpha else "red"


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
) -> InpaintResult:
    """Repaint the masked region of panel ``seq`` (or an explicit ``source``) from
    ``prompt``. ``mask`` and ``source`` must live inside ``project``."""
    project = Path(project)
    settings = load_settings()
    models = load_models()
    d = settings.defaults.inpaint
    denoise = d.denoise if denoise is None else denoise
    grow_mask_by = d.grow_mask_by if grow_mask_by is None else grow_mask_by

    if source is None:
        src = _newest_panel(project, seq)
        if src is None:
            raise InpaintError(f"no rendered image for panel {seq} in {project / 'panels'}")
    else:
        src = _resolve(source, project, "source")
    mask_path = _resolve(mask, project, "mask")

    if not (0.0 < denoise <= 1.0):
        raise InpaintError("denoise must be in (0, 1]")

    with Image.open(src) as im:
        width, height = im.size
    with Image.open(mask_path) as im:
        mw, mh = im.size
    if (mw, mh) != (width, height):
        raise InpaintError(
            f"mask size {mw}x{mh} must match the source {width}x{height}"
        )

    client = client or ComfyClient(settings.comfyui.base_url)
    if not client.is_up():
        raise InpaintError(f"ComfyUI is not reachable at {settings.comfyui.base_url}")

    neg = negative if negative is not None else _style_negative()
    positive = _with_style(prompt)
    seed = seed if seed is not None else random.randrange(2**32)
    source_up = client.upload_image(str(src))
    mask_up = client.upload_image(str(mask_path))
    graph = graphs.inpaint(
        ckpt=models["checkpoints"]["primary"]["id"],
        image=source_up["name"], mask=mask_up["name"],
        prompt=positive, negative=neg, seed=seed,
        prefix=f"imanganation_{seq:03d}_inpaint",
        denoise=denoise, grow_mask_by=grow_mask_by,
        mask_channel=mask_channel_for(mask_path),
        sampling=graphs.Sampling(d.steps, d.cfg),
    )
    blobs = client.run(graph)
    if not blobs:
        raise InpaintError("ComfyUI returned no image")

    out = output_path(project, seq)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blobs[0])
    result = InpaintResult(
        path=str(out), seq=seq, source=str(src), mask=str(mask_path), prompt=prompt,
        width=width, height=height, denoise=denoise, grow_mask_by=grow_mask_by, seed=seed,
        positive=positive,
    )
    out.with_suffix(".json").write_text(json.dumps(asdict(result), indent=2))
    return result


def _with_style(prompt: str) -> str:
    """The patch is drawn into a panel rendered with the colour style, so give it the
    same style prefix; a bare "red apple" drifts from the surrounding art."""
    from manganation.render.panel import load_style

    prefix = load_style().get("prompt_prefix", "").strip().rstrip(",")
    return f"{prefix}, {prompt.strip()}" if prefix else prompt.strip()


def _style_negative() -> str:
    from manganation.render.panel import load_style

    return load_style().get("negative", "")
