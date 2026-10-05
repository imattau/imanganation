"""Install the ComfyUI that imanganation renders with: pinned, patched, right PyTorch.

``manganation install-comfyui`` drives this. It builds what ``config/comfyui.yaml``
pins, under ``settings.paths.comfyui_dir`` (``vendor/ComfyUI``):

1. **GPU:** NVIDIA (``nvidia-smi``: driver CUDA version and compute capability), AMD
   (``rocminfo``), Apple (``mps``) or CPU. The PyTorch build follows: the newest CUDA
   build the driver supports that has kernels for the GPU.
2. **ComfyUI** and the custom nodes, cloned and checked out at their pinned commits.
   Local edits are refused unless they are only imanganation's own patches.
3. **A venv** (``uv``, the pinned Python), PyTorch from its wheel index first, then
   ComfyUI's requirements (so PyPI never swaps in a different torch build).
4. **Patches** imanganation needs (``PATCHES``), idempotent.
5. **A check** that PyTorch sees the GPU and ComfyUI starts with every pinned node
   (the command also points ComfyUI's model paths at the models folder).

Every step is skipped when already done, so re-running is safe and quick.

**Bundled** (the Flatpak): ComfyUI's code ships in the app, read-only and already
patched (``BUNDLED_MARKER``), so there's no checkout; the venv, a uv-managed Python and
ComfyUI's writable folders live in the data folder (``config.data_root()``), and the
GPU is read from the driver library because the sandbox has no ``nvidia-smi``.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class InstallError(RuntimeError):
    pass


# Written into ComfyUI's code folder by the Flatpak build: the code is bundled
# (read-only, patched), so install into the data folder instead of the checkout.
BUNDLED_MARKER = ".imanganation-bundled"
READY_MARKER = ".imanganation-ready"  # in the venv: what a verified install holds


# --- patches ------------------------------------------------------------------


@dataclass(frozen=True)
class Patch:
    file: str  # relative to the ComfyUI checkout
    old: str
    new: str
    why: str


PATCHES = (
    Patch(
        file="comfy/clip_vision.py",
        old="""        if self.model_type == "siglip_vision_model":
            self.return_all_hidden_states = True
        else:
            self.return_all_hidden_states = False""",
        new="""        self.return_all_hidden_states = True""",
        why="IP-Adapters need the encoder's penultimate hidden states; upstream only "
            "keeps them for SigLIP, so SDXL adapters fail with a proj_in size mismatch",
    ),
)


def apply_patches(comfy: Path, patches=PATCHES) -> list[str]:
    """Apply each patch once. -> lines describing what happened. Raises if a patch's
    text is neither there nor already applied (ComfyUI changed under it)."""
    report = []
    for patch in patches:
        path = Path(comfy) / patch.file
        text = path.read_text()
        if patch.new in text and patch.old not in text:
            report.append(f"already patched: {patch.file}")
        elif patch.old in text:
            path.write_text(text.replace(patch.old, patch.new))
            report.append(f"patched: {patch.file} ({patch.why})")
        else:
            raise InstallError(f"can't patch {patch.file}: its code changed. This ComfyUI "
                               "commit isn't the pinned one, or the patch needs updating")
    return report


# Packages whose build depends on the GPU backend: they come from the PyTorch index
# chosen for this machine, so the constraints file never pins them.
_BACKEND_PACKAGE = re.compile(r"^(torch|torchvision|torchaudio|triton|nvidia-[\w-]+|"
                              r"cuda-[\w-]+)==", re.IGNORECASE)


def constraints_from_freeze(freeze: str) -> str:
    """``uv pip freeze`` of a tested venv -> a constraints file for fresh installs:
    every package at its tested version, minus the GPU-specific builds."""
    keep = [line for line in freeze.splitlines()
            if "==" in line and not _BACKEND_PACKAGE.match(line)]
    return ("# Package versions of the tested ComfyUI venv (manganation install-comfyui "
            "--freeze).\n# GPU-specific builds (torch, CUDA, triton) come from the "
            "PyTorch index instead.\n" + "\n".join(sorted(keep, key=str.lower)) + "\n")


def _unpatch(text: str, patch: Patch) -> str:
    return text.replace(patch.new, patch.old)


# --- GPU and PyTorch ---------------------------------------------------------------


@dataclass
class Gpu:
    backend: str  # cuda, rocm, mps, cpu
    name: str = ""
    driver_cuda: str = ""  # the NVIDIA driver's "CUDA Version"
    capability: str = ""  # e.g. "12.0"
    vram_gb: float = 0.0


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:2]) or (0,)


def _libcuda_gpu(load: Callable[[str], object] | None = None) -> Gpu | None:
    """The first NVIDIA GPU through the driver's own library (``libcuda``): what the
    sandbox has where ``nvidia-smi`` isn't (the Flatpak's NVIDIA driver extension)."""
    import ctypes

    try:
        lib = (load or ctypes.CDLL)("libcuda.so.1")
        if lib.cuInit(0) != 0:
            return None
        version, count, device = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        lib.cuDriverGetVersion(ctypes.byref(version))
        if lib.cuDeviceGetCount(ctypes.byref(count)) != 0 or count.value < 1:
            return None
        lib.cuDeviceGet(ctypes.byref(device), 0)
        name = ctypes.create_string_buffer(256)
        lib.cuDeviceGetName(name, 256, device)
        major, minor, memory = ctypes.c_int(), ctypes.c_int(), ctypes.c_size_t()
        lib.cuDeviceGetAttribute(ctypes.byref(major), 75, device)  # compute capability
        lib.cuDeviceGetAttribute(ctypes.byref(minor), 76, device)
        lib.cuDeviceTotalMem_v2(ctypes.byref(memory), device)
    except (OSError, AttributeError):
        return None
    v = version.value
    return Gpu("cuda", name.value.decode(errors="replace"), f"{v // 1000}.{v % 1000 // 10}",
               f"{major.value}.{minor.value}", round(memory.value / 1024 ** 3, 1))


def detect_gpu(run: Callable[..., str] | None = None,
               libcuda: Callable[[str], object] | None = None) -> Gpu:
    """What PyTorch build this machine needs. ``run(cmd) -> stdout`` (raises
    FileNotFoundError / CalledProcessError when a tool is missing); ``libcuda`` loads
    the driver library (ctypes.CDLL), tried when there's no nvidia-smi."""
    run = run or _capture
    try:
        rows = run(["nvidia-smi", "--query-gpu=name,compute_cap,memory.total",
                    "--format=csv,noheader,nounits"]).strip().splitlines()
        banner = run(["nvidia-smi"])
        name, capability, memory = (part.strip() for part in rows[0].split(","))
        cuda = re.search(r"CUDA Version:\s*([\d.]+)", banner)
        return Gpu("cuda", name, cuda.group(1) if cuda else "", capability,
                   round(float(memory) / 1024, 1))
    except (FileNotFoundError, subprocess.CalledProcessError, IndexError, ValueError):
        pass
    gpu = _libcuda_gpu(libcuda)
    if gpu is not None:
        return gpu
    try:
        info = run(["rocminfo"])
        names = re.findall(r"Marketing Name:\s*(.+)", info)
        gpus = [n.strip() for n in names if "CPU" not in n.upper()]
        if gpus:
            return Gpu("rocm", gpus[0])
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return Gpu("mps", "Apple Silicon")
    return Gpu("cpu")


