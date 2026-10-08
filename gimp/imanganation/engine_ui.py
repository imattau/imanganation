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


DEFAULT_STYLE = {"preset": "default", "text": ""}


def project_render(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """The project's engine choice, with defaults for anything unset."""
    chosen = ((manifest or {}).get("project") or {}).get("render") or {}
    return {**DEFAULT, **{k: v for k, v in chosen.items() if k in DEFAULT}}


def project_style(manifest: dict[str, Any] | None) -> dict[str, str]:
    """The project's look: ``{"preset", "text"}`` (the default look when unset)."""
    chosen = (((manifest or {}).get("project") or {}).get("render") or {}).get("style")
    return {**DEFAULT_STYLE, **{k: str(v) for k, v in (chosen or {}).items()
                                if k in DEFAULT_STYLE}}


def is_default_style(style: dict[str, str]) -> bool:
    return style.get("preset", "default") == "default" and not style.get("text", "").strip()


def set_project_render(manifest: dict[str, Any], engine: str, face_pass: bool,
                       style: dict[str, str] | None = None) -> None:
    render = {"engine": engine, "face_pass": bool(face_pass)}
    style = style if style is not None else project_style(manifest)
    style = {"preset": style.get("preset") or "default",
             "text": " ".join(style.get("text", "").split())}
    if not is_default_style(style):
        render["style"] = style
    manifest["project"]["render"] = render


def style_options(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """``{"style": …}`` for a job or design request when the project has a look of its
    own, else nothing (the engine's default look)."""
    style = project_style(manifest)
    return {} if is_default_style(style) else {"style": style}


def job_options(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """What a render job sends: nothing for a project that never chose (the engine's
    own settings apply), else its engine and face pass, and its look if it has one."""
    chosen = ((manifest or {}).get("project") or {}).get("render")
    return {**project_render(manifest), **style_options(manifest)} if chosen else {}


STYLE_INTRO = ("The project's look, for every panel and for the character and location "
               "designs they follow. Panels stay in colour; black and white is done in "
               "GIMP.")


def style_note(preset: dict[str, Any] | None) -> str:
    """The chosen preset's summary and what measuring it found."""
    if not preset:
        return ""
    note = preset.get("summary", "")
    if preset.get("measured"):
        note += f" Measured: {preset['measured']}"
    return note


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
