"""Anime person detector (deepghs person_detect_v1.3_s, YOLOv8 ONNX): image -> figures.

The tagger judges a whole picture, so it can't say *who* wears the skirt, and it
counted a three-figure render as ``1boy, 1girl``. The eval finds each figure with
this detector and tags the crops one by one. Runs on the CPU like the tagger.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

MAX_SIDE = 640  # the size the model was trained at; inputs are multiples of its stride
STRIDE = 32


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float
    score: float

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2


def iou(a: Box, b: Box) -> float:
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    h = min(a.y1, b.y1) - max(a.y0, b.y0)
    inter = max(0.0, w) * max(0.0, h)
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def suppress(boxes: list[Box], overlap: float) -> list[Box]:
    """Non-maximum suppression: the best box of each cluster, best first. Two people
    side by side overlap a lot (a drag, a hug), so only near-duplicates (YOLO's 0.7) go."""
    kept: list[Box] = []
    for box in sorted(boxes, key=lambda b: -b.score):
        if all(iou(box, k) < overlap for k in kept):
            kept.append(box)
    return kept


class Detector:
    def __init__(self, model: Path, threshold: float = 0.324, overlap: float = 0.7):
        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise RuntimeError("the detector needs onnxruntime: uv sync --extra eval") from exc
        self.session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name
        self.threshold = threshold
        self.overlap = overlap

    def figures(self, image: Image.Image | Path | str) -> list[Box]:
        """People in the image, in image pixels, left to right."""
        import numpy as np

        if not isinstance(image, Image.Image):
            with Image.open(image) as im:
                image = im.convert("RGB")
        image = image.convert("RGB")
        scale = MAX_SIDE / max(image.size)
        w = max(STRIDE, round(image.width * scale / STRIDE) * STRIDE)
        h = max(STRIDE, round(image.height * scale / STRIDE) * STRIDE)
        pixels = np.asarray(image.resize((w, h), Image.BILINEAR), dtype=np.float32)
        (out,) = self.session.run(None, {self.input: pixels.transpose(2, 0, 1)[None] / 255})
        rows = out[0] if out.shape[1] == 5 else out[0].T  # (5, anchors): cx, cy, w, h, p
        sx, sy = image.width / w, image.height / h
        boxes = [Box((cx - bw / 2) * sx, (cy - bh / 2) * sy, (cx + bw / 2) * sx,
                     (cy + bh / 2) * sy, float(p))
                 for cx, cy, bw, bh, p in rows.T if p >= self.threshold]
        return sorted(suppress(boxes, self.overlap), key=lambda b: b.center_x)


def default_detector(name: str | None = None) -> Detector:
    """A detector ``models.yaml`` registers (default: people; ``anime_face``: faces),
    from the model store."""
    from manganation import models_setup as ms
    from manganation.config import load_models, models_root

    root = models_root()
    models = load_models()
    name = name or ms.DEFAULT_DETECTOR
    model = ms.detector(models, name)
    if ms.state(model, root) != "present":
        raise RuntimeError(f"detector missing ({model.file or model.note}): run "
                           "`manganation setup --eval`")
    entry = models.get("detectors", {}).get(name, {})
    return Detector(model.path(root), threshold=float(entry.get("threshold", 0.324)))
