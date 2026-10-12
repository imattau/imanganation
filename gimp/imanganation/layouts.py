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


def _preset(name: str, regions, borderless=(), description=None):
    normalised = tuple(tuple(float(v) for v in r) for r in regions)
    # Later regions that intersect an earlier one are authored as overlays. The
    # renderer puts those panel groups above the base template and keeps their own
    # ink on a foreground layer.
    overlays = set(borderless)
    for index, (x, y, width, height) in enumerate(normalised):
        if any(x < ox + ow and ox < x + width and y < oy + oh and oy < y + height
               for ox, oy, ow, oh in normalised[:index]):
            overlays.add(index)
    preset = {"name": name, "regions": normalised,
              "borderless": tuple(borderless), "overlays": tuple(sorted(overlays))}
    if description:
        preset["description"] = description
    return preset


def _paced_column(heights, gaps):
    y = _M
    regions = []
    for index, panel_height in enumerate(heights):
        regions.append((_M, y, _W, panel_height))
        if index < len(gaps):
            y += panel_height + gaps[index]
    return tuple(regions)


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
# More open, cinematic arrangements. These keep the reading order explicit while
# varying panel width and scale beyond the regular grid and opener patterns.
_wide_middle = (
    (_M, _M, (_W - _G) / 2, 0.31),
    (_M + (_W + _G) / 2, _M, (_W - _G) / 2, 0.31),
    (_M, _M + 0.31 + _G, _W, 0.25),
    (_M, _M + 0.31 + _G + 0.25 + _G, _W, 1 - _M - (_M + 0.31 + _G + 0.25 + _G)),
)
_five_cinematic = (
    (_M, _M, _W, 0.30),
    (_M, _M + 0.30 + _G, (_W - _G) * 0.62, 0.34),
    (_M + (_W + _G) * 0.62, _M + 0.30 + _G, (_W - _G) * 0.38, 0.34),
    (_M, _M + 0.30 + _G + 0.34 + _G, (_W - _G) / 2, 1 - _M - (_M + 0.30 + _G + 0.34 + _G)),
    (_M + (_W + _G) / 2, _M + 0.30 + _G + 0.34 + _G, (_W - _G) / 2, 1 - _M - (_M + 0.30 + _G + 0.34 + _G)),
)
_six_wide_beats = (
    (_M, _M, _W, 0.25),
    (_M, _M + 0.25 + _G, (_W - _G) / 2, 0.22),
    (_M + (_W + _G) / 2, _M + 0.25 + _G, (_W - _G) / 2, 0.22),
    (_M, _M + 0.25 + _G + 0.22 + _G, _W, 0.20),
    (_M, _M + 0.25 + _G + 0.22 + _G + 0.20 + _G, (_W - _G) / 2, 1 - _M - (_M + 0.25 + _G + 0.22 + _G + 0.20 + _G)),
    (_M + (_W + _G) / 2, _M + 0.25 + _G + 0.22 + _G + 0.20 + _G, (_W - _G) / 2, 1 - _M - (_M + 0.25 + _G + 0.22 + _G + 0.20 + _G)),
)
# Insets deliberately cross the underlying frame; the later region is painted above
# it. Borderless indices suppress just that panel's frame while retaining its region.
_three_inset = (
    (_M, _M, _W, 0.60),
    (0.47, 0.31, 0.43, 0.31),
    (_M, 0.68, _W, 0.28),
)
_four_borderless_opener = (
    (_M, _M, _W, 0.48),
    (0.56, 0.24, 0.40, 0.31),
    (_M, 0.52, (_W - _G) / 2, 0.44),
    (_M + (_W + _G) / 2, 0.52, (_W - _G) / 2, 0.44),
)
_five_insets = (
    (_M, _M, _W, 0.53),
    (0.08, 0.30, 0.34, 0.26),
    (0.58, 0.37, 0.34, 0.25),
    (_M, 0.57, (_W - _G) / 2, 0.39),
    (_M + (_W + _G) / 2, 0.57, (_W - _G) / 2, 0.39),
)
def _clip(polygon, a, b, c):
    """Keep the part of a convex polygon where ``a*x + b*y + c >= 0``."""
    kept = []
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        d1, d2 = a * x1 + b * y1 + c, a * x2 + b * y2 + c
        if d1 >= 0:
            kept.append((x1, y1))
        if (d1 >= 0) != (d2 >= 0):
            t = d1 / (d1 - d2)
            kept.append((x1 + t * (x2 - x1), y1 + t * (y2 - y1)))
    return tuple(kept)


