# Writing an imanganation script

An imanganation script is a plain text file (`.md` or `.txt`) made of **pages**, each
page made of **panels**, and each panel made of labelled **sections**. Every line sits
under a label that says what it is, so nothing is guessed: an action line with a colon
in it is still action, and a line in the wrong place is reported with its line number.

**New Project from Script…** in GIMP reads the script and builds the whole project: the
script panels, the cast (with your character designs), the locations (with your place descriptions), and one page
document per `PAGE`. The engine is not needed for this. Nothing in the script is ever
drawn as text; dialogue becomes speech bubbles only when you letter a page.

A complete example that uses every feature is in
[`script-template.txt`](script-template.txt): copy it, keep the layout, replace the
story.

## The skeleton

```
[CHARACTERS]
NAME: age, gender, hair, eyes, build, outfit, marks. Personality, body language.
  An indented line continues the description above it.
OTHER NAME (aka Nickname): ...

[LOCATIONS]
Place: inside or out, era, layout, landmarks, light.
  An indented line continues the description above it.
Another place: ...

COVER
[SCENE: Place — time of day]
[SHOT: low angle]
[CHARACTERS: Name, Other Name]
[ACTION]
The picture for the front cover.
[NOTES]
Leave the top clear for the title.

PAGE 1
[SCENE: Place — time of day]

PANEL 1
[SHOT: wide shot]
[FRAME: wide, large]
[CHARACTERS: Name, Other Name]
[EXPRESSIONS: Name: smiling; Other Name: worried]
[ACTION]
What we see in the picture.
[DIALOGUE]
NAME: A spoken line.
OTHER NAME (thought): A thought.
[SFX]
BANG
[NOTES]
A note for yourself or the artist.

PANEL 2
[ACTION]
...

PAGE 2
[SCENE: Another place — time]

PANEL 1
[ACTION]
...
```

## The building blocks

| Line | Where | What it does |
|---|---|---|
| `[CHARACTERS]` | top, before `PAGE 1` | starts the cast block (optional) |
| `[LOCATIONS]` | top, before `PAGE 1` | starts the locations block (optional) |
| `COVER` | own line, before `PAGE 1` | starts the cover (optional, once) |
| `PAGE n` | own line | starts a page |
| `[SCENE: Place — time]` | between panels | the scene for the panels after it |
| `[FLASHBACK START]` / `[FLASHBACK END]` | between panels | panels between them are a flashback |
| `PANEL n` | own line, under a page | starts a panel, numbered within its page |
| `[SHOT: …]` | in a panel | the shot (framing) |
| `[FRAME: …]` | in a panel | the frame's shape and size on the page (optional) |
| `[CHARACTERS: …]` | in a panel | exactly who is in the picture |
| `[EXPRESSIONS: …]` | in a panel | each character's expression |
| `[LOCATION: …]` | in a panel | this panel's place, when it differs from the scene |
| `[ACTION]` | in a panel | section: what we see |
| `[DIALOGUE]` (or `[DIALOG]`) | in a panel | section: spoken lines, thoughts, narration |
| `[SFX]` | in a panel | section: sound effects, one per line |
| `[NOTES]` | in a panel | section: notes, never drawn |

Labels and `PAGE` / `PANEL` are not case-sensitive, and blank lines are ignored. A
section runs until the next label, `PANEL` or `PAGE`, so no end marker is needed
(`[END DIALOGUE]` is accepted if you like closing them). Sections can come in any
order within a panel.

## Characters (the cast block)

```
[CHARACTERS]
MIO: 15, girl, short teal bob with a yellow hairclip, green eyes, freckles,
  oversized grey hoodie over a school skirt, white sneakers. Curious and restless.
KAITO (aka Kai): 16, boy, tall and lanky, spiky brown hair, silver earring.
  Calm, sarcastic, slouches.
```

- One entry per character, at the left edge: `NAME: description`. Indented lines
  continue the description above; any other line is reported.
- `(aka …)` lists other names the script uses for them, comma-separated. Wherever a
  name is used (`[CHARACTERS: Kai]`, `KAI: …`), an alias counts as that character.
- Names in capitals become title case: `MIO` is `Mio`, `GRANDPA SATO` is
  `Grandpa Sato`.
- **The description is your design and it wins.** The engine turns it into the traits
  every render uses, keeping your words and only filling what you leave open, then
  draws the character's reference sheet. Describe what a reader would see: age,
  gender, build, hair (colour and style), eyes, skin, outfit, accessories and marks
  (scars, glasses, earrings). End with personality and body language (demeanour,
  posture, a habitual expression); these guide expressions without being drawn as
  appearance.
