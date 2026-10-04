# Panel Inpaint (GIMP "Inpaint Selection")

**Status: engine path COMPLETE.** Repaint just the masked region of an existing panel
from a short prompt; everything outside the mask is preserved exactly. This is the
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

The GIMP plug-in exports the selection as a mask image, same pixel size as the panel:

- **Transparent PNG** (selection opaque, rest transparent) → `channel: alpha`.
- **Opaque black/white PNG** (white = repaint) → `channel: red`.

The channel is auto-detected from the file (`mask_channel_for`): any alpha channel →
`alpha`, otherwise `red`.

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
- **Live**: panel 3 (Akira + Yuki two-shot) with a mask over the lower-left region.
  At denoise 0.3/0.6/0.85 the characters stay pixel-identical and only the masked
  region changes — confirming the mask constrains generation as intended.

## Notes / next

- Works with the standard (non-inpaint) NoobAI checkpoint via the masked-latent path;
  no separate inpaint model needed on 16 GB.
- `source` defaults to the *newest* take, which may be a `_hires`/`_inpaint`
  iteration. Pass `--source` explicitly when the plug-in knows the exact layer.
- Face-aware inpainting (e.g. FaceID on the patch) would improve identity when a face
  falls inside the mask — future work.
