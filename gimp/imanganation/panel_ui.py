"""Text models for the host-rendered Imanganation project docks."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import quote


def _label(value: Any) -> str:
    """Keep host row delimiters and line breaks out of visible labels."""
    return " ".join(str(value or "").replace("\t", " ").splitlines()).strip()


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


def character_row_id(name: str) -> str:
    """Build a collision-safe row key from the engine's character identity name."""
    return "character:" + quote(name, safe="")


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
                previews: dict[str, str] | None = None) -> dict[str, str]:
    """Build generic host content and stable selections from a project manifest."""
    panels = manifest.get("panels", [])
    pages = manifest.get("pages", [])
    page_by_id = {page["id"]: page for page in pages}
    panel_by_id = {panel["id"]: panel for panel in panels}
    cast = manifest.get("cast", [])
    character_by_id = {character_row_id(c["name"]): c for c in cast}
    if (selected_id not in panel_by_id and selected_id not in page_by_id
            and selected_id not in character_by_id):
        selected_id = manifest.get("cursor", {}).get("next_panel")
    if (selected_id not in panel_by_id and selected_id not in page_by_id
            and selected_id not in character_by_id):
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
    if chapter:
        project_rows.append(f"# {chapter}")
    project_rows.append("# Pages")
    for page in pages:
        page_label = _label(page.get("label")) or "Page"
        project_rows.append(f"{page['id']}\t{page_label} · {page_progress(page)}")
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
    locations = manifest.get("locations", [])
    props = manifest.get("props", [])
    if cast or locations or props:
        project_rows.append("# Assets")
        if cast:
            project_rows.append("\t# Characters")
            project_rows.extend(
                f"\t\t{character_row_id(character['name'])}\t"
                f"{_label(character.get('name'))}"
                for character in cast)
        for section, assets in (("Locations", locations), ("Props", props)):
            if not assets:
                continue
            project_rows.append(f"\t# {section}")
            for asset in assets:
                name = _label(asset.get("name")) or section[:-1]
                notes = _label(asset.get("notes"))
                summary = f"{name} · {notes}" if notes else name
                if len(summary) > 140:
                    summary = summary[:137].rstrip() + "…"
                project_rows.append(f"\t\t# {summary}")

    previews = previews or {}
    filmstrip_rows = []
    for page in pages:
        row = (f"{page['id']}\t{_label(page.get('label')) or 'Page'} · "
               f"{page_counts[page['id']]} panels · {page_progress(page)}")
        preview = previews.get(page["id"])
        if (preview and "\t" not in preview and "\n" not in preview
                and len(row.encode("utf-8")) + len(preview.encode("utf-8")) + 1 <= 4096):
            row += f"\t{preview}"
        filmstrip_rows.append(row)
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
        character_rows.extend(
            f"{character_row_id(character['name'])}\t{_label(character.get('name'))}"
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
        inspector_rows.extend([
            "# Panel",
            f"ID\t{panel['id']}",
            f"Script page\t{script_label.get('page', '—')}",
            f"Panel number\t{script_label.get('panel', '—')}",
            f"Status\t{_label(panel.get('status')) or 'unplaced'}",
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
            f"ID\t{selected_id}",
            f"Label\t{_label(page.get('label'))}",
            f"Document\t{_label(page.get('file'))}",
            f"Document status\t{page_file_state(page)}",
            f"Progress\t{page_progress(page)}",
            f"Placed panels\t{len(panels_on_page)}",
        ])
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
            f"Aliases\t{', '.join(_label(a) for a in character.get('aliases', [])) or 'None'}",
            "# Story notes",
            _label(character.get("notes")) or "No project notes",
        ])
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
