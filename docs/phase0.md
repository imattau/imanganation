# Phase 0 — Environment + render-stack spike (COMPLETE)

Goal: prove the local ComfyUI manga stack fits the 16 GB RTX 5060 Ti and
produces text-free panels in **both colour and B&W**, with **character
consistency via IP-Adapter**, before building the orchestration layer.

## Result — PASS

Rendered end-to-end on 2026-10-03:

| Output | Size | Mode |
|---|---|---|
| `projects/_spike/panels/01_colour.png` | 1024×1024 | colour |
| `projects/_spike/panels/02_bw.png` | 1024×1024 | B&W / manga screentone |
| `projects/_spike/characters/hero_design.png` | 768×1024 | character design sheet |
| `projects/_spike/panels/04_ipadapter.png` | 1024×1024 | IP-Adapter-driven panel |

All panels are text-free. The IP-Adapter panel was generated from the
`hero_design.png` reference (no LoRA training), confirming reference-based
character consistency.

## Environment

- GPU: RTX 5060 Ti, 16 GB (Blackwell sm_120)
- CUDA: 13.0; PyTorch 2.14.1+cu130 in ComfyUI's venv
- ComfyUI: 0.38.0, cloned to `vendor/ComfyUI`, venv at `vendor/ComfyUI/.venv`
- Ollama: up; using existing `qwen3.5:latest` for script parsing

## Models (in `models/`, wired via `config/comfyui_extra_model_paths.yaml`)

| Role | File | Size |
|---|---|---|
| Checkpoint | `checkpoints/noobaiXL.safetensors` (NoobAI-XL v1.1, SDXL) | 6.7 GB |
| IP-Adapter | `ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors` | 848 MB |
| IP-Adapter (face) | `ipadapter/ip-adapter-plus-face_sdxl_vit-h.safetensors` | 848 MB |
| CLIP vision | `ipadapter/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` (ViT-H/1280) | 2.5 GB |

B&W was achieved by prompt/LoRA (`manga, monochrome, greyscale, screentone`);
colour by the plain checkpoint. A dedicated manga LoRA is still TODO.

> Updated 2026-10-04: the engine is now **colour-only**; B&W/screentone moved to a
> GIMP-side artist step. See [`color-policy.md`](./color-policy.md).

## Custom nodes

- `ComfyUI_IPAdapter_plus` — character consistency + regional conditioning
- `ComfyUI-ppm` — `AttentionCouplePPM` for multi-character regional prompting
- (removed `ComfyUI_essentials` — heavy optional deps, not needed yet)

## Patches applied to ComfyUI (`scripts/apply_comfyui_patches.sh`)

1. **`comfy/clip_vision.py`** — force `return_all_hidden_states = True` for all
   vision encoders. The IPAdapter node needs ViT-H penultimate hidden states
   (`-2`); current ComfyUI only populates them for siglip, so SDXL ViT-H
   IP-Adapters fail with `proj_in.weight [1280,1664]`.
2. **`ComfyUI-ppm`** — guard the Anima-only `apply_rotary_pos_emb` import
   (symbol removed from ComfyUI); the SDXL Attention-Couple path is unaffected.

Re-apply after any `git pull` in `vendor/ComfyUI`.

## VRAM observation

SDXL + IP-Adapter at 1024×1024 fits 16 GB with ComfyUI's default dynamic
offload. B&W 1024² took ~11 s, colour ~16 s. LLM and diffusion must not run
concurrently (sequential scheduling in `settings.yaml`).

## Lifecycle

- `scripts/comfy.sh start|stop|restart|status|log`
- `uv run python scripts/phase0_spike.py` — re-run the spike

## Next (Phase 1)

Script layer: `PanelSpec` schema (done) + Ollama parser (prose and
standard page/panel manga script) → `panels.json`.
