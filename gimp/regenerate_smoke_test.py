"""Live test: place a panel, regenerate it (same seed, then a new seed).

Needs ComfyUI and the engine (``uv run manganation serve``) running, and a project with
panel 001 rendered (default ``_gimp_smoke``; ideally also a ``001_hires.png`` from
refine_smoke_test.py so the hi-res -> original-seed path is exercised). Run:

    flatpak run org.gimp.GIMP -i --batch-interpreter=python-fu-eval \\
        -b "exec(open('gimp/regenerate_smoke_test.py').read())" --quit
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


def covers_tightly(layer, frame):
    """Covers the whole frame and overhangs it on at most one axis (cover-fit)."""
    _, x, y = layer.get_offsets()
    fx, fy, fw, fh = frame
    w, h = layer.get_width(), layer.get_height()
    covers = x <= fx and y <= fy and x + w >= fx + fw and y + h >= fy + fh
    return covers and (abs(w - fw) <= 1 or abs(h - fh) <= 1)


original_seed = json.loads((PROJECT / "panels" / "001.json").read_text())["seed"]
cursor = (PROJECT / "gimp_cursor.json").read_text() if (PROJECT / "gimp_cursor.json").exists() else None

page = Gimp.Image.new(1200, 1700, Gimp.ImageBaseType.RGB)
bg = Gimp.Layer.new(page, "page", 1200, 1700, Gimp.ImageType.RGB_IMAGE, 100,
                    Gimp.LayerMode.NORMAL)
page.insert_layer(bg, None, 0)
bg.fill(Gimp.FillType.WHITE)
page.select_rectangle(Gimp.ChannelOps.REPLACE, 100, 100, 500, 560)
first = call("plug-in-imanganation-place-next-panel", page, **{
    "project-dir": Gio.File.new_for_path(str(PROJECT)), "panel-number": 1}).index(1)
print("PLACED", Path(meta(first)["file"]).name)
page.select_rectangle(Gimp.ChannelOps.REPLACE, 700, 900, 300, 300)  # artist's own selection

prev = first
for label, same in (("SAME SEED", True), ("NEW SEED", False)):
    r = call("plug-in-imanganation-regenerate-panel", page, **{
        "drawables": [prev.get_parent()], "same-seed": same})
    if r.index(0) != Gimp.PDBStatusType.SUCCESS:
        print(label, "FAIL", r.index(1))
        break
    new = r.index(1)
    m = meta(new)
    print(label, new.get_name(), Path(m["file"]).name, "seed", m["render"]["seed"],
          "| equals original:", m["render"]["seed"] == original_seed,
          "| covers frame tightly:", covers_tightly(new, (100, 100, 500, 560)),
          "| mask:", new.get_mask() is not None, "| prev hidden:", not prev.get_visible())
    prev = new

print("SELECTION KEPT", Gimp.Selection.bounds(page)[1:] == (True, 700, 900, 1000, 1200))
after = (PROJECT / "gimp_cursor.json").read_text() if (PROJECT / "gimp_cursor.json").exists() else None
print("CURSOR UNCHANGED", after == cursor)
print("GROUP", [c.get_name() for c in first.get_parent().get_children()])
Gimp.Selection.none(page)
flat = page.duplicate()
flat.flatten()
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, flat,
               Gio.File.new_for_path(OUT + "/regenerate_page.png"), None)