def _cut(polygon, start, end, gap=_G):
    """Split a convex polygon along the line start->end, leaving ``gap`` between the
    halves. Returns (before, after): above / left of the line, then below / right."""
    (x1, y1), (x2, y2) = start, end
    length = math.hypot(x2 - x1, y2 - y1)
    nx, ny = (y1 - y2) / length, (x2 - x1) / length
    # the "after" side is below a mostly-horizontal line, right of a mostly-vertical one
    if (ny < 0) if abs(ny) >= abs(nx) else (nx < 0):
        nx, ny = -nx, -ny
    base = nx * x1 + ny * y1
    before = _clip(polygon, -nx, -ny, base - gap / 2)
    after = _clip(polygon, nx, ny, -base - gap / 2)
    return before, after


def _angled(name, polygons, description=None):
    """A layout of convex, non-rectangular panels; each region is the polygon's box."""
    polygons = tuple(tuple((float(x), float(y)) for x, y in polygon)
                     for polygon in polygons)
    regions = tuple((min(x for x, _ in polygon), min(y for _, y in polygon),
                     max(x for x, _ in polygon) - min(x for x, _ in polygon),
                     max(y for _, y in polygon) - min(y for _, y in polygon))
                    for polygon in polygons)
    preset = _preset(name, regions, description=description
                     or "Angled panels split by diagonal gutters.")
    # Boxes of neighbouring diagonal panels overlap although the panels never do.
    preset["overlays"] = ()
    preset["polygons"] = polygons
    return preset


_PAGE = ((_M, _M), (_M + _W, _M), (_M + _W, _M + _W), (_M, _M + _W))


def _h(y_left, y_right):
    return (_M, y_left), (_M + _W, y_right)


def _v(x_top, x_bottom):
    return (x_top, _M), (x_bottom, _M + _W)


