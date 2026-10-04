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

**Canonical (deterministic path).** Token grammar, parsed by plain Python:

```
PAGE 7
[SCENE: School rooftop — afternoon]
[FLASHBACK START] / [FLASHBACK END]

Panel 1: Wide shot. Akira sits alone, eating lunch.
AKIRA: Finally, some peace and quiet.
AKIRA (thought): Not again...
SFX: BANG
CUT TO: the stairwell          -> annotates the NEXT panel's location
[[any production note]]
```

- `PAGE n`, `Panel n:` (also `Panel n -`), `[SCENE: ...]`, `[FLASHBACK START/END]`,
  `CHARACTER:` dialogue, `SPEAKER (kind):` for thought/whisper/shout/narration,
  `SFX:`, `CUT TO:`, `[[notes]]`.
- **Names normalised:** `AKIRA:` → `Akira` (stable key for the character registry).
- **Camera auto-detected** from action prose (wide shot, close-up, dutch angle…).
- Dialogue is captured but **never rendered** (text-free panels).
- **Writing a script:** [script-template.md](script-template.md) is the user guide, with
  a full example in [script-template.txt](script-template.txt).
- **Cast block (optional):** before the first page, `CHARACTERS` (or `CAST`) and one
  unindented `NAME (aka Alias, Other): description` per character; indented lines
  continue a description. Each description is the author's design and wins over the
  LLM when traits are derived (`character suggest`, `POST /characters`).

  ```
  CHARACTERS
  AKIRA: 17, boy, messy black hair with blue tips, amber eyes,
    school uniform with red tie. Quiet loner, slouches.
  YUKI (aka Yuki-chan): 16, girl, silver bob with pink tips, sailor uniform.
  ```
- **Characters in a panel:** its speakers, plus any known character (declared, by name or
  alias, or speaking anywhere in the script) named in its action text as a whole word,
  as written or in capitals ("Yuki drags Akira by the wrist" has both).
- The tokenizer is `script/formats/canonical.py`, standard library only, so the GIMP
  plug-in imports the same file (`gimp/imanganation/script_canonical.py` links to it).

**Prose (LLM path).** Free-form story text is normalised by the local model,
constrained by a **JSON schema** (Ollama structured outputs), then pydantic-validated.
If validation fails once, a repair turn is appended and retried.

Routing is automatic: canonical input never touches the LLM. A cast block is
parsed the same way on both paths; the LLM only sees the story.

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
