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
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import (  # noqa: E402
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
    import lettering
    import services as engine_services
    import setup_ui
    from layouts import frame_rings, layout_preview_rgb, page_layout_availability
    from panel_ui import _panel_label as panel_label
    from panel_ui import (
        build_docks,
        build_welcome_docks,
        character_row_id,
        location_row_id,
        rgb_png,
    )
    from project_store import (
        ProjectFileError,
        apply_field_edit,
        delete_character,
        delete_location,
        delete_page,
        find_location,
        load_project,
        location_key,
        new_id,
        new_project_document,
        panels_at_location,
        project_from_script,
        record_take,
        reorder_pages,
        save_project,
    )
    from script_canonical import looks_canonical as script_looks_canonical
    from script_canonical import parse as parse_script_text
except ImportError:  # Keep older single-file plug-in installs usable for legacy projects.
    ProjectFileError = ValueError
    project_from_script = parse_script_text = script_looks_canonical = None
    lettering = bubble_templates = setup_ui = engine_services = engine_ui = None
    load_project = record_take = save_project = apply_field_edit = None
    delete_character = delete_location = find_location = None
    location_key = panels_at_location = None
    new_id = None
    new_project_document = None
    build_docks = None
    character_row_id = location_row_id = None
    panel_label = None
    rgb_png = None
    build_welcome_docks = None
    frame_rings = layout_preview_rgb = page_layout_availability = None

PROC_RENDER = "plug-in-imanganation-render-panel"
PROC_NEXT = "plug-in-imanganation-place-next-panel"
PROC_PLACE = "plug-in-imanganation-place-panel"
PROC_REFINE = "plug-in-imanganation-refine-panel"
PROC_REGEN = "plug-in-imanganation-regenerate-panel"
PROC_SETREF = "plug-in-imanganation-set-character-reference"
PROC_SET_LOCATION_REF = "plug-in-imanganation-set-location-reference"
PROC_INPAINT = "plug-in-imanganation-inpaint-selection"
PROC_STATUS = "plug-in-imanganation-engine-status"
PROC_PROJECT_DOCKS = "plug-in-imanganation-project-docks"
PROC_PAGE_LAYOUT = "plug-in-imanganation-page-layout"
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
DOCK_IDS = (DOCK_PROJECT, DOCK_INSPECTOR, DOCK_FILMSTRIP, DOCK_SCRIPT,
            DOCK_CHARACTERS, DOCK_PANEL, DOCK_BUBBLES)
DOCK_ACTIONS = {
    DOCK_PROJECT: "plug-in-imanganation-dock-project-action",
    DOCK_INSPECTOR: "plug-in-imanganation-dock-inspector-generate",
    DOCK_FILMSTRIP: "plug-in-imanganation-dock-filmstrip-add-page",
    DOCK_SCRIPT: "plug-in-imanganation-dock-script-refresh",
    DOCK_CHARACTERS: "plug-in-imanganation-dock-characters-refresh",
    DOCK_PANEL: "plug-in-imanganation-dock-panel-refresh",
}
DOCK_OPEN_PROJECT = "plug-in-imanganation-dock-open-project"
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
DOCK_GENERATE_LAYOUT = "plug-in-imanganation-dock-generate-page-layout"
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
    DOCK_FILMSTRIP: "P_ages", DOCK_SCRIPT: "_Script",
    DOCK_CHARACTERS: "_Character Bible", DOCK_PANEL: "P_anel",
}
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
        raise EngineError(f"imanganation engine not reachable at {url} ({exc}). "
                          "Start it with: uv run manganation serve") from exc


