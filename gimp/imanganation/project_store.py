"""Small, stdlib-only persistence layer for the GIMP-owned project manifest.

The plug-in runs in GIMP's Python environment, so this module deliberately has no
dependency on the engine package or optional JSON Schema validators. Callers keep
the loaded mapping intact and edit only the keys they own; unknown keys survive.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


FORMAT = "imanganation.project"
VERSION = 1
PROJECT_PRESETS = {"oneshot", "manga_series", "color_comic", "digital_comic", "custom"}
_ID_PATTERNS = {
    "prj_": re.compile(r"^prj_[a-z0-9]{6,}$"),
    "pnl_": re.compile(r"^pnl_[a-z0-9]{6,}$"),
    "pg_": re.compile(r"^pg_[a-z0-9]{6,}$"),
    "tk_": re.compile(r"^tk_[a-z0-9]{6,}$"),
}


class ProjectFileError(ValueError):
    """Invalid, unsupported, or unreadable project manifest."""


def new_id(prefix: str) -> str:
    """Return a schema-shaped opaque project, panel, page, or take id."""
    if prefix not in {"prj_", "pnl_", "pg_", "tk_"}:
        raise ValueError(f"unsupported project id prefix: {prefix!r}")
    return prefix + secrets.token_hex(6)


def new_project_document(title: str, *, reading_order: str = "rtl",
                         default_color_mode: str = "color",
                         chapter: str = "", starter_panels: int = 1,
                         page_size: tuple[int, int] = (1512, 2150),
                         resolution: int = 300, page_format: str = "jis_b6",
                         preset: str = "custom") -> dict[str, Any]:
    """Return a valid starter manifest for a project built without a script."""
    title = title.strip() if isinstance(title, str) else ""
    if not title:
        raise ProjectFileError("project title cannot be empty")
    if reading_order not in {"rtl", "ltr"}:
        raise ProjectFileError("reading order must be right-to-left or left-to-right")
    if default_color_mode not in {"color", "bw", "inherit"}:
        raise ProjectFileError("default color mode must be color, bw, or inherit")
    if preset not in PROJECT_PRESETS:
        raise ProjectFileError(f"unsupported project preset: {preset!r}")
    if isinstance(starter_panels, bool) or not isinstance(starter_panels, int) \
            or not 0 <= starter_panels <= 100:
        raise ProjectFileError("starter panel count must be between 0 and 100")
    if (not isinstance(page_size, (tuple, list)) or len(page_size) != 2
            or any(isinstance(value, bool) or not isinstance(value, int)
                   or not 1 <= value <= 20000 for value in page_size)):
        raise ProjectFileError("page dimensions must be between 1 and 20000 pixels")
    if isinstance(resolution, bool) or not isinstance(resolution, int) \
            or not 36 <= resolution <= 1200:
        raise ProjectFileError("page resolution must be between 36 and 1200 PPI")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    project = {
        "id": new_id("prj_"),
        "title": title,
        "reading_order": reading_order,
        "default_color_mode": default_color_mode,
        "preset": preset,
        "page_setup": {"format": page_format, "width": page_size[0],
                       "height": page_size[1], "resolution": resolution},
        "created": now,
        "modified": now,
    }
    if chapter.strip():
        project["chapter"] = chapter.strip()
    panels = [{
        "id": new_id("pnl_"),
        "label": {"page": 1, "panel": number},
        "scene_heading": "",
        "location": "",
        "characters": [],
        "action": "",
        "camera": "",
        "expressions": {},
        "dialogue": [],
        "sfx": [],
        "notes": "",
        "seed": None,
        "status": "unplaced",
        "placement": None,
        "takes": [],
        "active_take": None,
    } for number in range(1, starter_panels + 1)]
    return {
        "format": FORMAT,
        "version": VERSION,
        "project": project,
        "panels": panels,
        "pages": [],
        "takes": {},
        "cast": [],
        "locations": [],
        "props": [],
        "cursor": {"next_panel": panels[0]["id"] if panels else None},
    }


def _relative_manifest_path(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ProjectFileError(f"{field} must be a non-empty relative path using '/' separators")
    path = Path(value)
    if (path.is_absolute() or value.startswith("/") or
            re.match(r"^[A-Za-z]:", value) or ".." in path.parts):
        raise ProjectFileError(f"{field} must stay inside the project folder: {value!r}")


def _check_id(value: Any, prefix: str, field: str) -> None:
    if not isinstance(value, str) or not _ID_PATTERNS[prefix].fullmatch(value):
        raise ProjectFileError(f"{field} must match {prefix}[a-z0-9]{{6,}}")


def _check_references(document: dict[str, Any]) -> None:
    project = document.get("project")
    if not isinstance(project, dict):
        raise ProjectFileError("project must be an object")
    _check_id(project.get("id"), "prj_", "project.id")
    page_setup = project.get("page_setup")
    if page_setup is not None:
        if not isinstance(page_setup, dict):
            raise ProjectFileError("project.page_setup must be an object")
        if not isinstance(page_setup.get("format"), str) or page_setup["format"] not in {
                "jis_b6", "shinsho", "a5", "jis_b5", "us_digest", "digital", "custom"}:
            raise ProjectFileError("project.page_setup.format is unsupported")
        for dimension in ("width", "height"):
            value = page_setup.get(dimension)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 20000:
                raise ProjectFileError(f"project.page_setup.{dimension} must be 1–20000 pixels")
        resolution = page_setup.get("resolution")
        if isinstance(resolution, bool) or not isinstance(resolution, int) \
                or not 36 <= resolution <= 1200:
            raise ProjectFileError("project.page_setup.resolution must be 36–1200 PPI")
    if ("preset" in project and (not isinstance(project["preset"], str)
            or project["preset"] not in PROJECT_PRESETS)):
        raise ProjectFileError("project.preset is unsupported")

    for field in ("script",):
        section = document.get(field)
        if isinstance(section, dict) and "file" in section:
            _relative_manifest_path(section["file"], f"{field}.file")

    pages = document.get("pages")
    if not isinstance(pages, list):
        raise ProjectFileError("pages must be an array")
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise ProjectFileError(f"pages[{index}] must be an object")
        _check_id(page.get("id"), "pg_", f"pages[{index}].id")
        if "file" in page:
            _relative_manifest_path(page["file"], f"pages[{index}].file")

    panels = document.get("panels")
    if not isinstance(panels, list):
        raise ProjectFileError("panels must be an array")
    for index, panel in enumerate(panels):
        if not isinstance(panel, dict):
            raise ProjectFileError(f"panels[{index}] must be an object")
        _check_id(panel.get("id"), "pnl_", f"panels[{index}].id")
        for character in panel.get("characters", []):
            if not isinstance(character, dict) or not character.get("name"):
                raise ProjectFileError(f"panels[{index}].characters entries need a name")

    takes = document.get("takes", {})
    if not isinstance(takes, dict):
        raise ProjectFileError("takes must be an object keyed by take id")
    for take_id, take in takes.items():
        _check_id(take_id, "tk_", f"takes key {take_id!r}")
        if not isinstance(take, dict):
            raise ProjectFileError(f"takes.{take_id} must be an object")
        if "file" in take:
            _relative_manifest_path(take["file"], f"takes.{take_id}.file")
        engine = take.get("engine")
        if isinstance(engine, dict) and "mask" in engine:
            _relative_manifest_path(engine["mask"], f"takes.{take_id}.engine.mask")

    cast = document.get("cast", [])
    if not isinstance(cast, list):
        raise ProjectFileError("cast must be an array")
    for index, character in enumerate(cast):
        if not isinstance(character, dict) or not character.get("name"):
            raise ProjectFileError(f"cast[{index}] entries need a name")
        if {"appearance", "versions", "reference"} & character.keys():
            raise ProjectFileError(f"cast[{index}] cannot contain engine identity or reference data")

    cursor = document.get("cursor")
    if not isinstance(cursor, dict):
        raise ProjectFileError("cursor must be an object")
    next_panel = cursor.get("next_panel")
    if next_panel is not None:
        _check_id(next_panel, "pnl_", "cursor.next_panel")


def load_project(root: str | os.PathLike[str]) -> dict[str, Any]:
    """Read project.json and reject unsupported format/version or unsafe paths."""
    path = Path(root) / "project.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectFileError(f"cannot read {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise ProjectFileError(f"{path} is not an {FORMAT} manifest")
    if document.get("version") != VERSION:
        raise ProjectFileError(f"unsupported project version {document.get('version')!r}")
    required = {"project", "panels", "pages", "takes", "cast", "cursor"}
    missing = sorted(required - document.keys())
    if missing:
        raise ProjectFileError(f"{path} is missing required fields: {', '.join(missing)}")
    _check_references(document)
    return document


def save_project(root: str | os.PathLike[str], document: dict[str, Any]) -> Path:
    """Atomically replace project.json, leaving all unrecognized keys untouched.

    The caller supplies the complete loaded mapping, edits fields it owns, and passes
    it back unchanged otherwise. The temporary filename follows the format contract.
    """
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise ProjectFileError(f"document format must be {FORMAT!r}")
    if document.get("version") != VERSION:
        raise ProjectFileError(f"unsupported project version {document.get('version')!r}")
    required = {"project", "panels", "pages", "takes", "cast", "cursor"}
    missing = sorted(required - document.keys())
    if missing:
        raise ProjectFileError(f"document is missing required fields: {', '.join(missing)}")
    _check_references(document)

    folder = Path(root)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "project.json"
    temporary = folder / "project.json.tmp"
    payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():  # the last good copy, for a bad edit or an interrupted session
            shutil.copy2(target, folder / "project.json.bak")
        os.replace(temporary, target)
        # Directory fsync makes the rename durable on POSIX; some platforms do not
        # support opening or syncing directory handles, so the atomic replace remains
        # the portable guarantee.
        try:
            descriptor = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass
    except OSError as exc:
        raise ProjectFileError(f"cannot write {target}: {exc}") from exc
    return target


def record_take(
    root: str | os.PathLike[str],
    document: dict[str, Any],
    panel_id: str,
    source: str | os.PathLike[str],
    *,
    kind: str,
    width: int,
    height: int,
    engine: dict[str, Any] | None = None,
    parent: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Copy an image to a new immutable take file and atomically record it.

    On success ``document`` is updated in place and the new take becomes active.
    If the manifest write fails, the just-created image is removed because nothing
    references it yet. Existing take files are never opened for writing.
    """
    if kind not in {"render", "refine", "inpaint", "import"}:
        raise ProjectFileError(f"unsupported take kind: {kind!r}")
    if width < 1 or height < 1:
        raise ProjectFileError("take dimensions must be positive")
    if kind in {"refine", "inpaint"} and parent is None:
        raise ProjectFileError(f"{kind} takes need a parent take")
    if kind in {"render", "import"} and parent is not None:
        raise ProjectFileError(f"{kind} takes cannot have a parent")

    candidate = deepcopy(document)
    panels = candidate.get("panels", [])
    panel = next((item for item in panels if item.get("id") == panel_id), None)
    if panel is None:
        raise ProjectFileError(f"unknown panel id {panel_id!r}")
    takes = candidate.get("takes", {})
    if parent is not None and (parent not in takes or takes[parent].get("panel") != panel_id):
        raise ProjectFileError(f"parent take {parent!r} does not belong to panel {panel_id}")

    source_path = Path(source)
    if not source_path.is_file():
        raise ProjectFileError(f"take source is missing: {source_path}")

    folder = Path(root)
    takes_folder = folder / "takes"
    takes_folder.mkdir(parents=True, exist_ok=True)
    extension = source_path.suffix.lower() or ".png"
    if extension not in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        extension = ".png"
    take_id = new_id("tk_")
    destination = takes_folder / f"{panel_id}-{take_id}{extension}"
    relative_file = destination.relative_to(folder).as_posix()
    origin = takes[parent]["origin"] if parent is not None else take_id
    created = datetime.now().astimezone().isoformat(timespec="seconds")
    take = {
        "panel": panel_id,
        "file": relative_file,
        "width": width,
        "height": height,
        "kind": kind,
        "parent": parent,
        "origin": origin,
        "created": created,
        "engine": deepcopy(engine or {}),
    }
    takes[take_id] = take
    panel.setdefault("takes", []).append(take_id)
    panel["active_take"] = take_id
    candidate["project"]["modified"] = created

    try:
        # Exclusive creation is the guard against accidentally reusing a filename.
        with destination.open("xb") as output, source_path.open("rb") as input_stream:
            shutil.copyfileobj(input_stream, output)
            output.flush()
            os.fsync(output.fileno())
        save_project(folder, candidate)
    except (OSError, ProjectFileError) as exc:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, ProjectFileError):
            raise
        raise ProjectFileError(f"cannot record take for {panel_id}: {exc}") from exc

    document.clear()
    document.update(candidate)
    return take, destination


