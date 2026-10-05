"""Panel staging: a script's prose -> the Danbooru tags NoobAI was trained on.

"Akira sits alone against the chain-link fence, lunchbox on his knees" is English that
SDXL's CLIP reads loosely, and the names in it mean nothing to it. As tags
(``sitting, against fence, knees up, bento, on lap``) the same panel renders: in the
rooftop eval, hand-written tags took "sitting at the fence" from 1/3 seeds to 3/3
(docs/quality/2026-10-05_eval_baseline.md). This module has the local LLM write
those tags at render time.

- **Per character:** pose and expression for each *visible* character. They go into
  that character's prompt group, and an expression stops their default one (Yuki
  grins by default; "sighs" means a sigh).
- **Shared:** interaction and props (``holding another's wrist``, ``bento``).
- **Setting:** place, indoors/outdoors, time of day.

Results are cached by everything that goes into them, so a panel converts once and
re-renders (new seeds, retakes) cost nothing. With no LLM the prose is used as
before, and the render says so in a warning.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from manganation.script.schema import PanelSpec

log = logging.getLogger(__name__)

VERSION = 2  # bump when the prompt or schema changes: old cache entries stop matching

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "pose": {"type": "array", "items": {"type": "string"}},
                    "expression": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "pose", "expression"],
            },
        },
        "shared": {"type": "array", "items": {"type": "string"}},
        "setting": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["characters", "shared", "setting"],
}

SYSTEM_PROMPT = """\
You turn one manga panel's script into Danbooru tags for an anime image model \
(NoobAI-XL). Use common Danbooru tags: short lowercase phrases such as "sitting", \
"standing", "squatting", "knees up", "against fence", "hands on own hips", \
"looking down", "looking up", "holding another's wrist", "pulling", "running", \
"stairs", "rooftop", "chain-link fence", "classroom", "indoors", "outdoors", "sunset", \
"evening", "orange sky", "grin", "smile", "closed eyes", "sigh", "surprised", \
"wide-eyed", "open mouth", "angry", "bento", "on lap".

