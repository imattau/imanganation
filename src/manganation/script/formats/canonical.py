"""Deterministic parser for the canonical page/panel manga script format.

Standard library only: the engine wraps the result in its pydantic models
(:mod:`manganation.script.formats.mangaplay`), and the GIMP plug-in imports this same
file (``gimp/imanganation/script_canonical.py`` is a symlink to it) to build a project
from a script without the engine. Free-form prose goes through the engine's LLM
parser instead (:mod:`manganation.script.parser`).

Grammar (line-oriented, case-insensitive tokens):

    CHARACTERS                                    -> optional cast block, before
    AKIRA: 17, boy, messy black hair, ...            the first page: one entry per
      indented lines continue the description        character, NAME (aka A, B):
    YUKI (aka Yuki-chan): 16, girl, silver bob       description
    PAGE 7
    [SCENE: School rooftop — afternoon]
    [FLASHBACK START] / [FLASHBACK END]
    Panel 1: Wide shot. Akira sits alone, eating lunch.
    AKIRA: Finally, some peace and quiet.        -> dialogue
    AKIRA (thought): ...                          -> thought
    SFX: BANG                                     -> sfx
    CUT TO: the classroom                         -> transition (new panel hint)
    [[any note]]                                  -> notes

A panel's characters are its speakers plus any known character (declared in the
cast block, by name or alias, or speaking anywhere in the script) named in its
action text.

Output (plain dicts, keys as in ``PanelSpec`` / ``CastEntry``)::

    {"cast": [{"name", "aliases", "description"}],
     "panels": [{"page", "panel", "scene_heading", "location", "characters", "action",
                 "camera", "dialogue": [{"speaker", "text", "kind"}], "sfx", "notes",
                 "flashback"}]}
"""

from __future__ import annotations

import re

# --- line recognisers -------------------------------------------------------

_PAGE_RE = re.compile(r"^\s*PAGE\s+(\d+)\s*$", re.IGNORECASE)
_PANEL_RE = re.compile(r"^\s*Panel\s+(\d+)\s*[:\-]\s*(.*)$", re.IGNORECASE)
_SCENE_RE = re.compile(r"^\s*\[SCENE\s*:\s*(.*?)\]\s*$", re.IGNORECASE)
_FLASH_START_RE = re.compile(r"^\s*\[FLASHBACK\s+START\]\s*$", re.IGNORECASE)
_FLASH_END_RE = re.compile(r"^\s*\[FLASHBACK\s+END\]\s*$", re.IGNORECASE)
_CUT_RE = re.compile(r"^\s*CUT\s+TO\s*:\s*(.*)$", re.IGNORECASE)
_SFX_RE = re.compile(r"^\s*SFX\s*:\s*(.*)$", re.IGNORECASE)
_NOTE_RE = re.compile(r"^\s*\[\[(.*?)\]\]\s*$", re.IGNORECASE)
_CAST_RE = re.compile(r"^\s*(?:CHARACTERS|CAST)\s*:?\s*$", re.IGNORECASE)
# NAME (aka Other, Another): description — unindented; indented lines continue it
_CAST_ENTRY_RE = re.compile(
    r"^(?P<name>[A-Za-z][\w .'\-]{0,30}?)"
    r"(?:\s*\((?:aka|a\.k\.a\.?)\s+(?P<aliases>[^)]*)\))?"
    r"\s*:\s*(?P<description>.*?)\s*$",
    re.IGNORECASE,
)
# SPEAKER (kind): text  — speaker is 1-4 words, kind optional
_DIALOGUE_RE = re.compile(
    r"^\s*(?P<speaker>[A-Za-z][\w .'\-]{0,30}?)"
    r"(?:\s*\((?P<kind>thought|whisper|shout|narration|speech)\))?"
    r"\s*:\s*(?P<text>.+?)\s*$"
)
_KINDS = {"thought", "whisper", "shout", "narration", "speech"}
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


def _detect_camera(text: str) -> str:
    low = text.lower()
    for pat in _CAMERA_PATTERNS:
        if pat in low:
            return pat
    return ""


def canonical_name(name: str) -> str:
    """Normalise a speaker/character name to a stable display form.

    Manga scripts write speakers in ALL CAPS (``AKIRA:``); we store ``Akira`` so
    names match across prose and canonical inputs and key the character registry
    consistently.
    """
    name = name.strip()
    if name.isupper():
        return name.title()
    return name


def looks_canonical(text: str) -> bool:
    """Heuristic: does this script already use the token grammar?"""
    return bool(
        _PAGE_RE.search(text)
        or _PANEL_RE.search(text)
        or re.search(r"^\s*Panel\s+\d+", text, re.IGNORECASE | re.MULTILINE)
    )


def _ends_cast(line: str) -> bool:
    return bool(_PAGE_RE.match(line) or _PANEL_RE.match(line) or _SCENE_RE.match(line))


