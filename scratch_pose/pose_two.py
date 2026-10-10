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

# Yuki: standing, hands on hips, elbows out (height h of full body)
yuki = person(0.70, 0.08, 0.84, {0:(0,.06),1:(0,.15),2:(-.10,.16),3:(-.20,.30),4:(-.10,.42),5:(.10,.16),6:(.20,.30),7:(.10,.42),
    8:(-.06,.52),9:(-.07,.76),10:(-.07,1.0),11:(.06,.52),12:(.07,.76),13:(.07,1.0),14:(-.015,.045),15:(.015,.045),16:(-.04,.06),17:(.04,.06)})
# Akira: seated on the ground, knees raised, hands on knees; head tilted up toward Yuki
akira = person(0.30, 0.42, 0.50, {0:(0,.08),1:(0,.22),2:(-.12,.24),3:(-.17,.45),4:(-.10,.62),5:(.12,.24),6:(.17,.45),7:(.10,.62),
    8:(-.07,.62),9:(-.10,.50),10:(-.10,.88),11:(.07,.62),12:(.10,.50),13:(.10,.88),14:(-.02,.05),15:(.02,.05),16:(-.05,.07),17:(.05,.07)})

use_pose = (sys.argv[1] if len(sys.argv) > 1 else "pose") == "pose"
strength = float(sys.argv[2]) if len(sys.argv) > 2 else 0.8
out = Path("out"); out.mkdir(exist_ok=True)

def with_guide(graph, client, settings, models, guide, width, height, strength_):
    pt.W, pt.H = width, height
    p = pt.draw([yuki, akira], out / "skeleton_two.png")
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
panel = next(p for p in proj["panels"] if p["id"] == "pnl_622a14042ddb")
res = P.render_inline(panel, "prj_cd3562f1216d", 1000, 700, seed=11, engine="sdxl", face_pass=False,
                      guide=Path("out/skeleton_two.png") if use_pose else None, guide_strength=strength)
name = f"two_{'pose' if use_pose else 'nopose'}_{strength}.png"
Path(res.path).replace(out / name)
print("done", out / name, res.warnings, res.reference)
