#!/usr/bin/env bash
# Build the Imanganation GIMP Flatpak and a single-file bundle to hand to users.
#   packaging/flatpak/build.sh [--install] [make_manifest.py options...]
# The fork is built at its checkout's HEAD (committed code only, cached by commit);
# --fork-worktree builds it as it is, --fork-git/--fork-commit a pushed commit, and
# --fork-pinned the commit fork.json pins (what CI and releases build).
# Needs flatpak and flatpak-builder. The first build downloads the GNOME SDK (~1 GB)
# and compiles GIMP's dependencies, which takes a while; later builds reuse the cache.
# Output: dist/imanganation-gimp.flatpak   (install: flatpak install --user <file>)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BUILD="$HERE/.build"
INSTALL=()
ARGS=()
for arg in "$@"; do
  if [ "$arg" = "--install" ]; then INSTALL=(--install); else ARGS+=("$arg"); fi
done

command -v flatpak-builder >/dev/null || {
  echo "flatpak-builder is needed (e.g. apt install flatpak-builder)"; exit 1; }
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo

MANIFEST="$(cd "$ROOT" && uv run -q python "$HERE/make_manifest.py" ${ARGS[@]+"${ARGS[@]}"})"
APP_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["app-id"])' "$MANIFEST")"
BRANCH="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["branch"])' "$MANIFEST")"

# FLATPAK_BUILDER_ARGS: extra options (CI passes --disable-rofiles-fuse).
# shellcheck disable=SC2086
flatpak-builder --user --install-deps-from=flathub --force-clean --ccache \
  --state-dir="$BUILD/state" --repo="$BUILD/repo" ${FLATPAK_BUILDER_ARGS:-} \
  ${INSTALL[@]+"${INSTALL[@]}"} \
  "$BUILD/app" "$MANIFEST"

mkdir -p "$ROOT/dist"
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
  "$BUILD/repo" "$ROOT/dist/imanganation-gimp.flatpak" "$APP_ID" "$BRANCH"
echo "Bundle: $ROOT/dist/imanganation-gimp.flatpak"
echo "Install: flatpak install --user $ROOT/dist/imanganation-gimp.flatpak"
