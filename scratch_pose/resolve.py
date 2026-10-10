import ast, json, os
import gi
gi.require_version("Gimp","3.0")
from gi.repository import Gimp, Gio
SRC = open("/home/lostcause/workspace/imanganation/gimp/imanganation/imanganation.py").read()
tree = ast.parse(SRC)
ns = {"Gimp": Gimp, "PARASITE": "imanganation-panelspec", "json": json}
for n in tree.body:
    if isinstance(n, ast.FunctionDef) and n.name in ("_panel_layer", "_panel_at", "_visible_renders", "_panel_under_selection"):
        exec(compile(ast.Module([n], []), "x", "exec"), ns)
img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path("/home/lostcause/workspace/imanganation/projects/rooftop.imanga/pages/page-001.xcf"))
def find(name):
    def walk(ls):
        for l in ls:
            if l.get_name().startswith(name): return l
            if l.is_group():
                f = walk(l.get_children())
                if f: return f
    return walk(img.get_layers())
for label, items in (("group 001", [find("001 Panel 1.1")]), ("group 002", [find("002 Panel 1.2")]), ("Background", [find("Background")]),
                      ("Template", [find("Template")]), ("Speech bubbles grp", [find("Speech bubbles")]), ("empty", [])):
    got = ns["_panel_layer"](items)
    print(label, "->", got.get_name()[:40] if got else None)
for pt in ((800, 600), (800, 1500), (800, 1900), (100, 100)):
    got = ns["_panel_at"](img, *pt); print("panel_at", pt, "->", got.get_name()[:40] if got else None)
for n in ("001 Panel 1.1", "002 Panel 1.2"):
    g = find(n); print(n, "group offsets/size", g.get_offsets()[1:], g.get_width(), g.get_height())
    for c in g.get_children(): print("   ", c.get_name()[:30], c.get_visible(), c.get_offsets()[1:], c.get_width(), c.get_height())

for box in ((100,100,300,300),(300,1000,500,1300),(300,900,500,1500),(0,0,1600,2400)):
    got = ns["_panel_under_selection"](img, *box); print("under", box, "->", got.get_name()[:30] if got else None)
t = find("Text #1"); print("text layer ->", (ns["_panel_layer"]([t]) or t).get_name()[:30], "(none expected)" if ns["_panel_layer"]([t]) is None else "")
img2 = Gimp.Image.new(500,500,Gimp.ImageBaseType.RGB)
outer = Gimp.GroupLayer.new(img2,"Panels"); img2.insert_layer(outer,None,0)
inner = Gimp.GroupLayer.new(img2,"001 Panel"); img2.insert_layer(inner,outer,0)
l = Gimp.Layer.new(img2,"render",200,200,Gimp.ImageType.RGBA_IMAGE,100,Gimp.LayerMode.NORMAL); img2.insert_layer(l,inner,0)
l.attach_parasite(Gimp.Parasite.new("imanganation-panelspec", Gimp.PARASITE_PERSISTENT, list(b"{}")))
print("nested outer ->", ns["_panel_layer"]([outer]).get_name(), "| inner ->", ns["_panel_layer"]([inner]).get_name())
