"""Speech-bubble templates: geometry, text boxes and previews. Standard library only.

Every bubble body is a closed outline sampled from a radius-around-the-centre
function (oval, rounded box, cloud, burst, ...), so one routine splices a tail into
any of them: the outline points facing the tail tip are replaced by the tail. The
plug-in turns an outline into an SVG path for a GIMP vector layer (editable with the
Paths tool afterwards) and puts a text layer in ``text_box``.

Coordinates are pixels in the bubble's own box: the body fills ``0..width`` by
``0..height`` and a tail may reach outside it.
"""

from __future__ import annotations

import math
import random
import struct
import zlib
from dataclasses import dataclass

GEOMETRY_VERSION = 1  # bump when shapes change, so cached previews regenerate

CATEGORIES = [
    ("speech", "Speech"),
    ("whisper", "Whisper"),
    ("thought", "Thought"),
    ("shout", "Shout"),
    ("narration", "Narration"),
    ("inner", "Inner voice"),
    ("nervous", "Nervous"),
    ("electric", "Electric"),
    ("flash", "Flash"),
    ("sfx", "Sound effect"),
]
CATEGORY_LABELS = dict(CATEGORIES)
# The script's dialogue kinds (and SFX) -> the picker's first category
KIND_CATEGORY = {"speech": "speech", "thought": "thought", "shout": "shout",
                 "whisper": "whisper", "narration": "narration", "sfx": "sfx"}

ASPECTS = {"wide": 1.7, "round": 1.2, "tall": 0.72}


@dataclass(frozen=True)
class Template:
    id: str
    category: str
    label: str
    shape: str            # oval, rounded, box, cloud, burst, wobbly, electric, flash, none
    aspect: float         # width / height
    tail: str = "pointed"  # pointed, curved, dots, none
    outline: str = "solid"  # solid, heavy, dashed, none
    fill: str = "white"   # white, black (white text), none
    detail: int = 0       # scallops / spikes / waves, per shape
    depth: float = 0.0    # spike depth for bursts

    @property
    def text_color(self) -> tuple[float, float, float]:
        return (1.0, 1.0, 1.0) if self.fill == "black" else (0.0, 0.0, 0.0)


def templates() -> list[Template]:
    """The built-in library (about 120 bubbles), grouped by category."""
    out: list[Template] = []

    def add(category, shape, aspect_name, tail="pointed", outline="solid", fill="white",
            detail=0, depth=0.0, label=""):
        aspect = ASPECTS[aspect_name]
        bits = [shape, aspect_name, tail, outline, fill]
        if detail:
            bits.append(str(detail))
        tid = f"{category}-" + "-".join(bits)
        name = label or f"{shape.title()} · {aspect_name}"
        if tail != "none":
            name += f" · {tail} tail"
        if outline not in ("solid", "none"):
            name += f" · {outline}"
        out.append(Template(tid, category, name, shape, aspect, tail, outline, fill,
                            detail, depth))

    for aspect in ASPECTS:
        for shape in ("oval", "rounded"):
            for tail in ("pointed", "curved", "none"):
                for outline in ("solid", "heavy"):
                    add("speech", shape, aspect, tail, outline)
            add("whisper", shape, aspect, "pointed", "dashed")
            add("whisper", shape, aspect, "curved", "dashed")
        for scallops in (9, 14):
            add("thought", "cloud", aspect, "dots", detail=scallops)
            add("thought", "cloud", aspect, "none", detail=scallops)
        for spikes, depth in ((12, 0.22), (18, 0.3), (26, 0.18)):
            add("shout", "burst", aspect, "pointed", "heavy", detail=spikes, depth=depth)
        add("shout", "burst", aspect, "none", "heavy", detail=18, depth=0.3)
        for shape in ("box", "rounded"):
            add("narration", shape, aspect, "none")
            add("narration", shape, aspect, "none", fill="black")
        add("inner", "oval", aspect, "none", fill="black")
        add("inner", "cloud", aspect, "none", fill="black", detail=12)
        add("nervous", "wobbly", aspect, "pointed", detail=22)
        add("nervous", "wobbly", aspect, "curved", "dashed", detail=30)
        add("electric", "electric", aspect, "pointed", detail=28)
        add("electric", "electric", aspect, "none", "heavy", detail=40)
        add("flash", "burst", aspect, "none", "none", detail=56, depth=0.28)
        add("flash", "burst", aspect, "none", "solid", detail=40, depth=0.2)
    for aspect in ("wide", "round"):
        add("sfx", "none", aspect, "none", "none", fill="none",
            label=f"Text only · {aspect}")
    return out