def _angled_layouts():
    layouts = []
    layouts.append(_angled("Two: diagonal stack", _cut(_PAGE, *_h(0.60, 0.40))))
    layouts.append(_angled("Two: diagonal side by side", _cut(_PAGE, *_v(0.62, 0.38))))

    a, rest = _cut(_PAGE, *_h(0.40, 0.30))
    layouts.append(_angled("Three: slanted stack", (a, *_cut(rest, *_h(0.70, 0.62)))))
    a, rest = _cut(_PAGE, *_h(0.50, 0.36))
    layouts.append(_angled("Three: diagonal opener", (a, *_cut(rest, *_v(0.58, 0.44)))))
    a, rest = _cut(_PAGE, *_v(0.46, 0.34))
    layouts.append(_angled("Three: angled tall opener", (a, *_cut(rest, *_h(0.52, 0.64)))))

    top, bottom = _cut(_PAGE, *_h(0.52, 0.44))
    layouts.append(_angled("Four: angled grid", (*_cut(top, *_v(0.56, 0.44)),
                                                 *_cut(bottom, *_v(0.46, 0.58)))))
    a, rest = _cut(_PAGE, *_h(0.32, 0.24))
    b, rest = _cut(rest, *_h(0.56, 0.62))
    layouts.append(_angled("Four: slanted rows", (a, b, *_cut(rest, *_h(0.80, 0.70)))))
    top, bottom = _cut(_PAGE, *_h(0.46, 0.38))
    b, rest = _cut(bottom, *_v(0.36, 0.28))
    layouts.append(_angled("Four: diagonal opener + trio",
                           (top, b, *_cut(rest, *_v(0.72, 0.64)))))

    a, rest = _cut(_PAGE, *_h(0.34, 0.26))
    top, bottom = _cut(rest, *_h(0.66, 0.56))
    layouts.append(_angled("Five: angled rhythm", (a, *_cut(top, *_v(0.54, 0.40)),
                                                   *_cut(bottom, *_v(0.44, 0.60)))))
    a, rest = _cut(_PAGE, *_v(0.42, 0.30))
    b, rest = _cut(rest, *_h(0.28, 0.20))
    c, rest = _cut(rest, *_h(0.56, 0.62))
    layouts.append(_angled("Five: tall opener, slanted stack",
                           (a, b, c, *_cut(rest, *_v(0.66, 0.78)))))

    rows = []
    rest = _PAGE
    for left, right in ((0.30, 0.24), (0.58, 0.64)):
        row, rest = _cut(rest, *_h(left, right))
        rows.append(row)
    rows.append(rest)
    layouts.append(_angled("Six: slanted grid", tuple(
        cell for row, cut in zip(rows, ((0.56, 0.44), (0.42, 0.58), (0.58, 0.46)))
        for cell in _cut(row, *_v(*cut)))))
    a, rest = _cut(_PAGE, *_h(0.24, 0.18))
    b, rest = _cut(rest, *_h(0.46, 0.52))
    c, rest = _cut(rest, *_h(0.70, 0.62))
    layouts.append(_angled("Six: angled beats", (
        a, *_cut(b, *_v(0.54, 0.40)), *_cut(c, *_v(0.40, 0.56)), rest)))
    return layouts


_scroll_three = _paced_column((0.18, 0.34, 0.22), (0.06, 0.08))
_scroll_four = _paced_column((0.15, 0.27, 0.16, 0.15), (0.05, 0.08, 0.06))
_scroll_five = _paced_column((0.11, 0.19, 0.12, 0.22, 0.12),
                             (0.03, 0.05, 0.04, 0.04))
_scroll_six = _paced_column((0.10, 0.15, 0.10, 0.18, 0.12, 0.10),
                            (0.025, 0.045, 0.03, 0.045, 0.025))
LAYOUTS = (
    _preset("Single panel", _grid(1, 1)),
    _preset("Two stacked", _grid(1, 2)),
    _preset("Two side by side", _grid(2, 1)),
    _preset("Three: wide opener", (_top, *_two_bottom)),
    _preset("Three: tall opener", _three_tall),
    _preset("Three stacked", _grid(1, 3)),
    _preset("Three: inset overlap", _three_inset),
    _preset("Vertical scroll: three paced beats", _scroll_three, borderless=range(3),
            description="Full-width panels with variable pauses; best on a tall canvas."),
    _preset("Four grid", _grid(2, 2)),
    _preset("Four: tall opener", _four_tall),
    _preset("Four: wide opener", (_top, *_three_bottom)),
    _preset("Four: cinematic beats", _wide_middle),
    _preset("Four: borderless opener + inset", _four_borderless_opener,
            borderless=(0,)),
    _preset("Vertical scroll: four paced beats", _scroll_four, borderless=range(4),
            description="Full-width panels with variable pauses; best on a tall canvas."),
    _preset("Five: wide opener", (_top, *_five_bottom)),
    _preset("Five grid", _five_grid),
    _preset("Five: cinematic rhythm", _five_cinematic),
    _preset("Five: overlapping insets", _five_insets),
    _preset("Vertical scroll: five paced beats", _scroll_five, borderless=range(5),
            description="Full-width panels with variable pauses; best on a tall canvas."),
    _preset("Six grid", _grid(2, 3)),
    _preset("Six: three across", _grid(3, 2)),
    _preset("Six: wide beats", _six_wide_beats),
    _preset("Vertical scroll: six paced beats", _scroll_six, borderless=range(6),
            description="Full-width panels with variable pauses; best on a tall canvas."),
    *_angled_layouts(),
)

