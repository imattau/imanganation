"""Configuration loading (settings.yaml + models.yaml)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


class Paths(BaseModel):
    projects_dir: str = "projects"
    models_dir: str = "models"
    workflows_dir: str = "workflows"
    comfyui_dir: str = "vendor/ComfyUI"

    def resolve(self, value: str) -> Path:
        return REPO_ROOT / value


class ComfyUIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8188
    base_url: str = "http://127.0.0.1:8188"
    checkpoint_subdir: str = "checkpoints"
    lora_subdir: str = "loras"
    ipadapter_subdir: str = "ipadapter"


class LLMConfig(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:14b-instruct"
    unload_before_render: bool = True


class SchedulingConfig(BaseModel):
    mode: str = "sequential"
    gpu_index: int = 0


class PanelDefaults(BaseModel):
    width: int = 768
    height: int = 768
    steps: int = 30
    cfg: float = 7.0
    sampler: str = "dpmpp_2m"
    scheduler: str = "karras"


class IPAdapterDefaults(BaseModel):
    """Reference strength. Lower weight = less colour bleed/saturation, more promptable.
    Tune against over-saturated character renders."""

    # Role in models.yaml -> ipadapter. Each adapter names the CLIP-vision encoder it
    # was trained with. The base-SDXL "plus" adapter breaks on NoobAI (tiled, glowing
    # panels); "noob_mark1" is NoobAI's own (docs/quality/2026-10-04_live_quality_check.md).
    adapter: str = "noob_mark1"
    weight_single: float = 0.45
    weight_regional: float = 0.5
    feather: int = 48  # mask feather in pixels for regional blending
    # Per-character text in hard canvas bands (ConditioningSetAreaPercentage). 0 = off
    # (default): live, the bands split two-shots into side-by-side pictures (even a
    # divider line) and at 0.4 still put a skirt on Akira; without them the scene is one
    # composition and each face is held by its own regional reference. Raise it to trade
    # composition for fine per-character traits (e.g. Akira's blue hair tips).
    regional_text: float = 0.0


class RefinerDefaults(BaseModel):
    """Two-pass hi-res fix of a finished panel (docs/phase6a.md).

    A diffusion upscale would be another full sampling pass. Instead we use
    ``realesrgan`` (fast, detail-preserving, no hallucination), then a very low
    ``denoise`` img2img polish that removes upscaler speckle while leaving identity
    intact. ``denoise: 0`` skips the polish and returns the pure upscale.
    """

    enabled: bool = True
    upscaler: str = "realesrgan"  # models.yaml -> upscalers role
    scale: float = 2.0  # target multiplier of the source panel
    denoise: float = 0.2  # img2img polish strength (0 = pure upscale)
    steps: int = 16
    cfg: float = 5.0
    max_pixels: int = 4096 * 4096  # refuse to exceed ~16 MP (VRAM/time guard)


class DatasetDefaults(BaseModel):
    """Synthetic per-character training-set generation (docs/phase6b.md).

    Variants are re-rendered from the character's design sheet with img2img so the
    identity is kept while pose/expression/background vary. ``denoise`` trades
    identity fidelity (low) against variation (high).
    """

    count: int = 24  # images to plan per character (15-30 is the LoRA sweet spot)
    denoise: float = 0.6  # img2img strength for variant generation
    steps: int = 28
    cfg: float = 6.0


class InpaintDefaults(BaseModel):
    """Masked repaint of a region of an existing panel (docs/inpaint.md).

    Used by the GIMP plug-in's *Inpaint Selection*: a selection becomes a mask, the
    active layer becomes the init image, and only the masked region is re-synthesised
    from a short prompt. ``denoise`` must be high (the mask region is regenerated from
    noise); ``grow_mask_by`` dilates the mask to blend the seam.
    """

    denoise: float = 0.85  # high: the masked region is re-synthesised, not nudged
    grow_mask_by: int = 8  # dilate the mask so the patch blends into its surroundings
    # Crop-and-stitch: inpaint a crop around the mask at ~1 MP, not the whole take.
    context: float = 0.5  # context added on each side, as a fraction of the mask's size
    min_crop: int = 256  # smallest crop side (source px), so tiny masks see surroundings
    # Named characters: their traits join the prompt; with exactly one, IP-Adapter uses
    # their reference so a repainted face stays on-model.
    ipadapter_weight: float = 0.45  # live: 0.45 kept identity and followed "surprised face"
    steps: int = 28
    cfg: float = 6.0


class ControlNetDefaults(BaseModel):
    """Keep composition: a render guided by the edges of an existing take.

    Strength and the step window trade "same layout and poses" against "free to redraw":
    ending early lets the last steps change a face or an expression."""

    model: str = "canny"  # models.yaml -> controlnets role
    strength: float = 0.7
    start: float = 0.0
    end: float = 0.7  # fraction of steps the guide applies to
    low_threshold: float = 0.4  # Canny edge thresholds (0.01-0.99)
    high_threshold: float = 0.8


class Defaults(BaseModel):
    color_mode: str = "color"  # recorded only; engine renders colour (docs/color-policy.md)
    reading_order: str = "rtl"
    panel: PanelDefaults = PanelDefaults()
    ipadapter: IPAdapterDefaults = IPAdapterDefaults()
    refiner: RefinerDefaults = RefinerDefaults()
    dataset: DatasetDefaults = DatasetDefaults()
    inpaint: InpaintDefaults = InpaintDefaults()
    controlnet: ControlNetDefaults = ControlNetDefaults()


class Settings(BaseModel):
    paths: Paths = Paths()
    comfyui: ComfyUIConfig = ComfyUIConfig()
    llm: LLMConfig = LLMConfig()
    scheduling: SchedulingConfig = SchedulingConfig()
    defaults: Defaults = Defaults()


@lru_cache
def load_settings() -> Settings:
    path = CONFIG_DIR / "settings.yaml"
    data = yaml.safe_load(path.read_text()) or {}
    return Settings(**data)


def load_models() -> dict:
    return yaml.safe_load((CONFIG_DIR / "models.yaml").read_text()) or {}
