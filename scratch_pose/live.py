import json, sys
from pathlib import Path
sys.path.insert(0, "../src")
from manganation.render import panel as P
proj = json.load(open("../projects/rooftop.imanga/project.json"))
panel = next(p for p in proj["panels"] if p["id"] == "pnl_824568b3d439")
panel["poses"] = {"Yuki": "drag_by_wrist.lead", "Akira": "drag_by_wrist.follow"}
res = P.render_inline(panel, "prj_cd3562f1216d", 500, 1000, seed=11, engine="sdxl", face_pass=False)
Path(res.path).replace("out/live_drag.png"); print(res.warnings, res.character_prompts)
