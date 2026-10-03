"""Region assignment for multi-character panels.

The engine renders one panel per image. When a panel has several characters each
needs its own IP-Adapter reference *bound to a region*, so their features do not
bleed into each other. This module decides those regions as normalised boxes
(fractions of the canvas), plus the pixel masks ComfyUI needs.

Layout is deliberately simple and deterministic (no ML segmentation): characters
are spread **horizontally** in reading order. The artist can override later.
"""

from __future__ import annotations

from dataclasses import dataclass

from manganation.script.schema import ReadingOrder


@dataclass(frozen=True)
class Region:
    """A normalised sub-rectangle of the canvas (0..1)."""

    x: float
    y: float
    w: float
    h: float

    def scaled(self, width: int, height: int, *, multiple: int = 64) -> tuple[int, int, int, int]:
        """Pixel box (x, y, w, h), sizes rounded to ``multiple`` and clipped."""
        x = round(self.x * width / multiple) * multiple
        y = round(self.y * height / multiple) * multiple
        w = max(multiple, round(self.w * width / multiple) * multiple)
        h = max(multiple, round(self.h * height / multiple) * multiple)
        w = min(w, width - x)
        h = min(h, height - y)
        return x, y, w, h


def assign_regions(
    count: int,
    *,
    order: ReadingOrder = ReadingOrder.RTL,
    margin: float = 0.0,
) -> list[Region]:
    """Split the canvas into ``count`` vertical bands in reading order.

    RTL (manga) fills right→left; LTR fills left→right. Characters keep a full-height
    band; vertical splitting (top/bottom) is avoided because SDXL composes upright
    figures poorly when cropped to a wide strip.

    ``margin`` shrinks each band horizontally (fraction of the band, 0..0.45) to
    reduce cross-talk between neighbours.
    """
    if count < 1:
        raise ValueError("count must be >= 1")
    band = 1.0 / count
    regions: list[Region] = []
    for i in range(count):
        inset = band * max(0.0, min(margin, 0.45))
        w = band - 2 * inset
        left_slot = i if order is ReadingOrder.LTR else (count - 1 - i)
        x = left_slot * band + inset
        regions.append(Region(x=x, y=0.0, w=w, h=1.0))
    return regions


def regions_for(width: int, height: int, count: int, *, order: ReadingOrder = ReadingOrder.RTL,
                margin: float = 0.05) -> list[tuple[int, int, int, int]]:
    """Pixel boxes for ``count`` characters in reading order."""
    return [r.scaled(width, height) for r in assign_regions(count, order=order, margin=margin)]
