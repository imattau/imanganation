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
