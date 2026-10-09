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

import re
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


_COUNT_TAG = re.compile(r"^\d+(girl|boy|other|person)s?$")
_KINDS = {"1boy": "a boy", "1girl": "a girl"}

# Models with an LLM text encoder (Qwen-Image, Z-Anime) read prose, and Qwen-Image runs
# at cfg 1 with no negative prompt, so "one figure, nobody else" has to be said in the
# positive prompt, plainly and more than once.
PROSE_DESIGN_NEGATIVE = (
    "multiple characters, 2people, 2boys, 2girls, group, crowd, duplicates, clones, "
    "multiple views, character sheet, reference sheet, turnaround, expression sheet, "
    "inset, panel border, border, text, speech bubble, watermark, signature, lowres, "
    "bad anatomy, bad hands, extra arms, extra hands, pov, shadow, gradient background, "
    "grey background, colored background, busy background, props, monochrome, sketch"
)


def build_prose_design_prompt(
    appearance: AppearanceSpec,
    *,
    style: str = "clean line art and cel shading",
) -> DesignPrompt:
    """The single-figure reference as plain English, for Qwen-Image and Z-Anime."""
    tags = [t.strip() for t in appearance.prompt_tags() if t and t.strip()]
    kind = _KINDS.get(tags[0], "a person") if tags else "a person"
    looks = ", ".join(t for t in tags if not _COUNT_TAG.match(t))
    positive = (
        f"A full-colour anime character reference illustration, {style}, showing exactly "
        f"one person and nobody else: {kind}" + (f", {looks}" if looks else "") + ". "
        "Pose: standing straight, facing the viewer, looking at the viewer, arms relaxed "
        "at the sides, framed from the thighs up with the whole head in frame. "
        "Background: plain flat white, nothing else in the picture. "
        "One single figure only: no second character, no other people, no group, no "
        "duplicates, no turnaround, no character sheet, no multiple views, no text, no "
        "panel borders."
    )
    return DesignPrompt(positive=positive, negative=PROSE_DESIGN_NEGATIVE,
                        tags=[kind, *([looks] if looks else [])])
