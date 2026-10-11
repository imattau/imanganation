# Prop reference for another character reference — 2026-10-11

Props already reach panel renders as references, but a character's own reference
images showed them without the object, so every panel had to combine two references and
sometimes dropped or changed the prop, or got the grip wrong. "Design another
reference…" can now draw the character holding or wearing one of the project's props
(Qwen-Image 2.1 only), so a panel can take one reference with the object already right.
Never the base design: a prop baked into the default reference would follow the
character into panels that don't have it.

## How

- The variant request takes `variant_prop` (a prop with a picture). The character's
  default reference goes in as `<image1>`, the prop's picture as `<image2>`.
- The prompt says the object is exactly the one in `<image2>` and shown once; the pose
  changes from "arms relaxed at the sides" to holding or wearing it naturally; `props`
  leaves the negative prompt for that image.
- SDXL (one IP-Adapter slot, already the character) and Z-Anime (no image input) can't
  use it: the GIMP drop-down is hidden and the API answers 422.

## Check

Akira (rooftop cast, a scratch copy), seed 11, Qwen-Image 2.1, "a plain grey raincoat",
with and without the prop "red umbrella" (a red paper parasol, bamboo handle; seed 3).
Sheet: `2026-10-11_prop_reference.jpg`, left to right: the prop, Akira's base, the
variant without the prop, the variant with it.

- The umbrella matches the prop picture (canopy, ribs, handle) and appears once.
- Held on the shoulder with a believable grip.
- Identity carries over from the base (hair, eyes, tie, watch, backpack); only the
  raincoat and the prop were added.
- The canopy nearly touches the top of the frame: a large prop may get cropped. A
  "keep the whole object in frame" line is the likely fix if it does.

One seed, one character, one prop: a first look, not a measurement. Not yet checked:
whether panels that take this reference render the prop better than two separate
references do (`manganation eval` on a panel set would show it).
