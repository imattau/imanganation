#!/usr/bin/env bash
# Run the uninstalled imanganation-gimp build with everything it needs:
#   scripts/gimp-dev.sh [gimp args...]
# Environment overrides: GIMP_BUILD (default /tmp/imanganation-gimp-build),
# GIMP_DEPS (default ~/.local/gimp-deps), GIMP3_DIRECTORY (default ~/.config/GIMP/3.3).
# The imanganation workspace starts the engine and ComfyUI with GIMP and stops them when
# it quits (IMANGANATION_AUTOSTART=0 to skip).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FORK="$ROOT/imanganation-gimp"
B="${GIMP_BUILD:-/tmp/imanganation-gimp-build}"
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
export GIMP3_DATADIR="$B/gimp-data"
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
