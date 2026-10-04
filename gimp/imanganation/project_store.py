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
