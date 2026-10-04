"""Ollama-backed script parser: free-form prose -> validated :class:`Script`.

Strategy (cheap first, LLM only when needed):

1. If the input already follows the token grammar, use the deterministic
   tokenizer (:mod:`manganation.script.formats.mangaplay`) — instant, lossless.
2. Otherwise ask the local LLM to normalise the prose into panels, constrained
   by a JSON schema, then validate with pydantic. One repair attempt is made if
   the model returns something that fails validation.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import ValidationError

from manganation.config import Settings, load_settings
from manganation.script.formats.mangaplay import (
    add_mentioned_characters,
    looks_canonical,
    parse_canonical,
    split_cast,
)
from manganation.script.llm import LLMError, OllamaClient
from manganation.script.schema import CastEntry, ColorMode, PanelSpec, ReadingOrder, Script


class LLMClient(Protocol):
    """Structural type for an LLM client (real or stubbed in tests)."""

    def chat_json(
        self, messages: list[dict[str, str]], *, schema: dict[str, Any] | None = ...
    ) -> Any: ...

    def unload(self) -> None: ...

# JSON schema handed to Ollama's structured-output decoder.
PANEL_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "panels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    "panel": {"type": "integer"},
                    "scene_heading": {"type": "string"},
                    "location": {"type": "string"},
                    "characters": {"type": "array", "items": {"type": "string"}},
                    "action": {"type": "string"},
                    "camera": {"type": "string"},
                    "expressions": {"type": "object", "additionalProperties": {"type": "string"}},
                    "dialogue": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "speaker": {"type": "string"},
                                "text": {"type": "string"},
                                "kind": {
                                    "type": "string",
                                    "enum": ["speech", "thought", "narration", "shout", "whisper"],
                                },
                            },
                            "required": ["speaker", "text"],
                        },
                    },
                    "sfx": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                    "flashback": {"type": "boolean"},
                },
                "required": ["panel", "action"],
            },
        }
    },
    "required": ["panels"],
}

SYSTEM_PROMPT = """\
You are a manga script breakdown assistant. You convert a story or loose script \
into an ordered list of manga PANELS for an image generator.

Rules:
- Break the story into individual panels in reading order. One visual moment per panel.
- Use standard manga page/panel numbering. Default to a new page every 4-6 panels \
unless the input specifies pages; keep `panel` sequential within each page.
- `action` is a concise visual description of what is SHOWN (who, doing what, \
where, framing). Do not put dialogue in `action`.
- `characters` lists the named characters visible in the panel (real names only, \
not pronouns).
- `camera` is one of: wide shot, medium shot, close-up, extreme close-up, \
over-the-shoulder, low angle, high angle, dutch angle, pov, two-shot, reaction shot, \
establishing shot (or empty if unclear).
- Capture dialogue in `dialogue` for narrative continuity (it will NOT be drawn). \
Use the speaker's name; kind is speech/thought/narration/shout/whisper.
- `sfx` lists sound effects. `flashback` is true for past-scene panels.
- Never invent characters who are not in the text.
- Output ONLY the JSON object described by the schema."""


class ParseError(RuntimeError):
    pass


def _build_messages(script_text: str, reading_order: ReadingOrder) -> list[dict[str, str]]:
    direction = "right-to-left (manga)" if reading_order is ReadingOrder.RTL else "left-to-right"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Reading order is {direction}.\n\n"
                f"Break the following script into panels:\n\n---\n{script_text.strip()}\n---"
            ),
        },
    ]


def _panels_to_script(
    data: dict[str, Any],
    *,
    title: str,
    reading_order: ReadingOrder,
    default_color_mode: ColorMode,
) -> Script:
    raw_panels = data.get("panels")
    if not isinstance(raw_panels, list) or not raw_panels:
        raise ParseError("model returned no panels")

    panels: list[PanelSpec] = []
    for i, item in enumerate(raw_panels, start=1):
        if not isinstance(item, dict):
            raise ParseError(f"panel {i} is not an object")
        item.setdefault("panel", i)
        item.setdefault("page", 1)
        item["reading_order"] = reading_order.value
        item["color_mode"] = default_color_mode.value
        try:
            panels.append(PanelSpec(**item))
        except ValidationError as exc:
            raise ParseError(f"panel {i} invalid: {exc}") from exc

    return Script(
        title=title,
        reading_order=reading_order,
        default_color_mode=default_color_mode,
        panels=panels,
    )


def parse(
    script_text: str,
    *,
    title: str = "",
    settings: Settings | None = None,
    client: LLMClient | None = None,
    force_llm: bool = False,
) -> Script:
    """Parse script text into a validated :class:`Script`.

    Uses the deterministic tokenizer when the input is already canonical, unless
    ``force_llm`` is set.
    """
    settings = settings or load_settings()
    reading_order = ReadingOrder(settings.defaults.reading_order)
    default_color_mode = ColorMode(settings.defaults.color_mode)

    if not force_llm and looks_canonical(script_text):
        return parse_canonical(
            script_text,
            title=title,
            reading_order=reading_order,
            default_color_mode=default_color_mode,
        )

    # A CHARACTERS block is parsed deterministically either way; the LLM only sees
    # the story, and its panels then pick up the declared names too.
    cast, story = split_cast(script_text)
    active_client: LLMClient = client or OllamaClient(settings.llm.base_url, settings.llm.model)
    if client is None:  # a real LLM: ComfyUI lets go of the GPU first
        from manganation.render.comfy_client import ComfyClient

        active_client.unload()  # reload it onto the GPU ComfyUI just freed
        ComfyClient(settings.comfyui.base_url).free()
    messages = _build_messages(story, reading_order)

    last_error: Exception | None = None
    convo = list(messages)
    for attempt in range(2):
        try:
            data = active_client.chat_json(convo, schema=PANEL_LIST_SCHEMA)
            script = _panels_to_script(
                data,
                title=title,
                reading_order=reading_order,
                default_color_mode=default_color_mode,
            )
            if settings.llm.unload_before_render:
                active_client.unload()
            script.cast = [CastEntry(**c) for c in cast]
            return add_mentioned_characters(script)
        except (ParseError, LLMError, ValidationError) as exc:
            last_error = exc
            if attempt == 0:
                repair = {"role": "user", "content": f"That failed: {exc}. Return corrected JSON."}
                convo = [*messages, repair]

    raise ParseError(f"failed to parse script: {last_error}")
