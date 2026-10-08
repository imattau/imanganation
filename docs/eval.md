# Render-accuracy eval

Does a render show what its panel asked for? `manganation eval` answers that with a
number, so a change to prompts, style, settings or models can be shown to help (or
not) instead of judged by eye from one seed.

## Setup (once)

```bash
uv sync --extra eval                 # onnxruntime + numpy, for the tagger
uv run manganation setup --eval      # tagger (~470 MB) + person detector (45 MB), models/taggers/
```

ComfyUI must be running (`./scripts/comfy.sh start`).

## Running

```bash
uv run manganation eval run config/eval/rooftop.yaml --label baseline
# … change something …
uv run manganation eval run config/eval/rooftop.yaml --label quality-tags
uv run manganation eval compare outputs/eval/rooftop-…-baseline outputs/eval/rooftop-…-quality-tags
```

Each run goes to `outputs/eval/<suite>-<time>-<label>/`. It holds the renders (with their
prompts in the `.json` beside each), `report.json` and `sheet.png`. The sheet has one
row per panel and one column per seed, each image captioned with the checks it failed.
An interrupted run resumes when given the same `--out`.

`eval score <run>` re-tags a run's existing renders against the suite as it is now.
Use it after fixing an expectation, so you don't have to render again.

## How it judges

The tagger ([WD SwinV2 v3](https://huggingface.co/SmilingWolf/wd-swinv2-tagger-v3))
reads a picture as Danbooru tags, the same vocabulary NoobAI was prompted with. It runs
on the CPU, so it never takes VRAM from ComfyUI. A tag counts as seen at probability
≥ the suite's `threshold` (0.35).

| Check | Passes when |
|---|---|
| **count** | The characters' count tags are seen (`1boy`, `1girl`), and no other count is (`2boys`, `solo` in a two-shot). Catches dropped, cloned and merged figures. Derived from the registry; `expect.count` overrides it. |
| **tags** | Each tag is seen; `a\|b` passes on either. |
| **forbid** | The tag is *not* seen: a grin on a sigh, the rooftop in the stairwell. |
| **figures** | The [person detector](https://huggingface.co/deepghs/anime_person_detection) finds as many figures as the panel has characters. Catches clones and characters fused into one figure, which the whole-picture tagger misses. |
| **identity** | Each figure is cropped and tagged alone, figures are matched to characters by the best assignment, and the suite's per-character `tags`/`forbid` are checked on each one's own crop. Costume bleed fails here ("Akira: not skirt"). |

The last two run when the suite has a `characters:` block. A figure whose box is more
than 30% covered by another figure (one standing behind the other) skips its `forbid`
checks: its crop shows both people, so "not skirt" would judge the wrong one. The sheet
draws each figure's box with the character it was matched to.

A run's score is the share of checks passed over all panels and seeds. The report also
breaks it down per kind, per panel and per check. `report.json` records the style,
panel and IP-Adapter settings and the git commit, so `compare` can tell you what changed.

## Writing a suite

```yaml
name: rooftop
script: config/eval/rooftop-script.md      # panels come from the real parser
identity: projects/_gimp_smoke                     # character registry to render with
seeds: [101, 202, 303, 404, 505, 606, 707, 808]
characters:                                        # checked on each one's own figure
  Akira: {tags: ["1boy|male focus", brown hair], forbid: [skirt, twintails]}
  Yuki: {tags: [1girl, twintails], forbid: [pants, brown hair]}
cases:
  - id: p2-3-drags-down-stairs
    panel: 2-3                    # PAGE-PANEL in the script
    frame: [704, 1344]            # the frame's size on the page
    expect:
      tags: [stairs, "holding another's wrist|hand grab|dragging", indoors]
      forbid: [rooftop]
    override: {}                  # optional PanelSpec fields, e.g. {expressions: {...}}
```

Expectations must be tags the tagger knows. The report lists any that aren't under
`unknown_tags`, because such a check can never pass. Write what the *script* asks for,
not what the current renders happen to show.

## Limits

- Case `tags`/`forbid` are judged on the whole picture: someone grins, not *who*.
  Put "who looks like what" in `characters:` instead.
- Scores depend on the checks: adding checks to a suite changes every run's total.
  Re-score an older run (`eval score <run> --suite …`) before comparing; `compare`
  warns when two reports were scored with different checks.
- **Noise.** Any prompt change re-rolls every image, even at the same seeds: removing
  one word ("daytime") moved a panel's 8-seed score by 7 points and single checks by
  25-38. `eval compare` shows a noise margin (two standard errors) per row and greys
  out changes within it. Use 8 seeds at least, and 24 on the panels a change targets,
  before believing a few points.
