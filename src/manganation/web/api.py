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
from manganation.render import inpaint as inpaint_render
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


class ReferenceRequest(BaseModel):
    project_dir: str
    name: str = Field(min_length=1, description="An existing character (name or alias)")
    image_path: str = Field(description="PNG inside the project, e.g. exported by GIMP")
    version_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,40}$")


class InpaintRequest(BaseModel):
    project_dir: str
    seq: int = Field(ge=1)
    mask: str = Field(description="Mask image inside the project (alpha or black/white)")
    prompt: str = Field(min_length=1, description="What to paint in the region")
    source: str | None = Field(default=None, description="Init image (default: newest take)")
    denoise: float | None = Field(default=None, gt=0, le=1)
    grow_mask_by: int | None = Field(default=None, ge=0, le=256)
    seed: int | None = None


class Job(BaseModel):
    id: str
    status: Literal["queued", "running", "done", "error"] = "queued"
    kind: Literal["render", "refine", "inpaint"] = "render"
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
    inpaint=inpaint_render.inpaint_panel,
) -> FastAPI:
    app = FastAPI(title="imanganation engine")
    jobs: dict[str, Job] = {}
    lock = threading.Lock()
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")

    def submit_job(kind: Literal["render", "refine", "inpaint"], request: dict, work) -> Job:
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

    def _inside(project: Path, raw: str, what: str, *, required: bool) -> Path | None:
        path = Path(raw)
        path = (path if path.is_absolute() else project / path).resolve()
        if project not in path.parents:
            raise HTTPException(400, f"{what} must be inside the project ({project})")
        if not path.is_file():
            if required:
                raise HTTPException(404, f"{what} not found: {path}")
            return None
        return path

    @app.post("/inpaint", status_code=202)
    def submit_inpaint(req: InpaintRequest) -> Job:
        project = resolve_project(req.project_dir, root)
        req.project_dir = str(project)
        mask = _inside(project, req.mask, "mask", required=True)
        # An explicit source is the exact take a GIMP layer shows: if it's gone, say so
        # rather than silently painting a different take.
        source = _inside(project, req.source, "source", required=True) if req.source else None
        return submit_job(
            "inpaint", req.model_dump(),
            lambda: inpaint(Path(req.project_dir), req.seq, mask=mask, prompt=req.prompt,
                            source=source, denoise=req.denoise,
                            grow_mask_by=req.grow_mask_by, seed=req.seed),
        )

    @app.get("/characters")
    def characters(project_dir: str) -> list[dict]:
        from manganation.characters.registry import CharacterRegistry

        reg = CharacterRegistry.from_path(resolve_project(project_dir, root))
        return [
            {"name": c.name, "aliases": c.aliases, "default_version": c.default_version,
             "versions": [v.id for v in c.versions],
             "reference": str(reg.reference_path(c.name) or "") or None}
            for c in reg.cast.characters
        ]

    @app.post("/characters/reference")
    def set_reference(req: ReferenceRequest) -> dict:
        """Register an image as a character's new active reference (a new version;
        earlier versions are kept). Synchronous: a file copy, no GPU."""
        from PIL import Image, UnidentifiedImageError

        from manganation.characters.registry import CharacterRegistry

        project = resolve_project(req.project_dir, root)
        image = Path(req.image_path).resolve()
        if project not in image.parents:
            raise HTTPException(400, f"image must be inside the project ({project})")
        if not image.is_file():
            raise HTTPException(404, f"image not found: {image}")
        try:
            with Image.open(image) as im:
                im.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(400, f"not a readable image: {exc}") from exc

        reg = CharacterRegistry.from_path(project)
        character = reg.get(req.name)
        if character is None:
            known = ", ".join(c.name for c in reg.cast.characters) or "none"
            raise HTTPException(404, f"no character {req.name!r} (known: {known})")
        previous = character.default_version
        version_id = req.version_id
        if version_id is None:  # never overwrite: gimp-01, gimp-02, …
            n = 1
            while character.version(f"gimp-{n:02d}") is not None:
                n += 1
            version_id = f"gimp-{n:02d}"
        elif character.version(version_id) is not None:
            raise HTTPException(409, f"{character.name} already has version {version_id!r}")
        reg.add_user_reference(character.name, str(image), version_id)
        return {"name": character.name, "version": version_id, "previous": previous,
                "reference": str(reg.reference_path(character.name))}

    @app.get("/jobs/{job_id}")
    def status(job_id: str) -> Job:
        with lock:
            job = jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "unknown job")
            return job.model_copy()

    return app
