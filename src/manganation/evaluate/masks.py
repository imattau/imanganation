"""Prototype per-figure masks using anime-specific segmentation and detection.

The anime-person detector proposes one crop per figure. SkyTNT's anime ISNet model
segments each crop, returning a soft mask. This is a feasibility prototype; boxes can
omit body parts and touching/overlapping figures may need manual cleanup.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from manganation.evaluate.detector import Box, Detector


@dataclass(frozen=True)
class FigureMask:
    box: Box
    mask: Image.Image


class AnimeSegmenter:
    """Small ONNX wrapper for SkyTNT's isnetis anime foreground model."""

    def __init__(self, model: Path):
        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError("mask generation needs onnxruntime") from exc
        self.session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0]
        dims = self.input.shape
        self.size = int(dims[-1]) if isinstance(dims[-1], int) else 640

    def mask(self, image: Image.Image) -> Image.Image:
        """Return a soft, full-resolution foreground matte for one character crop."""
        image = image.convert("RGB")
        width, height = image.size
        if not width or not height:
            raise ValueError("cannot segment an empty image")
        scale = self.size / max(width, height)
        resized_width = max(1, round(width * scale))
        resized_height = max(1, round(height * scale))
        resized = image.resize((resized_width, resized_height), Image.Resampling.BILINEAR)
        canvas = np.zeros((self.size, self.size, 3), dtype=np.float32)
        left = (self.size - resized_width) // 2
        top = (self.size - resized_height) // 2
        canvas[top:top + resized_height, left:left + resized_width] = (
            np.asarray(resized, dtype=np.float32) / 255.0)
        tensor = canvas.transpose(2, 0, 1)[None]
        prediction = self.session.run(None, {self.input.name: tensor})[0]
        matte = np.asarray(prediction, dtype=np.float32).squeeze()
        if matte.ndim != 2:
            raise RuntimeError(f"unexpected segmenter output shape: {prediction.shape}")
        matte = matte[top:top + resized_height, left:left + resized_width]
        matte_image = Image.fromarray(np.clip(matte * 255, 0, 255).astype(np.uint8), "L")
        return matte_image.resize((width, height), Image.Resampling.BILINEAR)


def character_masks(image: Image.Image, detector: Detector, segmenter: AnimeSegmenter,
                    *, padding: float = 0.08) -> list[FigureMask]:
    """Detect figures, segment each padded box, and place masks on the full canvas."""
    if not 0 <= padding <= 0.5:
        raise ValueError("padding must be between 0 and 0.5")
    image = image.convert("RGB")
    width, height = image.size
    results: list[FigureMask] = []
    for box in detector.figures(image):
        dx = (box.x1 - box.x0) * padding
        dy = (box.y1 - box.y0) * padding
        x0 = max(0, int(box.x0 - dx))
        y0 = max(0, int(box.y0 - dy))
        x1 = min(width, int(np.ceil(box.x1 + dx)))
        y1 = min(height, int(np.ceil(box.y1 + dy)))
        if x1 <= x0 or y1 <= y0:
            continue
        matte = segmenter.mask(image.crop((x0, y0, x1, y1)))
        full = Image.new("L", image.size, 0)
        full.paste(matte, (x0, y0))
        results.append(FigureMask(box, full))
    return results
