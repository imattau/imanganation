"""Live test: placement layers steer where each character goes in a rendered frame.

Needs ComfyUI and the engine (``uv run manganation serve``) and a legacy project with
a two-character panel 3 (default ``_gimp_smoke``: Yuki + Akira). Renders twice with
the same seed: without placement layers (default right-to-left bands: Yuki on the
right), then with Yuki placed on the LEFT and Akira on the RIGHT. Run:

    flatpak run org.gimp.GIMP -i --batch-interpreter=python-fu-eval \\
        -b "exec(open('gimp/placement_smoke_test.py').read())" --quit
"""
import json
import os
from pathlib import Path

import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gegl, Gimp, Gio  # noqa: E402

ROOT = os.environ.get("IMANGANATION_ROOT", "/home/lostcause/workspace/imanganation")
PROJECT = Path(ROOT) / "projects" / os.environ.get("IMANGANATION_PROJECT", "_gimp_smoke")
OUT = os.environ.get("IMANGANATION_OUT", "/tmp")
FRAME = (100, 100, 1000, 620)
pdb = Gimp.get_pdb()


def page():
    img = Gimp.Image.new(1200, 1700, Gimp.ImageBaseType.RGB)
    bg = Gimp.Layer.new(img, "page", 1200, 1700, Gimp.ImageType.RGB_IMAGE, 100,
                        Gimp.LayerMode.NORMAL)
    img.insert_layer(bg, None, 0)
    bg.fill(Gimp.FillType.WHITE)
    return img


def blob(img, name, boxes):
    layer = Gimp.Layer.new(img, name, 1200, 1700, Gimp.ImageType.RGBA_IMAGE, 40,
                           Gimp.LayerMode.NORMAL)  # artists keep guides faint
    img.insert_layer(layer, None, 0)
    layer.fill(Gimp.FillType.TRANSPARENT)
    Gimp.context_set_foreground(Gegl.Color.new("red"))
    for (x, y, w, h) in boxes:
        img.select_ellipse(Gimp.ChannelOps.REPLACE, x, y, w, h)
        layer.edit_fill(Gimp.FillType.FOREGROUND)
    Gimp.Selection.none(img)


def render(img, label):
    img.select_rectangle(Gimp.ChannelOps.REPLACE, *FRAME)
    proc = pdb.lookup_procedure("plug-in-imanganation-render-panel")
    c = proc.create_config()
    c.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    c.set_property("image", img)
    c.set_property("project-dir", Gio.File.new_for_path(str(PROJECT)))
    c.set_property("panel-number", 3)
    c.set_property("seed", 4321)
    r = proc.run(c)
    if r.index(0) != Gimp.PDBStatusType.SUCCESS:
        print(label, "FAIL", r.index(1))
        return
    layer = r.index(1)
    meta = json.loads(bytes(layer.get_parasite("imanganation-panelspec").get_data()))
    print(label, "OK", Path(meta.get("file", "")).name)
    Gimp.Selection.none(img)
    flat = img.duplicate()
    for lyr in flat.get_layers():
        if lyr.get_name().lower().startswith("placement:"):
            lyr.set_visible(False)
    flat.flatten()
    flat.crop(FRAME[2], FRAME[3], FRAME[0], FRAME[1])
    Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, flat,
                   Gio.File.new_for_path(f"{OUT}/placement_{label}.png"), None)


render(page(), "default")
img = page()
blob(img, "placement: Yuki", [(160, 150, 380, 560), (300, 900, 300, 400)])  # 2nd is off-frame
blob(img, "Placement: akira", [(680, 180, 380, 520)])
blob(img, "placement: Bob", [(500, 300, 200, 200)])  # not in this panel: ignored
render(img, "placed")
