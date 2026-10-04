"""Speech bubbles on a page: an editable vector shape plus a text layer, grouped.

A page's bubbles live in one top-level layer group, "Speech bubbles" (created the first
time, reused after). Each bubble is its own small group holding a vector layer (the
shape from ``bubbles.py``; edit it with the Paths tool) and a text layer (edit it with
the Text tool). The bubble group carries a parasite recording its template, text, box
and tail, and the script line it letters, so it can be found again and refitted.

GIMP-API code; stdlib + gi only.
"""

from __future__ import annotations

import json

import bubbles as B

BUBBLES_PARASITE = "imanganation-bubbles"  # the page's "Speech bubbles" group
BUBBLE_PARASITE = "imanganation-bubble"    # one bubble group
GROUP_NAME = "Speech bubbles"
DEFAULT_FONTS = ("Comic Neue Bold", "Comic Neue", "Sans-serif Bold", "Sans-serif")
SFX_FONTS = ("Bangers", "Bangers Regular", "Sans-serif Bold", "Sans-serif")
# Comic Neue and Bangers ship in assets/fonts (SIL OFL); scripts/gimp-dev.sh links
# them into the profile's fonts folder.


def _gimp():
    from gi.repository import Gegl, Gimp

    return Gimp, Gegl


def _parasite(item, name):
    parasite = item.get_parasite(name)
    if parasite is None:
        return None
    try:
        return json.loads(bytes(parasite.get_data()).decode("utf-8"))
    except (ValueError, UnicodeError):
        return None


def _attach(item, name, data):
    Gimp, _ = _gimp()
    item.attach_parasite(Gimp.Parasite.new(
        name, Gimp.PARASITE_PERSISTENT, list(json.dumps(data, separators=(",", ":"))
                                             .encode("utf-8"))))


def font_for(template, preferred=None):
    """The first installed font of the preferred one, then the lettering defaults."""
    Gimp, _ = _gimp()
    names = ((preferred,) if preferred else ()) + (
        SFX_FONTS if template.category == "sfx" else DEFAULT_FONTS)
    for name in names:
        font = Gimp.Font.get_by_name(name)
        if font is not None:
            return font
    return Gimp.context_get_font()


def bubbles_group(image, create=True):
    """The page's top-level "Speech bubbles" group, created at the top if missing."""
    Gimp, _ = _gimp()
    for layer in image.get_layers():
        if layer.is_group() and _parasite(layer, BUBBLES_PARASITE) is not None:
            return layer
    if not create:
        return None
    group = Gimp.GroupLayer.new(image, GROUP_NAME)
    image.insert_layer(group, None, 0)
    _attach(group, BUBBLES_PARASITE, {"role": "speech-bubbles"})
    return group


def find_bubbles(image):
    """Bubble groups on this page with their records: [(group, record)]."""
    group = bubbles_group(image, create=False)
    if group is None:
        return []
    found = []
    for layer in group.get_children():
        record = _parasite(layer, BUBBLE_PARASITE) if layer.is_group() else None
        if record is not None:
            found.append((layer, record))
    return found


def find_line_bubble(image, panel_id, line):
    """The bubble lettering ``panel_id``'s script line ``line`` (an index), if any."""
    return next((g for g, r in find_bubbles(image)
                 if r.get("panel") == panel_id and r.get("line") == line), None)


def wrap_text(text, font, size, max_width):
    """Greedy word wrap to ``max_width`` px -> (wrapped text, width, height)."""
    Gimp, _ = _gimp()

    def width_of(value):
        ok, w, h, *_ = Gimp.text_get_extents_font(value or " ", size, font)
        return w, h

    lines = []
    for paragraph in text.splitlines() or [""]:
        words, current = paragraph.split(), ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and width_of(candidate)[0] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    wrapped = "\n".join(lines)
    w, h = width_of(wrapped)
    return wrapped, w, h


