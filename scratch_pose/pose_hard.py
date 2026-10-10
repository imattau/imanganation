"""Two-character pose test through the real engine path (regional IP-Adapter + prompts)."""
import json, sys
from pathlib import Path
sys.path.insert(0, "../src")
import pose_test as pt
from manganation.render import panel as P, graphs
from manganation.render.comfy_client import ComfyClient

def person(cx, top, h, spec):
    """spec: dict of joint -> (dx, dy) in units of h from (cx, top)."""
    return [None if spec.get(i) is None else (cx + spec[i][0]*h*pt.H/pt.W, top + spec[i][1]*h) for i in range(18)]

def kp(d, eyes):
    pts = [d[k] for k in ("nose","neck","Rsho","Relb","Rwri","Lsho","Lelb","Lwri","Rhip","Rknee","Rank","Lhip","Lknee","Lank")]
    n = d["nose"]; dx = eyes
    return pts + [(n[0]-dx*.4, n[1]-.006), (n[0]+dx*.4, n[1]-.006), (n[0]-dx, n[1]), (n[0]+dx, n[1])]
akira = kp(dict(nose=(.30,.12),neck=(.31,.19),Rsho=(.24,.20),Relb=(.20,.30),Rwri=(.22,.38),Lsho=(.38,.20),Lelb=(.46,.27),Lwri=(.54,.34),
    Rhip=(.27,.38),Rknee=(.25,.52),Rank=(.24,.64),Lhip=(.35,.38),Lknee=(.36,.50),Lank=(.40,.62)), .035)
yuki = kp(dict(nose=(.66,.34),neck=(.64,.41),Rsho=(.58,.41),Relb=(.56,.37),Rwri=(.54,.34),Lsho=(.70,.42),Lelb=(.78,.50),Lwri=(.82,.58),
    Rhip=(.60,.62),Rknee=(.55,.76),Rank=(.52,.90),Lhip=(.67,.62),Lknee=(.72,.74),Lank=(.78,.88)), .035)
use_pose = (sys.argv[1] if len(sys.argv) > 1 else "pose") == "pose"
strength = float(sys.argv[2]) if len(sys.argv) > 2 else 0.8
out = Path("out"); out.mkdir(exist_ok=True)

def with_guide(graph, client, settings, models, guide, width, height, strength_):
    pt.W, pt.H = width, height
    p = pt.draw([yuki, akira], out / "skeleton_hard.png")
    up = client.upload_image(str(p))["name"]
    graph = {k: {**v, "inputs": dict(v["inputs"])} for k, v in graph.items()}
    graph["cn_image"] = {"class_type": "LoadImage", "inputs": {"image": up}}
    graph["cn_model"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": "noob_openpose.safetensors"}}
    s = graph["5"]["inputs"]
    graph["cn_apply"] = {"class_type": "ControlNetApplyAdvanced", "inputs": {
        "positive": s["positive"], "negative": s["negative"], "control_net": ["cn_model", 0],
        "image": ["cn_image", 0], "strength": strength, "start_percent": 0.0, "end_percent": 0.8}}
    s["positive"], s["negative"] = ["cn_apply", 0], ["cn_apply", 1]
    return graph

P._with_guide = with_guide
proj = json.load(open("../projects/rooftop.imanga/project.json"))
panel = next(p for p in proj["panels"] if p["id"] == "pnl_824568b3d439")
res = P.render_inline(panel, "prj_cd3562f1216d", 500, 1000, seed=11, engine="sdxl", face_pass=False,
                      guide=Path("out/skeleton_hard.png") if use_pose else None, guide_strength=strength)
name = f"hard_{'pose' if use_pose else 'nopose'}_{strength}.png"
Path(res.path).replace(out / name)
print("done", out / name, res.warnings, res.reference)
