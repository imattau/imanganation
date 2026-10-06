"""Render one panel of a project at a caller-chosen frame size.

The caller (the GIMP plug-in, or the CLI) supplies the target frame in pixels; the
renderer picks the nearest SDXL-friendly resolution with the same aspect ratio, so
the result drops into the frame with minimal cropping. Output goes to
``panels/{seq:03d}.png`` (retakes: ``{seq:03d}_take02.png``…), where ``seq`` is the
panel's 1-based position in panels.json.
"""

from __future__ import annotations

import json
import logging
import random
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from manganation.config import CONFIG_DIR, load_models, load_settings
from manganation.layout.regions import assign_regions
from manganation.render import graphs
from manganation.render.comfy_client import ComfyClient
from manganation.render.staging import Staging, stage
from manganation.script.schema import PanelSpec, ReadingOrder, Script

log = logging.getLogger(__name__)

SDXL_PIXELS = 1024 * 1024
MAX_ASPECT = 3.0  # beyond this SDXL composes badly; the frame mask crops the rest


class RenderError(RuntimeError):
    pass


@dataclass
class RenderResult:
    path: str
    seq: int | None
    seed: int
    width: int
    height: int
    prompt: str
    reference: str | None = None
    panel_id: str | None = None  # container panel id, for inline renders
    references: dict[str, str] = field(default_factory=dict)  # character -> version used
    placements: dict[str, str] = field(default_factory=dict)  # character -> mask file used
    guide: str | None = None  # the take whose composition this render kept (ControlNet)
    warnings: list[str] = field(default_factory=list)  # e.g. a character rendered untagged
    # character -> their own masked prompt (defaults.ipadapter.regional_prompts)
    character_prompts: dict[str, str] = field(default_factory=dict)


def fit_resolution(
    frame_w: float, frame_h: float, *, pixels: int = SDXL_PIXELS, multiple: int = 64,
) -> tuple[int, int]:
    """~1 MP size, in multiples of 64, closest to the frame's aspect ratio.

    Rounding width and height independently drifts the ratio (1.65 -> 1.75), which
    the frame mask then crops, so search the grid for the best ratio instead.
    """
    if frame_w <= 0 or frame_h <= 0:
        raise ValueError("frame must have a positive size")
    aspect = min(max(frame_w / frame_h, 1 / MAX_ASPECT), MAX_ASPECT)
    best: tuple[float, int, int] | None = None
    for w in range(multiple, 4096 + 1, multiple):
        h = max(multiple, round(w / aspect / multiple) * multiple)
        if not 0.85 * pixels <= w * h <= 1.15 * pixels:
            continue
        score = abs(w / h - aspect) / aspect + abs(w * h - pixels) / pixels * 0.1
        if best is None or score < best[0]:
            best = (score, w, h)
    assert best is not None
    return best[1], best[2]


def load_style() -> dict:
    """The single colour style. B&W/screentone is the artist's job in GIMP, so a
    panel's ``color_mode`` is recorded but never changes the render."""
    return yaml.safe_load((CONFIG_DIR / "styles" / "default_color.yaml").read_text())


COUNT_TAG = re.compile(r"^(\d+)(girl|boy|other)s?$")


def head_count(names: list[str], character_tags: dict[str, list[str]] | None) -> list[str]:
    """Danbooru head-count tags for a multi-character panel ("1boy, 1girl", "2girls").

    Each character's tags lead with their own count tag; scattered through the prompt
    ("1girl, silver hair, … 1boy, brown hair, …") they don't add up to "two people",
    so two-shots came out with one figure, three, or a merged pair. Empty unless every
    character has a count tag: a partial count would ask for too few figures."""
    counts: dict[str, int] = {}
    for name in names:
        tags = (character_tags or {}).get(name, [])
        match = COUNT_TAG.match(tags[0].strip()) if tags else None
        if match is None:
            return []
        counts[match.group(2)] = counts.get(match.group(2), 0) + int(match.group(1))
    return [f"{n}{kind}{'s' if n > 1 else ''}"
            for kind, n in sorted(counts.items(), key=lambda kv: ("boy", "girl", "other")
                                  .index(kv[0]))]