def _color(rgb):
    _, Gegl = _gimp()
    color = Gegl.Color.new("black")
    color.set_rgba(*rgb, 1.0)
    return color


def _shape_layer(image, template, box, tail_tip, parent, position=0):
    """The bubble's vector layer at ``box`` (x, y, w, h in page px), inserted into
    ``parent`` (GIMP styles only attached layers)."""
    Gimp, _ = _gimp()
    x, y, w, h = box
    tip = (tail_tip[0] - x, tail_tip[1] - y) if tail_tip else None
    polygons = B.outline(template, w, h, tip)
    if not polygons:
        return None
    svg = B.svg_document(polygons, image.get_width(), image.get_height(), offset=(x, y))
    ok, paths = image.import_paths_from_string(svg, len(svg.encode("utf-8")), True, False)
    if not ok or not paths:
        raise RuntimeError("GIMP could not import the bubble outline")
    path = paths[0]
    path.set_name(f"Bubble outline · {template.label}")
    layer = Gimp.VectorLayer.new(image, path)
    layer.set_name("Bubble")
    image.insert_layer(layer, parent, position)
    fill = {"white": (1.0, 1.0, 1.0), "black": (0.05, 0.05, 0.05)}.get(template.fill)
    layer.set_enable_fill(fill is not None)
    if fill is not None:
        layer.set_fill_color(_color(fill))
    width = B.stroke_width(template, w, h)
    layer.set_enable_stroke(width > 0)
    if width > 0:
        layer.set_stroke_color(_color((0.05, 0.05, 0.05) if template.fill != "black"
                                      else (0.3, 0.3, 0.3)))
        layer.set_stroke_width(width)
        dashes = B.dash_pattern(template)
        if dashes:
            layer.set_stroke_dash_pattern(dashes)
    return layer


def _text_layer(image, template, text, box, font, size, vertical, parent):
    Gimp, _ = _gimp()
    tx, ty, tw, th = B.text_box(template, box[2], box[3])
    layer = Gimp.TextLayer.new(image, text or " ", font, size, Gimp.Unit.pixel())
    layer.set_name("Text")
    image.insert_layer(layer, parent, 0)  # GIMP styles only attached text layers
    layer.set_justification(Gimp.TextJustification.CENTER)
    layer.set_color(_color(template.text_color))
    if vertical:
        layer.set_base_direction(Gimp.TextDirection.TTB_RTL)
        layer.resize(tw, th)
        layer.set_offsets(int(box[0] + tx), int(box[1] + ty))
    else:  # a dynamic box centred in the text area (GIMP boxes align to the top)
        lw, lh = layer.get_width(), layer.get_height()
        layer.set_offsets(int(box[0] + tx + (tw - lw) / 2), int(box[1] + ty + (th - lh) / 2))
    return layer


def bubble_box_for_text(template, text, font, size, center, max_text_width=None):
    """Wrap ``text`` and size a bubble around it, centred on ``center``.
    -> (wrapped text, (x, y, w, h)).

    The wrap width makes the text block roughly the template's proportions (a tall
    bubble wraps narrow), within ``max_text_width``."""
    _, one_line, line_height = wrap_text(" ".join(text.split()), font, size, 10 ** 6)
    # x1.3: greedy wrapping leaves ragged, shorter lines than the ideal width
    width = 1.3 * (template.aspect * one_line * line_height) ** 0.5
    width = max(size * 4, min(width, max_text_width or size * 14))
    wrapped, w, h = wrap_text(text, font, size, width)
    bw, bh = B.size_for_text(template, max(w, size * 2), max(h, size), padding=size * 0.35)
    return wrapped, (center[0] - bw / 2, center[1] - bh / 2, bw, bh)


def default_size(image):
    """Lettering size for a page: about 1/64 of its height (37 px on 2400)."""
    return float(max(18, round(image.get_height() / 64)))


