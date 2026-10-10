#!/usr/bin/env python3
"""Write the Flatpak manifest for the Imanganation GIMP build.

It starts from the fork's own upstream manifest (``imanganation-gimp/build/linux/
flatpak/org.gimp.GIMP-nightly.json``), so GIMP's dependency list stays whatever the
fork's GIMP version needs, and changes only what makes it ours:

- **App:** our app id, a stable GNOME runtime instead of nightly, our build id, and
  the fork's ``imanganation`` icon (not GIMP's) exported under the app id.
- **Meson and CMake modules** install into ``lib`` (they default to ``lib64`` here).
- **babl / GEGL:** pinned to the releases the fork is tested with (upstream builds
  their moving git master).
- **GIMP:** the fork at a commit: the local checkout's HEAD (default), a pushed one
  (``--fork-git``), or the one the ``imanganation-gimp`` submodule pins (``--fork-pinned``: CI and releases). flatpak-builder caches git sources by commit; a ``dir`` source it
  can't checksum, so GIMP would rebuild every time. ``--fork-worktree`` builds the
  checkout as it is, uncommitted changes included, for testing them.
- **The Imanganation plug-in**, installed with GIMP (``lib/gimp/3.0/plug-ins``), and
  its lettering fonts (``share/fonts``).
- **The engine**, runnable in the sandbox before anything is downloaded: its code and
  config in ``share/imanganation``, a ``manganation`` launcher, and its Python packages
  (``engine-deps.json``, from ``engine_deps.py``) for the runtime's Python.
- **ComfyUI's code** at the commits ``config/comfyui.yaml`` pins, with its IP-Adapter node
  and imanganation's patches (``share/imanganation/vendor/ComfyUI``). Its PyTorch is
  GPU-specific and gigabytes big, so it's installed on first run, with **uv** (bundled).
- **Starting the engine:** ``--talk-name=org.freedesktop.Flatpak``, so the plug-in can
  start the engine and ComfyUI on the host (``flatpak-spawn --host``) for a host install.

Run with the repo's environment (``uv run``: it reads ``config/comfyui.yaml``); network
access is needed to read the pinned ComfyUI file the patch applies to. ``build.sh`` runs
it; see README.md.
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
APP_ID = "io.github.imattau.Imanganation"
RUNTIME_VERSION = "51"  # GNOME; GIMP 3.x is GTK 3, which every GNOME runtime ships
PINNED = {  # module -> git tag and commit (what ~/.local/gimp-deps was built from)
    "babl": {"url": "https://gitlab.gnome.org/GNOME/babl.git", "tag": "BABL_0_1_118",
             "commit": "b47df7d3da1fdd3bf751a369a7ce453a63b2bebf"},
    "gegl": {"url": "https://gitlab.gnome.org/GNOME/gegl.git", "tag": "GEGL_0_4_66",
             "commit": "3ed6237faa44bc64bdff8dcb2750c734ac680d40"},
}
PLUGIN_DIR = "/app/lib/gimp/3.0/plug-ins/imanganation"
ICON = "imanganation"  # the fork's app icon (branding/), named so by its desktop entry
# .git/modules: the gimp-data submodule's git data, also rewritten by every git status.
GIT_CHURN = [".git/index", ".git/FETCH_HEAD", ".git/ORIG_HEAD", ".git/logs", ".git/modules"]
ENGINE_DIR = "/app/share/imanganation"  # REPO_ROOT inside the Flatpak
UV = {"version": "0.11.29", "sha256":
      "04f8b82f5d47f0512dcd32c67a4a6f16a0ea27c81537c338fd0ad6b23cebe829"}
LAUNCHER = f"""#!/usr/bin/env python3
import sys
sys.path.insert(0, "{ENGINE_DIR}/src")
from manganation.cli import app
sys.exit(app())
"""


def plugin_module(repo: Path) -> dict:
    """The plug-in's files. script_canonical.py is a symlink into the engine's source
    (the plug-in and engine share one script parser), so it's copied from its target."""
    plugin = repo / "gimp" / "imanganation"
    sources, commands = [], []
    for path in sorted(plugin.glob("*.py")):
        target = path.resolve()  # follows the script_canonical.py symlink
        sources.append({"type": "file", "path": str(target), "dest-filename": path.name})
        mode = "755" if path.name == "imanganation.py" else "644"
        commands.append(f"install -Dm{mode} {path.name} {PLUGIN_DIR}/{path.name}")
    for font in sorted((repo / "assets" / "fonts").glob("*")):
        if font.suffix.lower() in (".ttf", ".otf", ".txt"):
            sources.append({"type": "file", "path": str(font)})
            commands.append(f"install -Dm644 {font.name} /app/share/fonts/imanganation/{font.name}")
    return {"name": "imanganation-plugin", "buildsystem": "simple",
            "build-commands": commands, "sources": sources}


