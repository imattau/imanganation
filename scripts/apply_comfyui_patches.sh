#!/usr/bin/env bash
# Apply the local patches imanganation needs on top of a fresh ComfyUI checkout.
# Re-run after pulling ComfyUI, or when bootstrapping a new machine.
#
#   1. comfy/clip_vision.py — always return the full hidden-state stack.
#      The IPAdapter node requests the penultimate hidden states (-2) for
#      ViT-H. Current ComfyUI only populates them for the siglip path, so the
#      SDXL ViT-H IP-Adapters otherwise receive the 1664-wide pooled output and
#      fail with "size mismatch ... proj_in.weight [1280,1664]".
#   2. custom_nodes/ComfyUI-ppm — its Anima negpip path imports a symbol removed
#      from ComfyUI. Guard the import; the SDXL/Atenion-Couple path is unaffected.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CV="$ROOT/vendor/ComfyUI/comfy/clip_vision.py"
PP="$ROOT/vendor/ComfyUI/custom_nodes/ComfyUI-ppm/src/negpip/anima_negpip.py"

python3 - "$CV" "$PP" <<'PY'
import sys
cv, pp = sys.argv[1], sys.argv[2]

s = open(cv).read()
old = """        if self.model_type == "siglip_vision_model":
            self.return_all_hidden_states = True
        else:
            self.return_all_hidden_states = False"""
new = """        self.return_all_hidden_states = True"""
if old in s:
    open(cv, "w").write(s.replace(old, new))
    print(f"patched {cv}")
elif new in s:
    print(f"already patched {cv}")
else:
    print(f"WARNING: pattern not found in {cv}; check ComfyUI version")

s = open(pp).read()
old = """from comfy.ldm.cosmos.predict2 import Attention as CosmosAttention
from comfy.ldm.cosmos.predict2 import apply_rotary_pos_emb"""
new = """from comfy.ldm.cosmos.predict2 import Attention as CosmosAttention

try:
    from comfy.ldm.cosmos.predict2 import apply_rotary_pos_emb
except ImportError:
    # This symbol was removed from ComfyUI; only the Anima negpip path uses it.
    def apply_rotary_pos_emb(*args, **kwargs):
        raise RuntimeError(
            "apply_rotary_pos_emb is unavailable in this ComfyUI build; "
            "the Anima negative-prompt path is unsupported"
        )"""
if old in s:
    open(pp, "w").write(s.replace(old, new))
    print(f"patched {pp}")
elif "def apply_rotary_pos_emb" in s:
    print(f"already patched {pp}")
else:
    print(f"WARNING: pattern not found in {pp}; check ComfyUI-ppm version")
PY