def template_by_id(template_id: str) -> Template | None:
    return next((t for t in templates() if t.id == template_id), None)


# --- geometry ---------------------------------------------------------------

def _superellipse(theta: float, a: float, b: float, n: float) -> float:
    c, s = abs(math.cos(theta)), abs(math.sin(theta))
    return (((c / a) ** n) + ((s / b) ** n)) ** (-1.0 / n)


def _box(theta: float, a: float, b: float) -> float:
    c, s = abs(math.cos(theta)), abs(math.sin(theta))
    return min(a / c if c > 1e-9 else math.inf, b / s if s > 1e-9 else math.inf)


def _body(t: Template, width: float, height: float) -> list[tuple[float, float]]:
    """The closed body outline (no tail), counter-clockwise from 3 o'clock."""
    a, b = width / 2.0, height / 2.0
    cx, cy = a, b
    rng = random.Random(t.id)  # bursts and electric edges: irregular but repeatable
    if t.shape == "cloud":
        count = t.detail * 12
    elif t.shape in ("burst", "electric"):
        count = t.detail * 2
    elif t.shape == "wobbly":
        count = 240
    else:
        count = 120
    points = []
    for i in range(count):
        theta = 2 * math.pi * i / count
        if t.shape == "box":
            r = _box(theta, a, b)
        elif t.shape == "rounded":
            r = _superellipse(theta, a, b, 4.5)
        else:
            r = _superellipse(theta, a, b, 2.0)
        if t.shape == "cloud":
            # rounded scallops with cusps between them
            r *= 0.84 + 0.16 * math.sqrt(abs(math.sin(t.detail * theta / 2)))
        elif t.shape == "burst":
            if i % 2:
                r *= 1.0 - t.depth * (0.75 + 0.5 * rng.random())
            else:
                r *= 0.94 + 0.06 * rng.random()
        elif t.shape == "electric":
            r *= (0.9 + 0.04 * rng.random()) if i % 2 else 1.0
        elif t.shape == "wobbly":
            r *= 0.965 + 0.035 * math.sin(t.detail * theta)
        points.append((cx + r * math.cos(theta), cy - r * math.sin(theta)))
    return points


def default_tail_tip(width: float, height: float) -> tuple[float, float]:
    """Below the bubble, a little left of centre."""
    return (width * 0.32, height * 1.38)


