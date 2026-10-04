# Phase 4 — Multi-Character Regional References

**Status: COMPLETE (first cut).** Panels with more than one character now attach a
reference image *per character*, each bound to its own region of the canvas, so
identities do not bleed into one another.

## The problem

A single IP-Adapter reference applies to the whole image. With two characters a
prompt like *"1girl … 1boy … two-shot"* makes SDXL stack or merge the figures, and a
single reference colours both. The fix is to give each character their own reference
**and** their own spatial region.

## What was built

| Piece | Where |
|---|---|
| Region maths (normalised boxes, reading order, feather) | `layout/regions.py` |
| Regional IP-Adapter graph (`IPAdapterRegionalConditioning` → `IPAdapterCombineParams` → `IPAdapterFromParams`) | `render/graphs.py::with_regional_ipadapter` |
| Region-aware **prompt** conditioning (`ConditioningSetAreaPercentage` + `ConditioningCombine`) | `render/graphs.py::with_regional_conditioning` |
| Wiring per panel + reference resolution | `render/panel.py::render_panel`, `find_references` |
| Tuning knobs | `config/settings.yaml` → `defaults.ipadapter` |

## How it works

For a panel with N>1 referenced characters:

1. **Regions:** the canvas is split into N vertical bands in reading order (RTL puts
   the first character on the right). `margin` insets each band; `feather` (px) softens
   the edges so neighbours blend instead of meeting at a hard seam.
2. **Prompt conditioning:** each character's own appearance tags are encoded and
   constrained to their band (`ConditioningSetAreaPercentage`), then combined with the
   base scene prompt. This is what stops the figures stacking.
3. **Reference conditioning:** each character's reference image is `LoadImage`d, given a
   region mask (`SolidMask` → `MaskComposite` → `FeatherMask`), and wrapped by an
   `IPAdapterRegionalConditioning`. The per-character params are concatenated and
   applied by `IPAdapterFromParams`.

Single-character panels keep the simpler `IPAdapterAdvanced` path.

## Tuning (config)

```yaml
defaults:
  ipadapter:
    weight_single: 0.6      # single-character panels
    weight_regional: 0.5    # per-region strength
    feather: 48             # regional mask feather (px)
```

Lower weight = less colour bleed and more prompt control; higher = stronger identity.
The defaults were tuned down from 0.75 to 0.5–0.6 to remove the over-saturation seen
in early tests.

## Verified

- **`tests/test_multichar.py`**: region assignment (order, margin, clipping, rounding),
  graph shape (single vs regional path, mask/feather wiring, no input mutation),
  region-conditioning wiring.
- **Live:** panel 3 of the rooftop script (`{Yuki, Akira}`, "two-shot") renders both
  characters *in their bands*, distinct and correctly placed. Feathering removed the
  seam; reduced weight removed most of the streaking.

## Known limits / next

- **Vertical bands only** — good for side-by-side two-shots; faces-at-angles and 3+
  characters may want a smarter stager (Phase 6: SAM2 or artist-supplied regions).
- **Region prompt text** uses each character's design-sheet tags; per-panel action text
  is not yet split per character.
- **3+ characters** work but each band narrows; consider img2img refinement.

## Two-shot identity bleed: root cause and fix (2026-10-04)

**Symptom:** in two-shots, Yuki came out with Akira's brown hair and face (3 of 4
seeds; `docs/quality/2026-10-04_twoshot_bleed_before.png`).

**Root cause: an upload name collision, not tuning.** Registry references are named by
version, so both characters' references are `base.png`. The engine uploaded images to
ComfyUI's flat input store by file name with overwrite on: the second upload (Akira)
replaced the first, and *both* regional IP-Adapter regions loaded Akira's face. ComfyUI's
`input/base.png` was byte-identical to Akira's reference. Found by switching mechanisms
off: without IP-Adapter Yuki was correct every time; with regional IP-Adapter alone both
characters were brunettes.

**Fixes**

1. `ComfyClient.upload_image` stores `<stem>-<content hash><ext>`: different images can
   never overwrite each other (identical ones share a file). This covers every upload
   path (renders, inpaint references, refine sources).
2. Region masks are softened with `MaskToImage → ImageBlur → ImageToMask`. ComfyUI's
   `FeatherMask` fades a mask towards the *canvas* borders, which left the boundary
   between the two characters a hard seam. (`ImageBlur` caps `sigma` at 10.)
3. **Per-character text bands are off by default** (`defaults.ipadapter.regional_text:
   0.0`). Same seeds (`twoshot_bleed_experiments.py`):
   - **A**, bands on (strength 1): identities right after fix 1, but side-by-side
     "split-screen" compositions, one with a black divider line.
   - **F**, bands off: one coherent scene with the characters interacting; Yuki right in
     4/4, Akira in 4/4 (`2026-10-04_twoshot_default_F.png`). Minor: Akira's blue tips
     fade, and Yuki's snowflake pin sometimes lands on his blazer.
   - **G**, bands at 0.4: blue tips return, but compositions drift back to side by
     side, and one seed put a skirt on Akira (`2026-10-04_twoshot_text_bands_04.png`).

   F is the default; raise `regional_text` to trade composition for fine traits.

**Known residue:** fine accessories can swap between characters; where the two soft
region masks overlap, a limb can look semi-transparent.

