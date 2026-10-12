"""Find newer versions of the downloadable models, and pin the ones the user accepts.

``config/models.yaml`` pins every download to a repo revision with its size and SHA-256,
so a model never changes under a project. This module asks Hugging Face whether the file
at that path is different on the repo's main branch (the LFS object id *is* the
SHA-256, so nothing is downloaded to find out) and, when the user accepts, records the
new pin in ``model-pins.yaml`` in the data folder. The bundled models.yaml stays
read-only (it is, in the Flatpak); ``apply_pins`` overlays the user's pins when it is
loaded. A pin is ignored once the shipped pin for that file changes (an app update
moved on), so it can never pin an older file than the one the app ships.

- **auto** files (taggers, detectors, upscalers) are small supporting files: the
  workspace offers them at start-up.
- **manual** files (checkpoints, adapters, ControlNets) change how renders look, so they
  are only listed in Set Up Models, with an Update button.
- The optional engines' files (``trials``: Qwen-Image, Z-Anime, the face pass) are
  "manual" too, as ``trials.<group>.<part>``.
- Only files already on disk are checked, and only files with an LFS object (a hash we
  can compare).
- At most one check a day; ``IMANGANATION_NO_UPDATE_CHECK`` or ``enabled: false`` in
  ``model-updates.json`` turns it off.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml

INTERVAL = 24 * 3600
AUTO_SECTIONS = ("upscalers", "taggers", "detectors")
MANUAL_SECTIONS = ("checkpoints", "ipadapter", "controlnets", "loras")
_RESOLVE = re.compile(r"^https://huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.+)$")


@dataclass
class Update:
    path: str  # "taggers.wd_swinv2_v3": the entry in models.yaml
    display: str
    file: str
    kind: str  # "auto" | "manual"
    size: int  # of the new file
    old_size: int | None
    url: str  # pinned to the new revision
    sha256: str
    base_sha256: str  # the shipped pin this replaces
    subdir: str = ""
    license: str = ""
    license_url: str = ""


# --- the user's pins -----------------------------------------------------------


def _data_file(name: str) -> Path:
    from manganation.config import data_root

    return data_root() / name


def pins_file() -> Path:
    return _data_file("model-pins.yaml")


def state_file() -> Path:
    return _data_file("model-updates.json")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def read_pins(path: Path | None = None) -> dict:
    try:
        data = yaml.safe_load((path or pins_file()).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def write_pin(update: Update, path: Path | None = None) -> None:
    pins = read_pins(path)
    pins[update.path] = {"url": update.url, "size": update.size, "sha256": update.sha256,
                         "base_sha256": update.base_sha256}
    _write_atomic(path or pins_file(), yaml.safe_dump(pins, sort_keys=True))


def entries(models: dict):
    """(dotted path, section, entry) for every model entry that has a download."""
    def has_download(entry):
        return isinstance(entry, dict) and (entry.get("download") or {}).get("url")

    for section in (*AUTO_SECTIONS, *MANUAL_SECTIONS):
        for key, entry in (models.get(section) or {}).items():
            if has_download(entry):
                yield f"{section}.{key}", section, entry
    for group, parts in (models.get("trials") or {}).items():
        for part, entry in (parts or {}).items():
            if has_download(entry):
                yield f"trials.{group}.{part}", "trials", entry


def apply_pins(models: dict, pins: dict) -> dict:
    """``models`` with the user's accepted updates in place of the shipped pins."""
    for path, section, entry in entries(models):
        pin = pins.get(path)
        download = entry["download"]
        if (not isinstance(pin, dict) or pin.get("base_sha256") != download.get("sha256")
                or not all(pin.get(k) for k in ("url", "size", "sha256"))):
            continue  # none, or the app has since shipped a different pin: it wins
        download.update(url=pin["url"], size=pin["size"], sha256=pin["sha256"])
        download.pop("mirrors", None)  # they hold the old file
    return models


# --- the state: when we last looked, what we found, what the user waved off ---------


def read_state(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or state_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"enabled": True, "checked_at": 0, "error": "", "updates": [], "skipped": {},
            **(data if isinstance(data, dict) else {})}


