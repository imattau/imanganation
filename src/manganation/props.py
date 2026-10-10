"""Props as references, like locations.

A prop is an object the story keeps coming back to: the red umbrella, Akira's katana,
the broken radio. It gets one reference image (the object alone on a plain ground),
designed from the author's description or supplied by them, and a panel that lists it
under ``props`` hands the renderer that picture (Qwen-Image 2.1 takes up to 10 references)
so the object looks the same in every panel. Renders on other engines use the prop's
name and description in the prompt.

Stored in the identity folder beside locations: ``props/props.json`` and
``props/<slug>.png``. A redesign, or a picture painted over in GIMP, writes a new image
and keeps the earlier ones listed in ``previous``.
"""

from __future__ import annotations

import json
import random
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path


def prop_key(name: str) -> str:
    """"The red Umbrella" -> ``red umbrella``: lower case, no leading article."""
    name = re.sub(r"^(?:the|a|an)\s+", "", " ".join(name.split()), flags=re.IGNORECASE)
    return name.lower()


def slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", key).strip("-") or "prop"


@dataclass
class Prop:
    key: str
    name: str  # as the author wrote it
    image: str = ""  # relative to the props folder; "" until a picture exists
    description: str = ""
    prompt: str = ""
    seed: int | None = None
    previous: list[str] = field(default_factory=list)  # earlier images, oldest first
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


class PropRegistry:
    def __init__(self, root: Path):
        self.root = Path(root) / "props"
        self.file = self.root / "props.json"
        data = json.loads(self.file.read_text()) if self.file.is_file() else {}
        self.props = {d["key"]: Prop(**d) for d in data.get("props", [])}

    @classmethod
    def from_path(cls, identity: Path) -> PropRegistry:
        return cls(identity)

    def get(self, name: str) -> Prop | None:
        return self.props.get(prop_key(name))

    def reference_path(self, name: str) -> Path | None:
        prop = self.get(name)
        path = self.root / prop.image if prop and prop.image else None
        return path if path is not None and path.is_file() else None

    def add(self, prop: Prop) -> None:
        self.props[prop.key] = prop
        self.save()

    def next_image(self, key: str) -> str:
        """A file name no image of this prop uses yet: <slug>.png, <slug>-2.png…"""
        base, n = slug(key), 1
        name = f"{base}.png"
        while (self.root / name).exists():
            n += 1
            name = f"{base}-{n}.png"
        return name

    def remove(self, name: str) -> tuple[Prop, Path | None] | None:
        """Drop a prop; its images move to ``props/.deleted/<slug>-<time>/`` rather than
        being erased. -> (prop, folder they moved to) or None."""
        prop = self.props.pop(prop_key(name), None)
        if prop is None:
            return None
        images = [self.root / n for n in (*prop.previous, prop.image) if n]
        images = [path for path in images if path.is_file()]
        moved = None
        if images:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            moved = self.root / ".deleted" / f"{slug(prop.key)}-{stamp}"
            moved.mkdir(parents=True, exist_ok=True)
            for path in images:
                shutil.move(str(path), moved / path.name)
        self.save()
        return prop, moved

    def set_reference(self, name: str, image: Path) -> Prop:
        """Make a picture (the author's, e.g. painted over in GIMP) a prop's reference:
        a copy beside the designed ones, the earlier image kept in ``previous``. A prop
        with no record yet gets one, named as given."""
        key = prop_key(name)
        before = self.props.get(key)
        self.root.mkdir(parents=True, exist_ok=True)
        stored = self.next_image(key)
        shutil.copyfile(image, self.root / stored)
        if before is None:
            prop = Prop(key=key, name=" ".join(name.split()), image=stored)
        else:
            previous = [*before.previous, before.image] if before.image else before.previous
            prop = Prop(**{**asdict(before), "image": stored, "prompt": "", "seed": None,
                           "previous": [p for p in previous if (self.root / p).is_file()],
                           "created_at": datetime.now(UTC).isoformat(timespec="seconds")})
        self.add(prop)
        return prop

    def make_current(self, name: str, image: str) -> Prop:
        """Make one of a prop's earlier images (a file name from ``previous``) its
        reference again; the current one goes back to ``previous``. KeyError: no such
        prop or image."""
        prop = self.props.get(prop_key(name))
        if prop is None:
            raise KeyError(f"no such prop: {name}")
        if image == prop.image:
            return prop
        if image not in prop.previous:
            raise KeyError(f"{prop.name} has no image {image}")
        prop.previous = [p for p in prop.previous if p != image]
        if prop.image:
            prop.previous.append(prop.image)
        prop.image, prop.prompt, prop.seed = image, "", None
        self.save()
        return prop

    def remove_image(self, name: str, image: str) -> tuple[Prop, Path | None]:
        """Set one image aside (moved to ``props/.deleted``, not erased). Deleting the
        current image promotes the newest earlier one; a prop's only image can't be
        deleted (delete the prop). KeyError: no such prop or image; ValueError: the last."""
        prop = self.props.get(prop_key(name))
        if prop is None:
            raise KeyError(f"no such prop: {name}")
        if image not in (prop.image, *prop.previous):
            raise KeyError(f"{prop.name} has no image {image}")
        if len([i for i in (prop.image, *prop.previous) if i]) == 1:
            raise ValueError(f"{prop.name}'s only image can't be deleted; "
                             "design another first, or delete the prop")
        if image == prop.image:
            prop.image = prop.previous.pop()
            prop.prompt, prop.seed = "", None
        else:
            prop.previous = [p for p in prop.previous if p != image]
        path, moved = self.root / image, None
        if path.is_file():
            folder = self.root / ".deleted"
            folder.mkdir(exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            moved = folder / f"{path.stem}-{stamp}{path.suffix}"
            shutil.move(str(path), moved)
        self.save()
        return prop, moved

    def set_description(self, name: str, description: str) -> Prop:
        """The author's words for a prop, kept with or without a picture."""
        key = prop_key(name)
        prop = self.props.get(key) or Prop(key=key, name=" ".join(name.split()))
        prop.description = " ".join(description.split())
        self.add(prop)
        return prop

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(
            {"props": [asdict(p) for p in self.props.values()]}, indent=2,
            ensure_ascii=False))


