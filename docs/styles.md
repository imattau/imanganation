# Project styles

A project can have an overall look: a **preset** and, optionally, **your own words**. Set
it in GIMP under *Imanganation → Render Engine… → Style*. It is saved in `project.json`
as `project.render.style` (`{"preset": "retro_90s", "text": "autumn palette"}`) and goes
with every render, inpaint, and character and location design.

| Preset | Look |
|---|---|
| Clean modern anime (`default`) | Crisp line art and cel shading; what every measurement before this used |
| 90s cel anime (`retro_90s`) | Hand-painted cel look of 1990s TV anime |
| Gritty seinen ink (`seinen_ink`) | Heavy ink outlines, muted desaturated colour |
| Soft shōjo (`soft_shoujo`) | Delicate lines, pastel colours, sparkles, glowing light |
| Watercolour (`watercolor`) | Soft watercolour washes on paper |

Panels stay in colour whatever the look; black and white and screentone are done in GIMP
(`docs/color-policy.md`).

## The references decide the look

**Redesign the characters (and locations) after choosing a look.** GIMP offers to when
you change it. Measured on the rooftop suite (`docs/quality/2026-10-08_styles.md`):

- **Style words alone barely change SDXL panels.** With the characters' references still
  in the default look, the look showed in 0–18% of panels and the pages looked much the
  same. IP-Adapter carries the reference's look into every panel, and it outweighs the
  prompt.
- **With references drawn in the look, every preset shows on every panel**, and the
  page reads as one style. That is why the style also goes into character and location
  designs: the design has no reference pulling against it, so the tags take hold there,
  and the panels follow.
- **Accuracy cost differs per look.** Soft shōjo and Watercolour kept the default's
  accuracy (84% and 82% of checks, against 83%). 90s cel anime cost the most (71%):
  its redesigned references drifted the characters' hair and outfits. The numbers per
  preset are shown in the dialog.

**Qwen-Image follows the look from the words alone** (watercolour measured: every panel
painted, accuracy unchanged, no redesign). It reads the look as a sentence, and its
references are taken only for faces, hair and outfits. So the redesign matters most
for the default engine (SDXL/NoobAI).

Redesigning keeps each character's traits (no new description) and keeps the earlier
designs as versions, so you can pin an outfit or go back.

## Your own words

*Also* adds your words to every prompt: as tags for SDXL (`thick brush outlines, autumn
palette`) and as a sentence for Qwen-Image and Z-Anime. Short, concrete visual words
work best; artist names are not a supported way to get a look. A look of your own is
not measured: render a few panels before committing a chapter to it.

## For developers

- Presets: `config/styles/presets.yaml` (tags for SDXL, prose for the prose engines,
  negative, `check` tags for the eval, and `measured`, which GIMP shows).
- `styles.apply` layers a look on `default_color.yaml`; `load_style(options)` in
  `render/panel.py` is the one entry point.
- Engine: `GET /styles`; a `style` field on `POST /jobs`, `/inpaint`, `/characters`,
  `/locations` and `/locations/design`.
- Measure a look: `manganation eval run config/eval/rooftop.yaml --style retro_90s`. The
  report's `summary.style` is the share of renders whose `check` tags the tagger saw,
  kept apart from the accuracy score. Change a preset only with a run like that.

## Adult content (NSFW)

An author opts in per project: both New Project dialogs (and the Render Engine dialog) have
**Allow adult (NSFW) content in renders**. It is stored as `project.render.style.nsfw` and sent
as `style.nsfw` with renders, inpaints, refines and character/location designs.

- **Not flagged (the default):** `nsfw` is added to every negative prompt (`styles.apply`,
  including `extra_negative` for character designs).
- **Flagged:** the guard is dropped. Nothing adult is added to the positive prompt; the
  story and style text still decide what is drawn. Illustrious and NoobAI render adult
  content far better than Animagine, so pick the checkpoint with that in mind.
- Prompts that bypass the style (location plates and some inpaint negatives) have their own
  fixed negatives and are unchanged.