def _ping(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return f"ok {resp.status}"
    except Exception as exc:  # report, never fail the placement on a ping
        return f"unreachable ({exc})"


def _error(procedure, msg):
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
    label = f"Panel {spec.get('page', '?')}.{spec.get('panel', '?')}"
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

    # Dialogue/SFX as hidden text layers: reference for hand lettering only.
    lines = [f"{d.get('speaker', '')}: {d.get('text', '')}" for d in spec.get("dialogue", [])]
    lines += [f"SFX: {sfx}" for sfx in spec.get("sfx", [])]
    for i, line in enumerate(lines):
        text = Gimp.TextLayer.new(image, line, Gimp.context_get_font(), 24.0,
                                  Gimp.Unit.pixel())
        text.set_name(line[:60])
        image.insert_layer(text, group, 0)
        text.set_offsets(x1 + 16, y1 + 16 + 36 * i)
        text.set_visible(False)

    if template is not None and overlay is not None:
        _clear_layout_region(image, template, overlay.get("region"))

    # Hand the template back so the next Fuzzy Select click samples the frames.
    if template is not None:
        image.set_selected_layers([template])

    image.undo_group_end()
    Gimp.displays_flush()
    return layer


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
    msg = (f"Placed {seq:03d}/{len(panels):03d} (script page {spec['page']}, "
           f"panel {spec['panel']}).")
    if nxt is None:
        msg += " That was the last panel."
    else:
        msg += f" Next: script page {nxt['page']}, panel {nxt['panel']}."
        if nxt["page"] != spec["page"]:
            msg += " (The script starts a new page there.)"
    Gimp.message(msg)


def _render_warnings(result):
    """Tell the artist what the engine had to render without (e.g. a character with no
    registered appearance comes out as nobody in particular)."""
    for warning in result.get("warnings") or []:
        Gimp.message(f"Warning: {warning}")


def _run_job(engine, path, body, label):
    """POST a job to the engine and poll it with a progress bar; -> result dict."""
    job = _http("POST", f"{engine}{path}", body)
    Gimp.progress_init(label)
    deadline = time.monotonic() + RENDER_TIMEOUT
    try:
        while job["status"] in ("queued", "running"):
            if time.monotonic() > deadline:
                raise EngineError(f"timed out after {RENDER_TIMEOUT}s (job {job['id']})")
            time.sleep(0.5)
            Gimp.progress_pulse()
            job = _http("GET", f"{engine}/jobs/{job['id']}")
    finally:
        Gimp.progress_end()
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
                          f"Rendering panel {seq:03d} (script page {spec['page']}, "
                          f"panel {spec['panel']})…")
        image_path, _take = _record_take(
            root, manifest, seq, result["path"], "render", result["width"],
            result["height"], engine={k: result.get(k) for k in
                                      ("seed", "width", "height", "prompt")})
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
    inside a selected panel group, or the render beside a selected text layer."""
    def tagged(layer):
        return layer if layer.get_parasite(PARASITE) else None

    for item in drawables:
        if isinstance(item, Gimp.LayerMask):
            item = Gimp.Layer.from_mask(item)
        found = tagged(item)
        group = item if item.is_group() else item.get_parent()
        if not found and group is not None:
            visible = [c for c in group.get_children() if tagged(c)]
            found = next((c for c in visible if c.get_visible()), visible[0] if visible else None)
        if found:
            return found
    return None


def _selected_panel(drawables):
    """-> (layer, meta, seq, source Path) for the selected placed panel, or raise
    ValueError with a message for the artist."""
    layer = _panel_layer(drawables)
    if layer is None:
        raise ValueError("Select a placed imanganation panel (its layer or group).")
    meta = json.loads(bytes(layer.get_parasite(PARASITE).get_data()))
    seq = meta.get("seq")
    source = meta.get("file") or (meta.get("render") or {}).get("path")
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
    stored = dict(meta, file=str(path),
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
                          f"Regenerating panel {seq:03d} (script page {spec['page']}, "
                          f"panel {spec['panel']})…")
    except EngineError as exc:
        return _error(procedure, str(exc))

    try:
        path, _take = _record_take(
            root, manifest, seq, result["path"], "render", result["width"],
            result["height"], engine={k: result.get(k) for k in
                                      ("seed", "width", "height", "prompt")})
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


def _panel_at(image, x, y):
    """The topmost visible placed take covering page point (x, y), if any."""
    def walk(layers):
        for layer in layers:  # top of the stack first
            if not layer.get_visible():
                continue
            if layer.is_group():
                found = walk(layer.get_children())
                if found:
                    return found
            elif layer.get_parasite(PARASITE):
                _, lx, ly = layer.get_offsets()
                if lx <= x < lx + layer.get_width() and ly <= y < ly + layer.get_height():
                    return layer
        return None

    return walk(image.get_layers())


def _name_list(text):
    """"Yuki, Akira" -> ["Yuki", "Akira"] (blank entries dropped)."""
    return [name.strip() for name in (text or "").split(",") if name.strip()]


def inpaint_selection(procedure, run_mode, image, drawables, config, data):
    _, non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
    if not non_empty:
        return _error(procedure, "Select the area to repaint first.")
    # After placing, the template is the selected layer (for Fuzzy Select), so an
    # artist who just draws a selection means "the panel under it".
    if _panel_layer(drawables) is None:
        under = _panel_at(image, (x1 + x2) // 2, (y1 + y2) // 2)
        drawables = [under] if under is not None else drawables
    try:
        layer, meta, seq, source = _selected_panel(drawables)
    except ValueError as exc:
        return _error(procedure, str(exc))
    _, lx, ly = layer.get_offsets()
    if x2 <= lx or y2 <= ly or x1 >= lx + layer.get_width() or y1 >= ly + layer.get_height():
        return _error(procedure, "The selection doesn't overlap the selected panel.")
    if not source.is_file():
        return _error(procedure, f"This take's file is missing: {source}")

    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config, PROC_INPAINT):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
    prompt = (config.get_property("prompt") or "").strip()
    if not prompt:
        return _error(procedure, "Describe what to paint in the selection.")

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
        result = _run_job(config.get_property("engine-url").rstrip("/"), "/inpaint", body,
                          f"Inpainting panel {seq:03d}: {prompt[:40]}…")
    except (EngineError, ValueError) as exc:
        return _error(procedure, str(exc))

    try:
        mask_ref = mask.relative_to(root).as_posix()
        path, _take = _record_derived_take(
            root, seq, source, result, "inpaint",
            {"mask": mask_ref, **{k: result.get(k) for k in
                                  ("prompt", "denoise", "grow_mask_by", "seed")}})
    except (ValueError, KeyError) as exc:
        return _error(procedure, str(exc))
    stored = dict(meta, file=str(path),
                  inpainted={k: result.get(k) for k in ("prompt", "source", "mask", "denoise",
                                                        "grow_mask_by", "seed")})
    new = _swap_in(image, layer, str(path), stored, f"{_base_name(layer)} inpaint",
                   fit="layer", take_ref=_take_reference(root, seq, path))
    Gimp.message(f"Inpainted panel {seq:03d} ({Path(result['path']).name}); outside the "
                 f"selection the take is unchanged. Previous take kept, hidden.")
    manifest = _manifest_for(root)
    if manifest is not None:
        _save_project_page(image, root, manifest)
    return _success(procedure, new)


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
        result = _http("POST", f"{engine}/characters/reference",
                       {**engine_project, "name": name, "image_path": str(path)})
    except (EngineError, ValueError) as exc:
        return _error(procedure, str(exc))

    Gimp.message(f"{result['name']}'s reference is now {result['version']} ({side}px square; "
                 f"was {result['previous']}). New renders of {result['name']} use it; earlier "
                 f"versions are kept in characters/{slug}/.")
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
        line += f" · next: panel {nxt:03d} (script page {spec['page']}, panel {spec['panel']})"
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
        lines.append(f"  ▶ {job(j)}: {j.get('elapsed_s', 0)} s so far")
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
        return _error(procedure, f"Panel {seq:03d} (script page {spec['page']}, panel "
                                 f"{spec['panel']}) is not rendered yet: expected "
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


def _remember_project(root):
    state_file = _last_project_file()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_file.with_suffix(".tmp")
    temporary.write_text(str(Path(root).resolve()), encoding="utf-8")
    os.replace(temporary, state_file)


def _remembered_project():
    try:
        root = Path(_last_project_file().read_text(encoding="utf-8").strip())
        load_project(root)
        return root
    except (OSError, ProjectFileError, ValueError):
        return None


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
                        recommendation=None):
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
                    f"{len(first_layout['regions'])} panels · script-matched layouts appear "
                    "first; choose a preview, then Apply")
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
        root = root or _DOCK_CONTEXT.get("root") or _remembered_project()
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
        selection = _choose_page_layout(
            combinations, image.get_width(), image.get_height(),
            page.get("label", "Page"), availability.get("recommendation")) \
            if run_mode == Gimp.RunMode.INTERACTIVE \
            else combinations[0]
        if selection is None:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        layout, frame_style = selection
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
        _save_project_page(image, root, manifest)
        return _success(procedure, layer)
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

    GLib.timeout_add_seconds(2, poll)


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


def _engine_character_rows(root, character_name):
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
    return [
        "# Engine character record",
        f"Default version\t{character.get('default_version') or 'None'}",
        f"Versions\t{', '.join(versions) or 'None'}",
        f"Reference image\t{reference}",
    ]


def _engine_location(root, manifest, name):
    """The engine's record of this place ({"key", "image", "description", …}), or None
    if it has none. Raises EngineError when the engine isn't answering."""
    query = urllib.parse.urlencode(_engine_project(root, manifest))
    known = _http("GET", f"{ENGINE_URL}/locations?{query}", timeout=3)
    key = location_key(name)
    return next((loc for loc in known if loc.get("key") == key), None)


