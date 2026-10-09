"""Canonical data model for a parsed manga script.

The script layer normalises any input (plain prose, page/panel script, or
Mangaplay/Fountain+) into a list of :class:`PanelSpec`. Dialogue is captured for
narrative continuity but is *never rendered* — imanganation produces text-free
panels only.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ReadingOrder(StrEnum):
    RTL = "rtl"  # manga: right-to-left
    LTR = "ltr"  # manhwa / western: left-to-right


class ColorMode(StrEnum):
    """Recorded metadata only. The engine renders colour only (docs/color-policy.md):
    ``color_mode`` is carried through for the artist/plug-in but never changes the render."""

    INHERIT = "inherit"
    BW = "bw"
    COLOR = "color"


class DialogueLine(BaseModel):
    """A spoken/thought/narration line. Stored for continuity; not drawn."""

    speaker: str
    text: str
    kind: Literal["speech", "thought", "narration", "shout", "whisper"] = "speech"


class PanelSpec(BaseModel):
    """One panel — the atomic unit of generation.

    Each panel renders to its own standalone image. ``page`` is only a grouping /
    pacing label (and drives reading order); panels are never composited together.
    """

    page: int = Field(ge=0)  # 0 = the cover
    panel: int = Field(ge=1)
    reading_order: ReadingOrder = ReadingOrder.RTL

    scene_heading: str = ""
    location: str = ""
    characters: list[str] = Field(default_factory=list)
    action: str = ""

    # staging
    camera: str = ""  # e.g. "wide shot", "close-up", "over-the-shoulder", "dutch angle"
    expressions: dict[str, str] = Field(default_factory=dict)

    # narrative text (never rendered)
    dialogue: list[DialogueLine] = Field(default_factory=list)
    sfx: list[str] = Field(default_factory=list)
    notes: str = ""
    flashback: bool = False
    cover: bool = False  # the cover picture (page 0): a text-free key illustration

    # generation
    color_mode: ColorMode = ColorMode.INHERIT
    # Frame hints ([FRAME: ...]): None / "" = none given. A drawn frame's size wins.
    aspect_ratio: str | None = Field(default=None, pattern=r"^[0-9]+:[0-9]+$")
    size: Literal["", "small", "large", "splash"] = ""
    refs: dict[str, str] = Field(default_factory=dict)  # character -> reference image path
    seed: int | None = None

    @field_validator("characters")
    @classmethod
    def _dedupe_characters(cls, v: list[str]) -> list[str]:
        seen: list[str] = []
        for name in v:
            if name and name not in seen:
                seen.append(name)
        return seen


class CastEntry(BaseModel):
    """A character declared in the script's ``CHARACTERS`` block: the author's own
    design description, which wins over anything the LLM would invent."""

    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    description: str = ""


class LocationEntry(BaseModel):
    """A place declared in the script's ``LOCATIONS`` block: the author's description,
    which the location's design is drawn from."""

    name: str = Field(min_length=1)
    description: str = ""


class Script(BaseModel):
    """A full parsed script: ordered panels plus project-level metadata."""

    title: str = ""
    reading_order: ReadingOrder = ReadingOrder.RTL
    default_color_mode: ColorMode = ColorMode.COLOR
    cast: list[CastEntry] = Field(default_factory=list)
    locations: list[LocationEntry] = Field(default_factory=list)
    panels: list[PanelSpec] = Field(default_factory=list)

    def panels_for_page(self, page: int) -> list[PanelSpec]:
        return [p for p in self.panels if p.page == page]

    def pages(self) -> list[int]:
        return sorted({p.page for p in self.panels})

    @classmethod
    def from_json(cls, text: str) -> Script:
        return cls.model_validate_json(text)

    def to_json(self, *, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)
