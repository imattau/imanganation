"""Get the model files the engine needs onto disk: check, link, download, verify.

Models aren't shipped with imanganation, so a new install fetches them. Which files
are needed follows the settings (the active checkpoint, IP-Adapter and its encoder,
upscaler, ControlNet); where each comes from, how big it is and its SHA-256 are in
``config/models.yaml`` (``download``). ``manganation setup`` drives this module.

- **Order:** the checkpoint first (rendering works as soon as it lands), then character
  references, then the small upscaler, then ControlNet.
- **Reuse:** a folder the user already has (ComfyUI, A1111) is searched for matching
  files, by size and then hash, and they are hard-linked (or symlinked) into place
  instead of downloaded again.
- **Downloads** resume from a ``.part`` file, are hashed as they arrive, and are only
  renamed into place once the hash matches, so an interrupted or corrupt download never
  looks like a model. Mirrors are tried in order.
- ``HF_TOKEN`` is sent to Hugging Face only; ``HF_ENDPOINT`` points Hugging Face URLs at
  a mirror.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

HF_HOST = "huggingface.co"
CHUNK = 1 << 20


class SetupError(RuntimeError):
    pass


@dataclass
class ModelFile:
    role: str  # e.g. "checkpoint", "ip-adapter (noob_mark1)"
    feature: str
    file: str  # the name ComfyUI expects
    subdir: str
    size: int | None = None
    sha256: str | None = None
    urls: list[str] = field(default_factory=list)  # main source, then mirrors
    license: str = ""
    license_url: str = ""
    note: str = ""  # why it can't be resolved (unknown role, …)

    def path(self, root: Path) -> Path:
        return Path(root) / self.subdir / self.file


def _entry(role: str, entry: dict | None, default_subdir: str, note: str = "") -> ModelFile:
    entry = entry or {}
    download = entry.get("download") or {}
    license_ = entry.get("license") or {}
    return ModelFile(
        role=role, feature=entry.get("feature", ""), file=entry.get("id", ""),
        subdir=entry.get("subdir", default_subdir), size=download.get("size"),
        sha256=(download.get("sha256") or "").lower() or None,
        urls=[u for u in [download.get("url"), *download.get("mirrors", [])] if u],
        license=license_.get("name", ""), license_url=license_.get("url", ""),
        note=note if not entry else "")


def needed(settings, models: dict) -> list[ModelFile]:
    """The files the current settings use, most useful first."""
    out = [_entry("checkpoint", models.get("checkpoints", {}).get("primary"), "checkpoints",
                  "no checkpoints.primary in models.yaml")]
    adapters = models.get("ipadapter", {})
    adapter = settings.defaults.ipadapter.adapter
    entry = adapters.get(adapter)
    out.append(_entry(f"ip-adapter ({adapter})", entry, "ipadapter",
                      f"unknown IP-Adapter {adapter!r}; models.yaml has {sorted(adapters)}"))
    if entry is not None:
        encoder = entry.get("encoder", "clip_vision")
        out.append(_entry("clip vision", adapters.get(encoder), "ipadapter",
                          f"IP-Adapter {adapter!r} names encoder {encoder!r}, "
                          "which isn't in models.yaml"))
    up = settings.defaults.refiner.upscaler
    out.append(_entry(f"upscaler ({up})", models.get("upscalers", {}).get(up),
                      "upscale_models", "not in models.yaml"))
    cn = getattr(settings.defaults, "controlnet", None)
    if cn is not None:
        out.append(_entry(f"controlnet ({cn.model})", models.get("controlnets", {}).get(cn.model),
                          "controlnet", "not in models.yaml"))
    return out


DEFAULT_TAGGER = "wd_swinv2_v3"
DEFAULT_DETECTOR = "anime_person"
FACE_DETECTOR = "anime_face"


def evaluation(models: dict, tagger: str = DEFAULT_TAGGER) -> list[ModelFile]:
    """The files ``manganation eval`` judges renders with: a tagger, its tag list and
    a person detector. Not needed to render, so ``setup`` only fetches them when asked
    (``--eval``)."""
    taggers = models.get("taggers", {})
    entry = taggers.get(tagger)
    out = [_entry(f"tagger ({tagger})", entry, "taggers",
                  f"unknown tagger {tagger!r}; models.yaml has {sorted(taggers)}")]
    if entry is not None:
        out.append(_entry("tagger tags", taggers.get(entry.get("tags", "")), "taggers",
                          f"tagger {tagger!r} names no tag list that's in models.yaml"))
    detectors = models.get("detectors", {})
    for name in (DEFAULT_DETECTOR, FACE_DETECTOR):
        out.append(_entry(f"detector ({name})", detectors.get(name), "taggers",
                          f"no detectors.{name} in models.yaml"))
    return out


def detector(models: dict, name: str) -> ModelFile:
    """One ``models.yaml -> detectors`` entry."""
    return _entry(f"detector ({name})", models.get("detectors", {}).get(name), "taggers",
                  f"no detectors.{name} in models.yaml")


def trial(models: dict, name: str) -> list[ModelFile]:
    """A trial model group (``models.yaml`` -> ``trials``): a candidate the eval compares,
    fetched only when asked (``setup --trial``)."""
    group = models.get("trials", {}).get(name)
    if not group:
        known = ", ".join(sorted(models.get("trials", {}))) or "none"
        return [_entry(f"trial {name}", None, "", f"unknown trial {name!r} (known: {known})")]
    return [_entry(f"{name} {part}", entry, entry.get("subdir", ""))
            for part, entry in group.items()]


# --- state -----------------------------------------------------------------


def sha256_of(path: Path, progress: Callable[[int], None] | None = None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
            if progress:
                progress(len(chunk))
    return digest.hexdigest()


def state(model: ModelFile, root: Path, *, verify: bool = False) -> str:
    """``present``, ``missing``, ``wrong size``, ``corrupt`` (hash, only with
    ``verify``) or ``unknown`` (no file name to look for)."""
    if not model.file:
        return "unknown"
    path = model.path(root)
    if not path.is_file():
        return "missing"
    if model.size is not None and path.stat().st_size != model.size:
        return "wrong size"
    if verify and model.sha256 and sha256_of(path) != model.sha256:
        return "corrupt"
    return "present"


def free_bytes(root: Path) -> int:
    """Free space where models go (the nearest existing parent of ``root``)."""
    probe = Path(root).resolve()
    while not probe.exists():
        probe = probe.parent
    return shutil.disk_usage(probe).free


# --- reuse files the user already has ---------------------------------------


def find_existing(models: Iterable[ModelFile], folders: Iterable[Path],
                  ) -> dict[str, Path]:
    """``{role: path}`` for each model found in ``folders`` (searched recursively):
    same size, then same SHA-256. Name doesn't matter (A1111 and ComfyUI name the
    same checkpoint differently). Models without a size and hash are never matched."""
    wanted: dict[int, list[ModelFile]] = {}
    for m in models:
        if m.size and m.sha256:
            wanted.setdefault(m.size, []).append(m)
    found: dict[str, Path] = {}
    hashes: dict[Path, str] = {}
    for folder in folders:
        for path in sorted(Path(folder).expanduser().rglob("*")):
            try:
                if not path.is_file() or path.stat().st_size not in wanted:
                    continue
            except OSError:
                continue
            for m in wanted[path.stat().st_size]:
                if m.role in found:
                    continue
                if path not in hashes:
                    hashes[path] = sha256_of(path)
                if hashes[path] == m.sha256:
                    found[m.role] = path
    return found


def link_into_place(source: Path, dest: Path) -> str:
    """Hard-link ``source`` to ``dest`` (no extra disk space, survives the original's
    folder being moved), else symlink across filesystems. -> "hard link" / "symlink"."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    try:
        os.link(source, dest)
        return "hard link"
    except OSError:
        dest.symlink_to(Path(source).resolve())
        return "symlink"