def _engine_location_rows(root, manifest, name):
    rows = ["# Engine location reference"]
    if location_key(name) in _LOCATION_DESIGN_JOBS.values():
        rows.append("Design\tIn progress…")
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
        DOCK_OPEN_PAGE, DOCK_GENERATE_LAYOUT, **_dock_actions(root, manifest))
    _DOCK_CONTEXT["selected_id"] = contents["selected_id"]
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
        engine_rows = "\n".join(_engine_character_rows(root, character["name"]))
        if character["name"] in _DESIGN_JOBS.values():
            engine_rows += "\nDesign\tIn progress…"
        contents["characters"] += "\n" + engine_rows
        if "\nReference image\tAvailable" in engine_rows:
            engine_rows += (f"\n!{DOCK_OPEN_CHARACTER_IMAGE}\tOpen reference image"
                            "\nPaint over\tOpen it, edit, then Imanganation > Set "
                            "Character Reference from Layer…")
        contents["inspector"] += "\n" + engine_rows
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
            (DOCK_PANEL, "panel", None)):  # properties rows have no selection
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
    for key in ("location", "camera", "characters", "expressions", "aspect_ratio",
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
    group_name = f"Panel {label.get('page', '?')}.{label.get('panel', '?')}"
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


def _create_project_page(root, manifest, width, height, number=None, resolution=None):
    """A new page document; ``number`` (a script page) fixes its label and file name."""
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
    panel = manifest["panels"][ids.index(selected)]
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
    config.set_property("panel-number", ids.index(selected) + 1)
    result = procedure.run(config)
    status = result.index(0)
    error = Gimp.get_pdb().get_last_error()  # read before other PDB calls replace it
    _refresh_project_docks()
    if status not in (Gimp.PDBStatusType.SUCCESS, Gimp.PDBStatusType.CANCEL):
        raise RuntimeError(error or "Generate failed")


def _dock_action(procedure, config, data):
    try:
        if data == "open-project":
            root = _remembered_project() or _choose_project_folder()
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
        elif data == "match-panel":
            _match_selected_panel()
            _refresh_project_docks()
        elif data == "set-panel-frame":
            _set_panel_frame_from_selection()
        elif data == "generate":
            _generate_selected_panel()
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
        elif data == "design-character":
            _design_selected_character()
        elif data == "design-location":
            _design_selected_location()
        elif data == "open-location-image":
            _open_selected_location_image()
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
    dialog.get_content_area().add(grid)
    dialog.show_all()
    try:
        while dialog.run() == Gtk.ResponseType.OK:
            options = {"script_path": Path(script.get_filename() or ""),
                       "title": title.get_text().strip(),
                       "parent": Path(folder.get_filename() or Path.home()),
                       "page_size": (width.get_value_as_int(), height.get_value_as_int()),
                       "design": design.get_active()}
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
            ("First page size", size_row), ("Resolution", resolution_row))
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
                           resolution, page_format, preset):
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
    return ({"cast": result["cast"], "panels": result["panels"],
             "problems": result.get("problems", [])}, result["format"])


