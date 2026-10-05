# Imanganation as a Flatpak

The customised GIMP (`imanganation-gimp`, a GIMP fork) with the Imanganation plug-in,
engine and ComfyUI built in, as one Flatpak that runs on any Linux distribution. Users
install a single file and click through **Set Up Models**: no terminal, no clone, no
GIMP compiling.

## For users

```bash
flatpak install --user imanganation-gimp.flatpak   # pulls the GNOME runtime from Flathub
```

Start Imanganation from the desktop menu. **Imanganation → Set Up Models…** opens by itself:
**Download** installs the renderer (ComfyUI and PyTorch for your GPU, about 5.5 GB on
disk) and then the models (about 15 GB). Already have the models (a ComfyUI or A1111
folder)? **Use Files I Have…** links them in without using more disk space. Projects
go in `~/Imanganation`.

NVIDIA GPUs need Flatpak's NVIDIA driver extension, which Flatpak installs to match the
driver; after a driver update it may need `flatpak update`. SDXL wants about 12 GB of
VRAM.

## What's inside, and where things go

| In the Flatpak (read-only) | Where |
|---|---|
| GIMP (the fork), babl and GEGL at pinned releases, GIMP's file-format libraries | `/app` |
| The Imanganation plug-in and its lettering fonts (Comic Neue, Bangers; SIL OFL) | `/app/lib/gimp/3.0/plug-ins/imanganation`, `/app/share/fonts/imanganation` |
| The engine (`src`, `config`), a `manganation` launcher, its Python packages | `/app/share/imanganation`, `/app/bin`; packages from `engine-deps.json` |
| ComfyUI and ComfyUI_IPAdapter_plus at the `config/comfyui.yaml` commits, patched | `/app/share/imanganation/vendor/ComfyUI` |
| `uv`, to install the renderer on first run | `/app/bin/uv` |

| Written at run time | Where |
|---|---|
| The renderer: a uv-managed Python 3.12, PyTorch for the GPU, ComfyUI's packages | `~/.var/app/io.github.imattau.Imanganation/data/imanganation/comfyui-venv`, `python/` |
| Models, render outputs, ComfyUI's user/input/output/temp folders, logs | the same data folder: `models/`, `outputs/`, `comfyui/`, `engine.log`, `comfyui.log` |
| Projects and their character identity stores | `~/Imanganation` |

How it runs: the plug-in starts the bundled engine inside the sandbox
(`manganation serve --comfyui`), and the engine starts its own ComfyUI as soon as the
renderer is installed (at launch, or right after Set Up installs it), so the first
render needs no restart. Both stop when GIMP quits. The sandbox reaches the GPU through
`--device=all` and Flatpak's NVIDIA driver extension (`libcuda`); the engine reads the
GPU from that library because the sandbox has no `nvidia-smi`.

Verified in the sandbox, from an empty data folder: the renderer installs (PyTorch
cu130 on an RTX 5060 Ti), existing models link in, the engine starts its ComfyUI, and
rooftop's two-shot renders with both character references in 26 s
(`docs/quality/2026-10-05_flatpak_bundled_render.png`).

**A developer checkout instead:** set `IMANGANATION_HOME` to it
(`flatpak override --user --env=IMANGANATION_HOME=/path/to/imanganation
io.github.imattau.Imanganation`); the plug-in then starts that checkout's engine and
ComfyUI on the host with `flatpak-spawn --host` (the manifest grants
`org.freedesktop.Flatpak`), as before.

## Build

```bash
packaging/flatpak/build.sh            # -> dist/imanganation-gimp.flatpak
packaging/flatpak/build.sh --install  # also install it for this user
```

Needs `flatpak`, `flatpak-builder` and `uv`; run it from the repo. The first build
downloads the GNOME 51 SDK and compiles GIMP's dependencies, which takes a long time;
later builds reuse the cache in `packaging/flatpak/.build/` and recompile only what
changed (the engine module always: its sources are directories, which flatpak-builder
can't checksum, so it comes last).

### CI and releases

`.github/workflows/flatpak.yml` builds the Flatpak on GitHub: on pushes to `main` and
pull requests (the bundle is a downloadable artifact of the run), on demand from the
Actions tab, and for a version tag, where it also creates a GitHub Release with the
bundle attached:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

CI builds the fork commit `fork.json` pins (`build.sh --fork-pinned`), not the fork's
latest: bump the pin when the fork changes. The builder's state is cached between runs
(main and tags only); the first build compiles everything and takes an hour or more.

`make_manifest.py` writes the manifest (`.build/<app id>.json`) from the fork's own
upstream manifest (`imanganation-gimp/build/linux/flatpak/org.gimp.GIMP-nightly.json`),
so GIMP's dependency list follows the fork as it merges upstream. It changes:

- **App:** app id `io.github.imattau.Imanganation`, GNOME runtime 51 instead of
  nightly, branch `stable`, the build id.
- **Meson and CMake modules** install into `lib` (with flatpak-builder 1.4 on this SDK
  they default to `lib64`, where nothing looks).
- **babl / GEGL:** pinned to `BABL_0_1_118` / `GEGL_0_4_66`, the releases the fork is
  developed against (upstream builds their git master).
- **GIMP:** the fork at its local checkout's HEAD: committed code only, and cached by
  commit, so GIMP rebuilds only when the fork changes. `--fork-worktree` builds the
  checkout as it is, uncommitted changes included (rebuilt every time); for a release,
  build a pushed commit: `--fork-git https://github.com/imattau/imanganation-gimp.git
  --fork-commit <sha>`.
- **The plug-in, engine, ComfyUI and uv** as modules after GIMP. The engine's Python
  packages are `engine-deps.json`: re-run `engine_deps.py` when `uv.lock` changes.
  ComfyUI's patch is generated from `comfy_setup.PATCHES` against the pinned file.
- **`--talk-name=org.freedesktop.Flatpak`**, for the developer-checkout mode.
