"""Feasibility: hand-built OpenPose skeletons -> NoobAI + noob_openpose ControlNet."""
import math, sys, tempfile
from pathlib import Path
from PIL import Image, ImageDraw
sys.path.insert(0, "../src")
from manganation.render import graphs
from manganation.render.comfy_client import ComfyClient

W, H = 832, 1216
# OpenPose body-18 limb order and colours
LIMBS = [(1,2),(1,5),(2,3),(3,4),(5,6),(6,7),(1,8),(8,9),(9,10),(1,11),(11,12),(12,13),
         (1,0),(0,14),(14,16),(0,15),(15,17)]
COLORS = [(255,0,0),(255,85,0),(255,170,0),(255,255,0),(170,255,0),(85,255,0),(0,255,0),
          (0,255,85),(0,255,170),(0,255,255),(0,170,255),(0,85,255),(0,0,255),(85,0,255),
          (170,0,255),(255,0,255),(255,0,170),(255,0,85)]

def draw(people, path):
    im = Image.new("RGB", (W, H), "black"); d = ImageDraw.Draw(im)
    for kp in people:  # kp: 18 (x,y) in 0..1 or None
        for (a, b), c in zip(LIMBS, COLORS):
            if kp[a] and kp[b]:
                (x1, y1), (x2, y2) = [(kp[i][0]*W, kp[i][1]*H) for i in (a, b)]
                cx, cy = (x1+x2)/2, (y1+y2)/2
                length = math.hypot(x2-x1, y2-y1); ang = math.degrees(math.atan2(y2-y1, x2-x1))
                # filled rotated ellipse, as the OpenPose renderer draws limbs
                e = Image.new("L", (W, H), 0); ed = ImageDraw.Draw(e)
                pts = []
                for t in range(0, 360, 10):
                    ex, ey = length/2*math.cos(math.radians(t)), 4*math.sin(math.radians(t))
                    r = math.radians(ang)
                    pts.append((cx+ex*math.cos(r)-ey*math.sin(r), cy+ex*math.sin(r)+ey*math.cos(r)))
                ed.polygon(pts, fill=255)
                im.paste(tuple(int(v*0.6) for v in c), mask=e)
        for i, p in enumerate(kp):
            if p:
                x, y = p[0]*W, p[1]*H
                d.ellipse((x-4, y-4, x+4, y+4), fill=COLORS[i])
    im.save(path); return path

# order: 0 nose 1 neck 2 Rsho 3 Relb 4 Rwri 5 Lsho 6 Lelb 7 Lwri 8 Rhip 9 Rknee 10 Rank
#        11 Lhip 12 Lknee 13 Lank 14 Reye 15 Leye 16 Rear 17 Lear
standing_reach = [(.50,.22),(.50,.30),(.43,.31),(.38,.42),(.36,.52),(.57,.31),(.66,.27),(.77,.21),
    (.45,.53),(.44,.68),(.44,.84),(.55,.53),(.56,.68),(.56,.84),(.48,.20),(.52,.20),(.46,.21),(.54,.21)]
sitting = [(.50,.30),(.50,.38),(.43,.39),(.40,.50),(.45,.58),(.57,.39),(.60,.50),(.55,.58),
    (.45,.60),(.30,.64),(.30,.82),(.55,.60),(.72,.64),(.72,.82),(.48,.28),(.52,.28),(.46,.29),(.54,.29)]
POSES = {"reach": [standing_reach], "sitting": [sitting]}

if __name__ == "__main__":
    name = sys.argv[1]; strength = float(sys.argv[2]) if len(sys.argv) > 2 else 0.8
    out = Path("out"); out.mkdir(exist_ok=True)
    guide = draw(POSES[name], out / f"skeleton_{name}.png")
    c = ComfyClient()
    up = c.upload_image(str(guide))["name"]
    g = graphs.txt2img(ckpt="noobaiXL.safetensors", width=W, height=H, seed=7, prefix="pose",
        prompt="masterpiece, best quality, 1girl, solo, long black hair, school uniform, simple background, white background, full body",
        negative="lowres, bad anatomy, bad hands, worst quality, text, watermark")
    g["cn_image"] = {"class_type": "LoadImage", "inputs": {"image": up}}
    g["cn_model"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": "noob_openpose.safetensors"}}
    s = g["5"]["inputs"]
    g["cn_apply"] = {"class_type": "ControlNetApplyAdvanced", "inputs": {
        "positive": s["positive"], "negative": s["negative"], "control_net": ["cn_model", 0],
        "image": ["cn_image", 0], "strength": strength, "start_percent": 0.0, "end_percent": 0.8}}
    s["positive"], s["negative"] = ["cn_apply", 0], ["cn_apply", 1]
    img = c.run(g)[0]
    (out / f"render_{name}_{strength}.png").write_bytes(img)
    print("done", out / f"render_{name}_{strength}.png")
