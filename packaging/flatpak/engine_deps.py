#!/usr/bin/env python3
"""Write ``engine-deps.json``: the engine's Python packages as a Flatpak module.

The engine runs inside the Flatpak on the runtime's Python (GNOME 51: 3.14), before
anything is downloaded (the setup dialog talks to it to do the downloading), so its
packages ship in the app. They are the versions in ``uv.lock`` (what the engine is
tested with), as binary wheels for that Python, each with PyPI's URL and SHA-256, so
the build installs them offline and verified.

Re-run when ``uv.lock`` changes: ``python3 packaging/flatpak/engine_deps.py``. Needs
``uv`` and network access (PyPI's JSON API).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PYTHON = "3.14"  # the runtime's (org.gnome.Platform//51)
CP = "cp" + PYTHON.replace(".", "")
OUT = HERE / "engine-deps.json"
# Optional groups the app needs too: `eval` holds onnxruntime, which the face pass's
# detectors (and the tagger matching faces to characters) run on
EXTRAS = ["eval"]
EXTRAS_ARGS = [arg for extra in EXTRAS for arg in ("--extra", extra)]


def pinned() -> list[tuple[str, str]]:
    """(name, version) for the engine on PYTHON, held to uv.lock's versions."""
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "lock.txt"
        lock.write_text(subprocess.run(
            ["uv", "export", "--format", "requirements-txt", "--no-dev",
             "--no-emit-project", "--no-hashes", "-q", *EXTRAS_ARGS],
            cwd=REPO, check=True, capture_output=True, text=True).stdout)
        resolved = subprocess.run(
            ["uv", "pip", "compile", "pyproject.toml", *EXTRAS_ARGS,
             "--python-version", PYTHON,
             "--python-platform", "x86_64-manylinux_2_28", "--only-binary", ":all:",
             "-c", str(lock), "-q", "--no-header", "--no-annotate"],
            cwd=REPO, check=True, capture_output=True, text=True).stdout
    out = []
    for line in resolved.splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;]+)", line.strip())
        if match:
            out.append((match[1], match[2]))
    return out


def _score(filename: str) -> int | None:
    """Prefer this Python's own wheel, then the stable ABI, then pure Python; only
    Linux x86_64 manylinux binaries (any glibc up to the runtime's)."""
    if filename.endswith("-none-any.whl"):
        return 1
    if "x86_64" not in filename or "manylinux" not in filename:
        return None
    if f"-{CP}-{CP}-" in filename:
        return 3
    abi3 = re.search(r"-cp3(\d+)-abi3-", filename)
    if abi3 and int(abi3[1]) <= int(CP[3:]):
        return 2
    return None


def wheel(name: str, version: str) -> dict:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json",
                                timeout=30) as response:
        files = json.load(response)["urls"]
    choices = [(_score(f["filename"]), f) for f in files if f["filename"].endswith(".whl")]
    choices = [(score, f) for score, f in choices if score is not None]
    if not choices:
        raise SystemExit(f"no {CP} Linux x86_64 wheel for {name}=={version}")
    best = max(choices, key=lambda c: c[0])[1]
    return {"type": "file", "url": best["url"], "sha256": best["digests"]["sha256"]}


def main() -> None:
    packages = pinned()
    sources = []
    for name, version in packages:
        sources.append(wheel(name, version))
        print(f"{name}=={version}: {sources[-1]['url'].rsplit('/', 1)[-1]}", file=sys.stderr)
    names = " ".join(f"{n}=={v}" for n, v in packages)
    module = {
        "name": "engine-python-deps",
        "buildsystem": "simple",
        "build-commands": [
            # --ignore-installed: the runtime has some of these (MarkupSafe…) in its
            # read-only /usr; install ours into /app beside them, never over them.
            "pip3 install --no-index --no-build-isolation --no-deps --ignore-installed "
            f"--find-links=. --prefix=${{FLATPAK_DEST}} {names}"],
        "sources": sources,
    }
    OUT.write_text(json.dumps(module, indent=2) + "\n")
    print(OUT)


if __name__ == "__main__":
    main()
