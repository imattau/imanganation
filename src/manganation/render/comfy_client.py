"""Minimal ComfyUI HTTP API client.

Talks to a running ComfyUI instance: queue a workflow (API format), wait for the
result, and fetch output images. Kept dependency-light (httpx only).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8188", client_id: str | None = None):
        self.base_url = base_url.rstrip("/")
        # One id for the whole process: ComfyUI reports a prompt's progress only to the
        # client that queued it, and progress.ProgressHub listens as this one.
        from manganation.render.progress import CLIENT_ID

        self.client_id = client_id or CLIENT_ID

    def is_up(self) -> bool:
        try:
            return httpx.get(f"{self.base_url}/system_stats", timeout=3.0).status_code == 200
        except httpx.HTTPError:
            return False

    def free(self, wait: float = 20.0) -> None:
        """Unload ComfyUI's models and free its VRAM, so the LLM fits on the GPU (on 16 GB
        a resident SDXL leaves the LLM running on the CPU, minutes per answer). ComfyUI
        reloads them on the next render."""
        try:
            before = self._vram_free()
            httpx.post(f"{self.base_url}/free",
                       json={"unload_models": True, "free_memory": True}, timeout=10.0)
        except httpx.HTTPError:
            return  # not running: nothing to free
        # /free only queues the unload; wait for the memory to come back, or the LLM
        # loads first and is placed on the CPU anyway.
        deadline, last = time.monotonic() + wait, before
        while time.monotonic() < deadline:
            time.sleep(0.5)
            try:
                now = self._vram_free()
            except httpx.HTTPError:
                return
            if now > before + 512 * 2**20 and now == last:  # freed, and settled
                return
            last = now

    def _vram_free(self) -> int:
        stats = httpx.get(f"{self.base_url}/system_stats", timeout=3.0).json()
        return sum(d.get("vram_free", 0) for d in stats.get("devices", []))

    def queue(self, workflow: dict[str, Any]) -> str:
        r = httpx.post(
            f"{self.base_url}/prompt",
            json={"prompt": workflow, "client_id": self.client_id},
            timeout=30.0,
        )
        if r.status_code != 200:
            raise ComfyError(f"prompt rejected ({r.status_code}): {r.text}")
        body = r.json()
        if "prompt_id" not in body:
            raise ComfyError(f"no prompt_id in response: {body}")
        return body["prompt_id"]

    def wait(self, prompt_id: str, timeout: float = 600.0, poll: float = 1.0) -> dict:
        """Poll history until the prompt completes; return its history entry."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = httpx.get(f"{self.base_url}/history/{prompt_id}", timeout=15.0)
            if r.status_code == 200:
                history = r.json()
                if prompt_id in history:
                    entry = history[prompt_id]
                    status = entry.get("status", {})
                    if status.get("status_str") == "error" or status.get("completed") is False:
                        raise ComfyError(
                            "execution failed: " + self._format_status(status)
                        )
                    return entry
            time.sleep(poll)
        raise ComfyError(f"timed out after {timeout}s waiting for {prompt_id}")

    @staticmethod
    def _format_status(status: dict) -> str:
        """Surface the node-level error messages ComfyUI buries in status.messages."""
        messages = status.get("messages", [])
        if not messages:
            return str(status)
        parts = []
        for kind, payload in messages:
            if kind == "execution_error":
                node = payload.get("node_type")
                msg = payload.get("exception_message", "").strip()
                parts.append(f"{node}: {msg}" if node else msg)
        return " | ".join(parts) or str(status)

    def output_images(self, history_entry: dict) -> list[dict]:
        images: list[dict] = []
        for node in history_entry.get("outputs", {}).values():
            images.extend(node.get("images", []))
        return images

    def upload_image(self, path: str, subfolder: str = "", overwrite: bool = True) -> dict:
        """Upload a local image into ComfyUI's input store; returns its descriptor.

        Stored as ``<stem>-<content hash><ext>``: ComfyUI's input store is flat, and
        different files with the same name overwrite each other. Registry references
        are all named by version (``characters/yuki/base.png``,
        ``characters/akira/base.png``), so in a two-shot the second upload replaced
        the first and both regions were guided by Akira's face. A content-derived name
        never collides, and identical images still share one file."""
        import hashlib
        from pathlib import Path

        p = Path(path)
        digest = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        name = f"{p.stem}-{digest}{p.suffix or '.png'}"
        with open(path, "rb") as fh:
            files = {"image": (name, fh, "image/png")}
            data = {"overwrite": "true" if overwrite else "false"}
            if subfolder:
                data["subfolder"] = subfolder
            r = httpx.post(f"{self.base_url}/upload/image", files=files, data=data, timeout=60.0)
        r.raise_for_status()
        return r.json()

    def fetch_image(self, image: dict) -> bytes:
        params = {
            "filename": image["filename"],
            "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        }
        r = httpx.get(f"{self.base_url}/view", params=params, timeout=60.0)
        r.raise_for_status()
        return r.content

    def run(self, workflow: dict[str, Any], timeout: float = 600.0) -> list[bytes]:
        """Queue a workflow and return the raw bytes of all output images."""
        entry = self.wait(self.queue(workflow), timeout=timeout)
        return [self.fetch_image(img) for img in self.output_images(entry)]
