"""imanganation command-line entry point."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import typer
from rich import print as rprint

app = typer.Typer(
    name="manganation",
    help="AI manga generation: script -> consistent, text-free panels.",
    no_args_is_help=True,
)
script_app = typer.Typer(help="Script parsing commands.")
app.add_typer(script_app, name="script")
character_app = typer.Typer(help="Character registry / design commands.")
app.add_typer(character_app, name="character")
project_app = typer.Typer(help="Project container commands (docs/project-container.md).")
app.add_typer(project_app, name="project")
eval_app = typer.Typer(help="Render-accuracy evaluation (docs/eval.md).")
app.add_typer(eval_app, name="eval")


@app.command()
def version() -> None:
    """Print the installed version."""
    from manganation import __version__

    rprint(f"imanganation {__version__}")


@app.command()
def serve(
    port: int = typer.Option(8790, help="Port to listen on (127.0.0.1 only)."),
    comfyui: bool = typer.Option(False, "--comfyui", help="Also run ComfyUI, as this "
                                 "engine's child (the Flatpak: nothing else starts it)."),
) -> None:
    """Run the local engine API (used by the GIMP plug-in)."""
    import uvicorn

    from manganation.config import remember_engine_home
    from manganation.web.api import create_app

    remember_engine_home()  # how a packaged (Flatpak) GIMP finds this checkout
    comfy = None
    if comfyui:
        import atexit

        from manganation.comfy_process import ComfyProcess
        from manganation.comfy_setup import installer_for
        from manganation.config import (
            comfy_paths_file,
            data_root,
            load_settings,
            models_root,
        )

        settings = load_settings()
        comfy = ComfyProcess(lambda log: installer_for(load_settings(), log=log),
                             settings.comfyui.base_url, comfy_paths=comfy_paths_file(),
                             models_root=models_root(settings),
                             log_file=data_root() / "comfyui.log")
        rprint(f"ComfyUI: {comfy.start()} ({settings.comfyui.base_url})")
        atexit.register(comfy.stop)

    uvicorn.run(create_app(comfy_process=comfy), host="127.0.0.1", port=port)


@app.command()
def render(
    project: str = typer.Argument(..., help="Project name under projects/."),
    seq: int = typer.Argument(..., help="Panel number in script order (1-based)."),
    width: float = typer.Option(1024, help="Target frame width (any unit; ratio matters)."),
    height: float = typer.Option(1024, help="Target frame height."),
    seed: int = typer.Option(None, help="Fixed seed (default: spec seed or random)."),
) -> None:
    """Render one panel to fit a frame of the given proportions."""
    from manganation.project import project_dir
    from manganation.render.panel import render_panel

    r = render_panel(project_dir(project), seq, width, height, seed=seed)
    rprint(f"[green]rendered[/green] {r.path}  {r.width}x{r.height}  seed={r.seed}")
    rprint(f"  prompt: {r.prompt}")


@app.command()
def refine(
    project: str = typer.Argument(..., help="Project name under projects/."),
    seq: int = typer.Argument(..., help="Panel number in script order (1-based)."),
    scale: float = typer.Option(
        None, help="Target size as a multiple of the panel's original render, never of "
        "an already-enlarged take (default: settings)."),
    denoise: float = typer.Option(
        None, help="img2img polish strength 0..1 (0 = pure upscale; default: settings)."
    ),
    seed: int = typer.Option(None, help="Fixed seed for the polish pass."),
) -> None:
    """Two-pass hi-res fix of an already-rendered panel (docs/phase6a.md)."""
    from manganation.project import project_dir
    from manganation.render.refiner import refine_panel

    r = refine_panel(project_dir(project), seq, scale=scale, denoise=denoise, seed=seed)
    rprint(
        f"[green]refined[/green] {r.path}  {r.width}x{r.height}  "
        f"upscaler={r.upscaler} denoise={r.denoise}"
    )


@app.command()
def inpaint(
    project: str = typer.Argument(..., help="Project name under projects/."),
    seq: int = typer.Argument(..., help="Panel number in script order (1-based)."),
    mask: Path = typer.Argument(..., exists=True, dir_okay=False,
                                help="Mask image (alpha or black/white), same size as the panel."),
    prompt: str = typer.Option(..., "--prompt", "-p", help="What to paint in the region."),
    source: Path = typer.Option(None, "--source", "-s", dir_okay=False,
                                help="Init image (default: newest panel take)."),
    denoise: float = typer.Option(None, help="Repaint strength 0..1 (default: settings)."),
    grow: int = typer.Option(None, "--grow", help="Mask dilation in px (default: settings)."),
    seed: int = typer.Option(None, help="Fixed seed for the repaint."),
) -> None:
    """Repaint the masked region of a panel (docs/inpaint.md)."""
    from manganation.project import project_dir
    from manganation.render.inpaint import inpaint_panel

    r = inpaint_panel(
        project_dir(project), seq, mask=mask.resolve(), prompt=prompt,
        source=source.resolve() if source is not None else None,
        denoise=denoise, grow_mask_by=grow, seed=seed,
    )
    rprint(f"[green]inpainted[/green] {r.path}  {r.width}x{r.height}  denoise={r.denoise}")


@app.command()
def doctor() -> None:
    """Check local prerequisites (GPU, ComfyUI, Ollama, model files)."""
    import httpx

    from manganation.config import REPO_ROOT, load_settings

    settings = load_settings()
    rprint("[bold]imanganation doctor[/bold]")

    # GPU check via the host python (ComfyUI owns the torch install).
    import subprocess
    probe = (
        "import torch; c=torch.cuda.is_available();"
        "print('|'.join([torch.__version__, str(c),"
        "(torch.cuda.get_device_name(0) if c else 'none'),"
        "(str(round(torch.cuda.get_device_properties(0).total_memory/1024**3,1)) if c else '0')]))"
    )
    interpreters = ["python3", str(REPO_ROOT / "vendor/ComfyUI/.venv/bin/python")]
    gpu_line = "[red]torch not found[/red]"
    for interp in interpreters:
        try:
            out = subprocess.run([interp, "-c", probe], capture_output=True, text=True, timeout=30)
            if out.returncode == 0:
                ver, cuda, name, vram = out.stdout.strip().splitlines()[-1].split("|")
                gpu_line = f"{ver}  cuda={cuda}  gpu={name}  vram={vram} GB"
                break
        except (FileNotFoundError, subprocess.SubprocessError):
            continue
    rprint(f"  torch: {gpu_line}")

    for label, url in (
        ("comfyui", f"{settings.comfyui.base_url}/system_stats"),
        ("ollama", f"{settings.llm.base_url}/api/tags"),
    ):
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code == 200:
                state = "[green]up[/green]"
            else:
                state = f"[yellow]{r.status_code}[/yellow]"
            rprint(f"  {label}: {state}")
        except Exception:
            rprint(f"  {label}: [red]down[/red]")

    from manganation.config import load_models, models_root
    from manganation.web.api import required_models

    missing = [m for m in required_models(settings, load_models(),
                                          models_root(settings))
               if not m["present"]]
    if missing:
        rprint(f"  models: [yellow]{len(missing)} missing[/yellow] "
               f"({', '.join(m['role'] for m in missing)}); run: manganation setup")
    else:
        rprint("  models: [green]all present[/green]")


def _gb(n: int | None) -> str:
    if n is None:
        return "? GB"
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


@app.command()
def setup(
    check: bool = typer.Option(False, "--check", help="Only report what's missing."),
    reuse: list[Path] = typer.Option(
        None, "--from", help="A folder of models you already have (ComfyUI, A1111, …): "
        "matching files are linked instead of downloaded. Repeatable."),
    verify: bool = typer.Option(False, "--verify",
                                help="Also hash files already in place (slow)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Download without asking."),
    evaluation: bool = typer.Option(False, "--eval",
                                    help="Also the tagger `manganation eval` judges with."),
) -> None:
    """Get the model files the engine needs: link ones you have, download the rest.

    Files are checked by size and SHA-256 (config/models.yaml). Downloads resume if
    interrupted. HF_TOKEN / HF_ENDPOINT are honoured for Hugging Face."""
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )
    from rich.table import Table

    from manganation import models_setup as ms
    from manganation.config import (
        comfy_paths_file,
        load_models,
        load_settings,
        models_root,
        remember_engine_home,
    )

    remember_engine_home()
    settings = load_settings()
    root = models_root(settings).resolve()
    models = ms.needed(settings, load_models())
    if evaluation:
        models += ms.evaluation(load_models())
    states = {m.role: ms.state(m, root, verify=verify) for m in models}

    if not check and reuse:
        candidates = [m for m in models if states[m.role] != "present"]
        if candidates:
            rprint(f"Looking for {len(candidates)} file(s) in "
                   f"{', '.join(str(f) for f in reuse)} (hashing size matches)…")
            for role, found in ms.find_existing(candidates, reuse).items():
                model = next(m for m in models if m.role == role)
                how = ms.link_into_place(found, model.path(root))
                states[role] = "present"
                rprint(f"  [green]linked[/green] {model.file} ← {found} ({how})")

    table = Table(title=f"Models in {root}")
    table.add_column("For")
    table.add_column("File", overflow="fold")
    table.add_column("Size", justify="right", no_wrap=True)
    table.add_column("State", no_wrap=True)
    colours = {"present": "green", "missing": "yellow"}
    for m in models:
        st = states[m.role]
        table.add_row(m.feature or m.role, m.file or f"[red]{m.note}[/red]", _gb(m.size),
                      f"[{colours.get(st, 'red')}]{st}[/]")
    rprint(table)

    if not check and ms.write_comfy_paths(comfy_paths_file(), root):
        rprint(f"Pointed ComfyUI's model paths at {root} "
               f"({comfy_paths_file()}); restart ComfyUI to pick it up.")

    todo = [m for m in models if states[m.role] != "present"]
    unfetchable = [m for m in todo if not m.urls]
    for m in unfetchable:
        rprint(f"[red]{m.role}: no download source[/red] ({m.note or 'add one to models.yaml'})")
    todo = [m for m in todo if m.urls]
    if not todo:
        rprint("[green]Everything the current settings need is in place.[/green]"
               if not unfetchable else "")
        raise typer.Exit(1 if unfetchable else 0)
    total = sum(m.size or 0 for m in todo)
    free = ms.free_bytes(root)
    rprint(f"\n{len(todo)} file(s) to download, {_gb(total)} ({_gb(free)} free).")
    for m in todo:
        rprint(f"  {m.file}: {m.license or 'licence unknown'}"
               + (f" — {m.license_url}" if m.license_url else ""))
    if check:
        raise typer.Exit(1)
    if total > free:
        rprint(f"[red]Not enough disk space: free up {_gb(total - free)} first.[/red]")
        raise typer.Exit(1)
    if not yes and not typer.confirm("Download them now, accepting those licences?"):
        raise typer.Exit(1)

    failed = []
    with Progress(TextColumn("{task.description}"), BarColumn(), DownloadColumn(),
                  TransferSpeedColumn(), TimeRemainingColumn()) as progress:
        for m in todo:  # most useful first: rendering works once the checkpoint lands
            task = progress.add_task(m.file, total=m.size)
            try:
                ms.download(m, root, progress=lambda done, size, task=task: progress.update(
                    task, completed=done, total=size))
            except ms.SetupError as exc:
                progress.update(task, description=f"[red]{m.file} failed")
                failed.append((m, exc))
    for _model, exc in failed:
        rprint(f"[red]{exc}[/red]")
    if failed:
        rprint("Run `manganation setup` again to retry; finished parts are kept.")
        raise typer.Exit(1)
    rprint("[green]Done: every model the current settings need is in place.[/green]")


@app.command("install-comfyui")
def install_comfyui(
    check: bool = typer.Option(False, "--check", help="Only report what would change."),
    gpu: str = typer.Option("auto", help="auto, cuda, rocm, mps or cpu (PyTorch build)."),
    target: Path = typer.Option(None, "--dir", help="Where ComfyUI goes "
                                "(default: settings.paths.comfyui_dir, vendor/ComfyUI)."),
    force: bool = typer.Option(False, "--force",
                               help="Overwrite local changes in the ComfyUI checkouts."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask before installing."),
    freeze: bool = typer.Option(False, "--freeze", help="Maintainers: write the installed "
                                "package versions to the constraints file and stop."),
) -> None:
    """Install the ComfyUI imanganation renders with: pinned commits, the right
    PyTorch for this GPU, the custom nodes, imanganation's patches. Safe to re-run."""
    from manganation import comfy_setup as cs
    from manganation import models_setup as ms
    from manganation.config import (
        CONFIG_DIR,
        comfy_paths_file,
        load_models,
        load_settings,
        models_root,
        remember_engine_home,
    )

    remember_engine_home()
    settings = load_settings()
    installer = cs.installer_for(settings, comfy=target and target.resolve(), log=rprint)
    comfy = installer.comfy
    if freeze:
        path = CONFIG_DIR / installer.pins["constraints"]
        path.write_text(installer.freeze())
        rprint(f"Wrote {path} from {comfy}'s venv.")
        raise typer.Exit(0)
    detected = cs.detect_gpu()
    chosen = detected if gpu in ("auto", detected.backend) else cs.Gpu(gpu)
    try:
        plan = installer.plan(chosen)
    except cs.InstallError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    g = plan.gpu
    rprint(f"[bold]ComfyUI[/bold] in {comfy}"
           + (f" (bundled; venv in {installer.venv})" if installer.bundled else ""))
    rprint("GPU: " + {"cuda": f"{g.name} (compute {g.capability}, {g.vram_gb} GB, driver "
                              f"CUDA {g.driver_cuda})",
                      "rocm": f"{g.name} (ROCm)", "mps": "Apple Silicon (Metal)",
                      "cpu": "[yellow]none found: CPU only, far too slow to render[/yellow]"
                      }.get(g.backend, g.backend))
    if g.backend == "cuda" and 0 < g.vram_gb < 11:
        rprint(f"[yellow]{g.vram_gb} GB of VRAM: SDXL needs about 12 GB; expect "
               "out-of-memory errors.[/yellow]")
    rprint(f"PyTorch: {plan.index or 'the default (PyPI) build'}")
    structural = plan.steps[:-2]  # the last two (requirements, patches) always run
    for step in plan.steps:
        rprint(f"  • {step}")
    if check:
        rprint("[green]Up to date.[/green]" if not structural
               else f"{len(structural)} change(s) to make; run without --check.")
        raise typer.Exit(1 if structural else 0)
    if structural and not yes and not typer.confirm("Go ahead?"):
        raise typer.Exit(1)
    try:
        installer.install(plan, force=force)
        rprint("Checking the install (PyTorch, then a ComfyUI start-up test)…")
        summary = installer.verify()
        installer.mark_ready()
    except (cs.InstallError, OSError) as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception as exc:  # a failed git/uv step: its output is already above
        rprint(f"[red]A step failed: {exc}[/red]")
        raise typer.Exit(1) from exc
    rprint(f"[green]ComfyUI is ready[/green]: torch {summary}")
    store = models_root(settings).resolve()
    if ms.write_comfy_paths(comfy_paths_file(), store):
        rprint(f"Pointed ComfyUI's model paths at {store}.")
    missing = [m for m in ms.needed(settings, load_models())
               if ms.state(m, store) != "present"]
    rprint("Next: [bold]manganation setup[/bold] to get the models."
           if missing else "Start it with ./scripts/comfy.sh start (GIMP starts it too).")