- With a cast block, a speaker or a `[CHARACTERS: …]` name that is not in it is
  reported, which catches typos (`Kiato`). A character listed without a description
  joins the cast with no design: write one in their notes in Context, then use
  **Design character**.

## The cover

```
COVER
[SCENE: Harbor pier — sunrise]
[SHOT: low angle]
[CHARACTERS: Mio, Kaito]
[EXPRESSIONS: Mio: excited grin; Kaito: wary]
[ACTION]
Mio and Kaito stand at the end of the pier, a huge black ship behind them.
[NOTES]
Leave the top third clear for the title.
```

- `COVER` goes after the cast block and before `PAGE 1`, once. It is one picture, so
  it has no `PANEL` line and takes the same fields as a panel: `[SCENE]`, `[SHOT]`,
  `[CHARACTERS]`, `[EXPRESSIONS]`, `[LOCATION]`, `[ACTION]` and `[NOTES]`.
- **The cover is text-free**: no `[DIALOGUE]` or `[SFX]`, and no `[FRAME]` (it fills
  its page). The title, volume and credits are lettered afterwards in GIMP with
  **Design Front Cover…**, over the artwork.
- **New Project from Script…** gives the cover its own page document, labelled
  **Cover** and placed first. Render it into the page's frame like any panel; the
  engine draws a finished key illustration with no title, logo or lettering.
- Describe the cover as you would a splash page: a striking moment that shows the
  characters and the world. Leave room (say so in `[NOTES]`) where the title will go.

## Pages and scenes

```
PAGE 3
[FLASHBACK START]
[SCENE: Harbor pier — fifty years ago]

PANEL 1
...

[FLASHBACK END]
[SCENE: Grandpa's house — morning]

PANEL 2
...
```

- Each `PAGE` becomes its own page document, in the order the pages appear.
- `[SCENE: …]` applies to **the panels after it**, until the next scene. The part before
  the dash (`Harbor pier`) becomes a location in the project's assets.
- `[LOCATION: …]` inside a panel gives that one panel a different place (a cutaway)
  and joins the locations too. It beats the scene for that panel only; the next panel
  is back in the scene's place unless it has its own `[LOCATION]`.
- A continuation marker is not part of the place: `Harbor pier — cont.` and
  `Kitchen (CONT'D)` are read as `Harbor pier` and `Kitchen`.
- **A flashback** (`[FLASHBACK START]` … `[FLASHBACK END]`) is drawn soft and faded.
  Write the place and period in its `[SCENE]` (`Harbor pier — fifty years ago`). The
  cover is never part of a flashback.

## Locations

```
[LOCATIONS]
Harbor pier: a long weathered stone pier in a fishing town, wooden bollards,
  lobster crates, a lighthouse on the far headland. Fog at dawn.
Grandpa's house: a cramped seaside house, indoors. Low beams, a cluttered
  workbench, fishing gear on the walls.
```

- The block goes before `PAGE 1` (and the cover), in the same form as the cast block:
  `NAME: description`, with indented lines continuing the description above. It is
  optional; a script with no block still gets a location for every place it names.
- **The name is the place as a `[SCENE]` writes it**, before the dash: `Harbor pier`
  covers `Harbor pier — dawn` and `Harbor pier — fifty years ago` (the time of day is
  ignored, and so is a leading *the* or a trailing `— cont.`). Name a place the same
  way every time: `Rooftop` and `School rooftop` are two places.
- **The description is the design.** It becomes the location's notes in GIMP, and
  **Design location** (or **Design Locations** in Render Engine, for every place that
  has none yet) draws one establishing image of it, with no people. Describe what a
  reader would see: inside or out, era, layout, landmarks, materials, light and weather.
  What changes from panel to panel (a door left open, rain starting) belongs in that
  panel's `[ACTION]`.
- Every panel at a designed place is rendered to match its image, so the look holds from
  panel to panel. This needs the Qwen-Image 2.1 engine; with another engine the place is
  still named in the prompt but there is no image to follow.
- A place named by a `[SCENE]` or `[LOCATION]` but not declared still joins the project,
  with no description: write one in its notes in GIMP. A declared place that no scene
  uses is reported, which catches typos (`Harbour pier`).
- A panel's `[LOCATION: …]` is also a place, and can be declared in the block too. The
  cover's `[SCENE]` or `[LOCATION]` counts like any other.

## Panels

```
PANEL 2
[SHOT: wide shot]
[CHARACTERS: Mio, Kaito]
[EXPRESSIONS: Mio: excited grin; Kaito: half asleep]
[ACTION]
Mio runs along the pier, Kaito trailing far behind her.
```

