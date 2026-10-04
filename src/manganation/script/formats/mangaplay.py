"""Canonical page/panel scripts as validated :class:`Script` objects.

The tokenizer itself is :mod:`manganation.script.formats.canonical` (standard library
only, shared with the GIMP plug-in); this module wraps its output in the engine's
pydantic models. See that module for the grammar, including the ``CHARACTERS`` block.
"""

from __future__ import annotations

from manganation.script.formats import canonical
from manganation.script.formats.canonical import looks_canonical, split_cast
from manganation.script.schema import CastEntry, ColorMode, PanelSpec, ReadingOrder, Script

__all__ = ["add_mentioned_characters", "looks_canonical", "parse_canonical", "split_cast"]


def add_mentioned_characters(script: Script) -> Script:
    """Add known characters named in a panel's action text (see ``canonical.add_mentions``)."""
    panels = [{"characters": list(p.characters), "action": p.action,
               "dialogue": [{"speaker": d.speaker} for d in p.dialogue]}
              for p in script.panels]
    canonical.add_mentions([c.model_dump() for c in script.cast], panels)
    for panel, found in zip(script.panels, panels, strict=True):
        panel.characters = found["characters"]
    return script


def parse_canonical(
    text: str,
    *,
    title: str = "",
    reading_order: ReadingOrder = ReadingOrder.RTL,
    default_color_mode: ColorMode = ColorMode.COLOR,
) -> Script:
    """Parse a token-grammar script into a validated :class:`Script`.

    Raises :class:`ValueError` if no panels are found.
    """
    data = canonical.parse(text)
    return Script(
        title=title,
        reading_order=reading_order,
        default_color_mode=default_color_mode,
        cast=[CastEntry(**c) for c in data["cast"]],
        panels=[PanelSpec(**p, color_mode=ColorMode.INHERIT) for p in data["panels"]],
    )
