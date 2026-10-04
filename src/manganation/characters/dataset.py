"""Character-training dataset construction (Phase 6b spike).

A per-character LoRA needs 15-30 varied images. We have exactly one generated
design sheet per character, so this module **synthesises** a dataset from it: the
sheet is the identity seed, and each variant re-renders it (img2img) with a new
pose / expression / background while keeping the character's appearance tags.

This module is purely the *spec builder* (no GPU): it turns an appearance + a count
into a deterministic list of :class:`VariantSpec` (prompt, negative, denoise, note,
caption). The generation wrapper that calls ComfyUI is :mod:`dataset_generator`.

Dataset layout (kohya/sd-scripts compatible)::

    projects/<name>/datasets/<slug>/
      01.png
      01.txt          <- caption (comma tags)
      ...
      dataset.json    <- our manifest: trait tags, prompts, seeds, lineage
"""

from __future__ import annotations

from dataclasses import dataclass, field

from manganation.characters.design import DESIGN_QUALITY
from manganation.characters.schema import Character

# Variation axes. Each entry is (tags added to the positive prompt, note). The
# base appearance tags are always kept; these only *add* angle/pose/mood/background
# so the character reads consistently while the dataset gains diversity. Ordering is
# deterministic (no random) so a run is reproducible once seeds are fixed.
ANGLES = [
    (["front view"], "front"),
    (["three quarter view"], "3/4"),
    (["from side", "profile"], "profile"),
    (["from behind", "back view"], "back"),
    (["looking up"], "lookup"),
    (["looking down"], "lookdown"),
]

EXPRESSIONS = [
    (["neutral expression"], "neutral"),
    (["smile", "happy"], "smile"),
    (["serious", "frown"], "serious"),
    (["surprised", "open mouth"], "surprised"),
    (["sad"], "sad"),
    (["angry"], "angry"),
]

POSES = [
    (["standing", "arms at sides"], "stand"),
    (["sitting"], "sit"),
    (["walking"], "walk"),
    (["arms crossed"], "armscrossed"),
    (["hand on hip"], "handonhip"),
    (["waving"], "wave"),
]

SHOTS = [
    (["cowboy shot"], "cowboy"),
    (["upper body", "portrait"], "bust"),
    (["full body"], "full"),
]

BACKGROUNDS = [
    (["simple white background"], "white"),
    (["simple grey background"], "grey"),
    (["outdoors", "sky", "clouds"], "outdoors"),
    (["classroom", "indoors"], "classroom"),
    (["city street", "day"], "street"),
    (["night", "dark background"], "night"),
]

# A flat, low-variance look is easier to train; keep the quality tail short.
_QUALITY = [t for t in DESIGN_QUALITY if t != "simple white background"]

# Caption prefix per axis, so the auto-caption describes the variation, not just
# names it. Kept short; kohya captions are comma-separated tags.
_CAPTION = {
    "front": "front view", "3/4": "three quarter view", "profile": "side profile",
    "back": "back view", "lookup": "looking up", "lookdown": "looking down",
    "neutral": "neutral expression", "smile": "smiling", "serious": "serious expression",
    "surprised": "surprised expression", "sad": "sad expression", "angry": "angry expression",
    "stand": "standing", "sit": "sitting", "walk": "walking", "armscrossed": "arms crossed",
    "handonhip": "hand on hip", "wave": "waving",
    "cowboy": "cowboy shot", "bust": "upper body", "full": "full body",
    "white": "white background", "grey": "grey background", "outdoors": "outdoors",
    "classroom": "classroom", "street": "city street", "night": "night",
}


@dataclass
class VariantSpec:
    """One planned training image: prompt + caption + labels for curation."""

    index: int
    positive: str
    negative: str
    note: str
    caption: str
    denoise: float = 0.6
    tags: list[str] = field(default_factory=list)


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.append(it)
    return seen


def variant_specs(
    character: Character,
    count: int = 24,
    *,
    base_denoise: float = 0.6,
) -> list[VariantSpec]:
    """Plan ``count`` varied dataset images for ``character`` (deterministic).

    Images are laid out as a deterministic cartesian walk of the variation axes, so
    the first N are the most-different-from-each-other. The character's appearance
    tags are always first in the prompt and caption, so the trained LoRA binds to
    the *person*, not the pose/background.
    """
    if count < 1:
        return []
    # Appearance only: expression is a variation axis, so the LoRA must not learn the
    # character's default face as part of who they are.
    identity = character.appearance.appearance_tags()
    identity_caption = ", ".join(identity)

    # Walk the cartesian product of the variation axes as a mixed-radix odometer so
    # each variant is distinct and early variants spread across several axes at once.
    axes = [ANGLES, EXPRESSIONS, POSES, SHOTS, BACKGROUNDS]
    specs: list[VariantSpec] = []
    for i in range(count):
        picks = []
        n = i
        for axis in axes:
            picks.append(axis[n % len(axis)])
            n //= len(axis)
        angle, expr, pose, shot, bg = picks

        variation_tags = [
            *angle[0], *expr[0], *pose[0], *shot[0], *bg[0],
        ]
        positive = ", ".join(_dedupe([*identity, *variation_tags, *_QUALITY]))
        caption = ", ".join(
            _dedupe([identity_caption, *[_CAPTION[nm] for nm in (
                angle[1], expr[1], pose[1], shot[1], bg[1],
            )]])
        )
        specs.append(
            VariantSpec(
                index=i + 1,
                positive=positive,
                negative=character_negative(),
                note="-".join([angle[1], expr[1], pose[1], shot[1], bg[1]]),
                caption=caption,
                denoise=base_denoise,
                tags=variation_tags,
            )
        )
    return specs


def character_negative() -> str:
    """Negative prompt for dataset variants (no extra people, no text)."""
    return (
        "text, speech bubble, watermark, signature, username, lowres, bad anatomy, "
        "bad hands, extra digits, missing fingers, worst quality, low quality, "
        "jpeg artifacts, multiple characters, 2people, multiple views, inset, border, "
        "panel border, character sheet, reference sheet, turnaround, chibi"
    )