def insert_bubble(image, template, text, *, center=None, box=None, tail_tip=None,
                  font_name=None, size=None, vertical=False, source=None, name=None):
    """Add a bubble to the page's Speech bubbles group -> the bubble group.

    ``box`` (x, y, w, h) fixes the bubble's size (text wraps inside it); otherwise the
    bubble is sized around the text, centred on ``center``. ``source`` records the
    script line it letters ({"panel": id, "line": index}).
    """
    Gimp, _ = _gimp()
    font = font_for(template, font_name)
    size = size or default_size(image)
    if center is None and box is None:
        center = (image.get_width() / 2, image.get_height() / 2)
    if box is None:
        wrapped, box = bubble_box_for_text(template, text, font, size, center)
    else:
        _, _, tw, _ = B.text_box(template, box[2], box[3])
        wrapped, _, _ = wrap_text(text, font, size, tw)
    image.undo_group_start()
    try:
        parent = bubbles_group(image)
        group = Gimp.GroupLayer.new(image, name or "Bubble")
        image.insert_layer(group, parent, 0)
        _shape_layer(image, template, box, tail_tip, group)
        _text_layer(image, template, wrapped, box, font, size, vertical, group)
        record = {"template": template.id, "box": [round(v, 1) for v in box],
                  "tail": [round(v, 1) for v in tail_tip] if tail_tip else None,
                  "font": font.get_name(), "size": size, "vertical": vertical}
        record.update(source or {})
        _attach(group, BUBBLE_PARASITE, record)
        image.set_selected_layers([group])
    finally:
        image.undo_group_end()
    Gimp.displays_flush()
    return group


def bubble_parts(group):
    """(vector layer or None, text layer or None) of a bubble group."""
    shape = text = None
    for layer in group.get_children():
        if layer.is_text_layer():
            text = layer
        elif layer.is_vector_layer():
            shape = layer
    return shape, text


def fit_bubble(image, group):
    """Reshape a bubble around its (edited) text, keeping its centre and tail."""
    Gimp, _ = _gimp()
    record = _parasite(group, BUBBLE_PARASITE)
    if record is None:
        raise ValueError("That layer is not an Imanganation bubble")
    template = B.template_by_id(record["template"])
    if template is None:
        raise ValueError(f"Unknown bubble template {record['template']}")
    shape, text = bubble_parts(group)
    if text is None:
        raise ValueError("The bubble has no text layer")
    content = text.get_text() or ""
    font = text.get_font()
    size, _ = text.get_font_size()
    x, y, w, h = record["box"]
    center = (x + w / 2, y + h / 2)
    flat = " ".join(content.split())  # rewrap the edited words
    wrapped, box = bubble_box_for_text(template, flat, font, size, center)
    image.undo_group_start()
    try:
        position = image.get_item_position(shape) if shape else 0
        if shape is not None:
            path = shape.get_path()
            image.remove_layer(shape)
            if path is not None and path.is_valid():
                image.remove_path(path)
        tail = tuple(record["tail"]) if record.get("tail") else None
        _shape_layer(image, template, box, tail, group, position)
        text.set_text(wrapped)
        tx, ty, tw, th = B.text_box(template, box[2], box[3])
        text.set_offsets(int(box[0] + tx + (tw - text.get_width()) / 2),
                         int(box[1] + ty + (th - text.get_height()) / 2))
        record["box"] = [round(v, 1) for v in box]
        _attach(group, BUBBLE_PARASITE, record)
        image.set_selected_layers([group])
    finally:
        image.undo_group_end()
    Gimp.displays_flush()
    return group


# --- library previews and the picker ---------------------------------------------

def preview_paths(cache_dir):
    """PNG previews of every template, generated once into ``cache_dir`` -> {id: path}."""
    from pathlib import Path

    folder = Path(cache_dir) / f"bubble-previews-v{B.GEOMETRY_VERSION}"
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for template in B.templates():
        path = folder / f"{template.id}.png"
        if not path.is_file():
            path.write_bytes(B.preview_png(template))
        paths[template.id] = str(path)
    return paths


