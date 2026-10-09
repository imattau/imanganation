"""Locations as references, like characters.

Each panel happens somewhere ("School rooftop — late afternoon", "the stairwell"). A
location gets one establishing image (no people), designed once and then given to the
renderer as a reference (Qwen-Image 2.1 takes up to 10), so every panel set there
shows the same place: the fence, the city behind it, the light. Without it two-shots
came out on near-empty backgrounds ("rooftop" 0% in the model trial).

Stored beside the characters in the identity folder: ``locations/locations.json`` and
``locations/<slug>.png``. The author can also design one place at a time from their own
description (the plug-in's Design location), like a character; a redesign writes a new
image and keeps the earlier ones listed in ``previous``.
"""

from __future__ import annotations

import json
import random
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_CONTINUED = re.compile(r"\s*[-—–:,]?\s*\(?\b(?:cont(?:'d|inued)?|contd)\.?\)?\s*$",
                        re.IGNORECASE)
_SPLIT = re.compile(r"\s+[—–-]\s+|\s*[,;(]\s*")


def location_key(place: str) -> str:
    """The place without its time or a continuation marker: "School rooftop — late
    afternoon" and "School rooftop — cont." are both ``school rooftop``."""
    place = _CONTINUED.sub("", place.strip())
    head = _SPLIT.split(place, maxsplit=1)[0]
    head = re.sub(r"^(?:the|a|an)\s+", "", head.strip(), flags=re.IGNORECASE)
    return " ".join(head.lower().split())


def slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", key).strip("-") or "location"


@dataclass
class Location:
    key: str
    name: str  # as the script first wrote it, e.g. "School rooftop — late afternoon"
    image: str  # relative to the locations folder
    details: list[str] = field(default_factory=list)  # setting tags from the script
    prompt: str = ""
    seed: int | None = None
    description: str = ""  # the author's words for the place (the project's notes)
    previous: list[str] = field(default_factory=list)  # earlier images, oldest first
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


class LocationRegistry:
    def __init__(self, root: Path):
        self.root = Path(root) / "locations"
        self.file = self.root / "locations.json"
        data = json.loads(self.file.read_text()) if self.file.is_file() else {}
        self.locations = {d["key"]: Location(**d) for d in data.get("locations", [])}

    @classmethod
    def from_path(cls, identity: Path) -> LocationRegistry:
        return cls(identity)

    def get(self, place: str) -> Location | None:
        return self.locations.get(location_key(place))

    def reference_path(self, place: str) -> Path | None:
        loc = self.get(place)
        path = self.root / loc.image if loc else None
        return path if path is not None and path.is_file() else None

    def add(self, location: Location) -> None:
        self.locations[location.key] = location
        self.save()

    def remove(self, place: str) -> tuple[Location, Path | None] | None:
        """Drop a location; its images move to ``locations/.deleted/<slug>-<time>/``
        rather than being erased. -> (location, folder they moved to) or None."""
        location = self.locations.pop(location_key(place), None)
        if location is None:
            return None
        images = [self.root / name for name in (*location.previous, location.image)]
        images = [path for path in images if path.is_file()]
        moved = None
        if images:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            moved = self.root / ".deleted" / f"{slug(location.key)}-{stamp}"
            moved.mkdir(parents=True, exist_ok=True)
            for path in images:
                shutil.move(str(path), moved / path.name)
        self.save()
        return location, moved

    def set_reference(self, name: str, image: Path) -> Location:
        """Make a picture (the author's, e.g. painted over in GIMP) a place's reference:
        a copy beside the designed ones, the earlier image kept in ``previous``. A place
        with no record yet gets one, named as given."""
        key = location_key(name)
        before = self.locations.get(key)
        self.root.mkdir(parents=True, exist_ok=True)
        stored = self.next_image(key)
        shutil.copyfile(image, self.root / stored)
        if before is None:
            location = Location(key=key, name=" ".join(name.split()), image=stored)
        else:
            previous = [*before.previous, before.image]
            location = Location(**{**asdict(before), "image": stored, "prompt": "",
                                   "seed": None,
                                   "previous": [p for p in previous
                                                if (self.root / p).is_file()],
                                   "created_at": datetime.now(UTC).isoformat(
                                       timespec="seconds")})
        self.add(location)
        return location

    def next_image(self, key: str) -> str:
        """A file name no image of this location uses yet: <slug>.png, <slug>-2.png…"""
        base, n = slug(key), 1
        name = f"{base}.png"
        while (self.root / name).exists():
            n += 1
            name = f"{base}-{n}.png"
        return name

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(
            {"locations": [asdict(loc) for loc in self.locations.values()]}, indent=2,
            ensure_ascii=False))


