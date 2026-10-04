"""One-shot import of a legacy ``projects/<name>/`` folder into a project container.

Maps today's layout onto docs/project-container.md:

- ``panels.json`` -> ``script`` + ``panels`` (new stable ids; script page/panel ->
  ``label``, so duplicate script numbers are fine)
- ``panels/NNN*.png`` + sidecar ``.json`` -> ``takes/`` + ``takes{}``: sidecar
  ``source`` -> ``parent``, refine ``upscaler`` -> kind ``refine``, inpaint ``mask``
  -> kind ``inpaint`` (the mask is copied into ``masks/``); no sidecar -> ``import``
- ``gimp_cursor.json`` -> ``cursor``
- ``characters.json`` -> ``cast`` names/aliases only (identity stays in the engine);
  ``projects/identities.json`` maps the new ``project.id`` to the legacy folder, so
  inline renders use the same character registry (``identity.py``)

Legacy history can be broken: take files were overwritten (a refine and an inpaint
once pointed at each other), sources deleted. Such a take is imported as kind
``import`` with an ``engine.import_note`` instead of guessing a history. The source
folder is never modified; take images are copied, so the container's files are its own.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from manganation.project_container import FORMAT, VERSION, integrity_errors
from manganation.script.schema import Script

_TAKE_NAME = re.compile(r"^(\d{3})(?:_.*)?\.png$")


class ImportError_(RuntimeError):  # noqa: N801 - avoid shadowing builtin ImportError
    pass


def new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(6)


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts else datetime.now(UTC).isoformat()


def _sidecar(png: Path) -> dict | None:
    try:
        return json.loads(png.with_suffix(".json").read_text())
    except (OSError, ValueError):
        return None


def import_project(src: Path, dest: Path) -> dict:
    """Convert ``src`` (a legacy project folder) into a new container at ``dest``.

    Returns the manifest. Refuses to write into an existing ``dest``."""
    src, dest = Path(src), Path(dest)
    if not (src / "panels.json").is_file():
        raise ImportError_(f"{src} has no panels.json")
    if dest.exists():
        raise ImportError_(f"{dest} already exists; choose a new destination")
    script = Script.from_json((src / "panels.json").read_text())
    work = dest.with_name(dest.name + ".importing")
    if work.exists():
        shutil.rmtree(work)
    (work / "takes").mkdir(parents=True)

    # --- panels -------------------------------------------------------------
    panel_ids = [new_id("pnl_") for _ in script.panels]
    panels = []
    for pid, p in zip(panel_ids, script.panels, strict=True):
        panels.append({
            "id": pid,
            "label": {"page": p.page, "panel": p.panel},
            "scene_heading": p.scene_heading, "location": p.location,
            "characters": [{"name": c, "version": None} for c in p.characters],
            "action": p.action, "camera": p.camera, "expressions": dict(p.expressions),
            "dialogue": [d.model_dump() for d in p.dialogue], "sfx": list(p.sfx),
            "notes": p.notes, "flashback": p.flashback, "aspect_ratio": p.aspect_ratio,
            "seed": p.seed, "status": "unplaced", "placement": None,
            "takes": [], "active_take": None,
        })

    # --- takes: copy images, map sidecar provenance -------------------------
    pngs = sorted((src / "panels").glob("*.png")) if (src / "panels").is_dir() else []
    by_file: dict[str, str] = {}  # resolved legacy path -> take id
    takes: dict[str, dict] = {}
    skipped: list[str] = []
    for png in pngs:
        m = _TAKE_NAME.match(png.name)
        seq = int(m.group(1)) if m else 0
        if not 1 <= seq <= len(panels):
            skipped.append(png.name)
            continue
        tid = new_id("tk_")
        pid = panel_ids[seq - 1]
        rel = f"takes/{pid}-{tid}.png"
        shutil.copy2(png, work / rel)
        with Image.open(png) as im:
            width, height = im.size
        side = _sidecar(png)
        if side is None:
            kind, engine = "import", {"import_note": f"no sidecar for {png.name}"}
        elif "upscaler" in side:
            kind = "refine"
            engine = {k: side[k] for k in ("upscaler", "denoise", "seed") if k in side}
        elif "mask" in side:
            kind = "inpaint"
            engine = {k: side[k] for k in ("prompt", "positive", "crop", "work_size",
                                           "denoise", "grow_mask_by", "seed") if k in side}
            mask = Path(side["mask"])
            if mask.is_file():
                (work / "masks").mkdir(exist_ok=True)
                shutil.copy2(mask, work / "masks" / f"{tid}.png")
                engine["mask"] = f"masks/{tid}.png"
        else:
            kind = "render"
            engine = {k: side[k] for k in ("seed", "prompt") if k in side}
            if side.get("reference"):
                engine["reference_file"] = Path(side["reference"]).name
        engine["legacy_file"] = png.name
        takes[tid] = {
            "panel": pid, "file": rel, "width": width, "height": height, "kind": kind,
            "parent": None, "origin": tid, "created": _iso(png.stat().st_mtime),
            "engine": engine, "_source": (side or {}).get("source"),
        }
        by_file[str(png.resolve())] = tid
        panels[seq - 1]["takes"].append(tid)

    # Resolve parents, then origins; break bad histories into imports.
    def demote(tid: str, why: str) -> None:
        t = takes[tid]
        t.update(kind="import", parent=None, origin=tid)
        t["engine"] = {"import_note": why, "legacy_file": t["engine"].get("legacy_file")}

    for tid, t in takes.items():
        source = t.pop("_source")
        if t["kind"] in ("refine", "inpaint"):
            parent = by_file.get(str(Path(source).resolve())) if source else None
            if parent is None:
                demote(tid, f"made from {Path(source).name if source else '?'}, "
                            "which no longer exists")
            elif takes[parent]["panel"] != t["panel"]:
                demote(tid, "made from another panel's take")
            else:
                t["parent"] = parent
    for tid, t in takes.items():
        seen, cur = set(), tid
        while takes[cur]["parent"] is not None and cur not in seen:
            seen.add(cur)
            cur = takes[cur]["parent"]
        if cur in seen:  # loop: overwritten files made the history circular
            demote(tid, "take history looped (an older file was overwritten)")
        else:
            t["origin"] = cur
    for tid, t in takes.items():  # a demotion upstream can change origins downstream
        cur = tid
        while takes[cur]["parent"] is not None:
            cur = takes[cur]["parent"]
        t["origin"] = cur

    # Active take: the newest file, the plug-in's "newest file wins" rule.
    for p in panels:
        if p["takes"]:
            p["takes"].sort(key=lambda t: takes[t]["created"])
            p["active_take"] = p["takes"][-1]

    # --- script, cast, cursor ------------------------------------------------
    script_section = {"file": "", "format": "canonical", "parser": {"kind": "imported"}}
    if (src / "script.md").is_file():
        from manganation.script.formats.mangaplay import looks_canonical

        (work / "script").mkdir()
        text = (src / "script.md").read_text()
        shutil.copy2(src / "script.md", work / "script" / "script.md")
        script_section.update(
            file="script/script.md", sha256=hashlib.sha256(text.encode()).hexdigest(),
            format="canonical" if looks_canonical(text) else "prose")
    else:
        script_section = None

    cast = []
    try:
        chars = json.loads((src / "characters.json").read_text())["characters"]
        cast = [{"name": c["name"], "aliases": c.get("aliases", []), "notes": ""}
                for c in chars]
    except (OSError, ValueError, KeyError):
        names = []
        for p in script.panels:
            names += [c for c in p.characters if c not in names]
        cast = [{"name": n, "aliases": [], "notes": ""} for n in names]

    try:
        nxt = json.loads((src / "gimp_cursor.json").read_text())["next"]
    except (OSError, ValueError, KeyError):
        nxt = 1
    now = _iso()
    doc = {
        "format": FORMAT, "version": VERSION,
        "project": {"id": new_id("prj_"), "title": script.title or src.name,
                    "reading_order": script.reading_order.value,
                    "default_color_mode": script.default_color_mode.value,
                    "created": now, "modified": now,
                    "imported_from": src.name},
        "panels": panels, "pages": [], "takes": takes, "cast": cast,
        "locations": [{"name": n} for n in dict.fromkeys(
            p.location for p in script.panels if p.location)],
        "props": [],
        "cursor": {"next_panel": panel_ids[nxt - 1] if 1 <= nxt <= len(panel_ids) else None},
    }
    if script_section:
        doc["script"] = script_section
    if skipped:
        doc["project"]["import_skipped"] = skipped

    errors = integrity_errors(doc, root=work)
    if errors:
        shutil.rmtree(work)
        raise ImportError_("import produced an inconsistent project: " + "; ".join(errors))
    tmp = work / "project.json.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, work / "project.json")
    os.replace(work, dest)  # appears complete or not at all
    from manganation.identity import register

    register(doc["project"]["id"], src.name, root=src.parent)
    return doc
