"""Panel 1 (Akira), fixed seed/size: NoobAI Mark 1 + ViT-bigG at several weights."""
import sys
from pathlib import Path
from manganation.config import load_models
from manganation.render import graphs
from manganation.render.comfy_client import ComfyClient
from manganation.render.panel import build_prompt, character_tags, ipadapter_files, load_style
from manganation.script.schema import Script

S = Path(sys.argv[1]); P = Path("projects/_gimp_smoke")
spec = Script.from_json((P / "panels.json").read_text()).panels[0]
style = load_style(); models = load_models(); c = ComfyClient()
prompt = build_prompt(spec, style, character_tags(P, spec.characters))
SEED, W, H = 2062860518, 960, 1024
ref = c.upload_image(str(P / "characters/akira/base.png"))["name"]
ipa, clip = ipadapter_files(models, "noob_mark1")

def run(name, weight=None, **kw):
    g = graphs.txt2img(ckpt=models["checkpoints"]["primary"]["id"], prompt=prompt,
                       negative=style["negative"], width=W, height=H, seed=SEED,
                       prefix=f"noob_{name}", sampling=graphs.Sampling(30, 7.0))
    if weight is not None:
        g = graphs.with_ipadapter(g, ref_image=ref, ipadapter=ipa, clip_vision=clip, weight=weight)
        g["11"]["inputs"].update(kw)
    (S / f"noob_{name}.png").write_bytes(c.run(g)[0]); print("done", name, flush=True)

run("0_noref")
run("1_w03", 0.3)
run("2_w06", 0.6)
run("3_w08", 0.8)
