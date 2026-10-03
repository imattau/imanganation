"""Live end-to-end test: template page -> click a frame -> engine renders -> placed.

Needs ComfyUI and the engine (``uv run manganation serve``) running, plus a project
under projects/ (default ``_gimp_smoke``). Renders for real (~15 s per panel). Run:

    flatpak run org.gimp.GIMP -i --batch-interpreter=python-fu-eval \\
        -b "exec(open('gimp/render_smoke_test.py').read())" --quit
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
(PROJECT / "gimp_cursor.json").unlink(missing_ok=True)

# The artist's template: white page, black frame lines, a slanted gutter. Opaque and
# Normal mode on purpose — the plug-in must cope with that.
page = Gimp.Image.new(1200, 1700, Gimp.ImageBaseType.RGB)
tpl = Gimp.Layer.new(page, "Template B4", 1200, 1700, Gimp.ImageType.RGB_IMAGE, 100,
                     Gimp.LayerMode.NORMAL)
page.insert_layer(tpl, None, 0)
tpl.fill(Gimp.FillType.WHITE)
Gimp.context_set_foreground(Gegl.Color.new("black"))
page.select_rectangle(Gimp.ChannelOps.REPLACE, 60, 60, 1080, 1580)
page.select_rectangle(Gimp.ChannelOps.SUBTRACT, 72, 72, 1056, 1556)
tpl.edit_fill(Gimp.FillType.FOREGROUND)
page.select_polygon(Gimp.ChannelOps.REPLACE, [60, 700, 1140, 560, 1140, 580, 60, 720])
tpl.edit_fill(Gimp.FillType.FOREGROUND)
page.select_rectangle(Gimp.ChannelOps.REPLACE, 594, 60, 12, 650)
tpl.edit_fill(Gimp.FillType.FOREGROUND)
Gimp.Selection.none(page)

proc = Gimp.get_pdb().lookup_procedure("plug-in-imanganation-render-panel")
# Click order = manga reading order: top-right, top-left, bottom.
for x, y in [(900, 300), (300, 300), (600, 1200)]:
    # Fuzzy Select samples the selected layer; the plug-in must hand the template back.
    sampled = page.get_selected_layers()[0].get_name() if page.get_selected_layers() else None
    Gimp.context_set_sample_threshold(0.3)
    page.select_contiguous_color(Gimp.ChannelOps.REPLACE, tpl, x, y)
    c = proc.create_config()
    c.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    c.set_property("image", page)
    c.set_property("project-dir", Gio.File.new_for_path(str(PROJECT)))
    r = proc.run(c)
    if r.index(0) != Gimp.PDBStatusType.SUCCESS:
        print("RENDER FAIL", (x, y), r.index(1))
        continue
    layer = r.index(1)
    meta = json.loads(bytes(layer.get_parasite("imanganation-panelspec").get_data()))
    print("RENDER OK", (x, y), layer.get_name(), "render", meta["render"]["width"], "x",
          meta["render"]["height"], "seed", meta["render"]["seed"], "| selected before:", sampled)

print("STACK", [lyr.get_name() for lyr in page.get_layers()], "template mode",
      tpl.get_mode().value_nick)
print("CURSOR", (PROJECT / "gimp_cursor.json").read_text())
Gimp.Selection.none(page)
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, page,
               Gio.File.new_for_path(OUT + "/render_page.xcf"), None)
flat = page.duplicate()
flat.flatten()
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, flat,
               Gio.File.new_for_path(OUT + "/render_page.png"), None)
