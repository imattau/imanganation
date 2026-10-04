"""Live test: Engine Status from an image that holds a placed panel.

Run it with the engine up (ideally with jobs queued) and again with it stopped:

    flatpak run org.gimp.GIMP -i --batch-interpreter=python-fu-eval \\
        -b "exec(open('gimp/status_smoke_test.py').read())" --quit
"""
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
        c.set_property(k, v)
    return proc.run(c)


page = Gimp.Image.new(1200, 1700, Gimp.ImageBaseType.RGB)
bg = Gimp.Layer.new(page, "page", 1200, 1700, Gimp.ImageType.RGB_IMAGE, 100,
                    Gimp.LayerMode.NORMAL)
page.insert_layer(bg, None, 0)
page.select_rectangle(Gimp.ChannelOps.REPLACE, 100, 100, 500, 560)
call("plug-in-imanganation-place-next-panel", page, **{
    "project-dir": Gio.File.new_for_path(str(PROJECT)), "panel-number": 1})
r = call("plug-in-imanganation-engine-status", page)
print("STATUS OK", r.index(0) == Gimp.PDBStatusType.SUCCESS)
for line in r.index(1).splitlines():
    print("REPORT |", line)
