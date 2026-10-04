"""Parser for imanganation scripts: pages of panels, each panel in labelled sections.

Standard library only: the engine wraps the result in its pydantic models
(:mod:`manganation.script.formats.mangaplay`), and the GIMP plug-in imports this same
file (``gimp/imanganation/script_canonical.py`` is a symlink to it) to build a project
from a script without the engine. Free-form prose (no ``PAGE`` / ``PANEL`` lines) goes
through the engine's LLM parser instead (:mod:`manganation.script.parser`).

The format (user guide: docs/script-template.md)::

    [CHARACTERS]                       optional, before the first page
    MIO: 15, girl, teal bob, ...       NAME (aka Alias, Other): description;
      indented lines continue it

    PAGE 1
    [SCENE: Harbor pier — dawn]        for the panels after it
    [FLASHBACK START] / [FLASHBACK END]

    PANEL 1
    [SHOT: wide shot]                  one-line fields
    [CHARACTERS: Mio, Kaito]           exactly who is in the picture
    [EXPRESSIONS: Mio: grin; Kaito: bored]
    [LOCATION: the boat]
    [ACTION]                           sections: every line until the next header
    Mio runs along the pier.           is of that kind (colons are just text)
    [DIALOGUE]
    MIO: Hurry up!                     SPEAKER: text / SPEAKER (kind): text
    KAITO (thought): Why me.           kinds: thought, whisper, shout, narration
    [SFX]
    CREAK                              one sound per line
    [NOTES]
    Keep the earring visible.

Sections need no end marker (``[END DIALOGUE]`` is accepted). Nothing is guessed: a
line the format does not allow is reported in ``problems`` with its line number.
Without ``[CHARACTERS: ...]`` a panel's characters are its speakers (not narration)
plus known characters named in its action; without ``[SHOT: ...]`` the shot is read
from the action.

Output (plain dicts, keys as in ``PanelSpec`` / ``CastEntry``)::

    {"cast": [{"name", "aliases", "description"}],
     "panels": [{"page", "panel", "scene_heading", "location", "characters", "action",
                 "camera", "expressions", "dialogue": [{"speaker", "text", "kind"}],
                 "sfx", "notes", "flashback"}],
     "problems": [{"line": n, "message": "..."}]}
"""

from __future__ import annotations

import re

_PAGE_RE = re.compile(r"^\s*PAGE\s+(\d+)\s*$", re.IGNORECASE)
_PANEL_RE = re.compile(r"^\s*PANEL\s+(\d+)\s*$", re.IGNORECASE)
# [NAME] or [NAME: value]
_HEADER_RE = re.compile(r"^\s*\[\s*([A-Za-z][A-Za-z ]*?)\s*(?::\s*(.*?))?\s*\]\s*$")
_DIALOGUE_RE = re.compile(
    r"^\s*(?P<speaker>[^\s:(][^:(]*?)\s*(?:\((?P<kind>[^)]*)\))?\s*:\s*(?P<text>.+?)\s*$")
_CAST_ENTRY_RE = re.compile(
    r"^(?P<name>[^\s:(][^:(]*?)\s*(?:\((?:aka|a\.k\.a\.?)\s+(?P<aliases>[^)]*)\))?"
    r"\s*:\s*(?P<description>.*?)\s*$", re.IGNORECASE)
KINDS = ("speech", "thought", "whisper", "shout", "narration")
SECTIONS = {"ACTION": "action", "DIALOGUE": "dialogue", "DIALOG": "dialogue",
            "SFX": "sfx", "NOTES": "notes", "NOTE": "notes"}
FIELDS = {"SHOT", "CHARACTERS", "EXPRESSIONS", "LOCATION"}

# shots recognised in the action when a panel has no [SHOT: ...]
_CAMERA_PATTERNS = [
    "extreme close-up", "close-up", "wide shot", "medium shot", "full shot",
    "establishing shot", "over-the-shoulder", "over the shoulder", "bird's-eye",
    "worm's-eye", "low angle", "high angle", "dutch angle", "pov", "two-shot",
    "reaction shot",
]


def _detect_camera(text: str) -> str:
    low = text.lower()
    return next((pattern for pattern in _CAMERA_PATTERNS if pattern in low), "")


def canonical_name(name: str) -> str:
    """``AKIRA`` -> ``Akira`` (scripts write names in capitals); others as written."""
    name = " ".join(name.split())
    return name.title() if name.isupper() else name