def prop_prompt(name: str, description: str = "", style: str = "") -> str:
    """The object alone, whole and plainly lit, for the renderer to keep. The author's
    description comes first: Qwen-Image reads prose, so their words go in as written."""
    description = " ".join(description.split()).rstrip(".")
    return ("A full-colour anime illustration of a single object for a manga: " + name
            + (f". {description}" if description else "")
            + ". The object alone, whole and centred, seen from a three-quarter angle on a "
            "plain light grey background with soft even light. No people, no hands, no "
            "characters, no text. "
            + (f"Style: {style}; clean, detailed object art." if style else
               "Clean line art and cel-shaded colour, detailed object art."))


def design(identity: Path, name: str, *, client=None, seed: int | None = None,
           width: int = 1024, height: int = 1024, description: str | None = None,
           style: dict | None = None, engine: str | None = None) -> Prop:
    """Render a prop's reference image and register it, with the project's render engine
    (``engine``; None = Qwen-Image 2.1, the one that reads a prop's image): Qwen-Image 2.1
    and Z-Anime from the prose prompt, SDXL from tags.

    A prop designed before is redesigned: a new image (a new seed unless one is given),
    the old one kept in ``previous``; ``description`` None keeps the last one."""
    from manganation.config import load_models, load_settings
    from manganation.render import graphs
    from manganation.render.comfy_client import ComfyClient
    from manganation.render.panel import trial_files

    registry = PropRegistry.from_path(identity)
    key = prop_key(name)
    before = registry.props.get(key)
    if description is None:
        description = before.description if before else ""
    if seed is None:
        seed = random.randrange(2**31) if before and before.image else 7
    settings = load_settings()
    if client is None:
        client = ComfyClient(settings.comfyui.base_url)
    engine = engine or "qwen_image_21"
    look = ""
    chosen = None
    if style:
        from manganation.render.panel import load_style

        chosen = load_style(style)
        if chosen["id"] != "default" or chosen["text"]:
            look = chosen["prose"]
    if engine == "sdxl":
        from manganation.characters.generator import _checkpoint_id

        tags = [*(chosen["tags"] if chosen else []), "no humans", "simple background",
                "grey background", "still life", name, description, "highly detailed"]
        prompt = ", ".join(dict.fromkeys(t.strip() for t in tags if t and t.strip()))
        graph = graphs.txt2img(
            ckpt=_checkpoint_id(settings), prompt=prompt,
            negative=("1girl, 1boy, people, person, hands, holding, text, watermark, "
                      "lowres, worst quality"),
            width=width, height=height, seed=seed, prefix="imanganation_prop",
            sampling=graphs.Sampling(steps=28, cfg=6.0))
    else:
        files = trial_files(load_models(), engine)
        prompt = prop_prompt(name, description, look)
        if engine == "qwen_image_21":
            graph = graphs.qwen_image21(
                unet=files["model"], clip=files["text_encoder"], vae=files["vae"],
                prompt=prompt, refs=[], width=width, height=height, seed=seed,
                prefix="imanganation_prop")
        else:
            graph = graphs.z_image(
                unet=files["model"], clip=files["text_encoder"], vae=files["vae"],
                prompt=prompt,
                negative="people, person, hands, characters, text, watermark, lowres",
                width=width, height=height, seed=seed, prefix="imanganation_prop")
    blobs = client.run(graph)
    if not blobs:
        raise RuntimeError("ComfyUI returned no image")
    registry.root.mkdir(parents=True, exist_ok=True)
    image = registry.next_image(key)
    (registry.root / image).write_bytes(blobs[0])
    previous = ([*before.previous, before.image] if before and before.image
                else (before.previous if before else []))
    prop = Prop(key=key, name=" ".join(name.split()), image=image, description=description,
                prompt=prompt, seed=seed,
                previous=[p for p in previous if (registry.root / p).is_file()])
    registry.add(prop)
    return prop