# Context dock fields: "<row id>.<field>" keys, edited in place in the manifest.
DIALOGUE_KINDS = ("speech", "thought", "narration", "shout", "whisper")
_DIALOGUE_LINE = re.compile(
    r"^\s*(?P<speaker>[^\s:(][^:(]*?)\s*(?:\((?P<kind>[^)]*)\))?\s*:\s*(?P<text>.+?)\s*$")
_LINE_FIELD = re.compile(r"^(dialogue|sfx)_(\d+|new)$")


def format_dialogue(line: dict[str, Any]) -> str:
    """A dialogue line as the script writes it: SPEAKER: text, SPEAKER (kind): text."""
    kind = line.get("kind", "speech")
    return (f"{line.get('speaker', '')}"
            + (f" ({kind})" if kind and kind != "speech" else "")
            + f": {line.get('text', '')}")


def parse_dialogue(value: str) -> dict[str, str]:
    match = _DIALOGUE_LINE.match(value)
    if not match:
        raise ProjectFileError("write dialogue as 'Speaker: text' or 'Speaker (thought): "
                               "text'")
    kind = (match.group("kind") or "speech").strip().lower()
    if kind not in DIALOGUE_KINDS:
        raise ProjectFileError(f"unknown kind ({kind}); use one of "
                               f"{', '.join(DIALOGUE_KINDS)}")
    return {"speaker": match.group("speaker").strip(), "text": match.group("text"),
            "kind": kind}


