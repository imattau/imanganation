"""Built-in page frame layouts, expressed as normalised (x, y, w, h) regions.

Regions are listed in reading order and authored left-to-right; ``for_reading_order``
mirrors them for right-to-left (manga) pages, so a layout's first region is always the
frame read first, and ranking can match frames to script panels one by one."""

import math
import re

_M = 0.04
_G = 0.018
_W = 1 - 2 * _M


def _grid(columns: int, rows: int) -> tuple[tuple[float, float, float, float], ...]:
    width = (_W - (columns - 1) * _G) / columns
    height = (_W - (rows - 1) * _G) / rows
    return tuple((_M + col * (width + _G), _M + row * (height + _G), width, height)
                 for row in range(rows) for col in range(columns))


def _preset(name: str, regions):
    return {"name": name, "regions": tuple(tuple(float(v) for v in r) for r in regions)}


# Uneven rows use a full-width establishing panel followed by equal beats.
_top = (_M, _M, _W, 0.34)
_bottom_y = 0.40
_bottom_h = 1 - _M - _bottom_y
_three_bottom = tuple((_M + col * (_W + _G) / 3,
                       _bottom_y,
                       (_W - 2 * _G) / 3,
                       _bottom_h) for col in range(3))
_two_bottom = tuple((_M + col * (_W + _G) / 2,
                     _bottom_y,
                     (_W - _G) / 2,
                     _bottom_h) for col in range(2))
_five_bottom = tuple((_M + col * (_W + _G) / 2,
                      _bottom_y + row * (_bottom_h + _G) / 2,
                      (_W - _G) / 2,
                      (_bottom_h - _G) / 2)
                     for row in range(2) for col in range(2))
_five_grid = tuple((_M + col * (_W + _G) / 2,
                    _M + row * (0.28 + _G),
                    (_W - _G) / 2, 0.28)
                   for row in range(2) for col in range(2)) + (
                       (_M, _M + 2 * (0.28 + _G), _W,
                        1 - _M - (_M + 2 * (0.28 + _G))),)
# A tall opener down the leading side, then the rest stacked beside it.
_tall_w = (_W - _G) * 0.42
_rest_x = _M + _tall_w + _G
_rest_w = (_W - _G) * 0.58
_three_tall = (
    (_M, _M, _tall_w, _W),
    (_rest_x, _M, _rest_w, (_W - _G) / 2),
    (_rest_x, _M + (_W - _G) / 2 + _G, _rest_w, (_W - _G) / 2),
)
_four_tall = (
    (_M, _M, _tall_w, _W),
    *tuple((_rest_x,
            _M + row * (_W + _G) / 3,
            _rest_w, (_W - 2 * _G) / 3) for row in range(3)),
)
LAYOUTS = (
    _preset("Single panel", _grid(1, 1)),
    _preset("Two stacked", _grid(1, 2)),
    _preset("Two side by side", _grid(2, 1)),
    _preset("Three: wide opener", (_top, *_two_bottom)),
    _preset("Three: tall opener", _three_tall),
    _preset("Three stacked", _grid(1, 3)),
    _preset("Four grid", _grid(2, 2)),
    _preset("Four: tall opener", _four_tall),
    _preset("Four: wide opener", (_top, *_three_bottom)),
    _preset("Five: wide opener", (_top, *_five_bottom)),
    _preset("Five grid", _five_grid),
    _preset("Six grid", _grid(2, 3)),
    _preset("Six: three across", _grid(3, 2)),
)

# Border treatments are independent of panel arrangement so every matching layout can
# be paired with the same visual styles.
FRAME_STYLES = (
    {"name": "Fine ink", "weight": 0.0035, "slant": 0.0, "double": False},
    {"name": "Classic ink", "weight": 0.006, "slant": 0.0, "double": False},
    {"name": "Bold ink", "weight": 0.012, "slant": 0.0, "double": False},
    {"name": "Slanted frames", "weight": 0.006, "slant": 0.025, "double": False},
    {"name": "Double rule", "weight": 0.007, "slant": 0.0, "double": True},
)


def layouts_for_count(count: int) -> list[dict]:
    """Return built-in layouts with exactly ``count`` regions."""
    return [layout for layout in LAYOUTS if len(layout["regions"]) == count]


def layout_style_combinations(count: int) -> list[tuple[dict, dict]]:
    """The gallery choices for a script page with ``count`` panels."""
    return [(layout, style) for layout in layouts_for_count(count)
            for style in FRAME_STYLES]