# --- download -----------------------------------------------------------------


def _with_endpoint(url: str) -> str:
    """Hugging Face URLs via ``HF_ENDPOINT`` (a mirror), when it's set."""
    endpoint = os.environ.get("HF_ENDPOINT", "").rstrip("/")
    prefix = f"https://{HF_HOST}"
    return endpoint + url[len(prefix):] if endpoint and url.startswith(prefix) else url


def _headers(url: str, start: int) -> dict[str, str]:
    headers = {"User-Agent": "imanganation-setup"}
    if start:
        headers["Range"] = f"bytes={start}-"
    token = os.environ.get("HF_TOKEN")
    if token and urlparse(url).hostname == HF_HOST:  # never to other hosts or mirrors
        headers["Authorization"] = f"Bearer {token}"
    return headers


def download(model: ModelFile, root: Path, *, client: httpx.Client | None = None,
             progress: Callable[[int, int], None] | None = None) -> Path:
    """Fetch ``model`` into ``root``; -> its final path.

    Resumes ``<file>.part`` if one is left from an earlier try. The file is hashed as
    it arrives and only renamed into place when size and SHA-256 match; a mismatch
    deletes the part (it can't be trusted to resume) and tries the next mirror.
    ``progress(done, total)`` is called as bytes arrive."""
    if not model.urls:
        raise SetupError(f"{model.file or model.role}: no download source in models.yaml")
    dest = model.path(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    own_client = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=120))
    errors = []
    try:
        for url in model.urls:
            url = _with_endpoint(url)
            try:
                _fetch(client, url, model, part, progress)
            except (httpx.HTTPError, SetupError, OSError) as exc:
                errors.append(f"{url}: {exc}")
                continue
            part.replace(dest)
            return dest
    finally:
        if own_client:
            client.close()
    raise SetupError(f"could not download {model.file}:\n  " + "\n  ".join(errors))