def _confirm_script_problems(problems):
    """List the script's format problems -> True to build the project anyway."""
    dialog = Gtk.Dialog(title="Script problems", flags=Gtk.DialogFlags.MODAL)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       "Create anyway", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    dialog.set_default_size(560, 360)
    box = dialog.get_content_area()
    box.set_spacing(8)
    intro = Gtk.Label(
        label=(f"{len(problems)} lines of the script don't" if len(problems) != 1
               else "1 line of the script doesn't")
              + " fit the format (see docs/script-template.md). Fix the script and try "
              "again, or create the project without them.",
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


def _create_project_from_script(script_path, title, parent, page_size, design):
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
        save_project(root, manifest)
        for number in dict.fromkeys(p["label"]["page"] for p in manifest["panels"]):
            _create_project_page(root, manifest, *page_size, number=number)
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


def _queue_character_design(root, manifest, character, redesign=False):
    """Ask the engine to design one character from its notes; the docks refresh when
    the sheet is ready."""
    body = {**_engine_project(root, manifest), "name": character["name"],
            "description": character.get("notes", ""),
            "aliases": character.get("aliases", []), "redesign": redesign}
    job = _http("POST", f"{ENGINE_URL}/characters", body)
    if not _DESIGN_JOBS:
        GLib.timeout_add_seconds(3, _poll_design_jobs)
    _DESIGN_JOBS[job["id"]] = character["name"]


def _poll_design_jobs():
    for job_id, name in list(_DESIGN_JOBS.items()):
        try:
            job = _http("GET", f"{ENGINE_URL}/jobs/{job_id}", timeout=3)
        except EngineError:
            continue  # engine restarting: try again next tick
        if job["status"] in ("queued", "running"):
            continue
        del _DESIGN_JOBS[job_id]
        if job["status"] == "error":
            Gimp.message(f"Designing {name} failed: {job.get('error')}")
        try:
            _refresh_project_docks()
        except Exception:
            pass
    return GLib.SOURCE_CONTINUE if _DESIGN_JOBS else GLib.SOURCE_REMOVE


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
    """Delete page… (a page's right-click menu) or a drop in the page strip."""
    try:
        root = _DOCK_CONTEXT["root"]
        item = config.get_property("item")
        manifest = load_project(root)
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
    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_border_width(12)
    box.pack_start(Gtk.Label(label=engine_ui.INTRO, xalign=0.0, wrap=True,
                             max_width_chars=76), False, False, 0)
    rows, group = {}, None

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
        button.connect("toggled", lambda *_: update_warning())

    for row in engine_ui.engines(report):
        add_row(row, radio=True)
    box.pack_start(Gtk.Separator(margin_top=6), False, False, 0)
    face = engine_ui.face_pass(report)
    if face is not None:
        add_row(face, radio=False)
    warning = Gtk.Label(xalign=0.0, wrap=True, max_width_chars=76, margin_top=6)
    box.pack_start(warning, False, False, 0)
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
        return {"engine": engine, "face_pass": face_on}

    def update_warning():
        now = _ENGINE_DIALOG.get("report") or report
        picked = next((r for r in engine_ui.engines(now) if r["id"] == selected()["engine"]),
                      None)
        text = engine_ui.warning(picked)
        warning.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>" if text else "")
        locations_note.set_text(engine_ui.locations_note(selected()))

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
            engine_ui.set_project_render(current, picked["engine"], picked["face_pass"])
            save_project(root, current)
            Gimp.message(f"Render engine for this project: {picked['engine']}"
                         + (", with the face pass." if picked["face_pass"] else "."))
        _ENGINE_DIALOG.clear()
        dlg.destroy()

    dialog.connect("response", on_response)
    dialog.connect("delete-event", lambda *_: _ENGINE_DIALOG.clear() or False)
    _ENGINE_DIALOG.update({"dialog": dialog, "report": report})
    dialog.show_all()
    refresh()
    GLib.timeout_add_seconds(1, refresh)


def _queue_location_design(root):
    """Ask the engine to design every location the project's panels use."""
    manifest = load_project(root)
    job = _http("POST", f"{ENGINE_URL}/locations/design",
                {"project": manifest["project"]["id"], "panels": manifest["panels"],
                 "locations": manifest.get("locations", [])})
    if not _LOCATION_JOBS:
        GLib.timeout_add_seconds(3, _poll_location_jobs)
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
            "description": location.get("notes", ""), "redesign": redesign}
    job = _http("POST", f"{ENGINE_URL}/locations", body)
    if not _LOCATION_DESIGN_JOBS:
        GLib.timeout_add_seconds(3, _poll_single_location_jobs)
    _LOCATION_DESIGN_JOBS[job["id"]] = location_key(location["name"])


