"""Placement layers: the artist shows where each character goes (docs/engine-api.md).

Convention: a layer named ``placement: <Character>`` (any case, anywhere in the layer
tree, visible or hidden), with a rough blob painted where that character stands. One
layer can serve the whole page: when rendering into a frame, only its part inside that
frame is used, and a layer with no paint in the frame is skipped.

Exported masks are frame-sized transparent PNGs (painted = this character). The engine
uses them as each character's regional reference mask instead of the default
reading-order bands. GIMP-API code; stdlib + gi only.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

PREFIX = "placement:"


def placement_layers(image) -> dict[str, object]:
    """``{character name as written: layer}`` for every ``placement: …`` layer."""
    found: dict[str, object] = {}

    def walk(layers):
        for layer in layers:
            name = layer.get_name()
            if name.lower().startswith(PREFIX):
                who = name[len(PREFIX):].strip()
                if who:
                    found.setdefault(who, layer)
            if layer.is_group():
                walk(layer.get_children())

    walk(image.get_layers())
    return found


def export_placements(image, frame: tuple[int, int, int, int], characters: list[str],
                      out_dir: Path) -> dict[str, str]:
    """Crop each panel character's placement layer to ``frame`` (x, y, w, h, page px)
    and save it as a frame-sized transparent PNG. -> ``{character: path}``, only for
    characters in ``characters`` whose layer has paint inside the frame."""
    from gi.repository import Gimp, Gio

    wanted = {c.lower(): c for c in characters}
    fx, fy, fw, fh = (int(v) for v in frame)
    out: dict[str, str] = {}
    for written, layer in placement_layers(image).items():
        who = wanted.get(written.lower())
        if who is None:
            continue  # a placement for someone not in this panel
        crop = Gimp.Image.new(fw, fh, Gimp.ImageBaseType.RGB)
        try:
            copy = Gimp.Layer.new_from_drawable(layer, crop)
            crop.insert_layer(copy, None, 0)
            _, lx, ly = layer.get_offsets()
            copy.set_offsets(lx - fx, ly - fy)
            copy.set_visible(True)
            copy.set_opacity(100.0)  # the paint's own alpha is the mask
            if not copy.has_alpha():
                copy.add_alpha()
            copy.resize_to_image_size()
            crop.select_item(Gimp.ChannelOps.REPLACE, copy)  # alpha -> selection
            _, painted, *_ = Gimp.Selection.bounds(crop)
            if not painted:
                continue  # nothing painted inside this frame
            Gimp.Selection.none(crop)
            out_dir.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "_", who.lower()).strip("_") or "character"
            path = out_dir / f"placement_{slug}_{time.strftime('%Y%m%d-%H%M%S')}.png"
            Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, crop, Gio.File.new_for_path(str(path)),
                           None)
            out[who] = str(path)
        finally:
            crop.delete()
    return out
