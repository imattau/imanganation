#!/usr/bin/env python3
"""imanganation GIMP 3 plug-in.

Runs inside GIMP's own Python (the flatpak ships 3.14 with GI bindings but no
numpy/PIL/pydantic), so this file is stdlib + gi only. All generation stays in the
imanganation engine (``manganation serve``); the plug-in is a thin HTTP client.

Workflow: the artist builds every page in GIMP — by hand or from an imanganation
panel layout template. They click into a frame (Fuzzy Select on the template),
run "Render Panel into Frame", and the engine renders the next script panel at
that frame's proportions; the plug-in drops it in, clipped to the frame, below the
template's frame lines. Then the next frame, and so on. Generated layouts provide
editable frame borders only; panel generation and page composition remain artist-led.

Panels are identified by their 1-based position in the script (``seq``), not
page/panel, because scripts can repeat panel numbers within a page (e.g. after
``CUT TO:``). A project container (``project.json``) renders through the engine's
inline form (project id + panel object); a legacy folder through ``panels.json`` +
``seq``, with images at ``panels/{seq:03d}*.png``.

Template convention: a layer whose name starts with "template" holds the frame
lines. New panels go directly beneath it (a Normal-mode template is switched to
Multiply so its white areas let panels show through), and it is re-selected after
each placement so the next Fuzzy Select click samples the frames, not the last
render.
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
    import lettering
    from layouts import frame_rings, layout_preview_rgb, page_layout_availability
    from panel_ui import _panel_label as panel_label
    from panel_ui import build_docks, build_welcome_docks, character_row_id, rgb_png
    from project_store import (
        ProjectFileError,
        apply_field_edit,
        delete_page,
        load_project,
        new_id,
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
    lettering = bubble_templates = None
    load_project = record_take = save_project = apply_field_edit = None
    new_id = None
    build_docks = None
    character_row_id = None
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
PROC_INPAINT = "plug-in-imanganation-inpaint-selection"
PROC_STATUS = "plug-in-imanganation-engine-status"
PROC_PROJECT_DOCKS = "plug-in-imanganation-project-docks"
PROC_PAGE_LAYOUT = "plug-in-imanganation-page-layout"
PROC_AUTOSTART = "extension-imanganation-ui"
PROC_NEW_PROJECT = "plug-in-imanganation-new-project"
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
DOCK_DESIGN_CHARACTER = "plug-in-imanganation-dock-design-character"
# Project tree right-click menus (one-string procedures: the row id)
DOCK_NEW_CHARACTER = "plug-in-imanganation-dock-new-character"
DOCK_DESIGN_CHARACTER_ITEM = "plug-in-imanganation-dock-design-character-item"
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
PARASITE = "imanganation-panelspec"
PROJECT_PARASITE = "imanganation-project"
PANEL_PARASITE = "imanganation-panel"
TAKE_PARASITE = "imanganation-take"
CURSOR_FILE = "gimp_cursor.json"  # per project: which panel comes next
ENGINE_URL = "http://127.0.0.1:8790"
RENDER_TIMEOUT = 600  # seconds
# The checkout the plug-in files are symlinked from: the workspace starts the engine and
# ComfyUI from it. IMANGANATION_HOME overrides; IMANGANATION_AUTOSTART=0 turns it off.
ENGINE_HOME = Path(os.environ.get("IMANGANATION_HOME")
                   or Path(__file__).resolve().parents[2])
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


def _place(image, panel_file, spec, seq=None, render=None, take_ref=None, frame=None):
    """Load a panel as a layer in its own group, fitted to the selection if any."""
    label = f"Panel {spec.get('page', '?')}.{spec.get('panel', '?')}"
    if seq is not None:
        label = f"{seq:03d} {label}"

    image.undo_group_start()

    # Reuse a previously defined empty semantic panel group, if present.
    template = _find_template(image)
    group = _find_panel_group(image, take_ref, empty_only=True)
    if group is None:
        group = Gimp.GroupLayer.new(image, label)
        if template is not None:
            # An opaque white-page template would hide panels beneath it; Multiply keeps
            # the black frame lines and lets the panel show through.
            if template.get_mode() == Gimp.LayerMode.NORMAL:
                template.set_mode(Gimp.LayerMode.MULTIPLY)
            image.insert_layer(group, template.get_parent(),
                               image.get_item_position(template) + 1)
        else:
            image.insert_layer(group, None, 0)
        _tag_panel_group(group, take_ref)

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
    return {"project": manifest["project"]["id"], "panel": manifest["panels"][seq - 1],
            "reading_order": manifest["project"].get("reading_order", "rtl")}


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


def _export_reference(image, drawable, path):
    """Export ``drawable`` (or its part inside the selection) as a square PNG.

    The CLIP encoder centre-crops references to a square, so the region is grown to
    a square around its centre here, on white, instead of being cropped there. Layer
    masks apply (a panel's frame clip), so only what the artist sees is exported."""
    _, lx, ly = drawable.get_offsets()
    lw, lh = drawable.get_width(), drawable.get_height()
    x1, y1, x2, y2 = lx, ly, lx + lw, ly + lh
    _, non_empty, sx1, sy1, sx2, sy2 = Gimp.Selection.bounds(image)
    if non_empty:
        x1, y1, x2, y2 = max(x1, sx1), max(y1, sy1), min(x2, sx2), min(y2, sy2)
        if x2 <= x1 or y2 <= y1:
            raise ValueError("The selection doesn't overlap the selected layer.")
    side = max(x2 - x1, y2 - y1)
    ox, oy = (x1 + x2) // 2 - side // 2, (y1 + y2) // 2 - side // 2

    out = Gimp.Image.new(side, side, image.get_base_type())
    try:
        copy = Gimp.Layer.new_from_drawable(drawable, out)
        copy.set_visible(True)
        out.insert_layer(copy, None, 0)
        copy.set_offsets(lx - ox, ly - oy)
        bg_type = (Gimp.ImageType.RGB_IMAGE if image.get_base_type() == Gimp.ImageBaseType.RGB
                   else Gimp.ImageType.GRAY_IMAGE)
        bg = Gimp.Layer.new(out, "white", side, side, bg_type, 100, Gimp.LayerMode.NORMAL)
        out.insert_layer(bg, None, 1)
        bg.fill(Gimp.FillType.WHITE)
        out.flatten()
        if out.get_base_type() != Gimp.ImageBaseType.RGB:
            out.convert_rgb()
        if side > REF_MAX:
            out.scale(REF_MAX, REF_MAX)
        path.parent.mkdir(parents=True, exist_ok=True)
        Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, out, Gio.File.new_for_path(str(path)), None)
    finally:
        out.delete()
    return side


