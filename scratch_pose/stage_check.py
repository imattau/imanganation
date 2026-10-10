import sys, json
from pathlib import Path
sys.path.insert(0, "../src")
import numpy as np
from PIL import Image
from manganation.render.inpaint import inpaint_inline
src = Path("../projects/rooftop.imanga/takes").glob("pnl_622a14042ddb-tk_*.png")
src = sorted(src)[0]
im = Image.open(src); w, h = im.size; print("source", src.name, im.size)
mask = Image.new("L", (w, h), 0); mask.paste(255, (w//3, h//4, w//3 + w//4, h//4 + h//4))
Path("out").mkdir(exist_ok=True); mask.save("out/stage_mask.png")
r = inpaint_inline("prj_cd3562f1216d", src, Path("out/stage_mask.png"), prompt="a red scarf", outputs=Path("out/inp"), seed=3)
print(r.path, r.overlay_path)
full = Image.open(r.path).convert("RGB"); ov = Image.open(r.overlay_path); print("overlay", ov.mode, ov.size, "alpha bbox", ov.getchannel("A").getbbox())
comp = im.convert("RGBA"); comp.alpha_composite(ov)
d = np.abs(np.asarray(comp.convert("RGB")).astype(int) - np.asarray(full).astype(int))
print("max diff composite vs stitched:", d.max(), "mean", d.mean())
outside = np.asarray(ov.getchannel("A"))[~(np.asarray(mask) > 0)]
print("overlay alpha outside mask max:", outside.max())
