# Plug-in commands: what to support next

Status: **proposal**, 2026-10-04. Covers the eight commands already built, the
engine features GIMP can't reach yet, and a design for **character sheets**.

## Where we are

| In GIMP today | Engine-only (CLI), no GIMP surface |
|---|---|
| Render Panel into Frame, Place Next Panel, Place Panel | `script parse` (script → panels.json) |
| Regenerate, Inpaint Selection, Refine (hi-res) | `character suggest` (LLM derives traits from the script) |
| Set Character Reference from Layer | `character design` (generate a reference) |
| Engine Status | `character list/show`, trait editing (by hand in JSON) |
| | reference **version switching** (`registry.set_default`, no CLI) |
| | `character dataset` (LoRA training set) |

The gap: a project can be *worked* in GIMP but not *started* or *cast* there.
Characters especially are JSON-and-CLI only, though most consistency problems so far
(multi-view references, unspecified skin tone, shadowed backdrops) were character
problems.

## Design: character sheets

A manga **model sheet** is the artist's reference for a character: turnaround (front,
3/4, side, back), an expression set, outfit variants, colour notes.

### The constraint that shapes it

**A sheet must never be the IP-Adapter reference.** IP-Adapter copies composition
along with identity, so a multi-view sheet renders panels as grids of repeated
figures (`docs/quality/2026-10-04_live_quality_check.md`). So:

- The **sheet** is a GIMP document for the artist: each view is its **own
  single-figure render on its own layer**.
- The **reference** stays one square single figure, chosen from the sheet with the
  existing *Set Character Reference from Layer*.

### Command: *Character Sheet…*

1. Dialog: character (validated against `GET /characters`), sections (turnaround /
   expressions / candidates), seed.
2. Engine job `POST /characters/{name}/sheet` renders each view separately:
   - **Turnaround and expressions**: txt2img with the character's appearance tags +
     view tags (`front view`, `from side, profile`, `from behind`; `smile`, `angry`…:
     the axes already in `characters/dataset.py`), guided by the current reference
     through IP-Adapter Mark 1 at a low weight, on a flat background.
     txt2img + IP-Adapter, not img2img from the reference: img2img keeps the
     reference's pose, so a "back view" comes out as the front.
   - **Candidates** (for a new or unhappy character): N references from different
     seeds, *without* IP-Adapter, to choose a look from.
   - Outputs go to `characters/<slug>/sheet/NN_<view>.png` + sidecars (prompt, seed,
     reference version used). Nothing overwrites a reference version.
3. The plug-in opens a **new image** "Akira — character sheet": one named layer per
   view in a simple grid, a text label under each, and a notes text layer with the
   traits. Only the plug-in places these; it's a reference board, not a manga page.
4. The artist curates: hides or deletes bad views, paints fixes, then uses *Set
   Character Reference from Layer* on the best front view, or keeps the sheet as
   their drawing reference.

Cost: about 4 turnaround + 6 expression views at ~12 s each ≈ 2 min per character;
4 candidates ≈ 50 s.

**Engine work:** a sheet job (reusing `graphs.txt2img` + `with_ipadapter`,
`design.py` prompt rules: solo-focus lead, flat background, square), a
`/characters/{name}/sheet` endpoint, and a sheet view spec module (pure, testable,
like `dataset.py`).

## Other proposed commands

Ordered by value for the work so far.

| # | Command | What it does | Engine work |
|---|---|---|---|
| 1 | **Edit Panel…** | Dialog with the selected panel's spec (characters, camera, action, expressions) → writes `panels.json`; then *Regenerate* (same seed keeps the composition). Also the in-GIMP fix for parser misses (panel 5 lacking Akira). | `PATCH /panels/{seq}`, schema-validated, keeps a backup |
| 2 | **Character Sheet…** | Above. | Sheet job + endpoint |
| 3 | **Edit Character Traits…** | Dialog for appearance fields (gender, hair, eyes, **skin**, build, outfit, accessories, distinguishing marks, aliases). Every future render's prompt changes. Pairs with *Character Sheet → candidates*. | `PATCH /characters/{name}` |
| 4 | **Switch Character Reference…** | Choose which saved version is active (`base`, `gimp-01`…). Undoes a bad *Set Reference*. | `POST /characters/{name}/default` (wraps `set_default`) |
| 5 | **New Project from Script…** | Pick a script file + project name → parse (canonical or prose via the LLM) → suggest the cast → optional sheets. Starts a project without the CLI. | `POST /projects` job (parse + suggest; LLM before diffusion, never both on 16 GB) |
| 6 | **Convert to Manga B&W** | Already promised by `docs/color-policy.md`: Desaturate → Curves on a new layer, non-destructive. Pure GIMP. | none |
| 7 | **Character Variant…** | Outfit / evolution versions (`variant`, `evolution` kinds exist in the schema), then choose which version a panel uses. | Panel → character-version mapping in `PanelSpec.refs`, renderer honours it |
| 8 | **Show/Hide Lettering Reference** | Toggle the hidden dialogue/SFX layers across the page. | none |

Not proposed: automatic lettering and export (GIMP already exports PNG/PDF).

## Suggested order

1. **Edit Panel** (small, immediately useful, fixes parser misses in-app).
2. **Character Sheet** with **Edit Traits** and **Switch Reference**: together they
   make characters workable in GIMP.
3. **New Project from Script**: closes the loop so nothing needs the CLI.
4. **Convert to Manga B&W**, **Variants**, lettering toggle.
