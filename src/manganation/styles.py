"""A project's overall look: a preset (config/styles/presets.yaml) and the author's
own words, on top of the colour style every render uses (default_color.yaml).

The plug-in sends ``{"preset": id, "text": "..."}`` (project.render.style) with renders,
inpaints and character and location designs, so references and panels share the look.
The engine stays colour-only (docs/color-policy.md).
"""

from __future__ import annotations

from functools import lru_cache

import yaml

from manganation.config import CONFIG_DIR

DEFAULT_PRESET = "default"
NSFW_NEGATIVE = "nsfw"


class StyleError(ValueError):
    pass


@lru_cache(maxsize=1)
def _presets() -> dict[str, dict]:
    data = yaml.safe_load((CONFIG_DIR / "styles" / "presets.yaml").read_text())
    return {key: {"id": key, **value} for key, value in (data.get("presets") or {}).items()}


def presets() -> list[dict]:
    """Every preset: id, label, summary, measured (what an eval found, if run)."""
    return [{k: p.get(k, "") for k in ("id", "label", "summary", "measured")}
            for p in _presets().values()]


def preset(preset_id: str | None) -> dict:
    found = _presets().get(preset_id or DEFAULT_PRESET)
    if found is None:
        raise StyleError(f"unknown style {preset_id!r} "
                         f"(known: {', '.join(_presets())})")
    return found


def check_tags(options: dict | None) -> list[str]:
    """The tags the eval tagger should see when this style shows."""
    return list(preset((options or {}).get("preset")).get("check") or [])


def _join(*parts: str) -> str:
    return ", ".join(p.strip().strip(",").strip() for p in parts if p and p.strip(", "))


def _words(text: str) -> str:
    return " ".join(str(text or "").split())


def apply(base: dict, options: dict | None) -> dict:
    """The colour style ``base`` (``prompt_prefix``, ``negative``) with a project's
    style on top. Adds ``prose`` (for prose-prompted engines and location images),
    ``tags`` (for character designs) and ``id``. ``options`` None: the default look."""
    options = options or {}
    chosen = preset(options.get("preset"))
    text = _words(options.get("text", ""))
    tags = _join(chosen.get("tags", ""), text)
    prose = _words(chosen.get("prose", "")) or "clean line art and cel shading"
    if text:
        prose += f"; {text}"
    style = dict(base)
    prefix = _join(base.get("prompt_prefix", ""), tags)
    style["prompt_prefix"] = f"{prefix}, " if prefix else ""
    # Adult content is the author's explicit choice per project: unflagged, "nsfw" is in
    # every negative (panels, designs, refiner, inpaint); flagged, the style adds nothing.
    guard = "" if options.get("nsfw") else NSFW_NEGATIVE
    style["negative"] = _join(base.get("negative", ""), chosen.get("negative", ""), guard)
    style["prose"] = prose
    style["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    style["extra_negative"] = _join(chosen.get("negative", ""), guard)  # for character designs
    style["id"] = chosen["id"]
    style["text"] = text
    style["nsfw"] = bool(options.get("nsfw"))
    return style
