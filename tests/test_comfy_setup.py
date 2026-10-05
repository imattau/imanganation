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


def _no_libcuda(name):
    raise OSError(f"{name}: not found")


class FakeCuda:
    """libcuda.so.1 as ctypes sees it: each call fills its byref() arguments."""

    def __init__(self, version=13000, name=b"NVIDIA GeForce RTX 5060 Ti", cc=(12, 0),
                 memory=16 * 1024 ** 3, init=0):
        self.values = {"version": version, "name": name, "cc": cc, "memory": memory}
        self.init = init

    def cuInit(self, flags):
        return self.init

    def cuDriverGetVersion(self, out):
        out._obj.value = self.values["version"]

    def cuDeviceGetCount(self, out):
        out._obj.value = 1
        return 0

    def cuDeviceGet(self, out, index):
        out._obj.value = index

    def cuDeviceGetName(self, buffer, size, device):
        buffer.value = self.values["name"]

    def cuDeviceGetAttribute(self, out, attribute, device):
        out._obj.value = self.values["cc"][0 if attribute == 75 else 1]

    def cuDeviceTotalMem_v2(self, out, device):
        out._obj.value = self.values["memory"]


def test_without_nvidia_smi_the_driver_library_tells_the_gpu():
    """The Flatpak sandbox has libcuda (the NVIDIA driver extension) but no nvidia-smi."""
    def nothing(cmd):
        raise FileNotFoundError

    gpu = cs.detect_gpu(nothing, libcuda=lambda name: FakeCuda())
    assert (gpu.backend, gpu.name, gpu.driver_cuda, gpu.capability, gpu.vram_gb) == (
        "cuda", "NVIDIA GeForce RTX 5060 Ti", "13.0", "12.0", 16.0)
    assert cs.torch_index(gpu, PINS).endswith("/cu130")
    assert cs.detect_gpu(nothing, libcuda=lambda n: FakeCuda(version=12080)).driver_cuda == "12.8"
    failed = cs.detect_gpu(nothing, libcuda=lambda name: FakeCuda(init=100))  # no device
    assert failed.backend in ("cpu", "mps")


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

    rocm = cs.detect_gpu(amd, libcuda=_no_libcuda)
    assert rocm.backend == "rocm" and rocm.name == "Radeon RX 7900 XTX"

    def nothing(cmd):
        raise FileNotFoundError

    assert cs.detect_gpu(nothing, libcuda=_no_libcuda).backend in ("cpu", "mps")


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



# --- bundled (the Flatpak): read-only patched code, venv in the data folder -----------


def _bundled_comfy(tmp_path: Path) -> Path:
    comfy = tmp_path / "app/vendor/ComfyUI"
    (comfy / "comfy").mkdir(parents=True)
    (comfy / "comfy/clip_vision.py").write_text("class C:\n" + cs.PATCHES[0].new + "\n")
    (comfy / "requirements.txt").write_text("torch\n")
    (comfy / cs.BUNDLED_MARKER).write_text("")
    return comfy


def _fake_torch(venv: Path, version: str) -> None:
    meta = venv / "lib/python3.12/site-packages" / f"torch-{version.split('+')[0]}.dist-info"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "METADATA").write_text(f"Metadata-Version: 2.1\nName: torch\nVersion: {version}\n")


def test_installer_for_picks_the_data_folder_for_bundled_code(tmp_path, monkeypatch):
    from manganation.config import load_settings

    monkeypatch.setenv("IMANGANATION_DATA", str(tmp_path / "data"))
    comfy = _bundled_comfy(tmp_path)
    installer = cs.installer_for(load_settings(), comfy=comfy)
    assert installer.bundled and installer.venv == tmp_path / "data/comfyui-venv"
    assert installer.uv_env() == {"UV_PYTHON_INSTALL_DIR": str(tmp_path / "data/python"),
                                  "UV_PYTHON_PREFERENCE": "only-managed"}
    args = installer.comfy_args()
    assert args[:2] == ["--user-directory", str(tmp_path / "data/comfyui/user")]
    assert (tmp_path / "data/comfyui/output").is_dir() and "--temp-directory" in args
    checkout = cs.installer_for(load_settings(), comfy=tmp_path / "checkout")
    assert not checkout.bundled and checkout.venv == tmp_path / "checkout/.venv"
    assert checkout.comfy_args() == [] and checkout.uv_env() == {}


def test_a_bundled_install_makes_a_venv_without_git_or_patching(tmp_path):
    comfy = _bundled_comfy(tmp_path)
    ran = []
    venv = tmp_path / "data/comfyui-venv"

    def run(cmd, cwd=None):
        ran.append(cmd)
        if cmd[:2] == ["uv", "venv"]:
            cs._python_in(venv).parent.mkdir(parents=True)
            cs._python_in(venv).write_text("")
        if "torch" in cmd and "--index-url" in cmd:
            _fake_torch(venv, "2.14.1+cu130")

    installer = cs.Installer(comfy, PINS, run=run, log=lambda *a: None, venv=venv,
                             data=tmp_path / "data/comfyui", bundled=True)
    plan = installer.plan(_gpu("13.0", "12.0"))
    assert not any(step.startswith(("clone", "move")) for step in plan.steps)
    assert "create a Python 3.12 venv (downloads that Python)" in plan.steps
    installer.install(plan)
    assert not any(cmd[0] == "git" for cmd in ran)
    assert ran[0] == ["uv", "venv", "--python", "3.12", str(venv)]
    assert any("--index-url" in cmd and cmd[-1].endswith("/cu130") for cmd in ran)
    assert cs.is_patched(comfy)  # untouched: it shipped patched
    assert not installer.ready()
    installer.mark_ready()
    assert installer.ready() and installer.torch_build() == "2.14.1+cu130"
    _fake_torch(venv, "2.14.1+cpu")  # PyTorch replaced since: no longer the verified one
    for meta in venv.glob("lib/python*/site-packages/torch-*.dist-info/METADATA"):
        meta.write_text("Version: 2.14.1+cpu\n")
    assert not installer.ready()


def test_an_unpatched_bundle_is_refused(tmp_path):
    comfy = _bundled_comfy(tmp_path)
    (comfy / "comfy/clip_vision.py").write_text("class C:\n" + cs.PATCHES[0].old + "\n")
    installer = cs.Installer(comfy, PINS, run=lambda *a, **k: None, log=lambda *a: None,
                             venv=tmp_path / "v", bundled=True)
    with pytest.raises(cs.InstallError, match="isn't patched"):
        installer.install(installer.plan(_gpu("13.0", "12.0")))
