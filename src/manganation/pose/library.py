"""The pose library (config/poses.yaml) and composing a panel's figures from it.

A character's pose is a reference string:

``stand_hands_on_hips``      a solo pose, fitted to the character's region of the panel
``drag_by_wrist.lead``       one role of a pair pose, which places both figures itself
``…@mirror``                 either of the above, flipped left-right

A pair's roles are filled by the panel's characters, so two references to the same pair
(``drag_by_wrist.lead`` and ``drag_by_wrist.follow``) make one scene."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from manganation.pose.rig import (JOINT_INDEX, Figure, Pose, PoseError, mirror_box,
                                  place, hold)

Box = tuple[float, float, float, float]


def _library_path() -> Path:
    from manganation.config import CONFIG_DIR

    return CONFIG_DIR / "poses.yaml"


@lru_cache(maxsize=1)
def load_library(path: str | None = None) -> dict:
    """{"poses": {id: Pose}, "pairs": {id: {...}}}, validated."""
    source = Path(path) if path else _library_path()
    data = yaml.safe_load(source.read_text()) or {}
    poses = {ident: Pose.from_dict(ident, body or {})
             for ident, body in (data.get("poses") or {}).items()}
    pairs = data.get("pairs") or {}
    for ident, pair in pairs.items():
        roles = pair.get("roles") or {}
        if len(roles) < 2:
            raise PoseError(f"pair {ident!r} needs at least two roles")
        for role, body in roles.items():
            if body.get("pose") not in poses:
                raise PoseError(f"pair {ident!r} role {role!r}: unknown pose "
                                f"{body.get('pose')!r}")
            if len(body.get("box") or ()) != 4:
                raise PoseError(f"pair {ident!r} role {role!r} needs box: [x, y, w, h]")
        for hold_ in pair.get("holds") or []:
            if hold_.get("figure") not in roles or hold_["to"].split(".")[0] not in roles:
                raise PoseError(f"pair {ident!r}: a hold names a role it doesn't have")
    return {"poses": poses, "pairs": pairs}


def catalogue() -> dict[str, str]:
    """Every reference a script can use, with a one-line description."""
    lib = load_library()
    out = {ident: pose.description for ident, pose in lib["poses"].items()}
    for ident, pair in lib["pairs"].items():
        for role in pair["roles"]:
            out[f"{ident}.{role}"] = f"{pair.get('description', '')} [{role}]"
    return out


def parse_reference(ref: str) -> tuple[str, str | None, bool]:
    """'drag_by_wrist.lead@mirror' -> ('drag_by_wrist', 'lead', True)."""
    text = ref.strip().lower()
    mirror = text.endswith("@mirror")
    if mirror:
        text = text[: -len("@mirror")]
    ident, _, role = text.partition(".")
    return ident, (role or None), mirror


def check_reference(ref: str) -> None:
    """Raise PoseError unless ``ref`` names a real pose or pair role."""
    lib = load_library()
    ident, role, _ = parse_reference(ref)
    if role is None:
        if ident not in lib["poses"]:
            raise PoseError(f"unknown pose {ref!r}")
    elif ident not in lib["pairs"] or role not in lib["pairs"][ident]["roles"]:
        raise PoseError(f"unknown pair pose {ref!r}")


def tags_for(ref: str) -> list[str]:
    """The prompt tags that go with a reference (a pair role's are its pose's)."""
    lib = load_library()
    ident, role, _ = parse_reference(ref)
    if role is not None:
        pair = lib["pairs"].get(ident)
        ident = (pair["roles"].get(role) or {}).get("pose", "") if pair else ""
    pose = lib["poses"].get(ident)
    return list(pose.tags) if pose else []


def compose(refs: dict[str, str], boxes: dict[str, Box], width: int, height: int, *,
            camera_pitch: float = 0.0) -> list[Figure]:
    """The figures for a panel. ``refs`` maps character -> reference; ``boxes`` maps
    character -> the canvas box (fractions) their solo pose is fitted into."""
    lib = load_library()
    figures: list[Figure] = []
    pending: dict[str, dict[str, tuple[str, bool]]] = {}
    for name, ref in refs.items():
        ident, role, mirror = parse_reference(ref)
        if role is None:
            if ident not in lib["poses"]:
                raise PoseError(f"{name}: unknown pose {ref!r}")
            if name not in boxes:
                raise PoseError(f"{name} has no region to place a pose in")
            pose = lib["poses"][ident]
            box = boxes[name]
            if mirror:
                pose, box = pose.mirrored(), mirror_box(box)
            figures.append(place(pose, box, width, height, name=name,
                                 camera_pitch=camera_pitch))
        else:
            if ident not in lib["pairs"] or role not in lib["pairs"][ident]["roles"]:
                raise PoseError(f"{name}: unknown pair pose {ref!r}")
            pending.setdefault(ident, {})[role] = (name, mirror)

    for ident, assigned in pending.items():
        pair = lib["pairs"][ident]
        missing = [r for r in pair["roles"] if r not in assigned]
        if missing:
            raise PoseError(f"{ident} needs a character for: {', '.join(missing)}")
        mirror = any(m for _, m in assigned.values())
        placed: dict[str, Figure] = {}
        for role, body in pair["roles"].items():
            pose = lib["poses"][body["pose"]]
            if "yaw" in body:
                pose = Pose(**{**pose.__dict__, "yaw": float(body["yaw"])})
            box = tuple(body["box"])
            if mirror:
                pose, box = pose.mirrored(), mirror_box(box)
            placed[role] = place(pose, box, width, height, name=assigned[role][0],
                                 camera_pitch=camera_pitch)
        for h in pair.get("holds") or []:
            hand = h["hand"] if not mirror else ("l" if h["hand"] == "r" else "r")
            target_role, joint = h["to"].split(".")
            if mirror:
                joint = ("r_" if joint.startswith("l_") else "l_") + joint[2:]
            target = placed[target_role].points[JOINT_INDEX[joint]]
            if target is None:
                raise PoseError(f"{ident}: the held joint {h['to']} isn't visible")
            hold(placed[h["figure"]], hand, target)
        figures.extend(placed.values())
    return figures
