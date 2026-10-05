"""Character data model for visual consistency (img-memory).

A character has a stable identity (name + canonical traits) and a set of
*versions* of their reference image. Panels bind to a character and reuse one
version, so the same face/hair/outfit recurs across the whole story.

When a story gives no reference image, imanganation generates a **design sheet**
first and locks it as the character's ``base`` version — the self-referential
chain that keeps identity stable without any LoRA training.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class VersionKind(StrEnum):
    BASE = "base"  # canonical design sheet — the identity anchor
    VARIANT = "variant"  # alternate outfit / pose / expression of the same character
    EVOLUTION = "evolution"  # in-story change (haircut, injury, timeskip)


# Classifying free-text traits that are really expression or body language. Checked
# mannerism-first, so "serious demeanor" is a mannerism and "stoic expression" an
# expression. Used to migrate older manifests (and catch LLM slips), where these were
# stored as identity in ``distinguishing``/``descriptors``.
_MANNERISM_WORDS = (
    "demeanor", "demeanour", "mood", "stance", "pose", "posture", "slouch", "gesture",
    "attitude", "energetic", "shy", "confident", "cheerful", "bouncy", "fidget",
)
_EXPRESSION_WORDS = (
    "grin", "smile", "smirk", "frown", "laugh", "pout", "scowl", "glare", "wink",
    "tears", "crying", "blush", "teeth", "toothy", "open mouth", "closed eyes",
    "expression", "angry", "happy", "sad", "surprised", "serious",
)


def classify_trait(tag: str) -> str:
    """'mannerism', 'expression' or 'appearance' for a free-text trait."""
    low = tag.lower()
    if any(w in low for w in _MANNERISM_WORDS):
        return "mannerism"
    if any(w in low for w in _EXPRESSION_WORDS):
        return "expression"
    return "appearance"


class AppearanceSpec(BaseModel):
    """Structured appearance traits, used to build generation prompts.

    Free-text ``descriptors`` carries anything *visual* that does not fit a field; the
    structured fields exist so prompt building is deterministic and consistent.

    Identity (``appearance_tags``) is physical only. A character's characteristic face
    (``default_expression``) and body language (``mannerisms``) are kept apart: stored
    as identity they overrode what a panel or an inpaint asked for (Yuki's "wide toothed
    grin" beat "surprised face, open mouth").
    """

    gender: str = ""  # "1girl" / "1boy" / "1other" token or free text
    age: str = ""  # "teen", "young adult", "30s" …
    hair_color: str = ""
    hair_style: str = ""  # "long straight", "short spiky", "twin tails" …
    eye_color: str = ""
    skin: str = ""
    build: str = ""
    outfit: str = ""
    accessories: list[str] = Field(default_factory=list)  # glasses, ribbon, scarf …
    distinguishing: list[str] = Field(default_factory=list)  # scar, tattoo, eyepatch …
    descriptors: list[str] = Field(default_factory=list)  # extra *visual* tags, verbatim
    # Not identity: a panel's expression or an inpaint prompt overrides these.
    default_expression: str = ""  # the characteristic face, e.g. "wide toothed grin"
    mannerisms: list[str] = Field(default_factory=list)  # e.g. "relaxed slouch"

    @model_validator(mode="before")
    @classmethod
    def _split_expression_traits(cls, data: Any) -> Any:
        """Move expression/body-language words out of the identity lists (older
        manifests and LLM output stored them in distinguishing/descriptors)."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        expressions = [data["default_expression"]] if data.get("default_expression") else []
        mannerisms = list(data.get("mannerisms") or [])
        for key in ("distinguishing", "descriptors"):
            kept = []
            for tag in data.get(key) or []:
                kind = classify_trait(tag) if isinstance(tag, str) else "appearance"
                if kind == "expression":
                    expressions.append(tag.strip())
                elif kind == "mannerism":
                    mannerisms.append(tag.strip())
                else:
                    kept.append(tag)
            data[key] = kept
        data["default_expression"] = ", ".join(dict.fromkeys(e for e in expressions if e))
        data["mannerisms"] = list(dict.fromkeys(m for m in mannerisms if m))
        return data

    def appearance_tags(self) -> list[str]:
        """Physical identity only, ordered and deduped: what must hold in every panel."""
        tags: list[str] = []
        for value in (
            self.gender,
            self.age,
            self.hair_color,
            self.hair_style,
            self.eye_color,
            self.skin,
            self.build,
        ):
            if value:
                tags.append(value.strip())
        if self.outfit:
            tags.append(self.outfit.strip())
        tags.extend(a.strip() for a in self.accessories if a.strip())
        tags.extend(d.strip() for d in self.distinguishing if d.strip())
        tags.extend(d.strip() for d in self.descriptors if d.strip())
        seen: list[str] = []
        for t in tags:
            if t and t not in seen:
                seen.append(t)
        return seen

    def prompt_tags(self, *, expression: bool = True, mannerisms: bool = False) -> list[str]:
        """Appearance, plus the default expression (and mannerisms) when wanted."""
        tags = self.appearance_tags()
        if expression and self.default_expression:
            tags.append(self.default_expression)
        if mannerisms:
            tags.extend(m for m in self.mannerisms if m not in tags)
        return tags

    @field_validator("accessories", "distinguishing", "descriptors", "mannerisms")
    @classmethod
    def _clean_list(cls, v: list[str]) -> list[str]:
        return [item.strip() for item in v if item and item.strip()]


class CharacterVersion(BaseModel):
    """One rendered reference image for a character."""

    id: str  # version id, e.g. "base", "school", "v2"
    kind: VersionKind = VersionKind.BASE
    image: str  # path relative to the character directory
    prompt: str = ""  # prompt used to generate it (reproducibility)
    seed: int | None = None
    parent: str | None = None  # version this was derived from (lineage)
    note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def lineage(self) -> list[str]:
        return [self.id]


class Character(BaseModel):
    """A story character and their visual-memory versions."""

    name: str
    aliases: list[str] = Field(default_factory=list)
    appearance: AppearanceSpec = AppearanceSpec()
    versions: list[CharacterVersion] = Field(default_factory=list)
    default_version: str | None = None  # which version panels use by default
    source: str = "auto"  # "auto" (generated design) | "user" (supplied reference)
    notes: str = ""

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("character name must not be empty")
        return v

    def version(self, version_id: str) -> CharacterVersion | None:
        return next((v for v in self.versions if v.id == version_id), None)

    def active_version(self) -> CharacterVersion | None:
        if self.default_version:
            found = self.version(self.default_version)
            if found:
                return found
        return self.versions[0] if self.versions else None

    def add_version(self, version: CharacterVersion) -> None:
        if self.version(version.id) is not None:
            raise ValueError(f"version {version.id!r} already exists for {self.name!r}")
        self.versions.append(version)
        if self.default_version is None:
            self.default_version = version.id

    def matches(self, name: str) -> bool:
        target = name.strip().casefold()
        if self.name.casefold() == target:
            return True
        return any(a.casefold() == target for a in self.aliases)


class Cast(BaseModel):
    """All characters in one project, loadable/saveable as ``characters.json``."""

    characters: list[Character] = Field(default_factory=list)

    def get(self, name: str) -> Character | None:
        return next((c for c in self.characters if c.matches(name)), None)

    def names(self) -> list[str]:
        return [c.name for c in self.characters]

    def remove(self, name: str) -> Character | None:
        """Take the character with this name or alias out of the cast."""
        character = self.get(name)
        if character is not None:
            self.characters.remove(character)
        return character

    def add(self, character: Character) -> Character:
        if self.get(character.name) is not None:
            raise ValueError(f"character {character.name!r} already exists")
        self.characters.append(character)
        return character

    def ensure(self, name: str) -> Character:
        """Return the character for ``name``, creating an empty one if new."""
        existing = self.get(name)
        if existing is not None:
            return existing
        return self.add(Character(name=name.strip()))

    @classmethod
    def from_json(cls, text: str) -> Cast:
        return cls.model_validate_json(text)

    def to_json(self, *, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)