def _fetch(client: httpx.Client, url: str, model: ModelFile, part: Path,
           progress: Callable[[int, int], None] | None) -> None:
    digest = hashlib.sha256()
    start = part.stat().st_size if part.is_file() else 0
    if model.size is not None and start > model.size:
        part.unlink()
        start = 0
    if start:
        with open(part, "rb") as f:
            while chunk := f.read(CHUNK):
                digest.update(chunk)
    with client.stream("GET", url, headers=_headers(url, start)) as response:
        if start and response.status_code == 200:  # the server ignored Range: start over
            start, digest = 0, hashlib.sha256()
        elif response.status_code == 416 and start == model.size:
            pass  # already complete; the hash check below decides
        elif response.status_code not in (200, 206):
            raise SetupError(f"HTTP {response.status_code}")
        total = model.size or (start + int(response.headers.get("content-length", 0)))
        done = start
        if response.status_code != 416:
            with open(part, "ab" if start else "wb") as f:
                for chunk in response.iter_bytes(CHUNK):
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    size = part.stat().st_size
    if model.size is not None and size != model.size:
        if size > model.size:
            part.unlink()
        raise SetupError(f"got {size} bytes, expected {model.size}")
    if model.sha256 and digest.hexdigest() != model.sha256:
        part.unlink()
        raise SetupError(f"SHA-256 mismatch (got {digest.hexdigest()[:12]}…, "
                         f"expected {model.sha256[:12]}…); the partial file was removed")


# --- ComfyUI model paths ------------------------------------------------------


def write_comfy_paths(config_file: Path, models_root: Path) -> bool:
    """Point ComfyUI's extra-model-paths file at ``models_root``. It holds an absolute
    path, so a fresh checkout's copy names another machine's folder. -> True if the
    file changed."""
    from manganation.config import load_models

    subdirs = {"checkpoints": "checkpoints", "loras": "loras"}
    for section in load_models().values():
        entries = section.values() if isinstance(section, dict) else section
        for entry in entries:
            if isinstance(entry, dict) and entry.get("subdir"):
                subdirs.setdefault(entry["subdir"], entry["subdir"])
    subdirs["clip_vision"] = "ipadapter"  # encoders live beside their adapters
    lines = ["# Point ComfyUI at imanganation's model store. Written by `manganation setup`.",
             "# Launch with: python main.py --extra-model-paths-config <this file>",
             "imanganation:", f"    base_path: {Path(models_root).resolve()}"]
    lines += [f"    {key}: {value}" for key, value in subdirs.items()]
    text = "\n".join(lines) + "\n"
    try:
        current = config_file.read_text()
    except FileNotFoundError:
        current = ""
    if _base_path(current) == str(Path(models_root).resolve()):
        return False  # already right; keep any hand edits
    config_file.write_text(text)
    return True


def _base_path(text: str) -> str | None:
    for line in text.splitlines():
        key, _, value = line.strip().partition(":")
        if key == "base_path":
            return value.strip()
    return None


