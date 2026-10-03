#!/usr/bin/env python3
"""imanganation GIMP 3 plug-in.

Runs inside GIMP's own Python (the flatpak ships 3.14 with GI bindings but no
numpy/PIL/pydantic), so this file is stdlib + gi only. All generation stays in the
imanganation engine (``manganation serve``); the plug-in is a thin HTTP client.

Workflow: the artist builds every page by hand — typically from a panel layout
template already in GIMP. They click into a frame (Fuzzy Select on the template),
run "Render Panel into Frame", and the engine renders the next script panel at
that frame's proportions; the plug-in drops it in, clipped to the frame, below the
template's frame lines. Then the next frame, and so on. The plug-in never lays out
a page.

Panels are identified by their 1-based position in panels.json (``seq``), not
page/panel, because scripts can repeat panel numbers within a page (e.g. after
``CUT TO:``). Rendered images live at ``panels/{seq:03d}*.png``.

Template convention: a layer whose name starts with "template" holds the frame
lines. New panels go directly beneath it (a Normal-mode template is switched to
Multiply so its white areas let panels show through), and it is re-selected after
each placement so the next Fuzzy Select click samples the frames, not the last
render.
"""

import json
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import gi

gi.require_version("Gimp", "3.0")
from gi.repository import Gimp  # noqa: E402
from gi.repository import Gio, GLib, GObject  # noqa: E402

PROC_RENDER = "plug-in-imanganation-render-panel"
PROC_NEXT = "plug-in-imanganation-place-next-panel"
PROC_PLACE = "plug-in-imanganation-place-panel"
PROC_REFINE = "plug-in-imanganation-refine-panel"
PROC_REGEN = "plug-in-imanganation-regenerate-panel"
PARASITE = "imanganation-panelspec"
CURSOR_FILE = "gimp_cursor.json"  # per project: which panel comes next
ENGINE_URL = "http://127.0.0.1:8790"
RENDER_TIMEOUT = 600  # seconds


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


def _place(image, panel_file, spec, seq=None, render=None):
    """Load a panel as a layer in its own group, fitted to the selection if any."""
    label = f"Panel {spec.get('page', '?')}.{spec.get('panel', '?')}"
    if seq is not None:
        label = f"{seq:03d} {label}"

    image.undo_group_start()

    # Directly beneath the template's frame lines, if there is a template.
    template = _find_template(image)
    group = Gimp.GroupLayer.new(image, label)
    if template is not None:
        # An opaque white-page template would hide panels beneath it; Multiply keeps
        # the black frame lines and lets white show the panel through.
        if template.get_mode() == Gimp.LayerMode.NORMAL:
            template.set_mode(Gimp.LayerMode.MULTIPLY)
        image.insert_layer(group, template.get_parent(),
                           image.get_item_position(template) + 1)
    else:
        image.insert_layer(group, None, 0)

    layer = Gimp.file_load_layer(Gimp.RunMode.NONINTERACTIVE, image, panel_file)
    layer.set_name(f"{label} render")
    image.insert_layer(layer, group, 0)

    # Fit to the artist's selection (the panel frame), cover-style, then clip with
    # a mask. No selection: drop it in at native size for the artist to position.
    _, non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
    if non_empty:
        fw, fh = x2 - x1, y2 - y1
        lw, lh = layer.get_width(), layer.get_height()
        s = max(fw / lw, fh / lh)
        nw, nh = round(lw * s), round(lh * s)
        layer.scale(nw, nh, False)
        layer.set_offsets(x1 + (fw - nw) // 2, y1 + (fh - nh) // 2)
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


def _load_project(config):
    """-> (root, panels, seq, explicit) or raises ValueError with a user message."""
    project = config.get_property("project-dir")
    if project is None:
        raise ValueError("Choose the project folder (the one with panels.json).")
    root = Path(project.get_path())
    try:
        panels = json.loads((root / "panels.json").read_text())["panels"]
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError(f"Cannot read {root / 'panels.json'}: {exc}") from exc

    explicit = config.get_property("panel-number")
    if explicit:
        seq = explicit
    else:
        try:
            seq = json.loads((root / CURSOR_FILE).read_text())["next"]
        except (OSError, ValueError, KeyError):
            seq = 1
    if not 1 <= seq <= len(panels):
        raise ValueError(f"All {len(panels)} panels placed. "
                         "Set a panel number to place one again.")
    return root, panels, seq, explicit


def _advance(root, panels, seq, explicit, spec):
    if not explicit:
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


def _newest_take(root, seq):
    """Newest file for a panel: a fresh retake beats an older hi-res, and a hi-res
    made from the current take beats that take. (Name order can't express this:
    ``003_hires`` sorts before ``003_take02``.)"""
    matches = list((root / "panels").glob(f"{seq:03d}*.png"))
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def render_panel(procedure, run_mode, image, drawables, config, data):
    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config, PROC_RENDER):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

    try:
        root, panels, seq, explicit = _load_project(config)
    except ValueError as exc:
        return _error(procedure, str(exc))
    spec = panels[seq - 1]

    _, non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
    if not non_empty:
        return _error(procedure, "Select the target frame first (e.g. Fuzzy Select inside "
                                 "an empty panel of your template).")

    # Keep the frame safe while the engine works; the artist may keep clicking.
    frame = Gimp.Selection.save(image)
    frame.set_name(f"imanganation frame {seq:03d}")
    try:
        engine = config.get_property("engine-url").rstrip("/")
        body = {"project_dir": str(root), "seq": seq,
                "frame_width": x2 - x1, "frame_height": y2 - y1}
        if config.get_property("seed") >= 0:
            body["seed"] = config.get_property("seed")
        result = _run_job(engine, "/jobs", body,
                          f"Rendering panel {seq:03d} (script page {spec['page']}, "
                          f"panel {spec['panel']})…")
        image.select_item(Gimp.ChannelOps.REPLACE, frame)
        layer = _place(image, Gio.File.new_for_path(result["path"]), spec, seq,
                       render={k: result.get(k) for k in ("seed", "width", "height",
                                                          "prompt", "path")})
    except EngineError as exc:
        return _error(procedure, str(exc))
    finally:
        image.remove_channel(frame)

    _advance(root, panels, seq, explicit, spec)
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


