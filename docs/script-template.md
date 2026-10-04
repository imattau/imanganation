# Writing an imanganation script

An imanganation script is a plain text file (`.md` or `.txt`) in the page-and-panel
format manga and comic writers already use. **New Project from Script…** in GIMP reads
it and builds the whole project: the script panels, the cast (with your character
designs), the locations, and one page document per `PAGE`. The engine is not needed
for this, and nothing in the script is ever drawn as text: dialogue becomes speech
bubbles only when you letter a page.

A complete example that uses every feature is in
[`script-template.txt`](script-template.txt): copy it, keep the layout, replace the
story.

## The skeleton

```
CHARACTERS
NAME: age, gender, hair, eyes, build, outfit, marks. Personality, body language.
  An indented line continues the description above it.
OTHER NAME (aka Nickname, Title): ...

PAGE 1
[SCENE: Place — time of day]

Panel 1: Shot. What we see, naming every character who is in the picture.
NAME: A spoken line.
NAME (thought): A thought.
SFX: BANG
[[A note for yourself or the artist.]]

Panel 2: ...

PAGE 2
[SCENE: Another place — time]

Panel 1: ...
```

Tokens are not case-sensitive (`Page 1`, `panel 1:` and `Panel 1 -` all work), blank
lines are ignored, and a long action line can continue on the next lines.

## Characters (the cast block)

Optional, but it is how you design your characters. It goes **before the first
`PAGE`**:

```
CHARACTERS
MIO: 15, girl, short teal bob with a yellow hairclip, green eyes, freckles,
  oversized grey hoodie over a school skirt, white sneakers. Curious and restless.
KAITO (aka Kai): 16, boy, tall and lanky, spiky brown hair, silver earring.
  Calm, sarcastic, slouches.
```

- One entry per character, starting at the left edge: `NAME: description`. Lines that
  start with spaces continue the description above.
- `(aka …)` lists other names the script uses for them, comma-separated. Dialogue and
  action written with an alias count as that character.
- Names are written in capitals as in dialogue; `MIO` becomes `Mio`, `GRANDPA SATO`
  becomes `Grandpa Sato`.
- **The description is your design and it wins.** The engine turns it into the traits
  every render uses, keeping your words and only filling what you leave open, then
  draws the character's reference sheet. Describe what a reader would see: age,
  gender, build, hair (colour and style), eyes, skin, outfit, accessories and marks
  (scars, glasses, earrings). End with personality and body language (demeanour,
  posture, a habitual expression); these guide expressions without being drawn as
  appearance.
- A character who speaks in the story but is not in the block still joins the cast,
  with no design: write a description in their notes in Context, then use **Design
  character** (or right-click them in the Project tree).

## Pages, scenes and flashbacks

```
PAGE 3
[FLASHBACK START]
[SCENE: Harbor pier — fifty years ago]
Panel 1: ...
[FLASHBACK END]
[SCENE: Grandpa's house — morning]
Panel 2: ...
```

- `PAGE n` starts a page. Each page becomes its own page document, in the order the
  pages appear.
- `[SCENE: Place — time]` sets the scene for **the panels after it**, until the next
  scene heading. Put it before the panels it covers, never in the middle of a
  panel's lines. The part before the dash (`Harbor pier`) becomes a location in the
  project's assets.
- `CUT TO: place` on its own line sets the location of the **next** panel only (the
  panel after the cut). That place also joins the locations.
- `[FLASHBACK START]` … `[FLASHBACK END]` mark the panels between them as a flashback.

## Panels

```
Panel 2: Wide shot. Mio runs along the pier, Kaito trailing far behind her.
```

- `Panel n:` starts a panel, numbered within its page. Everything after the colon,
  and any plain lines that follow, is the panel's **action**: the picture to draw.
- **Name every character who is in the picture** in the action, as written in the cast
  block or by an alias (`Kai yawns`). That is how a panel knows who to draw. Someone
  who speaks in the panel is included automatically, even off-panel, so describe who
  is visible. Unnamed people ("a young woman", "the crowd") are drawn but not as cast
  members.
- Open the action with a **shot** so the framing is recorded. These are recognised:
  `extreme close-up`, `close-up`, `medium shot`, `full shot`, `wide shot`,
  `establishing shot`, `two-shot`, `reaction shot`, `over-the-shoulder`, `low angle`,
  `high angle`, `bird's-eye`, `worm's-eye`, `dutch angle`, `POV`.
- Keep one moment per panel: what a single still picture can show.

## Dialogue and sound effects

```
MIO: Hurry up!
KAITO (thought): Why did I agree to this.
MIO (whisper): Fifty years...?
MIO (shout): LOOK AT IT!
NARRATOR (narration): The summer everything changed started with a ship.
SFX: VRRRMMMM
```

- `NAME: line` is spoken dialogue. Add a kind in brackets for anything else:
  `(thought)`, `(whisper)`, `(shout)` or `(narration)`. A narrator is never treated as
  a character in the picture.
- `SFX: sound` is a sound effect.
- Lines stay in order. In GIMP each one gets a **Bubble…** button in the panel's
  Context, and the bubble picker opens on the matching style: speech → Speech,
  thought → Thought (cloud), whisper → Whisper (dashed), shout → Shout (burst),
  narration → Narration (box), SFX → Sound effect.
- Dialogue is never drawn into the renders; panels are rendered without text.

## Notes

```
[[Grandpa is always in his yellow raincoat. Keep the rod in his left hand.]]
```

`[[ … ]]` on its own line inside a panel is a note: kept with the panel (shown in
Context), never treated as action or dialogue.

## What the script does not set

These are set in GIMP after the project is built:

- **Expressions** per character (`Mio: wide grin; Kaito: bored`), in the panel's
  Context.
- **Aspect ratio** of a panel, in its Context (otherwise the frame you render into
  decides).
- **Character designs beyond the first**, with **Design character** (a new version)
  or *Set Character Reference from Layer*.
- **Page layouts and panel frames**: you draw these, or choose a layout for a page.

## Common mistakes

- **A colon in an action line.** `Note: the sky is red` reads as dialogue from a
  character called "Note". Use `[[note]]` for notes and rephrase action lines
  (`The sky is red.`).
- **A scene heading after a panel's lines** applies to the next panels, not the one
  above it. Put it before the panels it covers.
- **A character in the picture but never named in the action** (and silent) is not
  drawn as a cast member. Name them.
- **Cast entries indented by mistake** continue the previous description instead of
  starting a new character. Start each `NAME:` at the left edge.
- **A different spelling** (`Kaito`, `Kai`, `KT`) is a different person unless it is
  listed as an alias.

## Prose instead of panels

A script without `PAGE` / `Panel` lines is treated as prose: the engine's language
model breaks it into panels (the engine must be running). A `CHARACTERS` block at the
top still works the same way and is read exactly, not by the model. Panel scripts are
recommended: they are read instantly, exactly as written, with the engine off.
