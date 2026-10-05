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

## Build

```bash
packaging/flatpak/build.sh            # -> dist/imanganation-gimp.flatpak
packaging/flatpak/build.sh --install  # also install it for this user
```

Needs `flatpak` and `flatpak-builder`. The first build downloads the GNOME 51 SDK and
compiles GIMP's dependencies, which takes a long time; later builds reuse the cache in
`packaging/flatpak/.build/` and recompile only what changed.

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
