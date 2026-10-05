"""The Flatpak manifest: the fork's upstream manifest, made ours."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("make_manifest",
                                              ROOT / "packaging/flatpak/make_manifest.py")
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)

UPSTREAM = {
    "app-id": "org.gimp.GIMP.Nightly", "branch": "master", "runtime": "org.gnome.Platform",
    "runtime-version": "master", "sdk": "org.gnome.Sdk", "command": "gimp",
    "desktop-file-name-prefix": "(Nightly) ", "tags": ["GTK+3", "nightly"],
    "finish-args": ["--share=network", "--filesystem=host"],
    "modules": [
        {"name": "babl", "sources": [{"type": "git", "url": "x", "branch": "master"}]},
        {"name": "gegl", "sources": [{"type": "git", "url": "y", "branch": "master"},
                                     {"type": "patch", "path": "p.patch"}]},
        "shared-modules/x.json",
        {"name": "gimp", "sources": [{"type": "dir", "path": "../../.."},
                                     {"type": "shell", "commands": ["true"]}],
         "config-opts": ["-Dgi-docgen=disabled", "-Dbuild-id=org.gimp.GIMP.flatpak.nightly"]},
    ],
}


def _names(manifest):
    return [m["name"] if isinstance(m, dict) else m for m in manifest["modules"]]


def test_the_manifest_is_ours_with_pinned_deps_and_the_plugin(tmp_path):
    out = mm.make(UPSTREAM, fork=tmp_path / "fork", fork_git=None, fork_commit=None)
    assert (out["app-id"], out["branch"], out["runtime-version"]) == (
        "io.github.imattau.Imanganation", "stable", mm.RUNTIME_VERSION)
    assert "desktop-file-name-prefix" not in out and out["tags"] == ["GTK+3"]
    assert "--talk-name=org.freedesktop.Flatpak" in out["finish-args"]  # starts the engine
    modules = {m["name"]: m for m in out["modules"] if isinstance(m, dict)}
    assert modules["babl"]["sources"] == [{"type": "git", **mm.PINNED["babl"]}]
    assert modules["gegl"]["sources"][1] == {"type": "patch", "path": "p.patch"}  # kept
    gimp = modules["gimp"]
    assert gimp["sources"][0] == {"type": "dir", "path": str((tmp_path / "fork").resolve())}
    assert gimp["sources"][1]["type"] == "shell"
    assert gimp["config-opts"] == ["-Dgi-docgen=disabled",
                                   "-Dbuild-id=io.github.imattau.Imanganation"]  # no buildsystem
    assert _names(out) == ["babl", "gegl", "shared-modules/x.json", "gimp",
                           "imanganation-plugin"]
    assert UPSTREAM["app-id"] == "org.gimp.GIMP.Nightly"  # the input isn't changed


def test_the_plugin_module_installs_every_file_resolving_the_parser_symlink():
    plugin = mm.plugin_module(ROOT)
    names = sorted(s["dest-filename"] for s in plugin["sources"])
    on_disk = sorted(p.name for p in (ROOT / "gimp/imanganation").glob("*.py"))
    assert names == on_disk and "setup_ui.py" in names and "script_canonical.py" in names
    canonical = next(s for s in plugin["sources"] if s["dest-filename"] == "script_canonical.py")
    assert canonical["path"].endswith("src/manganation/script/formats/canonical.py")
    assert f"install -Dm755 imanganation.py {mm.PLUGIN_DIR}/imanganation.py" in plugin[
        "build-commands"]


def test_meson_and_cmake_modules_install_into_lib_even_nested():
    upstream = {**UPSTREAM, "modules": [
        {"name": "gexiv2", "buildsystem": "meson", "modules": [
            {"name": "exiv2", "buildsystem": "cmake-ninja", "config-opts": ["-DX=OFF"]}]},
        {"name": "set", "buildsystem": "cmake", "config-opts": ["-DCMAKE_INSTALL_LIBDIR=lib"]},
        {"name": "auto", "config-opts": ["--disable-x"]},
        *UPSTREAM["modules"]]}
    out = mm.make(upstream, fork=Path("fork"), fork_git=None, fork_commit=None)
    gexiv2 = out["modules"][0]
    assert gexiv2["config-opts"] == ["--libdir=lib"]
    assert gexiv2["modules"][0]["config-opts"] == ["-DX=OFF", "-DCMAKE_INSTALL_LIBDIR=lib"]
    assert out["modules"][1]["config-opts"] == ["-DCMAKE_INSTALL_LIBDIR=lib"]  # not twice
    assert out["modules"][2]["config-opts"] == ["--disable-x"]  # autotools: already lib


def test_a_release_builds_the_fork_from_a_pinned_commit(tmp_path):
    out = mm.make(UPSTREAM, fork=None, fork_git="https://github.com/imattau/imanganation-gimp.git",
                  fork_commit="0a3088b465")
    gimp = next(m for m in out["modules"] if isinstance(m, dict) and m["name"] == "gimp")
    assert gimp["sources"][0] == {"type": "git", "commit": "0a3088b465",
                                  "url": "https://github.com/imattau/imanganation-gimp.git"}


@pytest.mark.skipif(not (ROOT / "imanganation-gimp/build/linux/flatpak").is_dir(),
                    reason="the GIMP fork isn't checked out here")
def test_the_real_upstream_manifest_still_has_what_we_change():
    upstream = json.loads((ROOT / "imanganation-gimp/build/linux/flatpak/"
                                  "org.gimp.GIMP-nightly.json").read_text())
    out = mm.make(upstream, fork=ROOT / "imanganation-gimp", fork_git=None, fork_commit=None)
    assert {"babl", "gegl", "gimp", "imanganation-plugin"} <= set(_names(out))
