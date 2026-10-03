# Live quality check — 2026-10-04

**Verdict: renders *without* IP-Adapter are good; every render *with* IP-Adapter is
broken.** Not fixable by tuning weights.

## What was run

All five rooftop panels through the engine (`manganation render`, project
`_gimp_smoke`), then a controlled matrix on panel 1 (same seed `2062860518`, same
960×1024 size, one change per run). Script: `ipa_experiments.py`.

## Findings

1. **Lower weights didn't fix it** (`2026-10-04_before_after.png`). Phase 4's 0.6
   single / 0.5 regional made images washed out and glowing, and two new failure modes
   showed up: Yuki's panel is a grid of repeated heads, and panel 4 shows three Akiras.
2. **No reference = clean** (`2026-10-04_ipa_experiments.png`, A/B). Good colour, follows
   the action (sitting at the fence, lunchbox), and the appearance tags carry identity
   (blue tips, amber eyes). cfg 7 vs 5 hardly matters.
3. **Any IP-Adapter = 2×2 tiling,** even with a single-figure crop as reference (D, E,
   G), with the face model (F), and at **weight 0.2** (`2026-10-04_ipa_weight0_vs_02.png`).
   At weight 0.0 the output is pixel-identical to no reference, so the hook itself is
   harmless. The damage is in the IP-Adapter contribution.
4. **Ruled out:** the CLIP-vision file (genuine ViT-H: 32 layers, 1280 wide); ComfyUI's
   config detection (picks ViT-H); encoder output (penultimate states `[1,257,1280]`,
   correct); preprocessing (standard `clip_preprocess`); attention backend (PyTorch
   SDPA, not the int8 kitchen backend).
5. **Design sheets make bad references anyway.** `characters/*/base.png` are multi-view
   sheets (Akira: two figures; Yuki: six heads plus full body). IP-Adapter copies
   composition, so it reproduces the grid. References should be one figure, roughly
   square.

## Most likely cause

`ip-adapter-plus_sdxl_vit-h` was trained on **base SDXL**. NoobAI-XL is a heavily
retrained anime model, and IP-Adapters trained on base SDXL are known to work poorly on
NoobAI/Illustrious-family checkpoints. NoobAI ships its own **IP-Adapter Mark 1**,
trained on NoobAI-XL EPS v1.1, which uses **CLIP-ViT-bigG** (not ViT-H). Not yet tested:
it needs a download. A fallback suspect is the unmaintained `ComfyUI_IPAdapter_plus`
(last commit 2025-04-14) against ComfyUI 0.38.

## Options

| Option | Cost | Notes |
|---|---|---|
| A. Disable IP-Adapter for now (`defaults.ipadapter` weights → 0) | none | Clean renders today; identity from appearance tags only, so drift is likely across panels |
| B. NoobAI IP-Adapter Mark 1 + CLIP-ViT-bigG | ~1 GB + ~3.7 GB download | The matched pairing; graphs need the bigG clip name. Official source still to confirm (Civitai is age-gated in AU) |
| C. Per-character LoRA (PLAN Phase 6) | training time per character | Strongest consistency for recurring characters on anime checkpoints |
| Also, whatever else is chosen | small | Store a single-figure, square-ish reference per character (crop from the sheet) instead of the multi-view sheet |

## Fix applied (same day): option B + single-figure references

| Change | Where |
|---|---|
| NoobAI **IP-Adapter Mark 1** + **CLIP-ViT-bigG** downloaded, sha256-verified | `models/ipadapter/`; sources + hashes in `config/models.yaml` |
| Adapter is a setting; each adapter names its encoder, so they can't be mismatched | `defaults.ipadapter.adapter: noob_mark1`; `render/panel.py::ipadapter_files` |
| Single-character weight **0.45** (sweep: 0.3 light, 0.6+ reference's white background bleeds in) | `settings.yaml`, `config.py` |
| References are now **one figure, square, cowboy shot** (no "reference sheet" tag; sheet terms in the negative) | `characters/design.py`, `generator.py` (1024²) |
| Single-character panels get `solo` + duplicate-figure negatives (wide frames were adding a second copy) | `render/panel.py::build_prompt/build_negative` |

Results (`2026-10-04_noob_mark1_weights.png`, `2026-10-04_fix_before_after.png`,
`2026-10-04_panel4_solo.png`): **no tiling, no glow.** Akira (brown hair with blue
tips, amber eyes, red tie, watch) and Yuki (silver hair, sailor uniform, snowflake pin)
stay consistent across panels, and single-character scenes keep their setting.

### Still open

- **Two-shot identity bleed** (panel 3): regional references at 0.5 with Mark 1 give
  Yuki brown hair and an Akira-like face. Tune `weight_regional` / feather, or try
  stronger per-region prompt conditioning.
- **Panel 5 missing Akira:** `panels.json` lists only `["Yuki"]` for "Yuki drags Akira
  by the wrist". This is a script-parser issue (character extraction), not rendering.
- Expressions and actions are only loosely followed (panel 4 "sighs" renders a grin).
- `projects/rooftop` still has the old multi-view sheets. Regenerate with
  `uv run manganation character design rooftop --force`.
