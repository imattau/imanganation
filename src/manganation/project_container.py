"""Reference integrity checks for the GIMP-owned project container (docs/project-container.md).

``docs/project-container.schema.json`` covers shapes; these are the cross-references a
JSON Schema can't express. They are the rules that broke in practice when take files
were overwritten: take histories that loop, or point at images that are gone.
Pure Python, no dependencies, so the engine, tests and an importer can all use it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from manganation.script.schema import PanelSpec

FORMAT = "imanganation.project"
VERSION = 1


def integrity_errors(doc: dict, root: Path | None = None) -> list[str]:
    """Problems with a manifest's cross-references; empty if it's consistent.

    With ``root`` (the project folder), also checks that referenced files exist."""
    errors: list[str] = []
    if doc.get("format") != FORMAT:
        errors.append(f"format is {doc.get('format')!r}, expected {FORMAT!r}")
    if doc.get("version") != VERSION:
        errors.append(f"version {doc.get('version')!r} not supported (expected {VERSION})")

    panels = doc.get("panels", [])
    takes = doc.get("takes", {})
    pages = {p["id"] for p in doc.get("pages", [])}
    panel_ids = [p["id"] for p in panels]

    for dup in sorted({i for i in panel_ids if panel_ids.count(i) > 1}):
        errors.append(f"panel id {dup} is used more than once")
    page_list = [p["id"] for p in doc.get("pages", [])]
    for dup in sorted({i for i in page_list if page_list.count(i) > 1}):
        errors.append(f"page id {dup} is used more than once")

    owned: dict[str, str] = {}
    for p in panels:
        for tid in p.get("takes", []):
            if tid not in takes:
                errors.append(f"panel {p['id']} lists unknown take {tid}")
            elif tid in owned:
                errors.append(f"take {tid} is listed by panels {owned[tid]} and {p['id']}")
            else:
                owned[tid] = p["id"]
        active = p.get("active_take")
        if active is not None and active not in p.get("takes", []):
            errors.append(f"panel {p['id']}: active_take {active} is not one of its takes")
        placement = p.get("placement")
        if p.get("status") == "placed" and not placement:
            errors.append(f"panel {p['id']} is placed but has no placement")
        if placement and placement.get("page") not in pages:
            errors.append(f"panel {p['id']} is placed on unknown page {placement.get('page')}")

    for tid, t in takes.items():
        if t.get("panel") not in panel_ids:
            errors.append(f"take {tid} belongs to unknown panel {t.get('panel')}")
        elif owned.get(tid) != t["panel"]:
            errors.append(f"take {tid} is not listed by its panel {t['panel']}")
        # Walk parent links to the root: no loops, ends at the declared origin.
        seen, cur = set(), tid
        while True:
            if cur in seen:
                errors.append(f"take {tid}: parent chain loops")
                break
            seen.add(cur)
            node = takes.get(cur)
            if node is None:
                errors.append(f"take {tid}: parent chain reaches unknown take {cur}")
                break
            if node.get("parent") is None:
                if cur != t.get("origin"):
                    errors.append(f"take {tid}: origin is {t.get('origin')}, "
                                  f"but its chain starts at {cur}")
                elif node.get("kind") not in ("render", "import"):
                    errors.append(f"take {tid}: origin {cur} is a {node.get('kind')}, "
                                  "not a render or import")
                break
            parent = takes.get(node["parent"])
            if parent is not None and parent.get("panel") != node.get("panel"):
                errors.append(f"take {cur} derives from {node['parent']} of another panel")
            cur = node["parent"]

    nxt = doc.get("cursor", {}).get("next_panel")
    if nxt is not None and nxt not in panel_ids:
        errors.append(f"cursor points at unknown panel {nxt}")

    if root is not None:
        files = [t["file"] for t in takes.values()] + [p["file"] for p in doc.get("pages", [])]
        files += [t["engine"]["mask"] for t in takes.values()
                  if t.get("kind") == "inpaint" and t.get("engine", {}).get("mask")]
        if doc.get("script", {}).get("file"):
            files.append(doc["script"]["file"])
        errors += [f"missing file: {f}" for f in files if not (root / f).is_file()]
    return errors


_SPEC_FIELDS = ("scene_heading", "location", "action", "camera", "expressions", "dialogue",
                "sfx", "notes", "flashback", "cover", "aspect_ratio", "size", "seed", "poses")


def panel_to_spec(panel: dict) -> tuple[PanelSpec, dict[str, str]]:
    """A container panel -> the engine's ``PanelSpec`` plus ``{character: version}``.

    Characters are ``[{"name", "version"}]`` in the container (``version`` optional,
    null = the active reference) and plain names in ``PanelSpec``. Raises ``ValueError``
    (pydantic) for an invalid spec."""
    from manganation.script.schema import PanelSpec

    label = panel.get("label") or {}
    names, versions = [], {}
    for c in panel.get("characters", []):
        name = c["name"] if isinstance(c, dict) else str(c)
        names.append(name)
        if isinstance(c, dict) and c.get("version"):
            versions[name] = c["version"]
    spec = PanelSpec(
        page=label.get("page", 1), panel=label.get("panel", 1), characters=names,
        **{k: panel[k] for k in _SPEC_FIELDS if k in panel and panel[k] is not None},
    )
    return spec, versions
