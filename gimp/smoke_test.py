"""Headless smoke test for the GIMP spike plug-in. Run via:

    flatpak run org.gimp.GIMP -i --batch-interpreter=python-fu-eval \\
        -b "exec(open('gimp/smoke_test.py').read())" --quit
"""
import json
import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, Gio

import os
ROOT = os.environ.get("IMANGANATION_ROOT", "/home/lostcause/workspace/imanganation")
OUT = os.environ.get("IMANGANATION_OUT", "/tmp")

# A B4-ish manga page at low res, with a panel frame selected.
img = Gimp.Image.new(1200, 1700, Gimp.ImageBaseType.RGB)
bg = Gimp.Layer.new(img, "page", 1200, 1700, Gimp.ImageType.RGB_IMAGE, 100, Gimp.LayerMode.NORMAL)
img.insert_layer(bg, None, 0)
bg.fill(Gimp.FillType.WHITE)
img.select_rectangle(Gimp.ChannelOps.REPLACE, 80, 100, 1040, 600)

proc = Gimp.get_pdb().lookup_procedure("plug-in-imanganation-place-panel")
cfg = proc.create_config()
cfg.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
cfg.set_property("image", img)
cfg.set_property("panel-file", Gio.File.new_for_path(ROOT + "/projects/_spike/panels/02_bw.png"))
cfg.set_property("panel-spec", json.dumps({"page": 7, "panel": 1, "camera": "wide shot", "seed": 42}))
res = proc.run(cfg)
print("STATUS", res.index(0))
layer = res.index(1)
print("LAYER", layer.get_name(), layer.get_width(), layer.get_height(), layer.get_offsets(), "mask", layer.get_mask() is not None)
p = layer.get_parasite("imanganation-panelspec")
print("PARASITE", bytes(p.get_data()).decode())

Gimp.Selection.none(img)
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, img, Gio.File.new_for_path(OUT + "/spike_page.xcf"), None)
flat = img.duplicate(); flat.flatten()
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, flat, Gio.File.new_for_path(OUT + "/spike_page.png"), None)

# Round-trip: reopen the XCF and confirm the spec survived on the layer.
img2 = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(OUT + "/spike_page.xcf"))
for item in img2.get_layers():
    for child in (item.get_children() if item.is_group() else []):
        par = child.get_parasite("imanganation-panelspec")
        print("REOPENED", item.get_name(), "/", child.get_name(), bytes(par.get_data()).decode() if par else None)

# --- Place Next Panel: walk a project in script order, artist draws the frames ---
import shutil
from pathlib import Path

proj = Path(OUT) / "smoke_project"
shutil.rmtree(proj, ignore_errors=True)
(proj / "panels").mkdir(parents=True)
shutil.copy(ROOT + "/projects/rooftop/panels.json", proj / "panels.json")
shutil.copy(ROOT + "/projects/_spike/panels/02_bw.png", proj / "panels/001.png")
shutil.copy(ROOT + "/projects/_spike/panels/04_ipadapter.png", proj / "panels/002_take2.png")
# 003 deliberately not rendered.

page = Gimp.Image.new(1200, 1700, Gimp.ImageBaseType.RGB)
bg = Gimp.Layer.new(page, "page", 1200, 1700, Gimp.ImageType.RGB_IMAGE, 100, Gimp.LayerMode.NORMAL)
page.insert_layer(bg, None, 0)
bg.fill(Gimp.FillType.WHITE)

nxt = Gimp.get_pdb().lookup_procedure("plug-in-imanganation-place-next-panel")


def place_next(frame=None):
    if frame:
        page.select_rectangle(Gimp.ChannelOps.REPLACE, *frame)
    else:
        Gimp.Selection.none(page)
    c = nxt.create_config()
    c.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    c.set_property("image", page)
    c.set_property("project-dir", Gio.File.new_for_path(str(proj)))
    return nxt.run(c)


for frame in [(620, 80, 500, 700), (80, 80, 500, 700), (80, 860, 1040, 760)]:
    r = place_next(frame)
    if r.index(0) == Gimp.PDBStatusType.SUCCESS:
        lyr = r.index(1)
        print("NEXT OK", lyr.get_parent().get_name(), lyr.get_offsets(),
              [ch.get_name() for ch in lyr.get_parent().get_children()])
    else:
        print("NEXT STOP", r.index(0), r.index(1))
print("CURSOR", (proj / "gimp_cursor.json").read_text())

Gimp.Selection.none(page)
flat = page.duplicate(); flat.flatten()
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, flat, Gio.File.new_for_path(OUT + "/smoke_next.png"), None)