# Covers are typography/artwork composition guides rather than script-panel grids.
# Each zone is a normalized rectangle on a single front/back cover canvas.
_COVER_SAFE = (_M + 0.035, _M + 0.035, _W - 0.07, _W - 0.07)
COVER_LAYOUTS = (
    {"name": "Front cover: classic masthead", "cover": "front",
     "description": "Title at top, clear focal-art area, credits at foot.",
     "zones": (("safe", _COVER_SAFE), ("title", (0.12, 0.08, 0.76, 0.17)),
               ("art", (0.12, 0.28, 0.76, 0.55)),
               ("credits", (0.12, 0.86, 0.76, 0.07))),
     "regions": tuple(rect for _, rect in (("safe", _COVER_SAFE),
               ("title", (0.12, 0.08, 0.76, 0.17)), ("art", (0.12, 0.28, 0.76, 0.55)),
               ("credits", (0.12, 0.86, 0.76, 0.07))))},
    {"name": "Front cover: art-first title", "cover": "front",
     "description": "Large artwork with a compact title band near the foot.",
     "zones": (("safe", _COVER_SAFE), ("art", (0.12, 0.10, 0.76, 0.70)),
               ("title", (0.12, 0.78, 0.76, 0.12)),
               ("credits", (0.12, 0.91, 0.76, 0.05))),
     "regions": (_COVER_SAFE, (0.12, 0.10, 0.76, 0.70),
                 (0.12, 0.78, 0.76, 0.12), (0.12, 0.91, 0.76, 0.05))},
    {"name": "Front cover: character spotlight", "cover": "front",
     "description": "Open focal area with offset title and a small issue mark.",
     "zones": (("safe", _COVER_SAFE), ("title", (0.12, 0.12, 0.54, 0.17)),
               ("art", (0.20, 0.30, 0.70, 0.57)),
               ("issue", (0.12, 0.80, 0.18, 0.10)),
               ("credits", (0.34, 0.88, 0.54, 0.05))),
     "regions": (_COVER_SAFE, (0.12, 0.12, 0.54, 0.17), (0.20, 0.30, 0.70, 0.57),
                 (0.12, 0.80, 0.18, 0.10), (0.34, 0.88, 0.54, 0.05))},
    {"name": "Back cover: blurb and barcode", "cover": "back",
     "description": "Reserved blurb area, credits, and a lower-corner barcode zone.",
     "zones": (("safe", _COVER_SAFE), ("blurb", (0.14, 0.16, 0.72, 0.47)),
               ("credits", (0.14, 0.69, 0.72, 0.11)),
               ("barcode", (0.68, 0.82, 0.18, 0.12))),
     "regions": (_COVER_SAFE, (0.14, 0.16, 0.72, 0.47),
                 (0.14, 0.69, 0.72, 0.11), (0.68, 0.82, 0.18, 0.12))},
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


def layout_regions_for_panels(manifest: dict, page_id: str) -> dict:
    """Stable panel IDs and sequence numbers in the page's reading order."""
    page = next((item for item in manifest.get("pages", [])
                 if item.get("id") == page_id), None)
    page_number, _ = page_panel_count(manifest, page.get("label", "") if page else "")
    if page_number is None:
        return {"panel_ids": (), "panel_sequences": ()}
    panels = manifest.get("panels", [])
    selected = [(index + 1, panel) for index, panel in enumerate(panels)
                if panel.get("status") != "orphaned"
                and (panel.get("label") or {}).get("page") == page_number]
    selected.sort(key=lambda item: (item[1].get("label") or {}).get("panel", 0))
    return {"panel_ids": tuple(panel.get("id") for _, panel in selected),
            "panel_sequences": tuple(index for index, _ in selected)}


def for_reading_order(layout: dict, reading_order: str = "rtl") -> dict:
    """The layout as read in ``reading_order``: mirrored left-right for ``rtl``, so the
    first region (the opener) sits on the right, where a manga page starts."""
    if reading_order != "rtl":
        return layout
    mirrored = {**layout, "regions": tuple((1 - x - w, y, w, h)
                                           for x, y, w, h in layout["regions"])}
    if layout.get("polygons"):
        # reversing the points keeps the winding, so mirrored polygons stay consistent
        mirrored["polygons"] = tuple(tuple((1 - x, y) for x, y in reversed(polygon))
                                     for polygon in layout["polygons"])
    return mirrored


def page_layout_availability(manifest: dict, page_id: str,
                             page_aspect: float = 1.0) -> dict:
    """Describe whether a page can receive a generated layout and why if it cannot."""
    page = next((item for item in manifest.get("pages", [])
                 if item.get("id") == page_id), None)
    if page is None:
        return {"available": False, "reason": "This project page could not be found."}

    label = str(page.get("label", "")).strip().lower()
    cover_kind = (("back" if "back" in label else "front")
                  if "cover" in label else None)
    if cover_kind:
        covers = [layout for layout in COVER_LAYOUTS if layout["cover"] == cover_kind]
        return {"available": True, "cover": cover_kind, "page_number": None,
                "panel_count": 0, "layouts": covers,
                "combinations": [(layout, {"name": "Cover guides"})
                                 for layout in covers],
                "recommendation": None, "panel_ids": (),
                "panel_sequences": ()}

    placed = sum(1 for panel in manifest.get("panels", [])
                 if (panel.get("placement") or {}).get("page") == page_id)

    page_number, count = page_panel_count(manifest, page.get("label", ""))
    reading_order = (manifest.get("project") or {}).get("reading_order", "rtl")
    script_matched = page_number is not None and count > 0
    if script_matched:
        layouts = [for_reading_order(layout, reading_order)
                   for layout in layouts_for_count(count)]
    else:
        # A page without a corresponding script still needs to be usable as a
        # hand-authored template. Offer every built-in arrangement and let the
        # artist choose its panel count.
        layouts = [for_reading_order(layout, reading_order) for layout in LAYOUTS]
    if not layouts:
        return {"available": False,
                "reason": f"No built-in layout supports {count} panels on Page {page_number}."}
    page_panels = sorted((panel for panel in manifest.get("panels", [])
                          if panel.get("status") != "orphaned"
                          and page_number is not None
                          and (panel.get("label") or {}).get("page") == page_number),
                         key=lambda panel: (panel.get("label") or {}).get("panel", 0))
    ranked, recommendation = (rank_layouts(layouts, page_panels, page_aspect)
                              if script_matched else (layouts, None))
    # Put one preview of every arrangement up front before repeating layouts with
    # alternate border treatments, so users can compare geometry without scrolling.
    combinations = [(layout, style) for style in FRAME_STYLES for layout in ranked]
    panel_refs = layout_regions_for_panels(manifest, page_id)
    return {"available": True, "page_number": page_number, "placed": placed,
            "panel_count": count or 0, "script_matched": script_matched,
            "layouts": ranked, "combinations": combinations,
            "recommendation": recommendation, **panel_refs}


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


def _polygon_area(polygon):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2)
                   in zip(polygon, polygon[1:] + polygon[:1]))) / 2


