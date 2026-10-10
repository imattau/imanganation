import json, os
from pathlib import Path
import gi
gi.require_version("Gimp","3.0")
from gi.repository import Gimp, Gio
PROJ = Path(os.environ["STAGE_PROJ"]); OUT = Path(os.environ["STAGE_OUT"])
pdb = Gimp.get_pdb()
def call(name, image, **props):
    proc = pdb.lookup_procedure(name); c = proc.create_config()
    c.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE); c.set_property("image", image)
    for k, v in props.items():
        c.set_core_object_array(k, v) if k == "drawables" else c.set_property(k, v)
    return proc.run(c)
def tree(layers, d=0):
    for l in layers:
        p = l.get_parasite("imanganation-panelspec")
        st = json.loads(bytes(p.get_data())).get("stage") if p else None
        print("  "*d, l.get_name()[:50], "| vis", l.get_visible(), "| mask", l.get_mask() is not None, "| size", l.get_width(), l.get_height(), l.get_offsets()[1:], "| stage", (st or {}).get("step"))
        if l.is_group(): tree(l.get_children(), d+1)
img = Gimp.Image.new(1000, 1000, Gimp.ImageBaseType.RGB)
bg = Gimp.Layer.new(img, "page", 1000, 1000, Gimp.ImageType.RGB_IMAGE, 100, Gimp.LayerMode.NORMAL)
img.insert_layer(bg, None, 0); bg.fill(Gimp.FillType.WHITE)
img.select_rectangle(Gimp.ChannelOps.REPLACE, 100, 100, 700, 800)
r = call("plug-in-imanganation-place-next-panel", img, **{"project-dir": Gio.File.new_for_path(str(PROJ)), "panel-number": 3})
print("PLACE", r.index(0)); base = r.index(1)
Gimp.Selection.none(img)
tree(img.get_layers())
results = []
for i, (box, prompt) in enumerate(((  (200, 150, 200, 200), "a yellow hat"), ((450, 500, 200, 200), "a blue backpack"))):
    img.select_rectangle(Gimp.ChannelOps.REPLACE, *box)
    group = base.get_parent()
    sel = [group] if group is not None else [base]
    r = call("plug-in-imanganation-develop-panel-stage", img, **{"drawables": sel, "prompt": prompt, "engine-url": "http://127.0.0.1:8791"})
    print("STAGE", i+1, r.index(0), r.index(1) if r.index(0) != Gimp.PDBStatusType.SUCCESS else r.index(1).get_name())
    Gimp.Selection.none(img)
    tree(img.get_layers())
flat = img.duplicate(); flat.flatten(); Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, flat, Gio.File.new_for_path(str(OUT/"stage_flat.png")), None)
xcf = OUT/"stage.xcf"; Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, img, Gio.File.new_for_path(str(xcf)), None)
re_ = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(str(xcf)))
print("REOPENED"); tree(re_.get_layers())
