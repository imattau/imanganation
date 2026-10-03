"""Phase 0 spike: prove the local ComfyUI manga stack on a 16 GB GPU.

Renders:
  1. a colour panel (text -> image)
  2. a black & white / manga panel (same seed, monochrome style)
  3. a character design sheet, then
  4. a new panel using that design as an IP-Adapter reference (consistency check)

Usage:  uv run python scripts/phase0_spike.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from manganation.render.comfy_client import ComfyClient  # noqa: E402

OUT = ROOT / "projects" / "_spike"
CKPT = "noobaiXL.safetensors"
IPA = "ip-adapter-plus_sdxl_vit-h.safetensors"
CLIP = "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"

NEG = (
    "text, speech bubble, watermark, signature, username, lowres, bad anatomy, "
    "bad hands, extra digits, worst quality, low quality, jpeg artifacts"
)


def base_graph(prompt: str, negative: str, width: int, height: int, seed: int, prefix: str) -> dict:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 28,
                "cfg": 6.0,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        },
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": prefix, "images": ["6", 0]},
        },
    }


def ipadapter_graph(
    prompt: str, negative: str, width: int, height: int, seed: int, prefix: str,
    ref_name: str, weight: float = 0.75,
) -> dict:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "8": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "9": {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": IPA}},
        "10": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": CLIP}},
        "11": {
            "class_type": "IPAdapterAdvanced",
            "inputs": {
                "model": ["1", 0],
                "ipadapter": ["9", 0],
                "image": ["8", 0],
                "clip_vision": ["10", 0],
                "weight": weight,
                "weight_type": "linear",
                "combine_embeds": "concat",
                "start_at": 0.0,
                "end_at": 1.0,
                "embeds_scaling": "V only",
            },
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 28,
                "cfg": 6.0,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
                "model": ["11", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        },
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": prefix, "images": ["6", 0]},
        },
    }


def save(out_dir: Path, name: str, blobs: list[bytes]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_bytes(blobs[0])
    print(f"  saved {path} ({len(blobs[0]) / 1024:.0f} KB)")
    return path


def main() -> None:
    client = ComfyClient()
    if not client.is_up():
        raise SystemExit("ComfyUI is not reachable at 127.0.0.1:8188")
    print("ComfyUI up. Running Phase 0 spike.\n")

    panels = OUT / "panels"
    chars = OUT / "characters"

    colour_prompt = (
        "1boy, solo, swordsman, black hair, hooded cloak, standing on rooftop, "
        "sunset, city skyline, dynamic angle, detailed background, anime screencap"
    )
    print("[1/4] colour panel (1024x1024)...")
    save(panels, "01_colour.png",
         client.run(base_graph(colour_prompt, NEG, 1024, 1024, 1111, "spike_colour")))

    bw_prompt = (
        "1boy, solo, swordsman, black hair, hooded cloak, standing on rooftop, "
        "dramatic angle, manga panel, monochrome, greyscale, screentone, ink drawing, "
        "high contrast, detailed lineart"
    )
    bw_neg = NEG + ", colour, colored, greyscale is fine"
    print("[2/4] black & white manga panel (1024x1024)...")
    save(panels, "02_bw.png",
         client.run(base_graph(bw_prompt, bw_neg, 1024, 1024, 1111, "spike_bw")))

    char_prompt = (
        "1girl, solo, full body, character design sheet, standing, school uniform, "
        "long silver hair, red eyes, front view, arms at sides, white background, "
        "clean lineart, anime"
    )
    print("[3/4] character design sheet (768x1024)...")
    char_path = save(chars, "hero_design.png",
                     client.run(base_graph(char_prompt, NEG, 768, 1024, 2025, "spike_char")))

    uploaded = client.upload_image(str(char_path))
    ref_name = uploaded["name"]
    print(f"  uploaded reference as {ref_name}")

    panel_prompt = (
        "1girl, solo, silver hair, red eyes, school uniform, sitting at desk in classroom, "
        "looking out window, afternoon light, detailed background, anime"
    )
    print("[4/4] IP-Adapter panel from reference (consistency)...")
    save(panels, "04_ipadapter.png",
         client.run(ipadapter_graph(panel_prompt, NEG, 1024, 1024, 3333,
                                    "spike_ipa", ref_name, weight=0.75)))

    print(f"\nDone. Outputs in {OUT}")


if __name__ == "__main__":
    main()
