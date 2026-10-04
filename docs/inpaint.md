# Panel Inpaint (GIMP "Inpaint Selection")

**Status: engine path COMPLETE.** Repaint just the masked region of an existing panel
from a short prompt; everything outside the mask is preserved exactly (the repaint is
composited back over the original through a soft copy of the grown mask). This is the
engine half the GIMP plug-in's *Inpaint Selection* calls.

## Pipeline

```
init image ──▶ VAEEncode ──▶ SetLatentNoiseMask(mask) ──▶ KSampler(denoise) ──▶ VAEDecode
mask image ──▶ LoadImageMask ──▶ GrowMask ─────────────────────────┘
```

- `SetLatentNoiseMask` marks which latent positions may receive noise, so the sampler
  only re-synthesises inside the mask; the rest of the latent is the encoded original.
- `GrowMask` dilates the mask (default 8 px) so the new pixels blend into the surround.
- `denoise` must be **high** (default 0.85): the masked region is regenerated from
  noise, not nudged. Lower values only tint the region.

## Mask convention

Either form works, **white/opaque = repaint**:

- **Transparent PNG**: selection opaque, rest transparent (what the GIMP plug-in exports).
- **Opaque black/white PNG**: white = repaint.

The engine normalises both to an opaque greyscale mask (`normalized_mask`) and loads it
by its red channel. Don't hand ComfyUI the alpha channel directly: `LoadImageMask` reads
alpha as `1 - alpha` (transparent = masked), which repaints everything *except* the
selection. That bug is why the normalisation exists (found live, 2026-10-04).

Output is a new take: `panels/{seq:03d}_inpaint.png` (then `_inpaint_takeNN.png`) with
its own sidecar, so the original render and the artist's iterations all survive.

## Config

```yaml
defaults:
  inpaint:
    denoise: 0.85     # high: the masked region is re-synthesised from noise
    grow_mask_by: 8   # dilate the mask so the patch blends into its surroundings
    steps: 28
    cfg: 6.0
```

## Interfaces

| Surface | Entry point |
|---|---|
| Python | `render.inpaint.inpaint_panel(project, seq, mask=, prompt=, source=, denoise=, grow_mask_by=, seed=)` |
| CLI | `manganation inpaint <project> <seq> <mask> --prompt "..." [--source] [--denoise] [--grow] [--seed]` |
| API | `POST /inpaint` → job (`kind: "inpaint"`), poll `GET /jobs/{id}` |

`source` defaults to the newest panel take; both `source` and `mask` must live inside
the project (the API and wrapper enforce containment).

## Verified

- **`tests/test_inpaint.py`**: graph shape (mask wired into the latent, grow-mask
  dilation, channel override), alpha/red detection, take naming, size-mismatch and
  out-of-project rejection, missing-render handling, `/inpaint` API + containment.
- **Live**: an opaque mask on a 960×1024 panel. Outside the mask (+20 px) 0 pixels
  change after the composite fix; before it, 7% moved by >8 levels (max 179).
- **Live, via GIMP** (`gimp/inpaint_smoke_test.py`): a feathered selection over a
  placed 1920×2176 hi-res take. This caught the inverted-alpha bug above.

## Notes / next

- Works with the standard (non-inpaint) NoobAI checkpoint via the masked-latent path;
  no separate inpaint model needed on 16 GB.
- `source` defaults to the *newest* take, which may be a `_hires`/`_inpaint`
  iteration. Pass `--source` explicitly when the plug-in knows the exact layer.
- Face-aware inpainting (e.g. FaceID on the patch) would improve identity when a face
  falls inside the mask — future work.
