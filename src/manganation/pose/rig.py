"""A small 3D figure rig that projects to OpenPose body-18 keypoints.

A pose is a handful of angles, so one definition serves every camera angle, facing and
mirroring. Units: body height = 1, y up, z toward the camera, x toward the figure's
*left* (which a figure facing the camera shows on the viewer's right).

Limb segments are given as ``[a, b]`` in degrees, absolute in the body frame:
``a`` from straight down (0 hanging, 90 horizontal, 180 straight up) and ``b`` the
direction around the vertical (0 forward, 90 outward, -90 across the body, 180 back).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# OpenPose body-18 order
(NOSE, NECK, R_SHO, R_ELB, R_WRI, L_SHO, L_ELB, L_WRI, R_HIP, R_KNEE, R_ANK, L_HIP,
 L_KNEE, L_ANK, R_EYE, L_EYE, R_EAR, L_EAR) = range(18)
JOINTS = ("nose", "neck", "r_sho", "r_elb", "r_wri", "l_sho", "l_elb", "l_wri", "r_hip",
          "r_knee", "r_ank", "l_hip", "l_knee", "l_ank", "r_eye", "l_eye", "r_ear", "l_ear")
JOINT_INDEX = {name: i for i, name in enumerate(JOINTS)}

TORSO, HIP_W, SHO_W = 0.30, 0.055, 0.095
UPPER_ARM, FOREARM, THIGH, SHIN = 0.17, 0.15, 0.245, 0.245
HEAD_RISE = 0.085  # neck to the middle of the head

Point = tuple[float, float, float]


class PoseError(ValueError):
    pass


@dataclass
class Pose:
    """One figure's pose. Angle fields default to a relaxed standing figure."""

    id: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    yaw: float = 0.0  # facing: 0 the camera, 90 screen-right, -90 screen-left, 180 away
    lean: float = 0.0  # torso forward lean
    sway: float = 0.0  # torso lean toward the figure's left
    twist: float = 0.0  # shoulders turned about the spine
    head_yaw: float = 0.0
    head_pitch: float = 0.0  # nod down
    l_arm: tuple = ((10, 80), (5, 80))
    r_arm: tuple = ((10, 80), (5, 80))
    l_leg: tuple = ((3, 90), (0, 0))
    r_leg: tuple = ((3, 90), (0, 0))
    anchor: str = "bottom"  # which edge of its box the figure rests on: bottom | center

    @classmethod
    def from_dict(cls, ident: str, data: dict) -> Pose:
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise PoseError(f"pose {ident!r}: unknown fields {sorted(unknown)}")
        pose = cls(id=ident, **{k: v for k, v in data.items()})
        for side in ("l_arm", "r_arm", "l_leg", "r_leg"):
            seg = getattr(pose, side)
            try:
                (a, b), (c, d) = seg
                setattr(pose, side, ((float(a), float(b)), (float(c), float(d))))
            except (TypeError, ValueError) as exc:
                raise PoseError(f"pose {ident!r}: {side} must be [[a, b], [a, b]]") from exc
        if pose.anchor not in ("bottom", "center"):
            raise PoseError(f"pose {ident!r}: anchor must be bottom or center")
        return pose

    def mirrored(self) -> Pose:
        """The same pose seen in a mirror: left and right swap, turns reverse."""
        flip = lambda seg: tuple((a, -b) for a, b in seg)  # noqa: E731
        return Pose(
            id=self.id, description=self.description, tags=list(self.tags),
            yaw=-self.yaw, lean=self.lean, sway=-self.sway, twist=-self.twist,
            head_yaw=-self.head_yaw, head_pitch=self.head_pitch,
            l_arm=flip(self.r_arm), r_arm=flip(self.l_arm),
            l_leg=flip(self.r_leg), r_leg=flip(self.l_leg), anchor=self.anchor)


def _rad(deg: float) -> float:
    return math.radians(deg)


def _add(p: Point, q: Point, k: float = 1.0) -> Point:
    return (p[0] + k * q[0], p[1] + k * q[1], p[2] + k * q[2])