def _area_ranks(regions, polygons=None):
    """Each region's size rank, 0 (smallest) .. 1 (largest). Equal areas share a rank,
    so in a grid every frame is 0.5 and no panel is favoured by list position."""
    areas = ([_polygon_area(polygon) for polygon in polygons] if polygons
             else [width * height for _, _, width, height in regions])
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
        rank = _area_ranks(regions, layout.get("polygons"))
        # Only hinted panels count: a panel with no hint is happy in any frame.
        size = [abs(rank[i] - target)
                for i, (target, hinted) in enumerate(sizes[:len(regions)]) if hinted]
        # Angled frames are always offered, but a plain frame wins a near tie.
        return ((sum(shape) / len(shape) if shape else 0.0)
                + (sum(size) / len(size) if len(regions) > 1 and size else 0.0)
                + (0.1 if layout.get("polygons") else 0.0))

    ranked = sorted(layouts, key=score)
    informed = any(ratios) or any(hinted for _, hinted in sizes)
    return ranked, (ranked[0]["name"] if informed and ranked else None)


def _inset_polygon(points, distance):
    """Move every edge of a convex polygon ``distance`` inwards (mitred corners)."""
    winding = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2)
                  in zip(points, points[1:] + points[:1]))
    side = 1 if winding > 0 else -1
    lines = []
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        length = math.hypot(x2 - x1, y2 - y1) or 1.0
        dx, dy = (x2 - x1) / length, (y2 - y1) / length
        lines.append(((x1 - dy * side * distance, y1 + dx * side * distance), (dx, dy)))
    inset = []
    for index, ((px, py), (dx, dy)) in enumerate(lines):
        (qx, qy), (ex, ey) = lines[index - 1]
        cross = ex * dy - ey * dx
        if abs(cross) < 1e-9:
            inset.append((px, py))
            continue
        t = ((px - qx) * dy - (py - qy) * dx) / cross
        inset.append((qx + t * ex, qy + t * ey))
    return tuple(inset)