PANEL_TEXT_FIELDS = {"location", "camera", "action", "notes", "aspect_ratio", "size"}
_ASPECT = re.compile(r"^[0-9]+:[0-9]+$")
# Frame hints ([FRAME: ...] in the script); absent = no hint, never a default shape.
PANEL_SIZES = ("small", "large", "splash")
FRAME_SHAPES = {"wide": "2:1", "tall": "1:2", "square": "1:1"}


SHOT_CHOICES = ("extreme close-up", "close-up", "reaction shot", "medium shot", "two-shot",
                "full shot", "wide shot", "establishing shot", "over-the-shoulder",
                "low angle", "high angle", "bird's-eye", "worm's-eye", "dutch angle", "pov")
ASPECT_CHOICES = ("wide", "tall", "square", "16:9", "3:2", "4:3", "1:1", "2:3", "3:4", "9:16")
# Context fields picked from a list (a Choose… button) rather than typed: field -> how
FIELD_CHOICES = {"characters": "many", "props": "many", "location": "one", "camera": "one",
                 "aspect_ratio": "one", "size": "one"}


def field_choices(document: dict[str, Any], panel_id: str, field: str) -> dict[str, Any]:
    """What a panel field can be picked from, for its Choose… dialog:
    ``{"mode": "many" | "one", "options": [...], "current": [...] | str, "label": …}``.

    The options come from the project (its cast, props and locations) or are the fixed
    choices (shots, shapes, sizes); whatever the panel has now is always among them, so
    a value typed before is never lost. ``many`` keeps the panel's own order first (a
    character's place in the frame follows it). Raises ProjectFileError for a field that
    isn't picked from a list."""
    panel = next((p for p in document["panels"] if p["id"] == panel_id), None)
    if panel is None:
        raise ProjectFileError("that panel is no longer in the project")
    mode = FIELD_CHOICES.get(field)
    if mode is None:
        raise ProjectFileError(f"{field!r} is typed, not picked from a list")
    if field == "characters":
        current = [c["name"] for c in panel.get("characters", [])]
        options = [c["name"] for c in document.get("cast", [])]
        label = "Characters"
    elif field == "props":
        current = list(panel.get("props", []))
        options = [p["name"] for p in document.get("props", [])]
        label = "Props"
    else:
        value = panel.get(field) or ""
        if field == "location" and not value:
            value = _location_name(panel.get("scene_heading") or "")
        current = value
        label, options = {
            "location": ("Location", [loc["name"] for loc in document.get("locations", [])]),
            "camera": ("Shot", list(SHOT_CHOICES)),
            "aspect_ratio": ("Aspect ratio", list(ASPECT_CHOICES)),
            "size": ("Frame size", list(PANEL_SIZES)),
        }[field]
    held = current if isinstance(current, list) else ([current] if current else [])
    options = [*held, *(o for o in options if o not in held)]
    return {"mode": mode, "options": options, "current": current, "label": label}


def _names(text: str) -> list[str]:
    seen: dict[str, str] = {}
    for name in (part.strip() for part in text.split(",")):
        if name and name.lower() not in seen:
            seen[name.lower()] = name
    return list(seen.values())


def format_expressions(expressions: dict[str, str]) -> str:
    return "; ".join(f"{who}: {what}" for who, what in expressions.items())


