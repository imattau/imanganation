# Character poses

A panel can pin each character's pose with an OpenPose skeleton drawn from the pose
library (`config/poses.yaml`). The skeleton steers the render through a ControlNet
(`noob_openpose`); the character reference and prompt still decide how the figure looks.

```bash
uv run manganation setup --poses     # the 2.5 GB OpenPose ControlNet, once
```

## Using a pose

A panel's `poses` field maps a character to a library reference:

```json
"poses": {"Yuki": "drag_by_wrist.lead", "Akira": "drag_by_wrist.follow"}
```

| Reference | Meaning |
|---|---|
| `stand_hands_on_hips` | a solo pose, fitted to the character's region (or their placement layer) |
| `drag_by_wrist.lead` | one role of a pair pose: it places both figures and pins one hand to the other's wrist |
| `…@mirror` | either of the above, flipped left-right |

Only the SDXL engine uses poses. Settings: `defaults.pose` in `config/settings.yaml`
(`strength`, and the step window `start`/`end`). The staging LLM's pose tags for a posed
character are replaced by the library's, so they can't contradict the skeleton.

There is no script syntax yet: set `poses` in the project file (or on the render
request's panel).

## The library

A pose is a few angles (`src/manganation/pose/rig.py`), so it works from any camera
angle and mirrors for free. Limb segments are `[a, b]` in degrees: `a` from straight down
(0 hanging, 90 horizontal, 180 up), `b` around the body (0 forward, 90 outward, -90 across,
180 back). `yaw` turns the whole figure (0 faces the camera, 90 faces screen-right), and
`lean`, `sway`, `twist`, `head_yaw`, `head_pitch` bend the torso and head. `tags` are the
prompt tags that go with it. Pair poses place two figures and can `hold` one hand on the
other's joint (two-bone IK; if the hands are out of reach the figure is moved to meet).

To see the library, draw every pose and pair with `draw_figures` (`pose/draw.py`); the
unit tests (`tests/test_pose.py`) check that each reference composes.

## What was measured

Hand-built skeletons through NoobAI-XL with both character references and regional
prompts: a single figure follows its skeleton; a two-shot with one standing over one
sitting keeps both characters on-model; a stairs panel put the figures where the skeleton
said. Hands are not controlled (body-only skeletons), so a wrist hold is likely, not
guaranteed.

The ControlNet's repository (`Laxhar/noob_openpose`) states no licence.
