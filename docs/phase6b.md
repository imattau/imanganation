# Phase 6b — Per-Character Dataset Synthesis (LoRA feasibility spike)

**Status: SPIKE COMPLETE — path proven viable, one root-cause bug found and fixed.**
Goal was to answer: *can we produce 15–30 varied, identity-consistent training images
per character from the single generated design sheet, before committing to building a
LoRA trainer?* Answer: **yes**, with caveats.

## Why this was the risky unknown

A per-character LoRA needs 15–30 varied images. imanganation has exactly **one**
generated design sheet per character (`characters/<slug>/base.png`). Training on 1–2
near-identical synthetic images overfits to the pose/background, so dataset synthesis
is a **prerequisite** for any trainer — not an optional extra.

## What was built

| Piece | Where |
|---|---|
| Variant planner (pure, deterministic cartesian walk of angle/expression/pose/shot/background) | `characters/dataset.py` |
| img2img graph (LoadImage → VAEEncode → KSampler → VAEDecode) | `render/graphs.py::img2img` |
| Dataset builder (renders variants, writes `NN.png` + `NN.txt` captions + `dataset.json`) | `characters/dataset_generator.py` |
| Tuning knobs | `config/settings.yaml` → `defaults.dataset` |
| CLI | `manganation character dataset <project> [--name] [--count] [--denoise] [--seed]` |

Output is a kohya/sd-scripts-style folder (`projects/<name>/datasets/<slug>/`) with
comma-tag captions whose **identity tags lead** and variation tags follow, so a future
LoRA binds to the person, not the pose.

## The finding: design sheets were silently multi-figure

The first dataset attempt produced garbage — every variant was the same *two-figure*
composition (a girl in a skirt on the left, Akira in a blazer on the right). The
variation axes barely registered.

Root cause: **the base design sheet itself was malformed.** The registered Akira prompt
is a long trait list (`… white dress shirt …, navy pleated trousers, grey blazer and
red necktie, …`) with `solo, 1 character` buried *after* the traits. SDXL resolved
those outfit tokens as a **design sheet of multiple figures** rather than one character.

Diagnosed live:

- Stronger negatives alone → the girl became a *second boy*; still two figures.
- The fix that worked → move single-figure control to the **front** of the prompt and
  add `solo focus` + explicit head-count as the first tokens.

`characters/design.py` now leads every design prompt with
`solo focus, solo, 1 character, <1boy|1girl>` (`DESIGN_LEAD`) and the negative bans
`2people, 2boys, 2girls`. Verified across two seeds: clean single figure, correct
outfit, stable identity.

## What the spike proved

- **Viable**: 6 variants rendered in ~60–75 s (~10–12 s each), all a clean single
  figure of the same character with real variation in head angle, expression, framing
  and background. 24 variants ≈ 4–5 min per character.
- **Deterministic**: the same seed reproduces the same set; captions are stable.
- **Fixed a latent quality bug** that also affected panel rendering (design sheets are
  the IP-Adapter reference, so multi-figure seeds corrupted every downstream panel).

## Caveats / next

- **Seed background artifacts propagate.** At denoise 0.6 the variants inherit the
  seed's shadow/background. A cleaner seed (or a background-removal pass) would raise
  dataset quality. Consider generating a *new, clean* solo seed per variant run rather
  than reusing a possibly-noisy base.
- **Denoise is the identity↔variation dial.** 0.6 kept identity well; lower = less
  variation, higher = more drift. Worth a small sweep before training.
- **Curation is manual.** The builder writes all variants; a quality filter (face
  count, identity embedding distance) is the obvious next addition.
- **Trainer not built.** This was the prerequisite. Next phase would be an isolated
  `kohya-ss/sd-scripts` install consuming this dataset — see the feasibility study in
  the session notes (16 GB is the "comfortable" SDXL tier; our cu130 torch already
  clears the old Blackwell/cu128 hurdle).
