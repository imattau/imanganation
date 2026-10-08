"""Text models for the host-rendered Imanganation project docks."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:  # Installed plug-in imports siblings as top-level modules.
    from layouts import page_layout_availability
    from project_store import location_key as _location_key
except ImportError:  # Package import in tests and external tooling.
    from .layouts import page_layout_availability
    from .project_store import location_key as _location_key


def _label(value: Any) -> str:
    """Keep host row delimiters and line breaks out of visible labels."""
    return " ".join(str(value or "").replace("\t", " ").splitlines()).strip()


def _field(row_id: str, field: str, title: str, value: Any) -> str:
    """An editable properties row; edits come back as ``<row id>.<field>``."""
    return f"@{row_id}.{field}\t{title}\t{_label(value)}"


def _expressions(expressions: dict[str, Any]) -> str:
    return "; ".join(f"{_label(who)}: {_label(what)}" for who, what in expressions.items())


def _panel_label(panel: dict[str, Any]) -> str:
    label = panel.get("label", {})
    number = f"Page {label.get('page', '?')} · Panel {label.get('panel', '?')}"
    action = _label(panel.get("action"))
    if len(action) > 72:
        action = action[:69].rstrip() + "…"
    return f"{number}  —  {action}" if action else number


def _script_panel_label(panel: dict[str, Any]) -> str:
    label = panel.get("label", {})
    title = f"Page {label.get('page', '?')} · Panel {label.get('panel', '?')}"
    action = _label(panel.get("action"))
    dialogue = panel.get("dialogue", [])
    if dialogue:
        first = dialogue[0]
        line = _label(f"{first.get('speaker', '')}: {first.get('text', '')}")
        action = f"{action} · {line}" if action else line
    if len(action) > 176:
        action = action[:173].rstrip() + "…"
    return f"{title} — {action}" if action else title


def _menu(*items: tuple[str, str]) -> str:
    """A row's right-click menu suffix, "\t!proc:Label|proc2:Label"; items without a
    procedure are left out, and no items means no menu."""
    entries = "|".join(f"{proc}:{label}" for proc, label in items if proc)
    return f"\t!{entries}" if entries else ""


def character_row_id(name: str) -> str:
    """Build a collision-safe row key from the engine's character identity name."""
    return "character:" + quote(name, safe="")


def location_row_id(name: str) -> str:
    """A location's row key; the engine finds its image by the place's name."""
    return "location:" + quote(name, safe="")


def rgb_png(width: int, height: int, pixels: bytes) -> bytes | None:
    """Encode packed RGB8 pixels as a small standards-compliant PNG."""
    if width < 1 or height < 1 or len(pixels) != width * height * 3:
        return None
    stride = width * 3
    scanlines = b"".join(b"\0" + pixels[row * stride:(row + 1) * stride]
                         for row in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        content = kind + payload
        return (struct.pack(">I", len(payload)) + content
                + struct.pack(">I", zlib.crc32(content)))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(scanlines, 6)) + chunk(b"IEND", b""))


