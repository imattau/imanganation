"""Live test: select an area over a placed panel and inpaint it.

Needs ComfyUI and the engine (``uv run manganation serve``) running and panel 001
rendered in the project (default ``_gimp_smoke``). Saves the flattened page before and
after to IMANGANATION_OUT so the caller can check pixels outside the selection. Run:

    flatpak run org.gimp.GIMP -i --batch-interpreter=python-fu-eval \\
        -b "exec(open('gimp/inpaint_smoke_test.py').read())" --quit
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


def save_flat(image, name):
    flat = image.duplicate()
    flat.flatten()
    Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, flat, Gio.File.new_for_path(f"{OUT}/{name}"),
                   None)
    flat.delete()


page = Gimp.Image.new(1200, 1700, Gimp.ImageBaseType.RGB)
bg = Gimp.Layer.new(page, "page", 1200, 1700, Gimp.ImageType.RGB_IMAGE, 100,
                    Gimp.LayerMode.NORMAL)
page.insert_layer(bg, None, 0)
bg.fill(Gimp.FillType.WHITE)
page.select_rectangle(Gimp.ChannelOps.REPLACE, 100, 100, 500, 560)
old = call("plug-in-imanganation-place-next-panel", page, **{
    "project-dir": Gio.File.new_for_path(str(PROJECT)), "panel-number": 1}).index(1)
# The artist's move: nudge and shrink the take inside its frame; the inpaint must
# land on exactly that geometry, not a re-fit.
old.scale(old.get_width() - 20, old.get_height() - 20, False)
old.set_offsets(old.get_offsets()[1] + 7, old.get_offsets()[2] + 5)
print("PLACED", Path(json.loads(bytes(old.get_parasite("imanganation-panelspec")
                                           .get_data()))["file"]).name,
      old.get_width(), old.get_height(), old.get_offsets()[1:])
Gimp.Selection.none(page)
save_flat(page, "inpaint_before.png")

# Feathered ellipse over the lower part of the panel; selected layer is the page bg
# (as after a placement, when the template is selected), so the plug-in must find
# the panel under the selection itself.
page.select_ellipse(Gimp.ChannelOps.REPLACE, 300, 450, 180, 160)
Gimp.Selection.feather(page, 12)
r = call("plug-in-imanganation-inpaint-selection", page, **{
    "drawables": [bg], "prompt": "red apple"})
if r.index(0) != Gimp.PDBStatusType.SUCCESS:
    print("INPAINT FAIL", r.index(1))
else:
    new = r.index(1)
    m = json.loads(bytes(new.get_parasite("imanganation-panelspec").get_data()))
    print("INPAINTED", new.get_name(), Path(m["file"]).name,
          "| exact geometry:", (new.get_width(), new.get_height(), new.get_offsets()[1:])
          == (old.get_width(), old.get_height(), old.get_offsets()[1:]),
          "| mask:", new.get_mask() is not None, "| old hidden:", not old.get_visible(),
          "| prompt:", m["inpainted"]["prompt"], "| source:", Path(m["inpainted"]["source"]).name)
    print("SELECTION KEPT", Gimp.Selection.bounds(page)[1])
    print("TEMP LAYER GONE", not any("mask (temp)" in lyr.get_name() for lyr in page.get_layers()))
Gimp.Selection.none(page)
save_flat(page, "inpaint_after.png")