def split_cast(text: str) -> tuple[list[dict], str]:
    """Take the ``CHARACTERS`` block off the front of a script.

    Returns the declared cast and the script without the block (its lines blanked in
    place, so the rest parses exactly as before). The block ends at the first page,
    panel or scene token.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if _ends_cast(line):
            break
        if _CAST_RE.match(line):
            start = index
            break
    if start is None:
        return [], text
    entries: list[dict] = []
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if _ends_cast(line):
            end = index
            break
        if not line.strip():
            continue
        m = _CAST_ENTRY_RE.match(line) if not line[:1].isspace() else None
        if m:
            aliases = [a.strip() for a in (m.group("aliases") or "").split(",") if a.strip()]
            entries.append({"name": canonical_name(m.group("name")), "aliases": aliases,
                            "parts": [m.group("description")]})
        elif entries:
            entries[-1]["parts"].append(line.strip())
    cast: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        if entry["name"].lower() in seen:
            continue
        seen.add(entry["name"].lower())
        cast.append({"name": entry["name"], "aliases": entry["aliases"],
                     "description": " ".join(p for p in entry["parts"] if p).strip()})
    rest = lines[:start] + [""] * (end - start) + lines[end:]
    return cast, "\n".join(rest)


def add_mentions(cast: list[dict], panels: list[dict]) -> None:
    """Add known characters named in each panel's action text to its characters.

    Known: the declared cast (names and aliases) and everyone who speaks somewhere in
    the script. Names match as whole words, as written or in capitals, so "Akira's"
    counts but a lowercase common word never does. Mentions go after the speakers,
    which keep their order. Edits ``panels`` in place.
    """
    known: dict[str, str] = {}  # spelling -> character name
    for entry in cast:
        for spelling in (entry["name"], *entry.get("aliases", [])):
            known.setdefault(spelling, entry["name"])
    for panel in panels:
        for line in panel.get("dialogue", []):
            known.setdefault(line["speaker"], line["speaker"])
    if not known:
        return
    spellings = sorted(known, key=len, reverse=True)  # "Yuki-chan" before "Yuki"
    pattern = re.compile(
        r"(?<![\w-])(" + "|".join(
            re.escape(s) + "|" + re.escape(s.upper()) for s in spellings) + r")(?![\w-])")
    by_upper = {s.upper(): name for s, name in known.items()}
    for panel in panels:
        characters = panel.setdefault("characters", [])
        for m in pattern.finditer(panel.get("action", "")):
            name = known.get(m.group(1)) or by_upper[m.group(1).upper()]
            if name not in characters:
                characters.append(name)


def parse(text: str) -> dict:
    """Parse a token-grammar script into ``{"cast": [...], "panels": [...]}``.

    Raises :class:`ValueError` if no panels are found.
    """
    cast, text = split_cast(text)
    panels: list[dict] = []
    current: dict | None = None
    page = 1
    scene = ""
    flashback = False
    pending_cut = ""

    def flush() -> None:
        nonlocal current
        if current is not None:
            action = " ".join(p.strip() for p in current.pop("action_parts")
                              if p.strip()).strip()
            current["action"] = action
            current["camera"] = _detect_camera(action)
            current["notes"] = " ".join(current["notes"]).strip()
            panels.append(current)
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
                current["scene_heading"] = m.group(1).strip()
            else:
                scene = m.group(1).strip()
            continue

        if _FLASH_START_RE.match(line):
            flashback = True
            if current is not None:
                current["flashback"] = True
            continue
        if _FLASH_END_RE.match(line):
            flashback = False
            continue

        m = _PANEL_RE.match(line)
        if m:
            flush()
            current = {"page": page, "panel": int(m.group(1)), "scene_heading": scene,
                       "location": pending_cut, "characters": [], "action_parts": [],
                       "dialogue": [], "sfx": [], "notes": [], "flashback": flashback}
            pending_cut = ""
            rest = m.group(2).strip()
            if rest:
                current["action_parts"].append(rest)
            continue

        m = _CUT_RE.match(line)
        if m:
            # A transition annotates the NEXT panel's location; it is not a panel.
            pending_cut = m.group(1).strip()
            continue

        m = _NOTE_RE.match(line)
        if m:
            if current is not None:
                current["notes"].append(m.group(1).strip())
            continue

        if current is None:
            # Free text before any panel: keep as scene/heading context.
            scene = scene or line.strip()
            continue

        m = _SFX_RE.match(line)
        if m:
            current["sfx"].append(m.group(1).strip())
            continue

        d = _DIALOGUE_RE.match(line)
        if d and d.group("speaker").strip().lower() not in _NON_DIALOGUE_TOKENS:
            speaker = canonical_name(d.group("speaker"))
            kind = (d.group("kind") or "speech").lower()
            current["dialogue"].append({"speaker": speaker, "text": d.group("text"),
                                        "kind": kind if kind in _KINDS else "speech"})
            if speaker and speaker not in current["characters"]:
                current["characters"].append(speaker)
            continue

        # Otherwise: descriptive action text for the current panel.
        current["action_parts"].append(line.strip())

    flush()

    if not panels:
        raise ValueError("no panels found in canonical script")
    add_mentions(cast, panels)
    return {"cast": cast, "panels": panels}