- **`[SHOT: …]`** sets the framing. Common shots: `extreme close-up`, `close-up`,
  `medium shot`, `full shot`, `wide shot`, `establishing shot`, `two-shot`,
  `reaction shot`, `over-the-shoulder`, `low angle`, `high angle`, `bird's-eye`,
  `worm's-eye`, `dutch angle`, `POV`. Without it, one of these words in the action is
  used.
- **`[CHARACTERS: …]`** is exactly who is drawn, comma-separated; `[CHARACTERS: ]`
  (empty) means nobody, as in an establishing shot. Without it, the panel's speakers
  (not narration) and any cast member named in the action are drawn, except anyone the
  action puts off-panel ("Yuki calls from off-panel"). Giving it is the surest way.
- **`[FRAME: …]`** describes the panel's frame on the page, so **Generate Page
  Layout** can recommend a layout that fits: a shape (`wide`, `tall`, `square`, or a
  ratio such as `3:2`, width first), a size (`small`, `large`, or `splash` for the
  page's dominant panel), or both: `[FRAME: wide, large]`. Both are optional; without
  one, the shot decides the size (an establishing shot wants a big, wide frame, a
  close-up a small one). Frames are matched to panels in reading order, so a large
  first panel favours a layout that opens big. It only guides the layout choice: a
  panel is always rendered into the frame you select.
- **`[EXPRESSIONS: …]`** is `Name: expression; Name: expression`. Without it, each
  character's usual expression from their design is used.
- **`[ACTION]`** is the picture: one moment that a single still image can show. Every
  line under it is action, colons and all.

## Dialogue and sound effects

```
[DIALOGUE]
MIO: Hurry up!
KAITO (thought): Why did I agree to this.
MIO (whisper): Fifty years...?
MIO (shout): LOOK AT IT!
NARRATOR (narration): The summer everything changed started with a ship.
[SFX]
VRRRMMMM
```

- Every line under `[DIALOGUE]` is `SPEAKER: text` or `SPEAKER (kind): text`, where the
  kind is `thought`, `whisper`, `shout` or `narration` (plain speech needs none). Any
  other line there is reported.
- A narrator is never drawn and need not be in the cast.
- Under `[SFX]`, each line is one sound effect.
- In GIMP each line gets a **Bubble…** button in the panel's Context, and the bubble
  picker opens on the matching style: speech → Speech, thought → Thought (cloud),
  whisper → Whisper (dashed), shout → Shout (burst), narration → Narration (box),
  SFX → Sound effect.
- Dialogue is never drawn into the renders; panels are rendered without text.

## Notes

`[NOTES]` holds anything for yourself or the artist. It stays with the panel (shown in
Context) and is never drawn.

## When something is wrong

The script is read strictly, and anything that does not fit is reported with its line
number, for example:

- `Text outside a section: put it under [ACTION], [DIALOGUE], [SFX] or [NOTES]`
- `Dialogue lines are SPEAKER: text or SPEAKER (kind): text`
- `Unknown kind (sings); use one of speech, thought, whisper, shout, narration`
- `Kiato speaks but is not in the [CHARACTERS] block`
- `Unknown header [WEATHER]`
- `Stairs is declared but no [SCENE] or [LOCATION] uses it`
- `Roof is declared twice`
- `A cover has no [DIALOGUE]: its title and credits are lettered in GIMP`
- `The cover needs an [ACTION]: what its picture shows`

New Project from Script… lists these before building the project, so you can fix the
script first or go ahead without the reported lines.

## Changing the script later

Edit the script file and use **Reload Imanganation Script**. A panel whose text is
unchanged keeps its takes, placement and Context edits (it is only renumbered); an
edited panel with work on it goes to **Needs matching** so you can join it to its new
text, and new characters and places are added without touching existing ones. The
cover is matched the same way. Fixing a typo in the middle of a long script therefore
does not throw away finished panels.

## Set later in GIMP

- **Panel frames**: the frame you render into decides a panel's real shape. `[FRAME]`
  hints can also be changed in the panel's Context (Aspect ratio, Frame size).
- **Character designs beyond the first**: **Design character** (a new version) or
  *Set Character Reference from Layer*.
- **Page layouts and panel frames**: you draw these, or choose a layout for a page.

Everything the script sets (shot, characters, expressions, location, action, notes) can
also be edited afterwards in the panel's Context.

## Prose instead of a script

A text with no `PAGE` / `PANEL` lines is treated as prose: the engine's language model
breaks it into panels (the engine must be running). A `[CHARACTERS]` block at the top
still works and is read exactly, not by the model. Scripts are recommended: they are
read instantly, exactly as written, with the engine off.