def character_group(spec: PanelSpec, name: str, tags: list[str], *,
                    counted: bool = False, pose: list[str] | None = None) -> list[str]:
    """One character's tags with their panel expression (and pose) right after them,
    so each stays with its owner ("Yuki surprised" is a name SDXL can't read, floating
    loose). ``counted``: the head count already said who is in the panel, so drop
    their own."""
    if counted and tags and COUNT_TAG.match(tags[0].strip()):
        tags = tags[1:]
    group = list(tags)
    face = spec.expressions.get(name)
    if face:
        group.append(face if tags else f"{name} {face}")
    return group + list(pose or [])


# "School rooftop — cont.", "Kitchen (CONT'D)": a script's continuation marker, not a place.
_CONTINUED = re.compile(r"\s*[-—–:,]?\s*\(?\b(?:cont(?:'d|inued)?|contd)\.?\)?\s*$",
                        re.IGNORECASE)


def setting(spec: PanelSpec) -> str:
    """Where the panel happens. Its own location wins over the scene heading: panel 3
    moves to the stairwell, and "School rooftop" would put it back on the roof."""
    place = spec.location.strip() or spec.scene_heading.strip()
    return _CONTINUED.sub("", place).strip()


def action_text(spec: PanelSpec) -> str:
    """The action without the shot it often opens with ("Medium shot. Yuki drags…"):
    the camera is already in the prompt, and the repeat only spends tokens."""
    action = spec.action.strip()
    camera = spec.camera.strip().rstrip(".")
    if camera:
        action = re.sub(rf"^{re.escape(camera)}\s*[.:;,-]\s*", "", action, flags=re.IGNORECASE)
    return action


# Script shots -> the Danbooru framing tags NoobAI was trained on ("medium shot" is
# English it reads loosely; "cowboy shot" is a framing it knows). Longest match wins.
SHOT_TAGS = {
    "extreme close-up": "close-up, portrait",
    "close-up": "close-up, portrait",
    "medium shot": "cowboy shot",
    "two-shot": "cowboy shot",
    "reaction shot": "upper body",
    "full shot": "full body",
    "wide shot": "wide shot, full body",
    "establishing shot": "very wide shot, scenery",
    "over-the-shoulder": "over shoulder, from behind",
    "over the shoulder": "over shoulder, from behind",
    "bird's-eye": "from above",
    "high angle": "from above",
    "worm's-eye": "from below",
    "low angle": "from below",
    "dutch angle": "dutch angle",
    "pov": "pov",
}


def shot_tags(camera: str) -> str:
    """Framing tags for a script shot; an unknown shot is passed on as written."""
    low = camera.strip().lower()
    for shot in sorted(SHOT_TAGS, key=len, reverse=True):
        if shot in low:
            return SHOT_TAGS[shot]
    return camera.strip()


def build_prompt(
    spec: PanelSpec, style: dict, character_tags: dict[str, list[str]] | None = None,
    staging: Staging | None = None, *, characters: bool = True,
) -> str:
    """The positive prompt. With ``staging`` (render/staging.py) the action and setting
    are tags; without it, the script's prose. ``characters=False`` leaves out each
    character's own tags, for renders that give them masked prompts of their own
    (``character_prompt``)."""
    parts = [style.get("prompt_prefix", "").strip().rstrip(",")]
    # Without a head count, wide frames tempt SDXL to add a second copy of the figure,
    # and two-shots to drop or duplicate one.
    if len(spec.characters) == 1:
        parts.append("solo")
        # NoobAI draws a lone figure as a girl unless told otherwise: "1boy" in the
        # character's tags wasn't enough (Akira read as 1girl in 8 of 9 solo renders).
        tags = (character_tags or {}).get(spec.characters[0], [])
        if tags and tags[0].strip() == "1boy":
            parts.append("male focus")
    counts = head_count(spec.characters, character_tags) if len(spec.characters) > 1 else []
    parts += counts
    # What happens and where before who: CLIP reads the prompt in 75-token chunks and
    # the later ones pull less, so the action used to trail ~100 tokens of costume
    # and lose (docs/quality/2026-10-05_eval_baseline.md).
    solo = len(spec.characters) == 1
    if staging is None:
        action, place = action_text(spec), setting(spec)
    else:
        # A lone figure's pose is the panel's action, so it goes up front; in a group
        # each pose stays with its owner (below), so the sitter isn't the one standing.
        poses = staging.pose(spec.characters[0]) if solo else []
        action = ", ".join([*poses, *staging.shared])
        place = ", ".join(staging.setting) or setting(spec)
    parts += [shot_tags(spec.camera), action, place]
    # Names mean nothing to SDXL; their registered appearance tags do.
    for name in spec.characters if characters else []:
        parts += character_group(
            spec, name, (character_tags or {}).get(name, []), counted=bool(counts),
            pose=staging.pose(name) if staging is not None and not solo else None)
    parts += [f"{who} {face}" for who, face in spec.expressions.items()
              if who not in spec.characters]
    if spec.flashback:
        parts.append("flashback, soft focus")
    return ", ".join(p.strip() for p in parts if p and p.strip())


