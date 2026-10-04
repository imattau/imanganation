# Project container (GIMP-owned) — schema draft

Status: **draft v1**, 2026-10-04. Target for the imanganation-gimp fork's project work
and the Python plug-in. Machine-readable: [`project-container.schema.json`](./project-container.schema.json)
(JSON Schema 2020-12). Worked example: [`project-container.example.json`](./project-container.example.json).

## The split this encodes

**GIMP owns the story and the document; the engine owns identity and style.**

| In the project container (GIMP) | In the engine (consistency memory) |
|---|---|
| Script source and the parsed panels | Character traits, reference versions, (later) LoRAs |
| Reading order, pages, which panel sits on which page | Style (checkpoint, style prefix, IP-Adapter, weights) |
| Every take of every panel, which one is active, and its history | Models, GPU queue, ComfyUI workflows |
| Story continuity: *who* appears in a panel and *which version* (outfit) | Recurring locations/props, if added |
| The place-next cursor | |

The container **names** characters (and the version to use per panel). It never stores
their traits or reference images: those live in the engine, keyed by `project.id`.
Docks that show character details fetch them from the engine (`GET /characters`).

The downstream GIMP C changes stay project-agnostic: they provide host-rendered dock
widgets and an IPC/PDB boundary. The Python plug-in is the owner of `project.json`,
schema-aware loading/saving, and dock content. It registers its own PDB procedures for
dock actions and item activation; the host sends row ids to those procedures.

## On disk

```
rooftop.imanga/            recommended project folder suffix
  project.json             the manifest (this schema)
  script/rooftop.md        the source script, as given
  pages/page-001.xcf       the artist's pages (one image per page)
  takes/                   every generated image; immutable, never overwritten
  masks/                   inpaint masks, kept for provenance
```

- `.imanga` is the recommended folder suffix. It is a directory containing
  `project.json`, not a single-file archive; tools should still identify a project
  by its valid manifest so users can rename folders freely.
- All paths in the manifest are **relative to the project folder**, using `/`.
- **Take files are immutable.** A new image always gets a new file and a new take id.
  Overwriting the file under a take silently changes what layers and derived takes
  point at, and has already made a take history loop once.
- **Writes are atomic:** write `project.json.tmp`, then rename over `project.json`.
- **Unknown keys are preserved** on load/save, so newer writers don't lose data when an
  older reader saves.

## IDs, not labels

Every panel, page and take has a **stable opaque id**: `pnl_…`, `pg_…`, `tk_…`, 6 or
more `[a-z0-9]` characters after the prefix. These ids are what UIs pass around,
including dock row activation. Script numbers (`page 2, panel 1`) are **labels only**.
They are not unique: the rooftop script legitimately has two "page 2, panel 1" entries
(after `CUT TO:`). The array order of `panels` is the script/reading order.

> Fork note: extension-panel list, tile, and tree rows use `id<TAB>label`. The host
> renders the label and returns the stable id for selection and activation. Label-only
> rows remain supported for simple panels.

### Re-parsing and orphaned panels

Keep orphaned panels in a visible **Needs matching** group, with the original script
label, first line of action text, page placement, and take count. Offer an explicit
**Match to…** command that lists newly parsed panels with their page/panel labels and
short action text; show the take count and active take before confirming. Permit
multiple old panels to remain unmatched. Do not silently map by label or position:
labels may repeat and script edits can shift positions. Matching changes the script
association while retaining panel ids, immutable take files, and take history. An
ambiguous suggestion may be highlighted, but requires an explicit artist choice.

## Manifest sections

### `format`, `version`
`"format": "imanganation.project"`, `"version": 1`. Readers refuse a higher major
version, and preserve unknown keys within the same version.

### `project`
| Field | Notes |
|---|---|
| `id` | **The engine's key for this project's consistency memory.** Generated once (e.g. `prj_` + 12 chars), never changes, even if the folder is renamed. |
| `title`, `chapter` | Display only. |
| `reading_order` | `rtl` (manga) or `ltr`. |
| `default_color_mode` | Recorded only; the engine renders colour (`docs/color-policy.md`). |
| `created`, `modified` | ISO 8601 timestamps. |