def set_character_reference(procedure, run_mode, image, drawables, config, data):
    layers = [d for d in drawables if isinstance(d, Gimp.Layer)]
    if not layers:
        return _error(procedure, "Select the layer that shows the character.")
    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config, PROC_SETREF):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

    root = _image_project(image)
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


def _choose_page_layout(combinations, page_width, page_height, page_label):
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
    content.pack_start(Gtk.Label(
        label=f"{page_label} · {len(combinations[0][0]['regions'])} panels · "
              "choose a preview, then Apply"), False, False, 0)

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
        caption = Gtk.Label(label=f"{layout['name']}\n{style['name']}")
        caption.set_justify(Gtk.Justification.CENTER)
        caption.set_line_wrap(True)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        card.set_border_width(8)
        card.pack_start(preview, True, True, 0)
        card.pack_start(caption, False, False, 0)
        child = Gtk.FlowBoxChild()
        child.set_can_focus(True)
        child.set_tooltip_text(f"{layout['name']} — {style['name']}")
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
    """Find generated frame layers, including if the user grouped them."""
    found = []

    def walk(layers):
        for item in layers:
            if item.get_name().startswith("Template - Imanganation Layout -"):
                found.append(item)
            if item.is_group():
                walk(item.get_children())

    walk(image.get_layers())
    return found


