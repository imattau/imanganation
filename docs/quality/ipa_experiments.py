"""Panel 1 (Akira), fixed seed/size; vary one factor per run. Writes to scratchpad."""
import sys
from pathlib import Path
from manganation.config import load_models, load_settings
from manganation.render import graphs
from manganation.render.comfy_client import ComfyClient
from manganation.render.panel import build_prompt, character_tags, load_style
from manganation.script.schema import Script

S = Path(sys.argv[1]); P = Path("projects/_gimp_smoke")
spec = Script.from_json((P / "panels.json").read_text()).panels[0]
style = load_style(); models = load_models(); c = ComfyClient()
prompt = build_prompt(spec, style, character_tags(P, spec.characters))
SEED, W, H = 2062860518, 960, 1024
sheet = c.upload_image(str(P / "characters/akira/base.png"))["name"]
figure = c.upload_image(str(S / "akira_figure.png"))["name"]
face = c.upload_image(str(S / "akira_face.png"))["name"]

def run(name, cfg=7.0, steps=30, ref=None, ipa="plus", **ipa_kw):
    g = graphs.txt2img(ckpt=models["checkpoints"]["primary"]["id"], prompt=prompt,
                       negative=style["negative"], width=W, height=H, seed=SEED,
                       prefix=f"exp_{name}", sampling=graphs.Sampling(steps, cfg))
    if ref:
        file = models["ipadapter"]["plus"]["id"] if ipa == "plus" else \
            "ip-adapter-plus-face_sdxl_vit-h.safetensors"
        g = graphs.with_ipadapter(g, ref_image=ref, ipadapter=file,
                                  clip_vision=models["ipadapter"]["clip_vision"]["id"],
                                  weight=ipa_kw.pop("weight", 0.6))
        g["11"]["inputs"].update(ipa_kw)
    (S / f"exp_{name}.png").write_bytes(c.run(g)[0]); print("done", name, flush=True)

run("A_noref_cfg7")
run("B_noref_cfg5", cfg=5.0)
run("C_sheet_w06_cfg7", ref=sheet)
run("D_figure_w06_cfg5", cfg=5.0, ref=figure)
run("E_figure_w05_end06_cfg5", cfg=5.0, ref=figure, weight=0.5, end_at=0.6)
run("F_face_facemodel_w06_cfg5", cfg=5.0, ref=face, ipa="face")
run("G_figure_w06_cfg5_KV", cfg=5.0, ref=figure, embeds_scaling="K+V")