def _segment(a: float, b: float, side: int) -> Point:
    """Unit direction for a limb segment; ``side`` is +1 for left, -1 for right."""
    return (math.sin(_rad(a)) * math.sin(_rad(b)) * side, -math.cos(_rad(a)),
            math.sin(_rad(a)) * math.cos(_rad(b)))


def _norm(v: Point) -> Point:
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _rotate_about(v: Point, axis: Point, deg: float) -> Point:
    k, t = _norm(axis), _rad(deg)
    c, s = math.cos(t), math.sin(t)
    dot = k[0] * v[0] + k[1] * v[1] + k[2] * v[2]
    cross = (k[1] * v[2] - k[2] * v[1], k[2] * v[0] - k[0] * v[2], k[0] * v[1] - k[1] * v[0])
    return tuple(v[i] * c + cross[i] * s + k[i] * dot * (1 - c) for i in range(3))  # type: ignore[return-value]


def _yaw(p: Point, deg: float) -> Point:
    t = _rad(deg)
    return (p[0] * math.cos(t) + p[2] * math.sin(t), p[1],
            -p[0] * math.sin(t) + p[2] * math.cos(t))


def skeleton(pose: Pose, camera_pitch: float = 0.0) -> list[Point | None]:
    """The 18 keypoints as (x, y, depth) with y DOWN, in body-height units, the pelvis
    at the origin. A joint the camera couldn't see (a face turned away) is None."""
    spine = _norm((math.sin(_rad(pose.sway)), math.cos(_rad(pose.lean)),
                   math.sin(_rad(pose.lean))))
    pelvis: Point = (0.0, 0.0, 0.0)
    neck = _add(pelvis, spine, TORSO)
    across = _rotate_about((1.0, 0.0, 0.0), spine, pose.twist)
    pts: dict[int, Point] = {NECK: neck}
    pts[L_SHO], pts[R_SHO] = _add(neck, across, SHO_W), _add(neck, across, -SHO_W)
    pts[L_HIP], pts[R_HIP] = (HIP_W, 0.0, 0.0), (-HIP_W, 0.0, 0.0)
    for (sho, elb, wri), seg, side in (((L_SHO, L_ELB, L_WRI), pose.l_arm, 1),
                                        ((R_SHO, R_ELB, R_WRI), pose.r_arm, -1)):
        pts[elb] = _add(pts[sho], _segment(*seg[0], side), UPPER_ARM)
        pts[wri] = _add(pts[elb], _segment(*seg[1], side), FOREARM)
    for (hip, knee, ank), seg, side in (((L_HIP, L_KNEE, L_ANK), pose.l_leg, 1),
                                         ((R_HIP, R_KNEE, R_ANK), pose.r_leg, -1)):
        pts[knee] = _add(pts[hip], _segment(*seg[0], side), THIGH)
        pts[ank] = _add(pts[knee], _segment(*seg[1], side), SHIN)

    head = _add(neck, spine, HEAD_RISE)
    face = _rotate_about((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), pose.head_pitch)
    face = _rotate_about(face, (0.0, 1.0, 0.0), pose.head_yaw)
    side_v = _rotate_about((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), pose.head_yaw)
    up = (0.0, 1.0, 0.0)
    pts[NOSE] = _add(_add(head, face, 0.045), up, -0.005)
    for idx, k in ((L_EYE, 1), (R_EYE, -1)):
        pts[idx] = _add(_add(_add(head, face, 0.038), side_v, 0.03 * k), up, 0.014)
    for idx, k in ((L_EAR, 1), (R_EAR, -1)):
        pts[idx] = _add(_add(head, side_v, 0.07 * k), face, -0.004)

    out: list[Point | None] = []
    facing = _yaw(face, pose.yaw)
    for i in range(18):
        x, y, z = _yaw(pts[i] if i in pts else (0.0, 0.0, 0.0), pose.yaw)
        c, s = math.cos(_rad(camera_pitch)), math.sin(_rad(camera_pitch))
        up_v, depth = y * c - z * s, y * s + z * c
        hidden = i in (NOSE, L_EYE, R_EYE) and facing[2] < -0.25
        out.append(None if hidden else (x, -up_v, depth))
    return out


