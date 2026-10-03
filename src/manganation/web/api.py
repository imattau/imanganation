"""Local engine API: what the GIMP plug-in (and any other client) talks to.

Renders are queued as jobs and executed one at a time on a single worker thread —
the GPU is never shared between jobs. Clients poll ``GET /jobs/{id}``.

Bind to 127.0.0.1 only. Projects must live under the configured projects root.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from manganation.project import projects_root
from manganation.render import panel as panel_render
from manganation.render import refiner as refiner_render
from manganation.render.comfy_client import ComfyClient


class RenderRequest(BaseModel):
    project_dir: str
    seq: int = Field(ge=1, description="1-based position of the panel in panels.json")
    frame_width: float = Field(gt=0)
    frame_height: float = Field(gt=0)
    seed: int | None = None


class RefineRequest(BaseModel):
    project_dir: str
    seq: int = Field(ge=1)
    # The exact take to refine (e.g. what a GIMP layer shows); default: newest render.
    source: str | None = None
    scale: float | None = Field(default=None, gt=0)
    denoise: float | None = Field(default=None, ge=0, le=1)
    seed: int | None = None


class Job(BaseModel):
    id: str
    status: Literal["queued", "running", "done", "error"] = "queued"
    kind: Literal["render", "refine"] = "render"
    request: dict
    result: dict | None = None
    error: str | None = None
    created: float = Field(default_factory=time.time)


def resolve_project(project_dir: str, root: Path | None = None) -> Path:
    root = (root or projects_root()).resolve()
    path = Path(project_dir).resolve()
    if path != root and root not in path.parents:
        raise HTTPException(400, f"project must be under {root}")
    if not (path / "panels.json").exists():
        raise HTTPException(404, f"no panels.json in {path}")
    return path


def create_app(
    render=panel_render.render_panel,
    root: Path | None = None,
    refine=refiner_render.refine_panel,
) -> FastAPI:
    app = FastAPI(title="imanganation engine")
    jobs: dict[str, Job] = {}
    lock = threading.Lock()
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")

    def submit_job(kind: Literal["render", "refine"], request: dict, work) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, request=request)
        with lock:
            jobs[job.id] = job
        worker.submit(run, job.id, work)
        return job

    def run(job_id: str, work) -> None:
        with lock:
            job = jobs[job_id]
            job.status = "running"
        try:
            result = work()
            with lock:
                job.result, job.status = asdict(result), "done"
        except Exception as exc:  # surface any failure to the polling client
            with lock:
                job.error, job.status = str(exc), "error"

    @app.get("/health")
    def health() -> dict:
        from manganation.config import load_settings

        return {"engine": "ok",
                "comfyui": ComfyClient(load_settings().comfyui.base_url).is_up()}

    @app.post("/jobs", status_code=202)
    def submit(req: RenderRequest) -> Job:
        req.project_dir = str(resolve_project(req.project_dir, root))
        return submit_job(
            "render", req.model_dump(),
            lambda: render(Path(req.project_dir), req.seq, req.frame_width,
                           req.frame_height, seed=req.seed),
        )

    @app.post("/refine", status_code=202)
    def submit_refine(req: RefineRequest) -> Job:
        project = resolve_project(req.project_dir, root)
        req.project_dir = str(project)
        source = None
        if req.source is not None:
            source = Path(req.source).resolve()
            if project not in source.parents:
                raise HTTPException(400, f"source must be inside the project ({project})")
            if not source.is_file():
                raise HTTPException(404, f"source not found: {source}")
        return submit_job(
            "refine", req.model_dump(),
            lambda: refine(Path(req.project_dir), req.seq, source=source, scale=req.scale,
                           denoise=req.denoise, seed=req.seed),
        )

    @app.get("/jobs/{job_id}")
    def status(job_id: str) -> Job:
        with lock:
            job = jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "unknown job")
            return job.model_copy()

    return app
