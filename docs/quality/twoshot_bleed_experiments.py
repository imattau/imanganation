"""Two-shot bleed: A = current prompt, B = no per-character tags in the global prompt."""
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw
from manganation.render import panel as P

S = Path(sys.argv[1]); configs = sys.argv[2].split(","); seeds = [int(x) for x in sys.argv[3].split(",")]
doc = json.load(open("projects/rooftop.imanga/project.json"))
spec_panel = doc["panels"][2]  # page 2 panel 1: Yuki + Akira two-shot
print("action:", spec_panel["action"], [c["name"] for c in spec_panel["characters"]])
orig = P.build_prompt

def global_counts_only(spec, style, character_tags=None):
    if len(spec.characters) < 2:
        return orig(spec, style, character_tags)
    counts = [ (character_tags or {}).get(n, [n])[0] for n in spec.characters ]  # gender tag first
    stub = {n: [] for n in spec.characters}
    base = orig(spec, style, stub)
    prefix = style.get("prompt_prefix", "").strip().rstrip(",")
    return base.replace(prefix, f"{prefix}, {', '.join(counts)}", 1)

from manganation.render import graphs as G
orig_ipa, orig_cond = G.with_regional_ipadapter, G.with_regional_conditioning
rows = []
for cfg in configs:
    P.build_prompt = orig if cfg in ("A", "D", "F", "G") else global_counts_only
    G.with_regional_ipadapter = (lambda g, **k: g) if cfg in ("C", "D") else orig_ipa
    G.with_regional_conditioning = (lambda g, **k: g) if cfg in ("E", "F") else orig_cond
    if cfg == "G":
        G.with_regional_conditioning = lambda g, regions, **k: orig_cond(
            g, regions=[{**r, "strength": 0.4} for r in regions], **k)
    tiles = []
    for seed in seeds:
        r = P.render_inline(spec_panel, doc["project"]["id"], 1024, 1024, seed=seed,
                            reading_order=doc["project"]["reading_order"],
                            outputs=S / "bleed")
        print(cfg, seed, "|", r.prompt[:150])
        tiles.append(Image.open(r.path).convert("RGB").resize((360, 360)))
    rows.append((cfg, tiles))
P.build_prompt = orig; G.with_regional_ipadapter, G.with_regional_conditioning = orig_ipa, orig_cond
W = 360 * len(seeds) + 60
sheet = Image.new("RGB", (W, 370 * len(rows)), "white"); d = ImageDraw.Draw(sheet)
for i, (cfg, tiles) in enumerate(rows):
    d.text((10, i * 370 + 170), cfg, fill="black")
    for j, t in enumerate(tiles):
        sheet.paste(t, (60 + j * 360, i * 370 + 5))
sheet.save(S / f"bleed_{''.join(configs)}.png")
