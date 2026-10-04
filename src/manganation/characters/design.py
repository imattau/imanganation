"""Character design: reference-less design-sheet generation.

When a script gives no reference image, we generate a **single-figure reference**
(one character, cowboy shot, facing the viewer, plain background, square) and lock
it as the character's ``base`` version. Every later panel reuses it through
IP-Adapter, so identity stays stable without training a LoRA.

Why not a multi-view design sheet: IP-Adapter transfers composition as well as
identity, so a sheet with several poses/heads renders panels as grids of repeated
figures (docs/quality/2026-10-04_live_quality_check.md). The CLIP encoder also
centre-crops to a square, so the reference is square to keep the head in frame.

This module is deliberately split into a pure *prompt builder* (unit-testable,
no GPU) and a thin *generation* wrapper that calls ComfyUI.
"""

from __future__ import annotations

from dataclasses import dataclass

from manganation.characters.schema import AppearanceSpec, Character

DESIGN_QUALITY = [
    "masterpiece",
    "best quality",
    "(cowboy shot:1.2)",  # thighs up: the head stays in CLIP's centre crop
    # A neutral reference pose: leaning, crouching or close-up framings carry their
    # composition into every panel through IP-Adapter
    "standing",
    "straight-on",
    "arms at sides",
    "looking at viewer",
    "facing viewer",
    # Danbooru tags (NoobAI's vocabulary), weighted: free text like "simple white
    # background", or the plain tags, still gave grey or tinted backdrops (dark
    # outfits pull a dark one)
    "(white background:1.4)",
    "(simple background:1.2)",
    "anime coloring",
    "anime",
    "highly detailed",
]

# Single-figure control must lead the prompt. SDXL resolves a long mid-prompt trait
# list (outfit + accessories) as a design sheet of *several* figures unless "solo
# focus" and an explicit head-count are the first tokens — a "solo" buried after the
# traits does not prevent the split (docs/phase6b.md).
DESIGN_LEAD = ["solo focus", "solo", "1 character"]

DESIGN_NEGATIVE = (
    "text, speech bubble, watermark, signature, username, lowres, bad anatomy, "
    "bad hands, extra digits, missing fingers, worst quality, low quality, "
    "jpeg artifacts, multiple views, multiple characters, 2people, 2boys, 2girls, "
    "cropped, out of frame, busy background, props, bag, inset, border, panel border, "
    "character sheet, reference sheet, turnaround, expression sheet, chibi, "
    # A shadowed/gradient backdrop rides along into IP-Adapter panels and dataset
    # variants (docs/phase6b.md); references want a flat, evenly lit background.
    "shadow, cast shadow, dramatic lighting, gradient background, grey background, "
    "colored background, monochrome, greyscale, sketch, lineart, uncolored, "
    # "looking at / facing viewer" pulls NoobAI toward POV framings, where the viewer's
    # hand reaches into the shot (a stray hand at the edge of the sheet)
    "pov, pov hands, disembodied limb, extra arms, extra hands, "
    "leaning forward, bent over, squatting, kneeling, sitting, close-up, "
    "from above, from below, dutch angle, foreshortening"
)

# Sane defaults when a trait is unknown, so generation never produces a blank look.
_DEFAULT_GENDER = "1person"


def _dedupe(items: list[str]) -> list[str]:
    """Order-preserving, drop empties/duplicates."""
    seen: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.append(it)
    return seen


@dataclass
class DesignPrompt:
    positive: str
    negative: str
    tags: list[str]

    def as_dict(self) -> dict:
        return {"positive": self.positive, "negative": self.negative, "tags": self.tags}


def build_design_prompt(
    appearance: AppearanceSpec,
    *,
    extra: list[str] | None = None,
) -> DesignPrompt:
    """Turn an :class:`AppearanceSpec` into a single-figure reference prompt."""
    traits = list(appearance.prompt_tags())
    if not appearance.gender:
        traits.insert(0, _DEFAULT_GENDER)
    if extra:
        traits.extend(extra)

    tags = [*traits, *DESIGN_QUALITY]
    seen = _dedupe(tags)
    # Single-figure control goes *first* so SDXL does not split the trait list into
    # a multi-figure sheet (see DESIGN_LEAD).
    seen = _dedupe([*DESIGN_LEAD, *seen])
    return DesignPrompt(
        positive=", ".join(seen),
        negative=DESIGN_NEGATIVE,
        tags=seen,
    )


def design_prompt_for(character: Character, *, extra: list[str] | None = None) -> DesignPrompt:
    return build_design_prompt(character.appearance, extra=extra)
