# imanganation

AI manga generation: give it a **script**, get **consistent, text-free manga panels**
generated one at a time — always rendered in **colour** (B&W/screentone is an artist
step in GIMP), with multi-character support, running **locally** via ComfyUI.

See [`PLAN.md`](./PLAN.md) for the full architecture and roadmap.

## Status

**Phase 0 — COMPLETE.** The local render stack is proven on the 16 GB RTX 5060 Ti:
text-free panels render, and IP-Adapter gives reference-based character consistency
without any LoRA training. See [`docs/phase0.md`](./docs/phase0.md).

**Phase 1 — COMPLETE.** Scripts (canonical page/panel format *or* free prose) parse
into a validated panel list. See [`docs/phase1.md`](./docs/phase1.md).

**Phase 2 — COMPLETE.** Character img-memory registry + reference-less design sheets.

**Phase 4 — COMPLETE (first cut).** Multi-character panels bind a reference per
character to its own canvas region. See [`docs/phase4.md`](./docs/phase4.md).

**Rendering policy:** the engine renders colour only; B&W is the artist's post-process
in GIMP. See [`docs/color-policy.md`](./docs/color-policy.md).

**Phase 5 — core flow working.** In GIMP: click a frame in your page template, run
*Filters → imanganation → Render Panel into Frame*, and the next script panel is
rendered to that frame's shape and dropped in. See [`docs/gimp-plugin.md`](./docs/gimp-plugin.md).

**Phase 6a — COMPLETE.** Finished panels enlarge cleanly: a low-denoise img2img polish
at native size followed by a Real-ESRGAN anime upscale, saved as a `_hires` take. See
[`docs/phase6a.md`](./docs/phase6a.md).

**Phase 6b — SPIKE DONE.** Varied per-character training sets can be synthesised from
the design sheet (img2img). The spike also found and fixed multi-figure design sheets
that were silently corrupting references. See [`docs/phase6b.md`](./docs/phase6b.md).

**Inpaint — engine COMPLETE.** Repaint only a masked region of a panel (the engine half
of the GIMP *Inpaint Selection*). See [`docs/inpaint.md`](./docs/inpaint.md).

Next: Phase 5 follow-ups (Inpaint Selection UI, regenerate, character reference from layer).

## Layout

```
config/     settings, model registry, style presets
src/        Python orchestrator (script, characters, layout, render, memory, web)
workflows/  ComfyUI workflow JSON templates
gimp/       GIMP 3 plug-in (the interface) + headless smoke tests
projects/   per-project scripts, characters, panels, manifests
models/     checkpoints, LoRAs, IP-Adapters, ControlNets
```

## Quick start (dev)

```bash
uv sync
./scripts/comfy.sh start          # launch local ComfyUI (models auto-wired)
uv run manganation doctor         # check GPU / ComfyUI / Ollama
uv run python scripts/phase0_spike.py

# Phase 1: parse a script into panels.json
uv run manganation script parse tests/fixtures/rooftop_canonical.md -p rooftop
uv run manganation script parse my_story.md -p my_story   # prose -> LLM parser
```

## GIMP (the interface)

```bash
ln -s "$PWD/gimp/imanganation" ~/.config/GIMP/3.2/plug-ins/imanganation   # once
uv run manganation serve          # engine API on 127.0.0.1:8790 (needs ComfyUI)
```

Then in GIMP: name your page template's frame-lines layer `Template`, click inside a
frame with Fuzzy Select, and run *Filters → imanganation → Render Panel into Frame…*.