def build_docks(manifest: dict[str, Any], selected_id: str | None = None,
                root: str | Path | None = None,
                previews: dict[str, str] | None = None,
                open_page_action: str = "",
                generate_layout_action: str = "",
                design_action: str = "", new_character_action: str = "",
                design_character_menu: str = "", delete_page_action: str = "",
                delete_character_menu: str = "",
                reorder_pages_action: str = "", bubble_line_action: str = "",
                bubbled: frozenset = frozenset(),
                new_bubble_action: str = "",
                design_location_action: str = "", new_location_action: str = "",
                design_location_menu: str = "",
                delete_location_menu: str = "") -> dict[str, str]:
    """Build generic host content and stable selections from a project manifest.

    The Context (inspector) rows are editable fields; ``open_page_action``, a dock
    procedure, adds an "Open page" button there for placed panels and pages.
    ``generate_layout_action`` adds a page-specific layout action when a matching
    built-in arrangement is available, and ``design_action`` a "Design character"
    button for characters (their notes are the description). In the Project tree,
    ``new_character_action`` puts "New character…" on the Characters heading's
    right-click menu, and ``design_character_menu`` puts "Design character" on each
    character's (both one-string dock procedures); ``delete_character_menu`` adds
    "Delete character…" there and on the Character Bible's rows. ``delete_page_action`` puts
    "Delete page…" on page rows and tiles, and ``reorder_pages_action`` lets the page
    strip's tiles be dragged (it gets "dragged id\ttarget id"). ``bubble_line_action``
    gives each of a panel's dialogue and SFX lines a button in Context (item
    "<panel id>:<line>", line an index or "sfx<index>"): "Select bubble" for the lines
    in ``bubbled``, else "Bubble…". ``new_bubble_action`` adds a free "Bubble…" button
    to a page's Context. Locations mirror characters: ``new_location_action`` on the
    Locations heading, ``design_location_menu`` and ``delete_location_menu`` on each
    location's row, and ``design_location_action`` a "Design location" button in its
    Context (its notes are the description)."""
    panels = manifest.get("panels", [])
    pages = manifest.get("pages", [])
    page_by_id = {page["id"]: page for page in pages}
    panel_by_id = {panel["id"]: panel for panel in panels}
    cast = manifest.get("cast", [])
    character_by_id = {character_row_id(c["name"]): c for c in cast}
    locations = manifest.get("locations", [])
    location_by_id = {location_row_id(loc["name"]): loc for loc in locations}

    def known(row_id):
        return any(row_id in rows for rows in (panel_by_id, page_by_id, character_by_id,
                                               location_by_id))

    if not known(selected_id):
        selected_id = manifest.get("cursor", {}).get("next_panel")
    if not known(selected_id):
        selected_id = next(iter(panel_by_id), next(iter(page_by_id), ""))

    page_counts = {page["id"]: 0 for page in pages}
    for panel in panels:
        page_id = (panel.get("placement") or {}).get("page")
        if page_id in page_counts:
            page_counts[page_id] += 1

    def page_file_state(page: dict[str, Any]) -> str:
        if not page.get("file"):
            return "No XCF"
        if root is None:
            return Path(page["file"]).name
        return "XCF ready" if (Path(root) / page["file"]).is_file() else "XCF missing"

    def page_progress(page: dict[str, Any]) -> str:
        count = page_counts[page["id"]]
        if root is not None and page.get("file") and page_file_state(page) == "XCF missing":
            return "! XCF missing"
        if count == 0:
            return "○ Not started"
        if not page.get("file"):
            return "◐ Draft"
        return "● In progress"

    title = _label(manifest.get("project", {}).get("title")) or "Untitled project"
    chapter = _label(manifest.get("project", {}).get("chapter"))
    project_rows = [f"# {title}"]
    preset_id = manifest.get("project", {}).get("preset")
    preset_name = {
        "oneshot": "Manga one-shot / anthology",
        "manga_series": "Serialized manga",
        "color_comic": "Full-color comic / manhua",
        "digital_comic": "Digital page comic",
        "custom": "Custom",
    }.get(preset_id)
    if preset_name:
        project_rows.append(f"Preset\t{preset_name}")
    if chapter:
        project_rows.append(f"# {chapter}")
    project_rows.append("# Pages")
    for page in pages:
        page_label = _label(page.get("label")) or "Page"
        project_rows.append(f"{page['id']}\t{page_label} · {page_progress(page)}"
                            + (f"\t!{delete_page_action}:Delete page…"
                               if delete_page_action else ""))
    project_rows.append("# Script panels")
    for panel in panels:
        if panel.get("status") == "orphaned":
            continue
        project_rows.append(f"{panel['id']}\t{_panel_label(panel)}")
    orphaned = [panel for panel in panels if panel.get("status") == "orphaned"]
    if orphaned:
        project_rows.append("# Needs matching")
        for panel in orphaned:
            count = len(panel.get("takes", []))
            project_rows.append(
                f"{panel['id']}\t{_panel_label(panel)} · {count} take{'s' if count != 1 else ''}")
    props = manifest.get("props", [])
    if cast or locations or props or new_character_action or new_location_action:
        project_rows.append("# Assets")
        if cast or new_character_action:
            heading_menu = (f"\t!{new_character_action}:New character…"
                            if new_character_action else "")
            row_menu = _menu((design_character_menu, "Design character"),
                             (delete_character_menu, "Delete character…"))
            project_rows.append(f"\t# Characters{heading_menu}")
            project_rows.extend(
                f"\t\t{character_row_id(character['name'])}\t"
                f"{_label(character.get('name'))}{row_menu}"
                for character in cast)
        def summary(asset, fallback):
            name = _label(asset.get("name")) or fallback
            notes = _label(asset.get("notes"))
            text = f"{name} · {notes}" if notes else name
            return text[:137].rstrip() + "…" if len(text) > 140 else text

        if locations or new_location_action:
            heading_menu = (f"\t!{new_location_action}:New location…"
                            if new_location_action else "")
            row_menu = _menu((design_location_menu, "Design location"),
                             (delete_location_menu, "Delete location…"))
            project_rows.append(f"\t# Locations{heading_menu}")
            project_rows.extend(
                f"\t\t{location_row_id(location['name'])}\t"
                f"{summary(location, 'Location')}{row_menu}"
                for location in locations)
        if props:
            project_rows.append("\t# Props")
            project_rows.extend(f"\t\t# {summary(prop, 'Prop')}" for prop in props)

    previews = previews or {}
    page_menu = f"\t!{delete_page_action}:Delete page…" if delete_page_action else ""
    filmstrip_rows = [f"!!reorder\t{reorder_pages_action}"] if reorder_pages_action else []
    for page in pages:
        row = (f"{page['id']}\t{_label(page.get('label')) or 'Page'} · "
               f"{page_counts[page['id']]} panels · {page_progress(page)}")
        preview = previews.get(page["id"])
        if (preview and "\t" not in preview and "\n" not in preview
                and len(row.encode("utf-8")) + len(preview.encode("utf-8")) + 1 <= 4096):
            row += f"\t{preview}"
        filmstrip_rows.append(row + page_menu)
    script_rows = ["# Reading order"]
    for panel in panels:
        if panel.get("status") == "orphaned":
            continue
        marker = "●" if panel.get("placement") or panel.get("status") == "placed" else "○"
        take_count = len(panel.get("takes", []))
        take_summary = f" · {take_count} take{'s' if take_count != 1 else ''}"
        script_rows.append(
            f"{panel['id']}\t{_script_panel_label(panel)} · {marker}{take_summary}")
    orphaned_script = [panel for panel in panels if panel.get("status") == "orphaned"]
    if orphaned_script:
        script_rows.append("# Needs matching")
        for panel in orphaned_script:
            take_count = len(panel.get("takes", []))
            active = panel.get("active_take")
            retained = f" · active {active}" if active else ""
            script_rows.append(
                f"{panel['id']}\t{_script_panel_label(panel)} · "
                f"{take_count} retained take{'s' if take_count != 1 else ''}{retained}")

    character_rows = ["# Cast"]
    if cast:
        bible_menu = _menu((delete_character_menu, "Delete character…"))
        character_rows.extend(
            f"{character_row_id(character['name'])}\t{_label(character.get('name'))}"
            f"{bible_menu}"
            for character in cast)
    else:
        character_rows.append("No characters in this project's cast")
    if selected_id in character_by_id:
        character = character_by_id[selected_id]
        character_rows.extend([
            "# Story record",
            f"Name\t{_label(character.get('name'))}",
            f"Aliases\t{', '.join(_label(a) for a in character.get('aliases', [])) or 'None'}",
            "# Story notes",
            _label(character.get("notes")) or "No project notes",
        ])

    panel_rows = ["# Panel brief"]
    if selected_id in panel_by_id:
        panel = panel_by_id[selected_id]
        label = panel.get("label", {})
        placement = panel.get("placement") or {}
        panel_rows.extend([
            f"ID\t{panel['id']}",
            f"Script position\tPage {label.get('page', '—')} · Panel {label.get('panel', '—')}",
            f"Status\t{_label(panel.get('status')) or 'unplaced'}",
            f"Page\t{placement.get('page') or 'Unassigned'}",
            f"Location\t{_label(panel.get('location')) or 'Unspecified'}",
            f"Shot\t{_label(panel.get('camera')) or 'Unspecified'}",
            f"Aspect ratio\t{_label(panel.get('aspect_ratio')) or 'Unspecified'}",
            f"Frame size\t{_label(panel.get('size')) or 'Unspecified'}",
            "# Action",
            _label(panel.get("action")) or "No action text",
        ])
        characters = panel.get("characters", [])
        panel_rows.append("# Characters")
        panel_rows.extend(
            f"{_label(character.get('name'))}\t{_label(character.get('version')) or 'Active version'}"
            for character in characters)
        if not characters:
            panel_rows.append("No characters specified")
        panel_rows.extend(["# Lettering reference",
                           f"Dialogue lines\t{len(panel.get('dialogue', []))}"])
        for line in panel.get("dialogue", []):
            speaker = _label(line.get("speaker")) or "Unassigned"
            text = _label(line.get("text"))
            panel_rows.append(f"{speaker}\t{text}")
        panel_rows.append(f"Sound effects\t{', '.join(_label(sfx) for sfx in panel.get('sfx', [])) or 'None'}")
        panel_rows.append(f"Notes\t{_label(panel.get('notes')) or 'None'}")
    else:
        panel_rows.append("Select a script panel to view its production brief")

    inspector_rows: list[str] = []
    if selected_id in panel_by_id:
        panel = panel_by_id[selected_id]
        script_label = panel.get("label", {})
        # Context for a panel: the production brief (who, where, shot, action) first,
        # then placement and takes. Generate acts on this panel.
        inspector_rows.extend([
            f"# Panel {script_label.get('panel', '—')} · Page {script_label.get('page', '—')}",
            f"Status\t{_label(panel.get('status')) or 'unplaced'}",
            "# Characters",
        ])
        pid = panel["id"]
        characters = panel.get("characters", [])
        inspector_rows.extend([
            _field(pid, "characters", "Characters",
                   ", ".join(_label(c.get("name")) for c in characters)),
            _field(pid, "expressions", "Expressions", _expressions(
                panel.get("expressions") or {})),
        ])
        inspector_rows.extend(  # a pinned reference version (outfit) per character
            f"{_label(c.get('name'))}\t{_label(c.get('version'))}"
            for c in characters if c.get("version"))
        inspector_rows.extend([
            "# Scene",
            _field(pid, "location", "Location", panel.get("location")),
            _field(pid, "camera", "Shot", panel.get("camera")),
            _field(pid, "aspect_ratio", "Aspect ratio", panel.get("aspect_ratio")),
            _field(pid, "size", "Frame size", panel.get("size")),
            "# Action",
            _field(pid, "action", "Action", panel.get("action")),
            _field(pid, "notes", "Notes", panel.get("notes")),
        ])
        if open_page_action and (panel.get("placement") or {}).get("page") in page_by_id:
            inspector_rows.append(f"!{open_page_action}\tOpen page")
        placement = panel.get("placement") or {}
        if placement and placement.get("page") in page_by_id:
            page_label = _label(page_by_id[placement["page"]].get("label")) or "its page"
            inspector_rows.append(f"Generate\tCreates a new take in the saved frame on {page_label}")
        else:
            inspector_rows.append(
                "Next\tOpen a project page, select a frame, then generate this panel")
        lines = [(str(i), _label(d.get("speaker")) or "—", _label(d.get("text")))
                 for i, d in enumerate(panel.get("dialogue", []))]
        lines += [(f"sfx{i}", "SFX", _label(sfx)) for i, sfx in enumerate(panel.get("sfx", []))]
        if lines:
            inspector_rows.append("# Dialogue")
            for line, speaker, text in lines:
                button = ""
                if bubble_line_action:
                    key = f"{pid}:{line}"
                    label = "Select bubble" if key in bubbled else "Bubble…"
                    button = f"\t!{bubble_line_action}:{key}:{label}"
                inspector_rows.append(f"{speaker}\t{text}{button}")
        inspector_rows.extend([
            "# Production",
            f"ID\t{panel['id']}",
            f"Takes\t{len(panel.get('takes', []))}",
            f"Active take\t{panel.get('active_take') or 'None'}",
        ])
        placement = panel.get("placement") or {}
        if placement:
            frame = placement.get("frame") or []
            frame_text = (" × ".join(str(value) for value in frame[2:4])
                          if len(frame) >= 4 else "Unknown")
            inspector_rows.extend([
                f"Placed page\t{placement.get('page', 'Unknown')}",
                f"Frame size\t{frame_text}",
            ])
        inspector_rows.append(f"Dialogue lines\t{len(panel.get('dialogue', []))}")
        take_map = manifest.get("takes", {})
        for take_id in panel.get("takes", []):
            take = take_map.get(take_id, {})
            marker = " · active" if take_id == panel.get("active_take") else ""
            parent = take.get("parent")
            details = [take.get("kind", "take"), take_id]
            if take.get("width") and take.get("height"):
                details.append(f"{take['width']}×{take['height']}")
            if parent:
                details.append(f"from {parent}")
            if root is not None and take.get("file"):
                state = ("file ready" if (Path(root) / take["file"]).is_file()
                         else "file missing")
                details.append(state)
            inspector_rows.append(f"{take_id}{marker}\t{' · '.join(details)}")
    elif selected_id in page_by_id:
        page = page_by_id[selected_id]
        panels_on_page = [p for p in panels
                          if (p.get("placement") or {}).get("page") == selected_id]
        inspector_rows.extend([
            "# Page",
            _field(selected_id, "label", "Label", page.get("label")),
            f"ID\t{selected_id}",
            f"Document\t{_label(page.get('file'))}",
            f"Document status\t{page_file_state(page)}",
            f"Progress\t{page_progress(page)}",
            f"Placed panels\t{len(panels_on_page)}",
        ])
        availability = page_layout_availability(manifest, selected_id)
        inspector_rows.append("# Page layout")
        if not availability["available"]:
            inspector_rows.append(f"Layout status\t{availability['reason']}")
        elif availability.get("cover"):
            inspector_rows.append(
                f"Layout status\t{availability['cover']} cover · "
                f"{len(availability['combinations'])} guide layouts")
            inspector_rows.append(
                "Layout note\tColored safe-area and typography guides; hide before export")
            if generate_layout_action:
                inspector_rows.append(f"!{generate_layout_action}\tChoose cover layout…")
        else:
            script_panel_count = availability["panel_count"]
            option_count = len(availability["combinations"])
            inspector_rows.append(
                f"Layout status\t{script_panel_count} script panels · "
                f"{option_count} preview choices")
            inspector_rows.append(
                "Layout note\tCyan borderless guides; overlapping panels layer above frame ink")
            if generate_layout_action:
                inspector_rows.append(f"!{generate_layout_action}\tChoose layout…")
        if open_page_action:
            inspector_rows.append(f"!{open_page_action}\tOpen page")
        if new_bubble_action:
            inspector_rows.append(f"!{new_bubble_action}\tBubble…")
        if panels_on_page:
            inspector_rows.append("# On this page")
            for panel in panels_on_page:
                panel_number = panel.get("label", {}).get("panel", "?")
                active = panel.get("active_take")
                take_summary = f"{len(panel.get('takes', []))} takes"
                if active:
                    take_summary += f" · active {active}"
                inspector_rows.append(
                    f"Panel {panel_number}\t{_label(panel.get('status')) or 'unplaced'} · "
                    f"{take_summary} · {panel['id']}")
    elif selected_id in character_by_id:
        character = character_by_id[selected_id]
        inspector_rows.extend([
            "# Character",
            f"Name\t{_label(character.get('name'))}",
            _field(selected_id, "aliases", "Aliases",
                   ", ".join(_label(a) for a in character.get("aliases", []))),
            "# Story notes",
            _field(selected_id, "notes", "Notes", character.get("notes")),
        ])
        if design_action:
            inspector_rows.append(f"!{design_action}\tDesign character")
    elif selected_id in location_by_id:
        location = location_by_id[selected_id]
        key = _location_key(location.get("name", ""))
        used = [p for p in panels if p.get("status") != "orphaned" and _location_key(
            p.get("location") or p.get("scene_heading") or "") == key]
        inspector_rows.extend([
            "# Location",
            f"Name\t{_label(location.get('name'))}",
            f"Panels set here\t{len(used)}",
            "# Description",
            _field(selected_id, "notes", "Notes", location.get("notes")),
        ])
        if design_location_action:
            inspector_rows.append(f"!{design_location_action}\tDesign location")
    else:
        inspector_rows.extend(["# Project", f"Title\t{title}",
                               f"Panels\t{len(panels)}", f"Pages\t{len(pages)}"])

    page_selection = selected_id if selected_id in page_by_id else ""
    if selected_id in panel_by_id:
        page_selection = (panel_by_id[selected_id].get("placement") or {}).get("page", "")
    return {
        "selected_id": selected_id or "",
        "project": "\n".join(project_rows),
        "project_selected": selected_id or "",
        "inspector": "\n".join(inspector_rows),
        "filmstrip": "\n".join(filmstrip_rows),
        "filmstrip_selected": page_selection,
        "script": "\n".join(script_rows),
        "script_selected": selected_id if selected_id in panel_by_id else "",
        "characters": "\n".join(character_rows),
        "character_selected": selected_id if selected_id in character_by_id else "",
        "panel": "\n".join(panel_rows),
        "panel_selected": selected_id if selected_id in panel_by_id else "",
    }


def build_welcome_docks(new_project_action: str = "",
                        new_script_project_action: str = "") -> dict[str, str]:
    """First-run workspace content shown before a project is selected."""
    actions = []
    if new_project_action:
        actions.append(f"!{new_project_action}\tNew project…")
    if new_script_project_action:
        actions.append(f"!{new_script_project_action}\tNew project from script…")
    start = "\nCreate a project:\n" + "\n".join(actions) if actions else ""
    return {
        "selected_id": "",
        "project": "# Imanganation\nChoose a project folder to open your workspace.",
        "project_selected": "",
        "inspector": ("# Context\nProject\tNot open\n\nOpen a project, or create one manually"
                      " and add its pages and story details as you go."
                      + start),
        "inspector_selected": "",
        "filmstrip": "Open a project to see its pages here.",  # tiles: no headings
        "filmstrip_selected": "",
        "script": "# Script\nOpen a project to see its reading order.",
        "script_selected": "",
        "characters": "# Character Bible\nOpen a project to see its cast and references.",
        "character_selected": "",
        "panel": "# Panel\nSelect a script panel to see its production brief.",
        "panel_selected": "",
    }
