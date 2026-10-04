# Phase 6a — Panel Refinement (two-pass hi-res fix)

**Status: COMPLETE.** A finished ~1 MP panel can now be re-rendered at 2× (or more)
so it holds up when the artist enlarges it in GIMP. Output is a new take
(`{seq:03d}_hires.png`, then `_hires_takeNN.png`, + sidecar), never overwriting an
existing take.

## Pipeline

```
source panel ──▶ [polish]  low-denoise img2img at native size (one VAE tile)
              ──▶ [upscale] Real-ESRGAN anime → final target size (lanczos)
              ──▶ panels/{seq:03d}_hires[_takeNN].png
```

1. **Polish** — a low-`denoise` img2img pass on the checkpoint. Most of the signal is
   retained from the source latent, so composition and identity survive; the pass
   re-synthesises fine detail and removes softness.
2. **Upscale** — `ImageUpscaleWithModel` with `RealESRGAN_x4plus_anime_6B`, then a
   lanczos resize to the exact target. The final enlargement is real
   super-resolution, not interpolation.

Set `denoise: 0` to skip the polish and get a pure upscale.

## Two bugs found and fixed

Both were caught by rendering live and *looking at the image*:

- **Blocky chroma seams** — polishing at the final size (2048²) forced the VAE past
  one tile, and ComfyUI fell back to **tiled VAE encoding**, which leaves visible
  block boundaries. Fixed by polishing at the *native* size (≤ ~1.3 MP,
  `POLISH_MAX_PIXELS`) so encode/decode stays in a single tile; the enlargement then
  happens in the upscaler, not the VAE.
- **Washed-out halos** — the first download was a *general* Real-ESRGAN, not the
  anime 6B model (the filename was right but the weights were wrong). Swapping in the
  real `RealESRGAN_x4plus_anime_6B.pth` (17.9 MB, xinntao release) removed the halos.

## Config

```yaml
defaults:
  refiner:
    enabled: true
    upscaler: realesrgan  # models.yaml -> upscalers role
    scale: 2.0            # target multiplier of the source panel
    denoise: 0.25         # img2img polish strength (0 = pure upscale)
    steps: 16
    cfg: 5.0
    max_pixels: 16777216  # 4096x4096 guard
```

`max_pixels` clamps the target (aspect preserved) so a large `scale` cannot blow the
budget. Re-refining always starts from the **render**, never a previous `_hires`, so
scales never compound.

## No compounding (scale is relative to the render)

`scale` always means "× the panel's **original render**", whatever take is refined. The
refiner follows the take's sidecar chain back (`render_origin`: refine sidecars carry
`upscaler` + `source`, inpaint sidecars `mask` + `source`) to the plain render, and
sizes the target from that. So:

- Refining a hi-res, or an inpaint made on one, at the default 2× is refused
  ("already … at or beyond 2× its render"). Before this guard, an explicit source
  bypassed it: 2× of a 2× gave 3840×4352 from a 960×1088 render.
- A larger scale still works on a derived take and keeps its edits: 3× of an inpainted
  2× take enlarges it by the remaining 1.5×.
- The polish prompt comes from the render's sidecar. A hi-res has no prompt, and an
  inpaint's is only the patch prompt ("red apple"), which would steer the whole panel.
- A chain that loops (an older take file was overwritten) or whose render is gone is
  refused, since the original size is then unknown.
- Takes are never overwritten (`_hires_takeNN`), which keeps these chains and the
  GIMP layers' recorded files valid.

## Interfaces

| Surface | Entry point |
|---|---|
| Python | `render.refiner.refine_panel(project, seq, scale=, denoise=, seed=)` |
| CLI | `manganation refine <project> <seq> [--scale 2.0] [--denoise 0.25]` |
| API | `POST /refine` → job (`kind: "refine"`), poll `GET /jobs/{id}`. Optional `source` (must be inside the project) refines an exact take |
| GIMP | *Imanganation → Refine Panel (Hi-res)…* on a placed panel. Swaps the result in at the same size and mask (`docs/gimp-plugin.md`) |

## Verified

- **`tests/test_refiner.py`**: target sizing (grid rounding, pixel cap, aspect,
  reject bad input), upscaler-role resolution, graph shape (polish-at-native →
  upscale-to-target, zero-denoise skip), re-refine source selection, `/refine` API.
- **Live**: rooftop panel 3 (Yuki + Akira two-shot) 1024² → 2048² in ~18 s. Clean
  edges, identity and composition preserved, no chroma blotching.

## Known limits / next

- Polish at 0.25 can slightly alter fine facial features on an already-sharp source;
  lower the denoise or use `0` for a faithful pure upscale.
- The upscaler is fixed at x4; the lanczos resize takes it to the requested scale
  (e.g. 2×). A true 2× model would sharpen further, but lanczos-after-x4 is fine.
