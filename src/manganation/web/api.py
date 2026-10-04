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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from manganation.project import projects_root
from manganation.render import inpaint as inpaint_render
from manganation.render import panel as panel_render
from manganation.render import refiner as refiner_render
from manganation.render.comfy_client import ComfyClient


class RenderRequest(BaseModel):
    """Either form, not both (docs/engine-api.md):

    - **inline** (project container): ``project`` (``prj_…``) + ``panel`` (a container
      panel object; per-character ``version`` honoured) + ``reading_order``.
    - **legacy**: ``project_dir`` + ``seq`` (panel ``seq`` of ``panels.json``)."""

    project_dir: str | None = None
    seq: int | None = Field(default=None, ge=1,
                            description="1-based position of the panel in panels.json")
    project: str | None = Field(default=None, pattern=r"^prj_[a-z0-9]{6,}$")
    panel: dict | None = None
    reading_order: Literal["rtl", "ltr"] = "rtl"
    frame_width: float = Field(gt=0)
    frame_height: float = Field(gt=0)
    seed: int | None = None
    # character -> mask image (the artist's placement layer, frame-shaped; white or
    # opaque = where that character goes). Replaces the default reading-order bands.
    placements: dict[str, str] | None = None
    # Keep composition: an existing take whose layout and poses the render keeps
    # (ControlNet on its edges); the prompt decides the details.
    guide: str | None = None
    guide_strength: float | None = Field(default=None, ge=0, le=2)

    @model_validator(mode="after")
    def _one_form(self) -> RenderRequest:
        inline = self.project is not None or self.panel is not None
        legacy = self.project_dir is not None or self.seq is not None
        if inline == legacy:
            raise ValueError("send either project + panel (inline) or project_dir + seq")
        if inline and (self.project is None or self.panel is None):
            raise ValueError("inline renders need both project and panel")
        if legacy and (self.project_dir is None or self.seq is None):
            raise ValueError("legacy renders need both project_dir and seq")
        return self


class RefineRequest(BaseModel):
    """Container form: ``project`` + ``source`` + the origin take's size (and its
    render ``prompt``), from the project's take history. Legacy: ``project_dir`` +
    ``seq`` (origin and prompt found from sidecars)."""

    project_dir: str | None = None
    seq: int | None = Field(default=None, ge=1)
    project: str | None = Field(default=None, pattern=r"^prj_[a-z0-9]{6,}$")
    # The exact take to refine (e.g. what a GIMP layer shows); default: newest render.
    source: str | None = None
    origin_width: int | None = Field(default=None, gt=0)
    origin_height: int | None = Field(default=None, gt=0)
    prompt: str | None = None
    scale: float | None = Field(default=None, gt=0)
    denoise: float | None = Field(default=None, ge=0, le=1)
    seed: int | None = None

    @model_validator(mode="after")
    def _one_form(self):
        inline, legacy = self.project is not None, self.project_dir is not None
        if inline == legacy:
            raise ValueError("send either project (container id) or project_dir, not both")
        if legacy and self.seq is None:
            raise ValueError("the project_dir form needs seq")
        if inline and self.source is None:
            raise ValueError("the project form needs the exact source take")
        return self

    @model_validator(mode="after")
    def _origin(self):
        if self.project is not None and (self.origin_width is None or
                                         self.origin_height is None):
            raise ValueError("the project form needs origin_width and origin_height "
                             "(the origin take's size), so scales never compound")
        return self


class ReferenceRequest(BaseModel):
    project_dir: str | None = None
    project: str | None = Field(default=None, pattern=r"^prj_[a-z0-9]{6,}$")
    name: str = Field(min_length=1, description="An existing character (name or alias)")
    image_path: str = Field(description="PNG inside the project, e.g. exported by GIMP")
    version_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,40}$")

    @model_validator(mode="after")
    def _one_form(self):
        if (self.project is None) == (self.project_dir is None):
            raise ValueError("send either project (container id) or project_dir")
        return self