def write_state(state: dict, path: Path | None = None) -> None:
    _write_atomic(path or state_file(), json.dumps(state, indent=1))


def enabled(state: dict) -> bool:
    return bool(state.get("enabled")) and not os.environ.get("IMANGANATION_NO_UPDATE_CHECK")


def due(state: dict, now: float | None = None) -> bool:
    return enabled(state) and (now or time.time()) - state.get("checked_at", 0) >= INTERVAL


def offered(state: dict) -> list[dict]:
    """The found updates the user hasn't skipped (that very version)."""
    return [u for u in state.get("updates", [])
            if state.get("skipped", {}).get(u["path"]) != u["sha256"]]


# --- asking Hugging Face --------------------------------------------------------------


def _api(url: str) -> str:
    from manganation.models_setup import _with_endpoint

    return _with_endpoint(url)


def _headers() -> dict[str, str]:
    from manganation.models_setup import HF_HOST, _headers

    return _headers(f"https://{HF_HOST}/", 0)


def _latest(client: httpx.Client, repo: str, files: list[str]) -> tuple[str, dict]:
    """(main's commit, {path: LFS info}) for ``files`` of a model repo."""
    base = f"https://huggingface.co/api/models/{repo}"
    head = client.get(_api(f"{base}/revision/main"), headers=_headers())
    head.raise_for_status()
    info = client.post(_api(f"{base}/paths-info/main"), headers=_headers(),
                       json={"paths": files})
    info.raise_for_status()
    return head.json()["sha"], {item["path"]: item for item in info.json()}


def check(models: dict, root: Path, *, client: httpx.Client | None = None) -> list[Update]:
    """Updates for the installed, hash-comparable, Hugging Face-hosted files. A repo that
    can't be reached or answers oddly is skipped; the rest are still checked.
    Raises httpx.HTTPError only when *nothing* could be reached."""
    wanted: dict[str, list] = {}
    for path, section, entry in entries(models):
        match = _RESOLVE.match(entry["download"]["url"])
        file = root / entry.get("subdir", "") / entry.get("id", "")
        if match and entry.get("id") and file.is_file():
            repo, _rev, name = match.groups()
            wanted.setdefault(repo, []).append((path, section, entry, name))
    own = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(15),
                                    headers={"User-Agent": "imanganation-update-check"})
    found, reached, failure = [], 0, None
    try:
        for repo, items in wanted.items():
            try:
                commit, info = _latest(client, repo, sorted({n for *_, n in items}))
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                failure = exc
                continue
            reached += 1
            for path, section, entry, name in items:
                lfs = (info.get(name) or {}).get("lfs") or {}
                new, size = (lfs.get("oid") or "").lower(), lfs.get("size")
                download = entry["download"]
                if len(new) != 64 or not size or new == (download.get("sha256") or "").lower():
                    continue
                licence = entry.get("license") or {}
                found.append(Update(
                    path=path, display=entry.get("display") or entry["id"], file=entry["id"],
                    kind="auto" if section in AUTO_SECTIONS else "manual", size=size,
                    old_size=download.get("size"),
                    url=f"https://huggingface.co/{repo}/resolve/{commit}/{quote(name)}",
                    sha256=new, base_sha256=download["sha256"],
                    subdir=entry.get("subdir", ""),
                    license=licence.get("name", ""), license_url=licence.get("url", "")))
    finally:
        if own:
            client.close()
    if wanted and not reached and failure is not None:
        raise failure
    return found


def run_check(models: dict, root: Path, *, state_path: Path | None = None,
              client: httpx.Client | None = None, now: float | None = None) -> dict:
    """Check, remember the answer (and when), and return the state. A failed check keeps
    the earlier findings and records why."""
    state = read_state(state_path)
    try:
        state["updates"] = [asdict(u) for u in check(models, root, client=client)]
        state["error"] = ""
    except httpx.HTTPError as exc:
        state["error"] = f"could not reach Hugging Face: {exc}"
    state["checked_at"] = now or time.time()
    # a skip only means something while that version is still the newest
    still = {u["path"]: u["sha256"] for u in state["updates"]}
    state["skipped"] = {p: s for p, s in state.get("skipped", {}).items() if still.get(p) == s}
    write_state(state, state_path)
    return state