def apply_field_edit(document: dict[str, Any], key: str, value: str,
                     character_id=None, location_id=None, prop_id=None) -> str:
    """Apply one edited Context field to the loaded manifest (the caller saves it).

    ``key`` is ``<panel/page/character/location row id>.<field>``; ``value`` is the
    field's text, collapsed to one line. ``character_id`` maps a cast name to its row
    id, ``location_id`` a location's and ``prop_id`` a prop's. Returns the id of the edited row. Raises
    ProjectFileError on a bad key or value.
    """
    row_id, _, field = key.rpartition(".")
    value = " ".join(value.split())
    panel = next((p for p in document["panels"] if p["id"] == row_id), None)
    if panel is not None:
        if field == "characters":
            cast = document["cast"]
            known = {c["name"].lower(): c["name"] for c in cast}
            for character in cast:
                for alias in character.get("aliases", []):
                    known.setdefault(alias.lower(), character["name"])
            versions = {c["name"]: c.get("version") for c in panel.get("characters", [])}
            names = _names(", ".join(known.get(n.lower(), n) for n in _names(value)))
            for name in names:
                if name.lower() not in known:  # new to the story: add to the cast
                    cast.append({"name": name})
                    known[name.lower()] = name
            panel["characters"] = [{"name": n, "version": versions.get(n)} for n in names]
        elif field == "props":
            known = {prop_key(p["name"]): p["name"] for p in document.get("props", [])}
            names = []
            for part in value.split(","):
                part = part.strip()
                if not part:
                    continue
                name = known.get(prop_key(part), part)
                if name not in names:
                    names.append(name)
                if prop_key(name) not in known:  # new to the story: add to its props
                    document.setdefault("props", []).append({"name": name})
                    known[prop_key(name)] = name
            if names:
                panel["props"] = names
            else:
                panel.pop("props", None)
        elif field == "expressions":
            expressions = {}
            for part in value.split(";"):
                who, colon, what = part.partition(":")
                if not part.strip():
                    continue
                if not colon or not who.strip() or not what.strip():
                    raise ProjectFileError(
                        "write expressions as 'Name: expression; Name: expression'")
                expressions[who.strip()] = what.strip()
            if expressions:
                panel["expressions"] = expressions
            else:
                panel.pop("expressions", None)
        elif _LINE_FIELD.match(field):
            kind, _, which = field.partition("_")
            lines = panel.setdefault("dialogue" if kind == "dialogue" else "sfx", [])
            index = len(lines) if which == "new" else int(which)
            if index >= len(lines) and which != "new":
                raise ProjectFileError("that line is no longer in the panel")
            if not value:  # blank removes the line (a blank new line adds nothing)
                if which != "new":
                    del lines[index]
            else:
                entry = parse_dialogue(value) if kind == "dialogue" else value
                if which == "new":
                    lines.append(entry)
                else:
                    lines[index] = entry
        elif field == "seed":
            if not value:  # blank: a new random seed each render
                panel["seed"] = None
            else:
                if not value.isdigit() or int(value) >= 2**31:
                    raise ProjectFileError("a seed is a whole number below 2147483648; "
                                           "leave it blank for a random one")
                panel["seed"] = int(value)
        elif field in PANEL_TEXT_FIELDS:
            if field == "aspect_ratio" and value:
                value = FRAME_SHAPES.get(value.lower(), value)
                if not _ASPECT.match(value):
                    raise ProjectFileError("aspect ratio must look like 3:2 (or wide, "
                                           "tall, square)")
            if field == "size" and value:
                value = value.lower()
                if value not in PANEL_SIZES:
                    raise ProjectFileError(f"size must be one of {', '.join(PANEL_SIZES)}")
            if value or field == "action":  # action is required, the rest optional
                panel[field] = value
            else:
                panel.pop(field, None)
        else:
            raise ProjectFileError(f"panels have no editable field {field!r}")
        return row_id
    page = next((p for p in document["pages"] if p["id"] == row_id), None)
    if page is not None:
        if field != "label" or not value:
            raise ProjectFileError("a page needs a label")
        page["label"] = value
        return row_id
    character = next((c for c in document["cast"]
                      if character_id is not None and character_id(c["name"]) == row_id),
                     None)
    if character is not None:
        if field == "aliases":
            aliases = _names(value)
            if aliases:
                character["aliases"] = aliases
            else:
                character.pop("aliases", None)
        elif field == "notes":
            if value:
                character["notes"] = value
            else:
                character.pop("notes", None)
        else:
            raise ProjectFileError(f"characters have no editable field {field!r}")
        return row_id
    location = next((loc for loc in document.get("locations", [])
                     if location_id is not None and location_id(loc["name"]) == row_id),
                    None)
    if location is not None:
        if field != "notes":
            raise ProjectFileError(f"locations have no editable field {field!r}")
        if value:
            location["notes"] = value
        else:
            location.pop("notes", None)
        return row_id
    prop = next((p for p in document.get("props", [])
                 if prop_id is not None and prop_id(p["name"]) == row_id), None)
    if prop is not None:
        if field != "notes":
            raise ProjectFileError(f"props have no editable field {field!r}")
        if value:
            prop["notes"] = value
        else:
            prop.pop("notes", None)
        return row_id
    raise ProjectFileError(f"{row_id} is no longer in the project")


def prop_key(name: str) -> str:
    """The engine's key for a prop (manganation/props.py): lower case, no leading
    article. "The Red umbrella" -> "red umbrella"."""
    return re.sub(r"^(?:the|a|an)\s+", "", " ".join(name.split()),
                  flags=re.IGNORECASE).lower()


def find_prop(document: dict[str, Any], name: str) -> dict[str, Any] | None:
    key = prop_key(name)
    return next((p for p in document.get("props", [])
                 if prop_key(p.get("name", "")) == key), None)


def panels_with_prop(document: dict[str, Any], name: str) -> list[dict[str, Any]]:
    key = prop_key(name)
    return [p for p in document["panels"]
            if any(prop_key(n) == key for n in p.get("props", []))]


def add_prop(document: dict[str, Any], name: str, notes: str = "") -> dict[str, Any]:
    """Add a prop to the project's assets (the caller saves)."""
    name = " ".join(name.split())
    if not any(ch.isalnum() for ch in name):
        raise ProjectFileError("a prop needs a name")
    existing = find_prop(document, name)
    if existing is not None:
        raise ProjectFileError(f"{existing['name']} is already a prop")
    prop = {"name": name, **({"notes": " ".join(notes.split())} if notes.strip() else {})}
    document.setdefault("props", []).append(prop)
    return prop


def delete_prop(document: dict[str, Any], name: str) -> tuple[dict[str, Any], list[str]]:
    """Remove a prop from the assets and from every panel that lists it. Returns the
    removed entry and the ids of the panels that changed."""
    prop = find_prop(document, name)
    if prop is None:
        raise ProjectFileError(f"{name} is not one of this project's props")
    changed = []
    for panel in panels_with_prop(document, name):
        panel["props"] = [n for n in panel["props"] if prop_key(n) != prop_key(name)]
        if not panel["props"]:
            panel.pop("props")
        changed.append(panel["id"])
    document["props"].remove(prop)
    return prop, changed


