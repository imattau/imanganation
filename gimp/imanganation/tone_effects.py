"""Non-AI manga tone and effect geometry (stdlib only)."""

from __future__ import annotations

import math


def screentone_svg(width: int, height: int, bounds: tuple[float, float, float, float],
                   spacing: float = 24.0, coverage: float = 35.0,
                   angle: float = 45.0) -> str:
    """Return a page-sized SVG path of halftone dots within the selection bounds.

    The SVG is intended to be loaded as a GIMP path, then intersected with the
    artist's saved selection before filling a separate layer.
    """
    if width < 1 or height < 1:
        raise ValueError("Page dimensions must be positive")
    if spacing < 4:
        raise ValueError("Dot spacing must be at least 4 px")
    if not 1 <= coverage <= 100:
        raise ValueError("Coverage must be between 1 and 100 percent")
    x, y, w, h = bounds
    if w <= 0 or h <= 0:
        raise ValueError("Select an area to apply screentone")

    radius = spacing * math.sqrt(coverage / 100.0 / math.pi)
    theta = math.radians(angle)
    co, si = math.cos(theta), math.sin(theta)
    # Inverse rotate the selection's corners to find a safe grid extent.
    corners = [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]
    grid = [(px * co + py * si, -px * si + py * co) for px, py in corners]
    left = math.floor(min(px for px, _ in grid) / spacing) - 1
    right = math.ceil(max(px for px, _ in grid) / spacing) + 1
    top = math.floor(min(py for _, py in grid) / spacing) - 1
    bottom = math.ceil(max(py for _, py in grid) / spacing) + 1

    circles = []
    for row in range(top, bottom + 1):
        gy = row * spacing
        for col in range(left, right + 1):
            gx = col * spacing + (spacing / 2 if row % 2 else 0)
            px, py = gx * co - gy * si, gx * si + gy * co
            if x - radius <= px <= x + w + radius and y - radius <= py <= y + h + radius:
                circles.append(f"M {px-radius:.2f},{py:.2f} a {radius:.2f},{radius:.2f} 0 1,0 {2*radius:.2f},0 a {radius:.2f},{radius:.2f} 0 1,0 {-2*radius:.2f},0 Z")
    path = " ".join(circles)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
            f'<path fill="#000" fill-rule="nonzero" d="{path}"/></svg>')
