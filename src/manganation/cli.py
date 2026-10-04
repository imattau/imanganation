"""imanganation command-line entry point."""

from __future__ import annotations

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


@app.command()
def version() -> None:
    """Print the installed version."""
    from manganation import __version__

    rprint(f"imanganation {__version__}")


@app.command()
def serve(
    port: int = typer.Option(8790, help="Port to listen on (127.0.0.1 only)."),
) -> None:
    """Run the local engine API (used by the GIMP plug-in)."""
    import uvicorn

    from manganation.web.api import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=port)


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
    """Check local prerequisites (GPU, ComfyUI, Ollama)."""
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
    from manganation.script.parser import parse

    text = source.read_text()
    script = parse(text, title=title or source.stem, force_llm=force_llm)

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
