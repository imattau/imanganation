"""Which engine the GIMP workspace starts: the Flatpak's own, or a host checkout."""

from __future__ import annotations

from pathlib import Path

from gimp.imanganation import services


def _checkout(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "pyproject.toml").write_text("")
    return path


def test_the_flatpak_starts_its_own_engine_with_its_own_comfyui(tmp_path):
    bundled = tmp_path / "app/bin/manganation"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("")
    env = {"XDG_DATA_HOME": str(tmp_path / ".var/app/x/data")}
    engine = services.find_engine(env, in_flatpak=True, plugin_file=tmp_path / "a/b/c/p.py",
                                  home=tmp_path, bundled_engine=bundled)
    assert engine.mode == "bundled"
    assert engine.data == tmp_path / ".var/app/x/data/imanganation"
    assert engine.projects == tmp_path / "Imanganation" and engine.log == engine.data / "engine.log"
    assert services.engine_command(engine) == [str(services.BUNDLED_ENGINE), "serve", "--comfyui"]
    assert services.comfy_command(engine, "8188") is None  # the engine starts ComfyUI


def test_a_developer_checkout_wins_when_named(tmp_path):
    checkout = _checkout(tmp_path / "imanganation")
    bundled = tmp_path / "manganation"
    bundled.write_text("")
    engine = services.find_engine({"IMANGANATION_HOME": str(checkout)}, in_flatpak=True,
                                  plugin_file=tmp_path / "a/b/c/p.py", home=tmp_path,
                                  bundled_engine=bundled)
    assert (engine.mode, engine.home, engine.projects) == ("host", checkout,
                                                           checkout / "projects")
    assert services.engine_command(engine, which=lambda name: "/usr/bin/uv") == [
        "/usr/bin/uv", "run", "manganation", "serve"]
    python = checkout / "vendor/ComfyUI/.venv/bin/python"
    assert services.comfy_command(engine, "8188") is None  # no ComfyUI venv yet
    python.parent.mkdir(parents=True)
    python.write_text("")
    assert services.comfy_command(engine, "8188")[:2] == [str(python), "main.py"]


def test_outside_the_flatpak_the_symlinked_checkout_or_the_note(tmp_path):
    checkout = _checkout(tmp_path / "repo")
    plugin = checkout / "gimp/imanganation/imanganation.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("")
    engine = services.find_engine({}, in_flatpak=False, plugin_file=plugin, home=tmp_path)
    assert (engine.mode, engine.home) == ("host", checkout)
    other = _checkout(tmp_path / "elsewhere")
    note = tmp_path / ".config/imanganation/engine-home"
    note.parent.mkdir(parents=True)
    note.write_text(f"{other}\n")
    found = services.find_engine({}, in_flatpak=False, plugin_file=tmp_path / "x/y/z/p.py",
                                 home=tmp_path)
    assert found.home == other
    nothing = services.find_engine({}, in_flatpak=False, plugin_file=tmp_path / "x/y/z/p.py",
                                   home=tmp_path / "empty")
    assert nothing.mode == "none" and services.engine_command(nothing) is None
