"""What the Render Engine dialog shows, from the engine's GET /engines report, and the
project's choice in project.json (``project.render``).

Standard library only (no GIMP or GTK), so it's testable outside GIMP; the dialog in
imanganation.py puts these strings and flags into widgets.
"""

from __future__ import annotations

from typing import Any

DEFAULT = {"engine": "sdxl", "face_pass": False}
FACE_PASS = "face_pass"

INTRO = ("Choose how this project's panels are drawn. Every panel of a project uses the "
         "same engine, so pages stay consistent. Engines differ in speed, in what they "
         "get right, and in licence.")


def human_size(n: int | None) -> str:
    if not n:
        return "0 MB"
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(1, round(n / 1e6))} MB"


def project_render(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """The project's engine choice, with defaults for anything unset."""
    chosen = ((manifest or {}).get("project") or {}).get("render") or {}
    return {**DEFAULT, **{k: v for k, v in chosen.items() if k in DEFAULT}}


def set_project_render(manifest: dict[str, Any], engine: str, face_pass: bool) -> None:
    manifest["project"]["render"] = {"engine": engine, "face_pass": bool(face_pass)}


def job_options(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """What a render job sends: nothing for a project that never chose (the engine's
    own settings apply), else its engine and face pass."""
    chosen = ((manifest or {}).get("project") or {}).get("render")
    return project_render(manifest) if chosen else {}


def engines(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [r for r in (report or {}).get("engines", []) if r.get("id") != FACE_PASS]


def face_pass(report: dict[str, Any] | None) -> dict[str, Any] | None:
    return next((r for r in (report or {}).get("engines", []) if r.get("id") == FACE_PASS),
                None)


def installing(row: dict[str, Any]) -> bool:
    return ((row.get("install") or {}).get("state")) == "running"


def state(row: dict[str, Any]) -> str:
    """"Installed", "Not installed: 17.3 GB", "Installing 42%", "Problem: …"."""
    task = row.get("install") or {}
    if installing(row):
        total = task.get("total") or 0
        return (f"Installing {int(100 * task.get('done', 0) / total)}%" if total
                else "Installing…")
    if row.get("problem"):
        return f"Problem: {row['problem']}"
    if row.get("installed"):
        return "Installed"
    errors = task.get("errors") or []
    if errors:
        return f"Install failed: {errors[0]}"
    return f"Not installed: {human_size(row.get('missing_bytes'))} to download"


def label(row: dict[str, Any]) -> str:
    return f"{row.get('name', row.get('id'))}  ({row.get('speed', '')})"


def licence_line(row: dict[str, Any]) -> str:
    use = "commercial use allowed" if row.get("commercial") else "NON-COMMERCIAL"
    return f"Licence: {row.get('licence', 'unknown')} ({use})"


def warning(row: dict[str, Any] | None) -> str:
    """Shown when a non-commercial engine is selected."""
    if not row or row.get("commercial", True):
        return ""
    return (f"{row.get('name')} is licensed for non-commercial use only. Pages it draws "
            "may not be used commercially (for example a manga you sell) without a "
            "licence from its maker. See: " + (row.get("licence_url") or ""))


def can_choose(row: dict[str, Any]) -> bool:
    return bool(row.get("installed")) and not installing(row)


def can_install(row: dict[str, Any]) -> bool:
    return not row.get("installed") and not installing(row) and not row.get("problem") \
        and bool(row.get("missing"))


def locations_note(choice: dict[str, Any]) -> str:
    if choice.get("engine") == "qwen_image_21":
        return ("Design Locations draws one image of each place in the script; Qwen-Image "
                "then keeps every panel set there looking like the same place.")
    return ("Location images are used by Qwen-Image 2.1 only; other engines draw each "
            "panel's setting from the script's words.")