class InpaintRequest(BaseModel):
    """Container form: ``project`` + exact ``source`` + ``mask`` (absolute paths).
    Legacy: ``project_dir`` + ``seq``, paths inside that folder."""

    project_dir: str | None = None
    seq: int | None = Field(default=None, ge=1)
    project: str | None = Field(default=None, pattern=r"^prj_[a-z0-9]{6,}$")
    mask: str = Field(description="Mask image inside the project (alpha or black/white)")
    prompt: str = Field(min_length=1, description="What to paint in the region")
    source: str | None = Field(default=None, description="Init image (default: newest take)")
    denoise: float | None = Field(default=None, gt=0, le=1)
    grow_mask_by: int | None = Field(default=None, ge=0, le=256)
    seed: int | None = None
    # Who is in the patch: names, or container-style {"name", "version"}. Their traits
    # join the prompt; with exactly one, their reference guides it (on-model faces).
    characters: list[str | dict] | None = None
    # Identity strength for a single named character (IP-Adapter weight); default from
    # settings. Lower lets the prompt change more (e.g. an expression), higher holds
    # the reference closer.
    character_weight: float | None = Field(default=None, ge=0, le=1.5)

    def character_list(self) -> list[dict] | None:
        if not self.characters:
            return None
        return [c if isinstance(c, dict) else {"name": c} for c in self.characters]

    @model_validator(mode="after")
    def _one_form(self):
        inline, legacy = self.project is not None, self.project_dir is not None
        if inline == legacy:
            raise ValueError("send either project (container id) or project_dir, not both")
        if legacy and self.seq is None:
            raise ValueError("the project_dir form needs seq")
        if inline and self.source is None:
            raise ValueError("the project form needs the exact source take")
        return self


class CharacterRequest(BaseModel):
    """Create a character from the author's description and design it: traits from
    the description (LLM), then a locked design sheet (ComfyUI). One job, queued with
    the renders so the GPU is never shared."""

    project_dir: str | None = None
    project: str | None = Field(default=None, pattern=r"^prj_[a-z0-9]{6,}$")
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=4000)
    aliases: list[str] = Field(default_factory=list)
    seed: int | None = None
    # A character that has a design: re-derive traits from the description (empty
    # keeps them) and add a new design version as the active reference
    redesign: bool = False

    @model_validator(mode="after")
    def _one_form(self):
        if (self.project is None) == (self.project_dir is None):
            raise ValueError("send either project (container id) or project_dir")
        return self


class ScriptParseRequest(BaseModel):
    """A script as text. Page/panel scripts parse instantly; prose goes through the
    LLM (queued like any job, so it never shares the GPU with a render)."""

    text: str = Field(min_length=1, max_length=500_000)
    title: str = ""


JobKind = Literal["render", "refine", "inpaint", "character", "parse"]


@dataclass
class ParsedScript:
    format: str
    cast: list
    panels: list
    problems: list


class Job(BaseModel):
    id: str
    status: Literal["queued", "running", "done", "error"] = "queued"
    kind: JobKind = "render"
    request: dict
    result: dict | None = None
    error: str | None = None
    created: float = Field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None


def required_models(settings, models: dict, models_root: Path) -> list[dict]:
    """The model files the current settings need, and whether each is on disk.

    A missing file otherwise only shows up as a cryptic ComfyUI error mid-render."""
    from manganation.render.panel import RenderError, ipadapter_files

    out: list[dict] = []

    def add(role: str, subdir: str, file: str | None, note: str = "") -> None:
        present = bool(file) and (models_root / subdir / file).is_file()
        out.append({"role": role, "file": file, "present": present, "note": note})

    ckpt = models.get("checkpoints", {}).get("primary", {})
    add("checkpoint", ckpt.get("subdir", "checkpoints"), ckpt.get("id"))
    adapter = settings.defaults.ipadapter.adapter
    try:
        ipa, enc = ipadapter_files(models, adapter)
        add(f"ip-adapter ({adapter})", models["ipadapter"][adapter].get("subdir", "ipadapter"), ipa)
        add("clip vision", "ipadapter", enc)
    except (RenderError, KeyError) as exc:
        add(f"ip-adapter ({adapter})", "ipadapter", None, str(exc))
    cn = getattr(settings.defaults, "controlnet", None)
    if cn is not None:
        entry = models.get("controlnets", {}).get(cn.model)
        add(f"controlnet ({cn.model})", (entry or {}).get("subdir", "controlnet"),
            (entry or {}).get("id"), "" if entry else "not in models.yaml")
    up = settings.defaults.refiner.upscaler
    entry = models.get("upscalers", {}).get(up)
    add(f"upscaler ({up})", (entry or {}).get("subdir", "upscale_models"),
        (entry or {}).get("id"), "" if entry else "not in models.yaml")
    return out


