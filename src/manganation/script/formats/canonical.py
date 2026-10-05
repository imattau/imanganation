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
    [FRAME: wide, large]               shape (wide / tall / square / 3:2) and size
                                       (small / large / splash), either optional
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
plus known characters named in its action, minus anyone the action puts off-panel
("Yuki calls from off-panel"); without ``[SHOT: ...]`` the shot is read from the action.

Output (plain dicts, keys as in ``PanelSpec`` / ``CastEntry``)::

    {"cast": [{"name", "aliases", "description"}],
     "panels": [{"page", "panel", "scene_heading", "location", "characters", "action",
                 "camera", "expressions", "dialogue": [{"speaker", "text", "kind"}],
                 "sfx", "notes", "flashback", "aspect_ratio" (None = no hint), "size"}],
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
# "Yuki calls from off-panel": the nearest name before the phrase (else after) is heard,
# not seen.
_OFF_PANEL_RE = re.compile(
    r"\b(?:off[- ]?(?:panel|screen|frame|camera)|offscreen|out of (?:frame|shot|view))\b"
    r"|\(o\.s\.\)", re.IGNORECASE)
_JOINED_RE = re.compile(r"\s*(?:,|&|\band\b)\s*", re.IGNORECASE)
KINDS = ("speech", "thought", "whisper", "shout", "narration")
SECTIONS = {"ACTION": "action", "DIALOGUE": "dialogue", "DIALOG": "dialogue",
            "SFX": "sfx", "NOTES": "notes", "NOTE": "notes"}
FIELDS = {"SHOT", "CHARACTERS", "EXPRESSIONS", "LOCATION", "FRAME"}
# [FRAME: ...] words. A shape is stored as the panel's aspect ratio (width:height), so a
# word and a written ratio mean the same thing to layout ranking and rendering.
FRAME_SHAPES = {"wide": "2:1", "tall": "1:2", "square": "1:1"}
FRAME_SIZES = ("small", "large", "splash")
_RATIO_RE = re.compile(r"^(\d+)\s*:\s*(\d+)$")

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


def split_joined(name: str, lookup) -> list[str]:
    """``"Yuki and Akira"`` -> ``["Yuki", "Akira"]`` when every part is a known name
    (``lookup``: spelling -> name or None); otherwise the name as it is. An LLM, or a
    writer, sometimes lists a pair as one character."""
    if lookup(name) is not None:
        return [lookup(name)]
    parts = [lookup(part) for part in _JOINED_RE.split(name) if part.strip()]
    return parts if len(parts) > 1 and None not in parts else [name]


def _off_panel(action: str, mentions: re.Pattern, name_of) -> set[str]:
    """Names the action puts off-panel: per sentence, the mention nearest before an
    off-panel phrase, or the first after it ("From off-panel, Yuki calls")."""
    off: set[str] = set()
    for sentence in re.split(r"(?<=[.!?;])\s+", action):
        found = [(m.start(), name_of(m.group(1))) for m in mentions.finditer(sentence)]
        for marker in _OFF_PANEL_RE.finditer(sentence):
            before = [n for at, n in found if at < marker.start()]
            after = [n for at, n in found if at > marker.start()]
            if before or after:
                off.add(before[-1] if before else after[0])
    return off


def add_mentions(cast: list[dict], panels: list[dict]) -> None:
    """For panels without an explicit character list (``characters_given`` false), add
    known characters named in the action: the cast (names and aliases) and everyone who
    speaks somewhere (not narration). Whole words, as written or in capitals. Anyone
    the action puts off-panel is left out (they may still speak), and a joined entry
    ("Yuki and Akira", from an LLM) is split into its known names. Edits ``panels`` in
    place."""
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
    by_fold = {s.casefold(): name for s, name in known.items()}

    def name_of(spelling: str) -> str:
        return known.get(spelling) or by_upper[spelling.upper()]

    for panel in panels:
        characters = []
        for entry in panel.get("characters", []):
            for name in split_joined(entry, lambda s: by_fold.get(s.casefold())):
                if name not in characters:
                    characters.append(name)
        panel["characters"] = characters
        if panel.get("characters_given"):
            continue
        action = panel.get("action", "")
        for m in pattern.finditer(action):
            name = name_of(m.group(1))
            if name not in characters:
                characters.append(name)
        off = _off_panel(action, pattern, name_of)
        panel["characters"] = [c for c in characters if c not in off]


def parse_frame(value: str) -> tuple[str | None, str, list[str]]:
    """``"wide, large"`` -> (aspect ratio or None, size or "", problem messages)."""
    aspect, size, problems = None, "", []
    for part in (p.strip().lower() for p in value.split(",")):
        if not part:
            continue
        ratio = _RATIO_RE.match(part)
        if part in FRAME_SHAPES or (ratio and int(ratio[1]) and int(ratio[2])):
            if aspect is not None:
                problems.append(f"[FRAME] has two shapes ({part!r} after {aspect})")
            aspect = FRAME_SHAPES.get(part) or f"{int(ratio[1])}:{int(ratio[2])}"
        elif part in FRAME_SIZES:
            if size:
                problems.append(f"[FRAME] has two sizes ({part!r} after {size!r})")
            size = part
        else:
            problems.append(f"Unknown frame hint {part!r}: use wide, tall, square or a "
                            f"ratio like 3:2, and small, large or splash")
    return aspect, size, problems


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
    labels: dict[tuple[int, int], int] = {}  # (page, panel) -> line it was first used

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
            label = (page, int(m.group(1)))
            if label in labels:
                problem(number, f"PAGE {page} PANEL {label[1]} is already used on line "
                                f"{labels[label]} (a new PAGE missing, or a repeated "
                                f"number?)")
            labels.setdefault(label, number)
            current = {"page": page, "panel": int(m.group(1)), "scene_heading": scene,
                       "location": "", "characters": [], "characters_given": False,
                       "camera": "", "expressions": {}, "action_lines": [],
                       "dialogue": [], "sfx": [], "notes": [], "flashback": flashback,
                       "aspect_ratio": None, "size": ""}
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
                elif name == "FRAME":
                    aspect, size, messages = parse_frame(value)
                    current["aspect_ratio"], current["size"] = aspect, size
                    for message in messages:
                        problem(number, message)
                elif name == "CHARACTERS":
                    current["characters_given"] = True
                    names = []
                    for who in (canonical_name(n) for n in value.split(",") if n.strip()):
                        names += split_joined(who, lambda s: resolve(s) if resolve(s)
                                              in declared else None) if declared else [who]
                    for who in names:
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