def _poll_single_location_jobs():
    for job_id, key in list(_LOCATION_DESIGN_JOBS.items()):
        try:
            job = _http("GET", f"{ENGINE_URL}/jobs/{job_id}", timeout=3)
        except EngineError:
            continue  # engine restarting: try again next tick
        if job["status"] in ("queued", "running"):
            continue
        del _LOCATION_DESIGN_JOBS[job_id]
        if job["status"] == "error":
            Gimp.message(f"Designing {job['request'].get('name', key)} failed: "
                         f"{job.get('error')}")
        try:
            _refresh_project_docks()
        except Exception:
            pass
    return GLib.SOURCE_CONTINUE if _LOCATION_DESIGN_JOBS else GLib.SOURCE_REMOVE


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


def _open_selected_character_image():
    """Open the selected character's active reference image in GIMP."""
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
    if record is None or not record.get("reference"):
        raise ValueError(f"{character['name']} has no reference image yet")
    _open_reference_image(record["reference"], CHARACTER_PARASITE, root, record["name"])


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
        _update_setup_dialog(widgets, _setup_report())
        _update_optional_engines(widgets, _engines_report(), post)
        return True

    def on_destroy(_widget):
        _SETUP_DIALOG.clear()

    dialog.connect("response", on_response)
    dialog.connect("destroy", on_destroy)
    _SETUP_DIALOG.update(widgets)
    refresh()
    dialog.show_all()
    GLib.timeout_add_seconds(1, refresh)