Rules:
- `characters`: one entry for each VISIBLE character listed, by the given name. \
`pose` = body position and gestures (2-5 tags). Give EVERY visible character a pose, \
inferring it when the action implies it (if one stands over another, the other is \
sitting). `expression` = face (1-3 tags), inferred from the action when not stated. \
A character who is only heard (off-panel) is not listed and gets no tags.
- `shared`: interactions between characters, and every prop or object the action \
names, as its Danbooru tag (a lunchbox is "bento") (0-5 tags).
- `setting`: where it happens (2-6 tags): the place, EXACTLY ONE of "indoors" or \
"outdoors", the time of day if given, notable background objects.
- Never use character names, pronouns or sentences in tags. No clothing or hair: \
appearance is handled elsewhere. No camera words.
- Output ONLY the JSON object described by the schema."""

# Worked examples (deliberately not from any eval suite).
EXAMPLES = [
    ("Visible characters: Mei, Taro\nShot: medium shot\nLocation: Classroom — morning\n"
     "Action: Mei slams her hand on Taro's desk. He leans back in his chair, startled.",
     {"characters": [
         {"name": "Mei", "pose": ["standing", "leaning forward", "hand on table"],
          "expression": ["angry", "clenched teeth"]},
         {"name": "Taro", "pose": ["sitting", "on chair", "leaning back"],
          "expression": ["surprised", "open mouth"]}],
      "shared": ["desk"], "setting": ["classroom", "indoors", "morning", "window"]}),
    ("Visible characters: Nami\nShot: wide shot\nLocation: The old pier — night\n"
     "Action: Nami crouches at the edge of the pier, a paper lantern in her hands. "
     "Her brother calls from off-panel.",
     {"characters": [
         {"name": "Nami", "pose": ["squatting", "holding lantern", "looking down"],
          "expression": ["sad", "parted lips"]}],
      "shared": ["paper lantern"], "setting": ["pier", "outdoors", "night", "ocean"]}),
]


@dataclass
class Staging:
    # name -> {"pose": [...], "expression": [...]}
    characters: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    shared: list[str] = field(default_factory=list)
    setting: list[str] = field(default_factory=list)

    def pose(self, name: str) -> list[str]:
        return self.characters.get(name, {}).get("pose", [])

    def expression(self, name: str) -> list[str]:
        return self.characters.get(name, {}).get("expression", [])


_NAME_RE = re.compile(r"[^a-z0-9 ()'+:;.\-^_<>|=@!?/\\*~]")


def clean_tags(tags: Any, names: list[str]) -> list[str]:
    """Lowercase, underscores to spaces, no sentences or character names, no repeats."""
    lowered = {n.lower() for n in names}
    out: list[str] = []
    for tag in tags if isinstance(tags, list) else []:
        tag = " ".join(str(tag).replace("_", " ").lower().split()).strip(" ,.")
        if (not tag or len(tag.split()) > 4 or _NAME_RE.search(tag)
                or any(n in tag.split() for n in lowered) or tag in out):
            continue
        out.append(tag)
    return out


def from_answer(data: Any, spec: PanelSpec) -> Staging:
    if not isinstance(data, dict):
        raise ValueError(f"staging model returned {type(data).__name__}, not an object")
    by_lower = {c.lower(): c for c in spec.characters}
    characters = {}
    for entry in data.get("characters") or []:
        name = by_lower.get(str((entry or {}).get("name", "")).strip().lower())
        if name is None:  # someone not visible in this panel
            continue
        characters[name] = {"pose": clean_tags(entry.get("pose"), spec.characters),
                            "expression": clean_tags(entry.get("expression"), spec.characters)}
    place = clean_tags(data.get("setting"), spec.characters)
    if "indoors" in place and "outdoors" in place:  # a contradiction helps neither
        place = [t for t in place if t not in ("indoors", "outdoors")]
    return Staging(characters=characters,
                   shared=clean_tags(data.get("shared"), spec.characters), setting=place)


def panel_text(spec: PanelSpec, place: str) -> str:
    lines = [f"Visible characters: {', '.join(spec.characters) or 'none'}"]
    if spec.camera:
        lines.append(f"Shot: {spec.camera}")
    if place:
        lines.append(f"Location: {place}")
    if spec.action:
        lines.append(f"Action: {spec.action}")
    for who, face in spec.expressions.items():
        lines.append(f"{who}'s expression: {face}")
    return "\n".join(lines)


def cache_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{VERSION}\n{model}\n{text}".encode()).hexdigest()[:24]


def cache_dir() -> Path:
    from manganation.config import data_root

    return data_root() / "cache" / "staging"


def stage(spec: PanelSpec, place: str, *, client=None, settings=None,
          cache: Path | None = None) -> Staging:
    """Tags for ``spec`` (whose setting is ``place``), from the cache or the LLM.
    Raises on any LLM failure; the caller falls back to the prose."""
    from manganation.config import load_settings
    from manganation.script.llm import OllamaClient

    settings = settings or load_settings()
    text = panel_text(spec, place)
    model = settings.llm.model
    folder = cache if cache is not None else cache_dir()
    hit = folder / f"{cache_key(model, text)}.json"
    if hit.is_file():
        try:
            return Staging(**json.loads(hit.read_text())["staging"])
        except (OSError, ValueError, KeyError, TypeError):
            pass  # a damaged entry is just a miss
    active = client or OllamaClient(settings.llm.base_url, model)
    try:
        shots = [m for question, answer in EXAMPLES
                 for m in ({"role": "user", "content": question},
                           {"role": "assistant", "content": json.dumps(answer)})]
        data = active.chat_json([{"role": "system", "content": SYSTEM_PROMPT}, *shots,
                                 {"role": "user", "content": text}],
                                schema=SCHEMA, timeout=180.0)
    finally:
        if client is None and settings.llm.unload_before_render:
            active.unload()  # give the GPU back to ComfyUI
    staging = from_answer(data, spec)
    folder.mkdir(parents=True, exist_ok=True)
    hit.write_text(json.dumps({"model": model, "panel": text, "staging": asdict(staging)},
                              indent=2, ensure_ascii=False))
    return staging
