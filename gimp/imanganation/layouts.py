"""Built-in page frame layouts, expressed as normalised (x, y, w, h) regions."""

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
LAYOUTS = (
    _preset("Single panel", _grid(1, 1)),
    _preset("Two stacked", _grid(1, 2)),
    _preset("Two side by side", _grid(2, 1)),
    _preset("Three: wide opener", (_top, *_two_bottom)),
    _preset("Three stacked", _grid(1, 3)),
    _preset("Four grid", _grid(2, 2)),
    _preset("Four: wide opener", (_top, *_three_bottom)),
    _preset("Five: wide opener", (_top, *_five_bottom)),
    _preset("Five grid", _five_grid),
    _preset("Six grid", _grid(2, 3)),
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


def page_layout_availability(manifest: dict, page_id: str) -> dict:
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
    layouts = layouts_for_count(count)
    if not layouts:
        return {"available": False,
                "reason": f"No built-in layout supports {count} panels on Page {page_number}."}
    return {"available": True, "page_number": page_number, "panel_count": count,
            "layouts": layouts, "combinations": layout_style_combinations(count)}


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
