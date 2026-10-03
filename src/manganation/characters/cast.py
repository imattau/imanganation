"""Cast assembly: turn a parsed script into a character registry.

Flow:

1. Collect every character name the script mentions (already extracted by the
   Phase 1 parser into ``PanelSpec.characters``).
2. For each, derive an appearance (LLM) if the script does not give one.
3. Optionally generate a design sheet and lock it as the ``base`` version.

The result is a persistent ``characters.json`` the renderer can consult.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from manganation.characters.generator import DesignResult, generate_design
from manganation.characters.registry import CharacterRegistry
from manganation.characters.traits import derive_appearance
from manganation.config import Settings, load_settings
from manganation.script.schema import Script


@dataclass
class CastPlan:
    """What :func:`assemble_cast` decided/did."""

    names: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    designs: list[DesignResult] = field(default_factory=list)


def collect_names(script: Script) -> list[str]:
    """Every character name mentioned, in first-appearance order."""
    names: list[str] = []
    for panel in script.panels:
        for name in panel.characters:
            if name and name not in names:
                names.append(name)
    return names


def assemble_cast(
    script: Script,
    project: str,
    *,
    registry: CharacterRegistry | None = None,
    derive: bool = True,
    render_designs: bool = False,
    seed: int | None = None,
    settings: Settings | None = None,
    script_text: str = "",
) -> CastPlan:
    """Populate the project's registry from the script's cast.

    ``derive`` asks the LLM for missing appearances; ``render_designs`` also
    generates and locks a design sheet per character.
    """
    settings = settings or load_settings()
    registry = registry or CharacterRegistry(project)
    plan = CastPlan(names=collect_names(script))

    for name in plan.names:
        existed = registry.get(name) is not None
        character = registry.ensure(name)
        if not existed:
            plan.created.append(name)

        needs_appearance = not character.appearance.prompt_tags()
        if derive and needs_appearance and script_text:
            character.appearance = derive_appearance(
                name, script_text, existing=character.appearance, settings=settings
            )

    registry.save()

    if render_designs:
        # Design only characters that do not already have a base reference.
        for name in plan.names:
            character = registry.get(name)
            if character is None:
                continue
            if character.version("base") is not None:
                continue
            plan.designs.append(
                generate_design(
                    character,
                    registry,
                    seed=seed,
                    settings=settings,
                )
            )
        registry.save()

    return plan