def character_prompt(spec: PanelSpec, name: str, tags: list[str],
                     staging: Staging | None = None) -> str:
    """One character's masked prompt: their tags (own count tag kept), face, pose."""
    group = character_group(spec, name, tags,
                            pose=staging.pose(name) if staging is not None else None)
    return ", ".join(group) or name


# Shots as framing instructions: Qwen-Image and Z-Anime read "Shot: close-up." as a
# passing remark and drew full figures (close-up framing 0% / 12% in the model trial).
SHOT_PROSE = {
    "extreme close-up": "Extreme close-up: the face fills the whole frame.",
    "close-up": "Close-up: the head and shoulders fill the frame.",
    "reaction shot": "Reaction shot: framed from the chest up, on the face.",
    "medium shot": "Medium shot: framed from the waist up.",
    "two-shot": "Two-shot: both characters framed together from the thighs up.",
    "full shot": "Full shot: the whole body in frame.",
    "wide shot": "Wide shot: whole figures, small in the frame, with the surroundings "
                 "around them.",
    "establishing shot": "Establishing shot: the place fills the frame; any people are "
                         "small.",
    "over-the-shoulder": "Over-the-shoulder shot: seen past one character's shoulder.",
    "over the shoulder": "Over-the-shoulder shot: seen past one character's shoulder.",
    "low angle": "Low angle: seen from below.",
    "worm's-eye": "Worm's-eye view: seen from the ground, looking up.",
    "high angle": "High angle: seen from above.",
    "bird's-eye": "Bird's-eye view: seen from directly above.",
    "dutch angle": "Dutch angle: the camera tilted.",
    "pov": "Point of view shot: seen through a character's eyes.",
}


def shot_prose(camera: str) -> str:
    low = camera.strip().lower()
    for shot in sorted(SHOT_PROSE, key=len, reverse=True):
        if shot in low:
            return SHOT_PROSE[shot]
    return f"Shot: {camera.strip()}." if camera.strip() else ""


def prose_prompt(spec: PanelSpec, appearances: dict[str, list[str]],
                 refs: dict[str, int] | None = None) -> str:
    """A panel in plain English, for models with an LLM text encoder (Qwen-Image,
    Z-Image): the script's own sentences, each character introduced by name with their
    look, and (``refs``: name -> image number) a pointer to their reference image."""
    count = len(spec.characters)
    who = {0: "no people", 1: "exactly one person", 2: "exactly two people",
           3: "exactly three people"}.get(count, f"exactly {count} people")
    parts = ["A full-colour anime illustration for a single manga panel, clean line art "
             f"and cel shading, showing {who}. No text, no speech bubbles, no panel borders."]
    if spec.camera:
        parts.append(shot_prose(spec.camera))
    place = setting(spec)
    if place:
        parts.append(f"Setting: {place}.")
    for name in spec.characters:
        tags = appearances.get(name, [])
        kind = {"1boy": "a boy", "1girl": "a girl"}.get(tags[0].strip() if tags else "",
                                                       "a person")
        looks = ", ".join(t for t in tags if not COUNT_TAG.match(t.strip()))
        # Qwen copied the reference's whole picture (its pose, neutral face and framing)
        # unless told to take only the identity from it.
        ref = (f" (take only the face, hair and outfit from <image{refs[name]}>; draw a new "
               "pose, expression and framing as this panel describes)"
               if refs and name in refs else "")
        line = f"{name} is {kind}{ref}" + (f": {looks}." if looks else ".")
        face = spec.expressions.get(name)
        parts.append(line + (f" {name}'s expression: {face}." if face else ""))
    if spec.action:
        parts.append(f"What happens: {action_text(spec)}")
    if spec.flashback:
        parts.append("It is a flashback: soft focus, faded colours.")
    return " ".join(parts)


