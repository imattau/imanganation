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
from manganation.characters.schema import VersionKind
from manganation.characters.traits import derive_appearance
from manganation.config import Settings, load_settings
from manganation.render.comfy_client import ComfyClient
from manganation.script.schema import Script


@dataclass
class CastPlan:
    """What :func:`assemble_cast` decided/did."""

    names: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    designs: list[DesignResult] = field(default_factory=list)


def collect_names(script: Script) -> list[str]:
    """Every character: the declared cast first, then the rest in first-appearance order."""
    names: list[str] = [entry.name for entry in script.cast]
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

    declared = {entry.name: entry for entry in script.cast}
    for name in plan.names:
        existed = registry.get(name) is not None
        character = registry.ensure(name)
        if not existed:
            plan.created.append(name)
        entry = declared.get(name)
        description = entry.description if entry else ""
        if entry:
            character.aliases = [*character.aliases,
                                 *[a for a in entry.aliases if a not in character.aliases]]
            character.notes = character.notes or description

        # Traits already there (derived before, or edited) are kept
        needs_appearance = not character.appearance.appearance_tags()
        if derive and needs_appearance and (script_text or description):
            character.appearance = derive_appearance(
                name, script_text, existing=character.appearance, settings=settings,
                description=description,
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


@dataclass
class CharacterDesign:
    """What :func:`design_character` did: the traits it settled on and the sheet."""

    name: str
    created: bool
    appearance: dict
    version_id: str
    image: str
    seed: int
    prompt: str


def design_character(
    registry: CharacterRegistry,
    name: str,
    description: str = "",
    *,
    aliases: list[str] | None = None,
    seed: int | None = None,
    redesign: bool = False,
    settings: Settings | None = None,
    llm=None,
    comfy=None,
    style: dict | None = None,
    engine: str | None = None,
    variant: dict | None = None,
) -> CharacterDesign:
    """Create (or update) one character from the author's description, derive its
    traits with the LLM, release the LLM's VRAM, then render and lock its design sheet.

    Used by the engine's ``POST /characters``: the GIMP plug-in's New Character… and
    New Project from Script… both end here. ``redesign`` re-derives traits from a new
    description (an empty one keeps the traits) and adds a new design version
    (``design-02``, …) as the active reference; earlier designs are never overwritten.
    ``style`` is the project's look (styles.py): its tags go into the design, so the
    reference every panel takes after is drawn in that look too.

    ``variant`` ({"id", "description"}) designs *another* reference of an already
    designed character: the traits are untouched, the description (an outfit, a season)
    is added to this one image, it is saved as version ``id``, and the default reference
    stays as it was. Panels pick it per character. The seed defaults to the default
    version's, and the default reference image goes in as a reference itself (IP-Adapter
    for SDXL, <image1> for Qwen-Image), so the same person is drawn, not a lookalike.
    Z-Anime has no image input and relies on the seed alone.
    """
    from manganation.script.llm import OllamaClient

    settings = settings or load_settings()
    created = registry.get(name) is None
    if variant is not None and (created or registry.get(name).default_version is None):
        raise ValueError(f"design {name} first: another reference needs the first one")
    character = registry.ensure(name)
    character.aliases = [*character.aliases,
                         *[a for a in (aliases or []) if a not in character.aliases]]
    if description.strip() and variant is None:
        character.notes = description.strip()
    if variant is None and (redesign or not character.appearance.appearance_tags()):
        if not description.strip() and not character.appearance.appearance_tags():
            raise ValueError(f"describe {character.name} first: no traits or description")
        if description.strip():
            client = llm or OllamaClient(settings.llm.base_url, settings.llm.model)
            # LLM and diffusion take turns on the GPU: ComfyUI lets go first, and an LLM
            # still loaded (placed on the CPU while ComfyUI held the GPU) is reloaded
            client.unload()
            (comfy or ComfyClient(settings.comfyui.base_url)).free()
            character.appearance = derive_appearance(
                character.name, "", existing=None if redesign else character.appearance,
                settings=settings, client=client, description=description,
            )
            if settings.llm.unload_before_render:
                client.unload()  # never share the GPU with SDXL
    registry.save()
    version_id, number = "base", 1
    if variant is not None:
        version_id = variant["id"]
        if character.version(version_id) is not None:
            raise ValueError(f"{character.name} already has a reference called {version_id}")
        base = character.active_version()
        if seed is None and base is not None:
            seed = base.seed
    while variant is None and character.version(version_id) is not None:
        number += 1
        version_id = f"design-{number:02d}"
    engine = engine or settings.defaults.renderer.engine
    prompt = None
    if style or variant is not None:
        from manganation.characters.generator import design_engine_prompt
        from manganation.render.panel import load_style

        look = load_style(style) if style else {"tags": [], "prose": None, "extra_negative": ""}
        prompt = design_engine_prompt(
            character, engine, extra=look["tags"] if engine == "sdxl" else None,
            style=look["prose"],
            variation=" ".join(variant["description"].split()) if variant else "",
            has_reference=variant is not None and engine == "qwen_image_21")
        if look["extra_negative"]:  # what the look must not bring (seinen: no monsters)
            prompt.negative = f"{prompt.negative}, {look['extra_negative']}"
    result = generate_design(character, registry, version_id=version_id, seed=seed,
                             settings=settings, client=comfy, prompt=prompt, engine=engine,
                             kind=VersionKind.VARIANT if variant else VersionKind.BASE,
                             reference=(registry.reference_path(character.name)
                                        if variant else None))
    return CharacterDesign(
        name=character.name, created=created,
        appearance=character.appearance.model_dump(), version_id=result.version_id,
        image=str(result.image), seed=result.seed, prompt=result.prompt,
    )
