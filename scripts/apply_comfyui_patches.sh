#!/usr/bin/env bash
# Re-apply imanganation's ComfyUI patches (e.g. after pulling ComfyUI by hand).
# The full install, which also does this, is: uv run manganation install-comfyui
# The patches themselves live in src/manganation/comfy_setup.py (PATCHES).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec uv run python -c '
import sys
from manganation.comfy_setup import apply_patches
from manganation.config import REPO_ROOT, load_settings
for line in apply_patches(REPO_ROOT / load_settings().paths.comfyui_dir):
    print(line)
'
