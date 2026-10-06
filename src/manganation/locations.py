"""Locations as references, like characters.

Each panel happens somewhere ("School rooftop — late afternoon", "the stairwell"). A
location gets one establishing image (no people), designed once and then given to the
renderer as a reference (Qwen-Image 2.1 takes up to 10), so every panel set there
shows the same place: the fence, the city behind it, the light. Without it two-shots
came out on near-empty backgrounds ("rooftop" 0% in the model trial).

Stored beside the characters in the identity folder: ``locations/locations.json`` and
``locations/<slug>.png``.
"""

from __future__ import annotations

import json
import re
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

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(
            {"locations": [asdict(loc) for loc in self.locations.values()]}, indent=2,
            ensure_ascii=False))


def location_prompt(name: str, details: list[str]) -> str:
    """An establishing view of the place, empty, for the renderer to keep."""
    extra = ", ".join(d for d in details if d not in ("indoors", "outdoors"))
    inside = "indoors" in details and "outdoors" not in details
    return ("A full-colour anime background illustration for a manga: an establishing "
            f"view of {name}" + (f", with {extra}" if extra else "") + ". "
            f"{'An interior' if inside else 'An outdoor scene'} seen at eye level, wide "
            "enough to show the whole place and its surroundings. No people, no "
            "characters, no text. Clean line art and cel-shaded colour, detailed "
            "background art.")


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
           seed: int = 7, width: int = 1344, height: int = 768) -> Location:
    """Render a location's reference image (Qwen-Image 2.1, no people) and register it."""
    from manganation.config import load_models, load_settings
    from manganation.render import graphs
    from manganation.render.comfy_client import ComfyClient
    from manganation.render.panel import trial_files

    settings = load_settings()
    client = client or ComfyClient(settings.comfyui.base_url)
    files = trial_files(load_models(), "qwen_image_21")
    prompt = location_prompt(name, details)
    graph = graphs.qwen_image21(unet=files["model"], clip=files["text_encoder"],
                                vae=files["vae"], prompt=prompt, refs=[], width=width,
                                height=height, seed=seed, prefix="imanganation_location")
    blobs = client.run(graph)
    if not blobs:
        raise RuntimeError("ComfyUI returned no image")
    registry = LocationRegistry.from_path(identity)
    registry.root.mkdir(parents=True, exist_ok=True)
    image = f"{slug(key)}.png"
    (registry.root / image).write_bytes(blobs[0])
    location = Location(key=key, name=name, image=image, details=details, prompt=prompt,
                        seed=seed)
    registry.add(location)
    return location