def _draw_page_layout(image, layout, frame_style, replace_layers=()):
    """Add or replace generated frame art in one undo step, preserving page content."""
    width, height = image.get_width(), image.get_height()
    selection = Gimp.Selection.save(image)
    layer = Gimp.Layer.new(
        image, f"Template - Imanganation Layout - {layout['name']} - {frame_style['name']}",
                           width, height, Gimp.ImageType.RGBA_IMAGE, 100,
                           Gimp.LayerMode.NORMAL)
    previous_foreground = Gimp.context_get_foreground()
    image.undo_group_start()
    try:
        layer.fill(Gimp.FillType.TRANSPARENT)
        image.insert_layer(layer, None, 0)
        Gimp.context_set_foreground(Gegl.Color.new("black"))
        for index, region in enumerate(layout["regions"]):
            for outer, inner in frame_rings(region, width, height, frame_style, index):
                image.select_polygon(
                    Gimp.ChannelOps.REPLACE,
                    [coordinate for point in outer for coordinate in point])
                image.select_polygon(
                    Gimp.ChannelOps.SUBTRACT,
                    [coordinate for point in inner for coordinate in point])
                layer.edit_fill(Gimp.FillType.FOREGROUND)
        for old_layer in replace_layers:
            if old_layer.get_image() is image:
                image.remove_layer(old_layer)
    except Exception:
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
        availability = page_layout_availability(manifest, page_id)
        if not availability["available"]:
            raise ValueError(availability["reason"])
        combinations = availability["combinations"]
        existing_layers = _generated_layout_layers(image)
        selection = _choose_page_layout(
            combinations, image.get_width(), image.get_height(),
            page.get("label", "Page")) if run_mode == Gimp.RunMode.INTERACTIVE \
            else combinations[0]
        if selection is None:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        layout, frame_style = selection
        layer = _draw_page_layout(image, layout, frame_style, existing_layers)
        Gimp.message(f"Added {layout['name']} with {frame_style['name']} frames to "
                     f"{page.get('label', 'page')}. "
                     "Select inside a frame with Fuzzy Select, render into it, and "
                     "the page has been saved.")
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


def _start_service(name, command, cwd, log):
    import subprocess

    try:
        with open(log, "wb") as out:
            _SERVICES[name] = subprocess.Popen(
                command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                stderr=subprocess.STDOUT, preexec_fn=_die_with_parent)
    except OSError as exc:
        Gimp.message(f"Could not start {name}: {exc} (see {log})")


def _start_engine_services():
    """Start ComfyUI and the engine with GIMP, unless they are already running. They
    stop when GIMP quits; anything that was already running is left alone."""
    import shutil

    home = ENGINE_HOME
    if os.environ.get("IMANGANATION_AUTOSTART", "1") == "0" or not (
            home / "pyproject.toml").is_file():
        return
    comfy_python = home / "vendor/ComfyUI/.venv/bin/python"
    if comfy_python.is_file() and not _url_up(COMFY_URL + "/system_stats"):
        _start_service("ComfyUI", [
            str(comfy_python), "main.py", "--extra-model-paths-config",
            str(home / "config/comfyui_extra_model_paths.yaml"),
            "--listen", "127.0.0.1", "--port", COMFY_URL.rsplit(":", 1)[1]],
            home / "vendor/ComfyUI", home / "comfyui.log")
        if "ComfyUI" in _SERVICES:  # so scripts/comfy.sh status/stop see it
            (home / ".comfyui.pid").write_text(f"{_SERVICES['ComfyUI'].pid}\n")
    if not _url_up(ENGINE_URL + "/health"):
        engine = home / ".venv/bin/manganation"
        uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
        command = ([str(engine), "serve"] if engine.is_file()
                   else [uv, "run", "manganation", "serve"])
        _start_service("engine", command, home, home / "engine.log")
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
                         f"see {ENGINE_HOME / 'engine.log'}")
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
        contents["inspector"] += "\n" + engine_rows
        contents["characters"] += "\n" + engine_rows

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
                "seed", "notes"):
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
    return 1600, 2400


