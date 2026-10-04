# imanganation — Implementation Plan

AI manga generation: accept a **script** (standard manga production practice), then
generate **one consistent panel at a time**, always in **colour**, with
**multi-character** support, running **locally** on a 16 GB RTX 5060 Ti.

## Confirmed Requirements

| Requirement | Decision |
|---|---|
| Output | **Colour only.** B&W/screentone is an artist post-process in GIMP (see `docs/color-policy.md`) |
| Rendering | ComfyUI, local (16 GB RTX 5060 Ti, torch 2.13.0+cu130) |
| Interface | **GIMP plug-in** (primary); engine API as the shared core |
| Multi-character | Required in MVP |
| Scripting | Standard manga production practice (page/panel-based) |
| Character references | Optional; system generates designs when absent |
| Page composition | **Engine renders one panel per image and never composites.** Pages are built **by hand in GIMP**: the artist's template, frame by frame |

## Stack

- **Render engine:** ComfyUI (uv-managed venv), driven via HTTP API — one panel per job
- **Base checkpoints:** NoobAI-XL (primary) → Illustrious-XL, Animagine XL 4.0 (fallbacks); SDXL, fp16
- **Output style:** colour only; single colour style (`config/styles/default_color.yaml`)
- **Consistency:** IP-Adapter Plus (SDXL) + optional FaceID; optional per-character LoRA
- **Multi-character:** IPAdapter Regional Conditioning + Attention Couple (`ComfyUI-ppm`); 3+ via img2img
- **Auto-masking:** SAM2 segmentation (optional) or manual regions
- **LLM:** Ollama (local model) for script parsing
- **Backend:** FastAPI (uv) — job API consumed by the GIMP plug-in
- **Frontend:** GIMP plug-in; optional minimal web status page

**VRAM scheduling:** parse script with the LLM first, unload, then hand the GPU to SDXL.
Never run LLM and diffusion concurrently on 16 GB.

## Architecture

```
GIMP plug-in ──HTTP──> FastAPI job API ──> ComfyUI Renderer (per panel, frame-sized) ──> ComfyUI
(stdlib only)              │
                           ├──> Character memory (img-memory)
                           └──> Layout/staging (R→L, camera, regions)

CLI ──> Script parser (Ollama) ──> panels.json      CLI ──> character designs / refs
```

No web frontend: GIMP is the interface for page work, the CLI for project setup.
See `docs/gimp-plugin.md` for where each former web-UI feature lives.

## Directory Layout

```
imanganation/
  config/            settings.yaml, models.yaml, styles/*.yaml
  src/manganation/
    script/          parser.py (Ollama), schema.py (PanelSpec), formats/mangaplay.py
    characters/      registry.py (img-memory), design.py (ref-less design), versions.py
    render/          comfy_client.py, graphs.py (ComfyUI graphs), panel.py (frame-sized renders),
                     refiner.py (two-pass hi-res fix of an existing panel)
    layout/          regions.py (multi-character regions)
    memory/          store.py, pages.py
    web/             api.py (FastAPI job API for the GIMP plug-in)
    cli.py
  workflows/         ComfyUI JSON: single-char, multi-char regional, upscale
  gimp/imanganation/ GIMP plug-in (primary interface)
  projects/<name>/   script.md, characters/, panels/, manifest.json
```

## Script Format (standard manga practice)

Manga has no single formal spec; practice is consistent: **the page is the unit of
pacing; scripts are page/panel-numbered prose.**

Canonical input:

```
PAGE 7
[SCENE: School rooftop — afternoon]

Panel 1: Wide shot. Akira sits alone, eating lunch.
AKIRA: Finally, some peace and quiet.

Panel 2: Close-up, Akira's head snaps up.
SFX: BANG
YUKI: There you are!
```

Tokens: `PAGE n`, `Panel n:`, `[SCENE: ...]`, `[FLASHBACK START/END]`,
`CHARACTER: line`, `SFX:`, `[[notes]]`, `CUT TO:`.

- **Reading order:** R→L default (manga); L→R for manhwa/Western. Reading order only
  sequences the panels — **each panel is rendered as its own standalone image**, never
  composited into a page.
- **Accept plain prose too** → Ollama normalizes to canonical `PanelSpec` JSON (pydantic-validated).
- **Mangaplay / Fountain+** (`# Page N`, `Panel N`) supported as a machine-readable import.
- **Dialogue is parsed for continuity but never rendered** (no speech bubbles).

### PanelSpec (core schema)

```
page, panel, reading_order, scene_heading, location,
characters[], action, camera/shot, expressions{},
dialogue[] (unrendered), sfx[], notes, flashback,
color_mode (inherit|color|bw — recorded, inert; engine renders colour), aspect_ratio, refs{}, seed
```

## Character Consistency (reference optional)

- **With references:** per-character images → IP-Adapter Plus, regionally masked per character.
- **Without references:** generate a **design sheet** first (portrait/expression), lock it as
  that character's reference, then reuse across all panels — a self-referential chain.
- **img-memory:** per-character folder with versions (`base`/`variant`/`evolution`), lineage
  markers, trait manifest. Recurring locations/props stored the same way.
- **Multi-character panels:** regional IP-Adapter + Attention Couple so each character's
  features bind to its region without bleeding.

## Phases

| Phase | Deliverable |
|---|---|
| **0. Env + spike** | ComfyUI (uv), SDXL manga checkpoint + LoRA + IP-Adapter + Attention Couple; render text-free panels from a reference — proves 16 GB |
| **1. Script layer** | `PanelSpec` + Ollama parser (prose + panel format) → `panels.json` — **DONE** (see `docs/phase1.md`) |
| **2. Character memory** | img-memory registry, reference-less design generation, versioning — **DONE** |
| **3. Renderer backend** | frame-sized panel renderer, prompt builder, single-char + IP-Adapter, colour output, job API — **mostly done** (`render/panel.py`, `web/api.py`); `workflows/` JSON templates not used yet |
| **4. Multi-char + story loop** | regional IP-Adapter/Attention Couple, per-panel seed/continuity, regenerate-one — **DONE** (see `docs/phase4.md`) |
| **5. GIMP plug-in** | click a frame in the artist's template → render the next panel into it; spec parasite; lettering reference layers — **core flow DONE** (see `docs/gimp-plugin.md`); Regenerate Panel, Refine Panel (hi-res, Phase 6a), Inpaint Selection (`docs/inpaint.md`) and Set Character Reference built; inpaint uses crop-and-stitch; refine never compounds (scale relative to the render); next: Engine Status |
| **6. Optional** | per-character LoRA training, batch chapters, GIMP-side B&W/tone actions, DiffSensei backend spike |
| **6a. Panel refinement** | two-pass hi-res fix (low-denoise polish + Real-ESRGAN anime upscale) → `_hires` take — **DONE** (see `docs/phase6a.md`) |
| **6b. Dataset synthesis** | varied per-character training set from the design sheet (img2img) — **SPIKE DONE**; found+fixed multi-figure design sheets (see `docs/phase6b.md`). LoRA trainer still to build. |

**MVP = Phases 0–5.**

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Multi-character feature bleed (3+) | regions + Attention Couple; 3+ via img2img, not txt2img |
| SDXL quality below FLUX | offer quantized FLUX.1-dev + manga LoRA as alt checkpoint later |
| ComfyUI on Blackwell / cu130 | install recent ComfyUI; torch already cu130 — verified in Phase 0 |
| LLM + SDXL VRAM contention | strict sequential scheduling |
| No reference → drift | design-sheet-first chain + IP-Adapter lock |
