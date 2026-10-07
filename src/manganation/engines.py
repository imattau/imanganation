"""The render engines a project can choose, and what each needs installed.

Measured on the rooftop suite (docs/quality/2026-10-07_combined.md): the SDXL pipeline
is the fast default; Qwen-Image 2.1 composes the most consistent pages (characters,
places, two people in one scene) but is non-commercial; Z-Anime composes from text
alone. The face pass repaints each face with the panel's expression after any of them.

``GET /engines`` reports these, with install state, so the GIMP plug-in can offer the
choice, show each licence, and install what's missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from manganation import models_setup as ms


@dataclass(frozen=True)
class Engine:
    id: str
    name: str
    summary: str
    licence: str
    licence_url: str
    commercial: bool  # may its output be used commercially?
    speed: str
    trial: str | None = None  # models.yaml trials group with its files


ENGINES = [
    Engine("sdxl", "NoobAI-XL (default)",
           "Fast. Tags and per-character references; two-shots can split in two and "
           "backgrounds stay plain.",
           "FAIR AI Public License 1.0-SD", "https://freedevproject.org/faipl-1.0-sd/",
           True, "~12 s a panel"),
    Engine("qwen_image_21", "Qwen-Image 2.1",
           "The most consistent pages: characters from their reference images, the "
           "place from its location image, two people in one scene. Faces come out "
           "neutral, so use the face pass with it.",
           "Qwen Research License (non-commercial)",
           "https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE",
           False, "~45 s a panel", trial="qwen_image_21"),
    Engine("z_anime", "Z-Anime",
           "Composes from the script's text alone: unified scenes and good light, but "
           "no reference images, so characters drift between panels.",
           "Apache-2.0", "https://huggingface.co/SeeSee21/Z-Anime",
           True, "~85 s a panel", trial="z_anime"),
]
FACE_PASS = Engine(
    "face_pass", "Face pass",
    "After the render, repaints each character's face with the panel's expression "
    "(surprise, a sigh), keeping their look.",
    "CreativeML OpenRAIL++-M (Animagine XL 4.0); detectors MIT",
    "https://huggingface.co/cagliostrolab/animagine-xl-4.0", True, "~20 s a face",
    trial="animagine_4_opt")
BY_ID = {e.id: e for e in [*ENGINES, FACE_PASS]}


def files(engine: Engine, models: dict, settings) -> list[ms.ModelFile]:
    """What ``engine`` needs on disk."""
    if engine.id == "sdxl":
        return ms.needed(settings, models)
    out = ms.trial(models, engine.trial) if engine.trial else []
    if engine.id == "face_pass":
        out += [ms.detector(models, ms.DEFAULT_DETECTOR), ms.detector(models, ms.FACE_DETECTOR),
                *ms.evaluation(models)[:2]]  # the tagger matches faces to characters
    return out


def _runtime_ok(engine: Engine) -> str | None:
    """A missing Python package, if any (the face pass runs its detectors on the CPU)."""
    if engine.id != "face_pass":
        return None
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return "onnxruntime is not installed (uv sync --extra eval)"
    return None


def report(models: dict, settings, root: Path) -> list[dict]:
    """Every engine (and the face pass), with what's missing."""
    rows = []
    for engine in [*ENGINES, FACE_PASS]:
        need = files(engine, models, settings)
        missing = [m for m in need if ms.state(m, root) != "present"]
        problem = _runtime_ok(engine)
        rows.append({
            "id": engine.id, "name": engine.name, "summary": engine.summary,
            "licence": engine.licence, "licence_url": engine.licence_url,
            "commercial": engine.commercial, "speed": engine.speed,
            "installed": not missing and problem is None,
            "missing": [m.file or m.note for m in missing],
            "missing_bytes": sum(m.size or 0 for m in missing),
            "problem": problem,
        })
    return rows
