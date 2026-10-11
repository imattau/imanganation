#!/usr/bin/env python3
"""Prototype: flatten a character reference's background to white using the figure matte.

Example:
  uv run --extra eval python scripts/prototype_reference_cleanup.py \
    projects/_spike/characters/hero_design.png --out outputs/reference-cleanup

Writes <name>-clean.png and <name>-compare.png (original | cleaned). Local only.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from manganation.evaluate.detector import default_detector
from manganation.evaluate.masks import AnimeSegmenter, clean_reference


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, default=Path("outputs/reference-cleanup"))
    parser.add_argument("--model", type=Path,
                        default=Path("models/taggers/anime-seg-isnetis.onnx"))
    args = parser.parse_args()
    if not args.model.is_file():
        parser.error(f"anime segmentation model not found: {args.model}")

    detector, segmenter = default_detector(), AnimeSegmenter(args.model)
    args.out.mkdir(parents=True, exist_ok=True)
    for path in args.images:
        with Image.open(path) as source:
            original = source.convert("RGB")
        name = f"{path.parent.name}-{path.stem}" if path.stem == "base" else path.stem
        cleaned, found = clean_reference(original, detector, segmenter)
        cleaned.save(args.out / f"{name}-clean.png")
        compare = Image.new("RGB", (original.width * 2, original.height), "white")
        compare.paste(original, (0, 0))
        compare.paste(cleaned, (original.width, 0))
        compare.save(args.out / f"{name}-compare.png")
        print(f"{path}: {found} figure(s){'' if found else ' - left unchanged'}")


if __name__ == "__main__":
    main()
