#!/usr/bin/env bash
# Run the uninstalled imanganation-gimp build with everything it needs:
#   scripts/gimp-dev.sh [gimp args...]
# Environment overrides: GIMP_BUILD (default /tmp/imanganation-gimp-build),
# GIMP_DATA_STAGE (default $GIMP_BUILD/dev-install),
# GIMP_DEPS (default ~/.local/gimp-deps), GIMP3_DIRECTORY (default ~/.config/GIMP/3.3).
# The imanganation workspace starts the engine and ComfyUI with GIMP and stops them when
# it quits (IMANGANATION_AUTOSTART=0 to skip).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FORK="$ROOT/imanganation-gimp"
B="${GIMP_BUILD:-/tmp/imanganation-gimp-build}"
DATA_STAGE="${GIMP_DATA_STAGE:-$B/dev-install}"
D="${GIMP_DEPS:-$HOME/.local/gimp-deps}"
PROFILE="${GIMP3_DIRECTORY:-$HOME/.config/GIMP/3.3}"

[ -x "$B/app/gimp-3.3" ] || { echo "No build at $B; see $FORK/docs/development-build.md"; exit 1; }

export PATH="$D/usr/bin:$PATH"
export GI_TYPELIB_PATH="$B/libgimp:$D/lib/x86_64-linux-gnu/girepository-1.0"
LIBS=""
for lib in libgimp libgimpbase libgimpcolor libgimpconfig libgimpmath libgimpmodule \
           libgimpthumb libgimpwidgets; do
  LIBS="$LIBS$B/$lib:"
done
export LD_LIBRARY_PATH="$LIBS$D/lib:$D/lib/x86_64-linux-gnu:$D/usr/lib/x86_64-linux-gnu"
# The uninstalled build tree contains compiled resource icons, but not the full icon
# themes (those are install_data assets). Stage the build under /tmp so GTK can find
# the complete Default and Legacy themes without installing anything system-wide.
# `ninja install` rebuilds first, so it gets what a build needs from the dependency
# prefix, scoped to this step:
#   PYTHONNOUSERSITE  the system g-ir-scanner imports distutils.msvccompiler; a
#                     setuptools in ~/.local (pip --user) shadows the system one
#                     with a distutils that no longer has it
#   GI_GIR_PATH       .gir files the prefix provides (GExiv2)
#   CPATH, LIBRARY_PATH  headers and libraries of packages unpacked into the prefix
#                     (poppler-glib), whose .pc files still say /usr
PYTHONNOUSERSITE=1 \
GI_GIR_PATH="$D/share/gir-1.0" \
CPATH="$D/usr/include/poppler/glib:$D/usr/include/poppler:$D/usr/include" \
LIBRARY_PATH="$D/usr/lib/x86_64-linux-gnu:$D/lib/x86_64-linux-gnu" \
PKG_CONFIG_PATH="$D/lib/x86_64-linux-gnu/pkgconfig:$D/lib/pkgconfig:$D/usr/lib/x86_64-linux-gnu/pkgconfig:$D/usr/share/pkgconfig" \
DESTDIR="$DATA_STAGE" ninja -C "$B" install
DATA_DIR="$(sed -n 's/^#define GIMPDATADIR "\(.*\)"$/\1/p' "$B/config.h")"
[ -n "$DATA_DIR" ] || { echo "Could not read GIMPDATADIR from $B/config.h"; exit 1; }
export GIMP3_DATADIR="$DATA_STAGE$DATA_DIR"
export GIMP_TESTING_MENUS_PATH="$B/menus:$FORK/menus"
# The fork's defaults: workspace layout (sessionrc) and compact toolbox (toolrc).
export GIMP3_SYSCONFDIR="$FORK/etc"

# An uninstalled build finds no plug-ins of its own, so it cannot open PNG/JPEG/...
# (rendered panels included). Search the build's C plug-ins, plus the profile's
# plug-ins folder for imanganation: this variable replaces the normal search path.
PLUGINS="$B/plug-ins/common"
for dir in "$B"/plug-ins/*/; do
  case "$(basename "$dir")" in
    common|python|script-fu) ;;  # Python/Script-Fu need interpreters set up too
    *) PLUGINS="$PLUGINS:${dir%/}" ;;
  esac
done
export GIMP_TESTING_PLUGINDIRS="$PLUGINS:$PROFILE/plug-ins"
# The fork's C sample docks (plug-ins/common/extension-panels) are not the workspace.
export GIMP_TESTING_PLUGINDIRS_BASENAME_IGNORES="extension-panels"
[ -n "${GIMP3_DIRECTORY:-}" ] && export GIMP3_DIRECTORY

# Lettering fonts (assets/fonts, SIL OFL): GIMP reads fonts from <profile>/fonts.
mkdir -p "$PROFILE/fonts"
for font in "$ROOT"/assets/fonts/*.ttf; do
  [ -e "$PROFILE/fonts/$(basename "$font")" ] || ln -s "$font" "$PROFILE/fonts/"
done

exec "$B/app/gimp-3.3" "$@"
