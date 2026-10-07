"""What the Set Up Models dialog shows, from the engine's GET /setup report.

Standard library only (no GIMP or GTK), so it's testable outside GIMP; the dialog in
imanganation.py just puts these strings and flags into widgets.
"""

from __future__ import annotations

from typing import Any

INTRO = ("Imanganation's renderer and AI models aren't included with the app. Download "
         "what's missing below, or point it at a folder of models you already have "
         "(ComfyUI, A1111): matching files are linked in without using more disk space.")
RENDERER = "renderer"  # the row for ComfyUI + PyTorch, when the engine installs it


def human_size(n: int | None) -> str:
    if n is None:
        return "?"
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    return f"{max(1, round(n / 1e6))} MB"


def running(report: dict[str, Any]) -> bool:
    return (report.get("task") or {}).get("state") == "running"


def row_state(row: dict[str, Any], task: dict[str, Any]) -> str:
    """One model's state line: "Ready", "Missing", "Downloading 42%", "Failed"…"""
    if row.get("state") == "present":
        return "Ready"
    if row.get("role") == RENDERER:
        if task.get("state") == "running" and task.get("phase") == RENDERER:
            return "Installing…"
        if any(error.startswith("renderer:") for error in task.get("errors", [])):
            return "Failed (see below)"
        return "Missing" if row.get("state") == "missing" else (
            f"Problem: {row.get('note') or row.get('state')}")
    if not row.get("file"):
        return f"Not configured: {row.get('note') or 'unknown'}"
    if task.get("state") == "running" and task.get("current") == row["file"]:
        total = task.get("file_total") or row.get("size") or 0
        return (f"Downloading {int(100 * task.get('file_done', 0) / total)}%"
                if total else "Downloading…")
    if any(row["file"] in error for error in task.get("errors", [])):
        return "Failed (see below)"
    if row.get("state") in ("wrong size", "corrupt"):
        return "Damaged: will download again"
    return "Missing" if row.get("downloadable") else "Missing (no download source)"


def headline(report: dict[str, Any]) -> str:
    task = report.get("task") or {}
    missing = [r for r in report.get("models", []) if r.get("state") != "present"]
    if running(report):
        if task.get("phase") == RENDERER:
            return ("Installing the renderer (ComfyUI and PyTorch for your GPU): a few GB, "
                    "then the models…")
        if task.get("kind") == "link":
            return "Searching for matching files (large files take a while to check)…"
        total = task.get("total") or 0
        return (f"Downloading {human_size(task.get('done', 0))} of {human_size(total)}…"
                if total else "Downloading…")
    if not missing:
        return "All models are ready."
    needed = report.get("missing_bytes") or 0
    first = missing[0]
    needs = [name for role, name in ((RENDERER, "the renderer"), ("checkpoint", "the checkpoint"))
             if any(r.get("role") == role for r in missing)]
    lead = (f"Rendering needs {' and '.join(needs)} first." if needs and first.get("role")
            in (RENDERER, "checkpoint")
            else f"{len(missing)} file{'s' if len(missing) != 1 else ''} missing.")
    return f"{lead} {human_size(needed)} to download, {human_size(report.get('free_bytes'))} free."


def details(report: dict[str, Any]) -> list[str]:
    """Lines under the headline: what just happened, errors, restart needed."""
    task = report.get("task") or {}
    lines = []
    state = task.get("state")
    if state == "done" and task.get("kind") == "link":
        found = task.get("finished") or []
        lines.append(f"Linked {len(found)} file{'s' if len(found) != 1 else ''}: "
                     + ", ".join(found) if found else
                     "No matching files found in that folder.")
    elif state == "done" and task.get("kind") == "download":
        renderer = [f for f in task.get("finished", []) if f.startswith("renderer (")]
        if renderer:
            lines.append(f"The renderer is installed and starts on your GPU: {renderer[0][10:-1]}.")
        lines.append("Download finished; every file was checked against its SHA-256.")
    elif state == "cancelled":
        lines.append("Download paused. Download again to resume where it stopped.")
    lines += [f"Error: {error}" for error in task.get("errors", [])]
    if state == "error" and task.get("kind") == "download":
        lines.append("Download again to retry; finished files and partial downloads are kept.")
    if report.get("comfy_paths_changed"):
        lines.append("ComfyUI was pointed at the models folder: restart GIMP so it picks "
                     "them up.")
    return lines


def progress(report: dict[str, Any]) -> tuple[float, str]:
    """(fraction, text) for the progress bar; (0, "") when nothing is running."""
    task = report.get("task") or {}
    if not running(report):
        return 0.0, ""
    if task.get("kind") == "link":
        return -1.0, "Checking files…"  # pulse
    if task.get("phase") == RENDERER:
        return -1.0, task.get("current") or "Installing the renderer…"
    total = task.get("total") or 0
    fraction = min(1.0, task.get("done", 0) / total) if total else 0.0
    current = task.get("current") or ""
    return fraction, f"{current}  {int(fraction * 100)}%" if current else f"{int(fraction * 100)}%"


def actions(report: dict[str, Any] | None) -> dict[str, Any]:
    """Button labels and sensitivity. ``None`` = the engine isn't answering."""
    if report is None:
        return {"download": "Download", "download_enabled": False, "link_enabled": False,
                "cancel_enabled": False}
    busy = running(report)
    fetchable = report.get("missing_bytes") or 0
    return {"download": (f"Download ({human_size(fetchable)})" if fetchable else "Download"),
            "download_enabled": not busy and fetchable > 0,
            "link_enabled": not busy and not report.get("ready"),
            "cancel_enabled": busy and (report.get("task") or {}).get("kind") == "download"}


def licence_note(report: dict[str, Any]) -> str:
    names = sorted({r["license"] for r in report.get("models", [])
                    if r.get("state") != "present" and r.get("license")})
    if not names:
        return ""
    return ("Downloading means accepting each file's licence (linked beside it): "
            + "; ".join(names) + ".")


def should_prompt(report: dict[str, Any] | None) -> bool:
    """Open the dialog by itself at startup? Only when rendering can't work at all (no
    renderer or no checkpoint) and nothing is already being set up."""
    if report is None or running(report):
        return False
    return any(r.get("role") in ("checkpoint", RENDERER) and r.get("state") != "present"
               for r in report.get("models", []))


OPTIONAL_INTRO = ("Optional engines: not needed to render, but a project can choose one "
                  "under Imanganation ▸ Render Engine. Each downloads separately.")
DEFAULT_ENGINE = "sdxl"  # its files are the required rows above


def optional_engines(engines_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The GET /engines rows Set Up Models offers as extras: every engine but the
    default (Qwen-Image 2.1, Z-Anime) and the face pass."""
    return [r for r in (engines_report or {}).get("engines", [])
            if r.get("id") != DEFAULT_ENGINE]


def optional_size(row: dict[str, Any]) -> str:
    return "" if row.get("installed") else human_size(row.get("missing_bytes") or 0)
