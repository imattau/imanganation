"""Project layout helpers: where a story's script and artefacts live."""

from __future__ import annotations

from pathlib import Path

from manganation.config import REPO_ROOT, load_settings


def projects_root() -> Path:
    return REPO_ROOT / load_settings().paths.projects_dir


def project_dir(name: str) -> Path:
    return projects_root() / name


def ensure_project(name: str) -> Path:
    """Create the standard project subfolders and return the project root."""
    root = project_dir(name)
    for sub in ("characters", "panels"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def script_path(project: str, filename: str = "script.md") -> Path:
    return project_dir(project) / filename


def panels_json_path(project: str) -> Path:
    return project_dir(project) / "panels.json"
