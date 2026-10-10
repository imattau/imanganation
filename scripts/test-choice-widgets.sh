#!/usr/bin/env bash
# Build and run the fork's panel widgets on their own: the choice rows (drop-down and check list) and
# the strip layout (row or column by shape) in
# imanganation-gimp/app/widgets/gimpextensionpanel.c) on their own, without compiling GIMP:
# the code between "Choice rows" and panel_create_content is extracted into
# scripts/choice_widgets_harness.c, built against GTK 3 and run on a virtual display.
#   scripts/test-choice-widgets.sh [path to gimpextensionpanel.c]
# Needs gcc, GTK 3 development files and xvfb-run.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${1:-$HERE/../imanganation-gimp/app/widgets/gimpextensionpanel.c}"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
python3 - "$SRC" "$HERE/choice_widgets_harness.c" "$OUT/harness.c" <<'PY'
import sys
src, harness, out = (open(a).read() if i < 2 else a for i, a in enumerate(sys.argv[1:]))
start = src.index("/* Choice rows in a properties panel")
end = src.index("static GtkWidget *\npanel_create_content")
strip_start = src.index("/* Strip layout:")
strip_end = src.index("static GtkWidget *\npanel_create_scrolled_content")
head, _, rest = harness.partition("/*@@ CHOICE WIDGETS @@*/")
_, _, tail = rest.partition("/*@@ STRIP LAYOUT @@*/")
open(out, "w").write(head + src[start:end] + src[strip_start:strip_end] + tail)
PY
gcc -Wall -Wno-unused-function $(pkg-config --cflags gtk+-3.0) "$OUT/harness.c" -o "$OUT/harness" \
  $(pkg-config --libs gtk+-3.0)
xvfb-run -a "$OUT/harness"
