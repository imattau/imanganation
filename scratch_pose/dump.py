import json, os
import gi
gi.require_version("Gimp","3.0")
from gi.repository import Gimp, Gio
def tree(layers, d=0):
    for l in layers:
        p = l.get_parasite("imanganation-panelspec")
        pp = l.get_parasite("imanganation-panel")
        info = ""
        if p:
            m = json.loads(bytes(p.get_data())); info = f"spec seq={m.get('seq')} file={os.path.basename(str(m.get('file')))} stage={bool(m.get('stage'))}"
        print("  "*d + l.get_name()[:45], "| grp" if l.is_group() else "", "| vis", l.get_visible(), "|", info, "| panelref" if pp else "")
        if l.is_group(): tree(l.get_children(), d+1)
for f in ("page-001.xcf","page-002.xcf"):
    img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path("/home/lostcause/workspace/imanganation/projects/rooftop.imanga/pages/"+f))
    print("==", f, img.get_width(), img.get_height()); tree(img.get_layers())
