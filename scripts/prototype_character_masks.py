#!/usr/bin/env python3
"""Prototype per-figure mask generation for an image layer export.

Example:
  uv run --extra eval python scripts/prototype_character_masks.py \
    projects/rooftop/panels/003.png --out outputs/character-mask-prototype

Each detected figure gets a full-size white-on-black PNG and the command also writes
an overlay preview. Images remain local; no renderer or ComfyUI process is used.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from manganation.evaluate.detector import default_detector
from manganation.evaluate.masks import AnimeSegmenter, character_masks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="PNG/JPEG export of the selected layer")
    parser.add_argument("--out", type=Path, default=Path("outputs/character-mask-prototype"))
    parser.add_argument("--model", type=Path,
                        default=Path("models/taggers/anime-seg-isnetis.onnx"))
    args = parser.parse_args()

    if not args.model.is_file():
        parser.error(f"anime segmentation model not found: {args.model}")

    with Image.open(args.image) as source:
        image = source.convert("RGB")
    figures = character_masks(image, default_detector(), AnimeSegmenter(args.model))
    if not figures:
        parser.error("no figures detected")

    args.out.mkdir(parents=True, exist_ok=True)
    overlay = image.convert("RGBA")
    for index, figure in enumerate(figures, start=1):
        mask_path = args.out / f"figure-{index:02d}-mask.png"
        figure.mask.save(mask_path)
        tint = Image.new("RGBA", image.size, (255, 40, 80, 0))
        tint.putalpha(figure.mask.point(lambda value: round(value * 0.38)))
        overlay = Image.alpha_composite(overlay, tint)
        print(f"figure {index}: score={figure.box.score:.3f}, "
              f"box=({figure.box.x0:.0f},{figure.box.y0:.0f},"
              f"{figure.box.x1:.0f},{figure.box.y1:.0f}), mask={mask_path}")
    preview_path = args.out / "overlay-preview.png"
    overlay.save(preview_path)
    print(f"preview: {preview_path}")


if __name__ == "__main__":
    main()
