# Engine API (for the GIMP plug-in and other clients)

`uv run manganation serve` (127.0.0.1:8790). One worker thread, so one GPU job at a
time; clients poll `GET /jobs/{id}`. Project containers: `docs/project-container.md`.

## Render a panel: `POST /jobs`

Two forms; send exactly one.

### Inline (project container): the spec travels in the request

```json
{
  "project": "prj_cd3562f1216d",
  "panel": { "...": "a container panel object, as in project.json" },
  "reading_order": "rtl",
  "frame_width": 1040, "frame_height": 640,
  "seed": 4242
}
```

- `panel` is the container panel as-is: `label`, `characters: [{name, version}]`,
  `action`, `camera`, `expressions`, … (`project_container.panel_to_spec`). Edits made
  in GIMP (e.g. adding a character the parser missed) apply directly. The engine
  never reads `panels.json` for this form.
- `characters[].version` selects that reference version (e.g. an outfit); null means
  the character's active reference. An unknown version fails the job, listing the
  known versions. It never falls back silently to another look.
- Identity comes from the project's identity store: `projects/identities.json` maps
  `project` → a character registry folder (`manganation/identity.py`). Imported
  projects map to their legacy folder; `manganation project link <container> <name>`
  links any other container to an existing cast. An unmapped project gets an empty
  store, so it renders without references rather than failing.
- **Output:** the engine writes to its own cache,
  `outputs/<project>/<panel id>-<random>.png` (+ sidecar), and never into the
  container. The job result carries what the plug-in records as the take:

```json
{"path": ".../outputs/prj_…/pnl_…-e97e8bc2e83f.png", "panel_id": "pnl_…",
 "seed": 4242, "width": 1344, "height": 832, "prompt": "…",
 "references": {"Yuki": "active", "Akira": "base"}, "reference": "Yuki,Akira"}
```

  The plug-in copies the file into `takes/` (a new immutable take, `kind: render`,
  `engine` = these fields) and sets `active_take`.

### Placements: where each character goes

Both forms accept `"placements": {"Yuki": "/abs/…/tmp/placement_yuki.png", …}`: a mask
per character (frame-shaped, any size; white or opaque = where that character goes),
such as an artist's placement layer exported from GIMP. The engine normalises each
mask, scales it to the render canvas, softens its edges, and uses it as that
character's regional reference mask instead of the default reading-order band.
Characters without a placement keep their band. A placement for someone not in the
panel, or an empty mask, fails the job; paths follow the container path rules. Results
record `placements` (character → mask file). In a multi-character panel every
reference stays masked, even when only one character has a reference, so it can't
pull the others' faces.

### Legacy: `project_dir` + `seq`

```json
{"project_dir": ".../projects/rooftop", "seq": 3, "frame_width": 500, "frame_height": 560}
```

Panel `seq` of `panels.json`, written to `projects/<name>/panels/`. Kept working while
the plug-in moves to containers.

Malformed requests (neither form, both, half of the inline form, a bad project id,
an invalid panel spec) are rejected with 422 before anything is queued.

## Refine: `POST /refine`

Container form. The take's history is in the project, so the caller sends it:

```json
{"project": "prj_…", "source": "/abs/…/takes/pnl_…-tk_….png",
 "origin_width": 1344, "origin_height": 832, "prompt": "<the origin render's prompt>",
 "scale": 2.0, "denoise": 0.25, "seed": 0}
```

- `origin_width/height` is the take's `origin` take size (`project.json`). The target
  is origin × scale, so refining an already-enlarged take at the same scale is refused
  ("already … at or beyond 2× its render"); scales never compound.
- `prompt` is the origin render's `engine.prompt` (an inpaint's patch prompt must not
  steer the polish). Omitted: the bare style prompt.
- Output: `outputs/<project>/<source stem>-hires-<random>.png`; record it as a
  `refine` take with `parent` = the source take.

Legacy: `project_dir` + `seq` (+ optional `source`); origin and prompt come from
sidecars (`docs/phase6a.md`).

## Inpaint: `POST /inpaint`

```json
{"project": "prj_…", "source": "/abs/…/takes/….png", "mask": "/abs/…/masks/….png",
 "prompt": "surprised face, open mouth", "characters": ["Yuki"],
 "character_weight": 0.45, "denoise": 0.85, "grow_mask_by": 8}
```

Crop-and-stitch, with outside-mask pixels kept exactly (`docs/inpaint.md`). Output:
`outputs/<project>/<source stem>-inpaint-<random>.png`; record it as an `inpaint` take
(`parent` = source, `engine.mask` = the mask copied into `masks/`).
**Character identity:** `characters` names who is in the patch (names, or
`{"name", "version"}`). Their appearance traits follow the artist's prompt, and with
exactly one character, IP-Adapter guides the patch with their reference (`version`
honoured; `character_weight` overrides `defaults.inpaint.ipadapter_weight`, 0.45).
With several characters, traits only. Only *appearance* traits are used, never the
character's `default_expression` or `mannerisms`, so the prompt decides the expression.
Unknown characters or versions fail, listing what exists. Leave `characters` out for
props and backgrounds. Recommended plug-in default: the panel's characters as choices,
none preselected for a two-shot.

## Characters

`GET /characters?project=prj_…` lists the project's cast from its identity store.
`POST /characters/reference` takes `project` instead of `project_dir`. Both accept
`project_dir` too (legacy). An unmapped project id has an empty cast.

## Paths in container forms

`source`, `mask` and `image_path` must be existing files under the projects root
(where containers live) or the engine's `outputs/` (a fresh result not yet copied into
`takes/`). Anything else is a 400 (outside) or 404 (missing).

## Other endpoints

| Endpoint | Purpose |
|---|---|
| `GET /jobs/{id}` | Job status: `queued` / `running` / `done` / `error`, with `result` or `error` |
| `POST /refine`, `POST /inpaint` | Above |
| `GET /characters` | The cast, by `project` or legacy `project_dir` |
| `POST /characters/reference` | Register an image as a character's new active reference version |
| `GET /status` | ComfyUI/GPU health, running/queued/recent jobs (by project + panel id), missing models |
| `GET /health` | Liveness |

Every endpoint now accepts a project id. The legacy `project_dir` forms remain until
the plug-in has moved to containers.