def looks_canonical(text: str) -> bool:
    """Is this a page-and-panel script (rather than prose for the LLM)?"""
    return any(_PAGE_RE.match(line) or _PANEL_RE.match(line) for line in text.splitlines())


def split_cast(text: str) -> tuple[list[dict], str]:
    """The ``[CHARACTERS]`` block at the top -> (cast, the rest with the block blanked,
    so line numbers stay true). Used on its own for prose scripts."""
    cast, _problems, rest = _read_cast(text.splitlines())
    return cast, "\n".join(rest)


def _read_cast(lines: list[str]) -> tuple[list[dict], list[dict], list[str]]:
    start = None
    for index, line in enumerate(lines):
        if _PAGE_RE.match(line) or _PANEL_RE.match(line):
            break
        header = _HEADER_RE.match(line)
        if header and header.group(1).upper() == "CHARACTERS" and header.group(2) is None:
            start = index
            break
    if start is None:
        return [], [], lines
    entries: list[dict] = []
    problems: list[dict] = []
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if _PAGE_RE.match(line) or _PANEL_RE.match(line) or _HEADER_RE.match(line):
            end = index
            break
        if not line.strip():
            continue
        m = _CAST_ENTRY_RE.match(line) if not line[:1].isspace() else None
        if m:
            aliases = [canonical_name(a) for a in (m.group("aliases") or "").split(",")
                       if a.strip()]
            entries.append({"name": canonical_name(m.group("name")), "aliases": aliases,
                            "parts": [m.group("description")], "line": index + 1})
        elif entries and line[:1].isspace():  # only indented lines continue
            entries[-1]["parts"].append(line.strip())
        else:
            problems.append({"line": index + 1, "message":
                             "Expected a character, NAME: description (indent a line to "
                             "continue the description above)"})
    cast: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        if entry["name"].casefold() in seen:
            problems.append({"line": entry["line"],
                             "message": f"{entry['name']} is declared twice"})
            continue
        seen.add(entry["name"].casefold())
        cast.append({"name": entry["name"], "aliases": entry["aliases"],
                     "description": " ".join(p for p in entry["parts"] if p).strip()})
    return cast, problems, lines[:start] + [""] * (end - start) + lines[end:]


def _resolver(cast: list[dict]):
    """name or alias (any case) -> the cast name; unknown names pass through."""
    known = {}
    for entry in cast:
        for spelling in (entry["name"], *entry.get("aliases", [])):
            known.setdefault(spelling.casefold(), entry["name"])
    return lambda name: known.get(name.casefold(), name)


def add_mentions(cast: list[dict], panels: list[dict]) -> None:
    """For panels without an explicit character list (``characters_given`` false), add
    known characters named in the action: the cast (names and aliases) and everyone who
    speaks somewhere (not narration). Whole words, as written or in capitals. Edits
    ``panels`` in place."""
    known: dict[str, str] = {}
    for entry in cast:
        for spelling in (entry["name"], *entry.get("aliases", [])):
            known.setdefault(spelling, entry["name"])
    for panel in panels:
        for line in panel.get("dialogue", []):
            if line.get("kind") != "narration":
                known.setdefault(line["speaker"], line["speaker"])
    if not known:
        return
    spellings = sorted(known, key=len, reverse=True)  # "Yuki-chan" before "Yuki"
    pattern = re.compile(r"(?<![\w-])(" + "|".join(
        re.escape(s) + "|" + re.escape(s.upper()) for s in spellings) + r")(?![\w-])")
    by_upper = {s.upper(): name for s, name in known.items()}
    for panel in panels:
        if panel.get("characters_given"):
            continue
        characters = panel.setdefault("characters", [])
        for m in pattern.finditer(panel.get("action", "")):
            name = known.get(m.group(1)) or by_upper[m.group(1).upper()]
            if name not in characters:
                characters.append(name)