def _create_project_page(root, manifest, width, height, number=None):
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

    image = Gimp.Image.new(width, height, Gimp.ImageBaseType.RGB)
    try:
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
        elif data == "design-character":
            _design_selected_character()
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
    projects = ENGINE_HOME / "projects"
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
    """Project tree right-click: New character… (Characters heading) or Design
    character (a character row, whose id is the item)."""
    try:
        root = _DOCK_CONTEXT["root"]
        if data == "design":
            _DOCK_CONTEXT["selected_id"] = config.get_property("item")
            _design_selected_character()
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


def _new_project_run(procedure, config, data):
    """Imanganation > New Project from Script: the extension shows the dialog."""
    try:
        _dock_pdb_call(DOCK_NEW_PROJECT, {})
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
            "delete_page_action": DOCK_DELETE_PAGE,
            "reorder_pages_action": DOCK_REORDER_PAGES,
            "bubble_line_action": DOCK_BUBBLE_LINE, "bubbled": frozenset(bubbled),
            "new_bubble_action": DOCK_NEW_BUBBLE}


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
        apply_field_edit(manifest, key, value, character_row_id)
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
        (DOCK_ACTIONS[DOCK_INSPECTOR], _dock_action, "generate", False),
        (DOCK_ITEMS[DOCK_INSPECTOR], _dock_field_edit, "field", True),
        (DOCK_OPEN_PAGE, _dock_action, "open-page", False),
        (DOCK_NEW_PROJECT, _dock_action, "new-project", False),
        (DOCK_DESIGN_CHARACTER, _dock_action, "design-character", False),
        (DOCK_NEW_CHARACTER, _dock_character_menu, "new", True),
        (DOCK_DESIGN_CHARACTER_ITEM, _dock_character_menu, "design", True),
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
        contents = (build_welcome_docks(DOCK_NEW_PROJECT)
                    if build_welcome_docks is not None else {
            "project": "# Imanganation\nChoose a project folder to open your workspace.",
            "inspector": "# Workspace\nOpen a project to get started.",
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
             "", "Generate", DOCK_ACTIONS[DOCK_INSPECTOR], DOCK_ITEMS[DOCK_INSPECTOR]),
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
                PROC_PLACE, PROC_STATUS, PROC_PROJECT_DOCKS, PROC_PAGE_LAYOUT, PROC_AUTOSTART,
                PROC_NEW_PROJECT, *DOCK_SHOW.values()]

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
        if name == PROC_NEW_PROJECT:
            proc = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, _new_project_run, None)
            proc.set_menu_label("_New Project from Script...")
            proc.add_enum_argument("run-mode", "Run mode", "How to run the procedure",
                                   Gimp.RunMode, Gimp.RunMode.INTERACTIVE,
                                   GObject.ParamFlags.READWRITE)
            proc.add_menu_path("<Image>/Imanganation")
            proc.set_documentation(
                "New Imanganation project from a script",
                "Build a project (panels, pages, cast, locations) from a script and "
                "design its characters.", name)
            proc.set_attribution("imanganation", "imanganation", "2026")
            return proc
        if name == PROC_PROJECT_DOCKS:
            proc = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, _project_docks_run, None)
            proc.set_menu_label("Open / Switch _Project...")
            proc.add_enum_argument("run-mode", "Run mode", "How to run the procedure",
                                   Gimp.RunMode, Gimp.RunMode.INTERACTIVE,
                                   GObject.ParamFlags.READWRITE)
            proc.add_menu_path("<Image>/Imanganation")
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
                "Generate selectable panel frames for the current project page",
                "Choose a built-in layout with one frame per non-orphaned script panel "
                "for the page number in the current project page label. Adds editable "
                "frame borders to the page without replacing its existing content.",
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
