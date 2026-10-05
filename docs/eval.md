# Render-accuracy eval

Does a render show what its panel asked for? `manganation eval` answers that with a
number, so a change to prompts, style, settings or models can be shown to help (or
not) instead of judged by eye from one seed.

## Setup (once)

```bash
uv sync --extra eval                 # onnxruntime + numpy, for the tagger
uv run manganation setup --eval      # WD SwinV2 tagger v3 (~470 MB) into models/taggers/
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

A run's score is the share of checks passed over all panels and seeds. The report also
breaks it down per kind, per panel and per check. `report.json` records the style,
panel and IP-Adapter settings and the git commit, so `compare` can tell you what changed.

## Writing a suite

```yaml
name: rooftop
script: projects/rooftop.imanga/script/script.md   # panels come from the real parser
identity: projects/_gimp_smoke                     # character registry to render with
seeds: [101, 202, 303]
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

- The tagger sees the whole picture. It can tell that someone grins, not *who*, so
  identity bleed between two characters (Akira getting Yuki's grin) only shows up
  partly, e.g. as a failed `forbid: grin` on a shot where nobody should grin.
- Three seeds is a small sample. Treat a change of a check or two as noise, and look
  at the sheet before believing it.
