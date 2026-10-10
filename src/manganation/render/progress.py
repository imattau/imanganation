"""Step-by-step progress of what ComfyUI is rendering, for every kind of job.

ComfyUI reports progress only over its websocket (``/ws?clientId=…``) and only to the
client that queued the prompt, so every ``ComfyClient`` in the engine process shares one
client id (``CLIENT_ID``) and one ``ProgressHub`` listens with it. The engine runs one job
at a time, so the hub keeps just the running job's state: which pass it is on (a refine
or a face pass queues a prompt of its own) and the step within that pass.

Best effort: if the websocket is unavailable the state stays empty and clients fall back
to elapsed time.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable

log = logging.getLogger(__name__)

CLIENT_ID = uuid.uuid4().hex  # shared by every ComfyClient in this process


class ProgressHub:
    def __init__(self, base_url: Callable[[], str]):
        self._base_url = base_url
        self._lock = threading.Lock()
        self._state: dict | None = None
        self._thread: threading.Thread | None = None

    # --- reading ------------------------------------------------------------------

    def reset(self) -> None:
        """A new job starts: forget the last one's progress, and make sure the listener
        runs (it starts with the first job, so an engine with no ComfyUI costs nothing)."""
        with self._lock:
            self._state = None
        self.start()

    def snapshot(self) -> dict | None:
        """{"pass": n, "step": value, "steps": max} for the running prompt, or None if
        ComfyUI hasn't reported any step yet (loading models, an LLM step, …)."""
        with self._lock:
            return dict(self._state) if self._state and self._state.get("steps") else None

    # --- listening ----------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._listen, name="comfy-progress",
                                        daemon=True)
        self._thread.start()

    def _listen(self) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError:  # no websockets: progress stays unavailable
            return
        delay = 1.0
        while True:
            url = (self._base_url().replace("http://", "ws://").replace("https://", "wss://")
                   .rstrip("/") + f"/ws?clientId={CLIENT_ID}")
            try:
                with connect(url, open_timeout=5, max_size=None) as socket:
                    delay = 1.0
                    for message in socket:
                        if isinstance(message, str):  # binary frames are preview images
                            self.handle(message)
            except Exception as exc:  # noqa: BLE001 - ComfyUI down or restarting
                log.debug("progress websocket: %s", exc)
            time.sleep(delay)
            delay = min(delay * 2, 15.0)

    def handle(self, message: str) -> None:
        """One ComfyUI websocket message."""
        try:
            event = json.loads(message)
            kind, data = event.get("type"), event.get("data") or {}
        except (ValueError, AttributeError):
            return
        with self._lock:
            state = self._state or {"pass": 0, "step": 0, "steps": 0}
            if kind == "execution_start":
                state = {"pass": state["pass"] + 1, "step": 0, "steps": 0}
            elif kind == "progress" and data.get("max"):
                state = {**state, "pass": max(state["pass"], 1),
                         "step": int(data.get("value", 0)), "steps": int(data["max"])}
            elif kind == "execution_success":
                state = {**state, "step": state["steps"]}
            else:
                return
            state["updated"] = time.time()
            self._state = state
