"""WD14 anime tagger (SmilingWolf's wd-swinv2-tagger-v3, ONNX): image -> Danbooru tags.

The eval uses it as the judge of what a render shows: head count, hair and eye colours,
pose, setting. It's the same vocabulary NoobAI was trained on, so "the prompt said
``hands on hips``, does the picture have ``hands on hips``" is a like-for-like check.

Runs on the CPU (``onnxruntime``, the ``eval`` extra) so it never competes with
ComfyUI for VRAM. Files: ``config/models.yaml`` -> ``taggers``.
"""

from __future__ import annotations

import csv
from pathlib import Path

from PIL import Image

# Tags that are emoticons: their underscores are part of the tag, not spaces.
KAOMOJI = {"0_0", "(o)_(o)", "+_+", "+_-", "._.", "<o>_<o>", "<|>_<|>", "=_=", ">_<",
           "3_3", "6_9", ">_o", "@_@", "^_^", "o_o", "u_u", "x_x", "|_|", "||_||"}
RATING = 9  # selected_tags.csv category for general/sensitive/questionable/explicit


def tag_name(raw: str) -> str:
    """``hands_on_hips`` -> ``hands on hips`` (the spelling prompts and suites use)."""
    return raw if raw in KAOMOJI else raw.replace("_", " ")


class Tagger:
    def __init__(self, model: Path, tags_csv: Path):
        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise RuntimeError("the tagger needs onnxruntime: uv sync --extra eval") from exc
        self.session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0]
        self.size = int(self.input.shape[1])  # NHWC, square
        with open(tags_csv, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.names = [tag_name(r["name"]) for r in rows]
        self.keep = [i for i, r in enumerate(rows) if int(r["category"]) != RATING]
        self.vocabulary = {self.names[i] for i in self.keep}

    def _prepare(self, image: Image.Image):
        import numpy as np

        image = image.convert("RGBA")
        flat = Image.new("RGBA", image.size, (255, 255, 255, 255))
        flat.alpha_composite(image)
        side = max(flat.size)
        square = Image.new("RGB", (side, side), (255, 255, 255))
        square.paste(flat.convert("RGB"), ((side - flat.width) // 2, (side - flat.height) // 2))
        square = square.resize((self.size, self.size), Image.BICUBIC)
        pixels = np.asarray(square, dtype=np.float32)[:, :, ::-1]  # the model wants BGR
        return np.ascontiguousarray(pixels[None])

    def tags(self, image: Image.Image | Path | str) -> dict[str, float]:
        """Every non-rating tag -> probability (0..1)."""
        if not isinstance(image, Image.Image):
            with Image.open(image) as im:
                image = im.copy()
        (probs,) = self.session.run(None, {self.input.name: self._prepare(image)})
        return {self.names[i]: float(probs[0][i]) for i in self.keep}


def default_tagger() -> Tagger:
    """The tagger ``models.yaml`` registers, from the model store."""
    from manganation import models_setup as ms
    from manganation.config import load_models, models_root

    root = models_root()
    model, tags, *_ = ms.evaluation(load_models())
    missing = [m.file or m.note for m in (model, tags) if ms.state(m, root) != "present"]
    if missing:
        raise RuntimeError(f"tagger files missing ({', '.join(missing)}): run "
                           "`manganation setup --eval`")
    return Tagger(model.path(root), tags.path(root))
