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

**Writing a script:** [`docs/script-template.md`](./docs/script-template.md) explains the
format (cast block, pages, scenes, panels, dialogue, SFX), with a complete example to
copy in [`docs/script-template.txt`](./docs/script-template.txt).

**Rendering policy:** the engine renders colour only; B&W is the artist's post-process
in GIMP. See [`docs/color-policy.md`](./docs/color-policy.md).

**Phase 5 — core flow working.** In GIMP: click a frame in your page template, run
*Imanganation → Render Panel into Frame*, and the next script panel is
rendered to that frame's shape and dropped in. See [`docs/gimp-plugin.md`](./docs/gimp-plugin.md).

**Phase 6a — COMPLETE.** Finished panels enlarge cleanly: a low-denoise img2img polish
at native size followed by a Real-ESRGAN anime upscale, saved as a `_hires` take. See
[`docs/phase6a.md`](./docs/phase6a.md).

**Phase 6b — SPIKE DONE.** Varied per-character training sets can be synthesised from
the design sheet (img2img). The spike also found and fixed multi-figure design sheets
that were silently corrupting references. See [`docs/phase6b.md`](./docs/phase6b.md).

**Inpaint — engine COMPLETE.** Repaint only a masked region of a panel (the engine half
of the GIMP *Inpaint Selection*). See [`docs/inpaint.md`](./docs/inpaint.md).

**Render-accuracy eval.** `manganation eval run config/eval/rooftop.yaml` renders a
script's panels at fixed seeds and scores them with an anime tagger. Did the picture get
the right people, pose, expression and setting? See [`docs/eval.md`](./docs/eval.md).

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
uv run manganation install-comfyui  # pinned ComfyUI + the right PyTorch for your GPU
uv run manganation setup          # fetch the models (~15 GB; see below)
./scripts/comfy.sh start          # launch local ComfyUI (models auto-wired)
uv run manganation doctor         # check GPU / ComfyUI / Ollama / models
uv run python scripts/phase0_spike.py

# Phase 1: parse a script into panels.json
uv run manganation script parse tests/fixtures/rooftop_canonical.md -p rooftop
uv run manganation script parse my_story.md -p my_story   # prose -> LLM parser
```

### ComfyUI

`uv run manganation install-comfyui` builds the ComfyUI imanganation is tested with
in `vendor/ComfyUI`: the commits pinned in `config/comfyui.yaml`, the custom node the
engine uses (ComfyUI_IPAdapter_plus), every package at its tested version
(`config/comfyui-constraints.txt`), and imanganation's patches. Needs `git` and `uv`.

- **PyTorch for your GPU:** NVIDIA is read from `nvidia-smi`: the newest CUDA build
  your driver supports that runs your GPU (RTX 50xx needs a CUDA 12.8+ driver; it says
  so if yours is older). AMD (ROCm) and Apple Silicon are detected too; `--gpu` overrides.
  SDXL wants about 12 GB of VRAM.
- Re-running is safe and quick: finished steps are skipped. `--check` reports what would
  change; local edits to the checkouts are refused unless `--force`.
- It ends with a check that PyTorch sees the GPU and ComfyUI starts with the IP-Adapter
  nodes loaded.

### Models

Models aren't distributed with imanganation. `uv run manganation setup` lists what the
current settings need (`config/models.yaml`), shows each licence, and downloads what's
missing, checkpoint first so you can render as soon as it lands:

| For | Size |
|---|---|
| Rendering (NoobAI-XL v1.1 checkpoint, required) | 7.1 GB |
| Characters staying on-model (IP-Adapter Mark 1 + CLIP-ViT-bigG encoder) | 5.1 GB |
| Keep composition (NoobAI ControlNet canny) | 2.5 GB |
| Hi-res upscale (Real-ESRGAN anime 6B) | 18 MB |

- Already have some of these in ComfyUI or A1111? `manganation setup --from
  ~/ComfyUI/models` finds them by content (any file name) and links them in, using no
  extra disk space.
- Downloads resume if interrupted, and every file is checked against its SHA-256
  before it's used. Run `setup` again to retry.
- `--check` only reports; `--verify` re-hashes files already in place; `-y` skips the
  question. `HF_TOKEN` and `HF_ENDPOINT` (a Hugging Face mirror) are honoured.
- Setup also points `config/comfyui_extra_model_paths.yaml` at your models folder.
- In GIMP the same thing is **Imanganation → Set Up Models…**, which opens by itself on
  first start when the checkpoint is missing.

## GIMP (the interface)

Imanganation runs in a customised GIMP (`imanganation-gimp`). Users get it as one
Flatpak with the plug-in, engine and ComfyUI built in: install it, open GIMP, and Set
Up Models installs the renderer and models (see
[`packaging/flatpak/README.md`](./packaging/flatpak/README.md)). For development, with
a stock or self-built GIMP:

```bash
ln -s "$PWD/gimp/imanganation" ~/.config/GIMP/3.2/plug-ins/imanganation   # once
uv run manganation serve          # engine API on 127.0.0.1:8790 (needs ComfyUI)
```

Then in GIMP: name your page template's frame-lines layer `Template`, click inside a
frame with Fuzzy Select, and run *Imanganation → Render Panel into Frame…*.