@script_app.command("parse")
def script_parse(
    source: Path = typer.Argument(..., exists=True, dir_okay=False, help="Script file to parse."),
    project: str = typer.Option(
        None,
        "--project",
        "-p",
        help="Write into projects/<name>/panels.json (else alongside input).",
    ),
    out: Path = typer.Option(None, "--out", "-o", help="Explicit output path for panels.json."),
    title: str = typer.Option("", "--title", help="Story title."),
    force_llm: bool = typer.Option(
        False, "--force-llm", help="Use the LLM even if the script looks canonical."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Print the parsed JSON to stdout instead of writing a file."
    ),
) -> None:
    """Parse a script (canonical page/panel format OR prose) into panels.json."""
    from manganation.project import ensure_project, panels_json_path
    from manganation.script.formats import canonical
    from manganation.script.parser import parse

    text = source.read_text()
    script = parse(text, title=title or source.stem, force_llm=force_llm)
    if not force_llm and canonical.looks_canonical(text):
        for problem in canonical.parse(text)["problems"]:  # the format guide: script-template
            rprint(f"[yellow]line {problem['line']}[/yellow]: {problem['message']}")

    if json_out:
        rprint(script.to_json())
        return

    if out is None:
        if project:
            root = ensure_project(project)
            out = panels_json_path(project)
            # Keep the original script with the project (needed for trait derivation).
            (root / "script.md").write_text(text)
        else:
            out = source.with_name("panels.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(script.to_json())

    pages = script.pages()
    rprint(
        f"[green]parsed[/green] {len(script.panels)} panels across {len(pages)} pages "
        f"-> [bold]{out}[/bold]"
    )
    for p in script.panels:
        who = ", ".join(p.characters) or "-"
        rprint(f"  p{p.page} panel {p.panel}: [cyan]{who}[/cyan]  {p.action[:60]}")


@character_app.command("suggest")
def character_suggest(
    project: str = typer.Argument(..., help="Project whose script defines the cast."),
    derive: bool = typer.Option(
        True, "--derive/--no-derive", help="Ask the LLM for missing appearances."
    ),
) -> None:
    """Derive the cast from a parsed project script into characters.json."""
    from manganation.characters.cast import assemble_cast
    from manganation.characters.registry import CharacterRegistry
    from manganation.project import project_dir, script_path
    from manganation.script.schema import Script

    panels = project_dir(project) / "panels.json"
    if not panels.exists():
        raise typer.BadParameter(f"no panels.json for project {project!r}; run script parse first")
    script = Script.from_json(panels.read_text())

    script_md = script_path(project)
    text = script_md.read_text() if script_md.exists() else ""

    plan = assemble_cast(
        script, project, registry=CharacterRegistry(project), derive=derive, script_text=text
    )
    rprint(
        f"[green]cast[/green] {len(plan.names)} characters, "
        f"{len(plan.created)} new -> projects/{project}/characters.json"
    )
    reg = CharacterRegistry(project)
    for name in plan.names:
        c = reg.get(name)
        traits = ", ".join(c.appearance.prompt_tags()[:6]) if c else "-"
        has_ref = "[green]yes[/green]" if reg.reference_path(name) else "[yellow]no[/yellow]"
        rprint(f"  [cyan]{name}[/cyan]  ref={has_ref}  {traits}")


@character_app.command("design")
def character_design(
    project: str = typer.Argument(..., help="Project name."),
    names: list[str] = typer.Option(
        None, "--name", "-n", help="Character(s) to design (default: all without a reference)."
    ),
    seed: int = typer.Option(None, "--seed", help="Fixed seed for reproducibility."),
    force: bool = typer.Option(False, "--force", help="Re-design even if a base exists."),
) -> None:
    """Generate single-figure reference images for the project's characters."""
    from manganation.characters.generator import generate_design
    from manganation.characters.registry import CharacterRegistry

    reg = CharacterRegistry(project)
    if not reg.cast.characters:
        raise typer.BadParameter(f"no characters for {project!r}; run character suggest first")

    targets = names or [c.name for c in reg.cast.characters]
    for name in targets:
        character = reg.get(name)
        if character is None:
            rprint(f"  [red]skip[/red] {name}: not in registry")
            continue
        if not force and character.version("base") is not None:
            rprint(f"  [yellow]skip[/yellow] {name}: base design already exists (use --force)")
            continue
        result = generate_design(character, reg, seed=seed, replace=force)
        rprint(
            f"  [green]designed[/green] {name} (seed {result.seed}) -> {result.image}"
        )


@character_app.command("dataset")
def character_dataset(
    project: str = typer.Argument(..., help="Project name."),
    names: list[str] = typer.Option(
        None, "--name", "-n", help="Character(s) to build a dataset for (default: all)."
    ),
    count: int = typer.Option(None, "--count", help="Images per character (default: settings)."),
    denoise: float = typer.Option(
        None, "--denoise", help="img2img strength: identity (low) vs variation (high)."
    ),
    seed: int = typer.Option(None, "--seed", help="Base seed for reproducibility."),
) -> None:
    """Build a synthetic training set per character from its design sheet."""
    from manganation.characters.dataset_generator import build_dataset
    from manganation.characters.registry import CharacterRegistry
    from manganation.project import project_dir

    reg = CharacterRegistry(project)
    if not reg.cast.characters:
        raise typer.BadParameter(f"no characters for {project!r}; run character suggest first")

    targets = names or [c.name for c in reg.cast.characters]
    for name in targets:
        character = reg.get(name)
        if character is None:
            rprint(f"  [red]skip[/red] {name}: not in registry")
            continue
        if reg.reference_path(name) is None:
            rprint(f"  [yellow]skip[/yellow] {name}: no design/reference image")
            continue
        result = build_dataset(
            character, project_root=project_dir(project),
            count=count, denoise=denoise, base_seed=seed,
        )
        rprint(
            f"  [green]dataset[/green] {name}: {result.count} images -> {result.root}"
        )


@character_app.command("list")
def character_list(
    project: str = typer.Argument(..., help="Project name."),
) -> None:
    """List the characters in a project and their reference status."""
    from manganation.characters.registry import CharacterRegistry

    reg = CharacterRegistry(project)
    if not reg.cast.characters:
        rprint(f"[yellow]no characters in {project}[/yellow]")
        return
    for c in reg.cast.characters:
        active = c.active_version()
        ref = "[green]yes[/green]" if active else "[yellow]no[/yellow]"
        versions = ", ".join(v.id for v in c.versions) or "-"
        rprint(
            f"  [cyan]{c.name}[/cyan]  ref={ref}  versions=[{versions}]  "
            f"default={c.default_version or '-'}"
        )


@character_app.command("show")
def character_show(
    name: str = typer.Argument(..., help="Character name."),
    project: str = typer.Option(..., "--project", "-p", help="Project name."),
) -> None:
    """Show a character's full manifest (traits + versions)."""
    from manganation.characters.registry import CharacterRegistry

    reg = CharacterRegistry(project)
    character = reg.get(name)
    if character is None:
        raise typer.BadParameter(f"no character {name!r} in {project!r}")
    rprint(character.model_dump_json(indent=2))


@character_app.command("add-ref")
def character_add_ref(
    name: str = typer.Argument(..., help="Character name."),
    image: Path = typer.Argument(..., exists=True, dir_okay=False, help="Reference image."),
    project: str = typer.Option(..., "--project", "-p", help="Project name."),
) -> None:
    """Attach a user-supplied reference image to a character."""
    from manganation.characters.registry import CharacterRegistry

    reg = CharacterRegistry(project)
    character = reg.add_user_reference(name, str(image))
    rprint(f"[green]added[/green] reference for {character.name} -> {reg.reference_path(name)}")


if __name__ == "__main__":
    app()


@project_app.command("import")
def project_import(
    project: str = typer.Argument(..., help="Legacy project name under projects/."),
    out: Path = typer.Option(
        None, "--out", "-o", help="Container folder to create (default: projects/<name>.imanga)."
    ),
) -> None:
    """Convert a legacy projects/<name>/ folder into a GIMP-owned project container."""
    from manganation.project import project_dir
    from manganation.project_import import import_project

    src = project_dir(project)
    dest = out or src.with_name(f"{src.name}.imanga")
    doc = import_project(src, dest)
    kinds: dict[str, int] = {}
    for t in doc["takes"].values():
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    rprint(f"[green]imported[/green] {src} -> [bold]{dest}[/bold]  ({doc['project']['id']})")
    rprint(f"  {len(doc['panels'])} panels, {len(doc['takes'])} takes "
           f"({', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))}), "
           f"{len(doc['cast'])} cast")
    for t in doc["takes"].values():
        if "import_note" in t.get("engine", {}):
            rprint(f"  [yellow]note[/yellow] {t['engine'].get('legacy_file')}: "
                   f"{t['engine']['import_note']}")


@project_app.command("link")
def project_link(
    container: Path = typer.Argument(..., exists=True, file_okay=False,
                                     help="Project container folder (has project.json)."),
    identity: str = typer.Argument(..., help="Folder under projects/ holding the cast's "
                                             "characters.json, e.g. rooftop."),
) -> None:
    """Point a container's project id at an existing character registry."""
    import json

    from manganation.identity import register
    from manganation.project import projects_root

    project_id = json.loads((container / "project.json").read_text())["project"]["id"]
    if not (projects_root() / identity / "characters.json").is_file():
        raise typer.BadParameter(f"projects/{identity} has no characters.json")
    register(project_id, identity)
    rprint(f"[green]linked[/green] {project_id} -> projects/{identity}")


# --- eval -------------------------------------------------------------------------


def _print_report(report: dict) -> None:
    from rich.table import Table

    summary = report["summary"]
    table = Table(title=f"{report['suite']} {report.get('label') or ''}: "
                        f"{summary['score']:.0%} of checks pass ({summary['images']} images)")
    table.add_column("Case")
    table.add_column("Score", justify="right")
    table.add_column("Failing checks (times failed)", overflow="fold")
    for case, score in summary["by_case"].items():
        fails: dict[str, int] = {}
        for r in report["results"]:
            if r["case"] == case:
                for c in r["checks"]:
                    if not c["ok"]:
                        fails[c["label"]] = fails.get(c["label"], 0) + 1
        table.add_row(case, f"{score:.0%}",
                      ", ".join(f"{k} ({n})" for k, n in fails.items()))
    rprint(table)
    rprint("By kind: " + ", ".join(f"{k} {v:.0%}" for k, v in summary["by_kind"].items()))
    if report["unknown_tags"]:
        rprint(f"[yellow]Not tags the tagger knows (fix the suite):[/yellow] "
               f"{', '.join(report['unknown_tags'])}")


@eval_app.command("run")
def eval_run(
    suite_file: Path = typer.Argument(..., help="Suite YAML, e.g. config/eval/rooftop.yaml."),
    label: str = typer.Option("", "--label", "-l", help="Name this run (e.g. 'baseline')."),
    out: Path = typer.Option(None, "--out", help="Run folder (default: outputs/eval/…). "
                             "An existing one resumes: renders already there are kept."),
    seeds: str = typer.Option("", "--seeds", help="Comma-separated seeds instead of the "
                              "suite's (quicker, but not comparable with full runs)."),
) -> None:
    """Render a suite's panels at fixed seeds, tag them, score them against the script."""
    import re

    from manganation.config import outputs_root
    from manganation.evaluate import suite as ev
    from manganation.evaluate.tagger import default_tagger

    suite = ev.load_suite(suite_file)
    if seeds:
        suite.seeds = [int(s) for s in seeds.split(",") if s.strip()]
    tagger = default_tagger()  # fail before an hour of rendering, not after
    if out is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-")
        out = outputs_root() / "eval" / f"{suite.name}-{stamp}{'-' + slug if slug else ''}"
    rprint(f"Run folder: {out}")
    snapshot = ev._snapshot()
    ev.render_all(suite, out, log=rprint)
    report = ev.score_all(suite, out, tagger, label=label, snapshot=snapshot)
    _print_report(report)
    rprint(f"Report: {out / 'report.json'}\nSheet: {out / 'sheet.png'}")


@eval_app.command("score")
def eval_score(
    run_dir: Path = typer.Argument(..., help="A run folder from `eval run`."),
    suite_file: Path = typer.Option(None, "--suite", help="Score against this suite "
                                    "(default: the one the run used)."),
) -> None:
    """Re-score a run's renders (after editing the suite's expectations) without
    re-rendering. The run's recorded config is kept."""
    from manganation.evaluate import suite as ev
    from manganation.evaluate.tagger import default_tagger

    old = json.loads((run_dir / "report.json").read_text())
    suite = ev.load_suite(suite_file or old["suite_path"])
    suite.seeds = old["seeds"]
    report = ev.score_all(suite, run_dir, default_tagger(), label=old.get("label", ""),
                          snapshot=old.get("config"))
    _print_report(report)


@eval_app.command("compare")
def eval_compare(
    before: Path = typer.Argument(..., help="Report (or run folder) to compare from."),
    after: Path = typer.Argument(..., help="Report (or run folder) to compare to."),
    all_rows: bool = typer.Option(False, "--all", help="Also checks that didn't change."),
) -> None:
    """Two runs side by side: overall, per kind, per case, and every check that moved."""
    from rich.table import Table

    from manganation.evaluate import suite as ev

    def load(path: Path) -> dict:
        return json.loads(((path / "report.json") if path.is_dir() else path).read_text())

    a, b = load(before), load(after)
    if a["seeds"] != b["seeds"]:
        rprint("[yellow]The runs used different seeds: differences are partly luck.[/yellow]")
    table = Table(title=f"{a.get('label') or before}  ->  {b.get('label') or after}")
    for col in ("", "Before", "After", "Change"):
        table.add_column(col, justify="left" if not col else "right", overflow="fold")
    fmt = lambda v: "-" if v is None else f"{v:.0%}"  # noqa: E731
    for i, (row, x, y) in enumerate(ev.compare(a, b)):
        is_check = ": " in row and row not in a["summary"]["by_case"]
        if is_check and x == y and not all_rows:
            continue
        change = "" if x is None or y is None else f"{y - x:+.0%}"
        colour = "green" if change.startswith("+") and change != "+0%" else (
            "red" if change.startswith("-") else "")
        table.add_row(row, fmt(x), fmt(y), f"[{colour}]{change}[/]" if colour else change,
                      end_section=i == 0)
    rprint(table)