def location_prompt(name: str, details: list[str], description: str = "",
                    style: str = "") -> str:
    """An establishing view of the place, empty, for the renderer to keep. The author's
    description comes first: Qwen-Image reads prose, so their words go in as written."""
    extra = ", ".join(d for d in details if d not in ("indoors", "outdoors"))
    inside = "indoors" in details and "outdoors" not in details
    description = " ".join(description.split()).rstrip(".")
    # The author's words say whether it is inside; without tags, don't contradict them
    kind = ("An interior" if inside else "An outdoor scene"
            if "outdoors" in details or not description else "The place")
    return ("A full-colour anime background illustration for a manga: an establishing "
            f"view of {name}" + (f". {description}" if description else "")
            + (f", with {extra}" if extra and not description else "")
            + (f". Also shown: {extra}" if extra and description else "") + ". "
            f"{kind} seen at eye level, wide "
            "enough to show the whole place and its surroundings. No people, no "
            "characters, no text. "
            + (f"Style: {style}; detailed background art." if style else
               "Clean line art and cel-shaded colour, detailed background art."))


def gather(panels, settings=None) -> dict[str, dict]:
    """Every location the script's panels use: key -> {"name", "details"}, details
    being the staging LLM's setting tags over all panels there (cached)."""
    from manganation.render.panel import setting
    from manganation.render.staging import stage

    found: dict[str, dict] = {}
    for spec in panels:
        place = setting(spec)
        if not place:
            continue
        entry = found.setdefault(location_key(place), {"name": place, "details": []})
        try:
            tags = stage(spec, place, settings=settings).setting
        except Exception:  # noqa: BLE001 - no LLM: the name alone still works
            tags = []
        entry["details"] += [t for t in tags if t not in entry["details"]]
    return found


def design(identity: Path, key: str, name: str, details: list[str], *, client=None,
           seed: int | None = None, width: int = 1344, height: int = 768,
           description: str | None = None, style: dict | None = None,
           engine: str | None = None) -> Location:
    """Render a location's reference image (no people) and register it, with the
    project's render engine (``engine``; None = Qwen-Image 2.1, the one that reads a
    place's image): Qwen-Image 2.1 and
    Z-Anime from the prose prompt, SDXL from tags.

    A place designed before is redesigned: a new image (a new seed unless one is given),
    the old one kept in ``previous``; ``description`` None keeps the last one, and the
    setting tags gathered before are kept when none are given. ``style`` is the
    project's look (styles.py)."""
    from manganation.config import load_models, load_settings
    from manganation.render import graphs
    from manganation.render.comfy_client import ComfyClient
    from manganation.render.panel import trial_files

    registry = LocationRegistry.from_path(identity)
    before = registry.locations.get(key)
    if description is None:
        description = before.description if before else ""
    if not details and before:
        details = before.details
    if seed is None:
        seed = random.randrange(2**31) if before else 7
    settings = load_settings()
    if client is None:
        client = ComfyClient(settings.comfyui.base_url)
    engine = engine or "qwen_image_21"  # only its renders read a place's image
    look = ""
    chosen = None
    if style:
        from manganation.render.panel import load_style

        chosen = load_style(style)
        if chosen["id"] != "default" or chosen["text"]:  # the default keeps its words
            look = chosen["prose"]
    if engine == "sdxl":
        from manganation.characters.generator import _checkpoint_id

        tags = [*(chosen["tags"] if chosen else []), "no humans", "scenery", name, *details,
                "wide shot", "highly detailed"]
        prompt = ", ".join(dict.fromkeys(t.strip() for t in tags if t and t.strip()))
        graph = graphs.txt2img(
            ckpt=_checkpoint_id(settings), prompt=prompt,
            negative=("1girl, 1boy, people, person, crowd, multiple girls, multiple boys, "
                      "text, watermark, lowres, worst quality"),
            width=width, height=height, seed=seed, prefix="imanganation_location",
            sampling=graphs.Sampling(steps=28, cfg=6.0))
    else:
        files = trial_files(load_models(), engine)
        prompt = location_prompt(name, details, description, look)
        if engine == "qwen_image_21":
            graph = graphs.qwen_image21(
                unet=files["model"], clip=files["text_encoder"], vae=files["vae"],
                prompt=prompt, refs=[], width=width, height=height, seed=seed,
                prefix="imanganation_location")
        else:
            graph = graphs.z_image(
                unet=files["model"], clip=files["text_encoder"], vae=files["vae"],
                prompt=prompt, negative="people, person, characters, text, watermark, lowres",
                width=width, height=height, seed=seed, prefix="imanganation_location")
    blobs = client.run(graph)
    if not blobs:
        raise RuntimeError("ComfyUI returned no image")
    registry.root.mkdir(parents=True, exist_ok=True)
    image = registry.next_image(key)
    (registry.root / image).write_bytes(blobs[0])
    previous = [*before.previous, before.image] if before else []
    location = Location(key=key, name=name, image=image, details=details, prompt=prompt,
                        seed=seed, description=description,
                        previous=[p for p in previous if (registry.root / p).is_file()])
    registry.add(location)
    return location
