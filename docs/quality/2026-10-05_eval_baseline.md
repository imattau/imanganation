# Render-accuracy baseline — 2026-10-05

First runs of `manganation eval` ([docs/eval.md](../eval.md)): the rooftop suite
(`config/eval/rooftop.yaml`), 5 panels × seeds 101/202/303, scored by the WD SwinV2 v3
tagger. Sheet for the current configuration: `2026-10-05_eval_baseline.jpg`.

## What changed between runs

| Run | Prompt code | Style prefix | Negative | Score | Count | Tags | Forbid |
|---|---|---|---|---|---|---|---|
| before | scene heading over panel location | `manga panel, anime illustration` | original | 64% | 47% | 59% | 100% |
| **code-only (current)** | panel location wins; "— cont." stripped; shot not repeated | original | original | **64%** | **47%** | **59%** | **100%** |
| new-negative | as current | original | + comic, border, bad hands, … | 64% | 40% | 62% | 100% |
| quality tags | as current | `masterpiece, best quality, newest, absurdres, highres` | + comic, border, … | 57% | 33% | 53% | 94% |

- **Location fix works:** the stairwell panel shows `stairs` at 3/3 seeds (was 2/3) and
  never the rooftop. The total is unchanged because one unrelated check moved the other way:
  a changed prompt changes the whole image, even at the same seed.
- **NoobAI quality tags hurt:** they pulled Akira toward a girl (skirts in close-ups) and
  dropped head-count checks to 33%. Not adopted.
- **Longer negative:** no measurable effect. Not adopted.

## Where the current renders fail

| Area | Pass rate | Examples |
|---|---|---|
| Identity (hair, eyes, uniform) | 67–100% | brown hair + blue streaks, white twintails, sailor collar |
| Action / pose | 0–67% | sitting at the fence 33%, standing/sitting in the two-shot 0%, wrist-drag 0%, sigh 0%, surprise 33% |
| Setting | 0–100% | rooftop 0%, outdoors 0%, chain-link fence 33%; stairs 100% |
| Framing | 0% | close-up / portrait / upper body for "close-up" |
| Lone male character reads as male | 0–33% | Akira alone is tagged `1girl` in most solo renders |

What this points at (the review of 2026-10-05):

1. **Action and setting are prose at the end of a ~150-token prompt.** Convert them to
   tags at parse time and put them before the costume tags; map shots to framing tags.
2. **Solo boys drift female.** His tags never say so beyond `1boy`; try `male focus` and
   a shorter list of costume tags.
3. **Two-shots are two separate pictures.** The regional reference bands keep each
   character in their own half, so "stands over him" can't happen. This needs
   placement-aware regions or Attention Couple.