def library_rows(previews):
    """The Bubbles dock's tiles: a heading per category, a tile per template."""
    rows = []
    for category, label in B.CATEGORIES:
        rows.append(f"# {label}")
        for template in B.templates():
            if template.category == category:
                short = template.label.split(" · ")[0]
                rows.append(f"{template.id}\t{short}\t{previews.get(template.id, '')}")
    return "\n".join(rows)


def choose_bubble(category, text, previews, title="Add bubble", size=28.0):
    """The bubble picker -> (template, text, vertical, size), or None if cancelled.
    Opens on ``category``'s tab with ``text`` ready to edit; double-click inserts."""
    from gi.repository import GdkPixbuf, Gtk

    dialog = Gtk.Dialog(title=title, flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Insert", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.set_default_size(560, 520)
    box = dialog.get_content_area()
    box.set_spacing(8)
    notebook = Gtk.Notebook(scrollable=True)
    chosen = {}

    def on_selected(flow, page_templates):
        children = flow.get_selected_children()
        if children:
            chosen["template"] = page_templates[children[0].get_index()]

    def on_activated(flow, child):
        dialog.response(Gtk.ResponseType.OK)

    pages = {}
    for cat, label in B.CATEGORIES:
        page_templates = [t for t in B.templates() if t.category == cat]
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE,
                           activate_on_single_click=False, max_children_per_line=6,
                           homogeneous=True, valign=Gtk.Align.START)
        for template in page_templates:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(previews[template.id])
                image = Gtk.Image.new_from_pixbuf(pixbuf)
            except Exception:
                image = Gtk.Image.new_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
            image.set_tooltip_text(template.label)
            flow.add(image)
        flow.connect("selected-children-changed", on_selected, page_templates)
        flow.connect("child-activated", on_activated)
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.add(flow)
        notebook.append_page(scrolled, Gtk.Label(label=label))
        pages[cat] = (notebook.get_n_pages() - 1, flow, page_templates)

    def on_page(nb, page, number):
        for index, flow, page_templates in pages.values():
            if index == number:
                if not flow.get_selected_children():
                    flow.select_child(flow.get_child_at_index(0))
                on_selected(flow, page_templates)

    notebook.connect("switch-page", on_page)
    box.pack_start(notebook, True, True, 0)
    grid = Gtk.Grid(column_spacing=12, row_spacing=6, margin=6)
    entry = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                         left_margin=4, right_margin=4, top_margin=4, bottom_margin=4)
    entry.get_buffer().set_text(text or "")
    frame = Gtk.ScrolledWindow(hexpand=True)
    frame.set_size_request(-1, 64)
    frame.set_shadow_type(Gtk.ShadowType.IN)
    frame.add(entry)
    size_spin = Gtk.SpinButton.new_with_range(8, 400, 1)
    size_spin.set_value(size)
    vertical = Gtk.CheckButton(label="Vertical text")
    grid.attach(Gtk.Label(label="Text", xalign=0.0, valign=Gtk.Align.START), 0, 0, 1, 1)
    grid.attach(frame, 1, 0, 3, 1)
    grid.attach(Gtk.Label(label="Size", xalign=0.0), 0, 1, 1, 1)
    grid.attach(size_spin, 1, 1, 1, 1)
    grid.attach(vertical, 2, 1, 1, 1)
    box.pack_start(grid, False, False, 0)
    dialog.show_all()
    index, flow, page_templates = pages.get(category, pages["speech"])
    notebook.set_current_page(index)
    flow.select_child(flow.get_child_at_index(0))
    on_selected(flow, page_templates)
    try:
        if dialog.run() != Gtk.ResponseType.OK or "template" not in chosen:
            return None
        buffer = entry.get_buffer()
        value = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        return (chosen["template"], value.strip(), vertical.get_active(),
                size_spin.get_value())
    finally:
        dialog.destroy()
