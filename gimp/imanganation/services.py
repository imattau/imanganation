"""Which engine the workspace starts, and how. Standard library only (testable outside
GIMP); imanganation.py runs the commands.

- **Bundled** (the Flatpak, the default there): the app's own engine, inside the
  sandbox. It brings its own ComfyUI (``serve --comfyui``) and keeps its renderer,
  models and logs in the app's data folder; projects go in ~/Imanganation.
- **Host checkout**: an engine checkout on the host (IMANGANATION_HOME, the checkout
  these plug-in files are symlinked from, or ~/.config/imanganation/engine-home). The
  workspace starts ComfyUI and the engine from it; from inside the Flatpak through
  ``flatpak-spawn --host``. IMANGANATION_HOME also picks this inside the Flatpak (a
  developer running the packaged GIMP against their checkout).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BUNDLED_ENGINE = Path("/app/bin/manganation")


@dataclass(frozen=True)
class Engine:
    mode: str  # "bundled", "host" or "none"
    home: Path | None = None  # the host checkout
    data: Path | None = None  # where the engine writes (logs, renderer, models)
    projects: Path | None = None  # the default folder for new projects

    @property
    def log(self) -> Path | None:
        return self.data / "engine.log" if self.data else None


def find_engine(env=None, *, in_flatpak: bool, plugin_file: Path, home: Path | None = None,
                bundled_engine: Path = BUNDLED_ENGINE) -> Engine:
    env = os.environ if env is None else env
    home = home or Path.home()
    if in_flatpak and not env.get("IMANGANATION_HOME") and bundled_engine.exists():
        data = Path(env.get("XDG_DATA_HOME") or home / ".local/share") / "imanganation"
        return Engine("bundled", data=data, projects=home / "Imanganation")
    candidates = [env.get("IMANGANATION_HOME"), Path(plugin_file).resolve().parents[2]]
    try:
        candidates.append((home / ".config/imanganation/engine-home").read_text().strip())
    except OSError:
        pass
    for candidate in candidates:
        if candidate and (Path(candidate) / "pyproject.toml").is_file():
            checkout = Path(candidate)
            return Engine("host", home=checkout, data=checkout,
                          projects=checkout / "projects")
    return Engine("none", projects=home / "Imanganation")


def engine_command(engine: Engine, which=None) -> list[str] | None:
    """The command that starts the engine (None: nothing to start)."""
    import shutil

    if engine.mode == "bundled":
        return [str(BUNDLED_ENGINE), "serve", "--comfyui"]
    if engine.mode == "host":
        venv = engine.home / ".venv/bin/manganation"
        if venv.is_file():
            return [str(venv), "serve"]
        uv = (which or shutil.which)("uv") or str(Path.home() / ".local/bin/uv")
        return [uv, "run", "manganation", "serve"]
    return None


def comfy_command(engine: Engine, port: str) -> list[str] | None:
    """ComfyUI for a host checkout (the bundled engine starts its own): None when
    there's no checkout or it has no ComfyUI venv yet."""
    if engine.mode != "host":
        return None
    python = engine.home / "vendor/ComfyUI/.venv/bin/python"
    if not python.is_file():
        return None
    return [str(python), "main.py", "--extra-model-paths-config",
            str(engine.home / "config/comfyui_extra_model_paths.yaml"),
            "--listen", "127.0.0.1", "--port", port]
