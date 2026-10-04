"""Where a project's character identity lives (the engine's consistency memory).

A GIMP-owned project container is identified by ``project.id`` (``prj_…``) and holds
no character data. The engine keeps traits and reference versions in a character
registry folder. ``projects/identities.json`` maps a container's id to that folder:

    {"prj_cd3562f1216d": "rooftop"}

Imported projects map to their legacy folder (``projects/rooftop/characters``), so the
CLI and the container share one registry and nothing is copied. A project with no
entry gets its own empty store under ``projects/_identity/<id>/``, so it renders
(without references) instead of failing.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

INDEX_NAME = "identities.json"
_PROJECT_ID = re.compile(r"^prj_[a-z0-9]{6,}$")


class IdentityError(ValueError):
    pass


def _root(root: Path | None) -> Path:
    if root is not None:
        return Path(root)
    from manganation.project import projects_root

    return projects_root()


def _check(project_id: str) -> None:
    if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
        raise IdentityError(f"not a project id: {project_id!r} (expected prj_[a-z0-9]{{6,}})")


def _load(root: Path) -> dict[str, str]:
    try:
        data = json.loads((root / INDEX_NAME).read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise IdentityError(f"cannot read {root / INDEX_NAME}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def identity_root(project_id: str, root: Path | None = None) -> Path:
    """The folder whose ``characters.json`` / ``characters/`` hold this project's cast."""
    _check(project_id)
    base = _root(root)
    folder = _load(base).get(project_id)
    if folder is None:
        return base / "_identity" / project_id
    path = (base / folder).resolve()
    if base.resolve() not in path.parents:
        raise IdentityError(f"identity folder for {project_id} escapes {base}: {folder!r}")
    return path


def register(project_id: str, folder: str | Path, root: Path | None = None) -> None:
    """Map ``project_id`` to ``folder`` (inside the projects root). Atomic write."""
    _check(project_id)
    base = _root(root)
    rel = Path(folder)
    rel = rel.resolve().relative_to(base.resolve()) if rel.is_absolute() else rel
    if ".." in rel.parts:
        raise IdentityError(f"identity folder must be inside {base}: {folder!r}")
    index = _load(base)
    existing = index.get(project_id)
    if existing is not None and existing != rel.as_posix():
        raise IdentityError(f"{project_id} is already mapped to {existing!r}")
    index[project_id] = rel.as_posix()
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / (INDEX_NAME + ".tmp")
    tmp.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, base / INDEX_NAME)