def trial_files(models: dict, group: str) -> dict[str, str]:
    """{part: file} for a models.yaml trials group (model, text_encoder, vae)."""
    entry = models.get("trials", {}).get(group)
    if not entry:
        raise RenderError(f"no trial {group!r} in models.yaml "
                          f"(known: {', '.join(models.get('trials', {})) or 'none'})")
    return {part: e["id"] for part, e in entry.items()}


def _prose_graph(engine: str, spec: PanelSpec, identity: Path, client, models: dict,
                 width: int, height: int, seed: int, prefix: str, style: dict):
    """Graph, prompt and references used, for a trial engine (prose prompt)."""
    names = {n: "" for n in spec.characters}  # appearance only: the action sets the face
    appearances = character_tags(identity, spec.characters, names)
    if engine == "qwen_image_21":
        files = trial_files(models, "qwen_image_21")
        refs = find_references(identity, spec)
        uploaded = [client.upload_image(str(path))["name"] for path in refs.values()]
        prompt = prose_prompt(spec, appearances, {n: i for i, n in enumerate(refs, 1)})
        graph = graphs.qwen_image21(unet=files["model"], clip=files["text_encoder"],
                                    vae=files["vae"], prompt=prompt, refs=uploaded,
                                    width=width, height=height, seed=seed, prefix=prefix)
        return graph, prompt, ",".join(refs) or None
    files = trial_files(models, "z_anime")
    prompt = prose_prompt(spec, appearances)
    negative = (style.get("negative", "") + ", comic, multiple panels, border, "
                "speech bubble, extra people" if spec.characters else style.get("negative", ""))
    graph = graphs.z_image(unet=files["model"], clip=files["text_encoder"], vae=files["vae"],
                           prompt=prompt, negative=negative, width=width, height=height,
                           seed=seed, prefix=prefix)
    return graph, prompt, None


# Danbooru angles that hold for an empty background too ("cowboy shot" doesn't).
ANGLE_TAGS = ("from above", "from below", "dutch angle", "pov", "from behind")


def background_prompt(spec: PanelSpec, style: dict, staging: Staging | None) -> str:
    """The panel's place without anyone in it (a layered render's plate)."""
    place = ", ".join(staging.setting) if staging is not None and staging.setting \
        else setting(spec)
    shot = shot_tags(spec.camera)
    angles = [a for a in ANGLE_TAGS if a in shot]
    parts = [style.get("prompt_prefix", "").strip().rstrip(","), "no humans", "scenery",
             place, *angles]
    return ", ".join(p.strip() for p in parts if p and p.strip())


def background_negative(style: dict) -> str:
    return (style.get("negative", "") + ", 1girl, 1boy, people, person, crowd, "
            "multiple girls, multiple boys")


def build_negative(spec: PanelSpec, style: dict) -> str:
    negative = style.get("negative", "")
    if len(spec.characters) == 1:
        negative += ", multiple views, 2boys, 2girls, multiple boys, multiple girls, clone"
    elif len(spec.characters) > 1:
        # Identity bleed shows up as twins; a lone figure means someone was dropped.
        negative += ", solo, multiple views, clone, twins"
    return negative


def reference_for(project: Path, name: str, spec: PanelSpec | None = None) -> Path | None:
    """Resolve one character's reference image (registry, override, then legacy flat)."""
    # 1. Explicit per-panel override.
    if spec is not None and name in spec.refs:
        ref = Path(spec.refs[name])
        ref = ref if ref.is_absolute() else project / ref
        if ref.exists():
            return ref

    # 2. Character registry (structured img-memory).
    try:
        from manganation.characters.registry import CharacterRegistry

        ref = CharacterRegistry.from_path(project).reference_path(name)
        if ref is not None and ref.exists():
            return ref
    except Exception:  # noqa: BLE001 - registry is best-effort here
        pass

    # 3. Legacy flat layout.
    for ext in ("png", "webp", "jpg"):
        ref = project / "characters" / f"{name.lower()}.{ext}"
        if ref.exists():
            return ref
    return None