def outline(t: Template, width: float, height: float,
            tail_tip: tuple[float, float] | None = None) -> list[list[tuple[float, float]]]:
    """Closed polygons for the bubble: the body (with its tail spliced in) first, then
    any thought dots. ``tail_tip`` defaults to below-left; a tip inside the body
    draws no tail."""
    if t.shape == "none":
        return []
    body = _body(t, width, height)
    if t.tail == "none":
        return [body]
    tip = tail_tip or default_tail_tip(width, height)
    cx, cy = width / 2.0, height / 2.0
    angle = math.atan2(cy - tip[1], tip[0] - cx) % (2 * math.pi)
    reach = math.hypot(tip[0] - cx, tip[1] - cy)
    edge = _superellipse(angle, cx, cy, 2.0)
    if reach <= edge * 1.02:
        return [body]
    count = len(body)
    if t.tail == "dots":
        dots = []
        for step, radius in ((0.28, 0.075), (0.58, 0.05), (0.86, 0.032)):
            px = cx + (edge + (reach - edge) * step) * math.cos(angle)
            py = cy - (edge + (reach - edge) * step) * math.sin(angle)
            rr = radius * min(width, height)
            dots.append([(px + rr * math.cos(2 * math.pi * k / 24),
                          py - rr * math.sin(2 * math.pi * k / 24)) for k in range(24)])
        return [body, *dots]
    # base of the tail: the outline points within a half-angle of the tail direction
    half = math.atan2(0.11 * min(width, height), edge)
    start = int(round((angle - half) / (2 * math.pi) * count)) % count
    end = int(round((angle + half) / (2 * math.pi) * count)) % count
    base1, base2 = body[start], body[end]
    if t.tail == "pointed":
        tail = [tip]
    else:  # curved: both edges bow to the same side, a hooked tail
        mx, my = (base1[0] + base2[0]) / 2, (base1[1] + base2[1]) / 2
        nx, ny = -(tip[1] - my), tip[0] - mx
        length = math.hypot(nx, ny) or 1.0
        bow = 0.22 * math.hypot(tip[0] - mx, tip[1] - my)
        ctrl = ((mx + tip[0]) / 2 + nx / length * bow, (my + tip[1]) / 2 + ny / length * bow)

        def quad(p0, p1, p2, steps=8):
            return [((1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * p1[0] + u * u * p2[0],
                     (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * p1[1] + u * u * p2[1])
                    for u in (k / steps for k in range(1, steps + 1))]

        tail = quad(base1, ctrl, tip)[:-1] + [tip] + quad(tip, ctrl, base2)[:-1]
    # walk the outline from base2 round to base1 (the long way), then the tail
    ring = [body[(end + k) % count] for k in range((start - end) % count + 1)]
    return [ring + tail]


_TEXT_BOX = {"oval": 0.7, "rounded": 0.8, "box": 0.88, "cloud": 0.6, "burst": 0.0,
             "wobbly": 0.68, "electric": 0.64, "none": 0.96}


def text_box(t: Template, width: float, height: float) -> tuple[float, float, float, float]:
    """Where the text goes, centred in the body: (x, y, w, h) in the bubble's box."""
    factor = _TEXT_BOX.get(t.shape, 0.7)
    if t.shape == "burst":
        factor = max(0.4, 0.74 - t.depth)
    w, h = width * factor, height * factor
    return ((width - w) / 2, (height - h) / 2, w, h)


def size_for_text(t: Template, text_width: float, text_height: float,
                  padding: float = 12.0) -> tuple[float, float]:
    """The bubble size whose text box holds a text block of the given size."""
    x, y, w, h = text_box(t, 1000.0, 1000.0)
    return ((text_width + 2 * padding) * 1000.0 / w,
            (text_height + 2 * padding) * 1000.0 / h)


def stroke_width(t: Template, width: float, height: float) -> float:
    if t.outline == "none":
        return 0.0
    base = max(2.0, min(width, height) * 0.018)
    return base * (2.2 if t.outline == "heavy" else 1.0)


def dash_pattern(t: Template) -> list[float]:
    """Dash lengths in stroke widths (a GIMP vector layer's dash pattern)."""
    return [3.0, 2.0] if t.outline == "dashed" else []


def svg_document(polygons: list[list[tuple[float, float]]], width: int, height: int,
                 offset: tuple[float, float] = (0.0, 0.0)) -> str:
    """An SVG of the polygons on an image-sized canvas, so GIMP imports the path at
    image coordinates (``offset`` places the bubble's box)."""
    ox, oy = offset
    parts = []
    for polygon in polygons:
        parts.append("M " + " L ".join(f"{x + ox:.2f} {y + oy:.2f}" for x, y in polygon) + " Z")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}"><path d="{" ".join(parts)}"/></svg>')


# --- previews ---------------------------------------------------------------

def preview_png(t: Template, size: int = 72, supersample: int = 2) -> bytes:
    """A small PNG of the bubble on a light grey tile (white bubbles stay visible)."""
    n = size * supersample
    aspect = t.aspect
    bw, bh = (n * 0.78, n * 0.78 / aspect) if aspect >= 1 else (n * 0.78 * aspect, n * 0.78)
    if t.tail != "none":
        bw, bh = bw * 0.86, bh * 0.86
    ox, oy = (n - bw) / 2, (n - bh) / 2 - (n * 0.08 if t.tail != "none" else 0)
    tip = (bw * 0.3, bh + n * 0.14)
    polygons = [[(x + ox, y + oy) for x, y in poly]
                for poly in outline(t, bw, bh, tip if t.tail != "none" else None)]
    bg = (226, 226, 226)
    canvas = bytearray(bytes(bg) * (n * n))
    fill = {"white": (255, 255, 255), "black": (20, 20, 20)}.get(t.fill)
    ink = (20, 20, 20) if t.fill != "black" else (90, 90, 90)
    if fill:
        for poly in polygons:
            _fill_polygon(canvas, n, poly, fill)
    sw = stroke_width(t, bw, bh) * 1.6
    if sw:
        for poly in polygons:
            _stroke_polygon(canvas, n, poly, sw, ink, dashed=t.outline == "dashed")
    if t.shape == "none" or t.fill == "black":  # sample text, so the tile reads
        tx, ty, tw, th = text_box(t, bw, bh)
        color = (255, 255, 255) if t.fill == "black" else (20, 20, 20)
        for row in range(3):
            y = oy + ty + th * (0.3 + 0.2 * row)
            _stroke_polygon(canvas, n, [(ox + tx + tw * 0.2, y), (ox + tx + tw * 0.8, y)],
                            max(2.0, n * 0.03), color, closed=False)
    return _downsample_png(canvas, n, supersample)


def _fill_polygon(canvas, n, poly, color):
    edges = list(zip(poly, poly[1:] + poly[:1], strict=True))
    pixel = bytes(color)
    for row in range(n):
        y = row + 0.5
        xs = sorted(x0 + (y - y0) * (x1 - x0) / (y1 - y0)
                    for (x0, y0), (x1, y1) in edges if (y0 <= y) != (y1 <= y))
        for left, right in zip(xs[::2], xs[1::2], strict=False):
            a, b = max(0, int(math.ceil(left - 0.5))), min(n, int(math.floor(right - 0.5)) + 1)
            if b > a:
                canvas[(row * n + a) * 3:(row * n + b) * 3] = pixel * (b - a)


def _stroke_polygon(canvas, n, poly, width, color, dashed=False, closed=True):
    half = width / 2.0
    pixel = bytes(color)
    segments = list(zip(poly, poly[1:] + (poly[:1] if closed else []), strict=False))
    travelled, dash = 0.0, width * 3
    for (x0, y0), (x1, y1) in segments:
        length = math.hypot(x1 - x0, y1 - y0)
        on = not dashed or int(travelled // dash) % 2 == 0
        travelled += length
        if not on or length == 0:
            continue
        for row in range(max(0, int(min(y0, y1) - half)), min(n, int(max(y0, y1) + half) + 1)):
            for col in range(max(0, int(min(x0, x1) - half)),
                             min(n, int(max(x0, x1) + half) + 1)):
                px, py = col + 0.5, row + 0.5
                u = max(0.0, min(1.0, ((px - x0) * (x1 - x0) + (py - y0) * (y1 - y0))
                                 / (length * length)))
                if math.hypot(px - x0 - u * (x1 - x0), py - y0 - u * (y1 - y0)) <= half:
                    canvas[(row * n + col) * 3:(row * n + col) * 3 + 3] = pixel


def _downsample_png(canvas, n, factor):
    size = n // factor
    out = bytearray(size * size * 3)
    area = factor * factor
    for row in range(size):
        for col in range(size):
            for channel in range(3):
                total = 0
                for dy in range(factor):
                    base = ((row * factor + dy) * n + col * factor) * 3 + channel
                    total += sum(canvas[base:base + factor * 3:3])
                out[(row * size + col) * 3 + channel] = total // area
    stride = size * 3
    raw = b"".join(b"\0" + bytes(out[r * stride:(r + 1) * stride]) for r in range(size))

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload)))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2,
                                                             0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
