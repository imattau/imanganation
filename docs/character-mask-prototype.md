# Character mask prototype

This experiment checks whether a detected-figure crop plus anime-specific foreground
segmentation can produce useful editable masks. Its CLI writes full-canvas grayscale
PNGs and a tinted review overlay; the GIMP command imports masks as editable layers.

## Setup and run

Install the optional CPU dependencies and both mask models:

```bash
uv sync --extra eval
uv run manganation setup --selection
```

The segmenter is SkyTNT's Apache-2.0 `isnetis.onnx` checkpoint from
[SkyTNT/anime-seg](https://huggingface.co/skytnt/anime-seg). The existing anime-person
detector is also used. The segmenter SHA-256 is
`f15622d853e8260172812b657053460e20806f04b9e05147d49af7bed31a6e99`.

Run the prototype on an exported layer or a rendered panel:

```bash
uv run --extra eval python scripts/prototype_character_masks.py \
  projects/rooftop/panels/003.png --out outputs/character-mask-prototype/003
```

Each detected figure gets a `figure-NN-mask.png` in the same canvas coordinates as the
input, plus `overlay-preview.png`. The figure masks are independent outputs and can
overlap where detector boxes overlap.

## Use it in GIMP

Start the engine with the `eval` extra installed (`onnxruntime` and NumPy are already
used by the evaluation tools), then choose **Imanganation → Settings → Set Up Models**
and install the optional character mask models. Select a layer and choose
**Imanganation → Edit Panel → Create Character Masks**. The command sends the selected
layer's visible composite to the local engine, then imports one white silhouette with
soft alpha per detected figure into a hidden group. Show the group and edit each mask
layer with GIMP's alpha-to-selection or other normal mask tools.

The detector proposes one crop per figure; touching or heavily overlapping figures may
need manual cleanup. The masks do not identify characters by name. This command is a
starting point for manual mask work rather than a guaranteed cutout.