def for_reading_order(layout: dict, reading_order: str = "rtl") -> dict:
    """The layout as read in ``reading_order``: mirrored left-right for ``rtl``, so the
    first region (the opener) sits on the right, where a manga page starts."""
    if reading_order != "rtl":
        return layout
    return {**layout, "regions": tuple((1 - x - w, y, w, h)
                                       for x, y, w, h in layout["regions"])}


def page_layout_availability(manifest: dict, page_id: str,
                             page_aspect: float = 1.0) -> dict:
    """Describe whether a page can receive a generated layout and why if it cannot."""
    page = next((item for item in manifest.get("pages", [])
                 if item.get("id") == page_id), None)
    if page is None:
        return {"available": False, "reason": "This project page could not be found."}

    placed = sum(1 for panel in manifest.get("panels", [])
                 if (panel.get("placement") or {}).get("page") == page_id)
    if placed:
        suffix = "panel is" if placed == 1 else "panels are"
        return {"available": False, "placed": placed,
                "reason": f"Layout locked · {placed} {suffix} already placed"}

    page_number, count = page_panel_count(manifest, page.get("label", ""))
    if page_number is None:
        return {"available": False,
                "reason": "Rename this page to Page N to match its script page."}
    if not count:
        return {"available": False,
                "reason": f"No script panels match Page {page_number}."}
    reading_order = (manifest.get("project") or {}).get("reading_order", "rtl")
    layouts = [for_reading_order(layout, reading_order)
               for layout in layouts_for_count(count)]
    if not layouts:
        return {"available": False,
                "reason": f"No built-in layout supports {count} panels on Page {page_number}."}
    page_panels = sorted((panel for panel in manifest.get("panels", [])
                          if panel.get("status") != "orphaned"
                          and (panel.get("label") or {}).get("page") == page_number),
                         key=lambda panel: (panel.get("label") or {}).get("panel", 0))
    ranked, recommendation = rank_layouts(layouts, page_panels, page_aspect)
    # Put one preview of every arrangement up front before repeating layouts with
    # alternate border treatments, so users can compare geometry without scrolling.
    combinations = [(layout, style) for style in FRAME_STYLES for layout in ranked]
    return {"available": True, "page_number": page_number, "panel_count": count,
            "layouts": ranked, "combinations": combinations,
            "recommendation": recommendation}


def _ratio(value):
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*",
        str(value or ""))
    if not match:
        return None
    left, right = map(float, match.groups())
    return left / right if right else None


# How big a panel wants to be, as its rank among the page's frames by area (0 = the
# smallest, 1 = the largest). An explicit [FRAME] size beats a guess from the shot.
SIZE_TARGETS = {"splash": 1.0, "large": 0.85, "small": 0.15}
NEUTRAL_SIZE = 0.5


def _size_target(panel):
    """(target area rank, is it a real hint?) for one panel."""
    size = str(panel.get("size") or "").lower()
    if size in SIZE_TARGETS:
        return SIZE_TARGETS[size], True
    shot = str(panel.get("camera") or "").lower()
    if "wide" in shot or "establish" in shot:
        return 0.78, True
    if "close" in shot or "detail" in shot:
        return 0.28, True
    return NEUTRAL_SIZE, False


def _implied_shape(panel):
    """(ratio, weight): a wide or establishing shot leans towards a wide frame (a manga
    convention, so only half weight); other shots say nothing about shape."""
    shot = str(panel.get("camera") or "").lower()
    if "wide" in shot or "establish" in shot:
        return 1.6, 0.5
    return None, 0.0


def _area_ranks(regions):
    """Each region's size rank, 0 (smallest) .. 1 (largest). Equal areas share a rank,
    so in a grid every frame is 0.5 and no panel is favoured by list position."""
    areas = [width * height for _, _, width, height in regions]
    if len(areas) < 2:
        return [NEUTRAL_SIZE] * len(areas)
    ranks = []
    for area in areas:
        smaller = sum(1 for other in areas if other < area * 0.97)
        equal = sum(1 for other in areas if abs(other - area) <= area * 0.03)
        ranks.append((smaller + (equal - 1) / 2) / (len(areas) - 1))
    return ranks


