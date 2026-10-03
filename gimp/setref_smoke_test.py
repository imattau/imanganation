"""Live test: set a character's reference from a placed panel layer.

Needs the engine (``uv run manganation serve``; ComfyUI not required) and a project
with panel 002 (Yuki) rendered (default ``_gimp_smoke``). **Adds reference versions
to that project's registry**; back up characters.json + characters/*/manifest.json
first if you care. Run:

    flatpak run org.gimp.GIMP -i --batch-interpreter=python-fu-eval \\
        -b "exec(open('gimp/setref_smoke_test.py').read())" --quit
"""
import json
import os
from pathlib import Path

import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, Gio  # noqa: E402

ROOT = os.environ.get("IMANGANATION_ROOT", "/home/lostcause/workspace/imanganation")
PROJECT = Path(ROOT) / "projects" / os.environ.get("IMANGANATION_PROJECT", "_gimp_smoke")
pdb = Gimp.get_pdb()


def call(name, image, **props):
    proc = pdb.lookup_procedure(name)
    c = proc.create_config()
    c.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    c.set_property("image", image)
    for k, v in props.items():
        if k == "drawables":
            c.set_core_object_array(k, v)
        else:
            c.set_property(k, v)
    return proc.run(c)


def yuki():
    return json.loads((PROJECT / "characters/yuki/manifest.json").read_text())


page = Gimp.Image.new(1200, 1700, Gimp.ImageBaseType.RGB)
bg = Gimp.Layer.new(page, "page", 1200, 1700, Gimp.ImageType.RGB_IMAGE, 100,
                    Gimp.LayerMode.NORMAL)
page.insert_layer(bg, None, 0)
bg.fill(Gimp.FillType.WHITE)
page.select_rectangle(Gimp.ChannelOps.REPLACE, 100, 100, 600, 800)
render = call("plug-in-imanganation-place-next-panel", page, **{
    "project-dir": Gio.File.new_for_path(str(PROJECT)), "panel-number": 2}).index(1)
print("PLACED", render.get_name(), "versions before:", [v["id"] for v in yuki()["versions"]])

# 1. A selection around her upper body, character typed in lower case.
page.select_rectangle(Gimp.ChannelOps.REPLACE, 200, 120, 400, 420)
r = call("plug-in-imanganation-set-character-reference", page, **{
    "drawables": [render], "character": "yuki"})
print("SET (selection)", r.index(0) == Gimp.PDBStatusType.SUCCESS,
      "| default:", yuki()["default_version"])

# 2. Whole panel group, no selection (hidden takes / text layers must not show).
Gimp.Selection.none(page)
r = call("plug-in-imanganation-set-character-reference", page, **{
    "drawables": [render.get_parent()], "character": "Yuki"})
print("SET (group)", r.index(0) == Gimp.PDBStatusType.SUCCESS,
      "| default:", yuki()["default_version"],
      "| versions:", [v["id"] for v in yuki()["versions"]])

# 3. Unknown name: refused, nothing created.
r = call("plug-in-imanganation-set-character-reference", page, **{
    "drawables": [render], "character": "Bob"})
print("UNKNOWN", r.index(0) != Gimp.PDBStatusType.SUCCESS, "|", r.index(1))
print("NO BOB", not (PROJECT / "characters/bob").exists())
