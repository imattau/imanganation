"""Run ComfyUI as the engine's child process (``manganation serve --comfyui``).

In the Flatpak the engine owns its ComfyUI: the code is bundled, its venv and folders
are in the data folder (comfy_setup), and nothing on the host starts it. The engine
starts it at launch when the renderer is installed, and again right after Set Up
installs it, so the first render needs no restart. It stops with the engine (which
stops with GIMP). A ComfyUI already answering on the port is left alone.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse


def _die_with_parent() -> None:
    """Child pre-exec: SIGTERM when the engine exits (Linux)."""
    try:
        import ctypes
        import signal

        ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:  # noqa: BLE001 - best-effort; stop() still ends it
        pass


def _up(url: str) -> bool:
    import httpx

    try:
        return httpx.get(f"{url.rstrip('/')}/system_stats", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


class ComfyProcess:
    """``installer(log)`` gives the comfy_setup.Installer (code, venv, folders);
    ``comfy_paths`` is the extra-model-paths file, written for ``models_root`` before
    each start. ``popen`` and ``is_up`` are injectable for tests."""

    def __init__(self, installer: Callable, url: str, *, comfy_paths: Path,
                 models_root: Path, log_file: Path, popen=None, is_up=None):
        self.installer = installer
        self.url = url
        self.comfy_paths = Path(comfy_paths)
        self.models_root = Path(models_root)
        self.log_file = Path(log_file)
        self._popen = popen or subprocess.Popen
        self._is_up = is_up or _up
        self.process = None

    def command(self, installer) -> list[str]:
        port = urlparse(self.url).port or 8188
        return [str(installer.python), "main.py",
                "--extra-model-paths-config", str(self.comfy_paths),
                *installer.comfy_args(), "--listen", "127.0.0.1", "--port", str(port)]

    def start(self) -> str:
        """-> "running" (ours, or something already on the port), "started", or
        "renderer missing" (Set Up installs it, then calls this again)."""
        from manganation.models_setup import write_comfy_paths

        if self.process is not None and self.process.poll() is None:
            return "running"
        if self._is_up(self.url):
            return "running"
        installer = self.installer(lambda *a: None)
        if not installer.ready():
            return "renderer missing"
        write_comfy_paths(self.comfy_paths, self.models_root)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_file, "ab") as log:
            self.process = self._popen(
                self.command(installer), cwd=installer.comfy, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, preexec_fn=_die_with_parent)
        return "started"

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