def engine_module(repo: Path) -> dict:
    """The engine's code and config, and a ``manganation`` launcher on PATH."""
    skip = ["__pycache__", "*.pyc"]
    return {
        "name": "imanganation-engine", "buildsystem": "simple",
        "sources": [
            {"type": "dir", "path": str(repo / "src"), "dest": "src", "skip": skip},
            {"type": "dir", "path": str(repo / "config"), "dest": "config"},
            {"type": "file", "path": str(repo / "pyproject.toml")},
            {"type": "inline", "contents": LAUNCHER, "dest-filename": "manganation"},
        ],
        "build-commands": [
            f"mkdir -p {ENGINE_DIR}",
            f"cp -r src config pyproject.toml {ENGINE_DIR}/",
            "install -Dm755 manganation /app/bin/manganation",
        ],
    }


def _raw_url(repo_url: str, commit: str, path: str) -> str:
    owner_repo = repo_url.removeprefix("https://github.com/").removesuffix(".git")
    return f"https://raw.githubusercontent.com/{owner_repo}/{commit}/{path}"


def _original(repo: Path, pin: dict, path: str) -> str:
    """A file of the pinned ComfyUI as upstream has it: from the local checkout's git
    when it has the commit, else GitHub."""
    try:
        return subprocess.run(["git", "show", f"{pin['commit']}:{path}"],
                              cwd=repo / "vendor" / "ComfyUI", check=True,
                              capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        with urllib.request.urlopen(_raw_url(pin["repo"], pin["commit"], path),
                                    timeout=30) as response:
            return response.read().decode()


def comfy_patches(repo: Path, pin: dict, out_dir: Path) -> list[Path]:
    """imanganation's ComfyUI patches (comfy_setup.PATCHES) as unified diffs."""
    from manganation.comfy_setup import PATCHES

    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for index, patch in enumerate(PATCHES, start=1):
        before = _original(repo, pin, patch.file)
        if patch.old not in before:
            raise SystemExit(f"{patch.file} at {pin['commit'][:10]} doesn't contain the "
                             "code imanganation patches; update the pin or the patch")
        after = before.replace(patch.old, patch.new)
        diff = "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            f"a/{patch.file}", f"b/{patch.file}"))
        path = out_dir / f"comfyui-{index:02d}-{Path(patch.file).stem}.patch"
        path.write_text(diff)
        files.append(path)
    return files


def comfyui_module(repo: Path, pins: dict, patches: list[Path]) -> dict:
    """ComfyUI and its custom nodes at their pinned commits, patched. Code only: the
    venv with PyTorch for the user's GPU is made on first run."""
    sources = [{"type": "git", "url": pins["comfyui"]["repo"],
                "commit": pins["comfyui"]["commit"]}]
    for name, pin in pins.get("custom_nodes", {}).items():
        sources.append({"type": "git", "url": pin["repo"], "commit": pin["commit"],
                        "dest": f"custom_nodes/{name}"})
    sources += [{"type": "patch", "path": str(p)} for p in patches]
    return {"name": "comfyui", "buildsystem": "simple", "sources": sources,
            # git sources arrive with their whole history (ComfyUI's is ~2 GB): code only.
            # The marker tells the engine this code is bundled (read-only, patched): its
            # venv and writable folders go to the data folder (comfy_setup.installer_for).
            "build-commands": ["find . -name .git -prune -exec rm -rf {} +",
                               f"mkdir -p {ENGINE_DIR}/vendor/ComfyUI",
                               f"cp -a . {ENGINE_DIR}/vendor/ComfyUI/",
                               f"touch {ENGINE_DIR}/vendor/ComfyUI/.imanganation-bundled"]}


def uv_module() -> dict:
    return {"name": "uv", "buildsystem": "simple",
            "sources": [{"type": "archive", "sha256": UV["sha256"], "url":
                         f"https://github.com/astral-sh/uv/releases/download/{UV['version']}"
                         "/uv-x86_64-unknown-linux-gnu.tar.gz"}],
            "build-commands": ["install -Dm755 uv /app/bin/uv"]}


LIBDIR_OPTION = {"meson": "--libdir=lib", "cmake": "-DCMAKE_INSTALL_LIBDIR=lib",
                 "cmake-ninja": "-DCMAKE_INSTALL_LIBDIR=lib"}


def libdir_into_lib(modules: list) -> None:
    """With flatpak-builder 1.4 on the GNOME 51 SDK, Meson and CMake install libraries
    into /app/lib64, where neither pkg-config nor the loader look (/app/lib is the
    Flatpak libdir): gexiv2 couldn't find exiv2, GEGL couldn't find babl, and GIMP's
    plug-in folder would move. Tell every Meson and CMake module, nested ones
    included, to use lib (autotools already does)."""
    for module in modules:
        if not isinstance(module, dict):
            continue
        option = LIBDIR_OPTION.get(module.get("buildsystem", ""))
        if option:
            opts = module.setdefault("config-opts", [])
            key = option.split("=")[0]
            if not any(o.startswith(key + "=") for o in opts):
                opts.append(option)
        libdir_into_lib(module.get("modules", []))


def _head(checkout: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, check=True,
                          capture_output=True, text=True).stdout.strip()


def make(upstream: dict, *, fork: Path | None, fork_git: str | None, fork_commit: str | None,
         app_id: str = APP_ID, repo: Path = REPO, engine: list[dict] | None = None,
         worktree: bool = False) -> dict:
    """``engine``: the engine/ComfyUI/uv modules to add after the plug-in (main() builds
    them; tests can leave them out)."""
    manifest = json.loads(json.dumps(upstream))  # a deep copy
    manifest.update({"app-id": app_id, "branch": "stable",
                     "runtime-version": RUNTIME_VERSION, "tags": ["GTK+3"],
                     "rename-icon": ICON})
    manifest.pop("desktop-file-name-prefix", None)  # no "(Nightly)" in the menu
    finish = manifest.setdefault("finish-args", [])
    if "--talk-name=org.freedesktop.Flatpak" not in finish:
        finish.append("--talk-name=org.freedesktop.Flatpak")
    modules = manifest["modules"]
    names = [m.get("name") if isinstance(m, dict) else None for m in modules]
    for name, pin in PINNED.items():
        module = modules[names.index(name)]
        module["sources"] = [{"type": "git", **pin}] + [
            s for s in module.get("sources", []) if s.get("type") != "git"]
    gimp = modules[names.index("gimp")]
    if fork_git:
        source = {"type": "git", "url": fork_git, "commit": fork_commit}
    elif worktree:  # uncommitted changes too; rebuilt on every build (dir sources are)
        source = {"type": "dir", "path": str(Path(fork).resolve()), "skip": GIT_CHURN}
    else:
        source = {"type": "git", "url": f"file://{Path(fork).resolve()}",
                  "commit": fork_commit or _head(Path(fork))}
    gimp["sources"] = [source] + [s for s in gimp["sources"] if s.get("type") != "dir"]
    gimp["config-opts"] = [opt for opt in gimp.get("config-opts", [])
                           if not opt.startswith("-Dbuild-id=")] + [f"-Dbuild-id={app_id}"]
    libdir_into_lib(modules)
    at = names.index("gimp") + 1
    modules[at:at] = [plugin_module(repo), *(engine or [])]
    return manifest


def pinned_fork() -> tuple[str, str]:
    """The fork's URL (.gitmodules) and the commit the superproject's HEAD pins."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(REPO), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    url = git("config", "-f", ".gitmodules", "submodule.imanganation-gimp.url")
    entry = git("ls-tree", "HEAD", "imanganation-gimp").split()
    if len(entry) < 3 or entry[0] != "160000":
        sys.exit("imanganation-gimp is not a submodule at HEAD; commit it first")
    return url, entry[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fork", type=Path, default=REPO / "imanganation-gimp",
                        help="local fork checkout (default: ./imanganation-gimp)")
    parser.add_argument("--fork-git", help="build the fork from this git URL instead")
    parser.add_argument("--fork-commit", help="the commit to build (with --fork-git)")
    parser.add_argument("--fork-pinned", action="store_true",
                        help="build the commit the imanganation-gimp submodule pins, from GitHub (CI, releases)")
    parser.add_argument("--fork-worktree", action="store_true",
                        help="build the local fork as it is, uncommitted changes included "
                        "(rebuilds GIMP every time)")
    parser.add_argument("--app-id", default=APP_ID)
    parser.add_argument("--out", type=Path, default=HERE / ".build" / f"{APP_ID}.json")
    args = parser.parse_args()
    if args.fork_pinned:
        args.fork_git, args.fork_commit = pinned_fork()
    if bool(args.fork_git) != bool(args.fork_commit):
        parser.error("--fork-git and --fork-commit go together")
    upstream_file = (args.fork / "build/linux/flatpak/org.gimp.GIMP-nightly.json")
    if not upstream_file.is_file():
        parser.error(f"no upstream manifest at {upstream_file}; pass --fork")
    from manganation.comfy_setup import load_pins

    pins = load_pins()
    # The engine's dir sources can't be checksummed, so it rebuilds every time, and so
    # does everything after it: last, so ComfyUI and uv stay cached.
    engine = [json.loads((HERE / "engine-deps.json").read_text()),
              comfyui_module(REPO, pins, comfy_patches(REPO, pins["comfyui"],
                                                       args.out.parent / "patches")),
              uv_module(), engine_module(REPO)]
    if not args.fork_git and not args.fork_worktree:
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               cwd=args.fork, capture_output=True, text=True).stdout.strip()
        if dirty:
            print(f"note: building the fork's HEAD ({_head(args.fork)[:10]}); its "
                  f"uncommitted changes aren't included (--fork-worktree to include them)",
                  file=sys.stderr)
    manifest = make(json.loads(upstream_file.read_text()), fork=args.fork,
                    fork_git=args.fork_git, fork_commit=args.fork_commit, app_id=args.app_id,
                    engine=engine, worktree=args.fork_worktree)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(args.out)


if __name__ == "__main__":
    main()
