"""Text models for the host-rendered Imanganation project docks."""

from __future__ import annotations

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


def character_row_id(name: str) -> str:
    """Build a collision-safe row key from the engine's character identity name."""
    return "character:" + quote(name, safe="")


def build_docks(manifest: dict[str, Any], selected_id: str | None = None,
                root: str | Path | None = None) -> dict[str, str]:
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

    title = _label(manifest.get("project", {}).get("title")) or "Untitled project"
    project_rows = [f"# {title}", "# Pages"]
    for page in pages:
        project_rows.append(f"{page['id']}\t{_label(page.get('label')) or 'Page'}")
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
    if cast:
        project_rows.append("# Cast")
        project_rows.extend(
            f"\t{character_row_id(character['name'])}\t{_label(character.get('name'))}"
            for character in cast)

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

    filmstrip_rows = [
        f"{page['id']}\t{_label(page.get('label')) or 'Page'} · "
        f"{page_counts[page['id']]} panels · {page_progress(page)}"
        for page in pages]

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
            f"Location\t{_label(panel.get('location')) or 'Unspecified'}",
            f"Shot\t{_label(panel.get('camera')) or 'Unspecified'}",
            f"Aspect ratio\t{_label(panel.get('aspect_ratio')) or 'Unspecified'}",
        ])
        placement = panel.get("placement") or {}
        if placement:
            frame = placement.get("frame") or []
            frame_text = " × ".join(str(value) for value in frame[2:4]) if len(frame) >= 4 else "Unknown"
            inspector_rows.extend([
                f"Placed page\t{placement.get('page', 'Unknown')}",
                f"Frame size\t{frame_text}",
            ])
        inspector_rows.append("# Action")
        inspector_rows.append(_label(panel.get("action")) or "No action text")
        dialogue = panel.get("dialogue", [])
        sfx = panel.get("sfx", [])
        inspector_rows.extend([
            "# Lettering reference",
            f"Dialogue lines\t{len(dialogue)}",
            f"Sound effects\t{', '.join(_label(value) for value in sfx) or 'None'}",
        ])
        characters = panel.get("characters", [])
        if characters:
            inspector_rows.append("# Characters · engine references")
            inspector_rows.extend(
                f"{_label(char.get('name'))}\t{_label(char.get('version')) or 'Active'}"
                for char in characters)
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
                state = "file ready" if (Path(root) / take["file"]).is_file() else "file missing"
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
            inspector_rows.extend(
                f"Panel {p.get('label', {}).get('panel', '?')}\t"
                f"{p['id']} · {len(p.get('takes', []))} takes"
                for p in panels_on_page)
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
    }
