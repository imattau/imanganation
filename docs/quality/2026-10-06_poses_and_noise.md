# Interaction poses, and how noisy the eval is — 2026-10-06

## The idea

In masked two-shots each character's pose lives in their own masked prompt, and an
interaction filed under one character (Yuki: `pulling, holding another's wrist`) only
pulls at one side of it. The fix tried: tell the LLM to put interactions in `shared`
(with an example), and move any pose tag about another person (`…another…`, `pulling`,
`hug`, …) into the shared tags in code.

## A false start: confounded measurements

The first measurement (the new prompt and the move together, 8 seeds) said 82% -> 72%
on the two-character panels. But the new prompt also changed the LLM's other answers.
For the wide two-shot, the only difference left in the prompt was one dropped word
(`daytime`), and that alone moved "Akira reads as a boy" from 100% to 62%.

**Any prompt change re-rolls every image**, so at 3-8 seeds a one-word change swings
a panel by ~7 points and a single check by 25-38. Several earlier calls in
2026-10-05_staging.md were made on 3 seeds: the large effects (staging tags, `male
focus`) are well outside this, the small ones are not.

## The isolated test

Same LLM answer for the stairwell panel (the cached one), rendered twice: as is (the
drag in Yuki's pose) and with the drag moved to the shared tags. 24 seeds each.

| | As is | Moved | Change | Noise (2 s.e.) |
|---|---|---|---|---|
| overall | 72% | 65% | −7 | ±8 |
| the drag shows (`holding another's wrist\|…\|dragging`) | 46% | 25% | −21 | ±27 |
| two figures found | 79% | 50% | −29 | ±27 |
| stairs | 62% | 83% | +21 | ±26 |

No sign it helps; it trends worse, and the pair merged into one figure more often with
the drag up front in the shared prompt. **Not adopted.** Interactions stay where the LLM
puts them.

The rooftop suite has no *wide* two-shot with an interaction (the case the idea was
for), so that is untested.

## What was kept

- **`eval compare` shows a noise margin** per row (two standard errors of the
  difference; pass rates smoothed) and greys out changes within it.
- **The rooftop suite runs 8 seeds** (was 3).
- **Indoors/outdoors follows the place**: the LLM has tagged the school rooftop
  `indoors`. A place named in the setting (rooftop, pier, classroom, stairwell, …)
  now decides it, also for cached answers.