# --- background runner (the engine's /setup endpoints) ---------------------------


class Cancelled(Exception):
    """Raised from a progress callback to stop a download; its .part is kept."""


RENDERER_ROLE = "renderer"
# What the renderer's venv takes on disk (measured: PyTorch cu130 with its CUDA
# libraries 5.2 GB, a uv-managed Python 0.1 GB, ComfyUI's packages); downloads are
# smaller (compressed wheels), so this is also a safe disk-space estimate.
RENDERER_BYTES = 5_500_000_000


class SetupRunner:
    """One setup task at a time (a download run or a link search) in a background
    thread, so the engine stays responsive and the GIMP dialog can poll progress.
    Downloads don't use the GPU, so they run beside renders, not in the job queue.

    ``models`` returns the needed ModelFiles (re-read each time, so a settings change
    shows up); ``comfy_paths`` is ComfyUI's extra-model-paths file, rewritten for
    ``root`` when a task starts (``comfy_paths_changed`` then asks for a restart).

    ``renderer(log)`` returns the ComfyUI installer (comfy_setup.Installer) when the
    renderer is set up here too (the Flatpak, where nothing else installs it): it's the
    first row, and a download installs it before the models; ``on_renderer_ready`` runs
    once it's installed (the engine starts its own ComfyUI)."""

    def __init__(self, root: Path, models: Callable[[], list[ModelFile]], *,
                 comfy_paths: Path | None = None, fetch=None, find=None, renderer=None,
                 on_renderer_ready: Callable[[], object] | None = None):
        import threading

        self.root = Path(root)
        self.models = models
        self.comfy_paths = comfy_paths
        self._fetch = fetch or download
        self._find = find or find_existing
        self.renderer = renderer
        self.on_renderer_ready = on_renderer_ready
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread = None
        self._task = {"kind": None, "state": "idle"}
        self.comfy_paths_changed = False

    # -- reading
    def report(self) -> dict:
        models = self.models()
        with self._lock:
            task = dict(self._task)
        rows = [{"role": m.role, "feature": m.feature, "file": m.file, "size": m.size,
                 "state": state(m, self.root), "license": m.license,
                 "license_url": m.license_url, "downloadable": bool(m.urls),
                 "note": m.note} for m in models]
        if self.renderer is not None:
            rows.insert(0, self._renderer_row())
        missing = [r for r in rows if r["state"] != "present"]
        return {"models_dir": str(self.root), "free_bytes": free_bytes(self.root),
                "models": rows, "missing_bytes": sum(r["size"] or 0 for r in missing
                                                    if r["downloadable"]),
                "ready": not missing, "task": task,
                "comfy_paths_changed": self.comfy_paths_changed}

    def _renderer_row(self) -> dict:
        row = {"role": RENDERER_ROLE,
               "feature": "renderer: ComfyUI and PyTorch for your GPU (required)",
               "file": "ComfyUI + PyTorch", "size": RENDERER_BYTES, "downloadable": True,
               "license": "GPL-3.0 (ComfyUI), BSD-3-Clause (PyTorch), NVIDIA CUDA EULA",
               "license_url": "https://github.com/comfyanonymous/ComfyUI/blob/master/LICENSE",
               "note": ""}
        try:
            row["state"] = "present" if self.renderer(lambda *a: None).ready() else "missing"
        except Exception as exc:  # noqa: BLE001 - a broken install shows, never crashes
            row.update(state="unknown", note=str(exc))
        return row

    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- starting
    def start_download(self, roles: list[str] | None = None) -> dict:
        """Download what's missing (``roles``: only these). The renderer, if it's set up
        here and missing, is installed first: rendering needs it before any model."""
        todo = [m for m in self.models() if state(m, self.root) != "present" and m.urls
                and (roles is None or m.role in roles)]
        renderer = (self.renderer is not None and (roles is None or RENDERER_ROLE in roles)
                    and self._renderer_row()["state"] != "present")
        total = sum(m.size or 0 for m in todo)
        needed = total + (RENDERER_BYTES if renderer else 0)
        if needed > free_bytes(self.root):
            raise SetupError(f"not enough disk space: {needed / 1e9:.1f} GB needed, "
                             f"{free_bytes(self.root) / 1e9:.1f} GB free")
        def work():
            if renderer:
                self._install_renderer()
            self._download(todo)

        return self._start("download", work, files=len(todo) + renderer, total=total,
                           renderer=renderer, phase="renderer" if renderer else "models")

    def start_link(self, folders: list[Path], also: list[ModelFile] | None = None) -> dict:
        """Search ``folders`` for the missing models, and for ``also`` (the optional
        engines' files, say) in the same pass, and link the matches in."""
        for folder in folders:
            if not Path(folder).expanduser().is_dir():
                raise SetupError(f"not a folder: {folder}")
        todo, seen = [], set()
        for m in [*self.models(), *(also or [])]:
            if state(m, self.root) != "present" and m.role not in seen:
                seen.add(m.role)
                todo.append(m)
        return self._start("link", lambda: self._link(todo, folders),
                           folders=[str(f) for f in folders])

    def cancel(self) -> dict:
        self._cancel.set()
        return self.report()["task"]

    def _start(self, kind: str, work: Callable[[], None], **info) -> dict:
        import threading

        with self._lock:
            if self.busy():
                raise SetupError(f"a {self._task['kind']} is already running")
            self._cancel.clear()
            self._task = {"kind": kind, "state": "running", "current": None, "done": 0,
                          "file_done": 0, "file_total": 0, "finished": [], "errors": [],
                          **info}
            self._thread = threading.Thread(target=self._run, args=(work,), daemon=True,
                                            name=f"setup-{kind}")
            self._thread.start()
            return dict(self._task)

    def _run(self, work: Callable[[], None]) -> None:
        if self.comfy_paths is not None:
            try:
                if write_comfy_paths(self.comfy_paths, self.root):
                    self.comfy_paths_changed = True
            except OSError as exc:
                self._update(errors=[*self._task["errors"],
                                     f"could not write {self.comfy_paths}: {exc}"])
        try:
            work()
            outcome = "cancelled" if self._cancel.is_set() else (
                "error" if self._task["errors"] else "done")
        except Exception as exc:  # noqa: BLE001 - reported to the dialog, never lost
            self._update(errors=[*self._task["errors"], str(exc)])
            outcome = "error"
        self._update(state=outcome, current=None)

    def _update(self, **changes) -> None:
        with self._lock:
            self._task = {**self._task, **changes}

    # -- work
    def _install_renderer(self) -> None:
        """ComfyUI's venv with PyTorch for this GPU (comfy_setup), then its start-up
        check. Its steps can't be interrupted mid-way: a pause applies after it."""
        import subprocess

        self._update(phase="renderer", current="Checking the GPU…")

        def log(*parts):
            self._update(current=" ".join(str(p) for p in parts)[:200])

        try:
            installer = self.renderer(log)
            installer.install(installer.plan())
            self._update(current="Checking that ComfyUI starts on the GPU…")
            summary = installer.verify()
            installer.mark_ready()
            self._update(finished=[*self._task["finished"], f"renderer ({summary})"])
            if self.on_renderer_ready is not None:
                self._update(current="Starting ComfyUI…")
                self.on_renderer_ready()
        except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
            self._update(errors=[*self._task["errors"], f"renderer: {exc}"])
        self._update(phase="models")

    def _download(self, todo: list[ModelFile]) -> None:
        before = 0
        for m in todo:  # most useful first
            if self._cancel.is_set():
                return
            self._update(current=m.file, file_done=0, file_total=m.size or 0)

            def progress(done, total, before=before):
                if self._cancel.is_set():
                    raise Cancelled
                self._update(file_done=done, file_total=total, done=before + done)

            try:
                self._fetch(m, self.root, progress=progress)
            except Cancelled:
                return
            except SetupError as exc:
                self._update(errors=[*self._task["errors"], str(exc)])
            else:
                self._update(finished=[*self._task["finished"], m.file])
            before += m.size or 0

    def _link(self, todo: list[ModelFile], folders: list[Path]) -> None:
        self._update(current="searching for matching files…")
        for role, found in self._find(todo, folders).items():
            model = next(m for m in todo if m.role == role)
            how = link_into_place(found, model.path(self.root))
            self._update(finished=[*self._task["finished"], f"{model.file} ({how})"])