def _swap_in(image, old, path, stored, name):
    """Put a new take of ``old``'s panel beside it: same group, cover-fitted to the
    panel's frame, same frame mask. The old take is kept, hidden; selection untouched.

    Fit to the frame, not the old layer: the old take already overhangs the frame, so
    covering *it* would crop more than necessary when the new take's shape differs."""
    fx, fy, fw, fh = _frame_box(image, old)
    image.undo_group_start()
    saved = Gimp.Selection.save(image)  # the artist's selection, restored below
    try:
        new = Gimp.file_load_layer(Gimp.RunMode.NONINTERACTIVE, image,
                                   Gio.File.new_for_path(path))
        new.set_name(name)
        image.insert_layer(new, old.get_parent(), image.get_item_position(old))
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
    return layer.get_name().rsplit(" render", 1)[0].rsplit(" hi-res", 1)[0].rsplit(" take", 1)[0]


def refine_panel(procedure, run_mode, image, drawables, config, data):
    try:
        layer, meta, seq, source = _selected_panel(drawables)
    except ValueError as exc:
        return _error(procedure, str(exc))

    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config, PROC_REFINE):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

    body = {"project_dir": str(source.parent.parent), "seq": seq, "source": str(source)}
    if config.get_property("scale") > 0:
        body["scale"] = config.get_property("scale")
    if config.get_property("denoise") >= 0:
        body["denoise"] = config.get_property("denoise")
    try:
        result = _run_job(config.get_property("engine-url").rstrip("/"), "/refine", body,
                          f"Refining panel {seq:03d} (hi-res)…")
    except EngineError as exc:
        return _error(procedure, str(exc))

    stored = dict(meta, file=result["path"],
                  refined={k: result.get(k) for k in ("source", "width", "height",
                                                      "upscaler", "denoise", "seed")})
    hires = _swap_in(image, layer, result["path"], stored, f"{_base_name(layer)} hi-res")
    Gimp.message(f"Refined panel {seq:03d}: {result['width']}×{result['height']} "
                 f"(previous take kept, hidden).")
    return _success(procedure, hires)


