#!/usr/bin/env python3
"""Write the Flatpak manifest for the Imanganation GIMP build.

It starts from the fork's own upstream manifest (``imanganation-gimp/build/linux/
flatpak/org.gimp.GIMP-nightly.json``), so GIMP's dependency list stays whatever the
fork's GIMP version needs, and changes only what makes it ours:

- **App:** our app id, a stable GNOME runtime instead of nightly, our build id.
- **Meson and CMake modules** install into ``lib`` (they default to ``lib64`` here).
- **babl / GEGL:** pinned to the releases the fork is tested with (upstream builds
  their moving git master).
- **GIMP:** the fork, from a local checkout (default) or a pinned git commit.
- **The Imanganation plug-in**, installed with GIMP (``lib/gimp/3.0/plug-ins``).
- **Starting the engine:** ``--talk-name=org.freedesktop.Flatpak``, so the plug-in can
  start the engine and ComfyUI on the host (``flatpak-spawn --host``). They stay outside
  the sandbox: they need the GPU, CUDA and gigabytes of models.

Standard library only. ``build.sh`` runs it; see README.md.
"""

from __future__ import annotations

import argparse
import json
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
    return {"name": "imanganation-plugin", "buildsystem": "simple",
            "build-commands": commands, "sources": sources}


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


def make(upstream: dict, *, fork: Path | None, fork_git: str | None, fork_commit: str | None,
         app_id: str = APP_ID, repo: Path = REPO) -> dict:
    manifest = json.loads(json.dumps(upstream))  # a deep copy
    manifest.update({"app-id": app_id, "branch": "stable",
                     "runtime-version": RUNTIME_VERSION, "tags": ["GTK+3"]})
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
    source = ({"type": "git", "url": fork_git, "commit": fork_commit} if fork_git
              else {"type": "dir", "path": str(Path(fork).resolve())})
    gimp["sources"] = [source] + [s for s in gimp["sources"] if s.get("type") != "dir"]
    gimp["config-opts"] = [opt for opt in gimp.get("config-opts", [])
                           if not opt.startswith("-Dbuild-id=")] + [f"-Dbuild-id={app_id}"]
    libdir_into_lib(modules)
    modules.insert(names.index("gimp") + 1, plugin_module(repo))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fork", type=Path, default=REPO / "imanganation-gimp",
                        help="local fork checkout (default: ./imanganation-gimp)")
    parser.add_argument("--fork-git", help="build the fork from this git URL instead")
    parser.add_argument("--fork-commit", help="the commit to build (with --fork-git)")
    parser.add_argument("--app-id", default=APP_ID)
    parser.add_argument("--out", type=Path, default=HERE / ".build" / f"{APP_ID}.json")
    args = parser.parse_args()
    if bool(args.fork_git) != bool(args.fork_commit):
        parser.error("--fork-git and --fork-commit go together")
    upstream_file = (args.fork / "build/linux/flatpak/org.gimp.GIMP-nightly.json")
    if not upstream_file.is_file():
        parser.error(f"no upstream manifest at {upstream_file}; pass --fork")
    manifest = make(json.loads(upstream_file.read_text()), fork=args.fork,
                    fork_git=args.fork_git, fork_commit=args.fork_commit, app_id=args.app_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(args.out)


if __name__ == "__main__":
    main()