def torch_index(gpu: Gpu, pins: dict) -> str | None:
    """The PyTorch wheel index URL for ``gpu`` (None: PyPI's default build, as on
    macOS). Raises InstallError when the NVIDIA driver is too old for the GPU."""
    torch = pins["torch"]
    base = torch["base_url"].rstrip("/")
    if gpu.backend == "mps":
        return None
    if gpu.backend in ("rocm", "cpu"):
        return f"{base}/{torch[gpu.backend]}"
    driver, capability = _version(gpu.driver_cuda), _version(gpu.capability or "0")
    for build in torch["cuda"]:
        if driver < _version(build["driver_cuda"]):
            continue
        if "max_capability" in build and capability > _version(build["max_capability"]):
            continue
        return f"{base}/{build['index']}"
    newest = torch["cuda"][0]["driver_cuda"]
    raise InstallError(
        f"the NVIDIA driver supports CUDA {gpu.driver_cuda or 'unknown'}, too old for a "
        f"PyTorch build that runs {gpu.name or 'this GPU'} (compute {gpu.capability}). "
        f"Update the driver (CUDA {newest} recommended), then run this again")


# --- running commands --------------------------------------------------------------


def _capture(cmd: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


def _capture_all(cmd: list[str], cwd: Path | None = None) -> str:
    """stdout and stderr together (ComfyUI logs to stderr)."""
    return subprocess.run(cmd, cwd=cwd, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True).stdout


def venv_python(comfy: Path) -> Path:
    """The python of a checkout's own venv (``<comfy>/.venv``)."""
    return _python_in(Path(comfy) / ".venv")


def _python_in(venv: Path) -> Path:
    return Path(venv) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def is_patched(comfy: Path, patches=PATCHES) -> bool:
    for patch in patches:
        text = (Path(comfy) / patch.file).read_text()
        if patch.old in text or patch.new not in text:
            return False
    return True


@dataclass
class Plan:
    """What install() will do, for --check and for the confirmation prompt."""
    comfy: Path
    gpu: Gpu
    index: str | None
    steps: list[str] = field(default_factory=list)


class Installer:
    """``run(cmd, cwd)`` executes a step (streams output); ``capture(cmd, cwd)``
    returns stdout, ``capture_all`` stdout and stderr. All injectable for tests."""

    def __init__(self, comfy: Path, pins: dict, *, run=None, capture=None,
                 capture_all=None, log=print, venv: Path | None = None,
                 data: Path | None = None, bundled: bool = False):
        """``bundled``: ComfyUI's code is read-only and already patched (the Flatpak);
        ``venv`` (default ``<comfy>/.venv``) and ``data`` (ComfyUI's user, input,
        output and temp folders; default: ComfyUI's own) are then in the data folder."""
        self.comfy = Path(comfy)
        self.pins = pins
        self.bundled = bundled
        self.venv = Path(venv) if venv is not None else self.comfy / ".venv"
        self.data = Path(data) if data is not None else None
        self._run = run or (lambda cmd, cwd=None: subprocess.run(
            cmd, cwd=cwd, check=True, env={**os.environ, **self.uv_env()}))
        self._capture = capture or _capture
        self._capture_all = capture_all or _capture_all
        self.log = log

    @property
    def python(self) -> Path:
        return _python_in(self.venv)

    def uv_env(self) -> dict[str, str]:
        """Bundled: uv fetches its own Python into the data folder (the runtime's is
        3.14, ComfyUI is pinned to another) and never uses a system one."""
        if not self.bundled:
            return {}
        return {"UV_PYTHON_INSTALL_DIR": str(self.venv.parent / "python"),
                "UV_PYTHON_PREFERENCE": "only-managed"}

    def comfy_args(self) -> list[str]:
        """ComfyUI command-line options for where it may write. Bundled, its code
        folder is read-only, so user data (and its database), input, output and temp go
        to the data folder; the code folder stays the base, so the bundled custom nodes
        are found."""
        if self.data is None:
            return []
        args = []
        for kind in ("user", "input", "output", "temp"):
            folder = self.data / kind
            folder.mkdir(parents=True, exist_ok=True)
            args += [f"--{kind}-directory", str(folder)]
        return args

    def ready(self) -> bool:
        """A verified install of the pinned ComfyUI with its PyTorch untouched since:
        cheap (no subprocess), for status polling. A full check is plan()."""
        try:
            marker = json.loads((self.venv / READY_MARKER).read_text())
        except (OSError, ValueError):
            # A checkout installed before the marker existed: PyTorch and our patch
            # are enough (`install-comfyui --check` does the thorough comparison).
            return (not self.bundled and self.torch_build() is not None
                    and (self.comfy / PATCHES[0].file).is_file() and is_patched(self.comfy))
        return (marker.get("comfyui") == self.pins["comfyui"]["commit"]
                and marker.get("torch") == self.torch_build() is not None)

    def mark_ready(self) -> None:
        (self.venv / READY_MARKER).write_text(json.dumps(
            {"comfyui": self.pins["comfyui"]["commit"], "torch": self.torch_build()}))

    # -- inspection
    def head(self, repo: Path) -> str | None:
        if not (repo / ".git").exists():
            return None
        try:
            return self._capture(["git", "rev-parse", "HEAD"], repo).strip()
        except subprocess.CalledProcessError:
            return None

    def local_edits(self, repo: Path) -> list[str]:
        """Modified tracked files, ignoring ones that differ only by our patches."""
        out = self._capture(["git", "status", "--porcelain", "--untracked-files=no"], repo)
        edits = []
        for line in out.splitlines():
            path = line[3:].strip()
            if line[:2].strip() == "D":
                continue  # a deleted placeholder file (ComfyUI ships some) is harmless
            if repo == self.comfy and any(p.file == path for p in PATCHES):
                original = self._capture(["git", "show", f"HEAD:{path}"], repo)
                current = (repo / path).read_text()
                if all(_unpatch(current, p) == original or current == original
                       for p in PATCHES if p.file == path):
                    continue
            edits.append(path)
        return edits

    def torch_build(self) -> str | None:
        """The installed PyTorch version (e.g. 2.14.1+cu130), from its package metadata:
        no import, so it's cheap enough to poll."""
        for meta in sorted(self.venv.glob("lib/python*/site-packages/torch-*.dist-info/METADATA")):
            for line in meta.read_text(errors="replace").splitlines():
                if line.startswith("Version:"):
                    return line.split(":", 1)[1].strip()
        return None

    def plan(self, gpu: Gpu | None = None) -> Plan:
        gpu = gpu or detect_gpu(lambda cmd: self._capture(cmd))
        index = torch_index(gpu, self.pins)
        plan = Plan(self.comfy, gpu, index)
        repos = [] if self.bundled else [("ComfyUI", self.comfy, self.pins["comfyui"])] + [
            (name, self.comfy / "custom_nodes" / name, pin)
            for name, pin in self.pins.get("custom_nodes", {}).items()]
        for name, path, pin in repos:
            head = self.head(path)
            if head is None:
                plan.steps.append(f"clone {name} at {pin['commit'][:10]}")
            elif head != pin["commit"]:
                plan.steps.append(f"move {name} from {head[:10]} to {pin['commit'][:10]}")
        build = self.torch_build()
        wanted = (index or "").rsplit("/", 1)[-1]
        if not self.python.is_file():
            plan.steps.append(f"create a Python {self.pins['python']} venv"
                              + (" (downloads that Python)" if self.bundled else ""))
        if build is None or (wanted and not build.endswith(f"+{wanted}")):
            plan.steps.append(f"install PyTorch ({wanted or 'default build'})"
                              + (f", replacing {build}" if build else ""))
        plan.steps.append("install ComfyUI's requirements (quick when already there)")
        plan.steps.append("check the install" if self.bundled else
                          "apply imanganation's patches; check the install")
        return plan

    # -- doing
    def install(self, plan: Plan, *, force: bool = False) -> None:
        if not self.bundled and shutil.which("git") is None:
            raise InstallError("git is needed: install it from your package manager")
        if shutil.which("uv") is None:
            raise InstallError("uv is needed: https://docs.astral.sh/uv/getting-started/")
        if self.bundled:
            if not is_patched(self.comfy):
                raise InstallError(f"the bundled ComfyUI in {self.comfy} isn't patched; "
                                   "the app build is broken")
        else:
            self._checkout("ComfyUI", self.comfy, self.pins["comfyui"], force)
        for name, pin in self.pins.get("custom_nodes", {}).items():
            node = self.comfy / "custom_nodes" / name
            if not self.bundled:
                self._checkout(name, node, pin, force)
        if not self.python.is_file():
            self.log(f"Creating a Python {self.pins['python']} venv…")
            self.venv.parent.mkdir(parents=True, exist_ok=True)
            self._run(["uv", "venv", "--python", self.pins["python"], str(self.venv)])
        for name in self.pins.get("custom_nodes", {}):
            requirements = self.comfy / "custom_nodes" / name / "requirements.txt"
            if requirements.is_file():
                self._pip(["-r", str(requirements)])
        wanted = (plan.index or "").rsplit("/", 1)[-1]
        build = self.torch_build()
        if build is None or (wanted and not build.endswith(f"+{wanted}")):
            self.log(f"Installing PyTorch ({wanted or 'default build'}); a few GB…")
            extra = ["--index-url", plan.index] if plan.index else []
            self._pip(["--reinstall-package", "torch", "--reinstall-package", "torchvision",
                       "torch", "torchvision", *extra] if build else
                      ["torch", "torchvision", *extra])
        self.log("Installing ComfyUI's requirements…")
        # torch is already the right build; uv keeps it (it satisfies "torch"). The
        # constraints hold every other package at the version imanganation was tested with.
        constraints = self.constraints_file()
        self._pip(["-r", str(self.comfy / "requirements.txt")]
                  + (["-c", str(constraints)] if constraints else []))
        if not self.bundled:
            for line in apply_patches(self.comfy):
                self.log(line)

    def constraints_file(self) -> Path | None:
        from manganation.config import CONFIG_DIR

        name = self.pins.get("constraints")
        path = CONFIG_DIR / name if name else None
        return path if path is not None and path.is_file() else None

    def freeze(self) -> str:
        """The constraints file text for this (tested) venv."""
        return constraints_from_freeze(self._capture(
            ["uv", "pip", "freeze", "--python", str(self.python)]))

    def _pip(self, args: list[str]) -> None:
        self._run(["uv", "pip", "install", "--python", str(self.python), *args])

    def _checkout(self, name: str, path: Path, pin: dict, force: bool) -> None:
        if self.head(path) is None:
            if path.exists() and any(path.iterdir()):
                raise InstallError(f"{path} exists but isn't a git checkout; move it aside")
            self.log(f"Cloning {name}…")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._run(["git", "clone", "--filter=blob:none", pin["repo"], str(path)])
        if self.head(path) == pin["commit"]:
            return
        edits = self.local_edits(path)
        if edits and not force:
            raise InstallError(f"{name} has local changes ({', '.join(edits[:5])}); commit "
                               "or discard them, or pass --force to overwrite them")
        self.log(f"Checking out {name} {pin['commit'][:10]}…")
        if path == self.comfy:  # our patches would block the checkout; re-applied later
            for patch in PATCHES:
                file = path / patch.file
                if file.is_file():
                    file.write_text(_unpatch(file.read_text(), patch))
        self._run(["git", "fetch", "--quiet", "origin", pin["commit"]], path)
        self._run(["git", "checkout", "--quiet"] + (["--force"] if force else [])
                  + [pin["commit"]], path)

    def verify(self) -> str:
        """Check the install works: PyTorch imports and sees the GPU, and ComfyUI
        starts (its own --quick-test-for-ci) with every pinned custom node loaded.
        -> a summary line; raises InstallError otherwise."""
        python = str(self.python)
        probe = ("import torch; cuda = torch.cuda.is_available(); "
                 "mps = getattr(torch.backends, 'mps', None) is not None "
                 "and torch.backends.mps.is_available(); "
                 "print(torch.__version__, '|', torch.cuda.get_device_name(0) if cuda "
                 "else 'Apple GPU (mps)' if mps else 'no GPU: CPU only, very slow')")
        try:
            torch_line = self._capture([python, "-c", probe], self.comfy).strip()
        except subprocess.CalledProcessError as exc:
            raise InstallError(f"PyTorch doesn't import in the venv: {exc.stderr or exc}"
                               ) from exc
        try:
            log = self._capture_all(
                [python, "main.py", "--quick-test-for-ci", *self.comfy_args()]
                + (["--cpu"] if "CPU only" in torch_line else []), self.comfy)
        except subprocess.CalledProcessError as exc:
            tail = "\n".join((exc.stdout or "").splitlines()[-15:])
            raise InstallError(f"ComfyUI doesn't start:\n{tail}") from exc
        for name in self.pins.get("custom_nodes", {}):
            lines = [line for line in log.splitlines() if f"custom_nodes/{name}" in line
                     or f"custom_nodes\\{name}" in line]
            if not lines or any("IMPORT FAILED" in line for line in lines):
                raise InstallError(f"ComfyUI started but the {name} nodes didn't load; "
                                   "see the log above")
        return torch_line


def installer_for(settings, *, comfy: Path | None = None, log=print, **kwargs) -> Installer:
    """The installer for this engine: the checkout's ComfyUI (``vendor/ComfyUI`` and
    its .venv), or, when its code is the Flatpak's bundled copy, the data folder."""
    from manganation.config import REPO_ROOT, data_root

    comfy = Path(comfy or REPO_ROOT / settings.paths.comfyui_dir)
    if (comfy / BUNDLED_MARKER).exists():
        data = data_root()
        return Installer(comfy, load_pins(), log=log, venv=data / "comfyui-venv",
                         data=data / "comfyui", bundled=True, **kwargs)
    return Installer(comfy, load_pins(), log=log, **kwargs)


def load_pins(path: Path | None = None) -> dict:
    from manganation.config import CONFIG_DIR

    return yaml.safe_load((path or CONFIG_DIR / "comfyui.yaml").read_text())
