"""Live test: Regenerate Panel with Keep composition (ControlNet on the take's edges).

Needs ComfyUI, the engine (``uv run manganation serve``) and panel 001 rendered in the
project (default ``_gimp_smoke``). Places panel 1, then regenerates it on a *new* seed
with and without Keep composition; saves both crops to IMANGANATION_OUT. Run:

    flatpak run org.gimp.GIMP -i --batch-interpreter=python-fu-eval \\
        -b "exec(open('gimp/keep_composition_smoke_test.py').read())" --quit
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


def save_crop(image, layer, name):
    flat = image.duplicate()
    for lyr in flat.get_layers():
        pass
    flat.flatten()
    _, x, y = layer.get_offsets()
    flat.crop(500, 560, 100, 100)
    Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, flat, Gio.File.new_for_path(f"{OUT}/{name}"), None)
    flat.delete()


page = Gimp.Image.new(1200, 1700, Gimp.ImageBaseType.RGB)
bg = Gimp.Layer.new(page, "page", 1200, 1700, Gimp.ImageType.RGB_IMAGE, 100,
                    Gimp.LayerMode.NORMAL)
page.insert_layer(bg, None, 0)
bg.fill(Gimp.FillType.WHITE)
page.select_rectangle(Gimp.ChannelOps.REPLACE, 100, 100, 500, 560)
original = call("plug-in-imanganation-place-next-panel", page, **{
    "project-dir": Gio.File.new_for_path(str(PROJECT)), "panel-number": 1}).index(1)
Gimp.Selection.none(page)
save_crop(page, original, "keep_original.png")
source = json.loads(bytes(original.get_parasite("imanganation-panelspec").get_data()))["file"]
print("PLACED", Path(source).name)

for label, keep in (("free", False), ("kept", True)):
    r = call("plug-in-imanganation-regenerate-panel", page, **{
        "drawables": [original.get_parent()], "same-seed": False, "keep-composition": keep})
    if r.index(0) != Gimp.PDBStatusType.SUCCESS:
        print(label, "FAIL", r.index(1))
        continue
    new = r.index(1)
    meta = json.loads(bytes(new.get_parasite("imanganation-panelspec").get_data()))
    print(label.upper(), Path(meta["file"]).name, "seed", meta["render"]["seed"])
    save_crop(page, new, f"keep_{label}.png")
    new.set_visible(False)
    original.set_visible(True)