def rank_layouts(layouts, panels, page_aspect=1.0):
    """Rank layouts against the page's panels' frame hints; keep every layout available.

    ``panels`` are in script order and each layout's regions in reading order, so frame
    *i* is matched with panel *i*: a wide establishing shot on panel 1 favours a wide
    opener, not just any layout with one wide frame somewhere. Shape comes from
    ``aspect_ratio`` ([FRAME: wide] or 3:2), size from ``size`` ([FRAME: large]) or,
    failing that, the shot. -> (ranked layouts, recommended name or None when the page
    has no hint at all)."""
    ratios = [_ratio(panel.get("aspect_ratio")) for panel in panels]
    shapes = [(ratio, 1.0) if ratio else _implied_shape(panel)
              for ratio, panel in zip(ratios, panels, strict=True)]
    sizes = [_size_target(panel) for panel in panels]

    def score(layout):
        regions = layout["regions"]
        shape = [weight * abs(math.log(width / height * page_aspect / wanted))
                 for (_, _, width, height), (wanted, weight) in zip(regions, shapes, strict=False)
                 if wanted]
        rank = _area_ranks(regions)
        # Only hinted panels count: a panel with no hint is happy in any frame.
        size = [abs(rank[i] - target)
                for i, (target, hinted) in enumerate(sizes[:len(regions)]) if hinted]
        return ((sum(shape) / len(shape) if shape else 0.0)
                + (sum(size) / len(size) if len(regions) > 1 and size else 0.0))

    ranked = sorted(layouts, key=score)
    informed = any(ratios) or any(hinted for _, hinted in sizes)
    return ranked, (ranked[0]["name"] if informed and ranked else None)


def frame_rings(region, page_width: int, page_height: int, style: dict, index: int = 0):
    """Return outer/inner polygon pairs for the visible border rings of one frame."""
    nx, ny, nw, nh = region
    x, y = round(nx * page_width), round(ny * page_height)
    width, height = round(nw * page_width), round(nh * page_height)
    line = max(2, min(32, round(min(page_width, page_height) * style["weight"])))
    border = max(1, min(line, (width - 2) // 2, (height - 2) // 2))
    slant = round(width * style["slant"])
    lean = 1 if index % 2 == 0 else -1

    def polygon(px, py, pw, ph, inset):
        tilt = slant * lean
        return ((px + inset + tilt, py + inset),
                (px + pw - inset, py + inset),
                (px + pw - inset - tilt, py + ph - inset),
                (px + inset, py + ph - inset))

    rings = [(polygon(x, y, width, height, 0),
              polygon(x, y, width, height, border))]
    if style["double"]:
        gap = max(border * 2, round(min(width, height) * 0.018))
        inner_border = max(1, border // 2)
        if width > gap * 2 + inner_border * 2 and height > gap * 2 + inner_border * 2:
            rings.append((polygon(x, y, width, height, gap),
                          polygon(x, y, width, height, gap + inner_border)))
    return tuple(rings)


def layout_preview_rgb(layout: dict, style: dict, width: int, height: int) -> bytes:
    """Render a small white page thumbnail using the same frame geometry as GIMP."""
    if width < 1 or height < 1:
        return b""
    pixels = bytearray((250, 250, 250) * (width * height))

    def fill_polygon(points, color):
        minimum_y = max(0, min(y for _, y in points))
        maximum_y = min(height, max(y for _, y in points))
        for py in range(minimum_y, maximum_y):
            scan_y = py + 0.5
            intersections = []
            for index, (x1, y1) in enumerate(points):
                x2, y2 = points[(index + 1) % len(points)]
                if (y1 <= scan_y < y2) or (y2 <= scan_y < y1):
                    intersections.append(x1 + (scan_y - y1) * (x2 - x1) / (y2 - y1))
            intersections.sort()
            for left, right in zip(intersections[::2], intersections[1::2]):
                start = max(0, int(left + 0.5))
                end = min(width, int(right + 0.5))
                for px in range(start, end):
                    offset = (py * width + px) * 3
                    pixels[offset:offset + 3] = color

    # A subtle page edge makes the white gutters visible on a light GTK background.
    fill_polygon(((1, 1), (width - 1, 1), (width - 1, height - 1), (1, height - 1)),
                 (235, 235, 235))
    fill_polygon(((2, 2), (width - 2, 2), (width - 2, height - 2), (2, height - 2)),
                 (255, 255, 255))
    for index, region in enumerate(layout["regions"]):
        for outer, inner in frame_rings(region, width, height, style, index):
            fill_polygon(outer, (20, 20, 20))
            fill_polygon(inner, (255, 255, 255))
    return bytes(pixels)


def page_panel_count(manifest: dict, page_label: str) -> tuple[int | None, int | None]:
    """Return (script page number, panel count), excluding orphaned script panels."""
    import re

    match = re.search(r"\bpage\s+(\d+)\b", str(page_label), re.IGNORECASE)
    if match is None:
        return None, None
    page_number = int(match.group(1))
    count = sum(1 for panel in manifest.get("panels", [])
                if panel.get("status") != "orphaned"
                and (panel.get("label") or {}).get("page") == page_number)
    return page_number, count