def bounds(points: list[Point | None]) -> tuple[float, float, float, float]:
    """(min x, min y, max x, max y) of the figure, head top included."""
    xs = [p[0] for p in points if p]
    ys = [p[1] for p in points if p]
    top = min(p[1] for p in (points[NOSE], points[NECK], points[L_EYE], points[R_EYE],
                              points[L_EAR], points[R_EAR]) if p)
    return min(xs), min(ys + [top - 0.07]), max(xs), max(ys)


# ---- placing, holding hands ------------------------------------------------------------

@dataclass
class Figure:
    name: str
    points: list[Point | None]  # canvas pixels (x, y, depth in the same unit)


def place(pose: Pose, box: tuple[float, float, float, float], width: int, height: int,
          *, name: str = "", camera_pitch: float = 0.0) -> Figure:
    """Fit ``pose`` inside ``box`` (fractions of the canvas: x, y, w, h), keeping its
    proportions, resting on the box's bottom edge (or centred, for ``anchor: center``)."""
    raw = skeleton(pose, camera_pitch)
    x0, y0, x1, y1 = bounds(raw)
    bx, by, bw, bh = box
    scale = min(bw * width / max(x1 - x0, 1e-6), bh * height / max(y1 - y0, 1e-6))
    ox = bx * width + (bw * width - (x1 - x0) * scale) / 2 - x0 * scale
    if pose.anchor == "center":
        oy = by * height + (bh * height - (y1 - y0) * scale) / 2 - y0 * scale
    else:
        oy = (by + bh) * height - y1 * scale
    return Figure(name, [None if p is None else (p[0] * scale + ox, p[1] * scale + oy,
                                                 p[2] * scale) for p in raw])


def hold(figure: Figure, hand: str, target: Point) -> bool:
    """Bend ``figure``'s arm so its ``hand`` ('l' or 'r') wrist is on ``target``.

    Two-bone IK that keeps the segment lengths and bends the elbow the way it already
    bent. A target out of reach is brought into reach by moving the whole figure
    toward it (the arm stretched straight), so the hands always meet. Returns True when
    the figure had to move."""
    sho, elb, wri = ((L_SHO, L_ELB, L_WRI) if hand == "l" else (R_SHO, R_ELB, R_WRI))
    s, e, w = figure.points[sho], figure.points[elb], figure.points[wri]
    if s is None or e is None or w is None:
        raise PoseError("that figure has no arm to place")
    l1, l2 = math.dist(s, e), math.dist(e, w)
    reach = (l1 + l2) * 0.98
    gap = math.dist(s, target)
    moved = gap > reach
    if moved:
        k = (gap - reach) / gap
        shift = tuple((target[i] - s[i]) * k for i in range(3))
        figure.points = [None if p is None else (p[0] + shift[0], p[1] + shift[1],
                                                 p[2] + shift[2]) for p in figure.points]
        s, e = figure.points[sho], figure.points[elb]
    vec = (target[0] - s[0], target[1] - s[1], target[2] - s[2])
    dist = max(math.sqrt(sum(v * v for v in vec)), abs(l1 - l2) + 1e-3)
    d = _norm(vec)
    along = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
    height = math.sqrt(max(l1 * l1 - along * along, 0.0))
    pole = (e[0] - s[0], e[1] - s[1], e[2] - s[2])
    dot = sum(pole[i] * d[i] for i in range(3))
    pole = (pole[0] - dot * d[0], pole[1] - dot * d[1], pole[2] - dot * d[2])
    if sum(v * v for v in pole) < 1e-9:
        pole = (0.0, 1.0, 0.0)
    pole = _norm(pole)
    figure.points[elb] = tuple(s[i] + d[i] * along + pole[i] * height for i in range(3))  # type: ignore[assignment]
    figure.points[wri] = tuple(target)  # type: ignore[assignment]
    return moved


def mirror_box(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, w, h = box
    return (1 - x - w, y, w, h)
