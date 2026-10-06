# Location references — 2026-10-07

No model drew the rooftop into the two-shot ("rooftop" 0% everywhere: those renders sat
on near-empty backgrounds), and nothing kept one panel's rooftop like the next's.
Locations now work like characters: one reference image each, given to the renderer.

## How

- `manganation/locations.py`: a panel's place is keyed from its setting without time or
  continuation ("School rooftop — late afternoon" and "— cont." are both `school
  rooftop`; "the stairwell" is `stairwell`). Registry: `<identity>/locations/`.
- `manganation location design IDENTITY SCRIPT` draws each location's establishing view
  with Qwen-Image 2.1, no people, eye level; the details are the staging LLM's setting
  tags over every panel there (rooftop: chain-link fence, late afternoon).
  Sheet: `2026-10-07_location_refs.jpg`.
- Qwen-Image renders get it as one more reference: "exactly the place shown in
  <imageN> (keep its layout, materials, colours and light; frame it as this panel's
  shot describes)".
- **Continuity** (`manganation eval`): for each location and seed, the mean similarity
  of every pair of panels set there (background tags, cosine; palette outside the
  people's boxes, histogram overlap), 0-1. `eval compare` shows it with a noise margin.

## Measured (rooftop suite, 8 seeds; continuity over the 4 rooftop panels)

| Pipeline | Score | Head count | Identity | Scene tags | Continuity | Two-shot on the rooftop |
|---|---|---|---|---|---|---|
| NoobAI | 83% | 72% | 91% | 65% | 0.13 | 0% |
| Qwen | 86% | 85% | 91% | 70% | 0.36 | 0% |
| Qwen + faces | **88%** | **88%** | **93%** | 74% | 0.32 | 0% |
| Qwen + location | 82% | 48% | 86% | 73% | **0.79** | 25% |
| Qwen + location + faces | 85% | 57% | 88% | **77%** | **0.79** | **50%** |

Sheet: `2026-10-07_locations.jpg` (rows: Qwen, + location, + location + faces). Every
rooftop panel now shows the same rooftop (fence, utility hut, city at sunset), the
two-shot included, and the stairwell matches its reference. Continuity +0.43 ± 0.04.

**The cost:** a lone Akira on the rooftop reads as a girl more often (head count on the
fence panel 100% -> 0%). Qwen weighs a full-size location image like a character
reference, and copies its composition too (the fence panel became the wide establishing
view, with a small Akira).

Two fixes, on the three solo rooftop panels (8 seeds, all scored alike):

| Variant | Score | Identity | Continuity | Akira reads as a boy (fence / close-up / sigh) |
|---|---|---|---|---|
| Qwen, no location | 90% | 97% | 0.32 | 100% / 88% / 88% |
| + location, full size | 80% | 85% | 0.77 | 75% / 38% / 62% |
| + who is in the panel, up front ("exactly one person: a boy") | 83% | 92% | 0.77 | 88% / 62% / 75% |
| + location at a quarter of a reference's pixels | 84% | 91% | 0.74 | 88% / 75% / 75% |

Both adopted (`defaults.renderer.location_scale: 0.25`): most of the identity comes
back, nearly all the continuity stays. A gap to "no location" remains on Akira alone;
the full suite hasn't been re-run with both fixes.

## Next

- Re-run the full suite with both fixes plus the face pass (expected: the identity gap
  narrows further; the face pass repaints the faces).
- Locations for the SDXL engine (a regional IP-Adapter reference over the background).
- A location's reference per time of day, or none for a scene that changes light.