def _update_setup_dialog(widgets, report):
    """Fill the dialog from a GET /setup report (None: the engine isn't answering)."""
    actions = setup_ui.actions(report)
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
            "reorder_pages_action": DOCK_REORDER_PAGES,
            "bubble_line_action": DOCK_BUBBLE_LINE, "bubbled": frozenset(bubbled),
            "new_bubble_action": DOCK_NEW_BUBBLE,
            "design_location_action": DOCK_DESIGN_LOCATION,
            "new_location_action": DOCK_NEW_LOCATION,
            "design_location_menu": DOCK_DESIGN_LOCATION_ITEM,
            "delete_location_menu": DOCK_DELETE_LOCATION}


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
            tip = (_bubble_tail_tip(image, speaker, center, frame)
                   if template.tail != "none" else None)
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
        apply_field_edit(manifest, key, value, character_row_id, location_row_id)
        save_project(root, manifest)
        _refresh_project_docks()
    except Exception as exc:
        return _error(procedure, f"Could not save that change: {exc}")
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _dock_item_action(procedure, config, data):
    try:
        item = config.get_property("item")
        manifest = load_project(_DOCK_CONTEXT["root"])
        valid = {p["id"] for p in manifest["panels"]}
        valid.update(page["id"] for page in manifest["pages"])
        valid.update(character_row_id(c["name"]) for c in manifest["cast"])
        valid.update(location_row_id(loc["name"]) for loc in manifest.get("locations", []))
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
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
    except Exception as exc:
        return _error(procedure, str(exc))


