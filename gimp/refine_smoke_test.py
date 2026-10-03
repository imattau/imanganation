"""Live test: place a panel into a frame, refine it, check the hi-res swap.

Needs ComfyUI and the engine (``uv run manganation serve``) running, and a project with
a rendered panel 001 (default ``_gimp_smoke``). Run:

    flatpak run org.gimp.GIMP -i --batch-interpreter=python-fu-eval \\
        -b "exec(open('gimp/refine_smoke_test.py').read())" --quit
"""
import json
import os
from pathlib import Path

import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, Gio  # noqa: E402

ROOT = os.environ.get("IMANGANATION_ROOT", "/home/lostcause/workspace/imanganation")
PROJECT = Path(ROOT) / "projects" / os.environ.get("IMANGANATION_PROJECT", "_gimp_smoke")
OUT = os.environ.get("IMANGANATION_OUT", "/tmp")
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


def meta(layer):
    return json.loads(bytes(layer.get_parasite("imanganation-panelspec").get_data()))


(PROJECT / "panels" / "001_hires.png").unlink(missing_ok=True)
page = Gimp.Image.new(1200, 1700, Gimp.ImageBaseType.RGB)
bg = Gimp.Layer.new(page, "page", 1200, 1700, Gimp.ImageType.RGB_IMAGE, 100,
                    Gimp.LayerMode.NORMAL)
page.insert_layer(bg, None, 0)
bg.fill(Gimp.FillType.WHITE)
page.select_rectangle(Gimp.ChannelOps.REPLACE, 100, 100, 500, 560)

r = call("plug-in-imanganation-place-next-panel", page, **{
    "project-dir": Gio.File.new_for_path(str(PROJECT)), "panel-number": 1})
render = r.index(1)
print("PLACED", render.get_name(), render.get_width(), render.get_height(),
      render.get_offsets()[1:], "file:", Path(meta(render)["file"]).name)

page.set_selected_layers([render.get_parent()])  # select the group, like an artist might
r = call("plug-in-imanganation-refine-panel", page, drawables=[render.get_parent()])
if r.index(0) != Gimp.PDBStatusType.SUCCESS:
    print("REFINE FAIL", r.index(1))
else:
    hires = r.index(1)
    m = meta(hires)
    print("REFINED", hires.get_name(), "same footprint:",
          (hires.get_width(), hires.get_height(), hires.get_offsets()[1:])
          == (render.get_width(), render.get_height(), render.get_offsets()[1:]),
          "| mask:", hires.get_mask() is not None, "| old hidden:", not render.get_visible(),
          "| engine size:", m["refined"]["width"], "x", m["refined"]["height"],
          "| source:", Path(m["refined"]["source"]).name)
    print("SELECTION KEPT", Gimp.Selection.bounds(page)[1:])

# Newest file wins: the fresh hi-res now beats the older render.
page.select_rectangle(Gimp.ChannelOps.REPLACE, 650, 100, 450, 560)
r = call("plug-in-imanganation-place-next-panel", page, **{
    "project-dir": Gio.File.new_for_path(str(PROJECT)), "panel-number": 1})
print("NEWEST TAKE", Path(meta(r.index(1))["file"]).name)

Gimp.Selection.none(page)
flat = page.duplicate()
flat.flatten()
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, flat,
               Gio.File.new_for_path(OUT + "/refine_page.png"), None)