def find_reference(project: Path, spec: PanelSpec) -> Path | None:
    """Reference for a single-character panel (multi-character uses find_references)."""
    if len(spec.characters) != 1:
        return None
    return reference_for(project, spec.characters[0], spec)


def find_references(project: Path, spec: PanelSpec) -> dict[str, Path]:
    """Every panel character that has a usable reference image, in script order."""
    out: dict[str, Path] = {}
    for name in spec.characters:
        ref = reference_for(project, name, spec)
        if ref is not None:
            out[name] = ref
    return out


def character_tags(
    project: Path, names: list[str], expressions: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Tags per character from the project's registry (best-effort): their appearance,
    plus their default expression *unless the panel gives one* (the panel wins; Yuki
    grins by default, but "surprised" means surprised). Mannerisms are left out: the
    panel's action decides the pose.

    A character left out of the result renders as a bare name, i.e. as nobody in
    particular; ``_render`` reports that as a warning."""
    try:
        from manganation.characters.registry import CharacterRegistry

        registry = CharacterRegistry.from_path(project)
        out = {}
        for name in names:
            character = registry.get(name)
            if character is not None:
                out[name] = character.appearance.prompt_tags(
                    expression=name not in (expressions or {}))
        return out
    except Exception as exc:  # noqa: BLE001 - no registry yet just means bare names
        log.warning("no character registry in %s (%s): rendering without appearance tags",
                    project, exc)
        return {}


def ipadapter_files(models: dict, adapter: str) -> tuple[str, str]:
    """(adapter file, CLIP-vision file) for a models.yaml ipadapter role.

    The encoder must be the one the adapter was trained with (ViT-H vs ViT-bigG), so
    it is looked up from the adapter's ``encoder`` key rather than chosen separately.
    """
    roles = models["ipadapter"]
    if adapter not in roles:
        raise RenderError(f"unknown IP-Adapter {adapter!r}; models.yaml has {sorted(roles)}")
    entry = roles[adapter]
    return entry["id"], roles[entry.get("encoder", "clip_vision")]["id"]


def output_path(project: Path, seq: int) -> Path:
    panels = project / "panels"
    first = panels / f"{seq:03d}.png"
    if not first.exists():
        return first
    take = 2
    while (panels / f"{seq:03d}_take{take:02d}.png").exists():
        take += 1
    return panels / f"{seq:03d}_take{take:02d}.png"


def render_panel(
    project: Path, seq: int, frame_w: float, frame_h: float, *,
    seed: int | None = None, client: ComfyClient | None = None,
    placements: dict[str, Path] | None = None, guide: Path | None = None,
    guide_strength: float | None = None,
) -> RenderResult:
    """Legacy form: panel ``seq`` of ``project/panels.json``, written to its panels/."""
    project = Path(project)
    try:
        script = Script.from_json((project / "panels.json").read_text())
    except (OSError, ValueError) as exc:
        raise RenderError(f"cannot read {project / 'panels.json'}: {exc}") from exc
    if not 1 <= seq <= len(script.panels):
        raise RenderError(f"panel {seq} out of range (script has {len(script.panels)})")
    return _render(
        script.panels[seq - 1], project, frame_w, frame_h,
        reading_order=script.reading_order, seed=seed, client=client,
        out=output_path(project, seq), seq=seq, placements=placements,
        guide=guide, guide_strength=guide_strength,
    )


def render_inline(
    panel: dict, project_id: str, frame_w: float, frame_h: float, *,
    reading_order: str = "rtl", seed: int | None = None,
    client: ComfyClient | None = None, identity: Path | None = None,
    outputs: Path | None = None, placements: dict[str, Path] | None = None,
    guide: Path | None = None, guide_strength: float | None = None,
) -> RenderResult:
    """Container form: the panel spec travels in the request (docs/engine-api.md).

    Character identity comes from the project's identity store (``identity.py``); each
    character's ``version`` selects that reference version. The image goes to the
    engine's own ``outputs/<project id>/`` cache. The engine never writes into the
    container: the plug-in copies the result in as a take."""
    from manganation.config import outputs_root
    from manganation.identity import identity_root
    from manganation.project_container import panel_to_spec

    spec, versions = panel_to_spec(panel)
    root = identity if identity is not None else identity_root(project_id)
    used: dict[str, str] = {}
    if versions:
        from manganation.characters.registry import CharacterRegistry

        reg = CharacterRegistry.from_path(root)
        for name, version in versions.items():
            ref = reg.reference_path(name, version)
            if ref is None or not ref.exists():
                character = reg.get(name)
                known = [v.id for v in character.versions] if character else []
                raise RenderError(f"{name} has no reference version {version!r} "
                                  f"(known: {', '.join(known) or 'none'})")
            spec.refs[name] = str(ref)  # the per-panel override path in reference_for
            used[name] = version
    out_dir = (outputs if outputs is not None else outputs_root()) / project_id
    out = out_dir / f"{panel.get('id', 'panel')}-{uuid.uuid4().hex[:12]}.png"
    result = _render(spec, root, frame_w, frame_h, reading_order=ReadingOrder(reading_order),
                     seed=seed, client=client, out=out, seq=None, placements=placements,
                     guide=guide, guide_strength=guide_strength)
    result.panel_id = panel.get("id")
    result.references = {**{n: "active" for n in spec.characters}, **used}
    out.with_suffix(".json").write_text(json.dumps(asdict(result), indent=2))
    return result


def _check_placements(spec: PanelSpec, placements: dict | None) -> dict[str, Path]:
    """Placement names -> the panel's character names; reject strangers and empties."""
    if not placements:
        return {}
    by_lower = {c.lower(): c for c in spec.characters}
    out: dict[str, Path] = {}
    for name, path in placements.items():
        canonical = by_lower.get(str(name).lower())
        if canonical is None:
            raise RenderError(f"placement for {name!r}, who isn't in this panel "
                              f"({', '.join(spec.characters) or 'no characters'})")
        from manganation.render.inpaint import normalized_mask

        path = Path(path)
        if not path.is_file():
            raise RenderError(f"placement mask for {canonical} not found: {path}")
        if normalized_mask(path).getbbox() is None:
            raise RenderError(f"placement mask for {canonical} is empty: {path.name}")
        out[canonical] = path
    return out


def _upload_placement(client, path: Path, width: int, height: int):
    """Normalise a placement mask to the canvas; -> (uploaded name, fractional box)."""
    import tempfile

    from PIL import Image

    from manganation.render.inpaint import normalized_mask

    mask = normalized_mask(path).resize((width, height), Image.LANCZOS)
    x0, y0, x1, y1 = mask.getbbox() or (0, 0, width, height)
    with tempfile.TemporaryDirectory() as tmp:
        canvas = Path(tmp) / f"placement-{path.stem}.png"
        mask.save(canvas)
        name = client.upload_image(str(canvas))["name"]
    return name, (x0 / width, y0 / height, (x1 - x0) / width, (y1 - y0) / height)


def controlnet_file(models: dict, role: str) -> str:
    """The file for a models.yaml ``controlnets`` role."""
    roles = models.get("controlnets", {})
    if role not in roles:
        raise RenderError(f"unknown ControlNet {role!r}; models.yaml has {sorted(roles)}")
    return roles[role]["id"]


def _with_guide(graph, client, settings, models, guide: Path, width: int, height: int,
                strength: float | None):
    """Resize the guide take to the canvas, upload it, and add the ControlNet."""
    import tempfile

    from PIL import Image

    if not guide.is_file():
        raise RenderError(f"guide image not found: {guide}")
    c = settings.defaults.controlnet
    with Image.open(guide) as im:
        canvas = im.convert("RGB").resize((width, height), Image.LANCZOS)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"guide-{guide.stem}.png"
        canvas.save(path)
        name = client.upload_image(str(path))["name"]
    return graphs.with_controlnet(
        graph, image=name, controlnet=controlnet_file(models, c.model),
        strength=c.strength if strength is None else strength, start=c.start, end=c.end,
        low_threshold=c.low_threshold, high_threshold=c.high_threshold)


def _render(
    spec: PanelSpec, identity: Path, frame_w: float, frame_h: float, *,
    reading_order: ReadingOrder, seed: int | None, client: ComfyClient | None,
    out: Path, seq: int | None, placements: dict[str, Path] | None = None,
    guide: Path | None = None, guide_strength: float | None = None,
    init: Path | None = None, init_denoise: float = 0.45,
) -> RenderResult:
    """Render ``spec`` with characters from ``identity`` (a character registry folder).

    ``init`` starts the SDXL render from an existing image at ``init_denoise`` instead
    of noise: a second stage that restyles another model's composition.

    ``placements`` maps characters to mask images (the artist's placement layers, any
    size with the frame's proportions; white or opaque = this character). They replace
    the default reading-order bands as each character's regional reference mask.

    ``guide`` is an existing take whose composition (layout, poses) the render keeps:
    its edges steer the early steps through ControlNet, while the prompt (an edited
    expression, outfit, …) decides the details."""
    placements = _check_placements(spec, placements)
    settings = load_settings()
    models = load_models()
    client = client or ComfyClient(settings.comfyui.base_url)
    if not client.is_up():
        raise RenderError(f"ComfyUI is not reachable at {settings.comfyui.base_url}")

    style = load_style()
    warnings: list[str] = []
    staging = None
    engine = settings.defaults.renderer.engine
    if engine != "sdxl":  # trial engines read the script's prose; see _prose_graph
        pass
    elif settings.defaults.staging.enabled and (spec.action or spec.characters):
        try:
            staging = stage(spec, setting(spec), settings=settings)
        except Exception as exc:  # noqa: BLE001 - any LLM trouble: the prose still works
            warnings.append(f"action and setting used as written, not as tags ({exc})")
    if staging is not None:
        # The panel's own faces win; the LLM's fill the rest (and so replace a default
        # grin with the sigh the action asks for).
        faces = {n: ", ".join(staging.expression(n)) for n in spec.characters
                 if staging.expression(n)}
        spec = spec.model_copy(update={"expressions": {**faces, **spec.expressions}})
    tags_by_char = character_tags(identity, spec.characters, spec.expressions)
    warnings += [f"{name} has no registered appearance: rendered from the name alone, so "
                "they won't look like their character"
                for name in spec.characters if not tags_by_char.get(name)]
    for warning in warnings:
        log.warning("%s (identity %s)", warning, identity)
    ipa = settings.defaults.ipadapter
    width, height = fit_resolution(frame_w, frame_h)
    layered = settings.defaults.layered.mode if len(spec.characters) > 1 else "off"
    masked = len(spec.characters) > 1 and (
        ipa.regional_prompts == "always" or layered != "off"
        or (ipa.regional_prompts == "wide" and width > height))
    prompt = build_prompt(spec, style, tags_by_char, staging, characters=not masked)
    seed = seed if seed is not None else (spec.seed if spec.seed is not None
                                          else random.randrange(2**32))
    d = settings.defaults.panel

    prefix = f"imanganation_{seq:03d}" if seq else "imanganation_inline"
    checkpoint = models["checkpoints"]["primary"]["id"]
    if settings.defaults.renderer.checkpoint:  # an SDXL trial, e.g. illustrious_v2
        checkpoint = trial_files(models, settings.defaults.renderer.checkpoint)["model"]
    graph = graphs.txt2img(
        ckpt=checkpoint, prompt=prompt,
        negative=build_negative(spec, style), width=width, height=height, seed=seed,
        prefix=f"imanganation_{seq:03d}" if seq else "imanganation_inline",
        sampling=graphs.Sampling(d.steps, d.cfg, d.sampler, d.scheduler),
    )
    refs = find_references(identity, spec)
    ref_used: str | None = None
    own_texts: dict[str, str] = {}
    if engine != "sdxl":
        graph, prompt, ref_used = _prose_graph(engine, spec, identity, client, models,
                                               width, height, seed, prefix, style)
    ipa_file, clip_file = ipadapter_files(models, ipa.adapter)
    if engine != "sdxl":
        pass  # the SDXL reference and region steps below don't apply
    elif len(refs) == 1 and len(spec.characters) == 1:
        name, ref = next(iter(refs.items()))
        uploaded = client.upload_image(str(ref))
        graph = graphs.with_ipadapter(
            graph, ref_image=uploaded["name"], ipadapter=ipa_file,
            clip_vision=clip_file, weight=ipa.weight_single,
        )
        ref_used = str(ref)
    elif len(spec.characters) > 1:
        # Multi-character: lay out *every* character in reading order, so each keeps
        # their band whether or not the others have a reference (laid out over only
        # the referenced ones, a lone reference got the whole canvas and pulled every
        # face). Each reference stays masked to its character's region, even when
        # only one character has one.
        regions = assign_regions(len(spec.characters), order=reading_order, margin=0.05)
        references = []
        region_text = []
        own_prompts: list[dict] = []
        for name, region in zip(spec.characters, regions, strict=True):
            box = (region.x, region.y, region.w, region.h)
            mask_name = None
            if name in placements and (name in refs or ipa.regional_text > 0 or masked):
                mask_name, box = _upload_placement(client, placements[name], width, height)
            if name in refs:
                uploaded = client.upload_image(str(refs[name]))
                entry = {"image": uploaded["name"], "mask": list(region.scaled(width, height)),
                         "canvas_w": width, "canvas_h": height,
                         "weight": ipa.weight_regional, "feather": ipa.feather}
                if mask_name:
                    entry["mask_image"] = mask_name
                references.append(entry)
            group = character_group(spec, name, tags_by_char.get(name, []))
            region_text.append({"text": ", ".join(group) or name, "box": box})
            if masked:
                own = {"text": character_prompt(spec, name, tags_by_char.get(name, []),
                                                 staging),
                       "mask": list(region.scaled(width, height)), "canvas_w": width,
                       "canvas_h": height, "feather": ipa.feather}
                if mask_name:
                    own["mask_image"] = mask_name
                own_prompts.append(own)
        # Optional per-character text bands (off by default: they split two-shots into
        # side-by-side pictures; the regional references hold each face instead).
        if ipa.regional_text > 0:
            graph = graphs.with_regional_conditioning(
                graph, regions=[{**r, "strength": ipa.regional_text} for r in region_text])
        if own_prompts:
            graph = graphs.with_masked_prompts(graph, regions=own_prompts)
            own_texts = {name: own["text"]
                         for name, own in zip(spec.characters, own_prompts, strict=True)}
        if references:
            graph = graphs.with_regional_ipadapter(
                graph, references=references, ipadapter=ipa_file, clip_vision=clip_file,
                force_regional=True,
            )
            ref_used = ",".join(n for n in spec.characters if n in refs)
        areas = [{k: own[k] for k in ("mask", "canvas_w", "canvas_h", "feather",
                                      "mask_image") if k in own} for own in own_prompts]
        if layered == "per_character":
            # One pass each: the character as a solo panel, painted in their own area.
            passes = []
            for name, area in zip(spec.characters, areas, strict=True):
                alone = spec.model_copy(update={
                    "characters": [name],
                    "expressions": {n: f for n, f in spec.expressions.items() if n == name}})
                text = build_prompt(alone, style, {name: tags_by_char.get(name, [])},
                                    staging)
                ref = next((e["image"] for e, n in zip(
                    references, [c for c in spec.characters if c in refs], strict=True)
                    if n == name), None)
                passes.append({**area, "prompt": text, "ref": ref,
                               "weight": ipa.weight_single})
                own_texts[name] = text
            graph = graphs.layered_per_character(
                ckpt=models["checkpoints"]["primary"]["id"],
                background=background_prompt(spec, style, staging),
                background_negative=background_negative(style),
                negative=build_negative(spec.model_copy(update={"characters": ["x"]}), style),
                width=width, height=height, seed=seed,
                prefix=f"imanganation_{seq:03d}" if seq else "imanganation_inline",
                passes=passes, ipadapter=ipa_file, clip_vision=clip_file,
                sampling=graphs.Sampling(d.steps, d.cfg, d.sampler, d.scheduler),
                denoise=settings.defaults.layered.denoise)

    if guide is not None:
        graph = _with_guide(graph, client, settings, models, Path(guide), width, height,
                            guide_strength)
    if init is not None and engine == "sdxl":
        graph = graphs.with_init_image(graph, image=client.upload_image(str(init))["name"],
                                       width=width, height=height, denoise=init_denoise)

    blobs = client.run(graph)
    if not blobs:
        raise RenderError("ComfyUI returned no image")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blobs[0])

    result = RenderResult(
        path=str(out), seq=seq, seed=seed, width=width, height=height,
        prompt=prompt, reference=ref_used,
        placements={n: str(p) for n, p in placements.items()},
        guide=str(guide) if guide is not None else None, warnings=warnings,
        character_prompts=own_texts,
    )
    out.with_suffix(".json").write_text(json.dumps(asdict(result), indent=2))
    return result
