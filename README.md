# imanganation

AI manga generation: give it a **script**, get **consistent, text-free manga panels**
generated one at a time — always rendered in **colour** (B&W/screentone is an artist
step in GIMP), with multi-character support, running **locally** via ComfyUI.

See [`PLAN.md`](./PLAN.md) for the architecture and roadmap.

## What it does

- **Scripts in, panels out.** Write a script in the canonical page/panel format *or* free
  prose; it parses into a validated panel list. The format (cast, locations, pages,
  scenes, panels, dialogue, SFX) is explained in
  [`docs/script-template.md`](./docs/script-template.md), with a complete example to copy
  in [`docs/script-template.txt`](./docs/script-template.txt).
- **Consistent characters.** A character registry keeps reference images per character,
  with design sheets generated from a description alone. IP-Adapter gives on-model
  results without any LoRA training, and multi-character panels bind a reference to each
  character's own region of the canvas.
- **Colour rendering, artist-finished.** The engine renders colour only; B&W and
  screentone are the artist's post-process in GIMP. See
  [`docs/color-policy.md`](./docs/color-policy.md).
- **Works inside GIMP.** Click a frame in your page template and render the next script
  panel to that frame's shape. Inpaint a masked region, develop a panel in stages as
  transparent layers, steer poses with an OpenPose library, and add dialogue and SFX
  lettering. See [`docs/gimp-plugin.md`](./docs/gimp-plugin.md) and
  [`docs/inpaint.md`](./docs/inpaint.md).
- **Print-ready enlargement.** Finished panels get a low-denoise polish and an anime
  upscale, saved as a `_hires` take. See [`docs/phase6a.md`](./docs/phase6a.md).
- **Render-accuracy eval.** `manganation eval run config/eval/rooftop.yaml` renders a
  script's panels at fixed seeds and scores them with an anime tagger: did the picture
  get the right people, pose, expression and setting? See [`docs/eval.md`](./docs/eval.md).
- **Local and private.** Everything runs on your own GPU through ComfyUI; nothing is sent
  to a cloud service.

Design notes and experiment write-ups live in [`docs/`](./docs).

## Layout

```
config/     settings, model registry, style presets
src/        Python orchestrator (script, characters, layout, render, memory, web)
workflows/  ComfyUI workflow JSON templates
gimp/       GIMP 3 plug-in (the interface) + headless smoke tests
android/    Android script companion (early development)
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

# parse a script into panels.json
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
- Optional engines are fetched only when asked: `--engine qwen_image_21` (Qwen-Image
  2.1, ~17 GB, non-commercial licence), `--engine z_anime`, `--engine face_pass`
  (repeatable). Choose one per project in GIMP under **Render Engine…**.
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
