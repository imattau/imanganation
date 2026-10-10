"""Character registry: persistent img-memory for a project's cast.

Layout::

    projects/<name>/
      characters.json                 <- serialised Cast
      characters/
        <slug>/
          base.png                    <- canonical design sheet (identity anchor)
          <version>.png
          manifest.json               <- redundant per-character copy (portable)

The registry is the single source of truth for "who exists" and "what do they
look like", and is what the renderer queries to attach IP-Adapter references.
"""

from __future__ import annotations

import re
from pathlib import Path

from manganation.characters.schema import Cast, Character, CharacterVersion
from manganation.project import project_dir


def slugify(name: str) -> str:
    """Filesystem-safe, stable slug for a character name."""
    slug = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE).strip().lower()
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug or "character"


class CharacterRegistry:
    def __init__(self, project: str | None = None, *, root: Path | None = None):
        if root is None and project is None:
            raise ValueError("CharacterRegistry needs a project name or a root path")
        self.project = project or ""
        self.root = Path(root) if root is not None else project_dir(project or "")
        self.chars_dir = self.root / "characters"
        self.cast_file = self.root / "characters.json"
        self._cast: Cast | None = None

    @classmethod
    def from_path(cls, root: str | Path) -> CharacterRegistry:
        """Build a registry from a project directory path (no name lookup)."""
        return cls(root=Path(root))

    # --- persistence --------------------------------------------------------

    @property
    def cast(self) -> Cast:
        if self._cast is None:
            self._cast = self._load()
        return self._cast

    def _load(self) -> Cast:
        if self.cast_file.exists():
            return Cast.from_json(self.cast_file.read_text())
        return Cast()

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.cast_file.write_text(self.cast.to_json())
        for character in self.cast.characters:
            self._write_manifest(character)

    def reload(self) -> None:
        self._cast = None

    # --- character dirs -----------------------------------------------------

    def character_dir(self, name: str) -> Path:
        return self.chars_dir / slugify(name)

    def ensure_dir(self, name: str) -> Path:
        d = self.character_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write_manifest(self, character: Character) -> None:
        d = self.ensure_dir(character.name)
        (d / "manifest.json").write_text(character.model_dump_json(indent=2))

    # --- queries ------------------------------------------------------------

    def get(self, name: str) -> Character | None:
        return self.cast.get(name)

    def ensure(self, name: str) -> Character:
        return self.cast.ensure(name)

    def names(self) -> list[str]:
        return self.cast.names()

    def remove(self, name: str) -> tuple[Character, Path | None] | None:
        """Delete a character from the cast. Their folder (designs, reference versions,
        manifest) is moved to ``characters/.deleted/<slug>-<time>/`` rather than erased,
        so a mistaken delete can be undone by hand. -> (the character, where the folder
        went or None if it had none), or None if there is no such character."""
        from datetime import datetime

        character = self.cast.remove(name)
        if character is None:
            return None
        folder, moved = self.character_dir(character.name), None
        if folder.is_dir():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            moved = self.chars_dir / ".deleted" / f"{folder.name}-{stamp}"
            n = 2
            while moved.exists():
                moved = moved.with_name(f"{folder.name}-{stamp}-{n}")
                n += 1
            moved.parent.mkdir(parents=True, exist_ok=True)
            folder.rename(moved)
        self.save()
        return character, moved

    # --- versions -----------------------------------------------------------

    def add_version(
        self,
        name: str,
        version: CharacterVersion,
        *,
        make_default: bool = False,
        replace: bool = False,
    ) -> Character:
        character = self.ensure(name)
        if replace and character.version(version.id) is not None:
            character.versions = [v for v in character.versions if v.id != version.id]
        character.add_version(version)
        if make_default or character.default_version == version.id:
            character.default_version = version.id
        self.save()
        return character

    def set_default(self, name: str, version_id: str) -> Character:
        character = self.cast.get(name)
        if character is None:
            raise KeyError(f"no such character: {name}")
        if character.version(version_id) is None:
            raise KeyError(f"{name} has no version {version_id}")
        character.default_version = version_id
        self.save()
        return character

    def add_user_reference(self, name: str, image_path: str, version_id: str = "base",
                           *, make_default: bool = True) -> Character:
        """Register a user-supplied reference image (no generation needed). With
        ``make_default`` False it is kept as an extra version (an outfit, an angle) for
        panels to pick, and the default reference stays as it was."""
        character = self.ensure(name)
        d = self.ensure_dir(name)
        dest = d / f"{version_id}.png"
        src = Path(image_path)
        if src.resolve() != dest.resolve():
            dest.write_bytes(src.read_bytes())
        rel = str(dest.relative_to(self.root))
        character.source = "user"
        return self.add_version(
            name,
            CharacterVersion(id=version_id, image=rel, note="user-supplied reference"),
            make_default=make_default or character.default_version is None,
        )

    def reference_path(self, name: str, version_id: str | None = None) -> Path | None:
        """Absolute path to a character's reference image, if one exists."""
        character = self.cast.get(name)
        if character is None:
            return None
        version = character.version(version_id) if version_id else character.active_version()
        if version is None:
            return None
        return self.root / version.image

    def resolve_references(self, names: list[str]) -> dict[str, Path]:
        """Map the panel's characters to existing reference images (skips missing)."""
        out: dict[str, Path] = {}
        for n in names:
            path = self.reference_path(n)
            if path and path.exists():
                out[n] = path
        return out


def load_registry(project: str) -> CharacterRegistry:
    return CharacterRegistry(project)