def parse(text: str) -> dict:
    """Parse a script -> ``{"cast", "panels", "problems"}``. Raises ValueError only if
    there is no panel at all."""
    lines = text.splitlines()
    cast, problems, lines = _read_cast(lines)
    resolve = _resolver(cast)
    declared = {c["name"] for c in cast}
    panels: list[dict] = []
    current: dict | None = None
    section: str | None = None
    page: int | None = None
    scene = ""
    flashback = False

    def problem(number, message):
        problems.append({"line": number, "message": message})

    def finish():
        nonlocal current
        if current is None:
            return
        current["action"] = " ".join(current.pop("action_lines")).strip()
        current["notes"] = " ".join(current["notes"]).strip()
        current["camera"] = current["camera"] or _detect_camera(current["action"])
        panels.append(current)
        current = None

    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        m = _PAGE_RE.match(line)
        if m:
            finish()
            page, section = int(m.group(1)), None
            continue
        m = _PANEL_RE.match(line)
        if m:
            finish()
            section = None
            if page is None:
                problem(number, "PANEL before any PAGE (assumed PAGE 1)")
                page = 1
            current = {"page": page, "panel": int(m.group(1)), "scene_heading": scene,
                       "location": "", "characters": [], "characters_given": False,
                       "camera": "", "expressions": {}, "action_lines": [],
                       "dialogue": [], "sfx": [], "notes": [], "flashback": flashback}
            continue
        header = _HEADER_RE.match(line)
        if header:
            name, value = header.group(1).upper(), header.group(2)
            name = " ".join(name.split())
            if name.startswith("END ") and value is None:
                if section and SECTIONS.get(name[4:]) == section:
                    section = None
                else:
                    problem(number, f"[{name}] does not close an open section")
                continue
            if name == "SCENE" and value is not None:
                if current is not None:
                    finish()  # a scene heading ends the panel before it
                scene, section = value, None
                continue
            if name in ("FLASHBACK START", "FLASHBACK END") and value is None:
                if current is not None:
                    finish()
                flashback, section = name == "FLASHBACK START", None
                continue
            if current is None:
                problem(number, f"[{header.group(1)}] belongs inside a PANEL")
                continue
            if name in SECTIONS and value is None:
                section = SECTIONS[name]
                continue
            if name in FIELDS and value is not None:
                section = None
                if name == "SHOT":
                    current["camera"] = value.lower()
                elif name == "LOCATION":
                    current["location"] = value
                elif name == "CHARACTERS":
                    current["characters_given"] = True
                    for who in (canonical_name(n) for n in value.split(",") if n.strip()):
                        who = resolve(who)
                        if declared and who not in declared:
                            problem(number, f"{who} is not in the [CHARACTERS] block")
                        if who not in current["characters"]:
                            current["characters"].append(who)
                else:  # EXPRESSIONS: Name: expression; Name: expression
                    for part in value.split(";"):
                        who, colon, what = part.partition(":")
                        if not part.strip():
                            continue
                        if not colon or not who.strip() or not what.strip():
                            problem(number, "Write expressions as Name: expression; "
                                            "Name: expression")
                            continue
                        current["expressions"][resolve(canonical_name(who))] = what.strip()
                continue
            problem(number, f"Unknown header [{header.group(1)}]"
                    + (" (sections take no value)" if name in SECTIONS else
                       " (needs a value, e.g. [SHOT: wide shot])" if name in FIELDS else ""))
            continue
        # an ordinary line: it belongs to the open section
        if current is None:
            problem(number, "Text outside a panel (start one with PANEL n)"
                    if page is not None else "Text before the first PAGE")
            continue
        if section is None:
            problem(number, "Text outside a section: put it under [ACTION], [DIALOGUE], "
                            "[SFX] or [NOTES]")
            continue
        if section == "action":
            current["action_lines"].append(line)
        elif section == "sfx":
            current["sfx"].append(line)
        elif section == "notes":
            current["notes"].append(line)
        else:
            m = _DIALOGUE_RE.match(line)
            kind = (m.group("kind") or "speech").strip().lower() if m else ""
            if not m or kind not in KINDS:
                problem(number, "Dialogue lines are SPEAKER: text or SPEAKER (kind): text"
                        if not m else
                        f"Unknown kind ({kind}); use one of {', '.join(KINDS)}")
                continue
            speaker = resolve(canonical_name(m.group("speaker")))
            current["dialogue"].append({"speaker": speaker, "text": m.group("text"),
                                        "kind": kind})
            if kind != "narration":
                if declared and speaker not in declared:
                    problem(number, f"{speaker} speaks but is not in the [CHARACTERS] block")
                if not current["characters_given"] and speaker not in current["characters"]:
                    current["characters"].append(speaker)
    finish()
    if not panels:
        raise ValueError("no panels found: start each panel with PANEL n under a PAGE n")
    add_mentions(cast, panels)
    for panel in panels:
        panel.pop("characters_given", None)
    return {"cast": cast, "panels": panels, "problems": problems}
