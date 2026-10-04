"""Text models for the host-rendered Imanganation project docks."""

from __future__ import annotations

from typing import Any


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


def build_docks(manifest: dict[str, Any], selected_id: str | None = None) -> dict[str, str]:
    """Build generic host content and stable selections from a project manifest."""
    panels = manifest.get("panels", [])
    pages = manifest.get("pages", [])
    page_by_id = {page["id"]: page for page in pages}
    panel_by_id = {panel["id"]: panel for panel in panels}
    if selected_id not in panel_by_id and selected_id not in page_by_id:
        selected_id = manifest.get("cursor", {}).get("next_panel")
    if selected_id not in panel_by_id and selected_id not in page_by_id:
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
    cast = manifest.get("cast", [])
    if cast:
        project_rows.append("# Cast")
        project_rows.extend(f"\t# {_label(character.get('name'))}" for character in cast)

    filmstrip_rows = [f"{page['id']}\t{_label(page.get('label')) or 'Page'}"
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
            "# Action",
            _label(panel.get("action")) or "No action text",
        ])
        characters = panel.get("characters", [])
        if characters:
            inspector_rows.append("# Characters · engine references")
            inspector_rows.extend(
                f"{_label(char.get('name'))}\t{_label(char.get('version')) or 'Active'}"
                for char in characters)
    elif selected_id in page_by_id:
        page = page_by_id[selected_id]
        panels_on_page = [p for p in panels
                          if (p.get("placement") or {}).get("page") == selected_id]
        inspector_rows.extend([
            "# Page",
            f"ID\t{selected_id}",
            f"Label\t{_label(page.get('label'))}",
            f"Document\t{_label(page.get('file'))}",
            f"Placed panels\t{len(panels_on_page)}",
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
