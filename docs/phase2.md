# Phase 2 — Character Image-Memory

**Status: COMPLETE.** A script's cast is turned into a persistent visual-identity
registry: appearance traits (derived from the script when absent) and reference
images, so the same character recurs across every panel.

## What was built

| Module | Purpose |
|---|---|
| `characters/schema.py` | `Character`, `AppearanceSpec`, `CharacterVersion`, `Cast`. Pydantic; versions + lineage; JSON round-trip. |
| `characters/registry.py` | Persistent img-memory: `characters.json` + `characters/<slug>/base.png` + per-character `manifest.json`. `from_path()` for path-based callers. |
| `characters/design.py` | Pure design-sheet **prompt builder** (no GPU, unit-tested). |
| `characters/traits.py` | LLM trait derivation from the script; merges traits already known. |
| `characters/generator.py` | Renders a design sheet via ComfyUI and registers it as the `base` version. |
| `characters/cast.py` | Orchestration: script → registry (`assemble_cast`). |
| `cli.py` | `character suggest / design / list / show / add-ref`. |

## Reference-optional consistency

- **Reference supplied:** register it with `character add-ref` (stored as the `base`
  version, no generation).
- **Reference absent (default):** derive appearance traits with the LLM, render a clean
  full-body **design sheet**, and lock it as `base`. Every later panel reuses it through
  IP-Adapter — a self-referential chain that keeps identity stable **without LoRA
  training**.

The renderer (Phase 3) resolves a single-character panel's reference through this
registry (`render/panel.py:find_reference` → `characters/<slug>/base.png`) and now also
injects the character's `AppearanceSpec` tags into the panel prompt.

## Versions & lineage

A character holds multiple `CharacterVersion`s (`base` / `variant` / `evolution`), each
with the prompt, seed and parent it came from. `default_version` selects which one
panels use. Re-designing with `--force` **replaces** the version in place.

## CLI

```bash
uv run manganation character suggest rooftop      # derive cast from panels.json + script.md
uv run manganation character design  rooftop      # render design sheets (reference-less)
uv run manganation character list    rooftop
uv run manganation character show    Akira -p rooftop
uv run manganation character add-ref Akira ref.png -p rooftop
```

## Verified

- **19 tests** in `tests/test_characters.py`: schema, registry, prompt builder,
  trait derivation (stubbed), cast assembly.
- **Live:** derived *Akira* and *Yuki* from the rooftop script (~95 s) and rendered
  design sheets (~26 s each); a full panel render used `characters/akira/base.png`
  through IP-Adapter.

## Notes / decisions

- Design prompts target a **solo, full-body, front-view** sheet with a plain background;
  NoobAI still occasionally splits into two figures, tunable via prompt/negative.
- Design sheets are **colour** (engine is colour-only — see
  [`color-policy.md`](./color-policy.md)). This also removed the old colour-bleed
  conflict between reference and monochrome prompt.
- Multi-character panels do **not** yet attach references (Phase 4: regional
  IP-Adapter / Attention Couple); they render unguided for now.

## Next

Phase 4 — multi-character regional references + per-panel continuity loop.

## Appearance vs expression (2026-10-04)

`AppearanceSpec` keeps **identity** (physical: gender, age, hair, eyes, skin, build,
outfit, accessories, physical `distinguishing` marks, visual `descriptors`) apart from:

- `default_expression`: the characteristic face ("wide toothed grin"). Renders use it
  only when the panel gives no expression for that character; a panel's `expressions`
  always wins. Design references include it; inpaint and training captions don't.
- `mannerisms`: body language and demeanor ("relaxed slouch", "energetic stance").
  Never in render or inpaint prompts (the panel's action decides the pose).

| Method | Returns |
|---|---|
| `appearance_tags()` | identity only |
| `prompt_tags()` | identity + default expression |
| `prompt_tags(expression=False)` | identity |
| `prompt_tags(mannerisms=True)` | identity + default expression + mannerisms |

**Why:** stored as identity, Yuki's "wide toothed grin" overrode "surprised face, open
mouth" in inpaint (even at IP-Adapter 0.3) and fought panel expressions in renders.
Live, same seed: as scripted she grins; with `expressions: {"Yuki": "surprised, open
mouth"}` she's surprised (`docs/quality/2026-10-04_render_expression_override.png`).

**Migration:** older manifests (and LLM slips) stored these in `distinguishing` /
`descriptors`. A validator moves them on load (`classify_trait`: body-language words
first, then expression words), and the next save writes the split. rooftop and
_gimp_smoke are migrated. The LLM trait prompt now asks for the two fields directly.

