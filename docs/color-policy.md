# Rendering policy: the engine is colour-only

**Decision (2026-10-04).** imanganation always renders **colour** panels. The engine
does not produce black & white, and does not choose a monochrome style. B&W, screentone,
and any other tonal treatment are the **artist's post-process in GIMP**.

## Why

- **Maximum options for the artist.** A colour render can be desaturated, thresholded,
  duotoned, halftoned, or selectively coloured in GIMP — and re-done cheaply. A B&W
  render can never be turned back into colour. Always storing colour keeps every door
  open.
- **GIMP is the right place.** The plug-in is where lettering and page assembly already
  happen; tone work belongs with them, on the artist's terms, non-destructively on
  layers.
- **It removes a real bug.** A colour character reference (IP-Adapter) overpowers a
  monochrome prompt, so "B&W via prompt/LoRA" was never reliable once a reference was
  attached. With a colour-only engine that conflict simply does not exist.
- **Less branching.** One style, one code path, fewer tests.

## What this means in practice

| Concern | Before | Now |
|---|---|---|
| Render style | `default_bw.yaml` + `default_color.yaml`, chosen per panel | one colour style |
| Monochrome LoRA | `manga_bw` LoRA applied in B&W mode | not applied |
| `color_mode` field | selected the style | **inert metadata**, always `color` |
| Screentone | prompt/LoRA in the engine | GIMP action / artist choice |
| B&W pages | engine output | GIMP post-process of a colour panel |

## `color_mode` is kept, but inert

`PanelSpec.color_mode` and `Script.default_color_mode` remain in the schema so that
existing `panels.json` files and the GIMP parasite stay valid backward-compatible. They
are **recorded, not used for generation**:

- New scripts/panels default to `color`.
- The renderer ignores the value when choosing a style.
- Old documents containing `inherit`/`bw` still load and render (in colour).

This keeps the door open for a future "render a B&W *style*" mode without a schema
migration, at the cost of one documented, unused field.

## GIMP-side B&W (the artist's toolkit)

The plug-in offers a **Convert to Manga B&W** action on the selected panel layer that
runs a sensible default chain (Desaturate → Curves) and leaves the result on a layer so
it stays non-destructive and tweakable. Halftone/screentone and thresholding remain
available through GIMP's own filters for artists who want them.