_CONTINUED = re.compile(r"\s*[-—–:,]?\s*\(?\b(?:cont(?:'d|inued)?|contd)\.?\)?\s*$",
                        re.IGNORECASE)
_PLACE_SPLIT = re.compile(r"\s+[—–-]\s+|\s*[,;(]\s*")


def location_key(place: str) -> str:
    """The engine's key for a place (manganation/locations.py): no time, no
    continuation, no article. "School rooftop — dusk" -> "school rooftop"."""
    place = _CONTINUED.sub("", place.strip())
    head = _PLACE_SPLIT.split(place, maxsplit=1)[0]
    head = re.sub(r"^(?:the|a|an)\s+", "", head.strip(), flags=re.IGNORECASE)
    return " ".join(head.lower().split())


def find_location(document: dict[str, Any], name: str) -> dict[str, Any] | None:
    """The project location that is this place (same key, any time of day), or None."""
    key = location_key(name)
    return next((loc for loc in document.get("locations", [])
                 if location_key(loc.get("name", "")) == key), None)


def panels_at_location(document: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Panels set at this place: their location, or else their scene heading."""
    key = location_key(name)
    return [panel for panel in document["panels"]
            if location_key(panel.get("location") or panel.get("scene_heading") or "")
            == key]


def delete_location(document: dict[str, Any], name: str) -> dict[str, Any]:
    """Remove a location from the project's assets. Panels keep their location text
    (it is script text). Returns the removed entry."""
    location = find_location(document, name)
    if location is None:
        raise ProjectFileError(f"{name} is not one of this project's locations")
    document["locations"].remove(location)
    return location


def _location_name(heading: str) -> str:
    """'School rooftop — late afternoon' -> 'School rooftop' (the place, not the time)."""
    return re.split(r"\s+[—–-]\s+", heading.strip(), maxsplit=1)[0].strip()


def project_from_script(parsed: dict[str, Any], *, title: str, script_file: str,
                        script_text: str, script_format: str,
                        reading_order: str = "rtl") -> dict[str, Any]:
    """A new project manifest from a parsed script (``script_canonical.parse`` output or
    the engine's parse result: ``{"cast": [...], "panels": [...]}``).

    Panels start unplaced; the caller adds pages (one per script page) and saves.
    Cast entries keep the script's description as their notes; locations come from
    scene headings and ``CUT TO:`` targets.
    """
    import hashlib

    if not parsed.get("panels"):
        raise ProjectFileError("the script has no panels")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    panels = [_panel_from_item(item) for item in parsed["panels"]]
    cast = _merge_cast([], parsed, panels)
    locations = _merge_locations([], parsed, panels)
    return {
        "format": FORMAT,
        "version": VERSION,
        "project": {"id": new_id("prj_"), "title": title, "reading_order": reading_order,
                    "default_color_mode": "color", "created": now, "modified": now},
        "script": {"file": script_file, "format": script_format, "parsed_at": now,
                   "sha256": hashlib.sha256(script_text.encode("utf-8")).hexdigest(),
                   "parser": {"kind": "plug-in" if script_format == "canonical"
                              else "engine"}},
        "panels": panels,
        "pages": [],
        "takes": {},
        "cast": cast,
        "locations": locations,
        "props": [],
        "cursor": {"next_panel": panels[0]["id"]},
    }


# A panel's script content: what the script says, not production state (takes,
# placement) nor its numbering, which shifts when panels are added before it.
_SCRIPT_FIELDS = ("scene_heading", "location", "characters", "action", "camera",
                  "expressions", "dialogue", "sfx", "notes", "flashback", "cover",
                  "aspect_ratio", "size")


def script_fingerprint(panel: dict[str, Any]) -> str:
    """A short hash of a panel's script content, so a re-parse can tell an unchanged
    panel from an edited one wherever it now sits."""
    import hashlib

    content = {
        "scene_heading": panel.get("scene_heading") or "",
        "location": panel.get("location") or "",
        "characters": [c["name"] if isinstance(c, dict) else str(c)
                       for c in panel.get("characters", [])],
        "action": panel.get("action") or "",
        "camera": panel.get("camera") or "",
        "expressions": dict(panel.get("expressions") or {}),
        "dialogue": [[d.get("speaker", ""), d.get("text", ""), d.get("kind") or "speech"]
                     for d in panel.get("dialogue", [])],
        "sfx": list(panel.get("sfx", [])),
        "notes": panel.get("notes") or "",
        "flashback": bool(panel.get("flashback", False)),
        "cover": bool(panel.get("cover", False)),
        "aspect_ratio": panel.get("aspect_ratio") or "",
        "size": panel.get("size") or "",
    }
    text = json.dumps(content, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _panel_from_item(item: dict[str, Any]) -> dict[str, Any]:
    """A new, unplaced manifest panel from one parsed script panel."""
    panel = {
        "id": new_id("pnl_"),
        "label": {"page": int(item.get("page", 1)), "panel": int(item.get("panel", 1))},
        "scene_heading": item.get("scene_heading", ""),
        "location": item.get("location", ""),
        "characters": [{"name": name, "version": None}
                       for name in item.get("characters", [])],
        **({"props": list(item["props"])} if item.get("props") else {}),
        "action": item.get("action", ""),
        "camera": item.get("camera", ""),
        "expressions": dict(item.get("expressions") or {}),
        "dialogue": [{"speaker": d["speaker"], "text": d["text"],
                      "kind": d.get("kind", "speech")}
                     for d in item.get("dialogue", [])],
        "sfx": list(item.get("sfx", [])),
        "notes": item.get("notes", ""),
        "flashback": bool(item.get("flashback", False)),
        **({"cover": True} if item.get("cover") else {}),
        **{key: item[key] for key in ("aspect_ratio", "size") if item.get(key)},
        "seed": item.get("seed"),
        "status": "unplaced",
        "placement": None,
        "takes": [],
        "active_take": None,
    }
    panel["source"] = script_fingerprint(panel)
    return panel


def _merge_cast(cast: list[dict[str, Any]], parsed: dict[str, Any],
                panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The cast plus anyone new in the script. Existing entries keep their notes; one
    without notes takes the script's description."""
    by_name = {}
    for entry in cast:
        for name in (entry["name"], *entry.get("aliases", [])):
            by_name.setdefault(name.lower(), entry)
    for entry in parsed.get("cast", []):
        existing = by_name.get(entry["name"].lower())
        if existing is not None:
            if entry.get("description") and not existing.get("notes"):
                existing["notes"] = entry["description"]
            continue
        record: dict[str, Any] = {"name": entry["name"],
                                  "aliases": list(entry.get("aliases", []))}
        if entry.get("description"):
            record["notes"] = entry["description"]
        cast.append(record)
        by_name[entry["name"].lower()] = record
    for panel in panels:  # characters the cast block did not declare
        for character in panel["characters"]:
            if character["name"].lower() not in by_name:
                record = {"name": character["name"], "aliases": []}
                cast.append(record)
                by_name[character["name"].lower()] = record
    return cast


def _merge_locations(locations: list[dict[str, Any]], parsed: dict[str, Any],
                     panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The locations plus any place new in the script. Declared places (the script's
    ``[LOCATIONS]`` block) come first and bring their description as notes, which the
    location's design is drawn from; an existing location keeps its notes, or takes the
    script's description when it has none. Other places come from scene headings."""
    by_key = {location_key(loc["name"]): loc for loc in locations}
    for entry in parsed.get("locations", []):
        existing = by_key.get(location_key(entry["name"]))
        if existing is not None:
            if entry.get("description") and not existing.get("notes"):
                existing["notes"] = entry["description"]
            continue
        record: dict[str, Any] = {"name": entry["name"]}
        if entry.get("description"):
            record["notes"] = entry["description"]
        locations.append(record)
        by_key[location_key(entry["name"])] = record
    for name in _script_locations(panels):
        if location_key(name) not in by_key:
            record = {"name": name}
            locations.append(record)
            by_key[location_key(name)] = record
    return locations


def _script_locations(panels: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(
        name for panel in panels
        for name in (_location_name(panel.get("location", "")),
                     _location_name(panel.get("scene_heading", "")))
        if name))


def adopt_script_fingerprints(document: dict[str, Any], parsed: dict[str, Any]) -> bool:
    """Record each panel's script fingerprint from the text it was parsed from, for a
    project made before fingerprints (or by an older parser, whose panels differ from
    a fresh parse). Only when the parse lines up with the panels exactly, same count
    and numbering in order; the caller checks the text is unchanged since parsing.
    -> True if anything was recorded (the caller saves)."""
    current = [p for p in document["panels"] if p.get("status") != "orphaned"]
    items = parsed.get("panels", [])
    if all(p.get("source") for p in current) or len(items) != len(current):
        return False
    labels = [{"page": int(i.get("page", 1)), "panel": int(i.get("panel", 1))}
              for i in items]
    if labels != [p.get("label") for p in current]:
        return False
    for panel, item in zip(current, items, strict=True):
        panel["source"] = _panel_from_item(item)["source"]
    return True


def _has_work(panel: dict[str, Any]) -> bool:
    return bool(panel.get("takes") or panel.get("active_take") or panel.get("placement"))


def reparse_script(document: dict[str, Any], parsed: dict[str, Any], *, script_file: str,
                   script_text: str, script_format: str) -> dict[str, list[str]]:
    """Bring the project up to an edited script, in place (the caller saves).

    A panel whose script content is unchanged (same fingerprint, wherever it now sits)
    is kept whole: id, takes, placement, Context edits, only its numbering updated.
    Every other script panel is new. Old panels that no longer match keep their work
    as ``orphaned`` (Needs matching, for Match selected); untouched ones are dropped.
    Never maps an edited panel by its label or position (docs/project-container.md).
    New cast members and locations are added; existing ones are kept.

    Panels added by hand (``manual``) are not part of the script and are carried
    through, after the last panel of their page.

    Returns panel ids: {"kept", "added", "orphaned", "removed"}."""
    import hashlib

    if not parsed.get("panels"):
        raise ProjectFileError("the script has no panels")
    manual = [p for p in document["panels"] if p.get("manual")]
    current = [p for p in document["panels"]
               if p.get("status") != "orphaned" and not p.get("manual")]
    orphans = [p for p in document["panels"]
               if p.get("status") == "orphaned" and not p.get("manual")]
    available: dict[str, list[dict[str, Any]]] = {}
    for panel in current:
        available.setdefault(panel.get("source") or script_fingerprint(panel),
                             []).append(panel)

    panels, kept, added = [], [], []
    for item in parsed["panels"]:
        fresh = _panel_from_item(item)
        candidates = available.get(fresh["source"])
        if candidates:
            panel = candidates.pop(0)  # identical content: the first, in reading order
            panel["label"] = fresh["label"]
            panel["source"] = fresh["source"]
            panels.append(panel)
            kept.append(panel["id"])
        else:
            panels.append(fresh)
            added.append(fresh["id"])

    orphaned, removed = [], []
    for panel in current:
        if panel["id"] in kept:
            continue
        if _has_work(panel):
            panel["status"] = "orphaned"
            orphans.append(panel)
            orphaned.append(panel["id"])
        else:
            removed.append(panel["id"])

    for panel in manual:  # hand-added panels are not in the script: they stay put
        if panel.get("cover") and any(p.get("cover") for p in panels):
            if _has_work(panel):  # the script has a cover of its own now
                panel["status"] = "orphaned"
                panel.pop("manual")
                orphans.append(panel)
                orphaned.append(panel["id"])
            else:
                removed.append(panel["id"])
            continue
        panels.insert(_page_end(panels, panel["label"]["page"]), panel)
    document["panels"] = panels + orphans
    document["cast"] = _merge_cast(document.get("cast", []), parsed, panels)
    document["locations"] = _merge_locations(document.get("locations", []), parsed, panels)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    document["script"] = {
        **document.get("script", {}), "file": script_file, "format": script_format,
        "parsed_at": now,
        "sha256": hashlib.sha256(script_text.encode("utf-8")).hexdigest(),
        "parser": {"kind": "plug-in" if script_format == "canonical" else "engine"}}
    ids = {p["id"] for p in panels}
    cursor = document.setdefault("cursor", {})
    if cursor.get("next_panel") not in ids:
        cursor["next_panel"] = next((p["id"] for p in panels if not _has_work(p)),
                                    panels[0]["id"])
    return {"kept": kept, "added": added, "orphaned": orphaned, "removed": removed}


_DEFAULT_PAGE_LABEL = re.compile(r"^Page\s+(\d+)$", re.IGNORECASE)


def _first_page_number(document: dict[str, Any]) -> int | None:
    numbers = [int(m.group(1)) for page in document["pages"]
               if (m := _DEFAULT_PAGE_LABEL.match(page.get("label", "")))]
    return min(numbers) if numbers else None


def _renumber_pages(document: dict[str, Any], first: int | None) -> None:
    """Default "Page N" labels follow the page order again, counting from ``first``
    (the lowest such number before the change: a script starting at page 7 keeps
    7, 8, ...); custom labels stay."""
    if first is None:
        return
    number = first
    for page in document["pages"]:
        if _DEFAULT_PAGE_LABEL.match(page.get("label", "")):
            page["label"] = f"Page {number}"
            number += 1


def reorder_pages(document: dict[str, Any], dragged: str, target: str) -> None:
    """Move page ``dragged`` to the position of page ``target`` (the page strip's
    drag and drop: dropped on a later page it goes after it, on an earlier one before)."""
    pages = document["pages"]
    ids = [page["id"] for page in pages]
    if dragged not in ids or target not in ids:
        raise ProjectFileError("that page is no longer in the project")
    first = _first_page_number(document)
    page = pages.pop(ids.index(dragged))
    pages.insert(ids.index(target), page)
    _renumber_pages(document, first)


def find_cast_member(document: dict[str, Any], name: str) -> dict[str, Any] | None:
    """The cast entry with this name or alias (any case), or None."""
    target = name.strip().casefold()
    return next((c for c in document["cast"]
                 if target in {n.casefold() for n in (c["name"], *c.get("aliases", []))}),
                None)


def panels_with_character(document: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Panels that list this cast member (by name or alias) among their characters."""
    member = find_cast_member(document, name)
    spellings = {n.casefold() for n in (member["name"], *member.get("aliases", []))} \
        if member else {name.casefold()}
    return [panel for panel in document["panels"]
            if any(c.get("name", "").casefold() in spellings
                   for c in panel.get("characters", []))]


def delete_character(document: dict[str, Any], name: str) -> tuple[dict[str, Any], list[str]]:
    """Remove a character from the cast and from every panel that lists them (their
    expressions go too; dialogue lines are script text and stay). Takes are kept.
    Returns the removed cast entry and the ids of the panels that changed."""
    member = find_cast_member(document, name)
    if member is None:
        raise ProjectFileError(f"{name} is not in this project's cast")
    spellings = {n.casefold() for n in (member["name"], *member.get("aliases", []))}
    changed = []
    for panel in panels_with_character(document, member["name"]):
        panel["characters"] = [c for c in panel["characters"]
                               if c.get("name", "").casefold() not in spellings]
        changed.append(panel["id"])
    for panel in document["panels"]:
        expressions = panel.get("expressions") or {}
        kept = {who: what for who, what in expressions.items()
                if who.casefold() not in spellings}
        if kept != expressions:
            if kept:
                panel["expressions"] = kept
            else:
                panel.pop("expressions", None)
            if panel["id"] not in changed:
                changed.append(panel["id"])
    document["cast"].remove(member)
    return member, changed


def delete_page(document: dict[str, Any], page_id: str) -> tuple[str, list[str]]:
    """Remove a page from the project. Panels placed on it become unplaced (their takes
    are kept). Returns the page's file (relative; the caller disposes of it) and the
    ids of the panels that were unplaced."""
    page = next((p for p in document["pages"] if p["id"] == page_id), None)
    if page is None:
        raise ProjectFileError("that page is no longer in the project")
    first = _first_page_number(document)
    unplaced = []
    for panel in document["panels"]:
        if (panel.get("placement") or {}).get("page") == page_id:
            panel["placement"] = None
            panel["status"] = "unplaced"
            unplaced.append(panel["id"])
    document["pages"].remove(page)
    _renumber_pages(document, first)
    return page.get("file", ""), unplaced


def _page_end(panels: list[dict[str, Any]], page_number: int) -> int:
    """Where a new panel of script page ``page_number`` goes in ``panels`` (reading
    order, no orphans): after that page's last panel, else before the first later page,
    else at the end."""
    index = None
    for i, panel in enumerate(panels):
        number = panel.get("label", {}).get("page", 0)
        if number == page_number:
            index = i + 1
        elif number > page_number and index is None:
            return i
    return len(panels) if index is None else index


def script_page_number(document: dict[str, Any], page_id: str) -> int:
    """The script page number a project page stands for: its "Page N" label, else the
    page its placed panels are labelled with, else its position counting from the
    first numbered page. The cover page is 0."""
    pages = document["pages"]
    page = next((p for p in pages if p["id"] == page_id), None)
    if page is None:
        raise ProjectFileError("that page is no longer in the project")
    if match := _DEFAULT_PAGE_LABEL.match(page.get("label", "")):
        return int(match.group(1))
    if page.get("cover") or page.get("label", "").strip().casefold() == "cover":
        return 0
    numbers = [p["label"]["page"] for p in document["panels"]
               if (p.get("placement") or {}).get("page") == page_id
               and p["label"]["page"] > 0]
    if numbers:
        return max(set(numbers), key=numbers.count)
    return (_first_page_number(document) or 1) + pages.index(page)


def add_panel(document: dict[str, Any], page_number: int, *, action: str,
              location: str = "", characters: list[str] | None = None,
              camera: str = "") -> dict[str, Any]:
    """Add a hand-written panel at the end of script page ``page_number`` (the caller
    saves). It is numbered after that page's last panel, unplaced, and marked
    ``manual`` so Reload script keeps it. New names join the cast."""
    action = " ".join(action.split())
    if not action:
        raise ProjectFileError("a panel needs an action")
    if page_number < 1:
        raise ProjectFileError("a panel needs a page number of 1 or more; "
                               "the cover is added with Add cover")
    live = [p for p in document["panels"] if p.get("status") != "orphaned"]
    same_page = [p["label"]["panel"] for p in live if p["label"]["page"] == page_number]
    panel = _panel_from_item({
        "page": page_number, "panel": max(same_page, default=0) + 1,
        "location": " ".join(location.split()), "action": action,
        "camera": " ".join(camera.split()),
        "characters": _names(", ".join(characters or []))})
    return _insert_manual(document, live, panel)


def add_cover(document: dict[str, Any], *, action: str, location: str = "",
              characters: list[str] | None = None, camera: str = "") -> dict[str, Any]:
    """Add a hand-written cover picture (label page 0), first in reading order (the
    caller saves). A project has one cover. Like the script's COVER it has no
    dialogue or sound effects."""
    action = " ".join(action.split())
    if not action:
        raise ProjectFileError("the cover needs an action: what its picture shows")
    live = [p for p in document["panels"] if p.get("status") != "orphaned"]
    if any(p.get("cover") for p in live):
        raise ProjectFileError("this project already has a cover")
    panel = _panel_from_item({
        "page": 0, "panel": 1, "cover": True,
        "location": " ".join(location.split()), "action": action,
        "camera": " ".join(camera.split()),
        "characters": _names(", ".join(characters or []))})
    return _insert_manual(document, live, panel)


def _insert_manual(document: dict[str, Any], live: list[dict[str, Any]],
                   panel: dict[str, Any]) -> dict[str, Any]:
    panel["manual"] = True
    live.insert(_page_end(live, panel["label"]["page"]), panel)
    document["panels"] = live + [p for p in document["panels"]
                                 if p.get("status") == "orphaned"]
    known = {c["name"].casefold() for c in document["cast"]}
    for entry in panel["characters"]:
        if entry["name"].casefold() not in known:
            document["cast"].append({"name": entry["name"], "aliases": [], "notes": ""})
            known.add(entry["name"].casefold())
    if panel["location"] and find_location(document, panel["location"]) is None:
        document.setdefault("locations", []).append({"name": panel["location"]})
    cursor = document.setdefault("cursor", {})
    if not cursor.get("next_panel"):
        cursor["next_panel"] = panel["id"]
    return panel


def duplicate_panel(document: dict[str, Any], panel_id: str) -> dict[str, Any]:
    """Copy a panel's brief (cast, place, shot, action, dialogue, SFX, notes) as a new,
    unplaced panel at the end of the same script page, for a variation or a second beat
    (the caller saves). The copy is ``manual``, so Reload script keeps it; takes,
    placement and the seed are not copied."""
    source = next((p for p in document["panels"] if p["id"] == panel_id), None)
    if source is None:
        raise ProjectFileError("that panel is no longer in the project")
    if source.get("cover"):
        raise ProjectFileError("a project has one cover; add pages for more pictures")
    live = [p for p in document["panels"] if p.get("status") != "orphaned"]
    page = source["label"]["page"]
    copy = _panel_from_item({
        **deepcopy(source),
        "page": page,
        "panel": max((p["label"]["panel"] for p in live if p["label"]["page"] == page),
                     default=0) + 1,
        "characters": [c["name"] for c in source.get("characters", [])],
        "seed": None,
    })
    for index, entry in enumerate(source.get("characters", [])):
        copy["characters"][index]["version"] = entry.get("version")
    return _insert_manual(document, live, copy)


def delete_panel(document: dict[str, Any], panel_id: str) -> dict[str, Any]:
    """Remove a hand-added panel that has no work on it (the caller saves)."""
    panel = next((p for p in document["panels"] if p["id"] == panel_id), None)
    if panel is None:
        raise ProjectFileError("that panel is no longer in the project")
    if not panel.get("manual"):
        raise ProjectFileError("only panels added by hand can be deleted; "
                               "script panels come from the script")
    if _has_work(panel):
        raise ProjectFileError("this panel has takes or a placement; "
                               "remove that work first")
    document["panels"].remove(panel)
    cursor = document.get("cursor", {})
    if cursor.get("next_panel") == panel_id:
        cursor["next_panel"] = next((p["id"] for p in document["panels"]
                                     if p.get("status") != "orphaned"), None)
    return panel


def retarget_character_version(document: dict[str, Any], name: str, old: str,
                               new: str | None) -> list[str]:
    """After a reference version is renamed (``new``) or deleted (``None``: back to the
    character's default), point every panel that pinned ``old`` at it (the caller
    saves). Returns the ids of the panels that changed."""
    member = find_cast_member(document, name)
    spellings = {n.casefold() for n in
                 ((member["name"], *member.get("aliases", [])) if member else (name,))}
    changed = []
    for panel in document["panels"]:
        for entry in panel.get("characters", []):
            if entry.get("name", "").casefold() in spellings and entry.get("version") == old:
                entry["version"] = new
                changed.append(panel["id"])
    return changed


def set_character_version(document: dict[str, Any], panel_id: str, index: int,
                          version: str | None) -> dict[str, Any]:
    """Pin which reference image (version) the panel's ``index``-th character uses, or
    ``None`` for the character's default (the caller saves). Returns that entry."""
    panel = next((p for p in document["panels"] if p["id"] == panel_id), None)
    if panel is None:
        raise ProjectFileError("that panel is no longer in the project")
    characters = panel.get("characters", [])
    if not 0 <= index < len(characters):
        raise ProjectFileError("that character is no longer in the panel")
    characters[index]["version"] = version or None
    return characters[index]


def set_active_take(document: dict[str, Any], panel_id: str, take_id: str) -> dict[str, Any]:
    """Make one of the panel's takes the active one (the caller saves). Returns the take."""
    panel = next((p for p in document["panels"] if p["id"] == panel_id), None)
    if panel is None:
        raise ProjectFileError("that panel is no longer in the project")
    if take_id not in panel.get("takes", []) or take_id not in document.get("takes", {}):
        raise ProjectFileError("that take no longer belongs to the panel")
    panel["active_take"] = take_id
    return document["takes"][take_id]
