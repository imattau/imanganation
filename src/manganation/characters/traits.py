"""Derive character appearance traits from the script (reference-less).

When the script does not describe a character's look (the common case — manga
scripts rarely do), we ask the local LLM to invent a *self-consistent* design
from whatever the text implies (role, personality, genre). The result is an
:class:`AppearanceSpec` that becomes the character's identity for all panels.

If the script *does* describe traits, those win. The LLM is only asked to fill
the gaps, and never to contradict the text.
"""

from __future__ import annotations

from typing import Any, Protocol

from manganation.characters.schema import AppearanceSpec
from manganation.config import Settings, load_settings
from manganation.script.llm import OllamaClient

APPEARANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "gender": {"type": "string", "description": "e.g. '1girl', '1boy', '1other'"},
        "age": {"type": "string"},
        "hair_color": {"type": "string"},
        "hair_style": {"type": "string"},
        "eye_color": {"type": "string"},
        "skin": {"type": "string"},
        "build": {"type": "string"},
        "outfit": {"type": "string"},
        "accessories": {"type": "array", "items": {"type": "string"}},
        "distinguishing": {"type": "array", "items": {"type": "string"}},
        "descriptors": {"type": "array", "items": {"type": "string"}},
        "default_expression": {"type": "string",
                               "description": "characteristic face, e.g. 'wide toothed grin'"},
        "mannerisms": {"type": "array", "items": {"type": "string"},
                       "description": "body language / demeanor, e.g. 'relaxed slouch'"},
    },
    "required": ["gender", "hair_color", "hair_style", "eye_color", "outfit"],
}

SYSTEM_PROMPT = """\
You design anime/manga characters. Given a character named in a story plus any \
context, produce a concise, visually distinctive appearance as structured tags \
(suitable for an image generator).

Rules:
- Follow the story: never contradict a trait the text states explicitly.
- Fill every field with a concrete, specific choice. Avoid generic defaults.
- Keep the design recognisable and consistent for reuse across many panels.
- `gender` must be a count tag: '1girl', '1boy', or '1other'.
- `hair_color`/`eye_color` may be anime colours (e.g. 'silver hair', 'crimson eyes').
- `outfit` is a single phrase (e.g. 'school uniform with red necktie').
- `accessories`/`distinguishing` are short *physical* tags (e.g. 'hair ribbon', \
'scar over left eye').
- `descriptors` is any extra *visual* tags (e.g. 'tall', 'freckles'), never an \
expression or a pose.
- `default_expression` is the character's characteristic face (e.g. 'wide toothed \
grin', 'stoic expression'); panels may override it.
- `mannerisms` is body language or demeanor (e.g. 'relaxed slouch', 'energetic \
stance'); never put these in the physical fields.
- Output ONLY the JSON object described by the schema."""


class LLMClient(Protocol):
    def chat_json(
        self, messages: list[dict[str, str]], *, schema: dict[str, Any] | None = ...
    ) -> Any: ...


def _context_for(name: str, script_text: str, existing: AppearanceSpec | None) -> str:
    known = ""
    if existing is not None:
        tags = existing.prompt_tags(mannerisms=True)
        if tags:
            known = "\nAlready known traits (keep these): " + ", ".join(tags)
    excerpt = script_text.strip()
    if len(excerpt) > 6000:
        excerpt = excerpt[:6000] + "\n…[truncated]"
    return f"Character: {name}{known}\n\nStory script:\n---\n{excerpt}\n---"


def derive_appearance(
    name: str,
    script_text: str,
    *,
    existing: AppearanceSpec | None = None,
    settings: Settings | None = None,
    client: LLMClient | None = None,
) -> AppearanceSpec:
    """Return an :class:`AppearanceSpec` for ``name`` derived from the script."""
    settings = settings or load_settings()
    active: LLMClient = client or OllamaClient(settings.llm.base_url, settings.llm.model)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _context_for(name, script_text, existing)},
    ]
    data = active.chat_json(messages, schema=APPEARANCE_SCHEMA)
    if not isinstance(data, dict):
        raise ValueError(f"appearance model returned non-object: {type(data)}")
    spec = AppearanceSpec(**{k: v for k, v in data.items() if k in AppearanceSpec.model_fields})
    if existing is not None:
        spec = _merge_missing(spec, existing)
    return spec


def _merge_missing(new: AppearanceSpec, old: AppearanceSpec) -> AppearanceSpec:
    """Keep any trait the caller already knew; new fills only the gaps."""
    data = new.model_dump()
    for field in AppearanceSpec.model_fields:
        old_val = getattr(old, field)
        if old_val and not data.get(field):
            data[field] = old_val
    return AppearanceSpec(**data)