def _comfy_stats(base_url: str) -> dict:
    import httpx

    stats = httpx.get(f"{base_url}/system_stats", timeout=2.0).json()
    return {
        "up": True,
        "version": stats.get("system", {}).get("comfyui_version"),
        "gpus": [{"name": d.get("name", "").split(" : ")[0],
                  "vram_total_gb": round(d.get("vram_total", 0) / 1024**3, 1),
                  "vram_free_gb": round(d.get("vram_free", 0) / 1024**3, 1)}
                 for d in stats.get("devices", [])],
    }


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
    comfy_stats=None,
    models_check=None,
    render_inline=panel_render.render_inline,
    refine_inline=refiner_render.refine_inline,
    inpaint_inline=inpaint_render.inpaint_inline,
    outputs: Path | None = None,
    design_character=None,
    parse_script=None,
) -> FastAPI:
    app = FastAPI(title="imanganation engine")
    jobs: dict[str, Job] = {}
    lock = threading.Lock()
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")

    def submit_job(kind: JobKind, request: dict, work) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, request=request)
        with lock:
            jobs[job.id] = job
        worker.submit(run, job.id, work)
        return job

    def run(job_id: str, work) -> None:
        with lock:
            job = jobs[job_id]
            job.status, job.started = "running", time.time()
        try:
            result = work()
            with lock:
                job.result, job.status = asdict(result), "done"
        except Exception as exc:  # surface any failure to the polling client
            with lock:
                job.error, job.status = str(exc), "error"
        finally:
            with lock:
                job.finished = time.time()

    def _engine_file(raw: str, what: str) -> Path:
        """A container-form image: an existing file under the projects root (where
        containers live) or the engine's outputs/ cache (a take not yet copied in)."""
        from manganation.config import REPO_ROOT

        roots = [(root or projects_root()).resolve(),
                 (outputs or REPO_ROOT / "outputs").resolve()]
        path = Path(raw).resolve()
        if not any(r in path.parents for r in roots):
            raise HTTPException(400, f"{what} must be under {roots[0]} or {roots[1]}")
        if not path.is_file():
            raise HTTPException(404, f"{what} not found: {path}")
        return path

    def _registry(project_dir: str | None, project: str | None):
        from manganation.characters.registry import CharacterRegistry
        from manganation.identity import IdentityError, identity_root

        if project is not None:
            try:
                return CharacterRegistry.from_path(identity_root(project, root=root))
            except IdentityError as exc:
                raise HTTPException(400, str(exc)) from exc
        return CharacterRegistry.from_path(resolve_project(project_dir, root))

    @app.get("/health")
    def health() -> dict:
        from manganation.config import load_settings

        return {"engine": "ok",
                "comfyui": ComfyClient(load_settings().comfyui.base_url).is_up()}

    @app.get("/status")
    def status_report() -> dict:
        """Everything an artist needs when something seems stuck or broken: is ComfyUI
        up (GPU, VRAM), what's running/queued, recent failures, missing model files."""
        from manganation.config import REPO_ROOT, load_models, load_settings

        settings = load_settings()
        try:
            comfy = (comfy_stats or _comfy_stats)(settings.comfyui.base_url)
        except Exception as exc:  # unreachable, or not ComfyUI
            comfy = {"up": False, "error": str(exc), "gpus": []}
        comfy["url"] = settings.comfyui.base_url
        models = (models_check or (lambda: required_models(
            settings, load_models(), REPO_ROOT / settings.paths.models_dir)))()

        now = time.time()

        def summary(j: Job) -> dict:
            out = {"id": j.id, "kind": j.kind, "status": j.status,
                   "seq": j.request.get("seq"),
                   "project": j.request.get("project")
                   or Path(j.request.get("project_dir", "")).name}
            if isinstance(j.request.get("panel"), dict):
                out["panel"] = j.request["panel"].get("id")
            if j.status == "running" and j.started:
                out["elapsed_s"] = round(now - j.started, 1)
            if j.finished and j.started:
                out["took_s"] = round(j.finished - j.started, 1)
            if j.error:
                out["error"] = j.error
            return out

        with lock:
            snapshot = [j.model_copy() for j in jobs.values()]
        finished = sorted((j for j in snapshot if j.finished), key=lambda j: -j.finished)
        return {
            "engine": "ok",
            "comfyui": comfy,
            "running": [summary(j) for j in snapshot if j.status == "running"],
            "queued": [summary(j) for j in sorted(snapshot, key=lambda j: j.created)
                       if j.status == "queued"],
            "recent": [summary(j) for j in finished[:5]],
            "counts": {k: sum(1 for j in snapshot if j.status == k)
                       for k in ("queued", "running", "done", "error")},
            "models": models,
        }

    @app.post("/jobs", status_code=202)
    def submit(req: RenderRequest) -> Job:
        extra = {}
        if req.placements:
            extra["placements"] = {name: _engine_file(path, f"placement for {name}")
                                   for name, path in req.placements.items()}
        if req.guide:
            extra["guide"] = _engine_file(req.guide, "guide")
            if req.guide_strength is not None:
                extra["guide_strength"] = req.guide_strength
        if req.panel is not None:
            from manganation.project_container import panel_to_spec

            try:
                panel_to_spec(req.panel)  # reject a bad spec now, not in the worker
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(422, f"invalid panel: {exc}") from exc
            return submit_job(
                "render", req.model_dump(exclude_none=True),
                lambda: render_inline(req.panel, req.project, req.frame_width,
                                      req.frame_height, reading_order=req.reading_order,
                                      seed=req.seed, **extra),
            )
        req.project_dir = str(resolve_project(req.project_dir, root))
        return submit_job(
            "render", req.model_dump(),
            lambda: render(Path(req.project_dir), req.seq, req.frame_width,
                           req.frame_height, seed=req.seed, **extra),
        )

    @app.post("/refine", status_code=202)
    def submit_refine(req: RefineRequest) -> Job:
        if req.project is not None:
            src = _engine_file(req.source, "source")
            return submit_job(
                "refine", req.model_dump(exclude_none=True),
                lambda: refine_inline(req.project, src, origin_width=req.origin_width,
                                      origin_height=req.origin_height, prompt=req.prompt,
                                      scale=req.scale, denoise=req.denoise, seed=req.seed),
            )
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
        if req.project is not None:
            src, mask = _engine_file(req.source, "source"), _engine_file(req.mask, "mask")
            return submit_job(
                "inpaint", req.model_dump(exclude_none=True),
                lambda: inpaint_inline(req.project, src, mask, prompt=req.prompt,
                                       denoise=req.denoise, grow_mask_by=req.grow_mask_by,
                                       seed=req.seed, characters=req.character_list(),
                                       character_weight=req.character_weight),
            )
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
                            grow_mask_by=req.grow_mask_by, seed=req.seed,
                            characters=req.character_list(),
                            character_weight=req.character_weight),
        )

    @app.get("/characters")
    def characters(project_dir: str | None = None, project: str | None = None) -> list[dict]:
        if (project is None) == (project_dir is None):
            raise HTTPException(422, "send either project (container id) or project_dir")
        reg = _registry(project_dir, project)
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

        if req.project is not None:
            image = _engine_file(req.image_path, "image")
        else:
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

        reg = _registry(req.project_dir, req.project)
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

    @app.post("/scripts/parse", status_code=202)
    def submit_parse(req: ScriptParseRequest) -> Job:
        """-> result ``{"format", "cast", "panels", "problems"}``, the plain-dict shape
        of ``script/formats/canonical.py`` (what the plug-in builds a project from).
        ``problems`` lists a script's lines that break the format (none for prose)."""
        from manganation.script.formats import canonical
        from manganation.script.parser import parse

        def work():
            canonical_text = canonical.looks_canonical(req.text)
            script = (parse_script or parse)(req.text, title=req.title)
            data = script.model_dump(mode="json")
            problems = canonical.parse(req.text)["problems"] if canonical_text else []
            return ParsedScript(format="canonical" if canonical_text else "prose",
                                cast=data["cast"], panels=data["panels"], problems=problems)

        return submit_job("parse", {"title": req.title, "chars": len(req.text)}, work)

    @app.post("/characters", status_code=202)
    def submit_character(req: CharacterRequest) -> Job:
        from manganation.characters.cast import design_character as default_design

        reg = _registry(req.project_dir, req.project)
        existing = reg.get(req.name)
        if existing is not None and existing.default_version is not None and not req.redesign:
            raise HTTPException(409, f"{existing.name} already has a design; send redesign "
                                     "to make a new one")
        if not req.description.strip() and (
                existing is None or not existing.appearance.appearance_tags()):
            raise HTTPException(422, f"describe {req.name} first")
        design = design_character or default_design
        return submit_job("character", req.model_dump(), lambda: design(
            reg, req.name, req.description, aliases=req.aliases, seed=req.seed,
            redesign=req.redesign))

    @app.get("/jobs/{job_id}")
    def status(job_id: str) -> Job:
        with lock:
            job = jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "unknown job")
            return job.model_copy()

    return app