def _add_dock_callbacks(plugin):
    callbacks = [
        (DOCK_ACTIONS[DOCK_PROJECT], _dock_action, "project-action", False),
        (DOCK_OPEN_PROJECT, _dock_action, "open-project", False),
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
        (DOCK_REORDER_PAGES, _dock_page_menu, "reorder", True),
        (DOCK_BUBBLE_LINE, _dock_bubble, "line", True),
        (DOCK_BUBBLE_ITEM, _dock_bubble, "template", True),
        (DOCK_NEW_BUBBLE, _dock_bubble, "new", False),
        (DOCK_FIT_BUBBLE, _dock_bubble, "fit", False),
        (DOCK_GENERATE_LAYOUT, _dock_action, "generate-layout", False),
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
                                       callback, data)
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
        ]
    else:
        manifest = load_project(root)
        contents = build_docks(
            manifest, _DOCK_CONTEXT.get("selected_id"), root,
            _project_page_thumbnails(root, manifest), DOCK_OPEN_PAGE,
            DOCK_GENERATE_LAYOUT, **_dock_actions(root, manifest))
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
        ]
    for identifier, title, presentation, content, selected, action_label, action, item in rows:
        _dock_pdb_call("gimp-extension-panel-register", {
            "identifier": identifier,
            "title": title,
            "content": content,
            "presentation": presentation,
            "selected-item": selected,
            "action-label": action_label,
            "action-procedure": action,
            "item-action-procedure": item,
        })
    if root is not None:
        _refresh_project_docks()


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


def _show_dock_run(procedure, config, dock):
    """Reopen (or bring forward) one workspace dock registered by the extension."""
    try:
        _dock_pdb_call("gimp-extension-panel-show", {"identifier": dock})
    except Exception:
        return _error(procedure, "The Imanganation workspace is not running; restart GIMP "
                                 "to start it")
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


def _autostart_run(procedure, config, data):
    """Install the default workspace before GIMP restores its dock layout."""
    try:
        root = _remembered_project()
        if root is not None:
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
        GLib.timeout_add(1000, _watch_canvas_selection)
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
        return [PROC_RENDER, PROC_NEXT, PROC_REGEN, PROC_INPAINT, PROC_REFINE, PROC_SETREF,
                PROC_SET_LOCATION_REF,
                PROC_PLACE, PROC_STATUS, PROC_PROJECT_DOCKS, PROC_PAGE_LAYOUT, PROC_AUTOSTART,
                PROC_NEW_PROJECT_MANUAL, PROC_NEW_PROJECT,
                PROC_SETUP_MODELS, PROC_RENDER_ENGINE,
                *DOCK_SHOW.values()]

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
            proc.add_menu_path("<Toolbox>/File/[Open]")
            proc.add_menu_path("<Image>/File/[Open]")
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
            # Keep project creation beside the other ways to open/create work,
            # in the first File section and in the toolbox before an image exists.
            proc.add_menu_path("<Toolbox>/File/[Open]")
            proc.add_menu_path("<Image>/File/[Open]")
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
            proc.add_menu_path("<Image>/Imanganation")
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
            proc.add_menu_path("<Image>/Imanganation")
            proc.set_documentation(
                "Choose the open project's render engine",
                "Pick the engine that draws this project's panels (each with its licence), "
                "turn the face pass on or off, install what's missing, and design the "
                "script's locations.", name)
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
            proc.add_menu_path("<Toolbox>/File/[Open]")
            proc.add_menu_path("<Image>/File/[Open]")
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
               PROC_PLACE: place_panel, PROC_STATUS: engine_status,
               PROC_PAGE_LAYOUT: page_layout}[name]
        proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, run, None)
        proc.set_image_types("*")
        proc.set_sensitivity_mask(
            Gimp.ProcedureSensitivityMask.DRAWABLE
            | Gimp.ProcedureSensitivityMask.DRAWABLES
            | Gimp.ProcedureSensitivityMask.NO_DRAWABLES)
        # GIMP 3.3 requires a menu label before a menu path is added. Each
        # procedure below replaces this fallback with its user-facing label.
        proc.set_menu_label("Imanganation")
        proc.add_menu_path("<Image>/Imanganation")
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

        proc.add_layer_return_value(
            "layer", "Layer", "The placed panel layer", False, GObject.ParamFlags.READWRITE)

        if name == PROC_INPAINT:
            proc.set_menu_label("_Inpaint Selection...")
            proc.set_documentation(
                "Repaint the selected area of a panel",
                "Repaint only the selected area of the selected panel's take from a short "
                "prompt; everything outside the selection stays exactly as it was. The "
                "result is swapped in as a new take, the previous one kept hidden.",
                name)
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
                "into the selected frame, with its dialogue "
                "as hidden text layers.",
                name)
            _add_project_args(proc)
            return proc

        proc.set_menu_label("_Place Panel...")
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