def _recorded_seed(meta, source):
    """The seed that *composed* ``source``. A hi-res take's own sidecar seed is the
    refine polish seed, so follow its ``source`` back to the original render."""
    if "refined" not in meta and (meta.get("render") or {}).get("seed") is not None:
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
        panels = json.loads((root / "panels.json").read_text())["panels"]
        spec = panels[seq - 1]  # the script as it is *now*: edits apply
    except (OSError, ValueError, KeyError, IndexError) as exc:
        return _error(procedure, f"Panel {seq:03d} not found in {root / 'panels.json'}: {exc}")

    if config.get_property("same-seed"):
        seed = _recorded_seed(meta, source)
        if seed is None:
            return _error(procedure, "No seed is recorded for this take; untick Same seed.")
    else:
        # Explicit, so a seed pinned in the script can't hand back the same image.
        seed = random.randrange(2**31)

    _, _, fw, fh = _frame_box(image, layer)
    body = {"project_dir": str(root), "seq": seq, "frame_width": fw, "frame_height": fh,
            "seed": seed}
    try:
        result = _run_job(config.get_property("engine-url").rstrip("/"), "/jobs", body,
                          f"Regenerating panel {seq:03d} (script page {spec['page']}, "
                          f"panel {spec['panel']})…")
    except EngineError as exc:
        return _error(procedure, str(exc))

    stored = dict(spec, seq=seq, file=result["path"],
                  render={k: result.get(k) for k in ("seed", "width", "height", "prompt",
                                                     "path")})
    take = Path(result["path"]).stem.split("_", 1)[-1] if "_" in Path(result["path"]).stem \
        else "take01"
    new = _swap_in(image, layer, result["path"], stored, f"{_base_name(layer)} {take}")
    Gimp.message(f"Regenerated panel {seq:03d} as {Path(result['path']).name} "
                 f"(seed {seed}; previous take kept, hidden).")
    return _success(procedure, new)


def place_next_panel(procedure, run_mode, image, drawables, config, data):
    if run_mode == Gimp.RunMode.INTERACTIVE and not _dialog(procedure, config, PROC_NEXT):
        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

    try:
        root, panels, seq, explicit = _load_project(config)
    except ValueError as exc:
        return _error(procedure, str(exc))
    spec = panels[seq - 1]

    newest = _newest_take(root, seq)
    if newest is None:
        return _error(procedure, f"Panel {seq:03d} (script page {spec['page']}, panel "
                                 f"{spec['panel']}) is not rendered yet: expected "
                                 f"panels/{seq:03d}*.png. Use Render Panel into Frame.")

    layer = _place(image, Gio.File.new_for_path(str(newest)), spec, seq)
    _advance(root, panels, seq, explicit, spec)
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
        "project-dir", "_Project folder", "imanganation project (has panels.json)",
        Gimp.FileChooserAction.SELECT_FOLDER, False, None, GObject.ParamFlags.READWRITE)
    proc.add_int_argument(
        "panel-number", "Panel _number", "Use this panel (1-based, in script order) "
        "instead of the next one; 0 = next", 0, 9999, 0, GObject.ParamFlags.READWRITE)


class Imanganation(Gimp.PlugIn):
    def do_set_i18n(self, procname):
        return False, None, None

    def do_query_procedures(self):
        return [PROC_RENDER, PROC_NEXT, PROC_REGEN, PROC_REFINE, PROC_PLACE]

    def do_create_procedure(self, name):
        run = {PROC_RENDER: render_panel, PROC_NEXT: place_next_panel,
               PROC_REFINE: refine_panel, PROC_REGEN: regenerate_panel,
               PROC_PLACE: place_panel}[name]
        proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, run, None)
        proc.set_image_types("*")
        proc.set_sensitivity_mask(
            Gimp.ProcedureSensitivityMask.DRAWABLE
            | Gimp.ProcedureSensitivityMask.DRAWABLES
            | Gimp.ProcedureSensitivityMask.NO_DRAWABLES)
        proc.add_menu_path("<Image>/Filters/imanganation")
        proc.set_attribution("imanganation", "imanganation", "2026")
        proc.add_layer_return_value(
            "layer", "Layer", "The placed panel layer", False, GObject.ParamFlags.READWRITE)

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
                "Take the next already-rendered panel from the project's panels.json "
                "(reading order) and fit it into the selected frame, with its dialogue "
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
