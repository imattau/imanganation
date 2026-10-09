"""Non-AI manga tone and effect geometry (stdlib only)."""

from __future__ import annotations

import math
import random


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
                points = [(px + radius * math.cos(math.tau * k / 16),
                           py + radius * math.sin(math.tau * k / 16))
                          for k in range(16)]
                circles.append("M " + " L ".join(
                    f"{cx:.2f},{cy:.2f}" for cx, cy in points) + " Z")
    path = " ".join(circles)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
            f'<path fill="#000" fill-rule="nonzero" d="{path}"/></svg>')


def speed_lines_svg(width: int, height: int,
                    bounds: tuple[float, float, float, float],
                    focal: tuple[float, float], count: int = 96,
                    clear_percent: float = 18.0, stroke: float = 5.0,
                    seed: int = 1) -> str:
    """Return editable radial manga speed-line polygons, ready to clip to a selection."""
    if width < 1 or height < 1:
        raise ValueError("Page dimensions must be positive")
    if count < 8 or count > 720:
        raise ValueError("Line count must be between 8 and 720")
    if not 0 <= clear_percent <= 80:
        raise ValueError("Clear area must be between 0 and 80 percent")
    if stroke <= 0:
        raise ValueError("Line width must be positive")
    x, y, w, h = bounds
    fx, fy = focal
    if w <= 0 or h <= 0:
        raise ValueError("Select an area for the speed lines")
    radius = max(math.hypot(px - fx, py - fy)
                 for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)))
    start = min(w, h) * clear_percent / 100.0
    rng = random.Random(seed)
    polygons = []
    for i in range(count):
        angle = math.tau * i / count + rng.uniform(-0.28, 0.28) * math.tau / count
        nx, ny = -math.sin(angle), math.cos(angle)
        dx, dy = math.cos(angle), math.sin(angle)
        inner = start * rng.uniform(0.82, 1.18)
        outer = radius * rng.uniform(1.04, 1.14)
        half = stroke * rng.uniform(0.45, 1.0) / 2
        points = ((fx + dx * inner + nx * half, fy + dy * inner + ny * half),
                  (fx + dx * outer + nx * half, fy + dy * outer + ny * half),
                  (fx + dx * outer - nx * half, fy + dy * outer - ny * half),
                  (fx + dx * inner - nx * half, fy + dy * inner - ny * half))
        polygons.append("M " + " L ".join(f"{px:.2f},{py:.2f}" for px, py in points) + " Z")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
            f'<path fill="#000" fill-rule="nonzero" d="{" ".join(polygons)}"/></svg>')


def impact_burst_svg(width: int, height: int,
                     bounds: tuple[float, float, float, float],
                     center: tuple[float, float], spikes: int = 16,
                     depth: float = 45.0, rotation: float = 0.0) -> str:
    """Return a filled, jagged burst of editable wedges clipped to a selection."""
    if width < 1 or height < 1:
        raise ValueError("Page dimensions must be positive")
    if spikes < 6 or spikes > 120:
        raise ValueError("Spike count must be between 6 and 120")
    if not 10 <= depth <= 90:
        raise ValueError("Burst depth must be between 10 and 90 percent")
    x, y, w, h = bounds
    cx, cy = center
    if w <= 0 or h <= 0:
        raise ValueError("Select an area for the impact burst")
    outer = max(math.hypot(px - cx, py - cy)
                for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))) * 1.12
    inner = min(w, h) * (1.0 - depth / 100.0) / 2.0
    step = math.tau / spikes
    base = math.radians(rotation)
    # Each black wedge has a pointed outside edge and leaves a small white gap
    # from its neighbours; the clear center keeps the subject visible.
    polygons = []
    for i in range(spikes):
        angle = base + i * step
        half = step * 0.34
        points = []
        for radius, theta in ((inner, angle - half),
                              (outer, angle - half * 0.42),
                              (outer, angle + half * 0.42),
                              (inner, angle + half)):
            points.append((cx + radius * math.cos(theta),
                           cy + radius * math.sin(theta)))
        polygons.append("M " + " L ".join(f"{px:.2f},{py:.2f}" for px, py in points) + " Z")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
            f'<path fill="#000" fill-rule="nonzero" d="{" ".join(polygons)}"/></svg>')
