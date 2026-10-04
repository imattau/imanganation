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

### Legacy: `project_dir` + `seq`

```json
{"project_dir": ".../projects/rooftop", "seq": 3, "frame_width": 500, "frame_height": 560}
```

Panel `seq` of `panels.json`, written to `projects/<name>/panels/`. Kept working while
the plug-in moves to containers.

Malformed requests (neither form, both, half of the inline form, a bad project id,
an invalid panel spec) are rejected with 422 before anything is queued.

## Other endpoints

| Endpoint | Purpose |
|---|---|
| `GET /jobs/{id}` | Job status: `queued` / `running` / `done` / `error`, with `result` or `error` |
| `POST /refine` | Hi-res fix (`docs/phase6a.md`); `source` = exact take; scale relative to the original render |
| `POST /inpaint` | Masked repaint with crop-and-stitch (`docs/inpaint.md`) |
| `GET /characters?project_dir=` | The cast (legacy, by folder) |
| `POST /characters/reference` | Register an image as a character's new active reference version |
| `GET /status` | ComfyUI/GPU health, running/queued/recent jobs (by project + panel id), missing models |
| `GET /health` | Liveness |

Not yet migrated to project ids: `/refine` and `/inpaint` (still `project_dir` +
`seq`, with paths inside it) and the character endpoints (`project_dir`). Next step:
accept `project` + source/mask paths in the engine's `outputs/` or a container.