### `script`
Where the panels came from: `file` (relative path), `sha256` of the source text (to
detect edits since parsing), `format` (`canonical` / `prose` / `mangaplay`),
`parsed_at`, and `parser` (e.g. `{"kind": "llm", "model": "qwen3.5:latest"}`).
Re-parsing creates **new panels** and keeps old ones that have takes, marked
`"status": "orphaned"`, so placed work is never silently dropped.

### `panels` (ordered)
The panel spec, the same fields the engine's `PanelSpec` uses, plus identity and
production state.

| Field | Notes |
|---|---|
| `id` | `pnl_…` |
| `label` | `{"page": 2, "panel": 1}` from the script; display only |
| `scene_heading`, `location`, `action`, `camera`, `notes`, `flashback` | as in `PanelSpec` |
| `characters` | `[{"name": "Yuki", "version": "summer"}]`. `version` is optional (the engine's active reference otherwise). This is how story continuity reaches the engine. |
| `expressions` | `{"Akira": "sighing"}` |
| `dialogue`, `sfx` | Never rendered. Lettering reference only. |
| `aspect_ratio` | A hint for panels rendered before a frame exists. A frame's real size wins. |
| `seed` | Optional pinned seed. |
| `status` | `unplaced`, `placed` or `orphaned` |
| `placement` | `{"page": "pg_…", "frame": [x, y, w, h]}` once placed. The page id is the source of truth for "which page is this panel on". |
| `takes` | Take ids for this panel, oldest first |
| `active_take` | The take the page shows, or null |

### `pages` (ordered)
`id`, `label` (e.g. `"Page 1"`), `file` (relative `.xcf`). No panel list: that's
derived from `panels[].placement.page`, so it can't drift.

### `takes` (map: take id → take)
Replaces today's per-image sidecar files. Provenance lives here, by id.

| Field | Notes |
|---|---|
| `panel` | Owning panel id |
| `file`, `width`, `height` | The immutable image, relative path |
| `kind` | `render` (first or regenerated), `refine`, `inpaint`, `import` (an outside image) |
| `parent` | The take it was made from (refine/inpaint), else null |
| `origin` | The root `render`/`import` take of the chain. **Refine scales against the origin's size, never the parent's**, so scales never compound. |
| `created` | ISO 8601 |
| `engine` | What the engine did. `render`: `seed`, `prompt`, `frame` (requested w×h), `references` (character → version used). `refine`: `scale`, `upscaler`, `denoise`, `seed`. `inpaint`: `prompt`, `positive`, `mask` (relative path), `crop`, `work_size`, `denoise`, `grow_mask_by`, `seed`. |

Integrity rules: `origin` is reachable by following `parent`; `parent` chains never
loop; `origin` is a `render` or `import` take; a take derives only from takes of its
own panel.

### `cast`, `locations`, `props`
Story-side lists. `cast` entries: `name`, `aliases`, `notes` (story notes, e.g. "Yuki
is energetic, always grinning"). **No traits or images**, which are engine state.
`locations` and `props` hold `name` and `notes` now, with room for engine refs later
(recurring-location consistency).

### `cursor`
`{"next_panel": "pnl_…"}`. The place-next/render-next pointer (today's
`gimp_cursor.json`).

## XCF linkage

Pages keep only **references** in parasites; the manifest holds the data, so nothing
drifts between files:

- Image parasite `imanganation-project`: `{"project": "prj_…", "page": "pg_…"}`
- Panel-group parasite `imanganation-panel`: `{"project": "prj_…", "panel": "pnl_…"}`
- Layer parasite `imanganation-take`: `{"project": "prj_…", "panel": "pnl_…", "take": "tk_…"}`

The panel-group reference makes a panel's layer group a stable canvas object; individual
render layers still identify their take. These parasites carry identity only.

(Replaces today's `imanganation-panelspec` layer parasite, which embeds a full spec
copy.) To find the manifest from an open XCF, walk up from the XCF's folder to the
first `project.json` whose `project.id` matches.

## Engine contract changes (follow-on)

The container makes engine requests **self-contained**:

- `POST /jobs` (render): send the panel spec inline plus `project` (the id),
  `characters[].version`, `frame` and `seed`. The engine no longer reads `panels.json`.
- `POST /refine`: send the source image plus the **origin's size**, which replaces the
  engine's sidecar walk.
- `POST /inpaint`: as now (source + mask).
- Results: the engine returns the image (bytes, or a path in its own output cache).
  The plug-in copies it into `takes/` and records the take. The engine never writes
  into the project folder.
- Character endpoints take `project` (the id) to scope identity.

The current `seq` / `project_dir` forms keep working during migration.

## Migration from today's layout

`projects/<name>/` maps one-to-one:

| Today | Container |
|---|---|
| `panels.json` | `script` + `panels` (ids generated; script `page/panel` → `label`) |
| `panels/NNN*.png` + sidecar `.json` | `takes/` + `takes{}` (`seq` → panel id; sidecar `source` → `parent`; refine `origin` → `origin`; inpaint `mask` → `engine.mask`) |
| `gimp_cursor.json` | `cursor` |
| `characters.json`, `characters/` | **stays engine-side**; `cast` keeps names/aliases only |
| layer parasite with full spec | `imanganation-take` reference |

A one-shot importer can do this. The rooftop example file was hand-converted this way.

### Current plug-in transition

The Python plug-in now reads the container cursor by panel id and writes it atomically.
The zero-argument persistent Python workspace extension starts with GIMP, restores the
last selected project, and registers Project, Script, Inspector, Panel, Page Filmstrip,
and Character Bible by default. On first launch its Project dock offers **Open project…**;
the selected project path is stored in GIMP's user settings. **Open / Switch Project…**
can load another project from the Filters menu. The docks render from the manifest; panel,
page, and cast rows
send stable keys over the PDB. Script rows follow panel reading order, show placement and
take counts, and group orphaned work under Needs matching; selecting a row synchronizes
the same panel in the other docks. **Match selected** matches one orphan to one untouched
current panel after showing the old take count and active take; the original panel id,
placement, and take history remain attached to the updated script entry. Panel presents
the selected script panel as a production brief:
action, cast/version, location, shot, dialogue, sound effects, and status. Cast, locations,
and props are grouped under Assets; location and prop notes are shown as non-activating
summaries because those manifest entries do not yet have IDs. The Character Bible keeps
project cast, aliases, and story notes separate from engine-owned versions and
reference-image state. Project shows the chapter when available and repeats the filmstrip's
page status cues in the page tree. **Add page** asks for dimensions (defaulting to the
first existing page), creates a white XCF, records it atomically, and opens it. Activating
a page in Project or Page Filmstrip opens its XCF and raises the already-open page display
when the plug-in created it earlier in the session. Activating a placed panel opens its
page and selects its stable panel group, falling back to the active-take layer for older
XCFs without a panel-group reference.
Inspector's Refresh action also syncs the dock selection from a selected take layer in the
current canvas image back to that panel's stable project identity.
The Panel dock's **Set frame** action turns the active image selection into a persistent
canvas object for the selected script panel: it creates a tagged panel group, stores the
selection as the group's mask in the XCF, and atomically writes `placement.page` plus the
selection's bounding rectangle to the manifest. **Render Panel** and **Place Next Panel**
use that saved rectangle when the active XCF is the referenced project page, so a live
selection is not needed to fit the take. The group mask preserves the original selection
shape. Rendering a new panel directly from a selection on a linked project page also
records its first placement in the same atomic manifest write as its take. Save the XCF
after setting the frame or rendering so the group and its mask persist with the manifest
placement. This first slice requires a page image opened from the project and does not
yet reshape an existing panel group.
For container projects it also copies newly rendered, refined, and inpainted images into
unique files under `takes/`, adds a new take id, and advances `active_take`; a legacy
image used from `panels/` is first imported as an immutable take. Opened page images and
manifest-backed rendered/refined/inpainted layers carry reference-only XCF parasites.
The currently shipped engine still reads `panels.json` and writes its own compatibility
files under `panels/`. The plug-in checks that both panel lists have matching counts and
display labels before rendering. Manifest-only rendering and page placement references
remain follow-on migration steps.

## Validation

- **Shape:** `project-container.schema.json` (JSON Schema 2020-12). It also rejects
  identity fields in `cast`, refine/inpaint takes without a parent, renders with one,
  absolute or `..` paths, and labels used as ids.
- **Cross-references** (what JSON Schema can't express):
  `manganation.project_container.integrity_errors(doc, root=None)`. It checks unique
  ids, panel ↔ take ownership, `active_take`, placement pages, the cursor, parent
  chains (no loops, end at the declared origin) and, given the project folder, that
  every referenced file exists. Pure Python, so the fork (via the Python plug-in), the
  engine and an importer can share it. Tested against the example in
  `tests/test_project_container.py`.
