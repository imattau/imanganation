"""Step progress from ComfyUI's websocket, shown on running jobs (no GPU)."""

from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient
from websockets.sync.server import serve

from manganation.render.comfy_client import ComfyClient
from manganation.render.panel import RenderResult
from manganation.render.progress import CLIENT_ID, ProgressHub
from manganation.script.schema import PanelSpec, Script
from manganation.web.api import create_app


def _msg(kind, **data):
    return json.dumps({"type": kind, "data": data})


def test_hub_follows_passes_and_steps():
    hub = ProgressHub(lambda: "http://127.0.0.1:1")
    assert hub.snapshot() is None
    hub.handle(_msg("execution_start", prompt_id="a"))
    assert hub.snapshot() is None  # no step yet: loading models
    hub.handle(_msg("progress", value=5, max=28, prompt_id="a"))
    snap = hub.snapshot()
    assert (snap["pass"], snap["step"], snap["steps"]) == (1, 5, 28)
    hub.handle(_msg("progress", value=28, max=28))
    hub.handle(_msg("execution_start", prompt_id="b"))  # a refine/face pass queues another
    hub.handle(_msg("progress", value=2, max=10))
    snap = hub.snapshot()
    assert (snap["pass"], snap["step"], snap["steps"]) == (2, 2, 10)
    for junk in ("not json", _msg("status"), _msg("progress", value=1, max=0)):
        hub.handle(junk)
    assert hub.snapshot()["step"] == 2
    hub._thread = threading.Thread()  # stand-in so reset() doesn't start a listener
    hub.reset()
    assert hub.snapshot() is None


def test_every_comfy_client_listens_as_the_same_client():
    assert ComfyClient().client_id == ComfyClient("http://x").client_id == CLIENT_ID


def test_hub_reads_a_real_websocket():
    seen = []

    def handler(socket):
        seen.append(socket.request.path)
        socket.send(_msg("execution_start", prompt_id="a"))
        socket.send(b"\x00\x01preview")  # a binary preview frame is ignored
        socket.send(_msg("progress", value=3, max=20, prompt_id="a"))
        time.sleep(1)

    with serve(handler, "127.0.0.1", 0) as server:
        port = server.socket.getsockname()[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        hub = ProgressHub(lambda: f"http://127.0.0.1:{port}")
        hub.start()
        for _ in range(100):
            if hub.snapshot():
                break
            time.sleep(0.02)
        server.shutdown()
    assert hub.snapshot()["step"] == 3 and hub.snapshot()["steps"] == 20
    assert seen == [f"/ws?clientId={CLIENT_ID}"]


def test_a_running_job_reports_progress_and_a_finished_one_does_not(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "panels.json").write_text(Script(panels=[PanelSpec(page=1, panel=1)]).to_json())
    gate = threading.Event()

    class Hub:
        def reset(self):
            pass

        def snapshot(self):
            return {"pass": 1, "step": 7, "steps": 28}

    def render(path, seq, fw, fh, seed=None):
        gate.wait(5)
        return RenderResult(path="x.png", width=1, height=1, seed=1, prompt="p")

    client = TestClient(create_app(render=render, root=tmp_path, progress=Hub(),
                                   comfy_stats=lambda u: {}, models_check=lambda: []))
    job = client.post("/jobs", json={"project_dir": str(project), "seq": 1,
                                     "frame_width": 100, "frame_height": 100}).json()
    for _ in range(100):
        got = client.get(f"/jobs/{job['id']}").json()
        if got["status"] == "running":
            break
        time.sleep(0.02)
    assert got["progress"] == {"pass": 1, "step": 7, "steps": 28}
    assert client.get("/status").json()["running"][0]["progress"]["step"] == 7
    gate.set()
    for _ in range(100):
        done = client.get(f"/jobs/{job['id']}").json()
        if done["status"] == "done":
            break
        time.sleep(0.02)
    assert done["progress"] is None
