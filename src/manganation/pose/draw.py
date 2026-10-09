"""Draw figures as the OpenPose body-18 image a pose ControlNet reads."""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from manganation.pose.rig import Figure

LIMBS = [(1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10), (1, 11),
         (11, 12), (12, 13), (1, 0), (0, 14), (14, 16), (0, 15), (15, 17)]
COLORS = [(255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0),
          (85, 255, 0), (0, 255, 0), (0, 255, 85), (0, 255, 170), (0, 255, 255),
          (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255), (170, 0, 255),
          (255, 0, 255), (255, 0, 170), (255, 0, 85)]


def draw_figures(figures: list[Figure], width: int, height: int) -> Image.Image:
    """The image an OpenPose ControlNet reads: coloured limb ellipses and joint dots on
    black. Farther figures are drawn first, so nearer limbs overlap them."""
    image = Image.new("RGB", (width, height), "black")
    stick = max(3.0, min(width, height) / 128)
    order = sorted(figures, key=lambda f: -sum(p[2] for p in f.points if p) / max(
        1, sum(1 for p in f.points if p)))
    for figure in order:
        pts = figure.points
        for (a, b), color in zip(LIMBS, COLORS, strict=False):
            if pts[a] is None or pts[b] is None:
                continue
            (x1, y1, _), (x2, y2, _) = pts[a], pts[b]
            length = math.hypot(x2 - x1, y2 - y1)
            if length < 1:
                continue
            angle = math.atan2(y2 - y1, x2 - x1)
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            ring = []
            for deg in range(0, 360, 12):
                ex = length / 2 * math.cos(math.radians(deg))
                ey = stick * math.sin(math.radians(deg))
                ring.append((cx + ex * math.cos(angle) - ey * math.sin(angle),
                             cy + ex * math.sin(angle) + ey * math.cos(angle)))
            layer = Image.new("L", (width, height), 0)
            ImageDraw.Draw(layer).polygon(ring, fill=255)
            image.paste(tuple(int(v * 0.6) for v in color), mask=layer)
        canvas = ImageDraw.Draw(image)
        for point, color in zip(pts, COLORS, strict=True):
            if point is not None:
                x, y, _ = point
                canvas.ellipse((x - stick, y - stick, x + stick, y + stick), fill=color)
    return image
