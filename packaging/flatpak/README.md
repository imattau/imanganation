# Imanganation GIMP as a Flatpak

The customised GIMP (`imanganation-gimp`, a GIMP fork) with the Imanganation plug-in
built in, as one Flatpak that runs on any Linux distribution. Users install a single
file; no GIMP compiling, no dependency prefix.

## What's inside, and what isn't

| In the Flatpak | On the host |
|---|---|
| GIMP (the fork), babl and GEGL at pinned releases, GIMP's file-format libraries | The engine (`manganation`, this repository) |
| The Imanganation plug-in (`lib/gimp/3.0/plug-ins/imanganation`) | ComfyUI (`manganation install-comfyui`) and the models (`manganation setup`) |
| The fork's workspace defaults (`etc/sessionrc`, `etc/toolrc`) | Your projects |

The engine and ComfyUI stay outside the sandbox: they need the GPU, CUDA and many
gigabytes of models. The plug-in starts them on the host with `flatpak-spawn --host`
(the manifest grants `org.freedesktop.Flatpak` for that). They stop when GIMP quits.
GIMP finds the engine through `~/.config/imanganation/engine-home`, which the engine
writes whenever `manganation serve`, `setup` or `install-comfyui` runs.
`IMANGANATION_HOME` overrides it (`flatpak override --user --env=IMANGANATION_HOME=…`).

### Moving the engine inside (in progress)

The Flatpak also carries the engine and ComfyUI, so a user won't need the host
install at all. Verified: CUDA PyTorch runs in the sandbox at full speed (Flatpak's
NVIDIA driver extension provides `libcuda`), and the bundled engine starts and serves.

| Bundled now | Where |
|---|---|
| The engine (`src`, `config`) and a `manganation` launcher | `/app/share/imanganation`, `/app/bin` |
| Its Python packages, the `uv.lock` versions for the runtime's Python 3.14 | `engine-deps.json` (regenerate with `engine_deps.py` when `uv.lock` changes) |
| ComfyUI and ComfyUI_IPAdapter_plus at the `config/comfyui.yaml` commits, patched | `/app/share/imanganation/vendor/ComfyUI` |
| `uv`, to install PyTorch for the user's GPU on first run | `/app/bin/uv` |
| The lettering fonts (Comic Neue, Bangers; SIL OFL) | `/app/share/fonts/imanganation` |

**First run (done):** the bundled engine installs the renderer itself, into the app's
data folder (`~/.var/app/io.github.imattau.Imanganation/data/imanganation`): a uv-managed
Python 3.12, PyTorch for the GPU (read from the driver library: the sandbox has no
`nvidia-smi`), and ComfyUI's packages at the tested versions (about 5.5 GB). ComfyUI runs
from the read-only bundled code with its user, input, output and temp folders there
too. `manganation install-comfyui` does this, and Set Up Models shows it as its first
row (**renderer**), installed before the models. Verified in the sandbox from an
empty data folder: GPU found, PyTorch cu130, ComfyUI starts with the IP-Adapter nodes.

Still to do: keep models and outputs in the data folder, and start the bundled engine
and ComfyUI instead of the host's. Until then the plug-in uses the host install above.

## Build

```bash
packaging/flatpak/build.sh            # -> dist/imanganation-gimp.flatpak
packaging/flatpak/build.sh --install  # also install it for this user
```

Needs `flatpak` and `flatpak-builder`. The first build downloads the GNOME 51 SDK and
compiles GIMP's dependencies, which takes a long time; later builds reuse the cache in
`packaging/flatpak/.build/` and recompile only what changed.

Run it from the repo (it uses `uv run` to read `config/comfyui.yaml`).
`make_manifest.py` writes the manifest (`.build/<app id>.json`) from the fork's own
upstream manifest (`imanganation-gimp/build/linux/flatpak/org.gimp.GIMP-nightly.json`),
so GIMP's dependency list follows the fork as it merges upstream. It changes:

- **App:** app id `io.github.imattau.Imanganation`, GNOME runtime 51 instead of
  nightly, branch `stable`, the build id.
- **babl / GEGL:** pinned to `BABL_0_1_118` / `GEGL_0_4_66`, the releases the fork is
  developed against (upstream builds their git master).
- **GIMP:** your local `imanganation-gimp` checkout as it is, uncommitted changes
  included. For a release, build a pushed commit instead:
  `build.sh --fork-git https://github.com/imattau/imanganation-gimp.git --fork-commit <sha>`.
- **The plug-in**, as a module after GIMP.
- **`--talk-name=org.freedesktop.Flatpak`**, so the plug-in can start the engine.

## For users

```bash
flatpak install --user imanganation-gimp.flatpak   # pulls the GNOME runtime from Flathub
git clone https://github.com/imattau/imanganation && cd imanganation
uv sync && uv run manganation install-comfyui && uv run manganation serve
```

Run `serve` once, so GIMP can find the engine, then stop it and start GIMP from the
desktop menu. GIMP starts the engine and ComfyUI itself, and **Imanganation → Set Up
Models…** fetches the models.
