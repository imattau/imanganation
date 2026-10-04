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