def _polygon_frame_rings(polygon, page_width, page_height, style):
    points = tuple((x * page_width, y * page_height) for x, y in polygon)
    xs, ys = [x for x, _ in points], [y for _, y in points]
    line = max(2, min(32, round(min(page_width, page_height) * style["weight"])))
    border = max(1, min(line, (max(xs) - min(xs) - 2) / 2, (max(ys) - min(ys) - 2) / 2))
    rings = [(points, _inset_polygon(points, border))]
    if style["double"]:
        gap = max(border * 2, min(max(xs) - min(xs), max(ys) - min(ys)) * 0.018)
        inner_border = max(1, border // 2)
        if min(max(xs) - min(xs), max(ys) - min(ys)) > gap * 2 + inner_border * 2:
            rings.append((_inset_polygon(points, gap),
                          _inset_polygon(points, gap + inner_border)))
    return tuple(rings)


def frame_rings(region, page_width: int, page_height: int, style: dict, index: int = 0,
                polygon=None):
    """Return outer/inner polygon pairs for the visible border rings of one frame.
    An angled ``polygon`` (normalised points) replaces the rectangle ``region``; it is
    already slanted, so the slanted style adds nothing to it."""
    if polygon:
        return _polygon_frame_rings(polygon, page_width, page_height, style)
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
        minimum_y = max(0, int(min(y for _, y in points)))
        maximum_y = min(height, int(max(y for _, y in points)) + 1)
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
    if layout.get("cover"):
        colors = {"safe": (150, 160, 170), "title": (190, 75, 75),
                  "art": (65, 125, 165), "credits": (75, 145, 105),
                  "issue": (185, 130, 50), "blurb": (65, 125, 165),
                  "barcode": (185, 130, 50)}
        guide_style = {"weight": 0.0025, "slant": 0.0, "double": False}
        for role, region in layout["zones"]:
            color = colors.get(role, (90, 110, 130))
            for outer, inner in frame_rings(region, width, height, guide_style):
                fill_polygon(outer, color)
                fill_polygon(inner, (255, 255, 255))
        return bytes(pixels)

    borderless = set(layout.get("borderless", ()))
    polygons = layout.get("polygons") or (None,) * len(layout["regions"])
    guide_style = {"weight": 0.0035, "slant": 0.0, "double": False}
    for index, (region, polygon) in enumerate(zip(layout["regions"], polygons)):
        if index in borderless:
            for outer, inner in frame_rings(region, width, height, guide_style, index,
                                            polygon):
                fill_polygon(outer, (65, 160, 185))
                fill_polygon(inner, (255, 255, 255))
            continue
        for outer, inner in frame_rings(region, width, height, style, index, polygon):
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
