"""install-comfyui: PyTorch build per GPU, GPU detection, patches, pinned checkouts."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from manganation import comfy_setup as cs

PINS = cs.load_pins()
BASE = "https://download.pytorch.org/whl"


def _gpu(driver, capability, name="GPU"):
    return cs.Gpu("cuda", name, driver, capability, 16.0)


@pytest.mark.parametrize(("driver", "capability", "index"), [
    ("13.0", "12.0", "cu130"),  # RTX 50xx on a current driver
    ("12.9", "12.0", "cu129"),
    ("12.8", "12.0", "cu128"),
    ("12.6", "8.9", "cu126"),  # RTX 40xx on an older driver
    ("13.1", "8.6", "cu130"),
])
def test_the_newest_cuda_build_the_driver_and_gpu_can_run(driver, capability, index):
    assert cs.torch_index(_gpu(driver, capability), PINS) == f"{BASE}/{index}"


def test_a_blackwell_gpu_on_a_cuda_12_6_driver_needs_a_driver_update():
    with pytest.raises(cs.InstallError, match="Update the driver"):
        cs.torch_index(_gpu("12.6", "12.0", "RTX 5060 Ti"), PINS)
    with pytest.raises(cs.InstallError, match="too old"):
        cs.torch_index(_gpu("11.8", "8.6"), PINS)


def test_other_backends():
    assert cs.torch_index(cs.Gpu("rocm", "RX 7900"), PINS) == f"{BASE}/{PINS['torch']['rocm']}"
    assert cs.torch_index(cs.Gpu("cpu"), PINS) == f"{BASE}/cpu"
    assert cs.torch_index(cs.Gpu("mps"), PINS) is None  # PyPI's macOS build has Metal


def test_detect_gpu_reads_nvidia_smi_then_rocminfo():
    def nvidia(cmd):
        if "--query-gpu=name,compute_cap,memory.total" in cmd:
            return "NVIDIA GeForce RTX 5060 Ti, 12.0, 16311\n"
        return "| NVIDIA-SMI 580.173.02  Driver Version: 580.173.02  CUDA Version: 13.0 |"

    gpu = cs.detect_gpu(nvidia)
    assert (gpu.backend, gpu.name, gpu.driver_cuda, gpu.capability, gpu.vram_gb) == (
        "cuda", "NVIDIA GeForce RTX 5060 Ti", "13.0", "12.0", 15.9)

    def amd(cmd):
        if cmd[0] == "nvidia-smi":
            raise FileNotFoundError
        return ("  Marketing Name:          AMD Ryzen 9 CPU\n"
                "  Marketing Name:   Radeon RX 7900 XTX\n")

    assert cs.detect_gpu(amd).backend == "rocm" and cs.detect_gpu(amd).name == "Radeon RX 7900 XTX"

    def nothing(cmd):
        raise FileNotFoundError

    assert cs.detect_gpu(nothing).backend in ("cpu", "mps")


def test_patches_apply_once_and_refuse_changed_code(tmp_path):
    patch = cs.PATCHES[0]
    target = tmp_path / patch.file
    target.parent.mkdir(parents=True)
    target.write_text("class X:\n" + patch.old + "\n")
    assert cs.apply_patches(tmp_path)[0].startswith("patched:")
    assert patch.new in target.read_text() and patch.old not in target.read_text()
    assert cs.apply_patches(tmp_path) == [f"already patched: {patch.file}"]
    target.write_text("class X:\n    pass\n")
    with pytest.raises(cs.InstallError, match="its code changed"):
        cs.apply_patches(tmp_path)


def test_the_pins_are_what_the_engine_uses():
    assert len(PINS["comfyui"]["commit"]) == 40
    assert list(PINS["custom_nodes"]) == ["ComfyUI_IPAdapter_plus"]
    assert all(len(pin["commit"]) == 40 for pin in PINS["custom_nodes"].values())
    graphs = (Path(__file__).parents[1] / "src/manganation/render/graphs.py").read_text()
    assert "IPAdapterRegionalConditioning" in graphs and "AttentionCouple" not in graphs


# --- pinned checkouts, against throwaway local git repos ---------------------------


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _origin(tmp_path: Path) -> tuple[Path, str, str]:
    """A fake ComfyUI upstream with two commits; -> (repo, old commit, new commit)."""
    origin = tmp_path / "origin"
    (origin / "comfy").mkdir(parents=True)
    _git("init", "-q", "-b", "master", cwd=origin)
    _git("config", "user.email", "t@example.com", cwd=origin)
    _git("config", "user.name", "t", cwd=origin)
    (origin / "comfy/clip_vision.py").write_text("class C:\n" + cs.PATCHES[0].old + "\n")
    (origin / "requirements.txt").write_text("")
    _git("add", ".", cwd=origin)
    _git("commit", "-qm", "one", cwd=origin)
    old = _git("rev-parse", "HEAD", cwd=origin)
    (origin / "README").write_text("two\n")
    _git("add", ".", cwd=origin)
    _git("commit", "-qm", "two", cwd=origin)
    return origin, old, _git("rev-parse", "HEAD", cwd=origin)


def _installer(comfy: Path, origin: Path, commit: str) -> cs.Installer:
    pins = {**PINS, "comfyui": {"repo": str(origin), "commit": commit}, "custom_nodes": {}}
    return cs.Installer(comfy, pins, log=lambda *a: None,
                        run=lambda cmd, cwd=None: subprocess.run(
                            cmd, cwd=cwd, check=True, capture_output=True))


def test_checkout_clones_moves_and_keeps_our_patch_from_blocking(tmp_path):
    origin, old, new = _origin(tmp_path)
    comfy = tmp_path / "ComfyUI"
    installer = _installer(comfy, origin, old)
    installer._checkout("ComfyUI", comfy, installer.pins["comfyui"], force=False)
    assert installer.head(comfy) == old
    cs.apply_patches(comfy)  # our patch is a local edit, but not "the user's"
    assert installer.local_edits(comfy) == []

    moved = _installer(comfy, origin, new)
    plan_steps = moved.plan(cs.Gpu("cpu")).steps
    assert any(step.startswith("move ComfyUI") for step in plan_steps)
    moved._checkout("ComfyUI", comfy, moved.pins["comfyui"], force=False)
    assert moved.head(comfy) == new


def test_the_users_own_changes_are_not_overwritten_without_force(tmp_path):
    origin, old, new = _origin(tmp_path)
    comfy = tmp_path / "ComfyUI"
    _installer(comfy, origin, old)._checkout(
        "ComfyUI", comfy, {"repo": str(origin), "commit": old}, force=False)
    (comfy / "requirements.txt").write_text("my-extra-package\n")
    moved = _installer(comfy, origin, new)
    with pytest.raises(cs.InstallError, match="local changes.*requirements.txt"):
        moved._checkout("ComfyUI", comfy, moved.pins["comfyui"], force=False)
    moved._checkout("ComfyUI", comfy, moved.pins["comfyui"], force=True)
    assert moved.head(comfy) == new


def test_a_non_git_folder_in_the_way_is_refused(tmp_path):
    origin, old, _ = _origin(tmp_path)
    comfy = tmp_path / "ComfyUI"
    comfy.mkdir()
    (comfy / "something").write_text("x")
    installer = _installer(comfy, origin, old)
    with pytest.raises(cs.InstallError, match="isn't a git checkout"):
        installer._checkout("ComfyUI", comfy, installer.pins["comfyui"], force=False)


def test_plan_for_a_fresh_machine(tmp_path):
    installer = cs.Installer(tmp_path / "ComfyUI", PINS, capture=lambda *a, **k: "")
    steps = installer.plan(_gpu("13.0", "12.0")).steps
    assert steps[0].startswith("clone ComfyUI") and steps[1].startswith(
        "clone ComfyUI_IPAdapter_plus")
    assert "create a Python 3.12 venv" in steps
    assert "install PyTorch (cu130)" in steps


def test_verify_requires_every_pinned_node_to_load(tmp_path):
    torch_ok = "2.14.1+cu130 | NVIDIA GeForce RTX 5060 Ti\n"
    loaded = ("Import times for custom nodes:\n"
              "   0.0 seconds: /x/custom_nodes/ComfyUI_IPAdapter_plus\n")
    failed = ("Import times for custom nodes:\n"
              "   0.0 seconds (IMPORT FAILED): /x/custom_nodes/ComfyUI_IPAdapter_plus\n")
    good = cs.Installer(tmp_path, PINS, capture=lambda *a: torch_ok,
                        capture_all=lambda *a: loaded)
    assert good.verify() == torch_ok.strip()
    bad = cs.Installer(tmp_path, PINS, capture=lambda *a: torch_ok,
                       capture_all=lambda *a: failed)
    with pytest.raises(cs.InstallError, match="ComfyUI_IPAdapter_plus nodes didn't load"):
        bad.verify()


def test_constraints_pin_everything_but_the_gpu_builds():
    freeze = ("torch==2.14.1+cu130\ntorchvision==0.29.1+cu130\ntorchsde==0.2.6\n"
              "nvidia-cublas==13.1.1.3\ncuda-toolkit==13.0.3.0\ntriton==3.8.0\n"
              "Pillow==12.0.0\naiohttp==3.14.3\n-e git+https://x#egg=y\n")
    lines = [line for line in cs.constraints_from_freeze(freeze).splitlines()
             if not line.startswith("#")]
    assert lines == ["aiohttp==3.14.3", "Pillow==12.0.0", "torchsde==0.2.6"]
    committed = (Path(__file__).parents[1] / "config" / PINS["constraints"]).read_text()
    assert "transformers==" in committed and "\ntorch==" not in committed
