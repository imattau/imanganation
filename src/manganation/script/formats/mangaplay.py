"""Deterministic parser for the canonical page/panel manga script format.

This is the *fast path*: when a script already follows the token grammar it is
parsed with plain Python (no LLM), which is instant and lossless. Free-form
prose goes through the Ollama parser instead (:mod:`manganation.script.parser`).

Grammar (line-oriented, case-insensitive tokens):

    PAGE 7
    [SCENE: School rooftop — afternoon]
    [FLASHBACK START] / [FLASHBACK END]
    Panel 1: Wide shot. Akira sits alone, eating lunch.
    AKIRA: Finally, some peace and quiet.        -> dialogue
    AKIRA (thought): ...                          -> thought
    SFX: BANG                                     -> sfx
    CUT TO: the classroom                         -> transition (new panel hint)
    [[any note]]                                  -> notes
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from manganation.script.schema import ColorMode, DialogueLine, PanelSpec, ReadingOrder, Script

DialogueKind = Literal["speech", "thought", "narration", "shout", "whisper"]

# --- line recognisers -------------------------------------------------------

_PAGE_RE = re.compile(r"^\s*PAGE\s+(\d+)\s*$", re.IGNORECASE)
_PANEL_RE = re.compile(r"^\s*Panel\s+(\d+)\s*[:\-]\s*(.*)$", re.IGNORECASE)
_SCENE_RE = re.compile(r"^\s*\[SCENE\s*:\s*(.*?)\]\s*$", re.IGNORECASE)
_FLASH_START_RE = re.compile(r"^\s*\[FLASHBACK\s+START\]\s*$", re.IGNORECASE)
_FLASH_END_RE = re.compile(r"^\s*\[FLASHBACK\s+END\]\s*$", re.IGNORECASE)
_CUT_RE = re.compile(r"^\s*CUT\s+TO\s*:\s*(.*)$", re.IGNORECASE)
_SFX_RE = re.compile(r"^\s*SFX\s*:\s*(.*)$", re.IGNORECASE)
_NOTE_RE = re.compile(r"^\s*\[\[(.*?)\]\]\s*$", re.IGNORECASE)
# SPEAKER (kind): text  — speaker is 1-4 words, kind optional
_DIALOGUE_RE = re.compile(
    r"^\s*(?P<speaker>[A-Za-z][\w .'\-]{0,30}?)"
    r"(?:\s*\((?P<kind>thought|whisper|shout|narration|speech)\))?"
    r"\s*:\s*(?P<text>.+?)\s*$"
)
_KIND_ALIASES: dict[str, DialogueKind] = {
    "thought": "thought",
    "whisper": "whisper",
    "shout": "shout",
    "narration": "narration",
    "speech": "speech",
}
# tokens that look like SPEAKER: but are not spoken dialogue
_NON_DIALOGUE_TOKENS = {"scene", "sfx", "panel", "page", "cut to", "cut"}

# camera / shot hints we can recognise inside a panel's action text
_CAMERA_PATTERNS = [
    "extreme close-up",
    "close-up",
    "wide shot",
    "medium shot",
    "full shot",
    "establishing shot",
    "over-the-shoulder",
    "over the shoulder",
    "bird's-eye",
    "worm's-eye",
    "low angle",
    "high angle",
    "dutch angle",
    "pov",
    "two-shot",
    "reaction shot",
]


@dataclass
class _PanelBuilder:
    page: int
    panel: int
    scene: str = ""
    location: str = ""
    action_parts: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    dialogue: list[DialogueLine] = field(default_factory=list)
    sfx: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    flashback: bool = False
    color_mode: ColorMode = ColorMode.INHERIT
    camera: str = ""

    def action_text(self) -> str:
        return " ".join(p.strip() for p in self.action_parts if p.strip()).strip()

    def build(self) -> PanelSpec:
        action = self.action_text()
        camera = self.camera or _detect_camera(action)
        return PanelSpec(
            page=self.page,
            panel=self.panel,
            scene_heading=self.scene,
            location=self.location,
            characters=list(self.characters),
            action=action,
            camera=camera,
            dialogue=list(self.dialogue),
            sfx=list(self.sfx),
            notes=" ".join(self.notes).strip(),
            flashback=self.flashback,
            color_mode=self.color_mode,
        )


def _detect_camera(text: str) -> str:
    low = text.lower()
    for pat in _CAMERA_PATTERNS:
        if pat in low:
            return pat
    return ""


def _canonical_name(name: str) -> str:
    """Normalise a speaker/character name to a stable display form.

    Manga scripts write speakers in ALL CAPS (``AKIRA:``); we store ``Akira`` so
    names match across prose and canonical inputs and key the character registry
    consistently.
    """
    name = name.strip()
    if name.isupper():
        return name.title()
    return name


def _add_character(builder: _PanelBuilder, name: str) -> None:
    name = _canonical_name(name)
    if name and name not in builder.characters:
        builder.characters.append(name)


def looks_canonical(text: str) -> bool:
    """Heuristic: does this script already use the token grammar?"""
    return bool(
        _PAGE_RE.search(text)
        or _PANEL_RE.search(text)
        or re.search(r"^\s*Panel\s+\d+", text, re.IGNORECASE | re.MULTILINE)
    )


def parse_canonical(
    text: str,
    *,
    title: str = "",
    reading_order: ReadingOrder = ReadingOrder.RTL,
    default_color_mode: ColorMode = ColorMode.COLOR,
) -> Script:
    """Parse a token-grammar script into a validated :class:`Script`.

    Raises :class:`ValueError` if no panels are found.
    """
    panels: list[PanelSpec] = []
    current: _PanelBuilder | None = None
    page = 1
    scene = ""
    flashback = False
    pending_cut = ""

    def flush() -> None:
        nonlocal current
        if current is not None:
            panels.append(current.build())
            current = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue

        m = _PAGE_RE.match(line)
        if m:
            flush()
            page = int(m.group(1))
            continue

        m = _SCENE_RE.match(line)
        if m:
            if current is not None:
                current.scene = m.group(1).strip()
            else:
                scene = m.group(1).strip()
            continue

        if _FLASH_START_RE.match(line):
            flashback = True
            if current is not None:
                current.flashback = True
            continue
        if _FLASH_END_RE.match(line):
            flashback = False
            continue

        m = _PANEL_RE.match(line)
        if m:
            flush()
            current = _PanelBuilder(
                page=page,
                panel=int(m.group(1)),
                scene=scene,
                flashback=flashback,
                location=pending_cut,
            )
            pending_cut = ""
            rest = m.group(2).strip()
            if rest:
                current.action_parts.append(rest)
            continue

        m = _CUT_RE.match(line)
        if m:
            # A transition annotates the NEXT panel's location; it is not a panel.
            pending_cut = m.group(1).strip()
            continue

        m = _NOTE_RE.match(line)
        if m:
            if current is not None:
                current.notes.append(m.group(1).strip())
            continue

        if current is None:
            # Free text before any panel: keep as scene/heading context.
            scene = scene or line.strip()
            continue

        m = _SFX_RE.match(line)
        if m:
            current.sfx.append(m.group(1).strip())
            continue

        d = _DIALOGUE_RE.match(line)
        if d and d.group("speaker").strip().lower() not in _NON_DIALOGUE_TOKENS:
            speaker = _canonical_name(d.group("speaker"))
            kind = _KIND_ALIASES.get((d.group("kind") or "speech").lower(), "speech")
            current.dialogue.append(DialogueLine(speaker=speaker, text=d.group("text"), kind=kind))
            _add_character(current, speaker)
            continue

        # Otherwise: descriptive action text for the current panel.
        current.action_parts.append(line.strip())

    flush()

    if not panels:
        raise ValueError("no panels found in canonical script")

    return Script(
        title=title,
        reading_order=reading_order,
        default_color_mode=default_color_mode,
        panels=panels,
    )
