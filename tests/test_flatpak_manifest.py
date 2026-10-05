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
    out = mm.make(UPSTREAM, fork=tmp_path / "fork", fork_git=None, fork_commit="abc123")
    assert (out["app-id"], out["branch"], out["runtime-version"]) == (
        "io.github.imattau.Imanganation", "stable", mm.RUNTIME_VERSION)
    assert "desktop-file-name-prefix" not in out and out["tags"] == ["GTK+3"]
    assert "--talk-name=org.freedesktop.Flatpak" in out["finish-args"]  # starts the engine
    modules = {m["name"]: m for m in out["modules"] if isinstance(m, dict)}
    assert modules["babl"]["sources"] == [{"type": "git", **mm.PINNED["babl"]}]
    assert modules["gegl"]["sources"][1] == {"type": "patch", "path": "p.patch"}  # kept
    gimp = modules["gimp"]
    # the local checkout at a commit: git sources are cached by commit, dir ones never
    assert gimp["sources"][0] == {"type": "git", "commit": "abc123",
                                  "url": f"file://{(tmp_path / 'fork').resolve()}"}
    assert gimp["sources"][1]["type"] == "shell"
    assert gimp["config-opts"] == ["-Dgi-docgen=disabled",
                                   "-Dbuild-id=io.github.imattau.Imanganation"]  # no buildsystem
    assert _names(out) == ["babl", "gegl", "shared-modules/x.json", "gimp",
                           "imanganation-plugin"]
    assert UPSTREAM["app-id"] == "org.gimp.GIMP.Nightly"  # the input isn't changed


def test_the_plugin_module_installs_every_file_resolving_the_parser_symlink():
    plugin = mm.plugin_module(ROOT)
    names = sorted(s["dest-filename"] for s in plugin["sources"] if "dest-filename" in s)
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
    out = mm.make(upstream, fork=Path("fork"), fork_git=None, fork_commit="abc123")
    gexiv2 = out["modules"][0]
    assert gexiv2["config-opts"] == ["--libdir=lib"]
    assert gexiv2["modules"][0]["config-opts"] == ["-DX=OFF", "-DCMAKE_INSTALL_LIBDIR=lib"]
    assert out["modules"][1]["config-opts"] == ["-DCMAKE_INSTALL_LIBDIR=lib"]  # not twice
    assert out["modules"][2]["config-opts"] == ["--disable-x"]  # autotools: already lib


def test_the_worktree_option_builds_uncommitted_changes_as_a_dir(tmp_path):
    out = mm.make(UPSTREAM, fork=tmp_path / "fork", fork_git=None, fork_commit=None,
                  worktree=True)
    gimp = next(m for m in out["modules"] if isinstance(m, dict) and m["name"] == "gimp")
    assert gimp["sources"][0] == {"type": "dir", "path": str((tmp_path / "fork").resolve()),
                                  "skip": mm.GIT_CHURN}


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
    gimp = next(m for m in out["modules"] if isinstance(m, dict) and m["name"] == "gimp")
    assert gimp["sources"][0]["type"] == "git" and len(gimp["sources"][0]["commit"]) == 40
    assert {"babl", "gegl", "gimp", "imanganation-plugin"} <= set(_names(out))


def test_the_lettering_fonts_ship_with_the_plugin():
    commands = mm.plugin_module(ROOT)["build-commands"]
    fonts = [c for c in commands if "/app/share/fonts/imanganation/" in c]
    assert any("Bangers-Regular.ttf" in c for c in fonts)
    assert any("ComicNeue-Bold.ttf" in c for c in fonts)
    assert any("OFL" in c for c in fonts)  # the licence travels with the fonts


def test_the_engine_runs_from_the_bundle():
    engine = mm.engine_module(ROOT)
    dests = {s.get("dest") for s in engine["sources"]}
    assert {"src", "config"} <= dests
    launcher = next(s for s in engine["sources"] if s["type"] == "inline")
    assert f'"{mm.ENGINE_DIR}/src"' in launcher["contents"]
    assert "install -Dm755 manganation /app/bin/manganation" in engine["build-commands"]


def test_engine_deps_are_the_locked_versions_as_verified_wheels():
    deps = json.loads((ROOT / "packaging/flatpak/engine-deps.json").read_text())
    command = deps["build-commands"][0]
    assert "--no-index" in command and "--no-deps" in command  # offline, exactly these
    assert "--ignore-installed" in command  # never over the runtime's read-only copies
    wheels = [s["url"].rsplit("/", 1)[-1] for s in deps["sources"]]
    assert all(len(s["sha256"]) == 64 for s in deps["sources"])
    for wheel in wheels:
        assert wheel.endswith("-none-any.whl") or "cp314" in wheel or "abi3" in wheel, wheel
    lock = (ROOT / "uv.lock").read_text()
    for pin in command.split()[-len(wheels):]:
        name, version = pin.split("==")
        assert f'name = "{name}"\nversion = "{version}"' in lock, pin


def test_comfyui_is_the_pinned_code_with_its_node_and_patch(tmp_path):
    from manganation.comfy_setup import load_pins

    pins = load_pins()
    patch = tmp_path / "comfyui-01-clip_vision.patch"
    patch.write_text("x")
    module = mm.comfyui_module(ROOT, pins, [patch])
    sources = module["sources"]
    assert sources[0] == {"type": "git", "url": pins["comfyui"]["repo"],
                          "commit": pins["comfyui"]["commit"]}
    node = pins["custom_nodes"]["ComfyUI_IPAdapter_plus"]
    assert {"type": "git", "url": node["repo"], "commit": node["commit"],
            "dest": "custom_nodes/ComfyUI_IPAdapter_plus"} in sources
    assert sources[-1] == {"type": "patch", "path": str(patch)}
    assert module["build-commands"][0].startswith("find . -name .git")  # no 2 GB history
    from manganation.comfy_setup import BUNDLED_MARKER

    assert module["build-commands"][-1].endswith(f"/vendor/ComfyUI/{BUNDLED_MARKER}")
    uv = mm.uv_module()["sources"][0]
    assert uv["type"] == "archive" and len(uv["sha256"]) == 64 and mm.UV["version"] in uv["url"]


@pytest.mark.skipif(not (ROOT / "vendor/ComfyUI/.git").exists(),
                    reason="no local ComfyUI checkout to diff against")
def test_the_patch_is_a_real_diff_of_the_pinned_file(tmp_path):
    from manganation.comfy_setup import PATCHES, load_pins

    [patch] = mm.comfy_patches(ROOT, load_pins()["comfyui"], tmp_path)
    text = patch.read_text()
    assert text.startswith(f"--- a/{PATCHES[0].file}\n+++ b/{PATCHES[0].file}\n")
    assert "+        self.return_all_hidden_states = True" in text
