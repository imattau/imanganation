# Panel Inpaint (GIMP "Inpaint Selection")

**Status: engine path COMPLETE.** Repaint just the masked region of an existing panel
from a short prompt; everything outside the mask is preserved exactly (the repaint is
composited back over the original through a soft copy of the grown mask). This is the
engine half the GIMP plug-in's *Inpaint Selection* calls.

## Pipeline (crop-and-stitch)

```
mask ──▶ bbox + context ──▶ crop (take + mask) ──▶ resize to ~1 MP (64-px grid)
     ──▶ VAEEncode ──▶ SetLatentNoiseMask(GrowMask) ──▶ KSampler(denoise) ──▶ VAEDecode
     ──▶ resize back ──▶ paste into the full-res take through a grown, blurred mask
```

- **Why crop:** SDXL paints well at ~1 MP. Painting a small selection inside a whole
  take (a hi-res can be 4-16 MP) gave incoherent patches: "red apple" became a smear.
  The crop is the mask's bounding box plus `context` (0.5 × the mask's larger side)
  per side, at least `min_crop` px and no thinner than 2:1, then resized to ~1 MP.
  Small selections are upscaled (more detail), huge takes downscaled.
- `SetLatentNoiseMask` limits noise to the masked latent; `GrowMask` (scaled to the
  crop's working size) dilates it so the new pixels blend in. `denoise` must be
  **high** (default 0.85): the region is regenerated from noise, not nudged.
- **Stitch:** the patch is resized back and pasted through the full-resolution mask,
  grown by `grow_mask_by` and Gaussian-softened. Outside that, pixels are the
  original's exactly, guaranteed in Python rather than relying on the graph.

## Character identity

Without it, repainting a face draws *a* character: "surprised face" on Yuki gave a
different girl (`docs/quality/2026-10-04_inpaint_identity.png`). Requests name who is
in the patch (`characters`):

- Their **appearance traits** join the prompt, *after* the artist's prompt, which says
  what to paint.
- With **exactly one** character, IP-Adapter (the project's adapter) guides the patch
  with their reference (`version` honoured) at `ipadapter_weight` (0.45), or the
  request's `character_weight`. Several: one head count (`1boy, 1girl`) leads their
  grouped traits, and each reference is masked to that character's band of the crop in
  the request's `reading_order`, like a panel render (an unmasked reference would pull
  every face to one identity). Bands cover every named character, so one without a
  reference keeps their part of the patch.
- **Only appearance traits are used** (`AppearanceSpec.appearance_tags()`): never
  the character's `default_expression` or `mannerisms`. Stored as identity, Yuki's
  "wide toothed grin" overrode "surprised face, open mouth" even at IP-Adapter weight
  0.3. Physical distinguishing marks (a scar, a pin) stay.

Live (Yuki, whole-face mask, same seed): no characters gave an open mouth on another
face; Yuki without the filter gave her stored grin; Yuki with the filter at 0.45 gave
her face, surprised.

The character schema now keeps expression and body language apart from appearance
(`docs/phase2.md`, "Appearance vs expression"), which also fixed renders, where the
stored grin fought a panel's `expressions`.

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
    context: 0.5      # crop-and-stitch: context per side, as a fraction of the mask size
    min_crop: 256     # smallest crop side in source px
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
- **Live, crop-and-stitch** (`docs/quality/2026-10-04_inpaint_crop_stitch.png`): the
  same selection on a 3840×4352 take. Crop 3296×3128 painted at 1088×1024: a clean,
  coherent red apple where the whole-take inpaint gave a smear; 0 pixels changed
  outside the mask (+40 px).

## Known limits

- When the crop is much larger than 1 MP (big selections on hi-res takes), it's
  downscaled to paint, so the patch is softer than its surroundings. Better: inpaint
  the base take, then Refine; or paint large crops at native size in tiles (future).

## Notes / next

- Works with the standard (non-inpaint) NoobAI checkpoint via the masked-latent path
  and crop-and-stitch;
  no separate inpaint model needed on 16 GB.
- `source` defaults to the *newest* take, which may be a `_hires`/`_inpaint`
  iteration. Pass `--source` explicitly when the plug-in knows the exact layer.
- Face-aware inpainting (e.g. FaceID on the patch) would improve identity when a face
  falls inside the mask — future work.
