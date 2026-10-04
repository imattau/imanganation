# Phase 1 — Script Layer

**Status: COMPLETE.** A script (canonical page/panel format *or* free prose) is
parsed into a validated list of `PanelSpec` objects, written as `panels.json`.

## What was built

| Module | Purpose |
|---|---|
| `script/schema.py` | Canonical data model: `Script`, `PanelSpec`, `DialogueLine`, `ReadingOrder`, `ColorMode` (recorded-only; engine is colour-only). Pydantic-validated; JSON round-trip. |
| `script/formats/mangaplay.py` | **Deterministic tokenizer** for the canonical token grammar. No LLM, instant, lossless. |
| `script/llm.py` | `OllamaClient` — thin HTTP wrapper (chat, structured JSON, `unload` for VRAM release). |
| `script/parser.py` | **Ollama parser** for prose. Cheap-first routing: canonical → tokenizer, prose → LLM. One repair retry on validation failure. |
| `project.py` | Project layout helpers (`projects/<name>/`, `panels.json`). |
| `cli.py` | `manganation script parse` command. |

## Input formats

**Script (deterministic path).** Pages of panels, each panel in labelled sections,
parsed by plain Python. The user guide is [script-template.md](script-template.md), with
a full example in [script-template.txt](script-template.txt):

```
[CHARACTERS]
AKIRA: 17, boy, messy black hair with blue tips, amber eyes, school uniform.
YUKI (aka Yuki-chan): 16, girl, silver bob with pink tips, sailor uniform.

PAGE 1
[SCENE: School rooftop — afternoon]

PANEL 1
[SHOT: wide shot]
[CHARACTERS: Akira]
[EXPRESSIONS: Akira: content]
[LOCATION: the fence]
[ACTION]
Akira sits alone, eating lunch.
[DIALOGUE]
AKIRA: Finally, some peace and quiet.
AKIRA (thought): Not again...
[SFX]
BANG
[NOTES]
any production note
```

- Every line sits under a label, so nothing is guessed from a line's shape (a colon in
  an action line is action). Lines that break the format come back as `problems`
  (line number and message); New Project from Script shows them, the CLI prints them.
- `[SCENE: …]` and `[FLASHBACK START/END]` apply to the panels after them;
  `[LOCATION: …]` overrides one panel's place.
- **Characters in a panel:** `[CHARACTERS: …]` when given (empty = nobody); otherwise
  the speakers (never narration) plus cast members named in the action, as written or
  in capitals. Aliases resolve to the cast name everywhere.
- **Names normalised:** `AKIRA` → `Akira`. The cast block's descriptions are the
  author's design and win over the LLM when traits are derived.
- Dialogue is captured but **never rendered** (text-free panels).
- The parser is `script/formats/canonical.py`, standard library only, so the GIMP
  plug-in imports the same file (`gimp/imanganation/script_canonical.py` links to it).

**Prose (LLM path).** Free-form story text is normalised by the local model,
constrained by a **JSON schema** (Ollama structured outputs), then pydantic-validated.
If validation fails once, a repair turn is appended and retried.

Routing is automatic: a text with `PAGE` / `PANEL` lines is a script and never
touches the LLM. A `[CHARACTERS]` block is read the same way on both paths; the LLM
only sees the story.

## Interface

```bash
manganation script parse <script.md> [--project NAME] [--out PATH] [--title T]
                                  [--force-llm] [--json]
```

Writes `projects/<name>/panels.json` (or alongside the input). `--json` prints to
stdout instead.

## Verified

- **15 pytest cases** green (`tests/test_script.py`): schema, tokenizer, routing,
  LLM path (stubbed), project helpers.
- **Canonical path:** `rooftop_canonical.md` → 5 panels / 2 pages, characters,
  camera, dialogue kinds, notes and `CUT TO` location all correct.
- **Prose path (live):** `rooftop_prose.md` → 9 panels via `qwen3.5:latest` in ~86 s,
  with camera, characters and structured action; model unloaded after parsing.

## Notes / decisions

- **Reading order** defaults to RTL (manga); LTR configurable in `settings.yaml`.
- **VRAM:** the parser calls `OllamaClient.unload()` when
  `scheduling.unload_before_render` is set, so the LLM frees the GPU before rendering.
- **One panel per image** (per project decision): `page` is only a grouping/pacing
  label; panels are never composited.

## Next

Phase 2 — character image-memory registry + reference-less design generation.
