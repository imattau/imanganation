"""Face pass: repaint each character's face with the panel's expression.

A composing model can get the layout, the people and their identity right while their
faces stay blank: Qwen-Image 2.1 copies its reference image's neutral face (surprise
and the sigh 0% in the model trial, docs/quality/2026-10-06_models.md). This pass keeps
the panel and repaints only the faces, one at a time, with an SDXL model that does
expressions well:

1. an anime face detector finds the faces;
2. each face is matched to a character by tagging it (hair and eyes) and comparing
   with the tags of the characters' reference images (a lone character needs no
   matching);
3. each face is inpainted (crop-and-stitch, ``render.inpaint``) inside a soft oval,
   from that character's expression for the panel, with their reference guiding
   IP-Adapter so the face stays on-model.

Needs the eval extra (onnxruntime) and its detectors: ``manganation setup --eval``.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from itertools import permutations
from pathlib import Path

from PIL import Image, ImageDraw

from manganation.script.schema import PanelSpec

log = logging.getLogger(__name__)

# Tag words that tell characters apart from the head up (not the expression).
IDENTITY_WORDS = ("hair", "eyes", "twintails", "ponytail", "braid", "bangs", "ahoge",
                  "ribbon", "hairclip", "hair ornament", "bun", "bob cut", "sidelocks")


@dataclass
class FacePassResult:
    faces: list[dict] = field(default_factory=list)  # box, character, expression
    warnings: list[str] = field(default_factory=list)


def identity_tags(probs: dict[str, float], threshold: float = 0.3) -> dict[str, float]:
    return {t: p for t, p in probs.items()
            if p >= threshold and any(w in t for w in IDENTITY_WORDS)}


def similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Weighted overlap of two identity-tag sets (0..1)."""
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    return sum(min(a.get(k, 0), b.get(k, 0)) for k in keys) / sum(
        max(a.get(k, 0), b.get(k, 0)) for k in keys)


def match_faces(face_tags: list[dict[str, float]], refs: dict[str, dict[str, float]],
                ) -> dict[int, str]:
    """Face index -> character, maximising total similarity (each used once)."""
    names = list(refs)
    best: tuple[float, dict[int, str]] = (-1.0, {})
    slots = list(range(len(face_tags))) + [None] * len(names)
    for chosen in dict.fromkeys(permutations(slots, len(names))):
        score = sum(similarity(face_tags[f], refs[n])
                    for n, f in zip(names, chosen, strict=True) if f is not None)
        if score > best[0]:
            best = (score, {f: n for n, f in zip(names, chosen, strict=True)
                            if f is not None})
    return best[1]


def face_mask(size: tuple[int, int], box, grow: float = 0.15) -> Image.Image:
    """White soft-edged oval over the face (``box`` grown by ``grow`` per side)."""
    x0, y0, x1, y1 = box
    gw, gh = (x1 - x0) * grow, (y1 - y0) * grow
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse((x0 - gw, y0 - gh, x1 + gw, y1 + gh), fill=255)
    return mask


def expressions_for(spec: PanelSpec, settings) -> dict[str, str]:
    """Each visible character's face for this panel: the script's own, else the
    staging LLM's (cached per panel text)."""
    faces = dict(spec.expressions)
    missing = [n for n in spec.characters if n not in faces]
    if missing:
        try:
            from manganation.render.panel import setting
            from manganation.render.staging import stage

            staged = stage(spec, setting(spec), settings=settings)
            for name in missing:
                if staged.expression(name):
                    faces[name] = ", ".join(staged.expression(name))
        except Exception as exc:  # noqa: BLE001 - no LLM: faces left as they are
            log.warning("face pass: no expressions from the LLM (%s)", exc)
    return faces


def face_pass(image: Path, spec: PanelSpec, identity: Path, out: Path, *,
              client=None, seed: int = 0, denoise: float = 0.55,
              checkpoint: str | None = None, tagger=None, face_detector=None,
              settings=None) -> FacePassResult:
    """Repaint every matched face of ``image`` (a render of ``spec``) into ``out``."""
    from manganation.config import load_settings
    from manganation.evaluate.detector import default_detector
    from manganation.evaluate.tagger import default_tagger
    from manganation.render.inpaint import inpaint_image
    from manganation.render.panel import find_references

    settings = settings or load_settings()
    tagger = tagger or default_tagger()
    face_detector = face_detector or default_detector("anime_face")
    result = FacePassResult()
    with Image.open(image) as im:
        source = im.convert("RGB")
    boxes = face_detector.figures(source)
    refs = find_references(identity, spec)
    names = [n for n in spec.characters if n in refs]
    if not boxes or not names:
        result.warnings.append("face pass: no faces found" if not boxes
                               else "face pass: no character references")
        source.save(out)
        return result

    if len(names) == 1 and len(boxes) == 1:
        owners = {0: names[0]}
    else:
        def head(b):  # the face with its hair, which is what tells characters apart
            w, h = b.x1 - b.x0, b.y1 - b.y0
            return source.crop((max(0, int(b.x0 - w * 0.5)), max(0, int(b.y0 - h * 0.6)),
                                min(source.width, int(b.x1 + w * 0.5)),
                                min(source.height, int(b.y1 + h * 0.4))))
        face_tags = [identity_tags(tagger.tags(head(b))) for b in boxes]
        ref_tags = {n: identity_tags(tagger.tags(refs[n])) for n in names}
        owners = match_faces(face_tags, ref_tags)

    faces = expressions_for(spec, settings)
    current = image
    with tempfile.TemporaryDirectory() as tmp:
        for i, box in enumerate(boxes):
            name = owners.get(i)
            if name is None:
                continue
            face = faces.get(name, "")
            mask_path = Path(tmp) / f"face{i}.png"
            face_mask(source.size, (box.x0, box.y0, box.x1, box.y1)).save(mask_path)
            step = Path(tmp) / f"step{i}.png"
            inpaint_image(Path(current), mask_path, out=step, seed=seed + i,
                          prompt=", ".join(p for p in (face, "face") if p),
                          denoise=denoise, client=client, tag="facepass",
                          characters=[{"name": name}], identity=identity,
                          checkpoint=checkpoint)
            current = step
            result.faces.append({"box": [round(box.x0), round(box.y0), round(box.x1),
                                         round(box.y1)], "character": name,
                                 "expression": face})
        with Image.open(current) as im:
            im.convert("RGB").save(out)
    return result
