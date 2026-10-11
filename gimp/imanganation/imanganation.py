#!/usr/bin/env python3
"""imanganation GIMP 3 plug-in.

Runs inside GIMP's own Python (the flatpak ships 3.14 with GI bindings but no
numpy/PIL/pydantic), so this file is stdlib + gi only. All generation stays in the
imanganation engine (``manganation serve``); the plug-in is a thin HTTP client.

Workflow: the artist builds every page in GIMP — by hand or from an imanganation
panel layout template. They click into a frame (Fuzzy Select on the template),
run "Render Panel into Frame", and the engine renders the next script panel at
that frame's proportions. Regular panels sit below the frame template; overlapping
and borderless panels sit above it so they cover the underlying frame ink. Cover
layouts are colored safe-area and typography guides, not script panels.

Panels are identified by their 1-based position in the script (``seq``), not
page/panel, because scripts can repeat panel numbers within a page (e.g. after
``CUT TO:``). A project container (``project.json``) renders through the engine's
inline form (project id + panel object); a legacy folder through ``panels.json`` +
``seq``, with images at ``panels/{seq:03d}*.png``.

Template convention: a layer whose name starts with "template" holds selectable
frame lines/guides. Normal panels go directly beneath it (a Normal-mode template is
switched to Multiply); overlay panels are inserted above it but below foreground
frame ink. The template is re-selected after each placement so the next Fuzzy Select
click samples the frames, not the last render.
"""

import copy
import hashlib
import json
import os
import random
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gimp", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import (  # noqa: E402
    Gdk,
    GdkPixbuf,
    Gegl,
    Gimp,
    Gio,
    GLib,
    GObject,
    Gtk,
)

try:
    import bubbles as bubble_templates
    import engine_ui
    import export_formats
    import lettering
    import tone_effects
    import services as engine_services
    import setup_ui
    from layouts import (COVER_LAYOUTS, frame_rings, layout_preview_rgb,
                         page_layout_availability)
    from panel_ui import _panel_label as panel_label
    from panel_ui import (
        build_docks,
        build_gallery,
        blank_size,
        job_fraction,
        job_status_text,
        build_welcome_docks,
        character_row_id,
        location_row_id,
        prop_row_id,
        rgb_png,
    )
    from project_store import (
        ProjectFileError,
        ASPECT_CHOICES,
        PANEL_SIZES,
        SHOT_CHOICES,
        add_cover,
        add_panel,
        edit_panel_story,
        adopt_script_fingerprints,
        apply_field_edit,
        delete_character,
        delete_location,
        delete_page,
        delete_panel,
        duplicate_panel,
        add_prop,
        delete_prop,
        rename_character,
        find_location,
        find_prop,
        load_project,
        location_key,
        new_id,
        new_project_document,
        panels_at_location,
        panels_with_prop,
        prop_key,
        project_from_script,
        record_take,
        reorder_pages,
        reorder_page_panels,
        reparse_script,
        save_project,
        script_page_number,
        script_fingerprint,
        retarget_character_version,
        set_active_take,
        set_character_version,
    )
    from script_canonical import looks_canonical as script_looks_canonical
    from script_canonical import parse as parse_script_text
    from script_canonical import serialize as serialize_script_text
except ImportError:  # Keep older single-file plug-in installs usable for legacy projects.
    ProjectFileError = ValueError
    project_from_script = parse_script_text = serialize_script_text = script_looks_canonical = None
    lettering = bubble_templates = setup_ui = engine_services = engine_ui = None
    export_formats = None
    tone_effects = None
    load_project = record_take = save_project = apply_field_edit = None
    delete_character = delete_location = find_location = None
    location_key = panels_at_location = None
    new_id = None
    new_project_document = None
    build_docks = None
    job_fraction = job_status_text = None
    prop_row_id = prop_key = None
    blank_size = None
    build_gallery = None
    character_row_id = location_row_id = None
    panel_label = None
    rgb_png = None
    build_welcome_docks = None
    frame_rings = layout_preview_rgb = page_layout_availability = None
    COVER_LAYOUTS = None

PROC_RENDER = "plug-in-imanganation-render-panel"
PROC_NEXT = "plug-in-imanganation-place-next-panel"
PROC_PLACE = "plug-in-imanganation-place-panel"
PROC_REFINE = "plug-in-imanganation-refine-panel"
PROC_REGEN = "plug-in-imanganation-regenerate-panel"
PROC_SETREF = "plug-in-imanganation-set-character-reference"
PROC_SET_LOCATION_REF = "plug-in-imanganation-set-location-reference"
PROC_SET_PROP_REF = "plug-in-imanganation-set-prop-reference"
PROC_INPAINT = "plug-in-imanganation-inpaint-selection"
PROC_STAGE = "plug-in-imanganation-develop-panel-stage"
DEVELOPMENT_PHASES = (
    ("composition", "Panel composition",
     "Thumbnail the read: camera, silhouette, gesture, and focal point. Select most "
     "or all of the frame for a broad change; use a smaller selection for one pose."),
    ("blocking", "Rough character blocking",
     "Establish who stands where, their scale, pose, and eyeline before polishing detail. "
     "Select the character's area. Name them to use their project reference."),
    ("setting", "Background and perspective",
     "Build the location around the action: foreground, midground, background, depth, "
     "time of day, and atmosphere. Select the background area to work on."),
    ("action", "Acting, interaction, and props",
     "Clarify the beat with gestures, contact, overlapping poses, hands, and shared props. "
     "Select the whole interaction and name everyone involved."),
    ("linework", "Linework and cleanup",
     "Tighten the focal contours, face, hands, costume edges, or important prop. Keep "
     "the selection focused on the shape to clean up and describe the line quality."),
    ("shadows", "Black shapes and shadow design",
     "Clarify form and mood with cast shadows, dark masses, rim light, or reflected light. "
     "Select the surfaces to adjust and name the light source."),
    ("effects", "Effects and final polish",
     "Add weather, impact, motion, or atmosphere where it helps the story beat. Keep "
     "effects on their own focused passes. After this, use GIMP's screentone and bubble "
     "tools for tones and lettering."),
)
PROC_STATUS = "plug-in-imanganation-engine-status"
PROC_PROJECT_DOCKS = "plug-in-imanganation-project-docks"
PROC_CLOSE_PROJECT = "plug-in-imanganation-close-project"
PROC_RELOAD_SCRIPT = "plug-in-imanganation-reload-script"
PROC_RESTART_WORKSPACE = "plug-in-imanganation-restart-workspace"
PROC_PAGE_LAYOUT = "plug-in-imanganation-page-layout"
PROC_SCREENTONE = "plug-in-imanganation-screentone"
PROC_SPEED_LINES = "plug-in-imanganation-speed-lines"
PROC_IMPACT_BURST = "plug-in-imanganation-impact-burst"
PROC_COVER_DESIGNER = "plug-in-imanganation-cover-designer"
PROC_EXPORT_PROJECT = "plug-in-imanganation-export-project"
PROC_AUTOSTART = "extension-imanganation-ui"
PROC_NEW_PROJECT_MANUAL = "plug-in-imanganation-new-project-manual"
PROC_NEW_PROJECT = "plug-in-imanganation-new-project"
PROC_SETUP_MODELS = "plug-in-imanganation-setup-models"
PROC_RENDER_ENGINE = "plug-in-imanganation-render-engine"
DOCK_PROJECT = "project"
DOCK_INSPECTOR = "inspector"
DOCK_FILMSTRIP = "filmstrip"
DOCK_SCRIPT = "script"
DOCK_CHARACTERS = "characters"
DOCK_PANEL = "panel"
DOCK_BUBBLES = "bubbles"  # the bubble library: click a bubble to add it to the page
DOCK_GALLERY = "gallery"  # the selection's pictures: takes, references; click to use
DOCK_IDS = (DOCK_PROJECT, DOCK_INSPECTOR, DOCK_FILMSTRIP, DOCK_SCRIPT,
            DOCK_CHARACTERS, DOCK_PANEL, DOCK_BUBBLES, DOCK_GALLERY)
DOCK_ICON_NAMES = {
    DOCK_PROJECT: "imanganation-project",
    DOCK_INSPECTOR: "imanganation-context",
    DOCK_FILMSTRIP: "imanganation-pages",
    DOCK_SCRIPT: "imanganation-script",
    DOCK_CHARACTERS: "imanganation-characters",
    DOCK_PANEL: "imanganation-panel",
    DOCK_BUBBLES: "imanganation-bubbles",
    DOCK_GALLERY: "imanganation-gallery",
}
DOCK_ACTIONS = {
    DOCK_PROJECT: "plug-in-imanganation-dock-project-action",
    DOCK_INSPECTOR: "plug-in-imanganation-dock-inspector-generate",
    DOCK_FILMSTRIP: "plug-in-imanganation-dock-filmstrip-add-page",
    DOCK_SCRIPT: "plug-in-imanganation-dock-script-refresh",
    DOCK_CHARACTERS: "plug-in-imanganation-dock-characters-refresh",
    DOCK_PANEL: "plug-in-imanganation-dock-panel-refresh",
}
DOCK_OPEN_PROJECT = "plug-in-imanganation-dock-open-project"
DOCK_CLOSE_PROJECT = "plug-in-imanganation-dock-close-project"
DOCK_RELOAD_SCRIPT = "plug-in-imanganation-dock-reload-script"
DOCK_LOAD_SCRIPT = "plug-in-imanganation-dock-load-script"
DOCK_OPEN_PAGE = "plug-in-imanganation-dock-open-page"  # Context's "Open page" button
DOCK_NEW_PROJECT = "plug-in-imanganation-dock-new-project"
DOCK_NEW_MANUAL_PROJECT = "plug-in-imanganation-dock-new-manual-project"
DOCK_SETUP_MODELS = "plug-in-imanganation-dock-setup-models"  # the workspace shows it
DOCK_RENDER_ENGINE = "plug-in-imanganation-dock-render-engine"  # the workspace shows it
DOCK_DESIGN_CHARACTER = "plug-in-imanganation-dock-design-character"
# Project tree right-click menus (one-string procedures: the row id)
DOCK_NEW_CHARACTER = "plug-in-imanganation-dock-new-character"
DOCK_DESIGN_CHARACTER_ITEM = "plug-in-imanganation-dock-design-character-item"
DOCK_DELETE_CHARACTER = "plug-in-imanganation-dock-delete-character"
# Locations, like characters: Context buttons, and the tree's right-click menus
DOCK_DESIGN_LOCATION = "plug-in-imanganation-dock-design-location"
DOCK_OPEN_CHARACTER_IMAGE = "plug-in-imanganation-dock-open-character-image"
DOCK_OPEN_LOCATION_IMAGE = "plug-in-imanganation-dock-open-location-image"
DOCK_NEW_LOCATION = "plug-in-imanganation-dock-new-location"
DOCK_DESIGN_LOCATION_ITEM = "plug-in-imanganation-dock-design-location-item"
DOCK_DELETE_LOCATION = "plug-in-imanganation-dock-delete-location"
# Page strip / Project tree: right-click Delete page… and drag to reorder
DOCK_DELETE_PAGE = "plug-in-imanganation-dock-delete-page"
DOCK_REORDER_PAGES = "plug-in-imanganation-dock-reorder-pages"
# Project tree: right-click Add panel… (heading, page or panel row) / Delete panel…
DOCK_ADD_PANEL = "plug-in-imanganation-dock-add-panel"
DOCK_DELETE_PANEL = "plug-in-imanganation-dock-delete-panel"
DOCK_DUPLICATE_PANEL = "plug-in-imanganation-dock-duplicate-panel"
DOCK_ADD_COVER = "plug-in-imanganation-dock-add-cover"
DOCK_OPEN_CHARACTER_VERSION = "plug-in-imanganation-dock-open-character-version"
DOCK_ADD_COVER_PAGE = "plug-in-imanganation-dock-add-cover-page"
DOCK_DESIGN_VARIANT = "plug-in-imanganation-dock-design-variant"
# Context: a panel character's "Reference…" (item "<panel id>:<index>")
DOCK_CHARACTER_VERSION = "plug-in-imanganation-dock-character-version"
# Context: a take's "Make active" (item "<panel id>:<take id>")
DOCK_ACTIVATE_TAKE = "plug-in-imanganation-dock-activate-take"
# Context (a page, or a panel on one): render every panel waiting on that page
DOCK_GENERATE_PAGE = "plug-in-imanganation-dock-generate-page"
DOCK_GENERATE_LAYOUT = "plug-in-imanganation-dock-generate-page-layout"
DOCK_STORYBOARD = "plug-in-imanganation-dock-storyboard-review"
# Speech bubbles: a script line's Bubble… (item "<panel id>:<line>"), a page's free
# Bubble…, a Bubbles dock tile (item: template id), Fit bubble to text
DOCK_BUBBLE_LINE = "plug-in-imanganation-dock-bubble-line"
DOCK_NEW_BUBBLE = "plug-in-imanganation-dock-new-bubble"
DOCK_BUBBLE_ITEM = "plug-in-imanganation-dock-bubbles-item"
DOCK_FIT_BUBBLE = "plug-in-imanganation-dock-fit-bubble"
# Windows > Imanganation: reopen a closed dock (the host keeps closed docks closed).
DOCK_SHOW = {dock: f"plug-in-imanganation-show-dock-{dock}" for dock in DOCK_IDS}
DOCK_MENU_LABELS = {
    DOCK_PROJECT: "_Project", DOCK_INSPECTOR: "_Context", DOCK_BUBBLES: "_Bubbles",
    DOCK_FILMSTRIP: "P_ages", DOCK_SCRIPT: "_Script", DOCK_GALLERY: "_Gallery",
    DOCK_CHARACTERS: "_Character Bible", DOCK_PANEL: "P_anel",
}
DOCK_GALLERY_ITEM = "plug-in-imanganation-dock-gallery-item"
# Context buttons for the selected character / prop / location (the same actions as the
# Project tree's right-click menus), and a reference row's Manage… (item: its version)
DOCK_RENAME_CHARACTER_BUTTON = "plug-in-imanganation-dock-rename-character-button"
DOCK_DELETE_CHARACTER_BUTTON = "plug-in-imanganation-dock-delete-character-button"
DOCK_VARIANT_BUTTON = "plug-in-imanganation-dock-another-reference-button"
DOCK_DELETE_PROP_BUTTON = "plug-in-imanganation-dock-delete-prop-button"
DOCK_DELETE_LOCATION_BUTTON = "plug-in-imanganation-dock-delete-location-button"
DOCK_MANAGE_REFERENCE = "plug-in-imanganation-dock-manage-reference"
# Props, like locations: Context's Design prop, the tree's right-click menus, and a prop's
# image tiles in the Gallery (item "img:<file name>")
DOCK_DESIGN_PROP = "plug-in-imanganation-dock-design-prop"
DOCK_NEW_PROP = "plug-in-imanganation-dock-new-prop"
DOCK_DESIGN_PROP_ITEM = "plug-in-imanganation-dock-design-prop-item"
DOCK_DELETE_PROP = "plug-in-imanganation-dock-delete-prop"
DOCK_OPEN_PROP_IMAGE = "plug-in-imanganation-dock-open-prop-image"
DOCK_PROP_IMAGE_DEFAULT = "plug-in-imanganation-dock-prop-image-default"
DOCK_PROP_IMAGE_DELETE = "plug-in-imanganation-dock-prop-image-delete"
# A character's reference tile in the Gallery: right-click menu (item "ref:<version>")
DOCK_REF_DEFAULT = "plug-in-imanganation-dock-reference-default"
DOCK_REF_RENAME = "plug-in-imanganation-dock-reference-rename"
DOCK_REF_DELETE = "plug-in-imanganation-dock-reference-delete"
DOCK_ITEMS = {
    DOCK_PROJECT: "plug-in-imanganation-dock-project-item",
    DOCK_INSPECTOR: "plug-in-imanganation-dock-inspector-field",  # an edited field
    DOCK_FILMSTRIP: "plug-in-imanganation-dock-filmstrip-item",
    DOCK_SCRIPT: "plug-in-imanganation-dock-script-item",
    DOCK_CHARACTERS: "plug-in-imanganation-dock-characters-item",
}
REF_MAX = 1024  # reference export cap; the CLIP encoder only sees 224-448 px anyway
LOCATION_REF_MAX = 1344  # a location's long side, as the engine designs them
PARASITE = "imanganation-panelspec"
LAYOUT_PARASITE = "imanganation-layout"
PROJECT_PARASITE = "imanganation-project"
PANEL_PARASITE = "imanganation-panel"
TAKE_PARASITE = "imanganation-take"
# On a reference image opened from Context: {"project": root, "name": who / place}
LOCATION_PARASITE = "imanganation-location"
PROP_PARASITE = "imanganation-prop"
CHARACTER_PARASITE = "imanganation-character"
CURSOR_FILE = "gimp_cursor.json"  # per project: which panel comes next
ENGINE_URL = "http://127.0.0.1:8790"
RENDER_TIMEOUT = 600  # seconds
# Inside the Flatpak the plug-in is sandboxed: by default it starts the app's own engine
# (which runs its own ComfyUI); a host checkout is started through flatpak-spawn --host
# (the manifest grants org.freedesktop.Flatpak). services.py decides which.
IN_FLATPAK = Path("/.flatpak-info").exists()
# IMANGANATION_AUTOSTART=0 stops the workspace starting the engine and ComfyUI.
ENGINE = (engine_services.find_engine(in_flatpak=IN_FLATPAK, plugin_file=Path(__file__))
          if engine_services is not None else None)
ENGINE_FOUND = ENGINE is not None and ENGINE.mode != "none"
COMFY_URL = f"http://127.0.0.1:{os.environ.get('COMFY_PORT', '8188')}"

_DOCK_PLUGIN = None
_DOCK_CONTEXT = {}
_PAGE_THUMBNAILS = {}
_PAGE_DISPLAYS = {}
_PAGE_IMAGES = {}


class EngineError(Exception):
    pass


def _http(method, url, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail", exc.reason)
        except ValueError:
            detail = exc.reason
        raise EngineError(f"engine refused the request: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        how = ("Try Windows → Imanganation → Restart Workspace" if IN_FLATPAK else
               "Try Windows → Imanganation → Restart Workspace, or run: "
               "uv run manganation serve")
        raise EngineError(f"imanganation engine not reachable at {url} ({exc}). "
                          f"{how}") from exc


def _ping(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return f"ok {resp.status}"
    except Exception as exc:  # report, never fail the placement on a ping
        return f"unreachable ({exc})"


CANCELLED = "Cancelled"


def _error(procedure, msg):
    if msg == CANCELLED:  # the artist's own choice: not an error dialog
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
    return procedure.new_return_values(
        Gimp.PDBStatusType.EXECUTION_ERROR,
        GLib.Error.new_literal(GLib.quark_from_string("imanganation"), msg, 0))


def _success(procedure, layer):
    retvals = procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
    # new_return_values pre-allocates every declared return slot; overwrite ours.
    retvals.remove(1)
    retvals.insert(1, GObject.Value(Gimp.Layer, layer))
    return retvals


def _dialog(procedure, config, name):
    gi.require_version("GimpUi", "3.0")
    from gi.repository import GimpUi

    GimpUi.init(name)
    dialog = GimpUi.ProcedureDialog(procedure=procedure, config=config)
    dialog.fill(None)
    ok = dialog.run()
    dialog.destroy()
    return ok


def _suggested_development_phase(meta):
    """Advance through manga panel development, holding at the last phase."""
    history = meta.get("stage_history") or []
    previous = (history[-1].get("phase") if history else
                (meta.get("stage") or {}).get("phase"))
    ids = [phase[0] for phase in DEVELOPMENT_PHASES]
    if previous not in ids:
        return ids[0]
    return ids[min(ids.index(previous) + 1, len(ids) - 1)]


def _development_stage_dialog(meta):
    """A guided phase chooser with concrete drawing/selection advice per stage."""
    dialog = Gtk.Dialog(title="Develop Panel in Stages", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Generate Stage…", Gtk.ResponseType.OK)
    dialog.set_default_size(560, 500)
    content = dialog.get_content_area()
    content.set_spacing(8)
    content.set_margin_top(12)
    content.set_margin_bottom(12)
    content.set_margin_start(12)
    content.set_margin_end(12)

    chooser = Gtk.ComboBoxText()
    for ident, title, _guide in DEVELOPMENT_PHASES:
        chooser.append(ident, title)
    chooser.set_active_id(_suggested_development_phase(meta))
    completed = {item.get("phase") for item in (meta.get("stage_history") or [])}
    if not completed and meta.get("stage"):
        completed.add(meta["stage"].get("phase"))
    completed_names = [title for ident, title, _hint in DEVELOPMENT_PHASES
                       if ident in completed]
    progress = Gtk.Label(label=("Completed: " + " → ".join(completed_names)
                                if completed_names else
                                "Start from the rendered panel. Phases can be skipped "
                                "or repeated."))
    progress.set_xalign(0)
    progress.set_line_wrap(True)
    guide = Gtk.Label()
    guide.set_xalign(0)
    guide.set_line_wrap(True)
    guide.set_max_width_chars(78)

    prompt = Gtk.TextView()
    prompt.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    prompt.set_size_request(-1, 110)
    prompt.get_buffer().set_text("")
    prompt_scroll = Gtk.ScrolledWindow()
    prompt_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    prompt_scroll.set_min_content_height(110)
    prompt_scroll.add(prompt)

    existing = meta.get("characters") or []
    if isinstance(existing, str):
        character_names = _name_list(existing)
    else:
        character_names = [c.get("name", "") if isinstance(c, dict) else str(c)
                           for c in existing]
    characters = Gtk.Entry()
    characters.set_placeholder_text("Character names for reference guidance; blank = none")

    strength = Gtk.SpinButton.new_with_range(0.1, 1.0, 0.05)
    strength.set_value(0.85)
    grow = Gtk.SpinButton.new_with_range(0, 256, 1)
    grow.set_value(8)

    grid = Gtk.Grid(column_spacing=12, row_spacing=8)
    grid.attach(progress, 0, 0, 2, 1)
    grid.attach(Gtk.Label(label="Development phase"), 0, 1, 1, 1)
    grid.attach(chooser, 1, 1, 1, 1)
    grid.attach(Gtk.Label(label="Focus"), 0, 2, 1, 1)
    grid.attach(guide, 1, 2, 1, 1)
    grid.attach(Gtk.Label(label="What should change?"), 0, 3, 1, 1)
    grid.attach(prompt_scroll, 1, 3, 1, 1)
    grid.attach(Gtk.Label(label="Characters in the selected area"), 0, 4, 1, 1)
    grid.attach(characters, 1, 4, 1, 1)
    grid.attach(Gtk.Label(label="Change strength"), 0, 5, 1, 1)
    grid.attach(strength, 1, 5, 1, 1)
    grid.attach(Gtk.Label(label="Mask blend (px)"), 0, 6, 1, 1)
    grid.attach(grow, 1, 6, 1, 1)
    content.add(grid)

    def update_guide(_combo):
        selected = chooser.get_active_id()
        phase = next((item for item in DEVELOPMENT_PHASES if item[0] == selected),
                     DEVELOPMENT_PHASES[0])
        guide.set_text(phase[2])
        # Character staging and interaction benefit from identity references by default;
        # the artist can clear or edit the list for any phase.
        if selected in {"blocking", "action", "linework"} and character_names:
            if not characters.get_text().strip():
                characters.set_text(", ".join(character_names))

    chooser.connect("changed", update_guide)
    update_guide(chooser)
    dialog.show_all()
    response = dialog.run()
    if response != Gtk.ResponseType.OK:
        dialog.destroy()
        return None
    start, end = prompt.get_buffer().get_bounds()
    values = {
        "phase": chooser.get_active_id() or DEVELOPMENT_PHASES[0][0],
        "prompt": prompt.get_buffer().get_text(start, end, True).strip(),
        "characters": characters.get_text().strip(),
        "denoise": strength.get_value(),
        "grow": grow.get_value_as_int(),
    }
    dialog.destroy()
    return values


def _find_template(image):
    def layout_template(layers):
        for layer in layers:
            parasite = layer.get_parasite(LAYOUT_PARASITE)
            if parasite is not None:
                try:
                    if json.loads(bytes(parasite.get_data())).get("role") == "selection-template":
                        return layer
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            if layer.is_group():
                found = layout_template(layer.get_children())
                if found:
                    return found
        return None

    generated = layout_template(image.get_layers())
    if generated is not None:
        return generated

    def walk(layers):
        for layer in layers:
            if layer.get_name().lower().startswith("template"):
                return layer
            if layer.is_group():
                found = walk(layer.get_children())
                if found:
                    return found
        return None

    return walk(image.get_layers())


def _tag_panel_group(group, take_ref):
    if group is None or not take_ref:
        return
    panel_ref = {"project": take_ref["project"], "panel": take_ref["panel"]}
    group.attach_parasite(Gimp.Parasite.new(
        PANEL_PARASITE, Gimp.PARASITE_PERSISTENT,
        list(json.dumps(panel_ref, separators=(",", ":")).encode())))


def _find_panel_group(image, reference, empty_only=False):
    if not reference:
        return None

    def walk(layers):
        for layer in layers:
            if layer.is_group():
                parasite = layer.get_parasite(PANEL_PARASITE)
                if parasite is not None:
                    try:
                        stored = json.loads(bytes(parasite.get_data()))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        stored = {}
                    if (stored.get("project") == reference.get("project")
                            and stored.get("panel") == reference.get("panel")
                            and (not empty_only or not layer.get_children())):
                        return layer
                found = walk(layer.get_children())
                if found is not None:
                    return found
        return None

    return walk(image.get_layers())


def _layout_overlay_for_panel(template, take_ref, seq):
    """Find the saved overlapping/borderless region for this script panel."""
    parasite = template.get_parasite(LAYOUT_PARASITE) if template is not None else None
    if parasite is None:
        return None
    try:
        data = json.loads(bytes(parasite.get_data()))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    panel_id = (take_ref or {}).get("panel")
    for entry in data.get("overlays", []):
        if panel_id and entry.get("panel_id") == panel_id:
            return entry
        if seq is not None and entry.get("sequence") == seq:
            return entry
    return None


def _clear_layout_region(image, template, region):
    """Remove base frame/guide ink underneath an overlay; its own ink is foreground."""
    if not region or template is None:
        return
    width, height = image.get_width(), image.get_height()
    x, y, w, h = region
    x1, y1, x2, y2 = x * width, y * height, (x + w) * width, (y + h) * height
    selection = Gimp.Selection.save(image)
    try:
        image.select_polygon(Gimp.ChannelOps.REPLACE,
                             [x1, y1, x2, y1, x2, y2, x1, y2])
        template.edit_clear()
    finally:
        image.select_item(Gimp.ChannelOps.REPLACE, selection)
        image.remove_channel(selection)


def _overlay_layer_position(image, template):
    """Immediately below layout ink, so each later overlay stacks over earlier ones."""
    parent = template.get_parent()
    def walk(layers):
        for layer in layers:
            if (layer.get_name().startswith("Imanganation Layout Ink -")
                    and layer.get_parent() == parent):
                return layer
            if layer.is_group():
                found = walk(layer.get_children())
                if found is not None:
                    return found
        return None
    ink = walk(image.get_layers())
    return (image.get_item_position(ink) + 1 if ink is not None
            else image.get_item_position(template))


def _place(image, panel_file, spec, seq=None, render=None, take_ref=None, frame=None):
    """Load a panel as a layer in its own group, fitted to the selection if any."""
    label = ("Cover" if spec.get("page") == 0
             else f"Panel {spec.get('page', '?')}.{spec.get('panel', '?')}")
    if seq is not None:
        label = f"{seq:03d} {label}"

    image.undo_group_start()

    # Reuse a previously defined empty semantic panel group, if present.
    template = _find_template(image)
    overlay = _layout_overlay_for_panel(template, take_ref, seq)
    group = _find_panel_group(image, take_ref, empty_only=True)
    if group is None:
        group = Gimp.GroupLayer.new(image, label)
        if template is not None:
            # An opaque white-page template would hide panels beneath it; Multiply keeps
            # the black frame lines and lets the panel show through.
            if template.get_mode() == Gimp.LayerMode.NORMAL:
                template.set_mode(Gimp.LayerMode.MULTIPLY)
            image.insert_layer(group, template.get_parent(),
                               (_overlay_layer_position(image, template) if overlay
                                else image.get_item_position(template) + 1))
        else:
            image.insert_layer(group, None, 0)
        _tag_panel_group(group, take_ref)
    elif template is not None:
        # Reused empty semantic groups may have been created before the layout was
        # chosen; put them in the correct slot too.
        image.remove_layer(group)
        image.insert_layer(group, template.get_parent(),
                           (_overlay_layer_position(image, template) if overlay
                            else image.get_item_position(template) + 1))

    layer = Gimp.file_load_layer(Gimp.RunMode.NONINTERACTIVE, image, panel_file)
    layer.set_name(f"{label} render")
    image.insert_layer(layer, group, 0)

    # Fit to the saved semantic frame when available, otherwise use the current
    # selection. The panel group's mask clips the saved frame's actual shape.
    _, selection_non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
    non_empty = selection_non_empty
    if frame is not None:
        x1, y1, fw, fh = frame
        x2, y2 = x1 + fw, y1 + fh
        non_empty = fw > 0 and fh > 0
    if non_empty:
        fw, fh = x2 - x1, y2 - y1
        lw, lh = layer.get_width(), layer.get_height()
        s = max(fw / lw, fh / lh)
        nw, nh = round(lw * s), round(lh * s)
        layer.scale(nw, nh, False)
        layer.set_offsets(x1 + (fw - nw) // 2, y1 + (fh - nh) // 2)
        if frame is None and selection_non_empty:
            layer.add_mask(layer.create_mask(Gimp.AddMaskType.SELECTION))
    else:
        _, x1, y1 = layer.get_offsets()

    stored = dict(spec)
    if panel_file.get_path():
        stored["file"] = panel_file.get_path()  # the exact take this layer shows
    if seq is not None:
        stored["seq"] = seq
    if render is not None:
        stored["render"] = render
    layer.attach_parasite(Gimp.Parasite.new(
        PARASITE, Gimp.PARASITE_PERSISTENT, list(json.dumps(stored).encode())))
    if take_ref:
        layer.attach_parasite(Gimp.Parasite.new(
            TAKE_PARASITE, Gimp.PARASITE_PERSISTENT,
            list(json.dumps(take_ref, separators=(",", ":")).encode())))

    if template is not None and overlay is not None:
        _clear_layout_region(image, template, overlay.get("region"))

    # Hand the template back so the next Fuzzy Select click samples the frames.
    if template is not None:
        image.set_selected_layers([template])

    image.undo_group_end()
    Gimp.displays_flush()
    return layer


def _script_position(spec):
    """'script page 2, panel 1' for a script position; the cover is page 0."""
    if spec.get("page") == 0:
        return "the cover"
    return f"script page {spec['page']}, panel {spec['panel']}"


def _container_spec(panel):
    """A container panel as the spec dict the placement and cursor code read
    (``page``/``panel`` from its script label; characters, dialogue, sfx as is)."""
    label = panel.get("label", {})
    return dict(panel, page=label.get("page"), panel=label.get("panel"))


def _render_body(root, manifest, seq):
    """Engine /jobs body naming the panel: inline for a container (the engine needs no
    panels.json), legacy project_dir + seq otherwise. Frame size is added by callers."""
    if manifest is None:
        return {"project_dir": str(root), "seq": seq}
    body = {"project": manifest["project"]["id"], "panel": manifest["panels"][seq - 1],
            "reading_order": manifest["project"].get("reading_order", "rtl")}
    if engine_ui is not None:  # the project's engine and face pass (Render Engine dialog)
        body.update(engine_ui.job_options(manifest))
    return body


def _engine_project(root, manifest=None):
    """How the engine names the project: the container id, or a legacy folder path."""
    if manifest is None:
        manifest = _manifest_for(root)
    if manifest is not None:
        return {"project": manifest["project"]["id"]}
    return {"project_dir": str(root)}


def _refine_origin(root, manifest, seq, source):
    """Container refine: the size (and render prompt) of the take this one descends
    from, so the engine scales from the original render and never compounds."""
    panel_id = manifest["panels"][seq - 1]["id"]
    take_id = _registered_take_id(root, manifest, source, panel_id)
    takes = manifest.get("takes", {})
    take = takes.get(takes.get(take_id, {}).get("origin") or take_id) if take_id else None
    if not take or not take.get("width") or not take.get("height"):
        width, height = _source_dimensions(source)  # not yet a take: it is its own origin
        return {"origin_width": width, "origin_height": height}
    origin = {"origin_width": take["width"], "origin_height": take["height"]}
    prompt = (take.get("engine") or {}).get("prompt")
    if prompt:
        origin["prompt"] = prompt
    return origin


def _load_project(config):
    """-> (root, panel specs, seq, explicit, manifest) or raises ValueError.

    The container is authoritative for the script, project identity and cursor; a
    folder without project.json is a legacy project read from panels.json.
    """
    project = config.get_property("project-dir")
    if project is None:
        raise ValueError("Choose a project folder (with project.json and panels.json).")
    root = Path(project.get_path())
    manifest = None
    manifest_path = root / "project.json"
    if manifest_path.exists():
        if load_project is None:
            raise ValueError("This plug-in install is missing project_store.py")
        try:
            manifest = load_project(root)
        except ProjectFileError as exc:
            raise ValueError(str(exc)) from exc
    if manifest is not None:
        panels = [_container_spec(panel) for panel in manifest["panels"]]
    else:
        try:
            panels = json.loads((root / "panels.json").read_text())["panels"]
        except (OSError, ValueError, KeyError) as exc:
            raise ValueError(f"Cannot read {root / 'panels.json'}: {exc}") from exc

    explicit = config.get_property("panel-number")
    if explicit:
        seq = explicit
    elif manifest is not None:
        next_id = manifest["cursor"].get("next_panel")
        ids = [panel["id"] for panel in manifest["panels"]]
        if next_id is None:
            seq = len(ids) + 1
        elif next_id in ids:
            seq = ids.index(next_id) + 1
        else:
            raise ValueError(f"project cursor points at unknown panel {next_id}")
    else:
        try:
            seq = json.loads((root / CURSOR_FILE).read_text())["next"]
        except (OSError, ValueError, KeyError):
            seq = 1
    if not 1 <= seq <= len(panels):
        raise ValueError(f"All {len(panels)} panels placed. "
                         "Set a panel number to place one again.")
    return root, panels, seq, explicit, manifest


def _advance(root, panels, seq, explicit, spec, manifest=None):
    if not explicit:
        if manifest is not None:
            manifest["cursor"]["next_panel"] = (
                manifest["panels"][seq]["id"] if seq < len(panels) else None)
            manifest["project"]["modified"] = datetime.now().astimezone().isoformat(
                timespec="seconds")
            try:
                save_project(root, manifest)
                _notify_project_docks(root)
            except ProjectFileError as exc:
                Gimp.message(f"Panel placed, but project cursor could not be saved: {exc}")
        else:
            (root / CURSOR_FILE).write_text(json.dumps({"next": seq + 1}))
    nxt = panels[seq] if seq < len(panels) else None
    msg = (f"Placed {seq:03d}/{len(panels):03d} ({_script_position(spec)}).")
    if nxt is None:
        msg += " That was the last panel."
    else:
        msg += f" Next: {_script_position(nxt)}."
        if nxt["page"] != spec["page"]:
            msg += " (The script starts a new page there.)"
    Gimp.message(msg)


def _render_warnings(result):
    """Tell the artist what the engine had to render without (e.g. a character with no
    registered appearance comes out as nobody in particular)."""
    for warning in result.get("warnings") or []:
        Gimp.message(f"Warning: {warning}")


class _JobWindow:
    """A small non-modal window for a running engine job: what it is doing, how long
    it has waited, and a Cancel button. Best effort: with no display it does nothing,
    and the GIMP progress bar still pulses."""

    def __init__(self, label):
        self.cancelled = False
        self.window = None
        try:
            if not Gtk.init_check()[0]:
                return
            window = Gtk.Window(title="Imanganation", resizable=False,
                                window_position=Gtk.WindowPosition.CENTER)
            window.set_keep_above(True)
            window.set_deletable(False)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, border_width=14)
            self.title = Gtk.Label(label=label, xalign=0)
            self.detail = Gtk.Label(label="Sending to the engine…", xalign=0)
            self.bar = Gtk.ProgressBar()
            self.button = Gtk.Button(label="Cancel")
            self.button.connect("clicked", self._cancel)
            for widget in (self.title, self.bar, self.detail, self.button):
                box.pack_start(widget, False, False, 0)
            window.add(box)
            window.set_size_request(340, -1)
            window.show_all()
            self.window = window
            self._pump()
        except Exception:
            self.window = None

    def _cancel(self, _button):
        self.cancelled = True
        self.button.set_sensitive(False)
        self.detail.set_text("Cancelling…")

    def _pump(self):
        context = GLib.MainContext.default()
        while context.iteration(False):
            pass

    def update(self, text, fraction=None):
        if self.window is not None:
            if not self.cancelled:
                self.detail.set_text(text)
            if fraction is None:
                self.bar.pulse()
            else:
                self.bar.set_fraction(fraction)
            self._pump()

    def close(self):
        if self.window is not None:
            self.window.destroy()
            self.window = None
            self._pump()


def _job_detail(job, started):
    seconds = int(time.monotonic() - started)
    if job_status_text is None:
        return f"{'Queued' if job['status'] == 'queued' else 'Rendering'} · {seconds}s"
    return job_status_text(job, seconds)


def _run_job(engine, path, body, label):
    """POST a job to the engine and poll it with a progress bar and a Cancel window;
    -> result dict. Cancelling stops the engine's job (queued: dropped; running:
    ComfyUI is interrupted) and raises EngineError(CANCELLED)."""
    job = _http("POST", f"{engine}{path}", body)
    Gimp.progress_init(label)
    window = _JobWindow(label)
    started = time.monotonic()
    deadline = started + RENDER_TIMEOUT
    asked = False
    try:
        while job["status"] in ("queued", "running"):
            if time.monotonic() > deadline:
                raise EngineError(f"timed out after {RENDER_TIMEOUT}s (job {job['id']})")
            time.sleep(0.5)
            fraction = job_fraction(job) if job_fraction is not None else None
            if fraction is None:
                Gimp.progress_pulse()
            else:
                Gimp.progress_update(fraction)
            text = _job_detail(job, started)
            Gimp.progress_set_text(text)
            window.update(text, fraction)
            if window.cancelled and not asked:
                asked = True
                try:
                    _http("POST", f"{engine}/jobs/{job['id']}/cancel", {})
                except EngineError:
                    pass  # the next poll shows what happened
            job = _http("GET", f"{engine}/jobs/{job['id']}")
    finally:
        window.close()
        Gimp.progress_end()
    if job["status"] == "cancelled":
        raise EngineError(CANCELLED)
    if job["status"] != "done":
        raise EngineError(f"{job.get('kind', 'render')} failed: {job.get('error')}")
    return job["result"]


def _newest_take(root, seq, manifest=None):
    """Newest file for a panel: a fresh retake beats an older hi-res, and a hi-res
    made from the current take beats that take. (Name order can't express this:
    ``003_hires`` sorts before ``003_take02``.) A container's active take is the
    source of truth when one is recorded."""
    if manifest is not None and seq <= len(manifest["panels"]):
        panel = manifest["panels"][seq - 1]
        take = manifest["takes"].get(panel.get("active_take"))
        if take:
            path = root / take["file"]
            return path if path.is_file() else None
    matches = list((root / "panels").glob(f"{seq:03d}*.png"))
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def _record_take(root, manifest, seq, source, kind, width, height, engine=None,
                 parent=None):
    """Register a new container take and return its immutable project-local copy."""
    if manifest is None:
        return Path(source), None
    try:
        take, path = record_take(
            root, manifest, manifest["panels"][seq - 1]["id"], source,
            kind=kind, width=width, height=height, engine=engine, parent=parent)
    except (ProjectFileError, OSError, IndexError) as exc:
        raise ValueError(f"Could not record project take: {exc}") from exc
    _notify_project_docks(root)
    return path, take


def _manifest_for(root):
    if not (Path(root) / "project.json").exists():
        return None
    if load_project is None:
        raise ValueError("This plug-in install is missing project_store.py")
    try:
        return load_project(root)
    except ProjectFileError as exc:
        raise ValueError(str(exc)) from exc


def _registered_take_id(root, manifest, source, panel_id):
    if manifest is None:
        return None
    source_path = Path(source).resolve()
    for take_id, take in manifest["takes"].items():
        if take.get("panel") == panel_id and (Path(root) / take["file"]).resolve() == source_path:
            return take_id
    return None


def _project_page_id_for_image(image, manifest):
    if not manifest:
        return None
    parasite = image.get_parasite(PROJECT_PARASITE)
    if parasite is None:
        return None
    try:
        page_ref = json.loads(bytes(parasite.get_data()))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if (page_ref.get("project") != manifest["project"]["id"]
            or not any(page["id"] == page_ref.get("page")
                       for page in manifest["pages"])):
        return None
    return page_ref["page"]


def _save_project_page(image, root, manifest):
    """Persist a successful canvas edit to this project's page XCF."""
    page_id = _project_page_id_for_image(image, manifest)
    if page_id is None:
        return False
    page = next((item for item in manifest.get("pages", [])
                 if item.get("id") == page_id), None)
    relative = page.get("file") if page else None
    destination = Path(root) / relative if relative else None
    if destination is None or not destination.is_file():
        Gimp.message("The panel is on the page, but its project XCF could not be found. "
                     "Save the page manually.")
        return False
    try:
        result = Gimp.file_save(
            Gimp.RunMode.NONINTERACTIVE, image,
            Gio.File.new_for_path(str(destination)), None)
        if result is False:
            raise RuntimeError("GIMP did not save the page")
        Gimp.displays_flush()
        return True
    except Exception as exc:
        Gimp.message(f"The panel is on the page, but the page XCF was not saved: {exc}. "
                     "Save the page manually.")
        return False


def _placed_frame_for_image(image, manifest, panel):
    """Return this panel's saved frame when the active XCF is its placed page."""
    placement = panel.get("placement") if panel else None
    if (not placement
            or _project_page_id_for_image(image, manifest) != placement.get("page")):
        return None
    frame = placement.get("frame")
    if (not isinstance(frame, list) or len(frame) != 4
            or frame[2] <= 0 or frame[3] <= 0):
        return None
    return tuple(frame)


def _take_reference(root, seq, source):
    manifest = _manifest_for(root)
    if manifest is None:
        return None
    panel_id = manifest["panels"][seq - 1]["id"]
    take_id = _registered_take_id(root, manifest, source, panel_id)
    if take_id is None:
        return None
    return {"project": manifest["project"]["id"], "panel": panel_id,
            "take": take_id}


def _source_dimensions(source):
    opened = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                            Gio.File.new_for_path(str(source)))
    try:
        return opened.get_width(), opened.get_height()
    finally:
        opened.delete()


def _record_derived_take(root, seq, source, result, kind, engine):
    """Register a refine/inpaint child, importing its source take if needed."""
    manifest = _manifest_for(root)
    if manifest is None:
        return Path(result["path"]), None
    panel_id = manifest["panels"][seq - 1]["id"]
    parent_id = _registered_take_id(root, manifest, source, panel_id)
    if parent_id is None:
        width, height = _source_dimensions(source)
        _record_take(
            root, manifest, seq, source, "import", width, height,
            engine={"imported_from_legacy_layout": True})
        parent_id = manifest["panels"][seq - 1]["takes"][-1]
    path, take = _record_take(
        root, manifest, seq, result["path"], kind, result["width"], result["height"],
        engine=engine, parent=parent_id)
    return path, take


def render_panel(procedure, run_mode, image, drawables, config, data):
    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config, PROC_RENDER):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

    try:
        root, panels, seq, explicit, manifest = _load_project(config)
    except ValueError as exc:
        return _error(procedure, str(exc))
    spec = panels[seq - 1]

    panel = manifest["panels"][seq - 1] if manifest else None
    saved_frame = _placed_frame_for_image(image, manifest, panel)
    page_id = _project_page_id_for_image(image, manifest)
    if (manifest and panel.get("placement")
            and panel["placement"].get("page") != page_id):
        return _error(procedure, "This panel is placed on another project page. Open that "
                                 "page before rendering it.")
    _, non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
    if saved_frame is None and not non_empty:
        return _error(procedure, "Select the target frame first (e.g. Fuzzy Select inside "
                                 "an empty panel of your template).")
    if saved_frame is not None:
        x1, y1, width, height = saved_frame
        x2, y2 = x1 + width, y1 + height
    if manifest and page_id and saved_frame is None:
        panel["placement"] = {"page": page_id, "frame": [x1, y1, x2 - x1, y2 - y1]}
        panel["status"] = "placed"

    # Keep the frame safe while the engine works; the artist may keep clicking.
    frame = Gimp.Selection.save(image)
    frame.set_name(f"imanganation frame {seq:03d}")
    try:
        engine = config.get_property("engine-url").rstrip("/")
        body = {**_render_body(root, manifest, seq),
                "frame_width": x2 - x1, "frame_height": y2 - y1}
        if config.get_property("seed") >= 0:
            body["seed"] = config.get_property("seed")
        # "placement: <Character>" layers say where each character goes in this frame.
        try:
            from placement import export_placements
        except ImportError:
            export_placements = None
        if export_placements is not None:
            names = [c["name"] if isinstance(c, dict) else c
                     for c in spec.get("characters", [])]
            placements = export_placements(image, (x1, y1, x2 - x1, y2 - y1), names,
                                           root / "tmp")
            if placements:
                body["placements"] = placements
        result = _run_job(engine, "/jobs", body,
                          f"Rendering panel {seq:03d} ({_script_position(spec)})…")
        image_path, _take = _record_take(
            root, manifest, seq, result["path"], "render", result["width"],
            result["height"], engine={k: result.get(k) for k in
                                      ("seed", "width", "height", "prompt", "warnings")
                                      if result.get(k) is not None})
        image.select_item(Gimp.ChannelOps.REPLACE, frame)
        layer = _place(image, Gio.File.new_for_path(str(image_path)), spec, seq,
                       render={k: result.get(k) for k in ("seed", "width", "height",
                                                          "prompt", "path")},
                       take_ref={"project": manifest["project"]["id"],
                                 "panel": manifest["panels"][seq - 1]["id"],
                                 "take": _registered_take_id(
                                     root, manifest, image_path,
                                     manifest["panels"][seq - 1]["id"])}
                       if manifest is not None else None,
                       frame=saved_frame)
    except (EngineError, ValueError, KeyError) as exc:
        return _error(procedure, str(exc))
    finally:
        image.remove_channel(frame)

    _advance(root, panels, seq, explicit, spec, manifest)
    _render_warnings(result)
    if manifest is not None:
        _save_project_page(image, root, manifest)
    return _success(procedure, layer)


def _panel_layer(drawables):
    """The placed render layer for the selection: the layer itself, or the render
    inside a selected panel group (however deeply nested), or the render beside a
    selected text layer. Of several renders, the topmost visible one."""
    def tagged(layer):
        return layer if layer.get_parasite(PARASITE) else None

    def inside(group):
        renders = []

        def walk(layers):
            for child in layers:  # top of the stack first
                if tagged(child):
                    renders.append(child)
                elif child.is_group():
                    walk(child.get_children())

        walk(group.get_children())
        return next((c for c in renders if c.get_visible()), renders[0] if renders else None)

    for item in drawables:
        if isinstance(item, Gimp.LayerMask):
            item = Gimp.Layer.from_mask(item)
        found = tagged(item)
        if not found and item.is_group():
            found = inside(item)
        parent = item.get_parent()
        while not found and parent is not None:  # a text layer or bubble beside a render
            found = inside(parent)
            parent = parent.get_parent()
        if found:
            return found
    return None


def _selected_panel(drawables):
    """-> (layer, meta, seq, source Path) for the selected placed panel, or raise
    ValueError with a message for the artist."""
    layer = _panel_layer(drawables)
    if layer is None:
        raise ValueError("No placed imanganation panel found. Select a panel's layer or "
                         "group, or make the selection over a visible panel.")
    meta = json.loads(bytes(layer.get_parasite(PARASITE).get_data()))
    seq = meta.get("seq")
    source = (meta.get("stage_source") if meta.get("stage") else None) \
        or meta.get("file") or (meta.get("render") or {}).get("path")
    if not seq or not source:
        raise ValueError("This layer has no panel number or source file (placed with "
                         "Place Panel?). Re-place it with Place Next Panel.")
    return layer, meta, seq, Path(source)


def _frame_box(image, layer):
    """The panel's frame as (x, y, w, h): its mask's extent, else the layer itself."""
    _, ox, oy = layer.get_offsets()
    own = (ox, oy, layer.get_width(), layer.get_height())
    mask = layer.get_mask()
    if mask is None:
        return own
    saved = Gimp.Selection.save(image)
    try:
        image.select_item(Gimp.ChannelOps.REPLACE, mask)
        _, non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
        image.select_item(Gimp.ChannelOps.REPLACE, saved)
    finally:
        image.remove_channel(saved)
    return (x1, y1, x2 - x1, y2 - y1) if non_empty else own


def _swap_in(image, old, path, stored, name, fit="frame", take_ref=None):
    """Put a new take of ``old``'s panel beside it, in the same group with the same
    frame mask. The old take is kept, hidden; the artist's selection is untouched.

    ``fit="frame"`` (a new composition, e.g. Regenerate): cover-fit to the panel's
    frame. Not to the old layer, which already overhangs the frame, so covering *it*
    would crop more than needed when the new take's shape differs.
    ``fit="layer"`` (the same picture, e.g. Inpaint / Refine): take the old layer's
    exact geometry so every pixel lines up, even if the artist moved or scaled it."""
    if fit == "layer":
        _, fx, fy = old.get_offsets()
        fw, fh = old.get_width(), old.get_height()
    else:
        fx, fy, fw, fh = _frame_box(image, old)
    image.undo_group_start()
    saved = Gimp.Selection.save(image)  # the artist's selection, restored below
    try:
        new = Gimp.file_load_layer(Gimp.RunMode.NONINTERACTIVE, image,
                                   Gio.File.new_for_path(path))
        new.set_name(name)
        image.insert_layer(new, old.get_parent(), image.get_item_position(old))
        if fit == "layer":
            nw, nh = fw, fh
        else:
            s = max(fw / new.get_width(), fh / new.get_height())
            nw, nh = round(new.get_width() * s), round(new.get_height() * s)
        new.scale(nw, nh, False)
        new.set_offsets(fx + (fw - nw) // 2, fy + (fh - nh) // 2)
        mask = old.get_mask()
        if mask is not None:
            image.select_item(Gimp.ChannelOps.REPLACE, mask)
            new.add_mask(new.create_mask(Gimp.AddMaskType.SELECTION))
        new.attach_parasite(Gimp.Parasite.new(
            PARASITE, Gimp.PARASITE_PERSISTENT, list(json.dumps(stored).encode())))
        if take_ref:
            new.attach_parasite(Gimp.Parasite.new(
                TAKE_PARASITE, Gimp.PARASITE_PERSISTENT,
                list(json.dumps(take_ref, separators=(",", ":")).encode())))
            _tag_panel_group(old.get_parent(), take_ref)
        old.set_visible(False)
        image.select_item(Gimp.ChannelOps.REPLACE, saved)
    finally:
        image.remove_channel(saved)
        image.undo_group_end()
    template = _find_template(image)
    if template is not None:
        image.set_selected_layers([template])
    Gimp.displays_flush()
    return new


def _base_name(layer):
    name = layer.get_name()
    for suffix in (" render", " hi-res", " inpaint", " take"):
        name = name.rsplit(suffix, 1)[0]
    return name


def _without_stage(meta):
    """A replacement take is a complete image, no longer an individual patch layer."""
    stored = dict(meta)
    stored.pop("stage", None)
    stored.pop("stage_source", None)
    return stored


def refine_panel(procedure, run_mode, image, drawables, config, data):
    try:
        layer, meta, seq, source = _selected_panel(drawables)
    except ValueError as exc:
        return _error(procedure, str(exc))

    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config, PROC_REFINE):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

    root = source.parent.parent
    try:
        manifest = _manifest_for(root)
        if manifest is not None:
            body = {**_engine_project(root, manifest), "source": str(source),
                    **_refine_origin(root, manifest, seq, source)}
        else:
            body = {"project_dir": str(root), "seq": seq, "source": str(source)}
    except (ValueError, KeyError, IndexError) as exc:
        return _error(procedure, str(exc))
    if config.get_property("scale") > 0:
        body["scale"] = config.get_property("scale")
    if config.get_property("denoise") >= 0:
        body["denoise"] = config.get_property("denoise")
    try:
        result = _run_job(config.get_property("engine-url").rstrip("/"), "/refine", body,
                          f"Refining panel {seq:03d} (hi-res)…")
    except EngineError as exc:
        return _error(procedure, str(exc))

    try:
        path, _take = _record_derived_take(
            source.parent.parent, seq, source, result, "refine",
            {k: result.get(k) for k in ("width", "height", "upscaler", "denoise", "seed")})
    except ValueError as exc:
        return _error(procedure, str(exc))
    stored = dict(_without_stage(meta), file=str(path),
                  refined={k: result.get(k) for k in ("source", "width", "height",
                                                      "upscaler", "denoise", "seed")})
    hires = _swap_in(image, layer, str(path), stored, f"{_base_name(layer)} hi-res",
                     fit="layer", take_ref=_take_reference(
                         source.parent.parent, seq, path))
    Gimp.message(f"Refined panel {seq:03d}: {result['width']}×{result['height']} "
                 f"(previous take kept, hidden).")
    if manifest is not None:
        _save_project_page(image, source.parent.parent, manifest)
    return _success(procedure, hires)


def _recorded_seed(meta, source):
    """The seed that *composed* ``source``. A hi-res take's own sidecar seed is the
    refine polish seed, so follow its ``source`` back to the original render."""
    if (meta.get("render") or {}).get("seed") is not None:
        return meta["render"]["seed"]
    path = Path((meta.get("refined") or {}).get("source") or source)
    for _ in range(4):  # render <- hi-res (<- …), never loop forever
        try:
            side = json.loads(path.with_suffix(".json").read_text())
        except (OSError, ValueError):
            return None
        if "upscaler" in side and side.get("source"):
            path = Path(side["source"])
            continue
        return side.get("seed")
    return None


def regenerate_panel(procedure, run_mode, image, drawables, config, data):
    try:
        layer, meta, seq, source = _selected_panel(drawables)
    except ValueError as exc:
        return _error(procedure, str(exc))

    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config, PROC_REGEN):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

    root = source.parent.parent
    try:
        manifest = _manifest_for(root)
        if manifest is not None:
            spec = _container_spec(manifest["panels"][seq - 1])
        else:
            spec = json.loads((root / "panels.json").read_text())["panels"][seq - 1]
        # the script as it is *now*: edits apply
    except (OSError, ValueError, KeyError, IndexError) as exc:
        return _error(procedure, f"Panel {seq:03d} not found in the project at {root}: {exc}")

    if config.get_property("same-seed"):
        seed = _recorded_seed(meta, source)
        if seed is None:
            return _error(procedure, "No seed is recorded for this take; untick Same seed.")
    else:
        # Explicit, so a seed pinned in the script can't hand back the same image.
        seed = random.randrange(2**31)

    _, _, fw, fh = _frame_box(image, layer)
    body = {**_render_body(root, manifest, seq), "frame_width": fw, "frame_height": fh,
            "seed": seed}
    if config.get_property("keep-composition"):
        # Keep this take's layout and poses (ControlNet on its edges); the edited
        # script decides the details. Works with a new or the same seed.
        body["guide"] = str(source)
        if config.get_property("composition-strength") > 0:
            body["guide_strength"] = config.get_property("composition-strength")
    try:
        result = _run_job(config.get_property("engine-url").rstrip("/"), "/jobs", body,
                          f"Regenerating panel {seq:03d} ({_script_position(spec)})…")
    except EngineError as exc:
        return _error(procedure, str(exc))

    try:
        path, _take = _record_take(
            root, manifest, seq, result["path"], "render", result["width"],
            result["height"], engine={k: result.get(k) for k in
                                      ("seed", "width", "height", "prompt", "warnings")
                                      if result.get(k) is not None})
    except (ValueError, KeyError) as exc:
        return _error(procedure, str(exc))
    stored = dict(spec, seq=seq, file=str(path),
                  render={k: result.get(k) for k in ("seed", "width", "height", "prompt",
                                                     "path")})
    take = Path(result["path"]).stem.split("_", 1)[-1] if "_" in Path(result["path"]).stem \
        else "take01"
    new = _swap_in(image, layer, str(path), stored, f"{_base_name(layer)} {take}",
                   take_ref=_take_reference(root, seq, path))
    Gimp.message(f"Regenerated panel {seq:03d} as {Path(result['path']).name} "
                 f"(seed {seed}; previous take kept, hidden).")
    _render_warnings(result)
    if manifest is not None:
        _save_project_page(image, root, manifest)
    return _success(procedure, new)


def _export_inpaint_mask(image, layer, source, path):
    """The current selection as a mask in ``source``'s own pixel space.

    The take is shown scaled (a 960x1024 render placed at 525x560), and the engine
    needs the mask at the source's size. So the selection (feathering included) is
    filled white into a transparent layer covering ``layer``'s footprint, then scaled
    to the source's real size and saved as a transparent PNG (engine: alpha channel)."""
    src = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(str(source)))
    sw, sh = src.get_width(), src.get_height()
    src.delete()

    _, lx, ly = layer.get_offsets()
    lw, lh = layer.get_width(), layer.get_height()
    image.undo_group_start()
    temp = Gimp.Layer.new(image, "imanganation mask (temp)", lw, lh,
                          Gimp.ImageType.RGBA_IMAGE, 100, Gimp.LayerMode.NORMAL)
    image.insert_layer(temp, None, 0)
    temp.set_offsets(lx, ly)
    temp.fill(Gimp.FillType.TRANSPARENT)
    temp.edit_fill(Gimp.FillType.WHITE)  # respects the selection and its feathering
    out = Gimp.Image.new(lw, lh, Gimp.ImageBaseType.RGB)
    try:
        copy = Gimp.Layer.new_from_drawable(temp, out)
        out.insert_layer(copy, None, 0)
        copy.set_offsets(0, 0)
        out.scale(sw, sh)
        path.parent.mkdir(parents=True, exist_ok=True)
        Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, out, Gio.File.new_for_path(str(path)), None)
    finally:
        out.delete()
        image.remove_layer(temp)
        image.undo_group_end()


def _visible_renders(image):
    """Every visible placed take, topmost first."""
    out = []

    def walk(layers):
        for layer in layers:
            if not layer.get_visible():
                continue
            if layer.is_group():
                walk(layer.get_children())
            elif layer.get_parasite(PARASITE):
                out.append(layer)

    walk(image.get_layers())
    return out


def _panel_at(image, x, y):
    """The topmost visible placed take covering page point (x, y), if any."""
    for layer in _visible_renders(image):
        _, lx, ly = layer.get_offsets()
        if lx <= x < lx + layer.get_width() and ly <= y < ly + layer.get_height():
            return layer
    return None


def _panel_under_selection(image, x1, y1, x2, y2):
    """The placed take under a selection: the one at its centre, else the one it
    overlaps most (a selection spanning a gutter or a bubble has its centre off the art)."""
    centre = _panel_at(image, (x1 + x2) // 2, (y1 + y2) // 2)
    if centre is not None:
        return centre
    best, best_area = None, 0
    for layer in _visible_renders(image):
        _, lx, ly = layer.get_offsets()
        w = min(x2, lx + layer.get_width()) - max(x1, lx)
        h = min(y2, ly + layer.get_height()) - max(y1, ly)
        if w > 0 and h > 0 and w * h > best_area:
            best, best_area = layer, w * h
    return best


def _name_list(text):
    """"Yuki, Akira" -> ["Yuki", "Akira"] (blank entries dropped)."""
    return [name.strip() for name in (text or "").split(",") if name.strip()]


def _blank_target(image):
    """Develop in Stages with nothing rendered yet starts from a blank picture. The panel
    is the one selected in the docks (or on the canvas), else the project's next.
    -> (root, manifest, seq, page id); ValueError says what's missing, so the artist
    hears it before filling in a dialog."""
    root = _DOCK_CONTEXT.get("root")
    manifest = _manifest_for(root) if root is not None else None
    page_id = _project_page_id_for_image(image, manifest) if manifest else None
    if page_id is None:
        raise ValueError("Open a project page to develop a new panel on, or render the "
                         "panel first (Render Panel into Frame).")
    ids = [panel["id"] for panel in manifest["panels"]]
    panel_id = (_selected_canvas_panel_id(manifest) or _DOCK_CONTEXT.get("selected_id")
                or manifest["cursor"].get("next_panel"))
    if panel_id not in ids:
        raise ValueError("Select the script panel to develop in Project or Script "
                         "first, then make the selection for its frame.")
    seq = ids.index(panel_id) + 1
    panel = manifest["panels"][seq - 1]
    if panel.get("takes"):
        raise ValueError("This panel already has a picture; select its layer or group "
                         "to develop it.")
    placement = panel.get("placement")
    if placement and placement.get("page") != page_id:
        raise ValueError("This panel is placed on another project page. Open that page "
                         "first.")
    return root, manifest, seq, page_id


def _blank_base_layer(image, target, x1, y1, x2, y2):
    """Put a blank paper-white base take in the panel's frame (the one saved for it on
    this page, else the selection) for the first stage to paint onto. -> the new layer."""
    root, manifest, seq, page_id = target
    panel = manifest["panels"][seq - 1]
    saved_frame = _placed_frame_for_image(image, manifest, panel)
    if saved_frame is not None:
        x1, y1, width, height = saved_frame
    else:
        width, height = x2 - x1, y2 - y1
    w, h = blank_size(width, height)
    tmp = Path(root) / "tmp"
    tmp.mkdir(exist_ok=True)
    blank = tmp / f"blank_{seq:03d}_{secrets.token_hex(4)}.png"
    png = rgb_png(w, h, b"\xff" * (w * h * 3)) if rgb_png else None
    if png is None:
        raise ValueError("This plug-in install can't make a blank starting picture")
    blank.write_bytes(png)
    if saved_frame is None:
        panel["placement"] = {"page": page_id, "frame": [x1, y1, width, height]}
        panel["status"] = "placed"
        save_project(root, manifest)
    path, _take = _record_take(root, manifest, seq, str(blank), "import", w, h,
                               engine={"blank": True})
    blank.unlink(missing_ok=True)
    take_ref = _take_reference(root, seq, path)
    manifest = _manifest_for(root)
    panel = manifest["panels"][seq - 1]
    return _place(image, Gio.File.new_for_path(str(path)), _container_spec(panel), seq,
                  render={"path": str(path), "width": w, "height": h, "blank": True},
                  take_ref=take_ref, frame=saved_frame)


def inpaint_selection(procedure, run_mode, image, drawables, config, data, *, staged=False):
    _, non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
    if not non_empty:
        return _error(procedure, "Select the area to repaint first.")
    # After placing, the template is the selected layer (for Fuzzy Select), so an
    # artist who just draws a selection means "the panel under it".
    if _panel_layer(drawables) is None:
        under = _panel_under_selection(image, x1, y1, x2, y2)
        drawables = [under] if under is not None else drawables
    blank_target = None  # nothing rendered yet: begin from a blank picture (below)
    try:
        layer, meta, seq, source = _selected_panel(drawables)
    except ValueError as exc:
        if not staged:
            return _error(procedure, str(exc))
        try:
            blank_target = _blank_target(image)
        except ValueError as blank_exc:
            return _error(procedure, str(blank_exc))
        layer, meta, seq, source = None, {}, blank_target[2], None
    if blank_target is None:
        _, lx, ly = layer.get_offsets()
        if (x2 <= lx or y2 <= ly or x1 >= lx + layer.get_width()
                or y1 >= ly + layer.get_height()):
            return _error(procedure, "The selection doesn't overlap the selected panel.")
        if not source.is_file():
            return _error(procedure, f"This take's file is missing: {source}")

    stage_phase = _suggested_development_phase(meta) if staged else None
    if run_mode == Gimp.RunMode.INTERACTIVE and staged:
        guided = _development_stage_dialog(meta)
        if guided is None:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        stage_phase = guided["phase"]
        config.set_property("prompt", guided["prompt"])
        config.set_property("characters", guided["characters"])
        config.set_property("denoise", guided["denoise"])
        config.set_property("grow", guided["grow"])
    elif run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(
            procedure, config, PROC_INPAINT):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
    prompt = (config.get_property("prompt") or "").strip()
    if not prompt:
        return _error(procedure, "Describe what to paint in the selection.")
    if blank_target is not None:  # nothing to keep: paint the selection fresh
        try:
            layer = _blank_base_layer(image, blank_target, x1, y1, x2, y2)
            layer, meta, seq, source = _selected_panel([layer])
        except (ValueError, ProjectFileError) as exc:
            return _error(procedure, str(exc))
        config.set_property("denoise", 1.0)

    root = source.parent.parent
    mask = root / "tmp" / f"inpaint_{seq:03d}_{time.strftime('%Y%m%d-%H%M%S')}_{secrets.token_hex(4)}.png"
    try:
        _export_inpaint_mask(image, layer, source, mask)
        engine_project = _engine_project(root)
        body = {**engine_project, "mask": str(mask), "prompt": prompt, "source": str(source)}
        if "project_dir" in engine_project:
            body["seq"] = seq
        if config.get_property("denoise") > 0:
            body["denoise"] = config.get_property("denoise")
        if config.get_property("grow") >= 0:
            body["grow_mask_by"] = config.get_property("grow")
        characters = _name_list(config.get_property("characters"))
        if characters:
            # Their references keep faces on-model; several split the patch in
            # reading order, like a panel render.
            body["characters"] = characters
            manifest = _manifest_for(root)
            if manifest is not None:
                body["reading_order"] = manifest["project"].get("reading_order", "rtl")
        if "project" in engine_project:  # the project's look, as its panels have
            body.update(engine_ui.style_options(_manifest_for(root)) if engine_ui else {})
        label = "Developing" if staged else "Inpainting"
        result = _run_job(config.get_property("engine-url").rstrip("/"), "/inpaint", body,
                          f"{label} panel {seq:03d}: {prompt[:40]}…")
    except (EngineError, ValueError) as exc:
        return _error(procedure, str(exc))

    overlay_path = result.get("overlay_path")
    if staged and not (overlay_path and Path(overlay_path).is_file()):
        # Checked before recording the take: an engine without stage layers (or one whose
        # output folder this GIMP can't read) must not leave a take in the project
        # history that no layer on the page shows.
        return _error(procedure, "The engine did not return a transparent stage layer. "
                                 "Update the engine and try again.")
    try:
        mask_ref = mask.relative_to(root).as_posix()
        path, _take = _record_derived_take(
            root, seq, source, result, "inpaint",
            {"mask": mask_ref, **{k: result.get(k) for k in
                                  ("prompt", "denoise", "grow_mask_by", "seed")}})
    except (ValueError, KeyError) as exc:
        return _error(procedure, str(exc))
    take_ref = _take_reference(root, seq, path)
    if staged:
        stage_number = int((meta.get("stage") or {}).get("step", 0)) + 1
        stage_meta = {
            "step": stage_number, "prompt": prompt,
            "phase": stage_phase,
            "source": str(source), "take": str(path),
            "mask": mask_ref, "seed": result.get("seed"),
            "denoise": result.get("denoise"),
            "grow_mask_by": result.get("grow_mask_by"),
            "overlay": str(overlay_path),
        }
        stage_history = list(meta.get("stage_history") or [])
        if not stage_history and meta.get("stage"):
            previous = meta["stage"]
            stage_history.append({k: previous.get(k) for k in ("step", "phase", "prompt")})
        stage_history.append({"step": stage_number, "phase": stage_phase,
                              "prompt": prompt})
        stored = dict(meta, file=str(overlay_path), stage_source=str(path),
                      stage=stage_meta, stage_history=stage_history,
                      inpainted={k: result.get(k) for k in
                                 ("prompt", "source", "mask", "denoise",
                                  "grow_mask_by", "seed")})
        saved = Gimp.Selection.save(image)
        image.undo_group_start()
        try:
            new = Gimp.file_load_layer(
                Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path(overlay_path))
            new.set_name(f"Stage {stage_number:02d} · {prompt[:48]}")
            image.insert_layer(new, layer.get_parent(), image.get_item_position(layer))
            new.scale(layer.get_width(), layer.get_height(), False)
            _, lx, ly = layer.get_offsets()
            new.set_offsets(lx, ly)
            # Respect a frame mask on the original panel as well as the transparent
            # edit alpha returned by the engine.
            panel_mask = layer.get_mask()
            if panel_mask is not None:
                image.select_item(Gimp.ChannelOps.REPLACE, panel_mask)
                new.add_mask(new.create_mask(Gimp.AddMaskType.SELECTION))
            new.attach_parasite(Gimp.Parasite.new(
                PARASITE, Gimp.PARASITE_PERSISTENT,
                list(json.dumps(stored).encode())))
            if take_ref:
                new.attach_parasite(Gimp.Parasite.new(
                    TAKE_PARASITE, Gimp.PARASITE_PERSISTENT,
                    list(json.dumps(take_ref, separators=(",", ":")).encode())))
                _tag_panel_group(layer.get_parent(), take_ref)
        except Exception as exc:
            return _error(procedure, f"Could not add the development stage: {exc}")
        finally:
            image.select_item(Gimp.ChannelOps.REPLACE, saved)
            image.remove_channel(saved)
            image.undo_group_end()
        Gimp.displays_flush()
    else:
        stored = dict(_without_stage(meta), file=str(path),
                      inpainted={k: result.get(k) for k in ("prompt", "source", "mask",
                                                            "denoise", "grow_mask_by", "seed")})
        new = _swap_in(image, layer, str(path), stored, f"{_base_name(layer)} inpaint",
                       fit="layer", take_ref=take_ref)
    if staged:
        current_title = next((title for ident, title, _hint in DEVELOPMENT_PHASES
                              if ident == stage_phase), "Development")
        next_phase = _suggested_development_phase({"stage": {"phase": stage_phase}})
        next_title = next((title for ident, title, _hint in DEVELOPMENT_PHASES
                           if ident == next_phase), current_title)
        Gimp.message(f"Added stage {stage_number} · {current_title} to panel {seq:03d}. "
                     f"The edit is a separate layer. Suggested next: {next_title}. "
                     "You can choose another phase or repeat a phase any time.")
    else:
        Gimp.message(f"Inpainted panel {seq:03d} ({Path(result['path']).name}); outside the "
                     f"selection the take is unchanged. Previous take kept, hidden.")
    manifest = _manifest_for(root)
    if manifest is not None:
        _save_project_page(image, root, manifest)
    return _success(procedure, new)


def develop_panel_stage(procedure, run_mode, image, drawables, config, data):
    """Add a masked AI edit as a new editable layer above the current panel take."""
    return inpaint_selection(procedure, run_mode, image, drawables, config, data,
                             staged=True)


def _image_project(image):
    """The project of any placed panel in this image (from its recorded file)."""
    def walk(layers):
        for layer in layers:
            p = layer.get_parasite(PARASITE)
            if p:
                meta = json.loads(bytes(p.get_data()))
                f = meta.get("file") or (meta.get("render") or {}).get("path")
                if f and any((Path(f).parent.parent / name).exists()
                             for name in ("project.json", "panels.json")):
                    return Path(f).parent.parent
            if layer.is_group():
                found = walk(layer.get_children())
                if found:
                    return found
        return None

    return walk(image.get_layers())


def _export_reference(image, drawable, path, square=True, max_side=REF_MAX):
    """Export ``drawable`` (or its part inside the selection) as a PNG.

    The CLIP encoder centre-crops character references to a square, so the region is
    grown to a square around its centre here, on white, instead of being cropped there;
    ``square=False`` (a location, given to Qwen-Image whole) keeps its proportions.
    Layer masks apply (a panel's frame clip), so only what the artist sees is exported.
    -> the exported region's longest side, before any scaling down to ``max_side``."""
    _, lx, ly = drawable.get_offsets()
    lw, lh = drawable.get_width(), drawable.get_height()
    x1, y1, x2, y2 = lx, ly, lx + lw, ly + lh
    _, non_empty, sx1, sy1, sx2, sy2 = Gimp.Selection.bounds(image)
    if non_empty:
        x1, y1, x2, y2 = max(x1, sx1), max(y1, sy1), min(x2, sx2), min(y2, sy2)
        if x2 <= x1 or y2 <= y1:
            raise ValueError("The selection doesn't overlap the selected layer.")
    side = max(x2 - x1, y2 - y1)
    if square:
        width = height = side
        ox, oy = (x1 + x2) // 2 - side // 2, (y1 + y2) // 2 - side // 2
    else:
        width, height, ox, oy = x2 - x1, y2 - y1, x1, y1

    out = Gimp.Image.new(width, height, image.get_base_type())
    try:
        copy = Gimp.Layer.new_from_drawable(drawable, out)
        copy.set_visible(True)
        out.insert_layer(copy, None, 0)
        copy.set_offsets(lx - ox, ly - oy)
        bg_type = (Gimp.ImageType.RGB_IMAGE if image.get_base_type() == Gimp.ImageBaseType.RGB
                   else Gimp.ImageType.GRAY_IMAGE)
        bg = Gimp.Layer.new(out, "white", width, height, bg_type, 100,
                            Gimp.LayerMode.NORMAL)
        out.insert_layer(bg, None, 1)
        bg.fill(Gimp.FillType.WHITE)
        out.flatten()
        if out.get_base_type() != Gimp.ImageBaseType.RGB:
            out.convert_rgb()
        if side > max_side:
            out.scale(max(1, round(width * max_side / side)),
                      max(1, round(height * max_side / side)))
        path.parent.mkdir(parents=True, exist_ok=True)
        Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, out, Gio.File.new_for_path(str(path)), None)
    finally:
        out.delete()
    return side


def set_character_reference(procedure, run_mode, image, drawables, config, data):
    layers = [d for d in drawables if isinstance(d, Gimp.Layer)]
    if not layers:
        return _error(procedure, "Select the layer that shows the character.")
    root, recorded = _reference_target(image, CHARACTER_PARASITE)
    if recorded and not config.get_property("character"):
        config.set_property("character", recorded)
    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config, PROC_SETREF):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

    root = root or _image_project(image)
    if root is None:
        chosen = config.get_property("project-dir")
        if chosen is None:
            return _error(procedure, "No placed panel in this image to find the project "
                                     "from; choose the project folder.")
        root = Path(chosen.get_path())
    name = (config.get_property("character") or "").strip()
    engine = config.get_property("engine-url").rstrip("/")
    try:
        from urllib.parse import quote

        engine_project = _engine_project(root)
        selector = "&".join(f"{key}={quote(value)}" for key, value in engine_project.items())
        cast = _http("GET", f"{engine}/characters?{selector}")
        known = {c["name"].lower(): c["name"] for c in cast}
        for c in cast:
            known.update({a.lower(): c["name"] for a in c.get("aliases", [])})
        if name.lower() not in known:
            return _error(procedure, f"Unknown character {name!r}. This project has: "
                                     f"{', '.join(c['name'] for c in cast) or 'none'}.")
        name = known[name.lower()]
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "character"
        path = root / "tmp" / f"gimp_ref_{slug}_{time.strftime('%Y%m%d-%H%M%S')}.png"
        side = _export_reference(image, layers[0], path)
        request = {**engine_project, "name": name, "image_path": str(path),
                   "make_default": bool(config.get_property("make-default"))}
        label = re.sub(r"[^A-Za-z0-9_-]+", "-",
                       (config.get_property("reference-name") or "").strip()).strip("-")[:40]
        if label:
            request["version_id"] = label
        result = _http("POST", f"{engine}/characters/reference", request)
    except (EngineError, ValueError) as exc:
        return _error(procedure, str(exc))

    if request["make_default"]:
        Gimp.message(f"{result['name']}'s reference is now {result['version']} ({side}px "
                     f"square; was {result['previous']}). New renders of {result['name']} "
                     f"use it; earlier versions are kept in characters/{slug}/.")
    else:
        Gimp.message(f"Saved {result['version']} as an extra reference for "
                     f"{result['name']} ({side}px square). Pick it per panel in Context "
                     f"-> Reference…; the default is still {result['previous']}.")
    _notify_project_docks(root)
    return _success(procedure, layers[0])


def _reference_target(image, kind=LOCATION_PARASITE):
    """(project root, name) recorded on a reference image opened from Context."""
    parasite = image.get_parasite(kind)
    if parasite is None:
        return None, ""
    try:
        meta = json.loads(bytes(parasite.get_data()))
        return Path(meta["project"]), meta.get("name", "")
    except (TypeError, ValueError, KeyError):
        return None, ""


def set_location_reference(procedure, run_mode, image, drawables, config, data):
    """Use the selected layer (or its part inside the selection) as a location's
    reference: Open reference image from Context, paint over it, then this. The
    engine keeps the earlier images."""
    layers = [d for d in drawables if isinstance(d, Gimp.Layer)]
    if not layers:
        return _error(procedure, "Select the layer that shows the place.")
    root, recorded = _reference_target(image, LOCATION_PARASITE)
    if recorded and not config.get_property("location"):
        config.set_property("location", recorded)
    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config,
                                                            PROC_SET_LOCATION_REF):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

    root = root or _image_project(image)
    if root is None:
        chosen = config.get_property("project-dir")
        if chosen is None:
            return _error(procedure, "This image isn't from a project; choose the project "
                                     "folder.")
        root = Path(chosen.get_path())
    name = (config.get_property("location") or "").strip()
    engine = config.get_property("engine-url").rstrip("/")
    try:
        manifest = _manifest_for(root)
        if manifest is None:
            raise ValueError(f"{root} has no project.json; locations need a project "
                             "container")
        location = find_location(manifest, name) if name else None
        if location is None:
            known = ", ".join(loc["name"] for loc in manifest.get("locations", []))
            raise ValueError(f"Unknown location {name!r}. This project has: "
                             f"{known or 'none'} (add one with New location… first).")
        slug = re.sub(r"[^a-z0-9]+", "_", location_key(location["name"])).strip("_")
        path = (root / "tmp"
                / f"gimp_location_{slug or 'place'}_{time.strftime('%Y%m%d-%H%M%S')}.png")
        side = _export_reference(image, layers[0], path, square=False,
                                 max_side=LOCATION_REF_MAX)
        result = _http("POST", f"{engine}/locations/reference",
                       {"project": manifest["project"]["id"], "name": location["name"],
                        "image_path": str(path)})
    except (EngineError, ValueError) as exc:
        return _error(procedure, str(exc))

    scaled = f", scaled from {side}px" if side > LOCATION_REF_MAX else ""
    Gimp.message(f"{location['name']}'s reference is now {Path(result['image']).name}"
                 f"{scaled}" + (f" (was {Path(result['replaced']).name}, kept)"
                                if result.get("replaced") else "")
                 + ". New Qwen-Image renders set there use it.")
    _notify_project_docks(root)
    return _success(procedure, layers[0])


def set_prop_reference(procedure, run_mode, image, drawables, config, data):
    """Use the selected layer (or its part inside the selection) as a prop's reference:
    Open reference image from Context, paint over it, then this. The engine keeps the
    earlier images."""
    layers = [d for d in drawables if isinstance(d, Gimp.Layer)]
    if not layers:
        return _error(procedure, "Select the layer that shows the object.")
    root, recorded = _reference_target(image, PROP_PARASITE)
    if recorded and not config.get_property("prop"):
        config.set_property("prop", recorded)
    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config,
                                                            PROC_SET_PROP_REF):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

    root = root or _image_project(image)
    if root is None:
        chosen = config.get_property("project-dir")
        if chosen is None:
            return _error(procedure, "This image isn't from a project; choose the project "
                                     "folder.")
        root = Path(chosen.get_path())
    name = (config.get_property("prop") or "").strip()
    engine = config.get_property("engine-url").rstrip("/")
    try:
        manifest = _manifest_for(root)
        if manifest is None:
            raise ValueError(f"{root} has no project.json; props need a project container")
        prop = find_prop(manifest, name) if name else None
        if prop is None:
            known = ", ".join(p["name"] for p in manifest.get("props", []))
            raise ValueError(f"Unknown prop {name!r}. This project has: "
                             f"{known or 'none'} (add one with New prop… first).")
        slug = re.sub(r"[^a-z0-9]+", "_", prop_key(prop["name"])).strip("_")
        path = (root / "tmp"
                / f"gimp_prop_{slug or 'prop'}_{time.strftime('%Y%m%d-%H%M%S')}.png")
        side = _export_reference(image, layers[0], path, square=False, max_side=REF_MAX)
        result = _http("POST", f"{engine}/props/reference",
                       {"project": manifest["project"]["id"], "name": prop["name"],
                        "image_path": str(path)})
    except (EngineError, ValueError) as exc:
        return _error(procedure, str(exc))

    scaled = f", scaled from {side}px" if side > REF_MAX else ""
    Gimp.message(f"{prop['name']}'s reference is now {result['image_name']}{scaled}"
                 ". New Qwen-Image renders that list it use it.")
    _notify_project_docks(root)
    return _success(procedure, layers[0])


def _project_progress(root):
    """'3/5 rendered · next: panel 004 (script page 2, panel 2)' for a project."""
    try:
        panels = json.loads((root / "panels.json").read_text())["panels"]
    except (OSError, ValueError, KeyError):
        return f"Project {root.name}: no readable panels.json"
    rendered = {int(p.name[:3]) for p in (root / "panels").glob("[0-9][0-9][0-9]*.png")
                if p.name[:3].isdigit()}
    done = sum(1 for seq in range(1, len(panels) + 1) if seq in rendered)
    try:
        nxt = json.loads((root / CURSOR_FILE).read_text())["next"]
    except (OSError, ValueError, KeyError):
        nxt = 1
    line = f"Project {root.name}: {done}/{len(panels)} panels rendered"
    if 1 <= nxt <= len(panels):
        spec = panels[nxt - 1]
        line += f" · next: panel {nxt:03d} ({_script_position(spec)})"
    else:
        line += " · all panels placed"
    return line


def _status_report(st, engine):
    def job(j):
        what = f"{j['kind']} panel {j['seq']:03d}" if j.get("seq") else j["kind"]
        where = f" ({j['project']})" if j.get("project") else ""
        return what + where

    c = st.get("comfyui", {})
    lines = [f"imanganation engine ({engine}): running"]
    if c.get("up"):
        gpus = "; ".join(f"{g['name'].replace('cuda:0 ', '')} {g['vram_free_gb']}/"
                         f"{g['vram_total_gb']} GB free" for g in c.get("gpus", []))
        lines.append(f"ComfyUI {c.get('version', '')} ({c.get('url')}): up · {gpus}")
    else:
        lines.append(f"ComfyUI ({c.get('url')}): DOWN: {c.get('error', '')}. "
                     "Start it with ./scripts/comfy.sh start")
    n = st.get("counts", {})
    lines.append(f"Jobs: {n.get('running', 0)} running, {n.get('queued', 0)} queued, "
                 f"{n.get('done', 0)} done, {n.get('error', 0)} failed")
    for j in st.get("running", []):
        step = j.get("progress") or {}
        steps = (f" · pass {step['pass']} step {step['step']}/{step['steps']}"
                 if step.get("steps") else "")
        lines.append(f"  ▶ {job(j)}: {j.get('elapsed_s', 0)} s so far{steps}")
    if st.get("queued"):
        lines.append("  … queued: " + ", ".join(job(j) for j in st["queued"]))
    for j in st.get("recent", []):
        mark = "✗" if j["status"] == "error" else "✓"
        tail = f": {j['error']}" if j.get("error") else f" in {j.get('took_s', '?')} s"
        lines.append(f"  {mark} {job(j)}{tail}")
    missing = [m for m in st.get("models", []) if not m.get("present")]
    if missing:
        lines.append("Models MISSING: " + "; ".join(
            f"{m['role']} ({m.get('file') or m.get('note') or '?'})" for m in missing))
    else:
        lines.append(f"Models: all {len(st.get('models', []))} present")
    return lines


def engine_status(procedure, run_mode, image, drawables, config, data):
    engine = config.get_property("engine-url").rstrip("/")
    try:
        lines = _status_report(_http("GET", f"{engine}/status"), engine)
    except EngineError as exc:
        lines = [f"imanganation engine: NOT RUNNING. {exc}"]
    root = _image_project(image) if image is not None else None
    if root is not None:
        lines.append(_project_progress(root))
    report = "\n".join(lines)
    Gimp.message(report)
    retvals = procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
    retvals.remove(1)
    retvals.insert(1, GObject.Value(GObject.TYPE_STRING, report))
    return retvals


def place_next_panel(procedure, run_mode, image, drawables, config, data):
    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config, PROC_NEXT):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

    try:
        root, panels, seq, explicit, manifest = _load_project(config)
    except ValueError as exc:
        return _error(procedure, str(exc))
    spec = panels[seq - 1]

    newest = _newest_take(root, seq, manifest)
    if newest is None:
        return _error(procedure, f"Panel {seq:03d} ({_script_position(spec)}) is not rendered yet: expected "
                                 f"panels/{seq:03d}*.png. Use Render Panel into Frame.")

    if manifest is not None:
        panel_id = manifest["panels"][seq - 1]["id"]
        if _registered_take_id(root, manifest, newest, panel_id) is None:
            try:
                width, height = _source_dimensions(newest)
                newest, _take = _record_take(
                    root, manifest, seq, newest, "import", width, height,
                    engine={"imported_from_legacy_layout": True})
            except (OSError, ValueError) as exc:
                return _error(procedure, str(exc))

    take_id = (_registered_take_id(
        root, manifest, newest, manifest["panels"][seq - 1]["id"])
        if manifest is not None else None)
    panel = manifest["panels"][seq - 1] if manifest else None
    frame = _placed_frame_for_image(image, manifest, panel)
    layer = _place(
        image, Gio.File.new_for_path(str(newest)), spec, seq,
        take_ref={"project": manifest["project"]["id"],
                  "panel": manifest["panels"][seq - 1]["id"], "take": take_id}
        if take_id else None, frame=frame)
    _advance(root, panels, seq, explicit, spec, manifest)
    if manifest is not None:
        _save_project_page(image, root, manifest)
    return _success(procedure, layer)


def place_panel(procedure, run_mode, image, drawables, config, data):
    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config, PROC_PLACE):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

    panel_file = config.get_property("panel-file")
    spec_json = config.get_property("panel-spec") or "{}"
    engine_url = config.get_property("engine-url")

    if panel_file is None:
        return _error(procedure, "panel-file is required")

    status = _ping(engine_url)
    Gimp.message(f"imanganation engine {engine_url}: {status}")

    try:
        spec = json.loads(spec_json)
    except json.JSONDecodeError:
        spec = {}
    return _success(procedure, _place(image, panel_file, spec))


def _add_project_args(proc):
    proc.add_file_argument(
        "project-dir", "_Project folder", "imanganation project folder",
        Gimp.FileChooserAction.SELECT_FOLDER, False, None, GObject.ParamFlags.READWRITE)
    proc.add_int_argument(
        "panel-number", "Panel _number", "Use this panel (1-based, in script order) "
        "instead of the next one; 0 = next", 0, 9999, 0, GObject.ParamFlags.READWRITE)


def _dock_pdb_call(name, values):
    procedure = Gimp.get_pdb().lookup_procedure(name)
    if procedure is None:
        raise RuntimeError(f"GIMP procedure is unavailable: {name}")
    config = procedure.create_config()
    for key, value in values.items():
        config.set_property(key, value)
    result = procedure.run(config)
    if result.index(0) != Gimp.PDBStatusType.SUCCESS:
        raise RuntimeError(f"GIMP procedure failed: {name}")


def _last_project_file():
    return Path(Gimp.directory()) / "imanganation" / "last-project.txt"


def _remember_project(root, closed=False):
    """Record the last project. A closed one is still remembered (the folder chooser
    starts beside it) but no longer reopened at startup."""
    state_file = _last_project_file()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_file.with_suffix(".tmp")
    temporary.write_text(str(Path(root).resolve()) + ("\nclosed" if closed else ""),
                         encoding="utf-8")
    os.replace(temporary, state_file)


def _remembered_project(include_closed=True):
    try:
        lines = _last_project_file().read_text(encoding="utf-8").splitlines()
        if not include_closed and "closed" in (line.strip() for line in lines[1:]):
            return None
        root = Path(lines[0].strip())
        load_project(root)
        return root
    except (OSError, IndexError, ProjectFileError, ValueError):
        return None


def _open_project():
    """The project to reopen: the last one, unless it was closed."""
    return _remembered_project(include_closed=False)


def _choose_project_folder():
    dialog = Gtk.FileChooserDialog(
        title="Open Imanganation Project", action=Gtk.FileChooserAction.SELECT_FOLDER)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       Gtk.STOCK_OPEN, Gtk.ResponseType.ACCEPT)
    dialog.set_modal(True)
    dialog.set_default_response(Gtk.ResponseType.ACCEPT)  # Enter accepts a typed path
    previous = _remembered_project()
    if previous is not None:
        # Start beside the last project with it selected, so switching to a sibling
        # project is one click (and Open works without clicking anything).
        dialog.set_current_folder(str(previous.parent))
        dialog.select_filename(str(previous))
    else:
        dialog.set_current_folder(str(Path.home()))  # "Recent" leaves Open disabled
    response = dialog.run()
    filename = dialog.get_filename() if response == Gtk.ResponseType.ACCEPT else None
    dialog.destroy()
    return Path(filename) if filename else None


def _choose_page_size(width, height):
    dialog = Gtk.Dialog(title="New page size", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Create", Gtk.ResponseType.OK)
    grid = Gtk.Grid(column_spacing=12, row_spacing=8, margin=12)
    width_spin = Gtk.SpinButton.new_with_range(1, 20000, 100)
    height_spin = Gtk.SpinButton.new_with_range(1, 20000, 100)
    width_spin.set_value(width)
    height_spin.set_value(height)
    grid.attach(Gtk.Label(label="Width"), 0, 0, 1, 1)
    grid.attach(width_spin, 1, 0, 1, 1)
    grid.attach(Gtk.Label(label="Height"), 0, 1, 1, 1)
    grid.attach(height_spin, 1, 1, 1, 1)
    dialog.get_content_area().add(grid)
    dialog.show_all()
    response = dialog.run()
    size = (width_spin.get_value_as_int(), height_spin.get_value_as_int()) \
        if response == Gtk.ResponseType.OK else None
    dialog.destroy()
    return size


def _choose_page_layout(combinations, page_width, page_height, page_label,
                        recommendation=None, script_matched=True):
    dialog = Gtk.Dialog(title="Choose page layout", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Apply", Gtk.ResponseType.OK)
    dialog.set_default_size(620, 520)
    content = dialog.get_content_area()
    content.set_spacing(8)
    content.set_margin_top(12)
    content.set_margin_bottom(12)
    content.set_margin_start(12)
    content.set_margin_end(12)
    first_layout = combinations[0][0]
    chooser_note = ("cover composition guides · choose a preview, then Apply"
                    if first_layout.get("cover") else
                    (f"{len(first_layout['regions'])} panels · script-matched layouts appear "
                     "first; choose a preview, then Apply" if script_matched else
                     "template layouts · choose a panel arrangement and style, then Apply"))
    content.pack_start(Gtk.Label(label=f"{page_label} · {chooser_note}"),
        False, False, 0)

    flow = Gtk.FlowBox()
    flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
    flow.set_min_children_per_line(2)
    flow.set_max_children_per_line(3)
    flow.set_row_spacing(8)
    flow.set_column_spacing(8)
    flow.set_homogeneous(True)
    for layout, style in combinations:
        ratio = page_width / page_height if page_height else 1.0
        if ratio >= 1:
            display_width = 120
            display_height = max(1, round(display_width / ratio))
        else:
            display_height = 150
            display_width = max(1, round(display_height * ratio))
        # Draw at a larger logical size so fine, classic, and bold rules stay
        # visibly distinct after GdkPixbuf scales the card to its display size.
        preview_width = display_width * 2
        preview_height = display_height * 2
        pixels = layout_preview_rgb(layout, style, preview_width, preview_height)
        png = rgb_png(preview_width, preview_height, pixels)
        if png is None:
            continue
        loader = GdkPixbuf.PixbufLoader.new()
        loader.write(png)
        loader.close()
        pixbuf = loader.get_pixbuf().scale_simple(
            display_width, display_height, GdkPixbuf.InterpType.BILINEAR)
        preview = Gtk.Image.new_from_pixbuf(pixbuf)
        prefix = ("Recommended · " if layout["name"] == recommendation
                  and style["name"] == "Fine ink" else "")
        caption_text = f"{prefix}{layout['name']}\n{style['name']}"
        if layout.get("description"):
            caption_text += f"\n{layout['description']}"
        caption = Gtk.Label(label=caption_text)
        caption.set_justify(Gtk.Justification.CENTER)
        caption.set_line_wrap(True)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        card.set_border_width(8)
        card.pack_start(preview, True, True, 0)
        card.pack_start(caption, False, False, 0)
        child = Gtk.FlowBoxChild()
        child.set_can_focus(True)
        child.set_tooltip_text(f"{layout['name']} — {layout.get('description', style['name'])}")
        child.add(card)
        flow.add(child)

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.set_min_content_height(360)
    scroll.set_min_content_width(460)
    scroll.add(flow)
    content.pack_start(scroll, True, True, 0)
    dialog.show_all()
    first = flow.get_child_at_index(0)
    if first is not None:
        flow.select_child(first)
        flow.grab_focus()
    selection = None
    if dialog.run() == Gtk.ResponseType.OK:
        selected = flow.get_selected_children()
        if selected:
            index = selected[0].get_index()
            if 0 <= index < len(combinations):
                selection = combinations[index]
    dialog.destroy()
    return selection


def _generated_layout_layers(image):
    """Find generated base and foreground frame layers, including in groups."""
    found = []

    def walk(layers):
        for item in layers:
            if (item.get_name().startswith("Template - Imanganation Layout -")
                    or item.get_name().startswith("Imanganation Layout Ink -")):
                found.append(item)
            if item.is_group():
                walk(item.get_children())

    walk(image.get_layers())
    return found


def _draw_page_layout(image, layout, frame_style, replace_layers=(), page_id=None,
                      panel_ids=(), panel_sequences=()):
    """Add a selectable frame/cover guide and any foreground overlay ink."""
    width, height = image.get_width(), image.get_height()
    selection = Gimp.Selection.save(image)
    layer = Gimp.Layer.new(
        image, f"Template - Imanganation Layout - {layout['name']} - {frame_style['name']}",
                           width, height, Gimp.ImageType.RGBA_IMAGE, 100,
                           Gimp.LayerMode.NORMAL)
    ink = None
    if not layout.get("cover") and layout.get("overlays"):
        ink = Gimp.Layer.new(
            image, f"Imanganation Layout Ink - {layout['name']} - {frame_style['name']}",
            width, height, Gimp.ImageType.RGBA_IMAGE, 100, Gimp.LayerMode.NORMAL)

    def draw_ring(target, region, style, index, color):
        Gimp.context_set_foreground(Gegl.Color.new(color))
        for outer, inner in frame_rings(region, width, height, style, index):
            image.select_polygon(Gimp.ChannelOps.REPLACE,
                                 [v for point in outer for v in point])
            image.select_polygon(Gimp.ChannelOps.SUBTRACT,
                                 [v for point in inner for v in point])
            target.edit_fill(Gimp.FillType.FOREGROUND)

    previous_foreground = Gimp.context_get_foreground()
    image.undo_group_start()
    try:
        layer.fill(Gimp.FillType.TRANSPARENT)
        image.insert_layer(layer, None, 0)
        layout_data = {"role": "selection-template", "page_id": page_id,
                       "cover": layout.get("cover"), "regions": layout.get("regions", ())}
        if layout.get("cover"):
            layer.set_name(f"Template - Imanganation Layout - Cover Guide - "
                           f"{layout['name']} (hide for export)")
            zone_colors = {"safe": "#82909B", "title": "#C35C5C", "art": "#4786AA",
                           "credits": "#4D9169", "issue": "#C18A32",
                           "blurb": "#4786AA", "barcode": "#C18A32"}
            guide_style = {"weight": 0.0028, "slant": 0.0, "double": False}
            for index, (role, region) in enumerate(layout["zones"]):
                draw_ring(layer, region, guide_style, index, zone_colors.get(role, "gray"))
            layout_data["cover_zones"] = layout["zones"]
        else:
            borderless = set(layout.get("borderless", ()))
            guide_style = {"weight": 0.0035, "slant": 0.0, "double": False}
            for index, region in enumerate(layout["regions"]):
                if index in borderless:
                    draw_ring(layer, region, guide_style, index, "cyan")
                else:
                    draw_ring(layer, region, frame_style, index, "black")

            records = []
            for index in layout.get("overlays", ()):
                if index >= len(layout["regions"]):
                    continue
                records.append({
                    "index": index,
                    "panel_id": panel_ids[index] if index < len(panel_ids) else None,
                    "sequence": (panel_sequences[index]
                                 if index < len(panel_sequences) else None),
                    "region": layout["regions"][index],
                    "borderless": index in borderless,
                })
            layout_data["overlays"] = records
            if records:
                ink.fill(Gimp.FillType.TRANSPARENT)
                image.insert_layer(ink, None, 0)
                for record in records:
                    if not record["borderless"]:
                        draw_ring(ink, record["region"], frame_style,
                                  record["index"], "black")

        layer.attach_parasite(Gimp.Parasite.new(
            LAYOUT_PARASITE, Gimp.PARASITE_PERSISTENT,
            list(json.dumps(layout_data, separators=(",", ":")).encode())))
        for old_layer in replace_layers:
            if old_layer.get_image() is image:
                image.remove_layer(old_layer)
    except Exception:
        if ink is not None and ink.get_image() is image:
            image.remove_layer(ink)
        if layer.get_image() is image:
            image.remove_layer(layer)
        raise
    finally:
        image.select_item(Gimp.ChannelOps.REPLACE, selection)
        Gimp.context_set_foreground(previous_foreground)
        image.remove_channel(selection)
        image.undo_group_end()
    image.set_selected_layers([layer])
    Gimp.displays_flush()
    return layer


def _unplace_page_panels(image, manifest, page_id):
    """Drop this page's placed panels (canvas groups and placements) ahead of a new
    layout. Takes stay registered, so they can be placed again into the new frames."""
    count = 0
    image.undo_group_start()
    try:
        for panel in manifest.get("panels", []):
            if (panel.get("placement") or {}).get("page") != page_id:
                continue
            group = _find_panel_group(
                image, {"project": manifest["project"]["id"], "panel": panel["id"]})
            if group is not None:
                image.remove_layer(group)
            panel["placement"] = None
            panel["status"] = "unplaced"
            count += 1
    finally:
        image.undo_group_end()
    return count


def page_layout(procedure, run_mode, image, drawables, config, data):
    """Generate a selectable frame template matching the open project's script page."""
    try:
        if load_project is None or frame_rings is None or page_layout_availability is None:
            raise ValueError("This plug-in install is missing project layout support")
        # Image procedures run in their own plug-in invocation, so the dock's
        # module globals are not guaranteed to be present here. Prefer the
        # project explicitly passed by the dock, then use the persisted project
        # for direct menu invocation.
        project_file = config.get_property("project-dir")
        root = (Path(project_file.get_path()) if project_file is not None else None)
        root = root or _DOCK_CONTEXT.get("root") or _open_project()
        if root is None:
            raise ValueError("Open an Imanganation project first")
        manifest = load_project(root)
        page_id = _project_page_id_for_image(image, manifest)
        if page_id is None:
            raise ValueError("Open a page from the active Imanganation project first")
        page = next(page for page in manifest["pages"] if page["id"] == page_id)
        availability = page_layout_availability(
            manifest, page_id, image.get_width() / image.get_height())
        if not availability["available"]:
            raise ValueError(availability["reason"])
        combinations = availability["combinations"]
        existing_layers = _generated_layout_layers(image)
        if availability.get("placed") and run_mode == Gimp.RunMode.INTERACTIVE:
            ask = Gtk.MessageDialog(
                message_type=Gtk.MessageType.WARNING, modal=True,
                text=f"Change the layout of {page.get('label', 'this page')}?",
                secondary_text=f"Its {availability['placed']} placed panel(s) will be "
                "unplaced and removed from the page. Their renders are kept, and you can "
                "place them again into the new frames.")
            ask.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                            "Change Layout", Gtk.ResponseType.OK)
            answer = ask.run()
            ask.destroy()
            if answer != Gtk.ResponseType.OK:
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        selection = _choose_page_layout(
            combinations, image.get_width(), image.get_height(),
            page.get("label", "Page"), availability.get("recommendation"),
            availability.get("script_matched", True)) \
            if run_mode == Gimp.RunMode.INTERACTIVE \
            else combinations[0]
        if selection is None:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        layout, frame_style = selection
        unplaced = _unplace_page_panels(image, manifest, page_id) \
            if availability.get("placed") and not layout.get("cover") else 0
        layer = _draw_page_layout(
            image, layout, frame_style, existing_layers, page_id,
            availability.get("panel_ids", ()), availability.get("panel_sequences", ()))
        if layout.get("cover"):
            Gimp.message(f"Added {layout['name']} guides to {page.get('label', 'page')}. "
                         "Gray = safe area, red = title, blue = artwork/blurb, green = "
                         "credits, amber = issue mark/barcode. Hide the Cover Guide layer "
                         "before export. This guide does not create script panels.")
        else:
            Gimp.message(f"Added {layout['name']} with {frame_style['name']} frames to "
                         f"{page.get('label', 'page')}. Cyan outlines mark borderless "
                         "panels for selection; overlay panels are placed above the frame "
                         "template. Select inside a frame with Fuzzy Select to render it.")
        if unplaced:
            manifest["project"]["modified"] = datetime.now().astimezone().isoformat(
                timespec="seconds")
            save_project(root, manifest)
            Gimp.message(f"{unplaced} panel(s) were unplaced by the new layout. Their "
                         "renders are kept: place them again into the new frames.")
        _save_project_page(image, root, manifest)
        return _success(procedure, layer)
    except Exception as exc:
        return _error(procedure, str(exc))


def _export_dialog(project_title, reading_order):
    """Choose a distribution format and the page-image settings."""
    dialog = Gtk.Dialog(title="Export Imanganation Project", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Export…", Gtk.ResponseType.OK)
    dialog.set_default_size(500, 430)
    grid = Gtk.Grid(column_spacing=12, row_spacing=10, margin=12)
    format_combo = Gtk.ComboBoxText()
    for ident, label in (("cbz", "CBZ comic archive"), ("pdf", "PDF volume"),
                         ("images", "Page image folder"),
                         ("vertical", "Vertical comic episode")):
        format_combo.append(ident, label)
    format_combo.set_active_id("cbz")
    image_combo = Gtk.ComboBoxText()
    image_combo.append("png", "PNG · lossless")
    image_combo.append("jpeg", "JPEG · smaller files")
    image_combo.set_active_id("png")
    direction_combo = Gtk.ComboBoxText()
    direction_combo.append("rtl", "Right to left (manga)")
    direction_combo.append("ltr", "Left to right")
    direction_combo.set_active_id(reading_order if reading_order in {"rtl", "ltr"} else "rtl")
    quality = Gtk.SpinButton.new_with_range(50, 100, 1)
    quality.set_value(95)
    quality_label = Gtk.Label(label="JPEG quality")
    quality_label.set_xalign(0)
    include_cover = Gtk.CheckButton(label="Include cover pages")
    include_cover.set_active(True)
    include_frames = Gtk.CheckButton(label="Include generated panel frame layers")
    include_frames.set_active(False)
    include_frames.set_tooltip_text(
        "Off exports the artwork without selectable panel layout guides or frame ink.")
    include_metadata = Gtk.CheckButton(label="Include ComicInfo.xml archive metadata")
    include_metadata.set_active(True)
    include_metadata.set_tooltip_text("Included only with CBZ exports.")
    reverse = Gtk.CheckButton(label="Reverse project page order")
    reverse.set_active(False)
    grid.attach(Gtk.Label(label="Format"), 0, 0, 1, 1)
    grid.attach(format_combo, 1, 0, 1, 1)
    grid.attach(Gtk.Label(label="Page images"), 0, 1, 1, 1)
    grid.attach(image_combo, 1, 1, 1, 1)
    grid.attach(quality_label, 0, 2, 1, 1)
    grid.attach(quality, 1, 2, 1, 1)
    grid.attach(include_cover, 0, 3, 2, 1)
    grid.attach(include_frames, 0, 4, 2, 1)
    grid.attach(include_metadata, 0, 5, 2, 1)
    grid.attach(reverse, 0, 6, 2, 1)
    grid.attach(Gtk.Label(label="Reading direction"), 0, 7, 1, 1)
    grid.attach(direction_combo, 1, 7, 1, 1)
    page_range = Gtk.Entry(text="all", activates_default=True)
    page_range.set_tooltip_text("Which pages, counted in the Pages strip: all, or "
                                "something like 1-4, 7")
    grid.attach(Gtk.Label(label="Pages"), 0, 8, 1, 1)
    grid.attach(page_range, 1, 8, 1, 1)
    preset = Gtk.ComboBoxText()
    for ident, label in (("webtoon", "WEBTOON CANVAS"), ("tapas", "Tapas"),
                         ("custom", "Custom")):
        preset.append(ident, label)
    preset.set_active_id("webtoon")
    vertical_width = Gtk.SpinButton.new_with_range(100, 10000, 10)
    vertical_width.set_value(800)
    slice_height = Gtk.SpinButton.new_with_range(100, 50000, 10)
    slice_height.set_value(1280)
    page_gap = Gtk.SpinButton.new_with_range(0, 2000, 4)
    page_gap.set_value(48)
    for row, label, widget in ((9, "Vertical preset", preset),
                               (10, "Output width (px)", vertical_width),
                               (11, "Maximum slice height (px)", slice_height),
                               (12, "Gap between pages (px)", page_gap)):
        grid.attach(Gtk.Label(label=label), 0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)
    dialog.get_content_area().add(grid)

    def image_format_changed(_combo):
        is_jpeg = image_combo.get_active_id() == "jpeg"
        quality.set_sensitive(is_jpeg)
        quality_label.set_sensitive(is_jpeg)

    def export_format_changed(_combo):
        include_metadata.set_sensitive(format_combo.get_active_id() == "cbz")
        vertical = format_combo.get_active_id() == "vertical"
        preset.set_sensitive(vertical)
        vertical_width.set_sensitive(vertical)
        slice_height.set_sensitive(vertical)
        page_gap.set_sensitive(vertical)

    def preset_changed(_combo):
        values = export_formats.VERTICAL_PRESETS.get(preset.get_active_id() or "webtoon")
        if values:
            vertical_width.set_value(values["width"])
            slice_height.set_value(values["slice_height"])

    image_combo.connect("changed", image_format_changed)
    format_combo.connect("changed", export_format_changed)
    preset.connect("changed", preset_changed)
    preset_changed(preset)
    image_format_changed(image_combo)
    export_format_changed(format_combo)
    dialog.show_all()
    response = dialog.run()
    choices = None
    if response == Gtk.ResponseType.OK:
        choices = {"format": format_combo.get_active_id() or "cbz",
                   "image_format": image_combo.get_active_id() or "png",
                   "quality": quality.get_value_as_int(),
                   "include_cover": include_cover.get_active(),
                   "include_frames": include_frames.get_active(),
                   "include_metadata": include_metadata.get_active(),
                   "reverse": reverse.get_active(),
                   "reading_order": direction_combo.get_active_id() or "rtl",
                   "page_range": page_range.get_text(),
                   "vertical_preset": preset.get_active_id() or "custom",
                   "vertical_width": vertical_width.get_value_as_int(),
                   "slice_height": slice_height.get_value_as_int(),
                   "page_gap": page_gap.get_value_as_int()}
    dialog.destroy()
    return choices


def _export_destination(project_title, export_format):
    if export_format in {"images", "vertical"}:
        action = Gtk.FileChooserAction.SELECT_FOLDER
        title, accept = ("Choose parent folder for vertical episode" if export_format == "vertical"
                         else "Choose parent folder for page images"), "Select"
    else:
        action = Gtk.FileChooserAction.SAVE
        title, accept = "Save Imanganation export", "Save"
    chooser = Gtk.FileChooserDialog(title=title, action=action)
    chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        accept, Gtk.ResponseType.ACCEPT)
    chooser.set_do_overwrite_confirmation(True)
    if export_format not in {"images", "vertical"}:
        extension = ".cbz" if export_format == "cbz" else ".pdf"
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", project_title).strip(".-_") or "manga"
        chooser.set_current_name(stem + extension)
    response = chooser.run()
    selected = chooser.get_filename() if response == Gtk.ResponseType.ACCEPT else None
    chooser.destroy()
    return Path(selected) if selected else None


def _remove_export_guides(image, include_frames=False):
    """Remove generated guide layers from a disposable export image copy."""
    def walk(layers):
        for layer in list(layers):
            parasite = layer.get_parasite(LAYOUT_PARASITE)
            if parasite is not None:
                try:
                    metadata = json.loads(bytes(parasite.get_data()))
                except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                    metadata = {}
                if metadata.get("role") == "selection-template" and metadata.get("cover"):
                    image.remove_layer(layer)
                    continue
            name = layer.get_name()
            if not include_frames and (name.lower().startswith("template")
                                       or name.startswith("Imanganation Layout Ink -")):
                image.remove_layer(layer)
                continue
            if layer.is_group():
                walk(layer.get_children())

    walk(image.get_layers())


def _save_project_export_page(image, path, image_format, quality, include_frames=False):
    copy = image.duplicate()
    try:
        _remove_export_guides(copy, include_frames)
        copy.flatten()
        path.parent.mkdir(parents=True, exist_ok=True)
        if image_format == "jpeg":
            procedure = Gimp.get_pdb().lookup_procedure("file-jpeg-export")
            if procedure is None:
                raise RuntimeError("GIMP's JPEG exporter is unavailable")
            config = procedure.create_config()
            config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
            config.set_property("image", copy)
            config.set_property("file", Gio.File.new_for_path(str(path)))
            config.set_property("quality", quality / 100.0)
            try:
                config.set_property("use-original-quality", False)
            except Exception:
                pass
            result = procedure.run(config)
            if result.index(0) != Gimp.PDBStatusType.SUCCESS:
                raise RuntimeError(f"GIMP could not export {path.name} as JPEG")
        else:
            result = Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, copy,
                                    Gio.File.new_for_path(str(path)), None)
            if result is False:
                raise RuntimeError(f"GIMP could not export {path.name} as PNG")
    finally:
        copy.delete()


def export_project(procedure, run_mode, image, drawables, config, data):
    """Export a project volume as CBZ, PDF, or an ordered page-image folder."""
    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config, PROC_EXPORT_PROJECT):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
    try:
        if load_project is None or export_formats is None:
            raise ValueError("This plug-in install is missing project export support")
        project_file = config.get_property("project-dir")
        root = Path(project_file.get_path()) if project_file is not None else None
        root = root or _DOCK_CONTEXT.get("root") or _open_project()
        if root is None:
            raise ValueError("Open an Imanganation project first")
        manifest = load_project(root)
        pages = list(manifest.get("pages", []))
        if not pages:
            raise ValueError("This project has no pages to export")
        options = _export_dialog(
            manifest["project"].get("title", "Manga"),
            manifest["project"].get("reading_order", "rtl"))
        if options is None:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        chosen_pages = export_formats.parse_page_range(  # before asking where to save
            options.get("page_range"), len(pages))
        destination = _export_destination(
            manifest["project"].get("title", "Manga"), options["format"])
        if destination is None:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

        pages = [pages[i] for i in chosen_pages]
        if not options["include_cover"]:
            pages = [page for page in pages if not export_formats.is_cover_page(page)]
        if options["reverse"]:
            pages.reverse()
        if not pages:
            raise ValueError("No pages remain after applying the export options")
        missing = [page.get("file", "(no page file)") for page in pages
                   if not page.get("file") or not (root / page["file"]).is_file()]
        if missing:
            raise ValueError("Page file is missing: " + ", ".join(missing))

        # Save every open project page first so its current canvas, not a stale XCF,
        # is what the volume export captures.
        for open_image in Gimp.get_images():
            if _project_page_id_for_image(open_image, manifest) is not None:
                if not _save_project_page(open_image, root, manifest):
                    raise ValueError("Save the open project page before exporting")

        suffix = ".jpg" if options["image_format"] == "jpeg" else ".png"
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-",
                      manifest["project"].get("title", "manga")).strip(".-_") or "manga"
        if options["format"] == "images":
            destination = destination / f"{stem}-pages"
            if destination.exists():
                raise ValueError(f"Export folder already exists: {destination}")
            destination.mkdir(parents=True)
        elif options["format"] == "vertical":
            destination = destination / f"{stem}-vertical"
            if destination.exists():
                raise ValueError(f"Export folder already exists: {destination}")
        temporary = root / "tmp" / f"export-{secrets.token_hex(6)}"
        temporary.mkdir(parents=True, exist_ok=False)
        page_files = []
        pdf_pages = []
        try:
            for index, page in enumerate(pages, 1):
                page_id = page.get("id")
                current = next((opened for opened in Gimp.get_images()
                                if _project_page_id_for_image(opened, manifest) == page_id), None)
                loaded = current or Gimp.file_load(
                    Gimp.RunMode.NONINTERACTIVE,
                    Gio.File.new_for_path(str(root / page["file"])))
                close_after = current is None
                try:
                    ext = ".png" if options["format"] == "vertical" else suffix
                    filename = export_formats.safe_page_stem(page.get("label"), index) + ext
                    staged = temporary / filename
                    _save_project_export_page(loaded, staged, options["image_format"],
                                              options["quality"],
                                              options["include_frames"])
                    page_files.append((filename, staged))
                    resolution = loaded.get_resolution()
                    ppi = float(resolution[0]) if resolution else 72.0
                    pdf_pages.append((staged, ppi))
                finally:
                    if close_after:
                        loaded.delete()
            if options["format"] == "cbz":
                destination = destination.with_suffix(".cbz")
                export_formats.write_cbz(
                    destination, page_files,
                    manifest["project"].get("title", ""),
                    manifest["project"].get("chapter", ""),
                    options["reading_order"], options["include_metadata"])
            elif options["format"] == "pdf":
                destination = destination.with_suffix(".pdf")
                export_formats.write_pdf(destination, pdf_pages,
                                         manifest["project"].get("title", ""))
            elif options["format"] == "images":
                for filename, staged in page_files:
                    os.replace(staged, destination / filename)
            else:
                slice_dir = temporary / "slices"
                preset = export_formats.VERTICAL_PRESETS.get(
                    options.get("vertical_preset"), export_formats.VERTICAL_PRESETS["custom"])
                slices = export_formats.write_vertical_slices(
                    page_files, slice_dir, width=options["vertical_width"],
                    slice_height=options["slice_height"], gap=options["page_gap"],
                    prefix=stem)
                if options["image_format"] == "jpeg":
                    for slice_path in slices:
                        loaded = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                                                Gio.File.new_for_path(str(slice_path)))
                        jpg_path = slice_path.with_suffix(".jpg")
                        try:
                            _save_project_export_page(loaded, jpg_path, "jpeg",
                                                      options["quality"], True)
                        finally:
                            loaded.delete()
                        slice_path.unlink()
                    slices = sorted(slice_dir.glob("*.jpg"))
                total_bytes = sum(path.stat().st_size for path in slices)
                max_file = preset["max_file_mb"] * 1024 * 1024
                max_episode = preset["max_episode_mb"] * 1024 * 1024
                too_large = [path.name for path in slices
                             if max_file and path.stat().st_size > max_file]
                if too_large or (max_episode and total_bytes > max_episode):
                    reasons = []
                    if too_large:
                        reasons.append("over per-image limit: " + ", ".join(too_large))
                    if max_episode and total_bytes > max_episode:
                        reasons.append(f"episode is {total_bytes / 1048576:.1f} MB, above "
                                       f"the {preset['max_episode_mb']} MB limit")
                    raise ValueError("Vertical export exceeds the preset limits (" +
                                     "; ".join(reasons) +"). Lower JPEG quality or output "
                                     "width, or choose Custom to export without platform limits.")
                destination.mkdir(parents=True)
                for path in slices:
                    os.replace(path, destination / path.name)
                (destination / "export-info.txt").write_text(
                    f"Preset: {preset['label']}\nWidth: {options['vertical_width']} px\n"
                    f"Maximum slice height: {options['slice_height']} px\n"
                    f"Page gap: {options['page_gap']} px\nFormat: {options['image_format']}\n"
                    f"Total: {total_bytes} bytes\n\n"
                    + "\n".join(path.name for path in slices) + "\n", encoding="utf-8")
        finally:
            import shutil
            shutil.rmtree(temporary, ignore_errors=True)
        if options["format"] == "vertical":
            Gimp.message(f"Exported {len(slices)} vertical slices ({total_bytes / 1048576:.1f} MB) "
                         f"to {destination}")
        else:
            Gimp.message(f"Exported {len(pages)} pages to {destination}")
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
    except Exception as exc:
        return _error(procedure, str(exc))


def _cover_wrap_text(text, font, size, max_width):
    """Greedy word wrap using GIMP's font extents for the chosen typeface."""
    lines = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            _, width, *_ = Gimp.text_get_extents_font(candidate, size, font)
            if current and width > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return "\n".join(lines)


def _cover_add_text(image, parent, text, name, region, font, color, page_width,
                    preferred_size):
    """Create centered, editable text scaled to fit a normalized cover zone."""
    if not text.strip() or region is None:
        return None
    x, y, w, h = region
    x, y, w, h = x * page_width, y * image.get_height(), w * page_width, h * image.get_height()
    inset_w, inset_h = w * 0.92, h * 0.88
    size = max(14.0, preferred_size)
    wrapped = text.strip()
    while size > 13.5:
        wrapped = _cover_wrap_text(text.strip(), font, size, inset_w)
        _, text_width, text_height, *_ = Gimp.text_get_extents_font(wrapped, size, font)
        if text_width <= inset_w and text_height <= inset_h:
            break
        size *= 0.9
    layer = Gimp.TextLayer.new(image, wrapped, font, size, Gimp.Unit.pixel())
    layer.set_name(name)
    image.insert_layer(layer, parent, 0)
    layer.set_justification(Gimp.TextJustification.CENTER)
    layer.set_color(color)
    layer.set_offsets(round(x + (w - layer.get_width()) / 2),
                      round(y + (h - layer.get_height()) / 2))
    return layer


def _cover_designer_dialog():
    dialog = Gtk.Dialog(title="Design Front Cover", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Create cover text", Gtk.ResponseType.OK)
    dialog.set_default_size(480, 440)
    grid = Gtk.Grid(column_spacing=12, row_spacing=8, margin=12)
    dialog.get_content_area().add(grid)
    layout_combo = Gtk.ComboBoxText()
    for layout in COVER_LAYOUTS:
        if layout.get("cover") == "front":
            layout_combo.append_text(layout["name"])
    layout_combo.set_active(0)
    title_entry = Gtk.Entry()
    subtitle_entry = Gtk.Entry()
    credit_entry = Gtk.Entry()
    title_entry.set_placeholder_text("Required")
    subtitle_entry.set_placeholder_text("Optional")
    credit_entry.set_placeholder_text("Optional")
    font_combo = Gtk.ComboBoxText()
    for font_name in ("Bangers", "Comic Neue Bold", "Sans-serif Bold"):
        if Gimp.Font.get_by_name(font_name) is not None:
            font_combo.append_text(font_name)
    if font_combo.get_active() < 0:
        font_combo.append_text("Current GIMP font")
    font_combo.set_active(0)
    color_combo = Gtk.ComboBoxText()
    color_combo.append_text("White")
    color_combo.append_text("Black")
    color_combo.set_active(0)
    guide_check = Gtk.CheckButton(label="Add safe-area and placement guides")
    guide_check.set_active(True)
    rows = (("Template", layout_combo), ("Title", title_entry),
            ("Subtitle / volume", subtitle_entry), ("Creator credits", credit_entry),
            ("Display font", font_combo), ("Text color", color_combo))
    for row, (label, widget) in enumerate(rows):
        grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)
    grid.attach(guide_check, 1, len(rows), 1, 1)
    hint = Gtk.Label(label="Text is created as editable GIMP layers over your cover art.")
    hint.set_line_wrap(True)
    hint.set_xalign(0)
    grid.attach(hint, 0, len(rows) + 1, 2, 1)
    dialog.show_all()
    if dialog.run() != Gtk.ResponseType.OK:
        dialog.destroy()
        return None
    result = {
        "layout_name": layout_combo.get_active_text(),
        "title": title_entry.get_text().strip(),
        "subtitle": subtitle_entry.get_text().strip(),
        "credits": credit_entry.get_text().strip(),
        "font_name": font_combo.get_active_text(),
        "color": color_combo.get_active_text(),
        "guides": guide_check.get_active(),
    }
    dialog.destroy()
    if not result["title"]:
        raise ValueError("Enter a cover title")
    return result


def cover_designer(procedure, run_mode, image, drawables, config, data):
    """Place editable front-cover typography using the built-in composition zones."""
    if COVER_LAYOUTS is None or frame_rings is None:
        return _error(procedure, "This plug-in install is missing cover layout support")
    if run_mode != Gimp.RunMode.INTERACTIVE:
        return _error(procedure, "The cover designer requires interactive mode")
    try:
        values = _cover_designer_dialog()
        if values is None:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        layout = next((candidate for candidate in COVER_LAYOUTS
                       if candidate["name"] == values["layout_name"]), None)
        if layout is None:
            raise ValueError("Choose a front-cover template")
        zones = dict(layout["zones"])
        guide = (_draw_page_layout(image, layout, {"name": "Cover guides"})
                 if values["guides"] else None)
        font = (Gimp.Font.get_by_name(values["font_name"])
                if values["font_name"] != "Current GIMP font" else None)
        font = font or Gimp.context_get_font()
        color = Gegl.Color.new("white" if values["color"] == "White" else "black")
        title_zone = zones.get("title")
        subtitle_zone = zones.get("issue")
        if subtitle_zone is None and title_zone is not None:
            x, y, w, h = title_zone
            title_zone = (x, y, w, h * 0.72)
            subtitle_zone = (x, y + h * 0.72, w, h * 0.28)

        group = Gimp.GroupLayer.new(image, "Cover Typography")
        image.undo_group_start()
        try:
            image.insert_layer(group, None, 0)
            title_height = (title_zone[3] * image.get_height()) if title_zone else 100
            _cover_add_text(image, group, values["title"], "Cover title", title_zone,
                            font, color, image.get_width(), min(title_height * 0.38, 240))
            _cover_add_text(image, group, values["subtitle"], "Subtitle / volume",
                            subtitle_zone, font, color, image.get_width(),
                            min((subtitle_zone[3] * image.get_height() * 0.58)
                                if subtitle_zone else 40, 96))
            _cover_add_text(image, group, values["credits"], "Creator credits",
                            zones.get("credits"), font, color, image.get_width(),
                            min((zones.get("credits", (0, 0, 0, 0))[3]
                                 * image.get_height() * 0.55), 72))
            group.attach_parasite(Gimp.Parasite.new(
                "imanganation-cover-text", Gimp.PARASITE_PERSISTENT,
                list(json.dumps({"layout": layout["name"], "title": values["title"]},
                                separators=(",", ":")).encode("utf-8"))))
        except Exception:
            if group.get_image() is image:
                image.remove_layer(group)
            if guide is not None and guide.get_image() is image:
                image.remove_layer(guide)
            raise
        finally:
            image.undo_group_end()
        image.set_selected_layers([group])
        Gimp.displays_flush()
        Gimp.message(f"Added editable cover text using {layout['name']}. "
                     "Move or restyle the text layers in the Cover Typography group.")
        return _success(procedure, group)
    except Exception as exc:
        return _error(procedure, str(exc))


def _activate_project(root):
    root = Path(root).resolve()
    manifest = load_project(root)
    selected = manifest.get("cursor", {}).get("next_panel")
    if not selected:
        selected = next((panel["id"] for panel in manifest["panels"]), None)
    if not selected and manifest["pages"]:
        selected = manifest["pages"][0]["id"]
    _remember_project(root)
    _adopt_fingerprints(root)
    _DOCK_CONTEXT.update(root=root, selected_id=selected,
                         orphan_id=None, candidate_id=None)
    _register_project_docks(_DOCK_PLUGIN)


_SERVICES = {}  # name -> Popen, for services the workspace started itself


def _url_up(url):
    try:
        urllib.request.urlopen(url, timeout=2).close()
    except urllib.error.HTTPError:
        pass  # something answers on the port
    except (urllib.error.URLError, OSError):
        return False
    return True


def _die_with_parent():
    """Child pre-exec: SIGTERM when the workspace extension (and so GIMP) exits."""
    try:
        import ctypes
        import signal
        ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:
        pass


def _host_command(command, cwd):
    """Run ``command`` on the host, not in the Flatpak sandbox: the engine and ComfyUI
    need the host's GPU, CUDA and Python. --watch-bus ends the host process when
    flatpak-spawn (which dies with GIMP) does."""
    if not IN_FLATPAK or ENGINE is None or ENGINE.mode != "host":
        return command  # the bundled engine runs in the sandbox, as GIMP does
    return ["flatpak-spawn", "--host", "--watch-bus", f"--directory={cwd}", *command]


def _start_service(name, command, cwd, log):
    import subprocess

    try:
        with open(log, "wb") as out:
            _SERVICES[name] = subprocess.Popen(
                _host_command(command, cwd), cwd=cwd, stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, preexec_fn=_die_with_parent)
    except OSError as exc:
        Gimp.message(f"Could not start {name}: {exc} (see {log})")


def _start_engine_services():
    """Start the engine (and, for a host checkout, ComfyUI) with GIMP, unless they are
    already running. They stop when GIMP quits; anything already running is left
    alone. The bundled engine (the Flatpak) starts its own ComfyUI."""
    if os.environ.get("IMANGANATION_AUTOSTART", "1") == "0" or not ENGINE_FOUND:
        return
    port = COMFY_URL.rsplit(":", 1)[1]
    comfy = engine_services.comfy_command(ENGINE, port)
    if comfy is not None and not _url_up(COMFY_URL + "/system_stats"):
        _start_service("ComfyUI", comfy, ENGINE.home / "vendor/ComfyUI",
                       ENGINE.home / "comfyui.log")
        if "ComfyUI" in _SERVICES:  # so scripts/comfy.sh status/stop see it
            (ENGINE.home / ".comfyui.pid").write_text(f"{_SERVICES['ComfyUI'].pid}\n")
    command = engine_services.engine_command(ENGINE)
    if command is not None and not _url_up(ENGINE_URL + "/health"):
        ENGINE.data.mkdir(parents=True, exist_ok=True)
        _start_service("engine", command, ENGINE.home or ENGINE.data, ENGINE.log)
    if "engine" in _SERVICES:
        _refresh_when_engine_up(time.monotonic() + 180)


def _refresh_when_engine_up(deadline):
    """Redraw the docks once the engine answers, so their status rows catch up."""
    def poll():
        process = _SERVICES.get("engine")
        if _url_up(ENGINE_URL + "/health"):
            try:
                _refresh_project_docks()
            except Exception:
                pass
            return GLib.SOURCE_REMOVE
        if process is not None and process.poll() is not None:
            Gimp.message(f"The imanganation engine exited (code {process.returncode}); "
                         f"see {ENGINE.log}")
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE if time.monotonic() < deadline else GLib.SOURCE_REMOVE

    GLib.timeout_add_seconds(2, _exclusive(poll))


def _engine_status(exc):
    """Short engine error for a dock row (the full message suits dialogs)."""
    if isinstance(exc.__cause__, (urllib.error.URLError, OSError)) and not isinstance(
            exc.__cause__, urllib.error.HTTPError):
        process = _SERVICES.get("engine")
        if process is not None and process.poll() is None:
            return "Engine starting…"
        return "Engine not running · start it with: uv run manganation serve"
    return f"Unavailable ({exc})"


def _engine_reference_rows(root, manifest, selected_id):
    panel = next((p for p in manifest["panels"] if p["id"] == selected_id), None)
    if panel is None or not panel.get("characters"):
        return []
    query = urllib.parse.urlencode(_engine_project(root, manifest))
    try:
        engine_characters = _http("GET", f"{ENGINE_URL}/characters?{query}", timeout=3)
    except EngineError as exc:
        return ["# Engine references", f"Status\t{_engine_status(exc)}"]
    by_name = {c.get("name", "").casefold(): c for c in engine_characters}
    rows = ["# Engine references"]
    for character in panel["characters"]:
        name = character.get("name", "")
        info = by_name.get(name.casefold())
        if info is None:
            rows.append(f"{name}\tNot found in engine")
            continue
        requested = character.get("version") or info.get("default_version") or "active"
        versions = ", ".join(info.get("versions", [])) or "None"
        reference = "Available" if info.get("reference") else "Not set"
        rows.append(f"{name}\t{requested} · versions: {versions} · reference: {reference}")
    return rows


def _engine_character_rows(root, character_name, open_buttons=False):
    query = urllib.parse.urlencode(_engine_project(root))
    try:
        characters = _http("GET", f"{ENGINE_URL}/characters?{query}", timeout=3)
    except EngineError as exc:
        return ["# Engine character record", f"Status\t{_engine_status(exc)}"]
    character = next((c for c in characters
                      if c.get("name", "").casefold() == character_name.casefold()), None)
    if character is None:
        return ["# Engine character record", "Status\tNot found in engine"]
    versions = character.get("versions", [])
    reference = "Available" if character.get("reference") else "Not set"
    rows = [
        "# Engine character record",
        f"Default version\t{character.get('default_version') or 'None'}",
        f"Versions\t{', '.join(versions) or 'None'}",
        f"Reference image\t{reference}",
    ]
    if open_buttons:  # each reference image opens on its own, not just the default
        images = character.get("version_images") or {}
        rows += [f"{version}\t{'Default' if version == character.get('default_version') else 'Extra'}"
                 f"\t!{DOCK_MANAGE_REFERENCE}:{version}:Manage…"
                 for version in versions if images.get(version)]
    return rows


def _engine_location(root, manifest, name):
    """The engine's record of this place ({"key", "image", "description", …}), or None
    if it has none. Raises EngineError when the engine isn't answering."""
    query = urllib.parse.urlencode(_engine_project(root, manifest))
    known = _http("GET", f"{ENGINE_URL}/locations?{query}", timeout=3)
    key = location_key(name)
    return next((loc for loc in known if loc.get("key") == key), None)


def _engine_location_rows(root, manifest, name):
    rows = ["# Engine location reference"]
    rows += _design_rows("location", location_key(name))
    try:
        record = _engine_location(root, manifest, name)
    except EngineError as exc:
        return rows + [f"Status\t{_engine_status(exc)}"]
    if record is None or not record.get("image"):
        rows.append("Reference image\tNot designed yet")
    else:
        versions = len(record.get("previous", [])) + 1
        rows.extend([
            f"Reference image\t{Path(record['image']).name}"
            + (f" · {versions} versions" if versions > 1 else ""),
            f"Designed\t{record.get('created_at') or 'Unknown'}",
            f"!{DOCK_OPEN_LOCATION_IMAGE}\tOpen reference image",
            "Paint over\tOpen it, edit, then Imanganation > Set Location Reference "
            "from Layer…",
        ])
        if record.get("details"):
            rows.append(f"Setting tags\t{', '.join(record['details'])}")
    if engine_ui is not None and engine_ui.project_render(manifest)["engine"] != "qwen_image_21":
        rows.append("Used by\tQwen-Image 2.1 renders only (Render Engine…)")
    return rows


def _canvas_take_rows(manifest):
    """Describe the selected canvas layer when it has a project take reference."""
    try:
        image = Gimp.context_get_image()
        if image is None:
            return []
        rows = []
        for layer in image.get_selected_layers():
            panel_parasite = layer.get_parasite(PANEL_PARASITE)
            if panel_parasite is not None:
                panel_ref = json.loads(bytes(panel_parasite.get_data()))
                if panel_ref.get("project") == manifest["project"]["id"]:
                    rows.append(f"Panel group\t{panel_ref.get('panel')}")
            parasite = layer.get_parasite(TAKE_PARASITE)
            if parasite is None:
                continue
            reference = json.loads(bytes(parasite.get_data()))
            if reference.get("project") != manifest["project"]["id"]:
                continue
            take_id = reference.get("take")
            take = manifest["takes"].get(take_id)
            panel = next((item for item in manifest["panels"]
                          if item["id"] == reference.get("panel")), None)
            if take is None or panel is None or take.get("panel") != panel["id"]:
                rows.append(f"{layer.get_name()}\tReference is not in this manifest")
                continue
            parent = take.get("parent") or "None (origin)"
            rows.append(f"{layer.get_name()}\t{take.get('kind', 'take')} · "
                        f"{take_id} · parent: {parent}")
            rows.append(f"File\t{take.get('file', 'Unknown')}")
        return ["# Canvas selection", *rows] if rows else []
    except Exception as exc:
        return ["# Canvas selection", f"Status\tUnavailable ({exc})"]


def _selected_canvas_panel_id(manifest):
    """Return the panel whose take is selected in the current project image."""
    try:
        image = Gimp.context_get_image()
        if image is None:
            return None
        panels = {panel["id"]: panel for panel in manifest["panels"]}
        for layer in image.get_selected_layers():
            panel_parasite = layer.get_parasite(PANEL_PARASITE)
            if panel_parasite is not None:
                panel_ref = json.loads(bytes(panel_parasite.get_data()))
                if (panel_ref.get("project") == manifest["project"]["id"]
                        and panel_ref.get("panel") in panels):
                    return panel_ref["panel"]
            parasite = layer.get_parasite(TAKE_PARASITE)
            if parasite is None:
                continue
            reference = json.loads(bytes(parasite.get_data()))
            if reference.get("project") != manifest["project"]["id"]:
                continue
            panel_id = reference.get("panel")
            take = manifest["takes"].get(reference.get("take"))
            panel = panels.get(panel_id)
            if panel is not None and take is not None and take.get("panel") == panel_id:
                return panel_id
    except Exception:
        pass
    return None


PAGE_THUMBNAIL_SIZE = 160  # px, longest side; the page strip shows them at 96


def _write_page_thumbnail(image, destination):
    """Flattened, scaled-down PNG of ``image`` (left untouched) at ``destination``."""
    # The projection thumbnail needs no image copy: GIMP's gimp_image_duplicate can hit
    # a critical (item lookup by path) on some layer trees, which aborts the app.
    width, height = image.get_width(), image.get_height()
    scale = PAGE_THUMBNAIL_SIZE / max(width, height)
    try:
        pixbuf = image.get_thumbnail(max(1, round(width * min(scale, 1))),
                                     max(1, round(height * min(scale, 1))),
                                     Gimp.PixbufTransparency.KEEP_ALPHA)
        if pixbuf is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.stem + ".tmp.png")
            pixbuf.savev(str(temporary), "png", [], [])
            os.replace(temporary, destination)
            return
    except Exception:
        pass
    copy = image.duplicate()
    try:
        copy.flatten()
        width, height = copy.get_width(), copy.get_height()
        scale = PAGE_THUMBNAIL_SIZE / max(width, height)
        if scale < 1:
            copy.scale(max(1, round(width * scale)), max(1, round(height * scale)))
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.stem + ".tmp.png")
        Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, copy,
                       Gio.File.new_for_path(str(temporary)), None)
        os.replace(temporary, destination)
    finally:
        copy.delete()


def _embedded_page_thumbnail(source, destination):
    """The XCF's own thumbnail (written only by interactive saves) as a PNG, or False."""
    procedure = Gimp.get_pdb().lookup_procedure("gimp-file-load-thumbnail")
    if procedure is None:
        return False
    config = procedure.create_config()
    config.set_property("file", Gio.File.new_for_path(str(source)))
    result = procedure.run(config)
    if result.index(0) != Gimp.PDBStatusType.SUCCESS:
        return False
    # GIMP 3's GimpValueArray.index() already returns the plain values
    width, height, thumbnail = result.index(1), result.index(2), result.index(3)
    pixels = thumbnail.get_data() if hasattr(thumbnail, "get_data") else bytes(thumbnail)
    png = rgb_png(width, height, bytes(pixels))
    if png is None:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.stem + ".tmp.png")
    temporary.write_bytes(png)
    os.replace(temporary, destination)
    return True


def _page_thumbnail(root, manifest, page):
    """A PNG preview of a page for the page strip, or None.

    A page open in GIMP is drawn from the open image, so unsaved work shows. A closed
    page uses its XCF's embedded thumbnail, else is loaded once; both are cached by the
    file's size and modification time."""
    relative = page.get("file")
    if not relative:
        return None
    source = root / relative
    destination = (Path(Gimp.cache_directory()) / "imanganation" / "page-thumbnails"
                   / f"{manifest['project']['id']}-{page['id']}.png")
    try:
        open_image = next((image for image in Gimp.get_images()
                           if _project_page_id_for_image(image, manifest) == page["id"]),
                          None)
        if open_image is not None:
            _write_page_thumbnail(open_image, destination)
            _PAGE_THUMBNAILS.pop(page["id"], None)  # the file may change before saving
            return str(destination)

        stat = source.stat()
        key = (str(source), stat.st_size, stat.st_mtime_ns)
        cached = _PAGE_THUMBNAILS.get(page["id"])
        if cached and cached[0] == key and Path(cached[1]).is_file():
            return cached[1]
        if not _embedded_page_thumbnail(source, destination):
            image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                                   Gio.File.new_for_path(str(source)))
            try:
                _write_page_thumbnail(image, destination)
            finally:
                image.delete()
        _PAGE_THUMBNAILS[page["id"]] = (key, str(destination))
        return str(destination)
    except Exception:
        return None


def _project_page_thumbnails(root, manifest):
    return {page["id"]: preview for page in manifest["pages"]
            if (preview := _page_thumbnail(root, manifest, page))}


def _refresh_project_docks(sync_canvas=False):
    if not _DOCK_CONTEXT or build_docks is None:
        return
    root = _DOCK_CONTEXT.get("root")
    if root is None:
        _register_project_docks(_DOCK_PLUGIN)
        return
    manifest = load_project(root)
    selected_id = _DOCK_CONTEXT.get("selected_id")
    if sync_canvas:
        selected_id = _selected_canvas_panel_id(manifest) or selected_id
    contents = build_docks(
        manifest, selected_id, root, _project_page_thumbnails(root, manifest),
        DOCK_OPEN_PAGE, DOCK_GENERATE_LAYOUT, storyboard_action=DOCK_STORYBOARD,
        **_dock_actions(root, manifest))
    _DOCK_CONTEXT["selected_id"] = contents["selected_id"]
    contents["gallery"] = _gallery_content(root, manifest, contents["selected_id"])
    canvas_rows = _canvas_take_rows(manifest)
    if canvas_rows:
        contents["inspector"] += "\n" + "\n".join(canvas_rows)
    bubble_rows = _canvas_bubble_rows()
    if bubble_rows:  # a bubble selected on the canvas comes first
        contents["inspector"] = "\n".join(bubble_rows) + "\n" + contents["inspector"]
    orphan_id = _DOCK_CONTEXT.get("orphan_id")
    candidate_id = _DOCK_CONTEXT.get("candidate_id")
    if orphan_id or candidate_id:
        by_id = {panel["id"]: panel for panel in manifest["panels"]}
        contents["script"] += "\n# Match selection"
        old = by_id.get(orphan_id)
        new = by_id.get(candidate_id)
        if old is not None:
            contents["script"] += (f"\nOrphan\t{panel_label(old)} · "
                                   f"{len(old.get('takes', []))} retained takes · "
                                   f"active {old.get('active_take') or 'None'}")
        if new is not None:
            contents["script"] += f"\nCandidate\t{panel_label(new)}"
    if selected_id in {panel["id"] for panel in manifest["panels"]}:
        references = _engine_reference_rows(root, manifest, selected_id)
        if references:
            contents["panel"] += "\n" + "\n".join(references)
            contents["inspector"] += "\n" + "\n".join(references)
    elif selected_id in {character_row_id(c["name"]) for c in manifest["cast"]}:
        character = next(c for c in manifest["cast"]
                         if character_row_id(c["name"]) == selected_id)
        engine_rows = "\n".join(_engine_character_rows(root, character["name"])
                                + _design_rows("character", character["name"], cancel=False))
        contents["characters"] += "\n" + engine_rows
        engine_rows = "\n".join(_engine_character_rows(
            root, character["name"], open_buttons=True)
            + _design_rows("character", character["name"]))
        if "\nReference image\tAvailable" in engine_rows:
            engine_rows += (f"\n!{DOCK_OPEN_CHARACTER_IMAGE}\tOpen reference image"
                            "\nPaint over\tOpen it, edit, then Imanganation > Set "
                            "Character Reference from Layer…")
        contents["inspector"] += "\n" + engine_rows
    elif selected_id in {prop_row_id(p["name"]) for p in manifest.get("props", [])}:
        prop = next(p for p in manifest["props"] if prop_row_id(p["name"]) == selected_id)
        contents["inspector"] += "\n" + "\n".join(_engine_prop_rows(root, manifest, prop))
    elif selected_id in {location_row_id(loc["name"]) for loc in manifest.get("locations", [])}:
        location = next(loc for loc in manifest.get("locations", [])
                        if location_row_id(loc["name"]) == selected_id)
        contents["inspector"] += "\n" + "\n".join(
            _engine_location_rows(root, manifest, location["name"]))

    for identifier, content_key, selection_key in (
            (DOCK_PROJECT, "project", "project_selected"),
            (DOCK_INSPECTOR, "inspector", None),
            (DOCK_FILMSTRIP, "filmstrip", "filmstrip_selected"),
            (DOCK_SCRIPT, "script", "script_selected"),
            (DOCK_CHARACTERS, "characters", "character_selected"),
            (DOCK_PANEL, "panel", None),  # properties rows have no selection
            (DOCK_GALLERY, "gallery", None)):
        values = {"identifier": identifier, "content": contents[content_key],
                  "selected-item": contents[selection_key] if selection_key else ""}
        _dock_pdb_call("gimp-extension-panel-update", values)


def _notify_project_docks(root):
    if not _DOCK_CONTEXT:
        # A command's own process (Render, Refine, Inpaint, ... from the menu): the docks
        # live in the workspace extension, so ask it to refresh through its dock action.
        try:
            procedure = Gimp.get_pdb().lookup_procedure(DOCK_ACTIONS[DOCK_CHARACTERS])
            if procedure is not None:
                procedure.run(procedure.create_config())
        except Exception:
            pass  # the workspace is optional; a stale dock refreshes on the next click
        return
    active_root = _DOCK_CONTEXT.get("root")
    if active_root and Path(root).resolve() == Path(active_root).resolve():
        try:
            _refresh_project_docks()
        except Exception as exc:
            Gimp.message(f"Could not refresh project docks: {exc}")


def _match_selected_panel():
    root = _DOCK_CONTEXT["root"]
    manifest = load_project(root)
    orphan_id = _DOCK_CONTEXT.get("orphan_id")
    candidate_id = _DOCK_CONTEXT.get("candidate_id")
    by_id = {panel["id"]: panel for panel in manifest["panels"]}
    orphan = by_id.get(orphan_id)
    candidate = by_id.get(candidate_id)
    if orphan is None or orphan.get("status") != "orphaned":
        raise ValueError("Select an orphaned panel in Script first")
    if candidate is None or candidate.get("status") != "unplaced":
        raise ValueError("Select a current script panel as the match candidate")
    if orphan_id == candidate_id:
        raise ValueError("The orphan and candidate must be different panels")
    if candidate.get("takes") or candidate.get("active_take") or candidate.get("placement"):
        raise ValueError("The candidate already has production work; choose an untouched panel")

    candidate_position = next(i for i, panel in enumerate(manifest["panels"])
                              if panel["id"] == candidate_id)
    before_candidate = sum(
        1 for panel in manifest["panels"][:candidate_position]
        if panel["id"] != orphan_id)

    # The newly parsed panel supplies the updated script text. Existing project-side
    # annotations and production references remain attached to the original identity.
    merged = dict(orphan)
    merged.update(candidate)
    for key in ("location", "camera", "characters", "props", "expressions", "aspect_ratio",
                "size", "seed", "notes"):
        if orphan.get(key):
            merged[key] = orphan[key]
    merged["id"] = orphan_id
    merged["takes"] = list(orphan.get("takes", []))
    merged["active_take"] = orphan.get("active_take")
    merged["placement"] = orphan.get("placement")
    merged["status"] = "placed" if merged["placement"] else "unplaced"

    remaining = [panel for panel in manifest["panels"]
                 if panel["id"] not in {orphan_id, candidate_id}]
    remaining.insert(before_candidate, merged)
    manifest["panels"] = remaining
    if manifest.get("cursor", {}).get("next_panel") == candidate_id:
        manifest["cursor"]["next_panel"] = orphan_id
    manifest["project"]["modified"] = datetime.now().astimezone().isoformat(
        timespec="seconds")
    save_project(root, manifest)
    _DOCK_CONTEXT["selected_id"] = orphan_id
    _DOCK_CONTEXT["orphan_id"] = None
    _DOCK_CONTEXT["candidate_id"] = None
    Gimp.message(f"Matched retained panel {orphan_id} to the selected script entry; "
                 f"kept {len(merged['takes'])} take(s) and its placement.")


def _set_panel_frame_from_selection():
    root = _DOCK_CONTEXT["root"]
    manifest = load_project(root)
    panel_id = _DOCK_CONTEXT.get("selected_id")
    panel = next((item for item in manifest["panels"] if item["id"] == panel_id), None)
    if panel is None or panel.get("status") == "orphaned":
        raise ValueError("Select a current script panel before setting its frame")

    image = Gimp.context_get_image()
    if image is None:
        raise ValueError("Open the target page and select its frame area first")
    parasite = image.get_parasite(PROJECT_PARASITE)
    if parasite is None:
        raise ValueError("Open the page from Project or Page Filmstrip to link it to this project")
    page_ref = json.loads(bytes(parasite.get_data()))
    if page_ref.get("project") != manifest["project"]["id"]:
        raise ValueError("The active image belongs to a different project")
    page_id = page_ref.get("page")
    if not any(page["id"] == page_id for page in manifest["pages"]):
        raise ValueError("The active image references a page that is missing from this project")

    _, non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
    if not non_empty or x2 <= x1 or y2 <= y1:
        raise ValueError("Select a panel-shaped region first")
    panel_ref = {"project": manifest["project"]["id"], "panel": panel_id}
    if _find_panel_group(image, panel_ref) is not None:
        raise ValueError("This panel already has a canvas group; editing an existing frame is not supported yet")

    label = panel.get("label", {})
    group_name = ("Cover" if label.get("page") == 0
                  else f"Panel {label.get('page', '?')}.{label.get('panel', '?')}")
    template = _find_template(image)
    group = Gimp.GroupLayer.new(image, group_name)
    image.undo_group_start()
    try:
        if template is not None:
            image.insert_layer(group, template.get_parent(),
                               image.get_item_position(template) + 1)
        else:
            image.insert_layer(group, None, 0)
        _tag_panel_group(group, panel_ref)
        group.add_mask(group.create_mask(Gimp.AddMaskType.SELECTION))

        panel["placement"] = {"page": page_id, "frame": [x1, y1, x2 - x1, y2 - y1]}
        panel["status"] = "placed"
        manifest["cursor"]["next_panel"] = panel_id
        manifest["project"]["modified"] = datetime.now().astimezone().isoformat(
            timespec="seconds")
        save_project(root, manifest)
    except Exception:
        if group.get_image() is image:
            image.remove_layer(group)
        raise
    finally:
        image.undo_group_end()

    image.set_selected_layers([group])
    _DOCK_CONTEXT["selected_id"] = panel_id
    _refresh_project_docks()
    Gimp.displays_flush()
    Gimp.message(f"Set the frame for panel {label.get('page', '?')}."
                 f"{label.get('panel', '?')} from the current selection.")
    _save_project_page(image, root, manifest)


def _show_project_page(root, manifest, page_id):
    page = next((candidate for candidate in manifest["pages"]
                 if candidate["id"] == page_id), None)
    if page is None:
        raise ValueError("Select a page or a placed panel before opening a page")
    relative = page.get("file")
    page_path = Path(root) / relative if relative else None
    if page_path is None or not page_path.is_file():
        raise ValueError(f"Page document is missing: {relative or page_id}")

    key = (manifest["project"]["id"], page_id)
    display = _PAGE_DISPLAYS.get(key)
    if display is not None and display.is_valid():
        display.present()
        image = _PAGE_IMAGES.get(key)
        if image is not None:
            return image

    image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                           Gio.File.new_for_path(str(page_path)))
    project_ref = {"project": key[0], "page": page_id}
    image.attach_parasite(Gimp.Parasite.new(
        PROJECT_PARASITE, Gimp.PARASITE_PERSISTENT,
        list(json.dumps(project_ref, separators=(",", ":")).encode())))
    display = Gimp.Display.new(image)
    _PAGE_DISPLAYS[key] = display
    _PAGE_IMAGES[key] = image
    return image


def _focus_panel_on_canvas(root, manifest, panel):
    page_id = (panel.get("placement") or {}).get("page")
    if not page_id or not panel.get("active_take"):
        return
    image = _show_project_page(root, manifest, page_id)
    if image is None:
        return
    expected = {"project": manifest["project"]["id"], "panel": panel["id"],
                "take": panel.get("active_take")}

    def find_panel_group(layers):
        for layer in layers:
            parasite = layer.get_parasite(PANEL_PARASITE)
            if parasite is not None:
                try:
                    reference = json.loads(bytes(parasite.get_data()))
                except (TypeError, ValueError, json.JSONDecodeError):
                    reference = {}
                if (reference.get("project") == expected["project"]
                        and reference.get("panel") == expected["panel"]):
                    return layer
            if layer.is_group():
                match = find_panel_group(layer.get_children())
                if match is not None:
                    return match
        return None

    def find_take(layers):
        for layer in layers:
            parasite = layer.get_parasite(TAKE_PARASITE)
            if parasite is not None:
                try:
                    reference = json.loads(bytes(parasite.get_data()))
                except (TypeError, ValueError, json.JSONDecodeError):
                    reference = {}
                if all(reference.get(key) == value for key, value in expected.items()):
                    return layer
            if layer.is_group():
                match = find_take(layer.get_children())
                if match is not None:
                    return match
        return None

    layers = image.get_layers()
    layer = find_panel_group(layers) or find_take(layers)
    if layer is not None:
        image.set_selected_layers([layer])
        Gimp.displays_flush()


def _default_page_size(root, manifest):
    for page in manifest["pages"]:
        relative = page.get("file")
        path = Path(root) / relative if relative else None
        if path is None or not path.is_file():
            continue
        try:
            image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                                   Gio.File.new_for_path(str(path)))
            try:
                return image.get_width(), image.get_height()
            finally:
                image.delete()
        except Exception:
            continue
    setup = manifest.get("project", {}).get("page_setup", {})
    if (isinstance(setup.get("width"), int) and isinstance(setup.get("height"), int)
            and 1 <= setup["width"] <= 20000 and 1 <= setup["height"] <= 20000):
        return setup["width"], setup["height"]
    return 1600, 2400


def _create_project_page(root, manifest, width, height, number=None, resolution=None,
                         cover=False):
    """A new page document; ``number`` (a script page) fixes its label and file name.
    ``cover`` makes the page labelled Cover (which offers cover layouts)."""
    if width < 1 or height < 1:
        raise ValueError("Page width and height must be positive")
    if width > 20000 or height > 20000:
        raise ValueError("Page width and height cannot exceed 20000 pixels")

    used_numbers = []
    for page in manifest["pages"]:
        match = re.fullmatch(r"Page\s+(\d+)", page.get("label", ""), re.IGNORECASE)
        if match:
            used_numbers.append(int(match.group(1)))
    if number is None:
        number = max(used_numbers, default=0) + 1
    folder = Path(root) / "pages"
    folder.mkdir(parents=True, exist_ok=True)
    relative = f"pages/page-{number:03d}.xcf"
    while (Path(root) / relative).exists():
        number += 1
        relative = f"pages/page-{number:03d}.xcf"
    page_id = new_id("pg_")
    page_label = f"Page {number}"
    if cover:
        relative, page_label = "pages/cover.xcf", "Cover"
        spare = 1
        while (Path(root) / relative).exists():  # a deleted cover's file may linger
            spare += 1
            relative = f"pages/cover-{spare}.xcf"
    destination = Path(root) / relative
    temporary = destination.with_name(f".{destination.stem}.{secrets.token_hex(4)}.tmp.xcf")
    if resolution is None:
        resolution = manifest.get("project", {}).get("page_setup", {}).get("resolution", 72)

    image = Gimp.Image.new(width, height, Gimp.ImageBaseType.RGB)
    try:
        image.set_resolution(resolution, resolution)
        # (GIMP 3 images have no set_name; the saved file name titles the page)
        background = Gimp.Layer.new(
            image, "Background", width, height, Gimp.ImageType.RGB_IMAGE, 100,
            Gimp.LayerMode.NORMAL)
        image.insert_layer(background, None, 0)
        background.fill(Gimp.FillType.WHITE)
        project_ref = {"project": manifest["project"]["id"], "page": page_id}
        image.attach_parasite(Gimp.Parasite.new(
            PROJECT_PARASITE, Gimp.PARASITE_PERSISTENT,
            list(json.dumps(project_ref, separators=(",", ":")).encode())))
        Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image,
                       Gio.File.new_for_path(str(temporary)), None)
        os.replace(temporary, destination)

        manifest["pages"].append({"id": page_id, "label": page_label, "file": relative})
        manifest["project"]["modified"] = datetime.now().astimezone().isoformat(
            timespec="seconds")
        save_project(root, manifest)
    except Exception:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise
    finally:
        image.delete()
    return page_id


def _project_image(manifest):
    """The active image if it is a page of this project, else any open project page."""
    images = [Gimp.context_get_image(), *Gimp.get_images()]
    return next((image for image in images if image is not None
                 and _project_page_id_for_image(image, manifest)), None)


def _generate_selected_panel():
    """Context > Generate: render the selected panel through the render command.

    A panel selected on the canvas wins over the dock selection. A placed panel renders
    into its saved frame on its page; an unplaced one uses the selection on an open page
    of the project (and is placed there, as with Render Panel into Frame)."""
    root = _DOCK_CONTEXT.get("root")
    if root is None:
        raise ValueError("Open a project first")
    manifest = load_project(root)
    selected = _selected_canvas_panel_id(manifest) or _DOCK_CONTEXT.get("selected_id")
    ids = [panel["id"] for panel in manifest["panels"]]
    if selected not in ids:
        raise ValueError("Select a panel (in Project or Script, or on the canvas) to "
                         "generate it")
    _DOCK_CONTEXT["selected_id"] = selected
    _generate_panel(root, manifest, selected)


def _generate_panel(root, manifest, panel_id):
    """Render one panel; -> whether it finished (False: the artist cancelled)."""
    ids = [panel["id"] for panel in manifest["panels"]]
    panel = manifest["panels"][ids.index(panel_id)]
    page_id = (panel.get("placement") or {}).get("page")
    image = (_show_project_page(root, manifest, page_id) if page_id
             else _project_image(manifest))
    if image is None:
        raise ValueError("Open a project page and select this panel's frame, then "
                         "Generate")

    procedure = Gimp.get_pdb().lookup_procedure(PROC_RENDER)
    config = procedure.create_config()
    config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    config.set_property("image", image)
    drawables = image.get_selected_drawables() or image.get_layers()[:1]
    config.set_core_object_array("drawables", drawables)
    config.set_property("project-dir", Gio.File.new_for_path(str(root)))
    config.set_property("panel-number", ids.index(panel_id) + 1)
    result = procedure.run(config)
    status = result.index(0)
    error = Gimp.get_pdb().get_last_error()  # read before other PDB calls replace it
    _refresh_project_docks()
    if status == Gimp.PDBStatusType.CANCEL:
        return False
    if status != Gimp.PDBStatusType.SUCCESS:
        raise RuntimeError(error or "Generate failed")
    return True


def _page_waiting_panels(manifest, page_id):
    """Script panels with a frame on this page and no render yet, in reading order."""
    return [p for p in manifest["panels"]
            if p.get("status") != "orphaned" and not p.get("takes")
            and (p.get("placement") or {}).get("page") == page_id]


def _generate_page_panels():
    """Context (a page, or a panel on one) > Generate all: render every panel with a
    frame on the page and no render yet, one after another. Stops at the first
    cancel or failure, saying how far it got; finished panels stay."""
    root = _DOCK_CONTEXT.get("root")
    if root is None:
        raise ValueError("Open a project first")
    manifest = load_project(root)
    selected = _DOCK_CONTEXT.get("selected_id")
    panel = next((p for p in manifest["panels"] if p["id"] == selected), None)
    page_id = (panel.get("placement") or {}).get("page") if panel else selected
    if not any(page["id"] == page_id for page in manifest["pages"]):
        raise ValueError("Select a page, or a panel placed on a page")
    waiting = [p["id"] for p in _page_waiting_panels(manifest, page_id)]
    if not waiting:
        raise ValueError("Every panel with a frame on this page already has a render")
    seconds = 20 * len(waiting)
    if not _confirm(f"Generate {len(waiting)} panel{'s' if len(waiting) != 1 else ''}?",
                    "Renders each panel that has a frame on this page and no render "
                    f"yet, one after another (about {seconds // 60 or 1} min in all). "
                    "Cancel any render to stop; finished panels are kept.", "Generate"):
        return
    done = 0
    try:
        for panel_id in waiting:
            if not _generate_panel(root, load_project(root), panel_id):
                break
            done += 1
    finally:
        Gimp.message(f"Generated {done} of {len(waiting)} panels"
                     + ("." if done == len(waiting) else
                        "; the rest still have no render."))


def _story_text(buffer):
    start, end = buffer.get_bounds()
    return buffer.get_text(start, end, True)


def _save_story_revision(root, manifest, reason="Storyboard edit"):
    """Write an immutable canonical working-script revision and update the manifest."""
    text = serialize_script_text(manifest)
    parsed = parse_script_text(text)
    if parsed.get("problems"):
        raise ProjectFileError("The edited story cannot be saved: " +
                               "; ".join(p["message"] for p in parsed["problems"][:3]))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    script = manifest.setdefault("script", {})
    previous = script.get("file")
    if script.get("sha256") == digest and previous:
        return
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    if previous:
        _archive_active_story(root, manifest, reason)
    relative = f"script/history/story-{digest[:16]}.md"
    destination = Path(root) / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temp = destination.with_suffix(".md.tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(destination)
    for panel in manifest["panels"]:
        if panel.get("status") != "orphaned":
            panel.pop("manual", None)
            panel["source"] = script_fingerprint(panel)
    script.update({"file": relative, "format": "canonical", "sha256": digest,
                   "parsed_at": now, "parser": {"kind": "plug-in"}})


def _edit_story_beat(root, page_number, panel=None, after_panel_id=None):
    """Edit or add a canonical story beat; return its stable panel id on save."""
    manifest = load_project(root)
    seed = panel or next((p for p in manifest["panels"]
                          if p.get("id") == after_panel_id), {})
    dialog = Gtk.Dialog(title="Edit story beat" if panel else "Add story beat",
                        flags=Gtk.DialogFlags.MODAL)
    dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
    dialog.add_button("Save beat" if panel else "Add beat", Gtk.ResponseType.OK)
    dialog.set_default_size(520, 650)
    area = dialog.get_content_area()
    area.set_margin_top(10); area.set_margin_bottom(10)
    area.set_margin_start(12); area.set_margin_end(12)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    area.pack_start(scroller, True, True, 0)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
    scroller.add(box)
    fields = {}
    def entry(label, value=""):
        box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
        widget = Gtk.Entry(); widget.set_text(value or "")
        box.pack_start(widget, False, False, 0); fields[label] = widget
    def entry_choices(label, value, choices):
        box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        widget = Gtk.Entry(); widget.set_text(value or "")
        combo = Gtk.ComboBoxText()
        for choice in choices: combo.append_text(choice)
        combo.set_active(-1)
        def append_choice(active_combo):
            chosen = active_combo.get_active_text()
            if chosen:
                present = widget.get_text().strip()
                widget.set_text((present + ", " if present else "") + chosen)
                active_combo.set_active(-1)
        combo.connect("changed", append_choice)
        row.pack_start(widget, True, True, 0)
        row.pack_start(combo, False, False, 0)
        box.pack_start(row, False, False, 0); fields[label] = widget
    def entry_suggestions(label, value, choices):
        box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
        combo = Gtk.ComboBoxText.new_with_entry()
        for choice in choices: combo.append_text(choice)
        child = combo.get_child(); child.set_text(value or "")
        box.pack_start(combo, False, False, 0); fields[label] = child
    def select_text(label, value, choices, *, editable=False):
        box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
        if editable:
            combo = Gtk.ComboBoxText.new_with_entry()
            combo.append_text("Unspecified")
            for choice in choices: combo.append_text(choice)
            child = combo.get_child(); child.set_text(value or "Unspecified")
            fields[label] = child
        else:
            combo = Gtk.ComboBoxText()
            combo.append_text("Unspecified")
            for choice in choices: combo.append_text(choice)
            try:
                combo.set_active(choices.index(value) + 1)
            except ValueError:
                combo.set_active(0)
            fields[label] = combo
        box.pack_start(combo, False, False, 0)
    def text_area(label, value="", height=3):
        box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
        widget = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        widget.get_buffer().set_text(value or "")
        scroller = Gtk.ScrolledWindow(); scroller.set_min_content_height(height * 24)
        scroller.add(widget); box.pack_start(scroller, False, False, 0)
        fields[label] = widget.get_buffer()
    characters = ", ".join(c.get("name", "") for c in seed.get("characters", [])) if panel else ""
    entry("Scene heading", seed.get("scene_heading", ""))
    entry_suggestions("Location", seed.get("location", ""),
                      [l.get("name", "") for l in manifest.get("locations", [])])
    entry_choices("Characters (comma separated)", characters,
                  [c.get("name", "") for c in manifest.get("cast", [])])
    entry_choices("Props (comma separated)", ", ".join(seed.get("props", [])) if panel else "",
                  [p.get("name", "") for p in manifest.get("props", [])])
    select_text("Shot", seed.get("camera", "") if panel else "", SHOT_CHOICES,
                editable=True)
    text_area("Action", seed.get("action", "") if panel else "", 4)
    dialogue = "\n".join(f"{d.get('speaker','')}"
                          + (f" ({d.get('kind')})" if d.get("kind") != "speech" else "")
                          + f": {d.get('text','')}"
                          for d in (seed.get("dialogue", []) if panel else []))
    text_area("Dialogue (one SPEAKER: line per row)", dialogue)
    text_area("Sound effects (one per row)", "\n".join(seed.get("sfx", [])) if panel else "")
    text_area("Notes", seed.get("notes", "") if panel else "")
    expressions = "; ".join(f"{name}: {value}" for name, value in
                             (seed.get("expressions", {}) if panel else {}).items())
    entry("Expressions (Name: expression; …)", expressions)
    select_text("Frame shape", seed.get("aspect_ratio", "") if panel else "",
                ASPECT_CHOICES, editable=True)
    select_text("Frame size", seed.get("size", "") if panel else "", PANEL_SIZES)
    flashback = Gtk.CheckButton(label="This beat is in a flashback")
    flashback.set_active(bool(seed.get("flashback")))
    box.pack_start(flashback, False, False, 0)
    dialog.show_all()
    try:
        if dialog.run() != Gtk.ResponseType.OK: return None
        data = {key: (("" if value.get_text() == "Unspecified" else value.get_text())
                      if isinstance(value, Gtk.Entry) else
                      ("" if value.get_active_text() == "Unspecified"
                       else value.get_active_text() or "")
                      if isinstance(value, Gtk.ComboBoxText) else _story_text(value))
                for key, value in fields.items()}
    finally:
        dialog.destroy()
    names = [n.strip() for n in data["Characters (comma separated)"].split(",") if n.strip()]
    props = [n.strip() for n in data["Props (comma separated)"].split(",") if n.strip()]
    dialog_lines = []
    for line in data["Dialogue (one SPEAKER: line per row)"].splitlines():
        speaker, sep, words = line.partition(":")
        if sep and speaker.strip() and words.strip():
            name, marker, kind = speaker.strip().partition(" (")
            dialog_lines.append({"speaker": name.strip(), "kind":
                                 kind.rstrip(")").strip() if marker else "speech",
                                 "text": words.strip()})
    story = {"scene_heading": data["Scene heading"], "location": data["Location"],
             "characters": names, "props": props, "camera": data["Shot"],
             "action": data["Action"].strip(), "dialogue": dialog_lines,
             "sfx": [v.strip() for v in data["Sound effects (one per row)"].splitlines()
                     if v.strip()], "notes": data["Notes"],
             "expressions": {name.strip(): value.strip()
                             for bit in data["Expressions (Name: expression; …)"].split(";")
                             if (name := bit.partition(":")[0].strip())
                             and (value := bit.partition(":")[2].strip())},
             "aspect_ratio": data["Frame shape"],
             "size": data["Frame size"],
             "flashback": flashback.get_active()}
    updated = load_project(root)
    if panel:
        result = edit_panel_story(updated, panel["id"], **story)
        if page_number > 0:
            # The storyboard review is opened from a numbered project page, so
            # that page is authoritative if an older manifest has stale cover
            # metadata or a zero-based script label on this ordinary beat.
            result["label"]["page"] = page_number
            result.pop("cover", None)
    else:
        result = add_panel(updated, page_number, **story, after_panel_id=after_panel_id)
    _save_story_revision(root, updated, "Edit beat" if panel else "Add beat")
    save_project(root, updated)
    _refresh_project_docks()
    return result["id"]


def _archive_active_story(root, manifest, reason):
    script = manifest.setdefault("script", {})
    current = Path(root) / script.get("file", "")
    if not current.is_file(): return
    text = current.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    relative = f"script/history/story-{digest[:16]}.md"
    destination = Path(root) / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists(): destination.write_text(text, encoding="utf-8")
    history = script.setdefault("history", [])
    if not any(item.get("file") == relative for item in history):
        history.append({"file": relative, "sha256": digest,
                        "format": script.get("format", "canonical"),
                        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "reason": reason})


def _reload_story_revision(root, revision, *, original=False):
    manifest = load_project(root)
    script = manifest.get("script") or {}
    relative = script.get("original_file") if original else revision.get("file")
    if not relative:
        Gimp.message("This project has no imported original script to reload.")
        return False
    path = Path(root) / relative
    if not path.is_file():
        Gimp.message(f"Script revision is missing: {relative}")
        return False
    text = path.read_text(encoding="utf-8")
    if original and not _confirm("Reload imported original?",
        "This replaces the current story beats with the imported script. The current "
        "working script remains in history. Panels with artwork or page placements "
        "that no longer match will be kept under Needs matching.", "Reload original"):
        return False
    parsed, fmt = _parse_script(text, manifest["project"].get("title", "Project"))
    if parsed.get("problems") and not _confirm_script_problems(parsed["problems"]):
        return False
    updated = copy.deepcopy(manifest)
    target_format = (script.get("original_format", fmt) if original
                     else revision.get("format", fmt))
    _archive_active_story(root, updated, "Before reload original" if original
                          else "Before history restore")
    summary = reparse_script(updated, parsed, script_file=relative, script_text=text,
                             script_format=target_format)
    updated["script"].update({"file": relative, "format": target_format,
                              "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                              "parsed_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    save_project(root, updated)
    _refresh_project_docks()
    Gimp.message(f"Story reloaded: {len(summary['kept'])} unchanged, "
                 f"{len(summary['added'])} new, {len(summary['orphaned'])} needing matches.")
    return True


def _storyboard_review(root, page_id):
    """Review and edit the current page's story beats."""
    manifest = load_project(root)
    page = next((item for item in manifest["pages"] if item["id"] == page_id), None)
    if page is None:
        raise ValueError("Select a project page first")
    page_number = script_page_number(manifest, page_id)
    panels = [panel for panel in manifest["panels"]
              if panel.get("status") != "orphaned"
              and (panel.get("label") or {}).get("page") == page_number]

    title = page.get("label") or "Page"
    dialog = Gtk.Dialog(title=f"Storyboard Review · {title}", flags=Gtk.DialogFlags.MODAL)
    dialog.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CANCEL)
    dialog.add_button("Open page", Gtk.ResponseType.OK)
    dialog.add_button("Open selected panel", 1)
    dialog.add_button("Add beat…", 2)
    dialog.add_button("Edit beat…", 3)
    dialog.add_button("Script history…", 4)
    if (manifest.get("script") or {}).get("original_file"):
        dialog.add_button("Reload original…", 5)
    dialog.set_default_size(980, 680)
    content = dialog.get_content_area()
    content.set_spacing(10)
    content.set_margin_top(12)
    content.set_margin_bottom(12)
    content.set_margin_start(12)
    content.set_margin_end(12)

    reading_order = ("right-to-left" if manifest["project"].get("reading_order") == "rtl"
                     else "left-to-right")
    intro = Gtk.Label(label=(f"{title} · {len(panels)} script panel"
                             f"{'s' if len(panels) != 1 else ''} · "
                             f"{reading_order} reading order"))
    intro.set_xalign(0)
    content.pack_start(intro, False, False, 0)

    columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
    content.pack_start(columns, True, True, 0)

    page_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    page_column.set_size_request(300, -1)
    page_heading = Gtk.Label(label="Current page")
    page_heading.set_xalign(0)
    page_column.pack_start(page_heading, False, False, 0)
    preview_frame = Gtk.Frame()
    preview_frame.set_shadow_type(Gtk.ShadowType.IN)
    page_file = _page_thumbnail(root, manifest, page)
    if page_file:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(page_file, 360, 520, True)
            preview = Gtk.Image.new_from_pixbuf(pixbuf)
            preview_frame.add(preview)
        except Exception:
            page_file = None
    if not page_file:
        empty = Gtk.Label(label=("Page preview unavailable\n"
                                 "Open the page in GIMP to see its latest artwork."))
        empty.set_line_wrap(True)
        empty.set_justify(Gtk.Justification.CENTER)
        empty.set_margin_top(28)
        empty.set_margin_bottom(28)
        empty.set_margin_start(18)
        empty.set_margin_end(18)
        preview_frame.add(empty)
    page_column.pack_start(preview_frame, True, True, 0)
    page_note = Gtk.Label(label="Artwork preview · script beats at right")
    page_note.set_xalign(0)
    page_note.set_line_wrap(True)
    page_column.pack_start(page_note, False, False, 0)
    columns.pack_start(page_column, False, False, 0)

    beats_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    beats_heading = Gtk.Label(label="Story beats · script order")
    beats_heading.set_xalign(0)
    beats_column.pack_start(beats_heading, False, False, 0)
    beat_list = Gtk.ListBox()
    beat_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
    drag_target = Gtk.TargetEntry.new("application/x-imanganation-panel",
                                      Gtk.TargetFlags.SAME_APP, 0)
    drag_targets = [drag_target]
    Gtk.Widget.drag_dest_set(beat_list, Gtk.DestDefaults.ALL, drag_targets,
                             Gdk.DragAction.MOVE)

    def drag_data_get(source_row, _context, selection, _info, _time):
        selection.set(selection.get_target(), 8, source_row.get_name().encode("utf-8"))

    def drag_data_received(_list, context, _x, y, selection, _info, timestamp):
        raw = selection.get_data()
        try:
            dragged_id = raw.decode("utf-8") if raw else ""
            source_row = next((r for r in beat_list.get_children()
                               if r.get_name() == dragged_id), None)
            target_row = beat_list.get_row_at_y(y)
            if source_row is None or target_row is None or source_row == target_row:
                Gtk.drag_finish(context, False, False, timestamp)
                return
            target_index = target_row.get_index()
            allocation = target_row.get_allocation()
            if y > allocation.y + allocation.height // 2:
                target_index += 1
            source_index = source_row.get_index()
            beat_list.remove(source_row)
            if source_index < target_index:
                target_index -= 1
            beat_list.insert(source_row, target_index)
            source_row.show_all()
            beat_list.select_row(source_row)
            for index, row in enumerate(beat_list.get_children(), 1):
                heading = getattr(row, "_story_heading", None)
                if heading is not None:
                    detail = heading.get_text().split(" · ", 1)
                    heading.set_text(f"Panel {index} · " + detail[1]
                                     if len(detail) > 1 else f"Panel {index}")
            Gtk.drag_finish(context, True, False, timestamp)
        except Exception:
            Gtk.drag_finish(context, False, False, timestamp)

    beat_list.connect("drag-data-received", drag_data_received)
    beat_scroll = Gtk.ScrolledWindow()
    beat_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    beat_scroll.set_min_content_width(520)
    beat_scroll.add(beat_list)
    beats_column.pack_start(beat_scroll, True, True, 0)
    columns.pack_start(beats_column, True, True, 0)

    def add_text(parent, value, *, bold=False, dim=False):
        label = Gtk.Label()
        label.set_markup(GLib.markup_escape_text(value) if not bold else
                         f"<b>{GLib.markup_escape_text(value)}</b>")
        label.set_xalign(0)
        label.set_line_wrap(True)
        label.set_selectable(True)
        if dim:
            label.set_opacity(0.72)
        parent.pack_start(label, False, False, 0)
        return label

    for panel in panels:
        placement = panel.get("placement") or {}
        if panel.get("takes"):
            art_state = f"Art: {len(panel['takes'])} take{'s' if len(panel['takes']) != 1 else ''}"
        else:
            art_state = "Art: not rendered"
        if placement.get("page") == page_id:
            frame_state = "Frame: on this page"
        elif placement.get("page"):
            frame_state = "Frame: on another page"
        else:
            frame_state = "Frame: not placed"
        number = (panel.get("label") or {}).get("panel", "—")
        row = Gtk.ListBoxRow()
        row.set_name(panel["id"])
        Gtk.Widget.drag_source_set(row, Gdk.ModifierType.BUTTON1_MASK, drag_targets,
                                   Gdk.DragAction.MOVE)
        row.connect("drag-data-get", drag_data_get)
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        card.set_border_width(7)
        handle = Gtk.Label(label="⠿")
        handle.set_tooltip_text("Drag to reorder this beat")
        card.pack_start(handle, False, False, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        heading = Gtk.Label(label=f"Panel {number} · {art_state} · {frame_state}")
        heading.set_xalign(0)
        heading.set_line_wrap(True)
        row._story_heading = heading
        body.pack_start(heading, False, False, 0)
        action = (panel.get("action") or "").strip() or "No action described."
        add_text(body, action, bold=True)
        dialogue = panel.get("dialogue") or []
        for line in dialogue:
            speaker = line.get("speaker") or "Unassigned"
            kind = line.get("kind") or "speech"
            kind_text = f" ({kind})" if kind != "speech" else ""
            add_text(body, f"{speaker}{kind_text}: {line.get('text', '')}")
        for sound in panel.get("sfx") or []:
            add_text(body, f"SFX · {sound}")
        if not dialogue and not panel.get("sfx"):
            add_text(body, "No dialogue or sound effects.", dim=True)
        card.pack_start(body, True, True, 0)
        row.add(card)
        beat_list.add(row)

    first = beat_list.get_row_at_index(0)
    if first is not None:
        beat_list.select_row(first)
    else:
        add_text(beats_column, "No script panels are assigned to this page.", dim=True)
    open_panel_button = dialog.get_widget_for_response(1)
    if open_panel_button is not None:
        open_panel_button.set_sensitive(first is not None)
    dialog.show_all()
    response = dialog.run()
    selected = beat_list.get_selected_row()
    selected_panel_id = selected.get_name() if selected is not None else None
    reordered_ids = [beat_list.get_row_at_index(i).get_name()
                     for i in range(beat_list.get_children().__len__())
                     if beat_list.get_row_at_index(i) is not None]
    dialog.destroy()

    if reordered_ids and reordered_ids != [p["id"] for p in panels]:
        changed = load_project(root)
        reorder_page_panels(changed, page_number, reordered_ids)
        _save_story_revision(root, changed, "Reorder story beats")
        save_project(root, changed)
        manifest = changed
        _refresh_project_docks()

    if response == 2:
        new_id = _edit_story_beat(root, page_number,
                                  after_panel_id=selected_panel_id)
        if new_id: _storyboard_review(root, page_id)
        return
    if response == 3 and selected_panel_id:
        current = next((p for p in manifest["panels"] if p["id"] == selected_panel_id), None)
        if current and _edit_story_beat(root, page_number, current):
            _storyboard_review(root, page_id)
        return
    if response == 4:
        current_script = manifest.get("script") or {}
        revisions = list(reversed(current_script.get("history", [])))
        if revisions:
            chooser = Gtk.Dialog(title="Script history", flags=Gtk.DialogFlags.MODAL)
            chooser.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
            chooser.add_button("Restore selected…", Gtk.ResponseType.OK)
            combo = Gtk.ComboBoxText()
            for item in revisions:
                combo.append_text(f"{item.get('created','')} · {item.get('reason','Edit')} · {item.get('file','')}")
            combo.set_active(0)
            chooser.get_content_area().pack_start(combo, True, True, 8)
            chooser.show_all()
            chosen = chooser.run() == Gtk.ResponseType.OK
            index = combo.get_active()
            chooser.destroy()
            if chosen and 0 <= index < len(revisions):
                item = revisions[index]
                if _confirm("Restore this script revision?",
                            "The current working script will be saved in history first. "
                            "Panels with art or placements that no longer match will be "
                            "kept under Needs matching.", "Restore"):
                    _reload_story_revision(root, item)
                    _storyboard_review(root, page_id)
        else:
            Gimp.message("No earlier script revisions are available yet.")
        return
    if response == 5:
        _reload_story_revision(root, None, original=True)
        _storyboard_review(root, page_id)
        return

    if response in (Gtk.ResponseType.OK, 1):
        image = _show_project_page(root, manifest, page_id)
        if selected_panel_id:
            _DOCK_CONTEXT["selected_id"] = selected_panel_id
            group = _find_panel_group(
                image, {"project": manifest["project"]["id"], "panel": selected_panel_id})
            if group is not None:
                image.set_selected_layers([group])
        _refresh_project_docks()


def _dock_action(procedure, config, data):
    try:
        if data == "open-project":
            root = _open_project() or _choose_project_folder()
            if root is None:
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            _activate_project(root)
        elif data == "project-action":
            if _DOCK_CONTEXT.get("root") is None:
                root = _choose_project_folder()
                if root is None:
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.CANCEL, GLib.Error())
                _activate_project(root)
                return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
            manifest = load_project(_DOCK_CONTEXT["root"])
            width, height = _default_page_size(_DOCK_CONTEXT["root"], manifest)
            size = _choose_page_size(width, height)
            if size is None:
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            page_id = _create_project_page(_DOCK_CONTEXT["root"], manifest, *size)
            _DOCK_CONTEXT["selected_id"] = page_id
            _refresh_project_docks()
            manifest = load_project(_DOCK_CONTEXT["root"])
            _show_project_page(_DOCK_CONTEXT["root"], manifest, page_id)
        elif data == "open-page":
            manifest = load_project(_DOCK_CONTEXT["root"])
            selected = _DOCK_CONTEXT.get("selected_id")
            panel = next((p for p in manifest["panels"] if p["id"] == selected), None)
            page_id = selected if any(page["id"] == selected for page in manifest["pages"]) \
                else (panel.get("placement") or {}).get("page") if panel else None
            _show_project_page(_DOCK_CONTEXT["root"], manifest, page_id)
        elif data == "generate-layout":
            root = _DOCK_CONTEXT["root"]
            manifest = load_project(root)
            selected = _DOCK_CONTEXT.get("selected_id")
            if not any(page["id"] == selected for page in manifest["pages"]):
                raise ValueError("Select a page in the Pages or Project dock first")
            image = _show_project_page(root, manifest, selected)
            layout_procedure = Gimp.get_pdb().lookup_procedure(PROC_PAGE_LAYOUT)
            if layout_procedure is None:
                raise ValueError("The page layout command is unavailable")
            layout_config = layout_procedure.create_config()
            layout_config.set_property("run-mode", Gimp.RunMode.INTERACTIVE)
            layout_config.set_property("image", image)
            layout_config.set_property("project-dir", Gio.File.new_for_path(str(root)))
            drawables = image.get_selected_drawables() or image.get_layers()[:1]
            layout_config.set_core_object_array("drawables", drawables)
            result = layout_procedure.run(layout_config)
            status = result.index(0)
            error = Gimp.get_pdb().get_last_error()
            if status not in (Gimp.PDBStatusType.SUCCESS, Gimp.PDBStatusType.CANCEL):
                raise RuntimeError(error or "Generate layout failed")
            _refresh_project_docks()
        elif data == "storyboard-review":
            root = _DOCK_CONTEXT.get("root")
            if root is None:
                raise ValueError("Open a project first")
            selected = _DOCK_CONTEXT.get("selected_id")
            if not any(page["id"] == selected for page in load_project(root)["pages"]):
                raise ValueError("Select a page in the Pages or Project dock first")
            _storyboard_review(root, selected)
        elif data == "match-panel":
            _match_selected_panel()
            _refresh_project_docks()
        elif data == "set-panel-frame":
            _set_panel_frame_from_selection()
        elif data == "generate":
            _generate_selected_panel()
        elif data == "generate-page":
            _generate_page_panels()
        elif data == "new-project":
            options = _choose_new_project()
            if options is None:
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            if _create_project_from_script(**options) is False:
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        elif data == "new-manual-project":
            options = _choose_manual_project()
            if options is None:
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            _create_manual_project(**options)
        elif data in ("reload-script", "load-script"):
            if not _reload_script(_DOCK_CONTEXT["root"], choose=data == "load-script"):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        elif data == "close-project":
            if not _close_project():
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        elif data == "design-character":
            _design_selected_character()
        elif data == "design-location":
            _design_selected_location()
        elif data == "open-location-image":
            _open_selected_location_image()
        elif data == "design-prop":
            _design_selected_prop()
        elif data == "rename-character":
            if not _rename_selected_character():
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        elif data == "delete-character":
            if not _delete_character(_DOCK_CONTEXT["root"], _DOCK_CONTEXT.get("selected_id")):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        elif data == "another-reference":
            if _design_another_reference() is False:
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        elif data == "delete-prop":
            if not _delete_prop(_DOCK_CONTEXT["root"], _DOCK_CONTEXT.get("selected_id")):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        elif data == "delete-location":
            if not _delete_location(_DOCK_CONTEXT["root"], _DOCK_CONTEXT.get("selected_id")):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        elif data == "open-prop-image":
            _open_selected_prop_image()
        elif data == "open-character-image":
            _open_selected_character_image()
        elif data == "setup-models":
            _show_setup_dialog()
        elif data == "render-engine":
            _show_engine_dialog()
        else:
            _refresh_project_docks()
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
    except Exception as exc:
        return _error(procedure, str(exc))


_DESIGN_JOBS = {}  # engine job id -> character name, while a design sheet renders
_JOB_INFO = {}  # engine job id -> what the docks show for a design in flight
_DESIGN_FAILURES = {}  # (kind, key) -> why the last design failed, until the next try
DOCK_CANCEL_JOB = "plug-in-imanganation-dock-cancel-job"


def _track_design(job, kind, key, jobs, name=None):
    """Follow an engine design job (a character, location or prop) so the docks can show
    its queue position and time, offer Cancel, and say why it failed."""
    jobs[job["id"]] = key
    _DESIGN_FAILURES.pop((kind, key), None)
    _JOB_INFO[job["id"]] = {"kind": kind, "key": key, "name": name or key, "jobs": jobs,
                            "queued_at": time.monotonic(), "status": job["status"],
                            "ahead": job.get("queue_position")}
    if len(_JOB_INFO) == 1:
        GLib.timeout_add_seconds(2, _exclusive(_poll_designs))


def _design_status(kind, key):
    """-> (job id, "Rendering · ▰▰▱▱▱▱▱▱▱▱ 7/28 · 34s") for a design in flight, else None."""
    for job_id, info in _JOB_INFO.items():
        if info["kind"] == kind and info["key"] == key:
            seconds = int(time.monotonic() - info["queued_at"])
            job = {"status": info["status"], "queue_position": info.get("ahead"),
                   "progress": info.get("progress")}
            if job_status_text is None:
                return job_id, f"{info['status']} · {seconds}s"
            return job_id, job_status_text(job, seconds)
    return None


def _design_rows(kind, key, cancel=True):
    """Context rows for an asset's design: progress with a Cancel button, or why the last
    try failed."""
    status = _design_status(kind, key)
    if status is not None:
        job_id, text = status
        return [f"Design\t{text}" + (f"\t!{DOCK_CANCEL_JOB}:{job_id}:Cancel design"
                                      if cancel else "")]
    if (kind, key) in _DESIGN_FAILURES:
        return [f"Design failed\t{' '.join(_DESIGN_FAILURES[(kind, key)].split())[:200]}"]
    return []


def _selected_design(manifest):
    """(kind, key) of the selected character, location or prop."""
    selected = _DOCK_CONTEXT.get("selected_id")
    for c in manifest.get("cast", []):
        if character_row_id(c["name"]) == selected:
            return "character", c["name"]
    for loc in manifest.get("locations", []):
        if location_row_id(loc["name"]) == selected:
            return "location", location_key(loc["name"])
    for p in manifest.get("props", []):
        if prop_row_id(p["name"]) == selected:
            return "prop", prop_key(p["name"])
    return None


def _poll_designs():
    """Every 2 s while a design is in flight: update each job's state, redraw the docks
    while the selected asset is the one rendering (so its time ticks), and on finishing
    refresh them (a failure is kept and shown in Context)."""
    finished = False
    for job_id, info in list(_JOB_INFO.items()):
        try:
            job = _http("GET", f"{ENGINE_URL}/jobs/{job_id}", timeout=3)
        except EngineError:
            continue  # engine restarting: try again next tick
        info["status"], info["ahead"] = job["status"], job.get("queue_position")
        info["progress"] = job.get("progress")
        if job["status"] in ("queued", "running"):
            continue
        del _JOB_INFO[job_id]
        info["jobs"].pop(job_id, None)
        finished = True
        if job["status"] == "error":
            _DESIGN_FAILURES[(info["kind"], info["key"])] = job.get("error") or "failed"
            Gimp.message(f"Designing {info['name']} failed: {job.get('error')}")
    try:
        root = _DOCK_CONTEXT.get("root")
        selected = _selected_design(load_project(root)) if root else None
        if finished or (selected and any((i["kind"], i["key"]) == selected
                                         for i in _JOB_INFO.values())):
            _refresh_project_docks()
    except Exception:
        pass
    return GLib.SOURCE_CONTINUE if _JOB_INFO else GLib.SOURCE_REMOVE


def _dock_cancel_job(procedure, config, data):
    """Context: Cancel design on a character, location or prop being designed."""
    try:
        job_id = config.get_property("item") or ""
        if job_id in _JOB_INFO:
            _http("POST", f"{ENGINE_URL}/jobs/{job_id}/cancel", {}, timeout=10)
            _JOB_INFO[job_id]["status"] = "cancelled"
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _choose_new_project():
    """New Project from Script dialog -> keyword arguments for
    _create_project_from_script, or None if cancelled."""
    dialog = Gtk.Dialog(title="New Project from Script", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Create", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    grid = Gtk.Grid(column_spacing=12, row_spacing=8, margin=12)
    script = Gtk.FileChooserButton(title="Choose a script", action=Gtk.FileChooserAction.OPEN)
    text_filter = Gtk.FileFilter()
    text_filter.set_name("Scripts (.md, .txt, .fountain)")
    for pattern in ("*.md", "*.txt", "*.fountain", "*.markdown"):
        text_filter.add_pattern(pattern)
    script.add_filter(text_filter)
    title = Gtk.Entry(activates_default=True, hexpand=True)
    folder = Gtk.FileChooserButton(title="Save the project in",
                                   action=Gtk.FileChooserAction.SELECT_FOLDER)
    projects = ENGINE.projects if ENGINE is not None else Path.home() / "Imanganation"
    if ENGINE is not None and ENGINE.mode == "bundled":
        projects.mkdir(exist_ok=True)  # the bundled engine reads projects only from here
    folder.set_current_folder(str(projects if projects.is_dir() else Path.home()))
    width = Gtk.SpinButton.new_with_range(1, 20000, 100)
    height = Gtk.SpinButton.new_with_range(1, 20000, 100)
    width.set_value(1600)
    height.set_value(2400)
    size = Gtk.Box(spacing=6)
    size.pack_start(width, False, False, 0)
    size.pack_start(Gtk.Label(label="×"), False, False, 0)
    size.pack_start(height, False, False, 0)
    design = Gtk.CheckButton(label="Design the cast now (uses the engine)", active=True)
    nsfw = Gtk.CheckButton(label="Allow adult (NSFW) content in renders", active=False)
    nsfw.set_tooltip_text("Off: 'nsfw' is added to every negative prompt so renders stay safe for work. On: the model may draw adult content when the story asks.")
    hint = Gtk.Label(label="The renders need the project inside the projects folder.",
                     xalign=0.0)
    hint.get_style_context().add_class("dim-label")

    def script_chosen(button):
        path = button.get_filename()
        if path and not title.get_text().strip():
            title.set_text(Path(path).stem.replace("_", " ").replace("-", " ").title())

    script.connect("file-set", script_chosen)
    for row, (label, widget) in enumerate((("Script", script), ("Title", title),
                                           ("Save in", folder), ("Page size", size))):
        grid.attach(Gtk.Label(label=label, xalign=0.0), 0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)
    grid.attach(hint, 1, 4, 1, 1)
    grid.attach(design, 1, 5, 1, 1)
    grid.attach(nsfw, 1, 6, 1, 1)
    dialog.get_content_area().add(grid)
    dialog.show_all()
    try:
        while dialog.run() == Gtk.ResponseType.OK:
            options = {"script_path": Path(script.get_filename() or ""),
                       "title": title.get_text().strip(),
                       "parent": Path(folder.get_filename() or Path.home()),
                       "page_size": (width.get_value_as_int(), height.get_value_as_int()),
                       "design": design.get_active(), "nsfw": nsfw.get_active()}
            problem = (None if options["script_path"].is_file() else "Choose a script file.")
            problem = problem or (None if options["title"] else "Give the project a title.")
            target = options["parent"] / f"{_slug(options['title'])}.imanga"
            problem = problem or (f"{target} already exists." if target.exists() else None)
            if problem is None:
                return options
            Gimp.message(problem)
        return None
    finally:
        dialog.destroy()


def _choose_manual_project():
    """Collect the minimum useful setup for a script-free project."""
    dialog = Gtk.Dialog(title="New Project", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Create Project", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.set_resizable(False)
    grid = Gtk.Grid(column_spacing=12, row_spacing=8, margin=12)

    title = Gtk.Entry(activates_default=True, hexpand=True)
    title.set_placeholder_text("e.g. The Last Light")
    chapter = Gtk.Entry(activates_default=True, hexpand=True)
    chapter.set_placeholder_text("Optional")
    folder = Gtk.FileChooserButton(title="Choose the parent folder",
                                   action=Gtk.FileChooserAction.SELECT_FOLDER)
    projects = ENGINE.projects if ENGINE is not None else Path.home() / "Imanganation"
    if ENGINE is not None and ENGINE.mode == "bundled":
        projects.mkdir(parents=True, exist_ok=True)
    folder.set_current_folder(str(projects if projects.is_dir() else Path.home()))

    nsfw = Gtk.CheckButton(label="Allow adult (NSFW) content in renders", active=False)
    nsfw.set_tooltip_text("Off: 'nsfw' is added to every negative prompt so renders stay safe for work. On: the model may draw adult content when the story asks.")
    reading_order = Gtk.ComboBoxText()
    reading_order.append("rtl", "Right to left (manga)")
    reading_order.append("ltr", "Left to right")
    reading_order.set_active_id("rtl")
    color_mode = Gtk.ComboBoxText()
    color_mode.append("color", "Color")
    color_mode.append("bw", "Black and white")
    color_mode.append("inherit", "Use engine default")
    color_mode.set_active_id("color")

    preset_options = {
        "oneshot": ("Manga one-shot / anthology", "a5", "rtl", "bw", "One-shot", 1),
        "manga_series": ("Serialized manga", "jis_b6", "rtl", "bw", "Chapter 1", 1),
        "color_comic": ("Full-color comic / manhua (LTR default)", "a5", "ltr",
                         "color", "Chapter 1", 1),
        "digital_comic": ("Digital page comic", "digital", "ltr", "color", "Chapter 1", 1),
        "custom": ("Custom", None, None, None, None, None),
    }
    project_preset = Gtk.ComboBoxText()
    for preset_id, values in preset_options.items():
        project_preset.append(preset_id, values[0])
    project_preset.set_active_id("oneshot")

    page_format = Gtk.ComboBoxText()
    for format_id, label in (
            ("jis_b6", "Manga tankōbon — JIS B6 · 128 × 182 mm"),
            ("shinsho", "Manga paperback — Shinsho · 106 × 173 mm"),
            ("a5", "A5 comic / anthology · 148 × 210 mm"),
            ("jis_b5", "Manga magazine / large comic — JIS B5 · 182 × 257 mm"),
            ("us_digest", "US manga digest · 5.5 × 8.5 in"),
            ("digital", "Digital portrait · 1600 × 2400 px"),
            ("custom", "Custom pixel dimensions")):
        page_format.append(format_id, label)
    page_format.set_active_id("jis_b6")

    create_page = Gtk.CheckButton(label="Create a blank first page", active=True)
    panel_count = Gtk.SpinButton.new_with_range(0, 100, 1)
    panel_count.set_value(1)
    width = Gtk.SpinButton.new_with_range(1, 20000, 100)
    height = Gtk.SpinButton.new_with_range(1, 20000, 100)
    resolution = Gtk.SpinButton.new_with_range(36, 1200, 1)
    resolution.set_value(300)
    size = Gtk.Box(spacing=6)
    size.pack_start(width, False, False, 0)
    size.pack_start(Gtk.Label(label="×"), False, False, 0)
    size.pack_start(height, False, False, 0)
    size_row = Gtk.Box(spacing=8)
    size_row.pack_start(size, False, False, 0)
    size_row.pack_start(Gtk.Label(label="px"), False, False, 0)
    resolution_row = Gtk.Box(spacing=8)
    resolution_row.pack_start(resolution, False, False, 0)
    resolution_row.pack_start(Gtk.Label(label="PPI"), False, False, 0)

    def update_page_format(_combo):
        format_id = page_format.get_active_id()
        print_sizes_mm = {
            "jis_b6": (128, 182),
            "shinsho": (106, 173),
            "a5": (148, 210),
            "jis_b5": (182, 257),
            "us_digest": (139.7, 215.9),
        }
        if format_id in print_sizes_mm:
            page_width, page_height = print_sizes_mm[format_id]
            width.set_value(round(page_width * 300 / 25.4))
            height.set_value(round(page_height * 300 / 25.4))
            resolution.set_value(300)
        elif format_id == "digital":
            width.set_value(1600)
            height.set_value(2400)
            resolution.set_value(72)
        custom = format_id == "custom"
        width.set_sensitive(custom)
        height.set_sensitive(custom)
        resolution.set_sensitive(custom)

    page_format.connect("changed", update_page_format)
    update_page_format(page_format)

    def apply_project_preset(combo):
        values = preset_options.get(combo.get_active_id())
        if values is None or combo.get_active_id() == "custom":
            return
        _label_text, suggested_format, direction, color, chapter_text, panels = values
        page_format.set_active_id(suggested_format)
        reading_order.set_active_id(direction)
        color_mode.set_active_id(color)
        chapter.set_text(chapter_text)
        panel_count.set_value(panels)

    project_preset.connect("changed", apply_project_preset)
    apply_project_preset(project_preset)

    hint = Gtk.Label(
        label="A new .imanga folder will be created inside the chosen parent. "
              "Starter panels appear in Script and Context so you can fill in the "
              "story by hand. Print presets are common manga/comic trim sizes at "
              "300 PPI; manhua specs vary. Preset values stay editable. Check printer "
              "trim and bleed requirements.",
        xalign=0.0, wrap=True, max_width_chars=66)
    hint.get_style_context().add_class("dim-label")
    rows = (("Project preset", project_preset),
            ("Project title", title), ("Parent folder", folder),
            ("Chapter", chapter), ("Reading order", reading_order),
            ("Default color", color_mode), ("Panels to start", panel_count),
            ("", create_page), ("Page format", page_format),
            ("First page size", size_row), ("Resolution", resolution_row),
            ("", nsfw))
    for row, (label, widget) in enumerate(rows):
        if label:
            grid.attach(Gtk.Label(label=label, xalign=0.0), 0, row, 1, 1)
        grid.attach(widget, 1 if label else 0, row, 1 if label else 2, 1)
    grid.attach(hint, 0, len(rows), 2, 1)
    dialog.get_content_area().add(grid)
    dialog.show_all()
    try:
        while dialog.run() == Gtk.ResponseType.OK:
            parent_path = folder.get_filename()
            options = {
                "title": title.get_text().strip(),
                "preset": project_preset.get_active_id() or "custom",
                "parent": Path(parent_path) if parent_path else None,
                "chapter": chapter.get_text().strip(),
                "reading_order": reading_order.get_active_id() or "rtl",
                "default_color_mode": color_mode.get_active_id() or "color",
                "starter_panels": panel_count.get_value_as_int(),
                "create_page": create_page.get_active(),
                "page_size": (width.get_value_as_int(), height.get_value_as_int()),
                "resolution": resolution.get_value_as_int(),
                "page_format": page_format.get_active_id() or "custom",
                "nsfw": nsfw.get_active(),
            }
            parent = options["parent"]
            problem = None if options["title"] else "Enter a project title."
            problem = problem or (None if parent is not None and parent.is_dir() else
                                 "Choose an existing parent folder.")
            problem = problem or (None if parent is not None and os.access(parent, os.W_OK) else
                                 "The chosen parent folder is not writable.")
            target = (parent / f"{_slug(options['title'])}.imanga"
                      if parent is not None and options["title"] else None)
            problem = problem or (f"{target} already exists. Choose another title or folder."
                                  if target is not None and target.exists() else None)
            if problem is None:
                return options
            Gimp.message(problem)
        return None
    finally:
        dialog.destroy()


def _create_manual_project(title, parent, chapter, reading_order,
                           default_color_mode, starter_panels, create_page, page_size,
                           resolution, page_format, preset, nsfw=False):
    """Write a schema-shaped manual project, optionally with a ready-to-draw page."""
    if new_project_document is None:
        raise RuntimeError("The plug-in install is missing project_store.py")
    root = Path(parent) / f"{_slug(title)}.imanga"
    root.mkdir(parents=False)
    try:
        manifest = new_project_document(
            title, reading_order=reading_order,
            default_color_mode=default_color_mode, chapter=chapter,
            starter_panels=starter_panels, page_size=page_size,
            resolution=resolution, page_format=page_format, preset=preset)
        if nsfw and engine_ui is not None:
            engine_ui.set_project_nsfw(manifest, True)
        save_project(root, manifest)
        first_page = (_create_project_page(root, manifest, *page_size,
                                           resolution=resolution)
                      if create_page else None)
        _activate_project(root)
        if first_page:
            manifest = load_project(root)
            _show_project_page(root, manifest, first_page)
    except Exception:
        import shutil

        shutil.rmtree(root, ignore_errors=True)
        raise


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "project"


def _parse_script(text, title):
    """-> (parsed dict, format). Page/panel scripts parse here, instantly and with the
    engine off; prose needs the engine's LLM."""
    if script_looks_canonical(text):
        try:
            return parse_script_text(text), "canonical"
        except ValueError as exc:
            raise ValueError(f"{exc}. The script format is described in "
                             "docs/script-template.md.") from exc
    try:
        result = _run_job(ENGINE_URL, "/scripts/parse", {"text": text, "title": title},
                          "Reading the script (prose, via the engine)…")
    except EngineError as exc:
        raise EngineError("This script is prose, not PAGE / Panel format, so it needs the "
                          f"engine to read it: {exc}") from exc
    return ({"cast": result["cast"], "locations": result.get("locations", []),
             "panels": result["panels"],
             "problems": result.get("problems", [])}, result["format"])


def _confirm_script_problems(problems, action="Create anyway",
                             without="create the project without them"):
    """List the script's format problems -> True to go ahead anyway."""
    dialog = Gtk.Dialog(title="Script problems", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       action, Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    dialog.set_default_size(560, 360)
    box = dialog.get_content_area()
    box.set_spacing(8)
    intro = Gtk.Label(
        label=(f"{len(problems)} lines of the script don't" if len(problems) != 1
               else "1 line of the script doesn't")
              + " fit the format (see docs/script-template.md). Fix the script and try "
              f"again, or {without}.",
        xalign=0.0, wrap=True, max_width_chars=70, margin=8)
    box.pack_start(intro, False, False, 0)
    view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                        left_margin=8, top_margin=6, wrap_mode=Gtk.WrapMode.WORD_CHAR)
    view.get_buffer().set_text("\n".join(f"Line {p['line']}: {p['message']}"
                                          for p in problems))
    scrolled = Gtk.ScrolledWindow(vexpand=True, margin=8)
    scrolled.set_shadow_type(Gtk.ShadowType.IN)
    scrolled.add(view)
    box.pack_start(scrolled, True, True, 0)
    dialog.show_all()
    try:
        return dialog.run() == Gtk.ResponseType.OK
    finally:
        dialog.destroy()


def _choose_script_file(start=None):
    """Open a script file -> its path, or None if cancelled."""
    dialog = Gtk.FileChooserDialog(title="Load Script", action=Gtk.FileChooserAction.OPEN)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       Gtk.STOCK_OPEN, Gtk.ResponseType.ACCEPT)
    dialog.set_default_response(Gtk.ResponseType.ACCEPT)
    text_filter = Gtk.FileFilter()
    text_filter.set_name("Scripts (.md, .txt, .fountain)")
    for pattern in ("*.md", "*.txt", "*.fountain", "*.markdown"):
        text_filter.add_pattern(pattern)
    dialog.add_filter(text_filter)
    if start is not None:
        dialog.set_current_folder(str(start))
    try:
        return (Path(dialog.get_filename()) if dialog.run() == Gtk.ResponseType.ACCEPT
                else None)
    finally:
        dialog.destroy()


def _adopt_fingerprints(root):
    """A project from before Reload script: record which script text each panel came
    from while the script is still the one it was parsed from, so later edits are
    matched panel by panel. Page/panel scripts only (instant, no engine)."""
    try:
        manifest = load_project(root)
        script = manifest.get("script") or {}
        path = Path(root) / script.get("file", "")
        if script.get("format") != "canonical" or not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
        if script.get("sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest():
            return
        if adopt_script_fingerprints(manifest, parse_script_text(text)):
            save_project(root, manifest)
    except Exception:  # an optimisation: never stop a project opening
        pass


def _reload_script(root, choose=False):
    """Reload script: read the project's script again (after editing it) and bring the
    panels up to it. Load script from file…: the same with another file, copied into
    the project as its script. Unchanged panels keep their takes, placement and
    Context edits; edited ones with work go to Needs matching (asked first).
    -> False if cancelled."""
    if root is None:
        raise ValueError("Open a project first (File > Open / Switch Project)")
    manifest = load_project(root)
    script = manifest.get("script") or {}
    current = Path(root) / script["file"] if script.get("file") else None
    source = current
    if choose or current is None or not current.is_file():
        source = _choose_script_file(current.parent if current else root)
        if source is None:
            return False
    text = source.read_text(encoding="utf-8")
    if source == current and script.get("sha256") == hashlib.sha256(
            text.encode("utf-8")).hexdigest():
        _adopt_fingerprints(root)
        Gimp.message(f"{current.name} hasn't changed since it was last read.")
        return True
    title = manifest["project"].get("title") or Path(root).name
    parsed, script_format = _parse_script(text, title)
    if parsed.get("problems") and not _confirm_script_problems(
            parsed["problems"], "Reload anyway", "reload without them"):
        return False
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    relative = (script["file"] if source == current
                else f"script/originals/{digest[:16]}{source.suffix or '.md'}")
    updated = copy.deepcopy(manifest)
    _archive_active_story(root, updated, "Before reload script")
    summary = reparse_script(updated, parsed, script_file=relative, script_text=text,
                             script_format=script_format)
    counts = {key: len(ids) for key, ids in summary.items()}
    if counts["orphaned"] or counts["removed"]:
        detail = (f"{counts['kept']} panels are unchanged and {counts['added']} are new "
                  "or edited.")
        if counts["orphaned"]:
            detail += (f" {counts['orphaned']} edited panel"
                       f"{'s' if counts['orphaned'] != 1 else ''} with takes or a place on "
                       "a page move to Needs matching, where Match selected joins one to "
                       "its new panel.")
        if counts["removed"]:
            detail += (f" {counts['removed']} panel{'s' if counts['removed'] != 1 else ''}"
                       " with no work yet are replaced (their Context edits are lost).")
        if not _confirm(f"Reload the script into {title}?", detail, "Reload"):
            return False
    if source != current:
        destination = Path(root) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists(): destination.write_text(text, encoding="utf-8")
        updated.setdefault("script", {}).update({"original_file": relative,
            "original_format": script_format, "original_sha256": digest})
    save_project(root, updated)
    if _DOCK_CONTEXT.get("selected_id") in set(summary["orphaned"] + summary["removed"]):
        _DOCK_CONTEXT["selected_id"] = None
    _refresh_project_docks()
    Gimp.message(f"Script reloaded: {counts['kept']} unchanged, {counts['added']} new or "
                 f"edited" + (f", {counts['orphaned']} to match" if counts["orphaned"]
                              else "") + ".")
    return True


def _create_project_from_script(script_path, title, parent, page_size, design, nsfw=False):
    """Build a project from a script: panels, cast (with the script's descriptions),
    locations and one page document per script page; open it, then queue a design
    sheet for each described character."""
    text = Path(script_path).read_text(encoding="utf-8")
    parsed, script_format = _parse_script(text, title)
    if parsed.get("problems") and not _confirm_script_problems(parsed["problems"]):
        return False
    root = Path(parent) / f"{_slug(title)}.imanga"
    root.mkdir(parents=True)
    try:
        suffix = Path(script_path).suffix or ".md"
        (root / "script").mkdir()
        (root / "script" / f"script{suffix}").write_text(text, encoding="utf-8")
        manifest = project_from_script(parsed, title=title,
                                       script_file=f"script/script{suffix}",
                                       script_text=text, script_format=script_format)
        original_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        original_relative = f"script/originals/{original_digest[:16]}{suffix}"
        original_path = root / original_relative
        original_path.parent.mkdir(parents=True, exist_ok=True)
        original_path.write_text(text, encoding="utf-8")
        manifest["script"].update({"original_file": original_relative,
            "original_format": script_format, "original_sha256": original_digest})
        if nsfw and engine_ui is not None:
            engine_ui.set_project_nsfw(manifest, True)
        save_project(root, manifest)
        for number in dict.fromkeys(p["label"]["page"] for p in manifest["panels"]):
            _create_project_page(root, manifest, *page_size, number=number or None,
                                 cover=number == 0)
    except Exception:
        import shutil

        shutil.rmtree(root, ignore_errors=True)  # never leave half a project behind
        raise
    _activate_project(root)
    manifest = load_project(root)
    if manifest["pages"]:
        _show_project_page(root, manifest, manifest["pages"][0]["id"])
    if not design:
        return
    described = [c for c in manifest["cast"] if c.get("notes")]
    try:
        for character in described:
            _queue_character_design(root, manifest, character)
    except EngineError as exc:
        Gimp.message(f"Project created. The cast was not designed: {_engine_status(exc)}. "
                     "Select a character and use Design character when the engine runs.")
        return
    undescribed = [c["name"] for c in manifest["cast"] if not c.get("notes")]
    if undescribed:
        Gimp.message("No description for " + ", ".join(undescribed) + ": write one in "
                     "their Context notes, then use Design character.")


def _style_options(manifest):
    """The project's render engine and look for a design request, or nothing."""
    return engine_ui.design_options(manifest) if engine_ui is not None and manifest else {}


def _look_options(manifest):
    """The project's look only: a location's image is read by Qwen renders, so it is
    drawn by Qwen whatever engine the panels use."""
    return engine_ui.style_options(manifest) if engine_ui is not None and manifest else {}


def _queue_character_design(root, manifest, character, redesign=False, describe=True):
    """Ask the engine to design one character from its notes; the docks refresh when
    the sheet is ready. ``describe`` False keeps their traits (a redesign in a new
    look, not a new description)."""
    body = {**_engine_project(root, manifest), "name": character["name"],
            "description": character.get("notes", "") if describe else "",
            "aliases": character.get("aliases", []), "redesign": redesign,
            **_style_options(manifest)}
    job = _http("POST", f"{ENGINE_URL}/characters", body)
    _track_design(job, "character", character["name"], _DESIGN_JOBS)


def _design_selected_character():
    """Context's Design character: design (or redesign) the selected character from
    its notes."""
    root = _DOCK_CONTEXT["root"]
    manifest = load_project(root)
    character = next((c for c in manifest["cast"]
                      if character_row_id(c["name"]) == _DOCK_CONTEXT.get("selected_id")),
                     None)
    if character is None:
        raise ValueError("Select a character first")
    if character["name"] in _DESIGN_JOBS.values():
        raise ValueError(f"{character['name']} is already being designed")
    query = urllib.parse.urlencode(_engine_project(root, manifest))
    known = _http("GET", f"{ENGINE_URL}/characters?{query}", timeout=3)
    record = next((c for c in known
                   if c.get("name", "").casefold() == character["name"].casefold()), None)
    if not character.get("notes") and (record is None or not record.get("default_version")):
        raise ValueError(f"Describe {character['name']} in their notes first")
    _queue_character_design(root, manifest, character,
                            redesign=bool(record and record.get("default_version")))
    _refresh_project_docks()


def _choose_variant_reference(name, taken, props=()):
    """Another reference dialog -> (reference name, what is different, prop name or "")
    or None. ``props``: names of props that have a picture (Qwen-Image 2.1 projects)."""
    dialog = Gtk.Dialog(title=f"Design Another Reference for {name}", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Design", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    grid = Gtk.Grid(column_spacing=12, row_spacing=8, margin=12)
    label = Gtk.Entry(activates_default=True, hexpand=True,
                      placeholder_text="e.g. summer, school-uniform")
    description = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                               left_margin=4, right_margin=4, top_margin=4, bottom_margin=4)
    scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
    scrolled.set_size_request(380, 100)
    scrolled.set_shadow_type(Gtk.ShadowType.IN)
    scrolled.add(description)
    hint = Gtk.Label(label=f"What is different: an outfit, a season, an age. {name}'s "
                           "traits and default reference stay as they are; panels choose "
                           "this one in Context -> Reference…. About a minute.",
                     xalign=0.0, wrap=True, max_width_chars=48)
    hint.get_style_context().add_class("dim-label")
    prop_box = Gtk.ComboBoxText(hexpand=True)
    prop_box.append_text("None")
    for prop in props:
        prop_box.append_text(prop)
    prop_box.set_active(0)
    rows = [("Name", label), ("Look", scrolled)]
    if props:
        rows.append(("Prop", prop_box))
    for row, (caption, widget) in enumerate(rows):
        grid.attach(Gtk.Label(label=caption, xalign=0.0, valign=Gtk.Align.START),
                    0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)
    grid.attach(hint, 1, len(rows), 1, 1)
    dialog.get_content_area().add(grid)
    dialog.show_all()
    try:
        while dialog.run() == Gtk.ResponseType.OK:
            buffer = description.get_buffer()
            text = " ".join(buffer.get_text(buffer.get_start_iter(),
                                            buffer.get_end_iter(), False).split())
            ident = re.sub(r"[^A-Za-z0-9_-]+", "-", label.get_text().strip()).strip("-")[:40]
            if not ident or not text:
                Gimp.message("Give the reference a name and describe the look.")
            elif ident.casefold() in taken:
                Gimp.message(f"{name} already has a reference called {ident}.")
            else:
                prop = prop_box.get_active_text() if prop_box.get_active() > 0 else ""
                return ident, text, prop
        return None
    finally:
        dialog.destroy()


def _design_another_reference():
    """Design another reference…: a new look for a designed character, kept beside the
    default. -> False if cancelled."""
    root = _DOCK_CONTEXT["root"]
    manifest = load_project(root)
    character = next((c for c in manifest["cast"]
                      if character_row_id(c["name"]) == _DOCK_CONTEXT.get("selected_id")),
                     None)
    if character is None:
        raise ValueError("Select a character first")
    if character["name"] in _DESIGN_JOBS.values():
        raise ValueError(f"{character['name']} is already being designed")
    query = urllib.parse.urlencode(_engine_project(root, manifest))
    known = _http("GET", f"{ENGINE_URL}/characters?{query}", timeout=3)
    record = next((c for c in known
                   if c.get("name", "").casefold() == character["name"].casefold()), None)
    if record is None or not record.get("default_version"):
        raise ValueError(f"Design {character['name']} first; another reference builds on "
                         "the first one")
    options = _style_options(manifest)
    props = []
    if options.get("engine") == "qwen_image_21":  # the only engine that sees a prop picture
        try:
            known = _http("GET", f"{ENGINE_URL}/props?"
                          f"{urllib.parse.urlencode(_engine_project(root, manifest))}",
                          timeout=3)
            props = [p["name"] for p in known if p.get("image")]
        except EngineError:
            pass
    chosen = _choose_variant_reference(character["name"],
                                       {v.casefold() for v in record["versions"]}, props)
    if chosen is None:
        return False
    ident, text, prop = chosen
    job = _http("POST", f"{ENGINE_URL}/characters",
                {**_engine_project(root, manifest), "name": character["name"],
                 "variant_id": ident, "variant_description": text,
                 **({"variant_prop": prop} if prop else {}), **options})
    _track_design(job, "character", character["name"], _DESIGN_JOBS)
    _refresh_project_docks()
    return True


def _choose_new_character():
    """New Character dialog -> (name, aliases, description, design now) or None."""
    dialog = Gtk.Dialog(title="New Character", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Create", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    grid = Gtk.Grid(column_spacing=12, row_spacing=8, margin=12)
    name = Gtk.Entry(activates_default=True, hexpand=True)
    aliases = Gtk.Entry(activates_default=True, placeholder_text="Optional, comma-separated")
    description = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                               left_margin=4, right_margin=4, top_margin=4, bottom_margin=4)
    scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
    scrolled.set_size_request(380, 120)
    scrolled.set_shadow_type(Gtk.ShadowType.IN)
    scrolled.add(description)
    hint = Gtk.Label(label="Age, build, hair, eyes, outfit, marks, demeanour. Your words "
                           "win; the engine only fills what you leave open.",
                     xalign=0.0, wrap=True, max_width_chars=48)
    hint.get_style_context().add_class("dim-label")
    design = Gtk.CheckButton(label="Design the character now (uses the engine)", active=True)
    for row, (label, widget) in enumerate((("Name", name), ("Aliases", aliases),
                                           ("Description", scrolled))):
        caption = Gtk.Label(label=label, xalign=0.0, valign=Gtk.Align.START)
        grid.attach(caption, 0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)
    grid.attach(hint, 1, 3, 1, 1)
    grid.attach(design, 1, 4, 1, 1)
    dialog.get_content_area().add(grid)
    dialog.show_all()
    try:
        while dialog.run() == Gtk.ResponseType.OK:
            buffer = description.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
            chosen = (" ".join(name.get_text().split()),
                      [a.strip() for a in aliases.get_text().split(",") if a.strip()],
                      " ".join(text.split()), design.get_active())
            if chosen[0]:
                return chosen
            Gimp.message("Give the character a name.")
        return None
    finally:
        dialog.destroy()


def _dock_character_menu(procedure, config, data):
    """Project tree right-click: New character… (Characters heading), or Design
    character / Delete character… (a character row, whose id is the item)."""
    try:
        root = _DOCK_CONTEXT["root"]
        if data == "design":
            _DOCK_CONTEXT["selected_id"] = config.get_property("item")
            _design_selected_character()
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
        if data == "variant":
            _DOCK_CONTEXT["selected_id"] = config.get_property("item")
            if not _design_another_reference():
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
        if data == "delete":
            if not _delete_character(root, config.get_property("item")):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
        chosen = _choose_new_character()
        if chosen is None:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        name, aliases, description, design = chosen
        manifest = load_project(root)
        taken = {n.casefold() for c in manifest["cast"]
                 for n in (c["name"], *c.get("aliases", []))}
        if name.casefold() in taken:
            raise ValueError(f"{name} is already in the cast")
        character = {"name": name, "aliases": aliases}
        if description:
            character["notes"] = description
        manifest["cast"].append(character)
        save_project(root, manifest)
        _DOCK_CONTEXT["selected_id"] = character_row_id(name)
        if design and description:
            try:
                _queue_character_design(root, manifest, character)
            except EngineError as exc:
                Gimp.message(f"{name} was added but not designed: {_engine_status(exc)}. "
                             "Use Design character when the engine runs.")
        _refresh_project_docks()
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def screentone(procedure, run_mode, image, drawables, config, data):
    """Fill the current selection with a configurable, editable halftone layer."""
    if tone_effects is None:
        Gimp.message("This plug-in install is missing tone and effects support.")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())
    if run_mode == Gimp.RunMode.INTERACTIVE:
        dialog = Gtk.Dialog(title="Create Screentone", flags=0)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Create", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        box.pack_start(Gtk.Label(label="Apply a manga halftone to the current selection."),
                       False, False, 0)

        def setting(label, value, low, high, step, digits=0):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
            control = Gtk.SpinButton.new_with_range(low, high, step)
            control.set_digits(digits)
            control.set_value(value)
            row.pack_end(control, False, False, 0)
            box.pack_start(row, False, False, 0)
            return control

        spacing = setting("Dot spacing (px)", 24, 4, 200, 1)
        coverage = setting("Dot coverage (%)", 35, 1, 100, 1)
        angle = setting("Screen angle (degrees)", 45, 0, 179, 1)
        dialog.show_all()
        response = dialog.run()
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        values = (spacing.get_value(), coverage.get_value(), angle.get_value())
        dialog.destroy()
    else:
        values = (24.0, 35.0, 45.0)

    saved = None
    path = None
    layer = None
    stage = "read selection"
    previous_foreground = Gimp.context_get_foreground()
    image.undo_group_start()
    try:
        _, selected, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
        if not selected:
            raise ValueError("Make a selection for the area you want to tone, then try again.")
        saved = Gimp.Selection.save(image)
        stage = "build halftone path"
        svg = tone_effects.screentone_svg(
            image.get_width(), image.get_height(), (x1, y1, x2 - x1, y2 - y1), *values)
        stage = "import halftone path"
        ok, paths = image.import_paths_from_string(svg, len(svg.encode("utf-8")), True, False)
        if not ok or not paths:
            raise RuntimeError("GIMP could not create the screentone pattern")
        path = paths[0]
        path.set_name(f"Screentone pattern · {values[1]:.0f}% · {values[2]:.0f}°")
        stage = "clip pattern to selection"
        image.select_item(Gimp.ChannelOps.REPLACE, path)
        image.select_item(Gimp.ChannelOps.INTERSECT, saved)
        stage = "create tone layer"
        layer = Gimp.Layer.new(image, f"Screentone · {values[1]:.0f}% · {values[2]:.0f}°",
                               image.get_width(), image.get_height(),
                               Gimp.ImageType.RGBA_IMAGE, 100, Gimp.LayerMode.NORMAL)
        image.insert_layer(layer, None, 0)
        layer.fill(Gimp.FillType.TRANSPARENT)
        stage = "fill tone layer"
        Gimp.context_set_foreground(Gegl.Color.new("black"))
        layer.edit_fill(Gimp.FillType.FOREGROUND)
        image.select_item(Gimp.ChannelOps.REPLACE, saved)
    except Exception as exc:
        if layer is not None and layer.get_image() is image:
            image.remove_layer(layer)
        Gimp.message(f"Could not create screentone during {stage}: {exc}")
        image.undo_group_end()
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())
    finally:
        if saved is not None:
            image.select_item(Gimp.ChannelOps.REPLACE, saved)
            image.remove_channel(saved)
        Gimp.context_set_foreground(previous_foreground)
    image.undo_group_end()
    image.set_selected_layers([layer])
    Gimp.displays_flush()
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def speed_lines(procedure, run_mode, image, drawables, config, data):
    """Create selection-clipped manga speed lines around a chosen focal point."""
    if tone_effects is None:
        Gimp.message("This plug-in install is missing tone and effects support.")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())
    _, selected, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
    if not selected:
        Gimp.message("Make a selection for the area you want to fill with speed lines.")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())
    if run_mode == Gimp.RunMode.INTERACTIVE:
        dialog = Gtk.Dialog(title="Create Speed Lines", flags=0)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Create", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        box.pack_start(Gtk.Label(
            label="Add radial linework inside the current selection."), False, False, 0)

        def setting(label, value, low, high, step, digits=0):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
            control = Gtk.SpinButton.new_with_range(low, high, step)
            control.set_digits(digits)
            control.set_value(value)
            row.pack_end(control, False, False, 0)
            box.pack_start(row, False, False, 0)
            return control

        count = setting("Line count", 96, 8, 720, 1)
        clear = setting("Clear area (%)", 18, 0, 80, 1)
        stroke = setting("Line width (px)", 5, 1, 40, 0.5, 1)
        focal_x = setting("Focal point X (%)", 50, 0, 100, 1)
        focal_y = setting("Focal point Y (%)", 50, 0, 100, 1)
        dialog.show_all()
        response = dialog.run()
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        values = (int(count.get_value()), clear.get_value(), stroke.get_value(),
                  focal_x.get_value() / 100.0, focal_y.get_value() / 100.0)
        dialog.destroy()
    else:
        values = (96, 18.0, 5.0, 0.5, 0.5)

    saved = None
    layer = None
    previous_foreground = Gimp.context_get_foreground()
    image.undo_group_start()
    try:
        saved = Gimp.Selection.save(image)
        bounds = (x1, y1, x2 - x1, y2 - y1)
        focal = (x1 + bounds[2] * values[3], y1 + bounds[3] * values[4])
        svg = tone_effects.speed_lines_svg(
            image.get_width(), image.get_height(), bounds, focal,
            values[0], values[1], values[2])
        ok, paths = image.import_paths_from_string(svg, len(svg.encode("utf-8")), True, False)
        if not ok or not paths:
            raise RuntimeError("GIMP could not create the speed-line pattern")
        path = paths[0]
        path.set_name(f"Speed lines pattern · {values[0]} rays")
        image.select_item(Gimp.ChannelOps.REPLACE, path)
        image.select_item(Gimp.ChannelOps.INTERSECT, saved)
        layer = Gimp.Layer.new(image, f"Speed lines · {values[0]} rays",
                               image.get_width(), image.get_height(),
                               Gimp.ImageType.RGBA_IMAGE, 100, Gimp.LayerMode.NORMAL)
        image.insert_layer(layer, None, 0)
        layer.fill(Gimp.FillType.TRANSPARENT)
        Gimp.context_set_foreground(Gegl.Color.new("black"))
        layer.edit_fill(Gimp.FillType.FOREGROUND)
        image.select_item(Gimp.ChannelOps.REPLACE, saved)
    except Exception as exc:
        if layer is not None and layer.get_image() is image:
            image.remove_layer(layer)
        Gimp.message(str(exc))
        image.undo_group_end()
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())
    finally:
        if saved is not None:
            image.select_item(Gimp.ChannelOps.REPLACE, saved)
            image.remove_channel(saved)
        Gimp.context_set_foreground(previous_foreground)
    image.undo_group_end()
    image.set_selected_layers([layer])
    Gimp.displays_flush()
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def impact_burst(procedure, run_mode, image, drawables, config, data):
    """Add a selection-clipped radial burst on its own editable layer."""
    if tone_effects is None:
        Gimp.message("This plug-in install is missing tone and effects support.")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())
    _, selected, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
    if not selected:
        Gimp.message("Make a selection for the area you want to fill with an impact burst.")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())
    if run_mode == Gimp.RunMode.INTERACTIVE:
        dialog = Gtk.Dialog(title="Create Impact Burst", flags=0)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Create", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        box.pack_start(Gtk.Label(
            label="Add a jagged radial burst inside the current selection."), False, False, 0)

        def setting(label, value, low, high, step):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
            control = Gtk.SpinButton.new_with_range(low, high, step)
            control.set_value(value)
            row.pack_end(control, False, False, 0)
            box.pack_start(row, False, False, 0)
            return control

        spikes = setting("Spike count", 16, 6, 120, 1)
        depth = setting("Burst depth (%)", 45, 10, 90, 1)
        rotation = setting("Rotation (degrees)", 0, 0, 359, 1)
        focal_x = setting("Center X (%)", 50, 0, 100, 1)
        focal_y = setting("Center Y (%)", 50, 0, 100, 1)
        dialog.show_all()
        response = dialog.run()
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        values = (int(spikes.get_value()), depth.get_value(), rotation.get_value(),
                  focal_x.get_value() / 100.0, focal_y.get_value() / 100.0)
        dialog.destroy()
    else:
        values = (16, 45.0, 0.0, 0.5, 0.5)

    saved = None
    layer = None
    previous_foreground = Gimp.context_get_foreground()
    image.undo_group_start()
    try:
        saved = Gimp.Selection.save(image)
        bounds = (x1, y1, x2 - x1, y2 - y1)
        center = (x1 + bounds[2] * values[3], y1 + bounds[3] * values[4])
        svg = tone_effects.impact_burst_svg(
            image.get_width(), image.get_height(), bounds, center,
            values[0], values[1], values[2])
        ok, paths = image.import_paths_from_string(svg, len(svg.encode("utf-8")), True, False)
        if not ok or not paths:
            raise RuntimeError("GIMP could not create the impact burst")
        paths[0].set_name(f"Impact burst pattern · {values[0]} spikes")
        image.select_item(Gimp.ChannelOps.REPLACE, paths[0])
        image.select_item(Gimp.ChannelOps.INTERSECT, saved)
        layer = Gimp.Layer.new(image, f"Impact burst · {values[0]} spikes",
                               image.get_width(), image.get_height(),
                               Gimp.ImageType.RGBA_IMAGE, 100, Gimp.LayerMode.NORMAL)
        image.insert_layer(layer, None, 0)
        layer.fill(Gimp.FillType.TRANSPARENT)
        Gimp.context_set_foreground(Gegl.Color.new("black"))
        layer.edit_fill(Gimp.FillType.FOREGROUND)
        image.select_item(Gimp.ChannelOps.REPLACE, saved)
    except Exception as exc:
        if layer is not None and layer.get_image() is image:
            image.remove_layer(layer)
        Gimp.message(str(exc))
        image.undo_group_end()
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error())
    finally:
        if saved is not None:
            image.select_item(Gimp.ChannelOps.REPLACE, saved)
            image.remove_channel(saved)
        Gimp.context_set_foreground(previous_foreground)
    image.undo_group_end()
    image.set_selected_layers([layer])
    Gimp.displays_flush()
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _delete_character(root, row_id):
    """Delete character…: confirm, take them out of the cast and their panels, and have
    the engine set their designs aside. -> False if cancelled."""
    manifest = load_project(root)
    character = next((c for c in manifest["cast"] if character_row_id(c["name"]) == row_id),
                     None)
    if character is None:
        raise ValueError("That character is no longer in the cast")
    name = character["name"]
    if name in _DESIGN_JOBS.values():
        raise ValueError(f"{name} is being designed; delete them when the design finishes")
    query = _engine_project(root, manifest)
    try:
        known = _http("GET", f"{ENGINE_URL}/characters?{urllib.parse.urlencode(query)}",
                      timeout=3)
        record = next((c for c in known
                       if c.get("name", "").casefold() == name.casefold()), None)
        engine_down = None
    except EngineError as exc:
        record, engine_down = None, exc
    panels = sum(any(c.get("name", "").casefold() == name.casefold()
                     for c in p.get("characters", [])) for p in manifest["panels"])
    detail = f"{name} is removed from the cast"
    detail += (f" and from {panels} panel{'s' if panels != 1 else ''} (their takes and "
               "dialogue are kept)." if panels else ".")
    if record and record.get("versions"):
        count = len(record["versions"])
        detail += (f" Their design{'s' if count != 1 else ''} ({count} version"
                   f"{'s' if count != 1 else ''}) move to the engine's characters/.deleted "
                   "folder, where they can be restored by hand.")
    elif engine_down is not None:
        detail += (f" The engine isn't reachable ({_engine_status(engine_down)}), so any "
                   f"design stays in the engine and would come back if you add a "
                   f"character named {name} again.")
    if not _confirm(f"Delete {name}?", detail, "Delete"):
        return False
    delete_character(manifest, name)
    save_project(root, manifest)
    if record is not None:
        try:
            _http("DELETE", f"{ENGINE_URL}/characters?"
                            f"{urllib.parse.urlencode({**query, 'name': name})}", timeout=10)
        except EngineError as exc:
            Gimp.message(f"{name} was removed from the project, but the engine kept their "
                         f"design: {_engine_status(exc)}")
    if _DOCK_CONTEXT.get("selected_id") == row_id:
        _DOCK_CONTEXT["selected_id"] = None
    _refresh_project_docks()
    return True


def _confirm(title, text, action):
    dialog = Gtk.MessageDialog(flags=Gtk.DialogFlags.MODAL,
                               message_type=Gtk.MessageType.QUESTION, text=title)
    dialog.format_secondary_text(text)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       action, Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    try:
        return dialog.run() == Gtk.ResponseType.OK
    finally:
        dialog.destroy()


def _close_project():
    """Close Project: offer to save pages with unsaved changes, close the pages the
    workspace opened, and show the welcome workspace. The project is not reopened at
    the next start. -> False if cancelled."""
    root = _DOCK_CONTEXT.get("root")
    if root is None:
        Gimp.message("No Imanganation project is open.")
        return True
    manifest = load_project(root)
    title = manifest["project"].get("title") or Path(root).name
    pages = _open_page_images(manifest)
    labels = {page["id"]: page.get("label") or page["id"] for page in manifest["pages"]}
    dirty = {page_id: image for page_id, image in pages.items() if image.is_dirty()}
    if dirty:
        names = ", ".join(labels.get(page_id, page_id) for page_id in dirty)
        dialog = Gtk.MessageDialog(flags=Gtk.DialogFlags.MODAL,
                                   message_type=Gtk.MessageType.QUESTION,
                                   text=f"Save changes to {title} before closing?")
        dialog.format_secondary_text(f"Unsaved changes: {names}.")
        dialog.add_buttons("Close without saving", Gtk.ResponseType.REJECT,
                           Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_SAVE, Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        try:
            response = dialog.run()
        finally:
            dialog.destroy()
        if response not in (Gtk.ResponseType.ACCEPT, Gtk.ResponseType.REJECT):
            return False
        if response == Gtk.ResponseType.ACCEPT:
            failed = [labels.get(page_id, page_id) for page_id, image in dirty.items()
                      if not _save_project_page(image, root, manifest)]
            if failed:  # _save_project_page has said why
                Gimp.message(f"{title} is still open: {', '.join(failed)} could not be "
                             "saved.")
                return False
    for page_id in pages:
        _close_page_display(manifest["project"]["id"], page_id)
    _remember_project(root, closed=True)
    _DOCK_CONTEXT.update(root=None, selected_id=None, orphan_id=None, candidate_id=None)
    _register_project_docks(_DOCK_PLUGIN)
    return True


def _close_project_run(procedure, config, data):
    """File > Close Imanganation Project / Reload Imanganation Script: the workspace
    extension does the work (``data``: its dock procedure)."""
    close = Gimp.get_pdb().lookup_procedure(data)
    if close is None:
        return _error(procedure, "The Imanganation workspace is not running; restart GIMP "
                                 "to start it")
    status = close.run(close.create_config()).index(0)
    return procedure.new_return_values(status, GLib.Error())


def _close_page_display(project_id, page_id):
    """Close a page's window if the workspace opened it (unsaved changes included:
    the caller has asked)."""
    key = (project_id, page_id)
    display = _PAGE_DISPLAYS.pop(key, None)
    image = _PAGE_IMAGES.pop(key, None)
    if image is not None and image.is_valid():
        image.clean_all()
    if display is not None and display.is_valid():
        display.delete()


def _trash_page_file(root, relative):
    """Send a deleted page's document to the system trash (recoverable), or into the
    project's .trash/ folder where there is no trash."""
    if not relative:
        return
    path = Path(root) / relative
    if not path.is_file():
        return
    try:
        Gio.File.new_for_path(str(path)).trash(None)
    except GLib.Error:
        folder = Path(root) / ".trash"
        folder.mkdir(exist_ok=True)
        os.replace(path, folder / f"{path.stem}-{secrets.token_hex(3)}{path.suffix}")


def _dock_page_menu(procedure, config, data):
    """Delete page… (a page's right-click menu), Add cover page… (the Pages heading) or
    a drop in the page strip."""
    try:
        root = _DOCK_CONTEXT["root"]
        item = config.get_property("item")
        manifest = load_project(root)
        if data == "cover":
            if any(p.get("cover") or str(p.get("label", "")).casefold() == "cover"
                   for p in manifest["pages"]):
                raise ValueError("This project already has a cover page")
            width, height = _default_page_size(root, manifest)
            size = _choose_page_size(width, height)
            if size is None:
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            page_id = _create_project_page(root, manifest, *size, cover=True)
            manifest = load_project(root)  # the cover leads the reading order
            cover = manifest["pages"].pop()
            manifest["pages"].insert(0, cover)
            save_project(root, manifest)
            _DOCK_CONTEXT["selected_id"] = page_id
            _refresh_project_docks()
            _show_project_page(root, load_project(root), page_id)
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
        if data == "reorder":
            dragged, _, target = item.partition("\t")
            reorder_pages(manifest, dragged, target)
            save_project(root, manifest)
            _refresh_project_docks()
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
        page = next((p for p in manifest["pages"] if p["id"] == item), None)
        if page is None:
            raise ValueError("That page is no longer in the project")
        placed = sum((p.get("placement") or {}).get("page") == item
                     for p in manifest["panels"])
        detail = "Its document goes to the trash."
        if placed:
            detail += (f" {placed} placed panel{'s' if placed != 1 else ''} go back to "
                       "unplaced; their takes are kept.")
        if not _confirm(f"Delete {page.get('label') or 'this page'}?", detail, "Delete"):
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        index = manifest["pages"].index(page)
        relative, _ = delete_page(manifest, item)
        save_project(root, manifest)
        _close_page_display(manifest["project"]["id"], item)
        _trash_page_file(root, relative)
        if _DOCK_CONTEXT.get("selected_id") == item:
            neighbours = manifest["pages"]
            _DOCK_CONTEXT["selected_id"] = (
                neighbours[min(index, len(neighbours) - 1)]["id"] if neighbours else None)
        _refresh_project_docks()
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _choose_new_panel(page_number, locations, cast):
    """Add Panel dialog (page 0: the cover) -> (action, location, characters, camera) or
    None."""
    dialog = Gtk.Dialog(title=(f"Add Panel to Page {page_number}" if page_number
                               else "Add Cover"), flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Add", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    grid = Gtk.Grid(column_spacing=12, row_spacing=8, margin=12)
    action = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                          left_margin=4, right_margin=4, top_margin=4, bottom_margin=4)
    scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
    scrolled.set_size_request(380, 100)
    scrolled.set_shadow_type(Gtk.ShadowType.IN)
    scrolled.add(action)
    location = Gtk.ComboBoxText.new_with_entry()
    for place in locations:
        location.append_text(place)
    characters = Gtk.Entry(hexpand=True, activates_default=True,
                           placeholder_text=", ".join(cast[:3]) or "Names, comma separated")
    camera = Gtk.Entry(hexpand=True, activates_default=True,
                       placeholder_text="e.g. close-up, low angle")
    rows = (("Action", scrolled), ("Location", location), ("Characters", characters),
            ("Camera", camera))
    for row, (label, widget) in enumerate(rows):
        grid.attach(Gtk.Label(label=label, xalign=0.0, valign=Gtk.Align.START), 0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)
    dialog.get_content_area().add(grid)
    dialog.show_all()
    try:
        while dialog.run() == Gtk.ResponseType.OK:
            buffer = action.get_buffer()
            text = " ".join(buffer.get_text(buffer.get_start_iter(),
                                            buffer.get_end_iter(), False).split())
            if text:
                return (text, location.get_active_text() or "", characters.get_text(),
                        camera.get_text())
            Gimp.message("Describe what the picture shows." if not page_number
                         else "Describe what happens in the panel.")
        return None
    finally:
        dialog.destroy()


def _add_panel_target_page(manifest, item):
    """The script page number "Add panel…" applies to: the right-clicked page or panel,
    else the selected one, else the active image's page, else the last page."""
    pages = {page["id"] for page in manifest["pages"]}
    panels = {panel["id"]: panel for panel in manifest["panels"]}
    for key in (item, _DOCK_CONTEXT.get("selected_id")):
        if key in pages:
            if script_page_number(manifest, key) > 0:
                return script_page_number(manifest, key)
            continue
        panel = panels.get(key)
        if panel is not None and panel["label"]["page"] > 0:
            return panel["label"]["page"]
    image = Gimp.context_get_image()
    page_id = _project_page_id_for_image(image, manifest) if image is not None else None
    if page_id is not None and script_page_number(manifest, page_id) > 0:
        return script_page_number(manifest, page_id)
    story = [p["id"] for p in manifest["pages"] if script_page_number(manifest, p["id"]) > 0]
    if story:
        return script_page_number(manifest, story[-1])
    numbers = [p["label"]["page"] for p in manifest["panels"] if p["label"]["page"] > 0]
    return max(numbers, default=1)


def _dock_panel_menu(procedure, config, data):
    """Project tree right-click: Add panel… (Script panels heading, a page or a panel:
    it goes at the end of that page), Add cover… (the heading), Duplicate panel or Delete panel… (a panel added by hand)."""
    try:
        root = _DOCK_CONTEXT["root"]
        item = config.get_property("item") or ""
        manifest = load_project(root)
        if data == "delete":
            panel = next((p for p in manifest["panels"] if p["id"] == item), None)
            if panel is None:
                raise ValueError("That panel is no longer in the project")
            if not _confirm("Delete this panel?", panel.get("action") or "(no action)",
                            "Delete"):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            delete_panel(manifest, item)
            save_project(root, manifest)
            if _DOCK_CONTEXT.get("selected_id") == item:
                _DOCK_CONTEXT["selected_id"] = None
        elif data == "duplicate":
            panel = duplicate_panel(manifest, item)
            save_project(root, manifest)
            _DOCK_CONTEXT["selected_id"] = panel["id"]
        else:
            number = 0 if data == "cover" else _add_panel_target_page(manifest, item)
            chosen = _choose_new_panel(
                number, [loc["name"] for loc in manifest.get("locations", [])],
                [c["name"] for c in manifest.get("cast", [])])
            if chosen is None:
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            action, place, names, camera = chosen
            fields = dict(action=action, location=place, characters=[names], camera=camera)
            panel = (add_cover(manifest, **fields) if data == "cover"
                     else add_panel(manifest, number, **fields))
            save_project(root, manifest)
            _DOCK_CONTEXT["selected_id"] = panel["id"]
        _refresh_project_docks()
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _choose_character_version(name, versions, default, current):
    """Reference dialog -> a version id, "" for the character's default, or None."""
    dialog = Gtk.Dialog(title=f"Reference for {name}", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Use", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_border_width(12)
    box.add(Gtk.Label(label=f"Which reference image of {name} should this panel use?",
                      xalign=0.0))
    combo = Gtk.ComboBoxText()
    combo.append("", f"Default ({default})" if default else "Default")
    for version in versions:
        combo.append(version, version)
    combo.set_active_id(current if current in versions else "")
    box.add(combo)
    dialog.show_all()
    try:
        return combo.get_active_id() if dialog.run() == Gtk.ResponseType.OK else None
    finally:
        dialog.destroy()


def _dock_character_version(procedure, config, data):
    """Context: a panel character's Reference… -> pick which of their reference images
    (versions the engine holds) this panel uses. Item "<panel id>:<index>"."""
    try:
        root = _DOCK_CONTEXT["root"]
        panel_id, _, index = (config.get_property("item") or "").rpartition(":")
        manifest = load_project(root)
        panel = next((p for p in manifest["panels"] if p["id"] == panel_id), None)
        if panel is None or not index.isdigit() or int(index) >= len(panel["characters"]):
            raise ValueError("That character is no longer in the panel")
        entry = panel["characters"][int(index)]
        query = urllib.parse.urlencode(_engine_project(root, manifest))
        known = _http("GET", f"{ENGINE_URL}/characters?{query}", timeout=3)
        record = next((c for c in known
                       if c.get("name", "").casefold() == entry["name"].casefold()), None)
        if record is None or not record.get("versions"):
            raise ValueError(f"{entry['name']} has no reference images yet; design them "
                             "or use Set Character Reference from Layer first")
        chosen = _choose_character_version(entry["name"], record["versions"],
                                           record.get("default_version"),
                                           entry.get("version"))
        if chosen is None:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        set_character_version(manifest, panel_id, int(index), chosen)
        save_project(root, manifest)
        _refresh_project_docks()
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _show_take_on_canvas(image, project_id, panel_id, take_id):
    """Show the panel's ``take_id`` layer and hide its other whole-picture takes (staged
    patch layers are the artist's additions and stay as they are). -> whether the take's
    layer was on this page."""
    found = False

    def reference(layer):
        parasite = layer.get_parasite(TAKE_PARASITE)
        if parasite is None:
            return None
        try:
            ref = json.loads(bytes(parasite.get_data()))
        except (TypeError, ValueError):
            return None
        return ref if ref.get("project") == project_id and ref.get("panel") == panel_id \
            else None

    def walk(layers):
        nonlocal found
        for layer in layers:
            ref = reference(layer)
            if ref is not None:
                meta = layer.get_parasite(PARASITE)
                staged = False
                if meta is not None:
                    try:
                        staged = bool(json.loads(bytes(meta.get_data())).get("stage"))
                    except (TypeError, ValueError):
                        pass
                if not staged:
                    wanted = ref.get("take") == take_id
                    found = found or wanted
                    layer.set_visible(wanted)
            if layer.is_group():
                walk(layer.get_children())

    image.undo_group_start()
    try:
        walk(image.get_layers())
    finally:
        image.undo_group_end()
    Gimp.displays_flush()
    return found


def _activate_take(root, panel_id, take_id):
    """The panel's ``take_id`` becomes its active take; on its open page it is the one
    shown."""
    manifest = load_project(root)
    set_active_take(manifest, panel_id, take_id)
    save_project(root, manifest)
    panel = next(p for p in manifest["panels"] if p["id"] == panel_id)
    page_id = (panel.get("placement") or {}).get("page")
    image = _open_page_images(manifest).get(page_id)
    if image is not None:
        if _show_take_on_canvas(image, manifest["project"]["id"], panel_id, take_id):
            _save_project_page(image, root, manifest)
        else:
            Gimp.message("That take's layer isn't on the open page, so only the "
                         "project record changed.")
    _refresh_project_docks()


def _dock_activate_take(procedure, config, data):
    """Context: a take's Make active. Item "<panel id>:<take id>"."""
    try:
        panel_id, _, take_id = (config.get_property("item") or "").rpartition(":")
        _activate_take(_DOCK_CONTEXT["root"], panel_id, take_id)
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _gallery_references(root, manifest, selected_id):
    """What the engine holds for the selected character or location (see
    panel_ui.build_gallery); empty if nothing is selected or the engine is down."""
    try:
        character = next((c for c in manifest["cast"]
                          if character_row_id(c["name"]) == selected_id), None)
        if character is not None:
            query = urllib.parse.urlencode(_engine_project(root, manifest))
            known = _http("GET", f"{ENGINE_URL}/characters?{query}", timeout=3)
            record = next((c for c in known if c.get("name", "").casefold()
                           == character["name"].casefold()), None) or {}
            images = record.get("version_images") or {}
            return {"versions": {v: images.get(v, "") for v in record.get("versions", [])},
                    "default": record.get("default_version")}
        location = next((loc for loc in manifest.get("locations", [])
                         if location_row_id(loc["name"]) == selected_id), None)
        if location is not None:
            record = _engine_location(root, manifest, location["name"]) or {}
            return {"image": record.get("image") or ""}
        prop = next((p for p in manifest.get("props", [])
                     if prop_row_id(p["name"]) == selected_id), None)
        if prop is not None:
            record = _engine_prop(root, manifest, prop["name"]) or {}
            images = {}
            if record.get("image_name"):
                images[record["image_name"]] = record.get("image") or ""
            images.update(record.get("previous") or {})
            return {"images": images, "current": record.get("image_name")}
    except EngineError:
        pass
    return {}


def _gallery_content(root, manifest, selected_id):
    if selected_id and selected_id.startswith("prop:"):
        menu = ((DOCK_GALLERY_ITEM, "Open"), (DOCK_PROP_IMAGE_DEFAULT, "Make current"),
                (DOCK_PROP_IMAGE_DELETE, "Delete…"))
    else:
        menu = ((DOCK_GALLERY_ITEM, "Open"), (DOCK_REF_DEFAULT, "Make default"),
                (DOCK_REF_RENAME, "Rename…"), (DOCK_REF_DELETE, "Delete…"))
    designing = _selected_design(manifest)
    status = _design_status(*designing) if designing else None
    return build_gallery(manifest, selected_id, root,
                         _gallery_references(root, manifest, selected_id), menu,
                         pending=status[1] if status else "")


def _dock_gallery_item(procedure, config, data):
    """Gallery: a tile was clicked. "take:<panel id>:<take id>" makes that the active
    take; "ref:<version>" and "loc:image" open the reference image in GIMP."""
    try:
        root = _DOCK_CONTEXT["root"]
        item = config.get_property("item") or ""
        kind, _, rest = item.partition(":")
        if kind == "take":
            panel_id, _, take_id = rest.rpartition(":")
            manifest = load_project(root)
            panel = next((p for p in manifest["panels"] if p["id"] == panel_id), None)
            if panel is not None and panel.get("active_take") != take_id:
                _activate_take(root, panel_id, take_id)
        elif kind == "ref":
            _open_selected_character_image(rest)
        elif kind == "loc":
            _open_selected_location_image()
        elif kind == "img":
            _open_selected_prop_image(rest)
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _ask_text(title, label, value="", action="OK"):
    """A one-line text prompt -> the text, or None if cancelled or left blank."""
    dialog = Gtk.Dialog(title=title, flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, action, Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_border_width(12)
    box.add(Gtk.Label(label=label, xalign=0.0))
    entry = Gtk.Entry(text=value, activates_default=True)
    entry.set_width_chars(34)
    box.add(entry)
    dialog.show_all()
    try:
        if dialog.run() != Gtk.ResponseType.OK:
            return None
        return " ".join(entry.get_text().split()) or None
    finally:
        dialog.destroy()


def _reference_action(action, version):
    """Make default / Rename… / Delete… on the selected character's reference ``version``,
    or Open it. -> False if cancelled. The engine holds the images; panels that pinned a
    renamed or deleted reference follow it (a deleted one falls back to the default)."""
    root = _DOCK_CONTEXT["root"]
    manifest = load_project(root)
    character = next((c for c in manifest["cast"]
                      if character_row_id(c["name"]) == _DOCK_CONTEXT.get("selected_id")),
                     None)
    if character is None or not version:
        raise ValueError("Select a character first")
    name = character["name"]
    if action == "open":
        _open_selected_character_image(version)
        return True
    body = {**_engine_project(root, manifest), "name": name, "version": version}
    if action == "default":
        _http("POST", f"{ENGINE_URL}/characters/versions/default", body, timeout=10)
    elif action == "rename":
        new = _ask_text(f"Rename {name}'s reference", "New name (letters, digits, - _):",
                        version, "Rename")
        if new is None or new == version:
            return False
        _http("POST", f"{ENGINE_URL}/characters/versions/rename",
              {**body, "new_version": new}, timeout=10)
        retarget_character_version(manifest, name, version, new)
        save_project(root, manifest)
    else:
        pinned = sum(1 for p in manifest["panels"] for c in p.get("characters", [])
                     if c.get("name", "").casefold() == name.casefold()
                     and c.get("version") == version)
        detail = (f"{name}'s reference “{version}” is set aside in the engine's "
                  "characters folder (.deleted-versions), where it can be restored by "
                  "hand.")
        if pinned:
            detail += (f" {pinned} panel{'s' if pinned != 1 else ''} using it will use "
                       "the default reference instead.")
        if not _confirm(f"Delete reference “{version}”?", detail, "Delete"):
            return False
        _http("POST", f"{ENGINE_URL}/characters/versions/delete", body, timeout=10)
        retarget_character_version(manifest, name, version, None)
        save_project(root, manifest)
    _refresh_project_docks()
    return True


def _dock_reference_menu(procedure, config, data):
    """Gallery > right-click a character's reference: Make default, Rename… or
    Delete… (item "ref:<version>")."""
    try:
        version = (config.get_property("item") or "").partition(":")[2]
        if not _reference_action(data, version):
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _manage_reference_dialog(name, version):
    """What to do with a reference: a dialog of buttons -> "open", "default", "rename",
    "delete" or None."""
    dialog = Gtk.Dialog(title=f"{name} · {version}", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons("Open in GIMP", 1, "Make default", 2, "Rename…", 3, "Delete…", 4,
                       Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
    box = dialog.get_content_area()
    box.set_border_width(12)
    box.add(Gtk.Label(label=f"What do you want to do with {name}'s reference “{version}”?",
                      xalign=0.0))
    dialog.show_all()
    try:
        return {1: "open", 2: "default", 3: "rename", 4: "delete"}.get(dialog.run())
    finally:
        dialog.destroy()


def _dock_manage_reference(procedure, config, data):
    """Context: Manage… on one of a character's reference images (item: its version)."""
    try:
        manifest = load_project(_DOCK_CONTEXT["root"])
        character = next((c for c in manifest["cast"] if character_row_id(c["name"])
                          == _DOCK_CONTEXT.get("selected_id")), None)
        version = config.get_property("item") or ""
        action = _manage_reference_dialog(character["name"] if character else "Character",
                                          version)
        if action is None or not _reference_action(action, version):
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _rename_selected_character():
    """Context: Rename character… -> False if cancelled. The old name stays as an alias;
    the engine's record (folder, references) follows if it has one."""
    root = _DOCK_CONTEXT["root"]
    manifest = load_project(root)
    character = next((c for c in manifest["cast"]
                      if character_row_id(c["name"]) == _DOCK_CONTEXT.get("selected_id")),
                     None)
    if character is None:
        raise ValueError("Select a character first")
    old = character["name"]
    if old in _DESIGN_JOBS.values():
        raise ValueError(f"{old} is being designed; rename them when the design finishes")
    new = _ask_text(f"Rename {old}", "New name (the old one stays as an alias):", old,
                    "Rename")
    if new is None or new == old:
        return False
    rename_character(manifest, old, new)  # checks the name before the engine is asked
    try:
        _http("POST", f"{ENGINE_URL}/characters/rename",
              {**_engine_project(root, manifest), "name": old, "new_name": new}, timeout=10)
    except EngineError as exc:
        if "no character" not in str(exc):  # a character the engine never designed is fine
            raise
    save_project(root, manifest)
    _DOCK_CONTEXT["selected_id"] = character_row_id(new)
    _refresh_project_docks()
    return True


def _setup_models_run(procedure, config, data):
    """Imanganation > Set Up Models: the workspace shows the dialog (it keeps polling
    the engine's download progress while you work)."""
    try:
        _dock_pdb_call(DOCK_SETUP_MODELS, {})
    except Exception:
        return _error(procedure, "The Imanganation workspace is not running; restart GIMP "
                                 "to start it")
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _render_engine_run(procedure, config, data):
    """Imanganation > Render Engine: the workspace shows the dialog for its open project
    (it keeps polling install progress while you work)."""
    try:
        _dock_pdb_call(DOCK_RENDER_ENGINE, {})
    except Exception:
        return _error(procedure, "The Imanganation workspace is not running; restart GIMP "
                                 "to start it")
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


_ENGINE_DIALOG = {}  # the open Render Engine dialog's widgets, while it's shown
_LOCATION_JOBS = {}  # job id -> project root, while locations are being designed


def _screen_height(widget, fallback=900):
    """The height of the screen ``widget`` is on (its monitor's workarea), in pixels."""
    try:
        display = widget.get_display()
        window = widget.get_window()
        monitor = (display.get_monitor_at_window(window) if window is not None
                   else display.get_primary_monitor() or display.get_monitor(0))
        return monitor.get_workarea().height
    except Exception:  # no display information: a size that fits most laptops
        return fallback


def _show_engine_dialog():
    """Render Engine: one choice per engine (licence, speed, install state), the face
    pass, Design Locations; Save writes project.render to project.json. Not modal:
    installs run in the engine and the dialog polls them every second."""
    if engine_ui is None or load_project is None:
        raise ValueError("This plug-in install is missing engine_ui.py or project_store.py")
    root = _DOCK_CONTEXT.get("root")
    if root is None:
        raise ValueError("Open a project first (File > Open / Switch Project)")
    if _ENGINE_DIALOG:
        _ENGINE_DIALOG["dialog"].present()
        return
    manifest = load_project(root)
    choice = engine_ui.project_render(manifest)
    try:
        report = _http("GET", f"{ENGINE_URL}/engines", timeout=5)
    except EngineError as exc:
        raise ValueError(f"The engine isn't answering: {exc}") from exc

    dialog = Gtk.Dialog(title=f"Render Engine — {manifest['project'].get('title', '')}")
    dialog.set_default_size(640, -1)
    # Engines, the face pass and the style outgrow a laptop screen: scroll, sized to
    # the content up to most of the screen's height (the buttons stay in view)
    scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                  vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                  propagate_natural_height=True, vexpand=True)
    scrolled.set_max_content_height(_screen_height(dialog) * 4 // 5 - 120)
    dialog.get_content_area().pack_start(scrolled, True, True, 0)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, border_width=12)
    scrolled.add(box)
    box.pack_start(Gtk.Label(label=engine_ui.INTRO, xalign=0.0, wrap=True,
                             max_width_chars=76), False, False, 0)
    rows, group, hooks = {}, None, {}

    def install(engine_id):
        try:
            _http("POST", f"{ENGINE_URL}/engines/{engine_id}/install", {}, timeout=10)
        except EngineError as exc:
            Gimp.message(f"Install failed to start: {exc}")
        refresh()

    def add_row(row, radio):
        nonlocal group
        grid = Gtk.Grid(column_spacing=12, row_spacing=2, margin_top=6)
        if radio:
            button = Gtk.RadioButton.new_with_label_from_widget(group, engine_ui.label(row))
            group = group or button
            button.set_active(row["id"] == choice["engine"])
        else:
            button = Gtk.CheckButton(label=engine_ui.label(row))
            button.set_active(bool(choice["face_pass"]))
        grid.attach(button, 0, 0, 2, 1)
        summary = Gtk.Label(label=row.get("summary", ""), xalign=0.0, wrap=True,
                            max_width_chars=70, margin_start=24)
        grid.attach(summary, 0, 1, 2, 1)
        licence = Gtk.Label(label=engine_ui.licence_line(row), xalign=0.0, margin_start=24)
        licence.get_style_context().add_class("dim-label")
        grid.attach(licence, 0, 2, 2, 1)
        state = Gtk.Label(xalign=0.0, margin_start=24)
        grid.attach(state, 0, 3, 1, 1)
        get = Gtk.Button(label="Install")
        get.connect("clicked", lambda *_: install(row["id"]))
        grid.attach(get, 1, 3, 1, 1)
        box.pack_start(grid, False, False, 0)
        rows[row["id"]] = {"button": button, "state": state, "install": get}
        # set_active on a later radio toggles this one before update_warning exists
        button.connect("toggled", lambda *_: hooks.get("warn", lambda: None)())

    for row in engine_ui.engines(report):
        add_row(row, radio=True)
    box.pack_start(Gtk.Separator(margin_top=6), False, False, 0)
    face = engine_ui.face_pass(report)
    if face is not None:
        add_row(face, radio=False)
    warning = Gtk.Label(xalign=0.0, wrap=True, max_width_chars=76, margin_top=6)
    box.pack_start(warning, False, False, 0)
    box.pack_start(Gtk.Separator(margin_top=6), False, False, 0)
    # The project's look: a preset from the engine (GET /styles) and the author's words
    current_style = engine_ui.project_style(manifest)
    try:
        presets = _http("GET", f"{ENGINE_URL}/styles", timeout=5)
    except EngineError:  # an engine from before styles: only the look it has
        presets = [{"id": "default", "label": "Clean modern anime"}]
    if current_style["preset"] not in {p["id"] for p in presets}:
        presets.append({"id": current_style["preset"], "label": current_style["preset"]})
    style_title = Gtk.Label(xalign=0.0, margin_top=6)
    style_title.set_markup("<b>Style</b>")
    box.pack_start(style_title, False, False, 0)
    box.pack_start(Gtk.Label(label=engine_ui.STYLE_INTRO, xalign=0.0, wrap=True,
                             max_width_chars=76), False, False, 0)
    style_grid = Gtk.Grid(column_spacing=12, row_spacing=6)
    style_combo = Gtk.ComboBoxText(hexpand=True)
    for entry in presets:
        style_combo.append(entry["id"], entry.get("label") or entry["id"])
    style_combo.set_active_id(current_style["preset"])
    style_text = Gtk.Entry(hexpand=True, max_length=300, text=current_style["text"],
                           placeholder_text="Optional, your own words: e.g. thick brush "
                                            "outlines, autumn palette")
    style_grid.attach(Gtk.Label(label="Look", xalign=0.0), 0, 0, 1, 1)
    style_grid.attach(style_combo, 1, 0, 1, 1)
    style_grid.attach(Gtk.Label(label="Also", xalign=0.0), 0, 1, 1, 1)
    style_grid.attach(style_text, 1, 1, 1, 1)
    style_nsfw = Gtk.CheckButton(label="Allow adult (NSFW) content in renders", active=bool(current_style.get("nsfw")))
    style_nsfw.set_tooltip_text("Off: 'nsfw' is added to every negative prompt so renders stay safe for work. On: the model may draw adult content when the story asks.")
    style_grid.attach(style_nsfw, 1, 2, 1, 1)
    box.pack_start(style_grid, False, False, 0)
    style_note = Gtk.Label(xalign=0.0, wrap=True, max_width_chars=76)
    style_note.get_style_context().add_class("dim-label")
    box.pack_start(style_note, False, False, 0)

    def update_style_note(*_):
        chosen = next((p for p in presets if p["id"] == style_combo.get_active_id()), None)
        style_note.set_text(engine_ui.style_note(chosen))

    style_combo.connect("changed", update_style_note)
    update_style_note()
    box.pack_start(Gtk.Separator(margin_top=6), False, False, 0)
    locations_note = Gtk.Label(xalign=0.0, wrap=True, max_width_chars=76)
    box.pack_start(locations_note, False, False, 0)
    locations_button = dialog.add_button("Design Locations", 1)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Save", Gtk.ResponseType.OK)

    def selected():
        engine = next((i for i, r in rows.items() if i != engine_ui.FACE_PASS
                       and r["button"].get_active()), choice["engine"])
        face_on = engine_ui.FACE_PASS in rows and rows[engine_ui.FACE_PASS]["button"].get_active()
        return {"engine": engine, "face_pass": face_on,
                "style": {"preset": style_combo.get_active_id() or "default",
                          "text": " ".join(style_text.get_text().split()),
                          **({"nsfw": True} if style_nsfw.get_active() else {})}}

    def update_warning():
        now = _ENGINE_DIALOG.get("report") or report
        picked = next((r for r in engine_ui.engines(now) if r["id"] == selected()["engine"]),
                      None)
        text = engine_ui.warning(picked)
        warning.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>" if text else "")
        locations_note.set_text(engine_ui.locations_note(selected()))

    hooks["warn"] = update_warning

    def refresh():
        if not _ENGINE_DIALOG:
            return GLib.SOURCE_REMOVE
        try:
            now = _http("GET", f"{ENGINE_URL}/engines", timeout=3)
        except EngineError:
            return GLib.SOURCE_CONTINUE  # engine restarting: try again next tick
        _ENGINE_DIALOG["report"] = now
        for row in now.get("engines", []):
            widgets = rows.get(row["id"])
            if widgets is None:
                continue
            widgets["state"].set_text(engine_ui.state(row))
            widgets["install"].set_visible(not row.get("installed"))
            widgets["install"].set_sensitive(engine_ui.can_install(row))
            # an engine can be chosen once installed; the current choice stays visible
            widgets["button"].set_sensitive(engine_ui.can_choose(row)
                                            or widgets["button"].get_active())
        locations_button.set_sensitive(not _LOCATION_JOBS)
        update_warning()
        return GLib.SOURCE_CONTINUE

    def on_response(dlg, response):
        if response == 1:
            _queue_location_design(root)
            refresh()
            return  # keep the dialog open
        if response == Gtk.ResponseType.OK:
            picked = selected()
            current = load_project(root)  # the docks may have saved meanwhile
            before = engine_ui.project_style(current)
            engine_ui.set_project_render(current, picked["engine"], picked["face_pass"],
                                         picked["style"])
            save_project(root, current)
            Gimp.message(f"Render engine for this project: {picked['engine']}"
                         + (", with the face pass." if picked["face_pass"] else "."))
            if engine_ui.project_style(current) != before:
                _offer_style_redesign(root, current)
        _ENGINE_DIALOG.clear()
        dlg.destroy()

    dialog.connect("response", on_response)
    dialog.connect("delete-event", lambda *_: _ENGINE_DIALOG.clear() or False)
    _ENGINE_DIALOG.update({"dialog": dialog, "report": report})
    dialog.show_all()
    refresh()
    GLib.timeout_add_seconds(1, refresh)


def _offer_style_redesign(root, manifest):
    """After the project's look changed: offer to redesign the characters and locations
    that have designs, so the references the panels follow are in the new look too.
    Characters keep their traits; earlier designs are kept as versions."""
    query = urllib.parse.urlencode(_engine_project(root, manifest))
    try:
        known = _http("GET", f"{ENGINE_URL}/characters?{query}", timeout=5)
        places = _http("GET", f"{ENGINE_URL}/locations?{query}", timeout=5)
    except EngineError as exc:
        Gimp.message(f"The look was saved. To draw the cast and locations in it, use "
                     f"Design character / Design location ({_engine_status(exc)}).")
        return
    designed = {c["name"].casefold() for c in known if c.get("default_version")}
    cast = [c for c in manifest["cast"] if c["name"].casefold() in designed
            and c["name"] not in _DESIGN_JOBS.values()]
    drawn = {p["key"] for p in places if p.get("image")}
    locations = [loc for loc in manifest.get("locations", [])
                 if location_key(loc["name"]) in drawn
                 and location_key(loc["name"]) not in _LOCATION_DESIGN_JOBS.values()]
    if not cast and not locations:
        return
    parts = [f"{len(cast)} character{'s' if len(cast) != 1 else ''}" if cast else "",
             f"{len(locations)} location{'s' if len(locations) != 1 else ''}"
             if locations else ""]
    what = " and ".join(p for p in parts if p)
    if not _confirm(f"Redesign {what} in the new look?",
                    "Panels follow these designs, so they keep the old look until the "
                    "designs are redrawn. Characters keep their traits; earlier designs "
                    "are kept as versions. About a minute each.", "Redesign"):
        return
    try:
        for character in cast:
            _queue_character_design(root, manifest, character, redesign=True,
                                    describe=False)
        for location in locations:
            _queue_single_location_design(root, manifest, location, redesign=True)
    except EngineError as exc:
        Gimp.message(f"Not every design was queued: {_engine_status(exc)}")
    _refresh_project_docks()


def _queue_location_design(root):
    """Ask the engine to design every location the project's panels use."""
    manifest = load_project(root)
    job = _http("POST", f"{ENGINE_URL}/locations/design",
                {"project": manifest["project"]["id"], "panels": manifest["panels"],
                 "locations": manifest.get("locations", []),
                 **_look_options(manifest)})
    if not _LOCATION_JOBS:
        GLib.timeout_add_seconds(3, _exclusive(_poll_location_jobs))
    _LOCATION_JOBS[job["id"]] = root
    Gimp.message("Designing the script's locations (about a minute each)…")


def _poll_location_jobs():
    for job_id in list(_LOCATION_JOBS):
        try:
            job = _http("GET", f"{ENGINE_URL}/jobs/{job_id}", timeout=3)
        except EngineError:
            continue
        if job["status"] in ("queued", "running"):
            continue
        del _LOCATION_JOBS[job_id]
        if job["status"] == "error":
            Gimp.message(f"Designing locations failed: {job.get('error')}")
        else:
            result = job.get("result") or {}
            made = [d["key"] for d in result.get("designed", [])]
            Gimp.message("Locations designed: " + (", ".join(made) or "none new")
                         + (f" (already had: {', '.join(result['existing'])})"
                            if result.get("existing") else ""))
        try:
            _refresh_project_docks()
        except Exception:
            pass
    return GLib.SOURCE_CONTINUE if _LOCATION_JOBS else GLib.SOURCE_REMOVE


_LOCATION_DESIGN_JOBS = {}  # engine job id -> location key, while one place renders


def _queue_single_location_design(root, manifest, location, redesign=False):
    """Ask the engine to design one location from its notes; the docks refresh when
    the image is ready."""
    body = {"project": manifest["project"]["id"], "name": location["name"],
            "description": location.get("notes", ""), "redesign": redesign,
            **_look_options(manifest)}
    job = _http("POST", f"{ENGINE_URL}/locations", body)
    _track_design(job, "location", location_key(location["name"]), _LOCATION_DESIGN_JOBS,
                  location["name"])


def _selected_location(manifest):
    selected = _DOCK_CONTEXT.get("selected_id")
    return next((loc for loc in manifest.get("locations", [])
                 if location_row_id(loc["name"]) == selected), None)


def _design_selected_location():
    """Context's Design location: design (or redesign) the selected place from its
    notes. A place with no notes is drawn from its name alone."""
    root = _DOCK_CONTEXT["root"]
    manifest = load_project(root)
    location = _selected_location(manifest)
    if location is None:
        raise ValueError("Select a location first")
    if location_key(location["name"]) in _LOCATION_DESIGN_JOBS.values():
        raise ValueError(f"{location['name']} is already being designed")
    record = _engine_location(root, manifest, location["name"])
    _queue_single_location_design(root, manifest, location,
                                  redesign=bool(record and record.get("image")))
    _refresh_project_docks()


def _open_reference_image(path, parasite, root, name):
    """Open a reference image in GIMP, tagged with its project and owner so the Set …
    Reference from Layer command fills them in after painting over it."""
    image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(str(path)))
    image.attach_parasite(Gimp.Parasite.new(
        parasite, Gimp.PARASITE_PERSISTENT,
        list(json.dumps({"project": str(root), "name": name}).encode())))
    Gimp.Display.new(image)


def _open_selected_location_image():
    """Open the selected location's reference image in GIMP, to look at or paint over."""
    root = _DOCK_CONTEXT["root"]
    manifest = load_project(root)
    location = _selected_location(manifest)
    if location is None:
        raise ValueError("Select a location first")
    record = _engine_location(root, manifest, location["name"])
    if record is None or not record.get("image"):
        raise ValueError(f"{location['name']} has no reference image yet")
    _open_reference_image(record["image"], LOCATION_PARASITE, root, location["name"])


def _open_selected_character_image(version=None):
    """Open the selected character's reference image in GIMP: the active one, or the
    named ``version`` (an extra reference)."""
    root = _DOCK_CONTEXT["root"]
    manifest = load_project(root)
    character = next((c for c in manifest["cast"]
                      if character_row_id(c["name"]) == _DOCK_CONTEXT.get("selected_id")),
                     None)
    if character is None:
        raise ValueError("Select a character first")
    query = urllib.parse.urlencode(_engine_project(root, manifest))
    known = _http("GET", f"{ENGINE_URL}/characters?{query}", timeout=3)
    record = next((c for c in known
                   if c.get("name", "").casefold() == character["name"].casefold()), None)
    path = ((record or {}).get("version_images") or {}).get(version) if version else \
        (record or {}).get("reference")
    if not path:
        raise ValueError(f"{character['name']} has no reference image yet" if not version
                         else f"{character['name']} has no reference called {version}")
    _open_reference_image(path, CHARACTER_PARASITE, root, record["name"])


def _dock_open_character_version(procedure, config, data):
    """Context: Open on one of a character's reference images (item: its version)."""
    try:
        _open_selected_character_image(config.get_property("item") or None)
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


_PROP_DESIGN_JOBS = {}  # engine job id -> prop key, while one prop renders


def _engine_prop(root, manifest, name):
    """The engine's record of this prop ({"key", "image", "previous", …}), or None if it
    has none. Raises EngineError when the engine isn't answering."""
    query = urllib.parse.urlencode(_engine_project(root, manifest))
    known = _http("GET", f"{ENGINE_URL}/props?{query}", timeout=3)
    key = prop_key(name)
    return next((p for p in known if p.get("key") == key), None)


def _send_prop_description(root, manifest, prop):
    """Keep the prop's notes in the engine, so renders on any engine put them in the
    prompt (best effort: the notes are saved in the project either way)."""
    try:
        _http("POST", f"{ENGINE_URL}/props/description",
              {**_engine_project(root, manifest), "name": prop["name"],
               "description": prop.get("notes", "")}, timeout=5)
    except EngineError:
        pass


def _queue_prop_design(root, manifest, prop, redesign=False):
    """Ask the engine to design one prop from its notes; the docks refresh when the
    image is ready. The image is drawn by Qwen-Image whatever engine the panels use,
    like a location's, because Qwen renders are what read it."""
    body = {"project": manifest["project"]["id"], "name": prop["name"],
            "description": prop.get("notes", ""), "redesign": redesign,
            **_look_options(manifest)}
    job = _http("POST", f"{ENGINE_URL}/props", body)
    _track_design(job, "prop", prop_key(prop["name"]), _PROP_DESIGN_JOBS, prop["name"])


def _selected_prop(manifest):
    selected = _DOCK_CONTEXT.get("selected_id")
    return next((p for p in manifest.get("props", [])
                 if prop_row_id(p["name"]) == selected), None)


def _design_selected_prop():
    """Context's Design prop: design (or redesign) the selected prop from its notes. A
    prop with no notes is drawn from its name alone."""
    root = _DOCK_CONTEXT["root"]
    manifest = load_project(root)
    prop = _selected_prop(manifest)
    if prop is None:
        raise ValueError("Select a prop first")
    if prop_key(prop["name"]) in _PROP_DESIGN_JOBS.values():
        raise ValueError(f"{prop['name']} is already being designed")
    record = _engine_prop(root, manifest, prop["name"])
    _queue_prop_design(root, manifest, prop, redesign=bool(record and record.get("image")))
    _refresh_project_docks()


def _open_selected_prop_image(image_name=None):
    """Open the selected prop's reference image (or one of its earlier images, by file
    name) in GIMP, to look at or paint over."""
    root = _DOCK_CONTEXT["root"]
    manifest = load_project(root)
    prop = _selected_prop(manifest)
    if prop is None:
        raise ValueError("Select a prop first")
    record = _engine_prop(root, manifest, prop["name"])
    path = None
    if record is not None:
        path = (record.get("previous") or {}).get(image_name) if image_name \
            and image_name != record.get("image_name") else record.get("image")
    if not path:
        raise ValueError(f"{prop['name']} has no reference image yet")
    _open_reference_image(path, PROP_PARASITE, root, prop["name"])


def _engine_prop_rows(root, manifest, prop):
    rows = ["# Engine prop reference"]
    rows += _design_rows("prop", prop_key(prop["name"]))
    try:
        record = _engine_prop(root, manifest, prop["name"])
    except EngineError as exc:
        return rows + [f"Status\t{_engine_status(exc)}"]
    if record is None or not record.get("image"):
        rows.append("Reference image\tNot designed yet")
    else:
        count = len(record.get("previous") or {}) + 1
        rows.extend([
            f"Reference image\t{record['image_name']}"
            + (f" · {count} images" if count > 1 else ""),
            f"Designed\t{record.get('created_at') or 'Unknown'}",
            f"!{DOCK_OPEN_PROP_IMAGE}\tOpen reference image",
            "Paint over\tOpen it, edit, then Imanganation > Set Prop Reference from "
            "Layer…",
        ])
    if engine_ui is not None and engine_ui.project_render(manifest)["engine"] != "qwen_image_21":
        rows.append("Used by\tQwen-Image 2.1 renders as a picture; other engines use "
                    "its name and notes")
    return rows


def _choose_new_prop():
    """New Prop dialog -> (name, description, design now) or None."""
    dialog = Gtk.Dialog(title="New Prop", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Create", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    grid = Gtk.Grid(column_spacing=12, row_spacing=8, margin=12)
    name = Gtk.Entry(activates_default=True, hexpand=True,
                     placeholder_text="As panels list it, e.g. Red umbrella")
    description = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                               left_margin=4, right_margin=4, top_margin=4, bottom_margin=4)
    scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
    scrolled.set_size_request(380, 120)
    scrolled.set_shadow_type(Gtk.ShadowType.IN)
    scrolled.add(description)
    hint = Gtk.Label(label="Shape, size, materials, colours, wear, markings. List the "
                           "prop under Props in a panel's Context to keep it the same "
                           "in every panel.",
                     xalign=0.0, wrap=True, max_width_chars=48)
    hint.get_style_context().add_class("dim-label")
    design = Gtk.CheckButton(label="Design the prop now (uses the engine)", active=True)
    for row, (label, widget) in enumerate((("Name", name), ("Description", scrolled))):
        caption = Gtk.Label(label=label, xalign=0.0, valign=Gtk.Align.START)
        grid.attach(caption, 0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)
    grid.attach(hint, 1, 2, 1, 1)
    grid.attach(design, 1, 3, 1, 1)
    dialog.get_content_area().add(grid)
    dialog.show_all()
    try:
        while dialog.run() == Gtk.ResponseType.OK:
            buffer = description.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
            chosen = (" ".join(name.get_text().split()), " ".join(text.split()),
                      design.get_active())
            if any(ch.isalnum() for ch in chosen[0]):
                return chosen
            Gimp.message("Give the prop a name.")
        return None
    finally:
        dialog.destroy()


def _dock_prop_menu(procedure, config, data):
    """Project tree right-click: New prop… (Props heading), or Design prop / Delete
    prop… (a prop row, whose id is the item)."""
    try:
        root = _DOCK_CONTEXT["root"]
        if data == "design":
            _DOCK_CONTEXT["selected_id"] = config.get_property("item")
            _design_selected_prop()
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
        if data == "delete":
            if not _delete_prop(root, config.get_property("item")):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
        chosen = _choose_new_prop()
        if chosen is None:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        name, description, design = chosen
        manifest = load_project(root)
        prop = add_prop(manifest, name, description)
        save_project(root, manifest)
        _DOCK_CONTEXT["selected_id"] = prop_row_id(prop["name"])
        _send_prop_description(root, manifest, prop)
        if design:
            try:
                _queue_prop_design(root, manifest, prop)
            except EngineError as exc:
                Gimp.message(f"{name} was added but not designed: {_engine_status(exc)}. "
                             "Use Design prop when the engine runs.")
        _refresh_project_docks()
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _delete_prop(root, row_id):
    """Delete prop…: confirm, take it out of the project's props and its panels, and have
    the engine set its images aside. -> False if cancelled."""
    manifest = load_project(root)
    prop = next((p for p in manifest.get("props", []) if prop_row_id(p["name"]) == row_id),
                None)
    if prop is None:
        raise ValueError("That prop is no longer in the project")
    name = prop["name"]
    if prop_key(name) in _PROP_DESIGN_JOBS.values():
        raise ValueError(f"{name} is being designed; delete it when the design finishes")
    try:
        record, engine_down = _engine_prop(root, manifest, name), None
    except EngineError as exc:
        record, engine_down = None, exc
    panels = len(panels_with_prop(manifest, name))
    detail = f"{name} is removed from the project's props"
    detail += (f" and from {panels} panel{'s' if panels != 1 else ''}." if panels else ".")
    if record is not None:
        detail += (" Its images move to the engine's props/.deleted folder, where they "
                   "can be restored by hand.")
    elif engine_down is not None:
        detail += (f" The engine isn't reachable ({_engine_status(engine_down)}), so any "
                   "image stays in the engine.")
    if not _confirm(f"Delete {name}?", detail, "Delete"):
        return False
    delete_prop(manifest, name)
    save_project(root, manifest)
    if record is not None:
        query = urllib.parse.urlencode({**_engine_project(root, manifest), "name": name})
        try:
            _http("DELETE", f"{ENGINE_URL}/props?{query}", timeout=10)
        except EngineError as exc:
            Gimp.message(f"{name} was removed from the project, but the engine kept its "
                         f"images: {_engine_status(exc)}")
    if _DOCK_CONTEXT.get("selected_id") == row_id:
        _DOCK_CONTEXT["selected_id"] = None
    _refresh_project_docks()
    return True


def _dock_prop_image_menu(procedure, config, data):
    """Gallery > right-click a prop's image: Make current or Delete… (item
    "img:<file name>")."""
    try:
        root = _DOCK_CONTEXT["root"]
        image_name = (config.get_property("item") or "").partition(":")[2]
        manifest = load_project(root)
        prop = _selected_prop(manifest)
        if prop is None or not image_name:
            raise ValueError("Select a prop first")
        body = {**_engine_project(root, manifest), "name": prop["name"], "image": image_name}
        if data == "default":
            _http("POST", f"{ENGINE_URL}/props/images/default", body, timeout=10)
        else:
            if not _confirm(f"Delete {image_name}?",
                            f"{prop['name']}'s image {image_name} is set aside in the "
                            "engine's props/.deleted folder, where it can be restored by "
                            "hand. If it is the current image, the newest earlier one "
                            "takes its place.", "Delete"):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            _http("POST", f"{ENGINE_URL}/props/images/delete", body, timeout=10)
        _refresh_project_docks()
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _choose_new_location():
    """New Location dialog -> (name, description, design now) or None."""
    dialog = Gtk.Dialog(title="New Location", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Create", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    grid = Gtk.Grid(column_spacing=12, row_spacing=8, margin=12)
    name = Gtk.Entry(activates_default=True, hexpand=True,
                     placeholder_text="As the script names it, e.g. School rooftop")
    description = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                               left_margin=4, right_margin=4, top_margin=4, bottom_margin=4)
    scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
    scrolled.set_size_request(380, 120)
    scrolled.set_shadow_type(Gtk.ShadowType.IN)
    scrolled.add(description)
    hint = Gtk.Label(label="Inside or outside, era, layout, landmarks, materials, what "
                           "lies beyond. Leave out the time of day: one image serves "
                           "every panel set here.",
                     xalign=0.0, wrap=True, max_width_chars=48)
    hint.get_style_context().add_class("dim-label")
    design = Gtk.CheckButton(label="Design the location now (uses the engine)", active=True)
    for row, (label, widget) in enumerate((("Name", name), ("Description", scrolled))):
        caption = Gtk.Label(label=label, xalign=0.0, valign=Gtk.Align.START)
        grid.attach(caption, 0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)
    grid.attach(hint, 1, 2, 1, 1)
    grid.attach(design, 1, 3, 1, 1)
    dialog.get_content_area().add(grid)
    dialog.show_all()
    try:
        while dialog.run() == Gtk.ResponseType.OK:
            buffer = description.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
            chosen = (" ".join(name.get_text().split()), " ".join(text.split()),
                      design.get_active())
            if any(ch.isalnum() for ch in location_key(chosen[0])):
                return chosen
            Gimp.message("Give the location a name.")
        return None
    finally:
        dialog.destroy()


def _dock_location_menu(procedure, config, data):
    """Project tree right-click: New location… (Locations heading), or Design
    location / Delete location… (a location row, whose id is the item)."""
    try:
        root = _DOCK_CONTEXT["root"]
        if data == "design":
            _DOCK_CONTEXT["selected_id"] = config.get_property("item")
            _design_selected_location()
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
        if data == "delete":
            if not _delete_location(root, config.get_property("item")):
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
        chosen = _choose_new_location()
        if chosen is None:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        name, description, design = chosen
        manifest = load_project(root)
        if find_location(manifest, name) is not None:
            raise ValueError(f"{find_location(manifest, name)['name']} is already a location")
        location = {"name": name}
        if description:
            location["notes"] = description
        manifest.setdefault("locations", []).append(location)
        save_project(root, manifest)
        _DOCK_CONTEXT["selected_id"] = location_row_id(name)
        if design:
            try:
                _queue_single_location_design(root, manifest, location)
            except EngineError as exc:
                Gimp.message(f"{name} was added but not designed: {_engine_status(exc)}. "
                             "Use Design location when the engine runs.")
        _refresh_project_docks()
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _delete_location(root, row_id):
    """Delete location…: confirm, take it out of the project's locations, and have the
    engine set its images aside. Panels keep their location text. -> False if
    cancelled."""
    manifest = load_project(root)
    location = next((loc for loc in manifest.get("locations", [])
                     if location_row_id(loc["name"]) == row_id), None)
    if location is None:
        raise ValueError("That location is no longer in the project")
    name = location["name"]
    if location_key(name) in _LOCATION_DESIGN_JOBS.values():
        raise ValueError(f"{name} is being designed; delete it when the design finishes")
    try:
        record, engine_down = _engine_location(root, manifest, name), None
    except EngineError as exc:
        record, engine_down = None, exc
    panels = len(panels_at_location(manifest, name))
    detail = f"{name} is removed from the project's locations."
    if panels:
        detail += (f" {panels} panel{'s' if panels != 1 else ''} set there keep "
                   f"{'their' if panels != 1 else 'its'} location text.")
    if record is not None:
        detail += (" Its reference images move to the engine's locations/.deleted "
                   "folder, where they can be restored by hand.")
    elif engine_down is not None:
        detail += (f" The engine isn't reachable ({_engine_status(engine_down)}), so any "
                   "reference image stays in the engine and Qwen-Image renders keep "
                   "using it.")
    if not _confirm(f"Delete {name}?", detail, "Delete"):
        return False
    delete_location(manifest, name)
    save_project(root, manifest)
    if record is not None:
        query = urllib.parse.urlencode({**_engine_project(root, manifest), "name": name})
        try:
            _http("DELETE", f"{ENGINE_URL}/locations?{query}", timeout=10)
        except EngineError as exc:
            Gimp.message(f"{name} was removed from the project, but the engine kept its "
                         f"image: {_engine_status(exc)}")
    if _DOCK_CONTEXT.get("selected_id") == row_id:
        _DOCK_CONTEXT["selected_id"] = None
    _refresh_project_docks()
    return True


_SETUP_DIALOG = {}  # the open Set Up Models dialog's widgets, while it's shown


def _setup_report():
    """GET /setup, or None while the engine isn't answering."""
    try:
        return _http("GET", f"{ENGINE_URL}/setup", timeout=3)
    except EngineError:
        return None


def _show_setup_dialog():
    """Set Up Models: what's missing, licences, Download / Use Files I Have / Cancel.
    Not modal: downloads run in the engine, and the dialog polls them every second."""
    if setup_ui is None:
        raise ValueError("This plug-in install is missing setup_ui.py")
    if _SETUP_DIALOG:
        _SETUP_DIALOG["dialog"].present()
        return
    dialog = Gtk.Dialog(title="Set Up Models")
    dialog.set_default_size(620, -1)
    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_border_width(12)
    intro = Gtk.Label(label=setup_ui.INTRO, xalign=0.0, wrap=True, max_width_chars=72)
    box.pack_start(intro, False, False, 0)
    grid = Gtk.Grid(column_spacing=12, row_spacing=4, margin_top=4)
    box.pack_start(grid, False, False, 0)
    headline = Gtk.Label(xalign=0.0, wrap=True, max_width_chars=72)
    headline.get_style_context().add_class("heading")
    box.pack_start(headline, False, False, 0)
    bar = Gtk.ProgressBar(show_text=True, no_show_all=True)
    box.pack_start(bar, False, False, 0)
    details = Gtk.Label(xalign=0.0, wrap=True, max_width_chars=72, selectable=True)
    box.pack_start(details, False, False, 0)
    licence = Gtk.Label(xalign=0.0, wrap=True, max_width_chars=72)
    licence.get_style_context().add_class("dim-label")
    box.pack_start(licence, False, False, 0)
    box.pack_start(Gtk.Separator(margin_top=6), False, False, 0)
    box.pack_start(Gtk.Label(label=setup_ui.OPTIONAL_INTRO, xalign=0.0, wrap=True,
                             max_width_chars=72), False, False, 0)
    optional = Gtk.Grid(column_spacing=12, row_spacing=4)
    box.pack_start(optional, False, False, 0)

    link_button = dialog.add_button("Use Files I Have…", 1)
    cancel_button = dialog.add_button("Pause Download", 2)
    download_button = dialog.add_button("Download", 3)
    dialog.add_button("Close", Gtk.ResponseType.CLOSE)
    widgets = {"dialog": dialog, "grid": grid, "headline": headline, "bar": bar,
               "details": details, "licence": licence, "link": link_button,
               "cancel": cancel_button, "download": download_button, "rows": None,
               "optional": optional, "optional_rows": None}

    def post(path, body):
        try:
            _http("POST", f"{ENGINE_URL}{path}", body, timeout=10)
        except EngineError as exc:
            Gimp.message(f"Set Up Models: {exc}")
        refresh()

    def on_response(_dialog, response):
        if response == 1:
            folder = _choose_models_folder(dialog)
            if folder is not None:
                post("/setup/link", {"folders": [str(folder)]})
        elif response == 2:
            post("/setup/cancel", {})
        elif response == 3:
            post("/setup/download", {})
        else:
            dialog.destroy()

    def refresh():
        if not _SETUP_DIALOG:
            return False  # closed: stop polling
        engines_report = _engines_report()
        _update_setup_dialog(widgets, _setup_report(), engines_report)
        _update_optional_engines(widgets, engines_report, post)
        return True

    def on_destroy(_widget):
        _SETUP_DIALOG.clear()

    dialog.connect("response", on_response)
    dialog.connect("destroy", on_destroy)
    _SETUP_DIALOG.update(widgets)
    refresh()
    dialog.show_all()
    GLib.timeout_add_seconds(1, refresh)


def _update_setup_dialog(widgets, report, engines_report=None):
    """Fill the dialog from a GET /setup report (None: the engine isn't answering)."""
    actions = setup_ui.actions(report, engines_report)
    widgets["download"].set_label(actions["download"])
    widgets["download"].set_sensitive(actions["download_enabled"])
    widgets["link"].set_sensitive(actions["link_enabled"])
    widgets["cancel"].set_sensitive(actions["cancel_enabled"])
    if report is None:
        widgets["headline"].set_text(
            "Waiting for the Imanganation engine to start…" if ENGINE_FOUND else
            "GIMP can't find the Imanganation engine. Install it (see its README), then "
            "run `uv run manganation serve` once in its folder so GIMP finds it, and "
            "restart GIMP.")
        widgets["details"].set_text("")
        widgets["bar"].hide()
        return
    grid, task = widgets["grid"], report.get("task") or {}
    rows = report.get("models", [])
    if widgets["rows"] is None or len(widgets["rows"]) != len(rows):
        for child in grid.get_children():
            grid.remove(child)
        widgets["rows"] = []
        for index, row in enumerate(rows):
            feature = Gtk.Label(label=row.get("feature") or row.get("role"), xalign=0.0,
                                hexpand=True, wrap=True, max_width_chars=34)
            size = Gtk.Label(label=setup_ui.human_size(row.get("size")), xalign=1.0)
            state = Gtk.Label(xalign=0.0)
            licence = (Gtk.LinkButton.new_with_label(row["license_url"], "Licence")
                       if row.get("license_url") else Gtk.Label(label=""))
            licence.set_tooltip_text(row.get("license") or "")
            for column, widget in enumerate((feature, size, state, licence)):
                grid.attach(widget, column, index, 1, 1)
            widgets["rows"].append(state)
        grid.show_all()
    for label, row in zip(widgets["rows"], rows, strict=True):
        label.set_text(setup_ui.row_state(row, task))
    widgets["headline"].set_text(setup_ui.headline(report))
    fraction, text = setup_ui.progress(report)
    if text:
        if fraction < 0:
            widgets["bar"].pulse()
        else:
            widgets["bar"].set_fraction(fraction)
        widgets["bar"].set_text(text)
        widgets["bar"].show()
    else:
        widgets["bar"].hide()
    widgets["details"].set_text("\n".join(setup_ui.details(report)))
    widgets["licence"].set_text(setup_ui.licence_note(report))


def _engines_report():
    """GET /engines, or None while the engine isn't answering."""
    try:
        return _http("GET", f"{ENGINE_URL}/engines", timeout=3)
    except EngineError:
        return None


def _update_optional_engines(widgets, report, post):
    """The optional engines under Set Up Models: name, size, state, licence, Install."""
    grid = widgets["optional"]
    rows = setup_ui.optional_engines(report)
    if engine_ui is None or not rows:
        grid.hide()
        return
    if widgets["optional_rows"] is None or len(widgets["optional_rows"]) != len(rows):
        for child in grid.get_children():
            grid.remove(child)
        widgets["optional_rows"] = []
        for index, row in enumerate(rows):
            name = Gtk.Label(label=engine_ui.label(row), xalign=0.0, hexpand=True,
                             wrap=True, max_width_chars=34)
            name.set_tooltip_text(row.get("summary") or "")
            size = Gtk.Label(xalign=1.0)
            state = Gtk.Label(xalign=0.0, wrap=True, max_width_chars=28)
            licence = (Gtk.LinkButton.new_with_label(row["licence_url"], "Licence")
                       if row.get("licence_url") else Gtk.Label(label=""))
            licence.set_tooltip_text(engine_ui.licence_line(row))
            get = Gtk.Button(label="Install")
            get.connect("clicked", lambda *_, i=row["id"]: post(f"/engines/{i}/install", {}))
            for column, widget in enumerate((name, size, state, licence, get)):
                grid.attach(widget, column, index, 1, 1)
            widgets["optional_rows"].append((size, state, get))
        grid.show_all()
    for (size, state, get), row in zip(widgets["optional_rows"], rows, strict=True):
        size.set_text(setup_ui.optional_size(row))
        state.set_text(engine_ui.state(row))
        get.set_visible(not row.get("installed"))
        get.set_sensitive(engine_ui.can_install(row))
    grid.show()


def _choose_models_folder(parent):
    dialog = Gtk.FileChooserDialog(
        title="Folder of Models You Already Have", action=Gtk.FileChooserAction.SELECT_FOLDER,
        transient_for=parent)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Use This Folder", Gtk.ResponseType.ACCEPT)
    dialog.set_modal(True)
    dialog.set_current_folder(str(Path.home()))
    response = dialog.run()
    filename = dialog.get_filename() if response == Gtk.ResponseType.ACCEPT else None
    dialog.destroy()
    return Path(filename) if filename else None


def _prompt_setup_when_engine_up(deadline):
    """At startup, once the engine answers: open Set Up Models if rendering can't work
    yet (no checkpoint). Gives up quietly after ``deadline``."""
    def poll():
        report = _setup_report()
        if report is None:
            return time.monotonic() < deadline  # keep waiting while the engine starts
        if setup_ui is not None and setup_ui.should_prompt(report):
            try:
                _show_setup_dialog()
            except Exception as exc:  # never break the workspace over this
                Gimp.message(f"Could not open Set Up Models: {exc}")
        return False

    GLib.timeout_add_seconds(3, poll)


def _new_project_run(procedure, config, data):
    """File > Create > New Project from Script: the extension shows the dialog."""
    try:
        _dock_pdb_call(DOCK_NEW_PROJECT, {})
    except Exception:
        return _error(procedure, "The Imanganation workspace is not running; restart GIMP "
                                 "to start it")
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _new_manual_project_run(procedure, config, data):
    """File > New Project: guide the user through a script-free project setup."""
    try:
        _dock_pdb_call(DOCK_NEW_MANUAL_PROJECT, {})
    except Exception:
        return _error(procedure, "The Imanganation workspace is not running; restart GIMP "
                                 "to start it")
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


_BUBBLE_PREVIEWS = {}


def _bubble_previews():
    if not _BUBBLE_PREVIEWS and lettering is not None:
        _BUBBLE_PREVIEWS.update(lettering.preview_paths(Path(Gimp.directory()) / "imanganation"))
    return _BUBBLE_PREVIEWS


def _bubble_library():
    return lettering.library_rows(_bubble_previews()) if lettering else "# Bubbles\nUnavailable"


def _open_page_images(manifest):
    """Page images the workspace has open: {page id: image}."""
    found = {}
    for (project_id, page_id), image in list(_PAGE_IMAGES.items()):
        if project_id == manifest["project"]["id"] and image is not None and image.is_valid():
            found[page_id] = image
    return found


def _dock_actions(root, manifest):
    """build_docks' action procedures, plus which script lines already have bubbles
    on an open page."""
    bubbled = set()
    if lettering is not None:
        for image in _open_page_images(manifest).values():
            bubbled.update(f"{r.get('panel')}:{r.get('line')}"
                           for _g, r in lettering.find_bubbles(image) if r.get("panel"))
    return {"design_action": DOCK_DESIGN_CHARACTER,
            "new_character_action": DOCK_NEW_CHARACTER,
            "design_character_menu": DOCK_DESIGN_CHARACTER_ITEM,
            "delete_character_menu": DOCK_DELETE_CHARACTER,
            "delete_page_action": DOCK_DELETE_PAGE,
            "add_panel_action": DOCK_ADD_PANEL,
            "delete_panel_action": DOCK_DELETE_PANEL,
            "duplicate_panel_action": DOCK_DUPLICATE_PANEL,
            "add_cover_action": DOCK_ADD_COVER,
            "character_version_action": DOCK_CHARACTER_VERSION,
            "take_action": DOCK_ACTIVATE_TAKE,
            "generate_page_action": DOCK_GENERATE_PAGE,
            "design_prop_action": DOCK_DESIGN_PROP,
            "inline_choices": True,
            "character_buttons": ((DOCK_VARIANT_BUTTON, "Design another reference…"),
                                  (DOCK_RENAME_CHARACTER_BUTTON, "Rename character…"),
                                  (DOCK_DELETE_CHARACTER_BUTTON, "Delete character…")),
            "delete_prop_action": DOCK_DELETE_PROP_BUTTON,
            "delete_location_action": DOCK_DELETE_LOCATION_BUTTON,
            "new_prop_action": DOCK_NEW_PROP,
            "design_prop_menu": DOCK_DESIGN_PROP_ITEM,
            "delete_prop_menu": DOCK_DELETE_PROP,
            "design_variant_menu": DOCK_DESIGN_VARIANT,
            "add_cover_page_action": DOCK_ADD_COVER_PAGE,
            "reorder_pages_action": DOCK_REORDER_PAGES,
            "bubble_line_action": DOCK_BUBBLE_LINE, "bubbled": frozenset(bubbled),
            "new_bubble_action": DOCK_NEW_BUBBLE,
            "design_location_action": DOCK_DESIGN_LOCATION,
            "new_location_action": DOCK_NEW_LOCATION,
            "design_location_menu": DOCK_DESIGN_LOCATION_ITEM,
            "delete_location_menu": DOCK_DELETE_LOCATION,
            "close_project_action": DOCK_CLOSE_PROJECT,
            "reload_script_action": DOCK_RELOAD_SCRIPT,
            "load_script_action": DOCK_LOAD_SCRIPT}


def _selected_bubble(image=None):
    """The bubble group selected on the canvas (the group or one of its layers)."""
    if lettering is None:
        return None
    try:
        image = image or Gimp.context_get_image()
        if image is None:
            return None
        for layer in image.get_selected_layers():
            for candidate in (layer, layer.get_parent()):
                if (candidate is not None
                        and candidate.get_parasite(lettering.BUBBLE_PARASITE) is not None):
                    return candidate
    except Exception:
        return None
    return None


def _canvas_bubble_rows():
    group = _selected_bubble()
    if group is None:
        return []
    record = lettering._parasite(group, lettering.BUBBLE_PARASITE) or {}
    _shape, text = lettering.bubble_parts(group)
    template = bubble_templates.template_by_id(record.get("template", ""))
    value = " ".join((text.get_text() or "").split()) if text else ""
    return ["# Bubble",
            f"@bubble:{group.get_id()}.text\tText\t{value}",
            f"Style\t{template.label if template else record.get('template', '?')}",
            f"!{DOCK_FIT_BUBBLE}\tFit bubble to text"]


def _bubble_tail_tip(image, speaker, bubble_center, frame):
    """Where a script line's tail points: the speaker's placement blob (its upper
    part, where the head is), or else into the frame below the bubble."""
    import placement

    layer = next((found for name, found in placement.placement_layers(image).items()
                  if name.casefold() == (speaker or "").casefold()), None)
    if layer is not None:
        saved = Gimp.Selection.save(image)
        try:
            image.select_item(Gimp.ChannelOps.REPLACE, layer)
            _, painted, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
        finally:
            image.select_item(Gimp.ChannelOps.REPLACE, saved)
            image.remove_channel(saved)
        if painted:
            return ((x1 + x2) / 2, y1 + (y2 - y1) * 0.18)
    fx, fy, fw, fh = frame
    toward = 1 if bubble_center[0] < fx + fw / 2 else -1
    return (bubble_center[0] + toward * fw * 0.08, bubble_center[1] + fh * 0.22)


def _next_bubble_center(image, panel_id, frame, reading_order):
    """Two columns of slots in the frame, in reading order (right first for rtl)."""
    count = sum(r.get("panel") == panel_id for _g, r in lettering.find_bubbles(image))
    column, row = count % 2, count // 2
    first, second = (0.7, 0.3) if reading_order == "rtl" else (0.3, 0.7)
    fx, fy, fw, fh = frame
    return (fx + fw * (first if column == 0 else second),
            fy + fh * min(0.82, 0.18 + 0.26 * row))


def _bubble_target_image():
    image = Gimp.context_get_image()
    if image is None or not image.is_valid():
        raise ValueError("Open a page to add a bubble to")
    return image


def _selection_box(image):
    _, nonempty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
    if nonempty and (x2 - x1) > 20 and (y2 - y1) > 20:
        return (x1, y1, x2 - x1, y2 - y1)
    return None


def _dock_bubble(procedure, config, data):
    """Bubble…: a script line's (line), a page's (new), a Bubbles dock tile
    (template), and Fit bubble to text (fit)."""
    try:
        if data == "fit":
            group = _selected_bubble()
            if group is None:
                raise ValueError("Select a bubble on the page first")
            lettering.fit_bubble(group.get_image(), group)
        elif data == "template":
            template = bubble_templates.template_by_id(config.get_property("item"))
            image = _bubble_target_image()
            box = _selection_box(image)
            if box:
                Gimp.Selection.none(image)
            lettering.insert_bubble(image, template, "Text", box=box,
                                    center=None if box else (image.get_width() / 2,
                                                             image.get_height() / 2))
        elif data == "new":
            root = _DOCK_CONTEXT["root"]
            manifest = load_project(root)
            page_id = _DOCK_CONTEXT.get("selected_id")
            image = (_show_project_page(root, manifest, page_id)
                     if any(p["id"] == page_id for p in manifest["pages"])
                     else _bubble_target_image())
            chosen = lettering.choose_bubble("speech", "", _bubble_previews(),
                                             size=lettering.default_size(image))
            if chosen is None:
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            template, text, vertical, size = chosen
            box = _selection_box(image)
            if box:
                Gimp.Selection.none(image)
            lettering.insert_bubble(image, template, text or "Text", box=box,
                                    center=None if box else (image.get_width() / 2,
                                                             image.get_height() / 2),
                                    size=size, vertical=vertical)
        else:  # a script line
            panel_id, _, line = config.get_property("item").rpartition(":")
            root = _DOCK_CONTEXT["root"]
            manifest = load_project(root)
            panel = next((p for p in manifest["panels"] if p["id"] == panel_id), None)
            if panel is None:
                raise ValueError("That panel is no longer in the project")
            page_id = (panel.get("placement") or {}).get("page")
            if not page_id:
                raise ValueError("Place this panel on a page first: its bubbles go in "
                                 "its frame")
            image = _show_project_page(root, manifest, page_id)
            existing = lettering.find_line_bubble(image, panel_id, line)
            if existing is not None:  # Select bubble
                image.set_selected_layers([existing])
                Gimp.displays_flush()
                _refresh_project_docks()
                return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
            if line.startswith("sfx"):
                speaker, text, kind = "SFX", panel.get("sfx", [])[int(line[3:])], "sfx"
            else:
                entry = panel.get("dialogue", [])[int(line)]
                speaker, text, kind = (entry.get("speaker", ""), entry.get("text", ""),
                                       entry.get("kind", "speech"))
            chosen = lettering.choose_bubble(
                bubble_templates.KIND_CATEGORY.get(kind, "speech"), text, _bubble_previews(),
                title=f"Bubble for {speaker}", size=lettering.default_size(image))
            if chosen is None:
                return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
            template, text, vertical, size = chosen
            frame = (panel.get("placement") or {}).get("frame") or [
                0, 0, image.get_width(), image.get_height()]
            center = _next_bubble_center(image, panel_id, frame,
                                         manifest["project"].get("reading_order", "rtl"))
            # A flipped library variant is an explicit top-tail choice; keep its
            # orientation instead of auto-aiming it toward a speaker below.
            tip = (_bubble_tail_tip(image, speaker, center, frame)
                   if template.tail != "none" and not template.flipped else None)
            lettering.insert_bubble(image, template, text, center=center, tail_tip=tip,
                                    size=size, vertical=vertical,
                                    source={"panel": panel_id, "line": line},
                                    name=f"{speaker}: {text}"[:60])
        _refresh_project_docks()
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


_CANVAS_SELECTION = {"key": None}


def _watch_canvas_selection():
    """Refresh Context when the canvas selection moves onto or off a bubble."""
    try:
        image = Gimp.context_get_image()
        key = None
        if image is not None and image.is_valid():
            bubble = _selected_bubble(image)
            key = bubble.get_id() if bubble is not None else None
        if key != _CANVAS_SELECTION["key"]:
            _CANVAS_SELECTION["key"] = key
            if _DOCK_CONTEXT.get("root") is not None:
                _refresh_project_docks()
    except Exception:
        pass
    return GLib.SOURCE_CONTINUE


def _dock_field_edit(procedure, config, data):
    """A Context field was edited: write it to the manifest, then redraw the docks."""
    try:
        key, _, value = config.get_property("item").partition("\t")
        if key.startswith("bubble:"):  # a bubble's text: set it, refit the bubble
            group = Gimp.Item.get_by_id(int(key.split(":", 1)[1].split(".", 1)[0]))
            if group is None or not group.is_valid():
                raise ValueError("That bubble is no longer on the page")
            _shape, text = lettering.bubble_parts(group)
            text.set_text(value)
            lettering.fit_bubble(group.get_image(), group)
            _refresh_project_docks()
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
        root = _DOCK_CONTEXT["root"]
        manifest = load_project(root)
        row_id = apply_field_edit(manifest, key, value, character_row_id, location_row_id,
                                  prop_row_id)
        save_project(root, manifest)
        prop = next((p for p in manifest.get("props", [])
                     if prop_row_id(p["name"]) == row_id), None)
        if prop is not None and key.endswith(".notes"):
            _send_prop_description(root, manifest, prop)
        _refresh_project_docks()
    except Exception as exc:
        return _error(procedure, f"Could not save that change: {exc}")
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


_LAST_PROJECT_CLICK = {"item": None, "ended": 0.0}
_DOUBLE_CLICK_SECONDS = 0.6  # click handlers take a while, so the gap runs from the end


def _project_double_click(item):
    """True when this Project tree click repeats the last one: the tree activates on a
    single click, so a double-click shows as the same item twice in quick succession."""
    last = _LAST_PROJECT_CLICK
    return (last["item"] == item
            and time.monotonic() - last["ended"] < _DOUBLE_CLICK_SECONDS)


def _dock_item_action(procedure, config, data):
    try:
        item = config.get_property("item")
        double = data in (DOCK_PROJECT, DOCK_SCRIPT) and _project_double_click(item)
        manifest = load_project(_DOCK_CONTEXT["root"])
        valid = {p["id"] for p in manifest["panels"]}
        valid.update(page["id"] for page in manifest["pages"])
        valid.update(character_row_id(c["name"]) for c in manifest["cast"])
        valid.update(location_row_id(loc["name"]) for loc in manifest.get("locations", []))
        valid.update(prop_row_id(p["name"]) for p in manifest.get("props", []))
        if item not in valid:
            raise ValueError(f"Unknown project item id: {item}")
        if data == DOCK_SCRIPT:
            panel = next((candidate for candidate in manifest["panels"]
                          if candidate["id"] == item), None)
            if panel and panel.get("status") == "orphaned":
                _DOCK_CONTEXT["orphan_id"] = item
            elif panel:
                _DOCK_CONTEXT["candidate_id"] = item
        _DOCK_CONTEXT["selected_id"] = item
        _refresh_project_docks()
        if any(page["id"] == item for page in manifest["pages"]):
            _show_project_page(_DOCK_CONTEXT["root"], manifest, item)
        else:
            panel = next((candidate for candidate in manifest["panels"]
                          if candidate["id"] == item), None)
            if panel is not None:
                _focus_panel_on_canvas(_DOCK_CONTEXT["root"], manifest, panel)
        if double:  # double-click: bring Context forward to edit the item
            try:
                _dock_pdb_call("gimp-extension-panel-show", {"identifier": DOCK_INSPECTOR})
            except Exception:
                pass
        if data in (DOCK_PROJECT, DOCK_SCRIPT):
            _LAST_PROJECT_CLICK.update(item=None if double else item,
                                       ended=time.monotonic())
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
    except Exception as exc:
        return _error(procedure, str(exc))


_DOCK_BUSY = []  # the dock callback running now (at most one)


class _SavedConfig:
    """A dock call's arguments, kept to run it later (its config is GIMP's to free)."""

    def __init__(self, values):
        self._values = values

    def get_property(self, name):
        return self._values.get(name)


def _exclusive(tick):
    """A GLib timer callback that makes GIMP calls, run under the same one-at-a-time
    rule as the dock callbacks: while it waits on GIMP, a dock click arrives inside it
    (the selection watcher's image check, then a click's dock redraw: a segfault in
    _gimp_gp_params_to_value_array). Busy: skip this tick and try again on the next."""
    def run():
        if _DOCK_BUSY:
            return GLib.SOURCE_CONTINUE
        _DOCK_BUSY.append(tick)
        try:
            return tick()
        finally:
            _DOCK_BUSY.clear()
    return run


def _one_at_a_time(callback, takes_item):
    """Never run a dock callback inside another. While one waits on a GIMP call
    (drawing a page thumbnail, say), libgimp delivers the next dock click right
    there, and a GIMP call from that nested one crashes libgimp (a segfault in
    gimp_value_array_new_from_types: Design character while a field edit redrew the
    docks). A call that arrives meanwhile runs as soon as the current one is done."""
    def run(procedure, config, data):
        if not _DOCK_BUSY:
            _DOCK_BUSY.append(callback)
            try:
                return callback(procedure, config, data)
            finally:
                _DOCK_BUSY.clear()
        saved = _SavedConfig({"item": config.get_property("item")} if takes_item else {})

        def later():
            if _DOCK_BUSY:  # e.g. a dialog of the first is still open
                return GLib.SOURCE_CONTINUE
            run(procedure, saved, data)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(50, later)
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

    return run


def _add_dock_callbacks(plugin):
    callbacks = [
        (DOCK_ACTIONS[DOCK_PROJECT], _dock_action, "project-action", False),
        (DOCK_OPEN_PROJECT, _dock_action, "open-project", False),
        (DOCK_CLOSE_PROJECT, _dock_action, "close-project", True),  # also a menu
        (DOCK_RELOAD_SCRIPT, _dock_action, "reload-script", True),
        (DOCK_LOAD_SCRIPT, _dock_action, "load-script", True),
        (DOCK_NEW_MANUAL_PROJECT, _dock_action, "new-manual-project", False),
        (DOCK_ACTIONS[DOCK_INSPECTOR], _dock_action, "generate", False),
        (DOCK_ITEMS[DOCK_INSPECTOR], _dock_field_edit, "field", True),
        (DOCK_OPEN_PAGE, _dock_action, "open-page", False),
        (DOCK_NEW_PROJECT, _dock_action, "new-project", False),
        (DOCK_SETUP_MODELS, _dock_action, "setup-models", False),
        (DOCK_RENDER_ENGINE, _dock_action, "render-engine", False),
        (DOCK_DESIGN_CHARACTER, _dock_action, "design-character", False),
        (DOCK_NEW_CHARACTER, _dock_character_menu, "new", True),
        (DOCK_DESIGN_CHARACTER_ITEM, _dock_character_menu, "design", True),
        (DOCK_DELETE_CHARACTER, _dock_character_menu, "delete", True),
        (DOCK_DESIGN_LOCATION, _dock_action, "design-location", False),
        (DOCK_OPEN_LOCATION_IMAGE, _dock_action, "open-location-image", False),
        (DOCK_OPEN_CHARACTER_IMAGE, _dock_action, "open-character-image", False),
        (DOCK_NEW_LOCATION, _dock_location_menu, "new", True),
        (DOCK_DESIGN_LOCATION_ITEM, _dock_location_menu, "design", True),
        (DOCK_DELETE_LOCATION, _dock_location_menu, "delete", True),
        (DOCK_DELETE_PAGE, _dock_page_menu, "delete", True),
        (DOCK_ADD_PANEL, _dock_panel_menu, "add", True),
        (DOCK_DELETE_PANEL, _dock_panel_menu, "delete", True),
        (DOCK_DUPLICATE_PANEL, _dock_panel_menu, "duplicate", True),
        (DOCK_ADD_COVER, _dock_panel_menu, "cover", True),
        (DOCK_CHARACTER_VERSION, _dock_character_version, "pick", True),
        (DOCK_ACTIVATE_TAKE, _dock_activate_take, "take", True),
        (DOCK_GALLERY_ITEM, _dock_gallery_item, "gallery", True),
        (DOCK_CANCEL_JOB, _dock_cancel_job, "cancel", True),
        (DOCK_RENAME_CHARACTER_BUTTON, _dock_action, "rename-character", False),
        (DOCK_DELETE_CHARACTER_BUTTON, _dock_action, "delete-character", False),
        (DOCK_VARIANT_BUTTON, _dock_action, "another-reference", False),
        (DOCK_DELETE_PROP_BUTTON, _dock_action, "delete-prop", False),
        (DOCK_DELETE_LOCATION_BUTTON, _dock_action, "delete-location", False),
        (DOCK_MANAGE_REFERENCE, _dock_manage_reference, "manage", True),
        (DOCK_DESIGN_PROP, _dock_action, "design-prop", False),
        (DOCK_OPEN_PROP_IMAGE, _dock_action, "open-prop-image", False),
        (DOCK_NEW_PROP, _dock_prop_menu, "new", True),
        (DOCK_DESIGN_PROP_ITEM, _dock_prop_menu, "design", True),
        (DOCK_DELETE_PROP, _dock_prop_menu, "delete", True),
        (DOCK_PROP_IMAGE_DEFAULT, _dock_prop_image_menu, "default", True),
        (DOCK_PROP_IMAGE_DELETE, _dock_prop_image_menu, "delete", True),
        (DOCK_REF_DEFAULT, _dock_reference_menu, "default", True),
        (DOCK_REF_RENAME, _dock_reference_menu, "rename", True),
        (DOCK_REF_DELETE, _dock_reference_menu, "delete", True),
        (DOCK_DESIGN_VARIANT, _dock_character_menu, "variant", True),
        (DOCK_ADD_COVER_PAGE, _dock_page_menu, "cover", True),
        (DOCK_OPEN_CHARACTER_VERSION, _dock_open_character_version, "open", True),
        (DOCK_REORDER_PAGES, _dock_page_menu, "reorder", True),
        (DOCK_BUBBLE_LINE, _dock_bubble, "line", True),
        (DOCK_BUBBLE_ITEM, _dock_bubble, "template", True),
        (DOCK_NEW_BUBBLE, _dock_bubble, "new", False),
        (DOCK_FIT_BUBBLE, _dock_bubble, "fit", False),
        (DOCK_GENERATE_LAYOUT, _dock_action, "generate-layout", False),
        (DOCK_STORYBOARD, _dock_action, "storyboard-review", False),
        (DOCK_GENERATE_PAGE, _dock_action, "generate-page", False),
        # Page strip: clicking a page opens it; the button adds one (as in Project)
        (DOCK_ACTIONS[DOCK_FILMSTRIP], _dock_action, "project-action", False),
        (DOCK_ITEMS[DOCK_PROJECT], _dock_item_action, DOCK_PROJECT, True),
        (DOCK_ITEMS[DOCK_FILMSTRIP], _dock_item_action, DOCK_FILMSTRIP, True),
        (DOCK_ACTIONS[DOCK_SCRIPT], _dock_action, "match-panel", False),
        (DOCK_ITEMS[DOCK_SCRIPT], _dock_item_action, DOCK_SCRIPT, True),
        (DOCK_ACTIONS[DOCK_CHARACTERS], _dock_action, "refresh", False),
        (DOCK_ITEMS[DOCK_CHARACTERS], _dock_item_action, DOCK_CHARACTERS, True),
        (DOCK_ACTIONS[DOCK_PANEL], _dock_action, "set-panel-frame", False),
    ]
    for name, callback, data, takes_item in callbacks:
        procedure = Gimp.Procedure.new(plugin, name, Gimp.PDBProcType.TEMPORARY,
                                       _one_at_a_time(callback, takes_item), data)
        if takes_item:
            procedure.add_string_argument("item", "Item id", "Stable project row id",
                                          "", GObject.ParamFlags.READWRITE)
        procedure.set_documentation("Handle an Imanganation dock action",
                                    "Called by the host-rendered Imanganation docks.", name)
        procedure.set_attribution("imanganation", "imanganation", "2026")
        plugin.add_temp_procedure(procedure)


def _register_project_docks(plugin):
    root = _DOCK_CONTEXT.get("root")
    if root is None:
        contents = (build_welcome_docks(DOCK_NEW_MANUAL_PROJECT, DOCK_NEW_PROJECT)
                    if build_welcome_docks is not None else {
            "project": "# Imanganation\nChoose a project folder to open your workspace.",
            "inspector": "# Workspace\nOpen or create a project to get started.",
            "filmstrip": "# Pages\nOpen a project to see its pages.",
            "script": "# Script\nOpen a project to see its reading order.",
            "characters": "# Character Bible\nOpen a project to see its cast.",
            "panel": "# Panel\nSelect a panel to see its production brief.",
        })
        contents.setdefault("gallery", "# Gallery\nOpen a project to see its pictures.")
        rows = [
            (DOCK_PROJECT, "Project", "tree", contents["project"], "",
             "Open project…", DOCK_OPEN_PROJECT, ""),
            (DOCK_INSPECTOR, "Context", "properties", contents["inspector"],
             "", "", "", ""),
            (DOCK_FILMSTRIP, "Pages", "strip", contents["filmstrip"],
             "", "", "", ""),
            (DOCK_SCRIPT, "Script", "list", contents["script"],
             "", "", "", ""),
            (DOCK_CHARACTERS, "Character Bible", "tree", contents["characters"],
             "", "", "", ""),
            (DOCK_PANEL, "Panel", "properties", contents["panel"],
             "", "", "", ""),
            (DOCK_BUBBLES, "Bubbles", "tiles", _bubble_library(), "", "", "",
             DOCK_BUBBLE_ITEM),
            (DOCK_GALLERY, "Gallery", "tiles", contents["gallery"], "", "", "",
             DOCK_GALLERY_ITEM),
        ]
    else:
        manifest = load_project(root)
        contents = build_docks(
            manifest, _DOCK_CONTEXT.get("selected_id"), root,
            _project_page_thumbnails(root, manifest), DOCK_OPEN_PAGE,
            DOCK_GENERATE_LAYOUT, storyboard_action=DOCK_STORYBOARD,
            **_dock_actions(root, manifest))
        contents["gallery"] = _gallery_content(root, manifest, contents["selected_id"])
        rows = [
            (DOCK_PROJECT, "Project", "tree", contents["project"],
             contents["project_selected"], "Add page", DOCK_ACTIONS[DOCK_PROJECT],
             DOCK_ITEMS[DOCK_PROJECT]),
            (DOCK_INSPECTOR, "Context", "properties", contents["inspector"],
             "", "Generate panel", DOCK_ACTIONS[DOCK_INSPECTOR], DOCK_ITEMS[DOCK_INSPECTOR]),
            (DOCK_FILMSTRIP, "Pages", "strip", contents["filmstrip"],
             contents["filmstrip_selected"], "Add page", DOCK_ACTIONS[DOCK_FILMSTRIP],
             DOCK_ITEMS[DOCK_FILMSTRIP]),
            (DOCK_SCRIPT, "Script", "list", contents["script"],
             contents["script_selected"], "Match selected", DOCK_ACTIONS[DOCK_SCRIPT],
             DOCK_ITEMS[DOCK_SCRIPT]),
            (DOCK_CHARACTERS, "Character Bible", "tree", contents["characters"],
             contents["character_selected"], "Refresh", DOCK_ACTIONS[DOCK_CHARACTERS],
             DOCK_ITEMS[DOCK_CHARACTERS]),
            (DOCK_PANEL, "Panel", "properties", contents["panel"],
             "", "Set frame", DOCK_ACTIONS[DOCK_PANEL], ""),
            (DOCK_BUBBLES, "Bubbles", "tiles", _bubble_library(), "", "", "",
             DOCK_BUBBLE_ITEM),
            (DOCK_GALLERY, "Gallery", "tiles", contents["gallery"], "", "", "",
             DOCK_GALLERY_ITEM),
        ]
    for identifier, title, presentation, content, selected, action_label, action, item in rows:
        _dock_pdb_call("gimp-extension-panel-register", {
            "identifier": identifier,
            "title": title,
            "icon-name": DOCK_ICON_NAMES[identifier],
            "content": content,
            "presentation": presentation,
            "selected-item": selected,
            "action-label": action_label,
            "action-procedure": action,
            "item-action-procedure": item,
        })
    if root is not None:
        _refresh_project_docks()
        return
    # Registering a dock that is already shown only stores its text; an update redraws
    # it (Close Project returns open docks to the welcome workspace this way).
    for identifier, _title, _presentation, content, selected, *_rest in rows:
        _dock_pdb_call("gimp-extension-panel-update", {
            "identifier": identifier, "content": content, "selected-item": selected})


def _project_docks_run(procedure, config, data):
    if load_project is None or build_docks is None:
        return _error(procedure, "The plug-in install is missing project_store.py or panel_ui.py")
    folder = config.get_property("project-dir")
    if config.get_property("run-mode") == Gimp.RunMode.INTERACTIVE:
        gi.require_version("GimpUi", "3.0")
        from gi.repository import GimpUi

        GimpUi.init(PROC_PROJECT_DOCKS)
        root = _choose_project_folder()  # starts in the last project
        if root is None:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
    elif folder is None:
        return _error(procedure, "Choose a project folder containing project.json")
    else:
        root = Path(folder.get_path())
    try:
        load_project(root)
        _remember_project(root)
        _dock_pdb_call(DOCK_OPEN_PROJECT, {})
    except Exception as exc:
        return _error(procedure, str(exc))
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _restart_workspace_run(procedure, config, data):
    """Windows > Imanganation > Restart Workspace: after the workspace process has
    died (GIMP says the plug-in crashed and its docks vanish), start it again, which
    starts the engine too, and show every dock. If it is running, just show the docks."""
    pdb = Gimp.get_pdb()
    if pdb.lookup_procedure(DOCK_ACTIONS[DOCK_PROJECT]) is None:  # its docks' callbacks
        workspace = pdb.lookup_procedure(PROC_AUTOSTART)
        if workspace is None:
            return _error(procedure, "The Imanganation workspace isn't installed")
        # Returns once the workspace has registered its docks (persistent_ready)
        status = workspace.run(workspace.create_config()).index(0)
        if status != Gimp.PDBStatusType.SUCCESS:
            return _error(procedure, "The Imanganation workspace did not start; see "
                                     f"{Path(Gimp.directory()) / 'imanganation'}"
                                     "/workspace.log")
    for dock in DOCK_IDS:
        try:
            _dock_pdb_call("gimp-extension-panel-show", {"identifier": dock})
        except Exception:
            pass  # a dock the workspace did not register (an older install)
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _show_dock_run(procedure, config, dock):
    """Reopen (or bring forward) one workspace dock registered by the extension."""
    try:
        _dock_pdb_call("gimp-extension-panel-show", {"identifier": dock})
    except Exception:
        return _error(procedure, "The Imanganation workspace is not running; restart GIMP "
                                 "to start it")
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


_CRASH_LOG = None  # kept open: faulthandler writes to it when the process dies


def _start_crash_log():
    """The workspace runs for the whole GIMP session; when it dies GIMP only says
    "plug-in crashed". Record why in <GIMP profile>/imanganation/workspace.log: a fatal
    signal's Python stack (faulthandler) and any uncaught exception."""
    global _CRASH_LOG
    import faulthandler
    import traceback

    try:
        path = Path(Gimp.directory()) / "imanganation" / "workspace.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > 1_000_000:
            path.replace(path.with_suffix(".log.old"))
        _CRASH_LOG = path.open("a", encoding="utf-8", buffering=1)
        _CRASH_LOG.write(f"\n--- workspace started {datetime.now().isoformat(timespec='seconds')}"
                         f" (pid {os.getpid()})\n")
        faulthandler.enable(file=_CRASH_LOG, all_threads=True)

        def log_uncaught(kind, value, tb):
            _CRASH_LOG.write("".join(traceback.format_exception(kind, value, tb)))
            sys.__excepthook__(kind, value, tb)

        sys.excepthook = log_uncaught
    except OSError:
        pass  # no log is no reason not to start


def _autostart_run(procedure, config, data):
    """Install the default workspace before GIMP restores its dock layout."""
    _start_crash_log()
    try:
        root = _open_project()
        if root is not None:
            _adopt_fingerprints(root)
            manifest = load_project(root)
            selected = manifest.get("cursor", {}).get("next_panel")
            if not selected:
                selected = next((panel["id"] for panel in manifest["panels"]), None)
            if not selected and manifest["pages"]:
                selected = manifest["pages"][0]["id"]
            _DOCK_CONTEXT.update(root=root, selected_id=selected,
                                 orphan_id=None, candidate_id=None)
        else:
            _DOCK_CONTEXT.update(root=None, selected_id=None,
                                 orphan_id=None, candidate_id=None)
        _add_dock_callbacks(_DOCK_PLUGIN)
        _start_engine_services()
        _prompt_setup_when_engine_up(time.monotonic() + 180)
        GLib.timeout_add(1000, _exclusive(_watch_canvas_selection))
        _register_project_docks(_DOCK_PLUGIN)
    except Exception as exc:
        Gimp.message(f"Could not start Imanganation workspace: {exc}")
    procedure.persistent_ready()
    _DOCK_PLUGIN.persistent_enable()
    GLib.MainLoop().run()
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


class Imanganation(Gimp.PlugIn):
    def do_set_i18n(self, procname):
        return False, None, None

    def do_query_procedures(self):
        return [PROC_RENDER, PROC_NEXT, PROC_REGEN, PROC_INPAINT, PROC_STAGE,
                PROC_REFINE, PROC_SETREF,
                PROC_SET_LOCATION_REF, PROC_SET_PROP_REF,
                PROC_PLACE, PROC_STATUS, PROC_PROJECT_DOCKS, PROC_CLOSE_PROJECT,
                PROC_RELOAD_SCRIPT,
                PROC_PAGE_LAYOUT, PROC_SCREENTONE, PROC_SPEED_LINES, PROC_IMPACT_BURST,
                PROC_COVER_DESIGNER, PROC_EXPORT_PROJECT,
                PROC_AUTOSTART,
                PROC_NEW_PROJECT_MANUAL, PROC_NEW_PROJECT,
                PROC_SETUP_MODELS, PROC_RENDER_ENGINE,
                PROC_RESTART_WORKSPACE, *DOCK_SHOW.values()]

    def do_create_procedure(self, name):
        global _DOCK_PLUGIN
        _DOCK_PLUGIN = self
        if name == PROC_AUTOSTART:
            proc = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PERSISTENT, _autostart_run, None)
            proc.set_documentation(
                "Start the Imanganation workspace",
                "Registers the Imanganation project docks at GIMP startup.", name)
            proc.set_attribution("imanganation", "imanganation", "2026")
            return proc
        if name == PROC_RESTART_WORKSPACE:
            proc = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, _restart_workspace_run, None)
            proc.set_menu_label("_Restart Workspace")
            proc.add_enum_argument("run-mode", "Run mode", "How to run the procedure",
                                   Gimp.RunMode, Gimp.RunMode.INTERACTIVE,
                                   GObject.ParamFlags.READWRITE)
            proc.add_menu_path("<Image>/Windows/Imanganation")
            proc.set_documentation(
                "Restart the Imanganation workspace",
                "Start the workspace (its docks and the engine) again after it stopped, "
                "and show every Imanganation dock.", name)
            proc.set_attribution("imanganation", "imanganation", "2026")
            return proc
        dock = next((d for d, proc_name in DOCK_SHOW.items() if proc_name == name), None)
        if dock is not None:
            proc = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, _show_dock_run, dock)
            proc.set_menu_label(DOCK_MENU_LABELS[dock])
            proc.add_enum_argument("run-mode", "Run mode", "How to run the procedure",
                                   Gimp.RunMode, Gimp.RunMode.INTERACTIVE,
                                   GObject.ParamFlags.READWRITE)
            proc.add_menu_path("<Image>/Windows/Imanganation")
            proc.set_documentation(
                "Show an Imanganation dock",
                "Reopen a closed Imanganation workspace dock, or bring it forward.", name)
            proc.set_attribution("imanganation", "imanganation", "2026")
            return proc
        if name == PROC_NEW_PROJECT_MANUAL:
            proc = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, _new_manual_project_run, None)
            proc.set_menu_label("New _Project...")
            proc.add_enum_argument("run-mode", "Run mode", "How to run the procedure",
                                   Gimp.RunMode, Gimp.RunMode.INTERACTIVE,
                                   GObject.ParamFlags.READWRITE)
            proc.add_menu_path("<Toolbox>/File/Imanganation")
            proc.add_menu_path("<Image>/File/Imanganation")
            proc.add_menu_path("<Image>/Imanganation/Project")
            proc.set_documentation(
                "Create an Imanganation project manually",
                "Set up a project with guided defaults and starter panels, without a script.",
                name)
            proc.set_attribution("imanganation", "imanganation", "2026")
            return proc
        if name == PROC_NEW_PROJECT:
            proc = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, _new_project_run, None)
            proc.set_menu_label("_New Project from Script...")
            proc.add_enum_argument("run-mode", "Run mode", "How to run the procedure",
                                   Gimp.RunMode, Gimp.RunMode.INTERACTIVE,
                                   GObject.ParamFlags.READWRITE)
            # Keep project actions together at the top of File, before GIMP's
            # standard Open section.
            proc.add_menu_path("<Toolbox>/File/Imanganation")
            proc.add_menu_path("<Image>/File/Imanganation")
            proc.add_menu_path("<Image>/Imanganation/Project")
            proc.set_documentation(
                "New Imanganation project from a script",
                "Build a project (panels, pages, cast, locations) from a script and "
                "design its characters.", name)
            proc.set_attribution("imanganation", "imanganation", "2026")
            return proc
        if name == PROC_SETUP_MODELS:
            proc = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, _setup_models_run, None)
            proc.set_menu_label("Set Up _Models...")
            proc.add_enum_argument("run-mode", "Run mode", "How to run the procedure",
                                   Gimp.RunMode, Gimp.RunMode.INTERACTIVE,
                                   GObject.ParamFlags.READWRITE)
            proc.add_menu_path("<Image>/Imanganation/Settings")
            proc.set_documentation(
                "Download or link Imanganation's AI models",
                "Show which model files the engine needs, their licences and state; "
                "download the missing ones or link files you already have.", name)
            proc.set_attribution("imanganation", "imanganation", "2026")
            return proc
        if name == PROC_RENDER_ENGINE:
            proc = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, _render_engine_run, None)
            proc.set_menu_label("Render _Engine...")
            proc.add_enum_argument("run-mode", "Run mode", "How to run the procedure",
                                   Gimp.RunMode, Gimp.RunMode.INTERACTIVE,
                                   GObject.ParamFlags.READWRITE)
            proc.add_menu_path("<Image>/Imanganation/Settings")
            proc.set_documentation(
                "Choose the open project's render engine",
                "Pick the engine that draws this project's panels (each with its licence), "
                "turn the face pass on or off, install what's missing, and design the "
                "script's locations.", name)
            proc.set_attribution("imanganation", "imanganation", "2026")
            return proc
        if name == PROC_RELOAD_SCRIPT:
            proc = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, _close_project_run,
                DOCK_RELOAD_SCRIPT)
            proc.set_menu_label("_Reload Imanganation Script")
            proc.add_enum_argument("run-mode", "Run mode", "How to run the procedure",
                                   Gimp.RunMode, Gimp.RunMode.INTERACTIVE,
                                   GObject.ParamFlags.READWRITE)
            proc.add_menu_path("<Toolbox>/File/Imanganation")
            proc.add_menu_path("<Image>/File/Imanganation")
            proc.add_menu_path("<Image>/Imanganation/Project")
            proc.set_documentation(
                "Read the open project's script again",
                "After editing the project's script: unchanged panels keep their takes, "
                "placement and edits; edited ones with work go to Needs matching.", name)
            proc.set_attribution("imanganation", "imanganation", "2026")
            return proc

        if name == PROC_CLOSE_PROJECT:
            proc = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, _close_project_run,
                DOCK_CLOSE_PROJECT)
            proc.set_menu_label("_Close Imanganation Project")
            proc.add_enum_argument("run-mode", "Run mode", "How to run the procedure",
                                   Gimp.RunMode, Gimp.RunMode.INTERACTIVE,
                                   GObject.ParamFlags.READWRITE)
            proc.add_menu_path("<Toolbox>/File/Imanganation")
            proc.add_menu_path("<Image>/File/Imanganation")
            proc.add_menu_path("<Image>/Imanganation/Project")
            proc.set_documentation(
                "Close the open Imanganation project",
                "Offer to save its pages with unsaved changes, close the pages the "
                "workspace opened, and return the docks to the welcome workspace. The "
                "project is not reopened at the next start.", name)
            proc.set_attribution("imanganation", "imanganation", "2026")
            return proc

        if name == PROC_PROJECT_DOCKS:
            proc = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, _project_docks_run, None)
            proc.set_menu_label("Open / Switch _Project...")
            proc.add_enum_argument("run-mode", "Run mode", "How to run the procedure",
                                   Gimp.RunMode, Gimp.RunMode.INTERACTIVE,
                                   GObject.ParamFlags.READWRITE)
            # The toolbox File menu is available before an image is open, which
            # is when users most need to open an existing project. Keep the
            # image-window entry as well for switching projects while editing.
            proc.add_menu_path("<Toolbox>/File/Imanganation")
            proc.add_menu_path("<Image>/File/Imanganation")
            proc.add_menu_path("<Image>/Imanganation/Project")
            proc.set_documentation(
                "Open the Imanganation project docks",
                "Open or switch the project shown in the startup Imanganation workspace.", name)
            proc.add_file_argument(
                "project-dir", "_Project folder", "Folder containing project.json",
                Gimp.FileChooserAction.SELECT_FOLDER, False, None,
                GObject.ParamFlags.READWRITE)
            proc.set_attribution("imanganation", "imanganation", "2026")
            return proc

        run = {PROC_RENDER: render_panel, PROC_NEXT: place_next_panel,
               PROC_REFINE: refine_panel, PROC_REGEN: regenerate_panel,
               PROC_SETREF: set_character_reference, PROC_INPAINT: inpaint_selection,
               PROC_SET_LOCATION_REF: set_location_reference,
               PROC_SET_PROP_REF: set_prop_reference,
               PROC_PLACE: place_panel, PROC_STATUS: engine_status,
               PROC_PAGE_LAYOUT: page_layout, PROC_SCREENTONE: screentone,
               PROC_SPEED_LINES: speed_lines, PROC_IMPACT_BURST: impact_burst,
               PROC_COVER_DESIGNER: cover_designer,
               PROC_EXPORT_PROJECT: export_project,
               PROC_STAGE: develop_panel_stage}[name]
        proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, run, None)
        proc.set_image_types("*")
        proc.set_sensitivity_mask(
            Gimp.ProcedureSensitivityMask.DRAWABLE
            | Gimp.ProcedureSensitivityMask.DRAWABLES
            | Gimp.ProcedureSensitivityMask.NO_DRAWABLES)
        # GIMP 3.3 requires a menu label before a menu path is added. Each
        # procedure below replaces this fallback with its user-facing label.
        proc.set_menu_label("Imanganation")
        menu_groups = {
            PROC_STATUS: "Settings",
            PROC_PAGE_LAYOUT: "Page & Cover",
            PROC_COVER_DESIGNER: "Page & Cover",
            PROC_EXPORT_PROJECT: "Project",
            PROC_SCREENTONE: "Manga Tools",
            PROC_SPEED_LINES: "Manga Tools",
            PROC_IMPACT_BURST: "Manga Tools",
            PROC_SETREF: "Project",
            PROC_SET_LOCATION_REF: "Project",
            PROC_SET_PROP_REF: "Project",
            PROC_RENDER: "Create",
            PROC_NEXT: "Create",
            PROC_PLACE: "Create",
            PROC_REGEN: "Edit Panel",
            PROC_INPAINT: "Edit Panel",
            PROC_STAGE: "Edit Panel",
            PROC_REFINE: "Edit Panel",
        }
        proc.add_menu_path(f"<Image>/Imanganation/{menu_groups[name]}")
        proc.set_attribution("imanganation", "imanganation", "2026")
        if name == PROC_STATUS:
            proc.set_menu_label("Engine _Status...")
            proc.set_documentation(
                "Show the imanganation engine's status",
                "Engine and ComfyUI health (GPU, VRAM), running/queued/recent jobs with "
                "errors, missing model files, and this image's project progress.",
                name)
            proc.add_string_argument(
                "engine-url", "_Engine URL", "imanganation engine (manganation serve)",
                ENGINE_URL, GObject.ParamFlags.READWRITE)
            proc.add_string_return_value(
                "report", "Report", "The status report text", "",
                GObject.ParamFlags.READWRITE)
            return proc

        if name == PROC_PAGE_LAYOUT:
            proc.add_file_argument(
                "project-dir", "_Project folder", "Imanganation project containing this page",
                Gimp.FileChooserAction.SELECT_FOLDER, True, None,
                GObject.ParamFlags.READWRITE)
            proc.add_layer_return_value(
                "layer", "Template layer", "The generated page layout layer", False,
                GObject.ParamFlags.READWRITE)
            proc.set_menu_label("Generate Page _Panel Layout...")
            proc.set_documentation(
                "Generate a page panel layout or a cover composition guide",
                "Choose one selectable frame per script panel for a numbered page, or "
                "colored title, artwork, credits, and safe-area guides for pages named "
                "Cover, Front Cover, or Back Cover. Overlapping panels are layered above "
                "the base frame template.",
                name)
            return proc

        if name == PROC_SCREENTONE:
            proc.set_menu_label("Create Screen_tone...")
            proc.set_documentation(
                "Fill the current selection with manga screentone",
                "Create a separate black halftone layer clipped to the current selection. "
                "Choose dot spacing, coverage and screen angle; the pattern is also kept "
                "as an editable path.", name)
            return proc

        if name == PROC_SPEED_LINES:
            proc.add_layer_return_value(
                "layer", "Speed lines layer", "The generated speed-line layer", False,
                GObject.ParamFlags.READWRITE)
            proc.set_menu_label("Create Speed _Lines...")
            proc.set_documentation(
                "Create manga speed lines inside the current selection",
                "Generate editable radial linework around a focal point, clipped to the "
                "current selection and added on a separate layer.", name)
            return proc

        if name == PROC_IMPACT_BURST:
            proc.add_layer_return_value(
                "layer", "Impact burst layer", "The generated impact burst layer", False,
                GObject.ParamFlags.READWRITE)
            proc.set_menu_label("Create Impact _Burst...")
            proc.set_documentation(
                "Create a radial manga impact burst inside the current selection",
                "Generate editable burst wedges around a chosen center, clipped to the "
                "current selection and added on a separate layer.", name)
            return proc

        if name == PROC_COVER_DESIGNER:
            proc.add_layer_return_value(
                "layer", "Cover typography group", "The editable cover text layers", False,
                GObject.ParamFlags.READWRITE)
            proc.set_menu_label("Design Front _Cover...")
            proc.set_documentation(
                "Add editable front-cover typography",
                "Choose a cover template and create editable title, subtitle and creator "
                "credit layers over the current cover artwork, with optional layout guides.",
                name)
            return proc

        if name == PROC_EXPORT_PROJECT:
            proc.add_file_argument(
                "project-dir", "_Project folder", "Imanganation project to export",
                Gimp.FileChooserAction.SELECT_FOLDER, True, None,
                GObject.ParamFlags.READWRITE)
            proc.set_menu_label("Export _Project...")
            proc.set_documentation(
                "Export an Imanganation project for comic distribution",
                "Export the ordered project pages as a CBZ archive, PDF volume, page "
                "image folder, or sliced vertical comic episode. Choose reading direction, "
                "cover inclusion, image format, page order, and vertical export dimensions.", name)
            return proc

        proc.add_layer_return_value(
            "layer", "Layer", "The placed panel layer", False, GObject.ParamFlags.READWRITE)

        if name in (PROC_INPAINT, PROC_STAGE):
            if name == PROC_STAGE:
                proc.set_menu_label("Develop Panel in _Stages...")
                proc.set_documentation(
                    "Add a manual AI development stage to the selected panel",
                    "Follow the suggested manga-development phases from composition "
                    "and rough character blocking through background, acting, linework, "
                    "shadows, and effects. Select the area, describe the change, and add "
                    "the result as a separate editable layer. Choose another phase to "
                    "skip or revisit. Select a frame and a script panel to start from a blank picture, or develop "
                    "an existing render.", name)
            else:
                proc.set_menu_label("_Inpaint Selection...")
                proc.set_documentation(
                    "Repaint the selected area of a panel",
                    "Repaint only the selected area of the selected panel's take from a "
                    "short prompt; everything outside the selection stays exactly as it "
                    "was. The result is swapped in as a new take, the previous one kept "
                    "hidden.", name)
            proc.add_string_argument(
                "prompt", "_Prompt", "What to paint in the selection, e.g. \"open hand\"",
                "", GObject.ParamFlags.READWRITE)
            proc.add_double_argument(
                "denoise", "_Strength", "How much to repaint (0 = engine default 0.85)",
                0.0, 1.0, 0.0, GObject.ParamFlags.READWRITE)
            proc.add_int_argument(
                "grow", "_Blend (px)", "Grow the mask to blend the seam; -1 = engine "
                "default", -1, 256, -1, GObject.ParamFlags.READWRITE)
            proc.add_string_argument(
                "characters", "_Characters", "Who is in the selection, comma-separated "
                "(e.g. \"Yuki, Akira\"); keeps their faces on-model. Blank = none",
                "", GObject.ParamFlags.READWRITE)
            proc.add_string_argument(
                "engine-url", "_Engine URL", "imanganation engine (manganation serve)",
                ENGINE_URL, GObject.ParamFlags.READWRITE)
            return proc

        if name == PROC_SETREF:
            proc.set_menu_label("Set _Character Reference from Layer...")
            proc.set_documentation(
                "Use the selected layer as a character's reference",
                "Export the selected layer (or its part inside the selection) as a square "
                "image and register it as the character's new active reference. Earlier "
                "reference versions are kept.",
                name)
            proc.add_string_argument(
                "character", "_Character", "Character name (or alias) in the project",
                "", GObject.ParamFlags.READWRITE)
            proc.add_string_argument(
                "reference-name", "Reference _name", "What to call this image, e.g. "
                "summer or school-uniform (letters, digits, - and _). Panels pick a "
                "reference by name. Blank = gimp-01, gimp-02, ...",
                "", GObject.ParamFlags.READWRITE)
            proc.add_boolean_argument(
                "make-default", "Use for _all panels", "On: this becomes the character's "
                "default reference. Off: keep it as an extra reference that panels can "
                "choose (Context -> Reference...)", True, GObject.ParamFlags.READWRITE)
            proc.add_file_argument(
                "project-dir", "_Project folder", "Only needed if no placed panel is in "
                "this image", Gimp.FileChooserAction.SELECT_FOLDER, True, None,
                GObject.ParamFlags.READWRITE)
            proc.add_string_argument(
                "engine-url", "_Engine URL", "imanganation engine (manganation serve)",
                ENGINE_URL, GObject.ParamFlags.READWRITE)
            return proc

        if name == PROC_SET_LOCATION_REF:
            proc.set_menu_label("Set _Location Reference from Layer...")
            proc.set_documentation(
                "Use the selected layer as a location's reference",
                "Export the selected layer (or its part inside the selection), keeping "
                "its proportions, and register it as the location's reference for "
                "Qwen-Image renders. Earlier images are kept. On a reference image "
                "opened from Context, the project and location are filled in.",
                name)
            proc.add_string_argument(
                "location", "_Location", "Location name in the project (any time of day)",
                "", GObject.ParamFlags.READWRITE)
            proc.add_file_argument(
                "project-dir", "_Project folder", "Only needed if this image isn't from "
                "the project", Gimp.FileChooserAction.SELECT_FOLDER, True, None,
                GObject.ParamFlags.READWRITE)
            proc.add_string_argument(
                "engine-url", "_Engine URL", "imanganation engine (manganation serve)",
                ENGINE_URL, GObject.ParamFlags.READWRITE)
            return proc

        if name == PROC_SET_PROP_REF:
            proc.set_menu_label("Set Pr_op Reference from Layer...")
            proc.set_documentation(
                "Use the selected layer as a prop's reference",
                "Export the selected layer (or its part inside the selection), keeping "
                "its proportions, and register it as the prop's reference for "
                "Qwen-Image renders. Earlier images are kept. On a reference image "
                "opened from Context, the project and prop are filled in.",
                name)
            proc.add_string_argument(
                "prop", "_Prop", "Prop name in the project", "", GObject.ParamFlags.READWRITE)
            proc.add_file_argument(
                "project-dir", "_Project folder", "Only needed if this image isn't from "
                "the project", Gimp.FileChooserAction.SELECT_FOLDER, True, None,
                GObject.ParamFlags.READWRITE)
            proc.add_string_argument(
                "engine-url", "_Engine URL", "imanganation engine (manganation serve)",
                ENGINE_URL, GObject.ParamFlags.READWRITE)
            return proc

        if name == PROC_REGEN:
            proc.set_menu_label("Re_generate Panel...")
            proc.set_documentation(
                "Render a new take of the selected panel",
                "Re-render the selected panel from the current script at its frame's "
                "size, and swap the new take in at the same footprint and mask. The "
                "previous take is kept, hidden.",
                name)
            proc.add_boolean_argument(
                "same-seed", "Same _seed",
                "Reuse this take's seed (keep the composition, apply script edits); "
                "off = a new random take", False, GObject.ParamFlags.READWRITE)
            proc.add_boolean_argument(
                "keep-composition", "_Keep composition",
                "Keep this take's layout and poses (ControlNet on its edges) while the "
                "edited script changes details such as an expression or outfit",
                False, GObject.ParamFlags.READWRITE)
            proc.add_double_argument(
                "composition-strength", "Composition s_trength",
                "How strictly to keep the layout; 0 = engine default (settings.yaml)",
                0.0, 2.0, 0.0, GObject.ParamFlags.READWRITE)
            proc.add_string_argument(
                "engine-url", "_Engine URL", "imanganation engine (manganation serve)",
                ENGINE_URL, GObject.ParamFlags.READWRITE)
            return proc

        if name == PROC_REFINE:
            proc.set_menu_label("Re_fine Panel (Hi-res)...")
            proc.set_documentation(
                "Refine the selected panel at higher resolution",
                "Send the take the selected panel layer shows to the engine's hi-res "
                "fix (polish + Real-ESRGAN upscale) and swap the result in at the same "
                "size and mask, keeping the previous take hidden.",
                name)
            proc.add_double_argument(
                "scale", "_Scale", "Size multiplier; 0 = engine default (settings.yaml)",
                0.0, 8.0, 0.0, GObject.ParamFlags.READWRITE)
            proc.add_double_argument(
                "denoise", "_Polish", "Img2img polish strength; -1 = engine default, "
                "0 = pure upscale", -1.0, 1.0, -1.0, GObject.ParamFlags.READWRITE)
            proc.add_string_argument(
                "engine-url", "_Engine URL", "imanganation engine (manganation serve)",
                ENGINE_URL, GObject.ParamFlags.READWRITE)
            return proc

        if name == PROC_RENDER:
            proc.set_menu_label("_Render Panel into Frame...")
            proc.set_documentation(
                "Render the next imanganation panel into the selected frame",
                "Ask the engine to render the next script panel at the selected frame's "
                "proportions, then place it clipped to the frame, beneath the template.",
                name)
            _add_project_args(proc)
            proc.add_int_argument(
                "seed", "_Seed", "Fixed seed; -1 = the panel's own seed or random",
                -1, 2**31 - 1, -1, GObject.ParamFlags.READWRITE)
            proc.add_string_argument(
                "engine-url", "_Engine URL", "imanganation engine (manganation serve)",
                ENGINE_URL, GObject.ParamFlags.READWRITE)
            return proc

        if name == PROC_NEXT:
            proc.set_menu_label("Place _Next Panel...")
            proc.set_documentation(
                "Place the next imanganation panel",
                "Take the next already-rendered panel in project reading order and fit it "
                "into the selected frame. Dialogue is lettered "
                "afterwards with Bubble….",
                name)
            _add_project_args(proc)
            return proc

        proc.set_menu_label("Place Panel _Image...")
        proc.set_documentation(
            "Place an imanganation panel",
            "Load any rendered panel as a layer, fit it to the selection, and tag it "
            "with its PanelSpec.",
            name)
        proc.add_file_argument(
            "panel-file", "Panel _image", "Rendered panel PNG",
            Gimp.FileChooserAction.OPEN, False, None, GObject.ParamFlags.READWRITE)
        proc.add_string_argument(
            "panel-spec", "Panel _spec (JSON)", "PanelSpec JSON stored on the layer",
            "{}", GObject.ParamFlags.READWRITE)
        proc.add_string_argument(
            "engine-url", "_Engine URL", "imanganation engine health URL",
            ENGINE_URL + "/health", GObject.ParamFlags.READWRITE)
        return proc


Gimp.main(Imanganation.__gtype__, sys.argv)
