# Exploration: imanganation as a GIMP plug-in

Status: **exploration + working spike (PASS)**, 2026-10-04. This doc doesn't change the
plan yet. It suggests how the plan would shift if a GIMP plug-in became the final
interface.

## The workflow

**The AI makes panels and the artist directs the page.** The engine renders one
text-free panel at a time. In GIMP, artists can choose a built-in panel layout whose
frame count matches the script panels for that page, or use their own template. The
plug-in creates selectable frame borders; the artist still chooses the composition,
places panels and letters the page.

```
artist's own template or a generated layout (frame lines on a layer named "Template…")
   │
   ├─ click inside a frame (Fuzzy Select)
   ├─ Imanganation → Render Panel into Frame
   │      plug-in ──POST /jobs {seq, frame w×h}──▶ engine ──▶ ComfyUI
   │      renders the next script panel at the frame's proportions (~15 s)
   │      ◀── panels/003.png ── placed, clipped to the frame, beneath the frame lines
   └─ click the next frame … repeat; the artist chooses when to create a new page
```

## Why GIMP fits this project

PLAN.md already has two gaps that GIMP fills by design:

1. **Panels are text-free on purpose.** Someone still has to letter them: speech
   bubbles, SFX, captions. GIMP's text layers, paths and brushes are where that work
   happens.
2. **The engine renders one panel per image and never composites.** A page still has
   to be assembled: panel frames, gutters, bleed. That's the artist's job, done by hand
   in GIMP. It keeps the engine's "one panel per image" rule intact.

GIMP also gives three things for free that a custom web UI would have to build:
**selections as masks** (inpaint or fix a hand), **layer history and undo**, and
**XCF as the project file**.

## Built so far

`gimp/imanganation/imanganation.py` and its stdlib-only siblings
To run the imanganation-gimp fork's uninstalled build with the plug-in, use
`scripts/gimp-dev.sh`: it sets the library, data, menu and default-layout paths and points
GIMP at the build's own plug-ins (without them it cannot open PNG or JPEG files, so
rendered panels fail to load).

`gimp/imanganation/project_store.py` and `gimp/imanganation/panel_ui.py` are installed together in
`~/.config/GIMP/3.2/plug-ins/imanganation`. The store reads and atomically updates
`project.json` while preserving unknown keys. It validates format/version, stable IDs,
and project-relative paths. A zero-argument persistent workspace extension starts with
GIMP and registers the workspace docks. In the fork's default layout the **Project**
navigator (pages, script panels, assets) sits left of a compact toolbox; on the right,
**Context** shows whatever is selected (project, page, panel or character), with a
**Script** tab beside it, above GIMP's Layers; **Pages**, the page strip, runs along the
bottom: one thumbnail per page (drawn from the open page when it is open, so unsaved
work shows), click to open a page, **+** after the last page to add one, drag a page onto another to
move it there, and right-click a page (here or in Project) for **Delete page…**: after a
confirmation its document goes to the system trash, and panels placed on it go back to
unplaced with their takes kept. Default "Page N" labels follow the new order. With a panel
selected (in a dock or on the canvas), Context's **Generate** renders it through *Render
Panel into Frame*: into its saved frame, or the selection on an open project page. Panel
and Character Bible are folded into Context and open only from *Windows → Imanganation*.
Context is also where the brief is edited: a panel's characters, expressions
(`Name: expression; …`), location, shot, aspect ratio, action and notes, a page's label,
and a character's aliases and notes are fields. Enter or leaving a field saves it to
`project.json`, and Escape reverts. A typed character name matches the cast
(case-insensitive, aliases too), and a new name joins the cast, so a parser miss such as
a missing character is fixed in place, then regenerated. **Open page** opens the page a
panel is placed on.
With a numbered page selected, Context reports the matching script-panel count and
available layouts, then offers **Generate layout** when a built-in frame count matches.
Pages named **Cover**, **Front Cover**, or **Back Cover** instead offer cover-composition
guides. The action opens the selected page and layout picker in one step.
**New Project…** (GIMP's *File* menu or the welcome Context dock) creates a manual,
script-free project. Its presets include **Manga one-shot**, **Serialized manga**,
**Full-color comic / manhua**, **Digital page comic**, and **Custom**. Presets
prefill editable reading order, color, page format, chapter, and starter panels.
The guided form also asks for a title and parent folder.
For the first page, choose manga tankōbon JIS B6 (128 × 182 mm), Shinsho
(106 × 173 mm), A5 (148 × 210 mm), JIS B5 (182 × 257 mm), a digital 1600 × 2400 px
canvas, a US manga digest (5.5 × 8.5 in), or custom pixels and PPI. Print presets use
300 PPI; the chosen setup is saved so later pages match. These are common manga/comic
trim presets, not a universal manhua standard. Presets do not include bleed, so check
the printer's trim and bleed requirements. The plug-in creates a `.imanga` folder with a valid
`project.json`, opens the workspace, and opens that first page when requested. Starter
panels appear in Script and Context, ready for you to fill in; you can also add
characters and pages as you work.

**New project from script…** (Context, before a project is open, or *Filters →
imanganation*) builds a project from a script file: panels, the cast (with the script's
`[CHARACTERS]` descriptions as their notes), locations (a `[LOCATIONS]` block's descriptions as their notes, plus every place the scenes name), and one page
document per script page, at the chosen size; then it opens page 1. A script (the
format in [script-template.md](script-template.md)) is parsed in the plug-in itself
(the engine's own parser, `script_canonical.py`), so this works with the engine off;
lines that break the format are listed first, to fix or skip. Prose goes to the
engine's LLM (`POST /scripts/parse`). With *Design the cast now*, every described character is
queued for a design sheet (`POST /characters`), and Context refreshes as each one
lands. A character's **Design character** button designs it from its notes, or makes a
new design version if it has one. In the Project tree, right-click **Characters** for
**New character…** (name, aliases and a description, designed straight away if you
like), or a character for **Design character** or **Delete character…** (also on the
Character Bible's rows). Deleting asks first, then removes them from the cast and from
every panel that lists them (takes and dialogue stay), and the engine moves their
designs to `characters/.deleted/` in its identity store, from where they can be restored
by hand. A designed character's Context has **Open reference image**, which
opens their active reference in GIMP; paint over it and run *Imanganation → Set
Character Reference from Layer…*, which already knows the project and character, to make
it a new reference version. The engine has ComfyUI unload its models before an LLM
step and unloads the LLM before a render, so both fit on 16 GB.
**Props** work like locations. Right-click **Props** under Assets for **New prop…** (a
name and a description: shape, materials, colours, wear; designed straight away if you
like), or a prop for **Design prop** or **Delete prop…**. Selecting a prop shows its
notes (editable; they are the description), how many panels show it, and the engine's
reference: **Design prop** draws the object alone on a plain ground (`POST /props`), a
new image if it has one, and **Open reference image** opens it in GIMP. Paint over it and
run *Imanganation → Set Prop Reference from Layer…* (project and prop are filled in) to
make that the reference. The **Gallery** shows a prop's images; right-click for **Make
current** or **Delete…**. A panel lists the props it shows under **Props** in its Context
(comma-separated; a new name joins the project's props, and deleting a prop removes it
from its panels). Qwen-Image 2.1 renders get each listed prop's picture as a reference
and keep the object the same in every panel (up to ten references in all, characters and
the location first); other engines put the prop's name, and its description, in the
prompt. Notes are saved with the project and sent to the engine as you edit them.
**Locations** are designed the same way. Right-click **Locations** under Assets for
**New location…** (a name as the script writes the place, and a description: inside
or out, era, layout, landmarks; designed straight away if you like), or a location for
**Design location** or **Delete location…**. Selecting a location shows its notes
(editable; they are the description), how many panels are set there (by their
location, else their scene heading, ignoring the time of day: "School rooftop — dusk"
is School rooftop), and the engine's reference: **Design location** draws its
establishing image from the notes (`POST /locations`, Qwen-Image 2.1, no people), or a
new one if it has one (earlier images are kept), and **Open reference image** opens it
in GIMP. To fix one by hand, paint over that image and run *Imanganation → Set Location
Reference from Layer…*: the project and location are already filled in (the opened
image carries them), and the selected layer, or its part inside a selection, becomes
the new reference at its own proportions (`POST /locations/reference`; the earlier
image is kept). Any project image works too; name the location and, if needed, the
project folder. Deleting asks first; panels keep their location text and the engine moves the
images to `locations/.deleted/`. Location images are used by Qwen-Image 2.1 renders
only (Context says so when the project uses another engine).
**Speech bubbles.** A placed panel's Context lists its dialogue and SFX lines, each with
a **Bubble…** button. It opens the bubble picker on the tab for the line's kind (speech,
thought, shout, whisper, narration, SFX) with the line's text ready to edit, and inserts
the bubble into the panel's frame, in reading order, its tail aimed at the speaker's
`placement:` layer if there is one. The button then reads **Select bubble**. Bubbles
go into a **Speech bubbles** group at the top of the page (created once, reused); each
is a group of an editable vector shape (Paths tool: move the tail) and a text layer
(Text tool). Select a bubble on the canvas and Context shows its text as a field
(Enter rewraps and resizes the bubble) and **Fit bubble to text**. The **Bubbles** dock
(a tab beside Context) is the whole library, about 130 generated templates in
categories. Sound effect includes impact bursts, jagged and rumble outlines, heavy
rounded lettering and reverse block styles. Click a template to add it to the open
page, inside the selection if there is one.
A page's Context also has a free **Bubble…**. Lettering uses Comic Neue / Bangers when
installed, else a bold sans; the size follows the page (1/64 of its height).

**Screentone.** Select an area and choose **Imanganation → Manga Tools → Create
Screentone…** to fill it with black halftone dots on a separate layer. Adjust dot
spacing, coverage, and screen angle; the source pattern is also kept as an editable path.
**Speed lines.** Select an area and choose **Imanganation → Manga Tools → Create Speed
Lines…** to add editable radial linework on a separate layer. Set the focal point within
the selection, line count, clear center area, and line width.
**Impact burst.** Choose **Imanganation → Manga Tools → Create Impact Burst…** for a
jagged radial burst, with controls for spike count, depth, rotation, and center point.
**Design Front Cover…** (*Imanganation → Page & Cover*) adds editable title,
subtitle/volume, and creator-credit text over the existing artwork. Choose one of the
front-cover templates, a display font and text color; optional safe-area guides are added
as a separate layer. Move and restyle each text layer in the **Cover Typography** group
with GIMP's normal tools.

On first launch, the Project dock offers **Open project…**; the
chosen project folder is remembered in GIMP's user settings and its docks are restored on
later launches. **Open / Switch Project…**, **New Project…**, and
**New Project from Script…** are
in the first section of GIMP's **File** menu, including the toolbox File menu
before an image is open. A dock you close
stays closed on later launches; reopen it from
*Windows → Imanganation*. If the workspace stops (GIMP reports that imanganation.py
crashed and the docks vanish), *Windows → Imanganation → Restart Workspace* starts it
again, with the engine, and shows every dock, without restarting GIMP; why it stopped
is in `imanganation/workspace.log` in the GIMP profile. Dock actions run one at a time:
a click that arrives while another is still working runs right after it. Row callbacks
carry stable IDs.
For container projects, generated and imported images are
copied into immutable `takes/` files and linked through take IDs; XCF parasites and
page placement metadata are still being migrated. Image procedures are grouped under
*Imanganation → Create* (render and place), *Imanganation → Edit Panel* (regenerate, inpaint, stages, refine), *Imanganation → Manga Tools*, *Imanganation → Page & Cover*,
*Imanganation → Project*, and *Imanganation → Settings*:

- **Reload Imanganation Script** (*File* menu or *Imanganation → Project*; also
  right-click the project's title in
  Project for **Reload script** / **Load script from file…**): after editing the
  project's script (`script/` in the project folder), read it again. A panel whose
  script text is unchanged keeps everything (takes, placement, Context edits) wherever
  it now sits, only renumbered; every other panel in the script is new. Edited panels
  with takes or a place on a page move to **Needs matching**, where **Match selected**
  joins each to its new panel; edited panels with no work yet are replaced. When work
  would move or edits be lost, it asks first. New characters and locations are added;
  existing ones and their notes are kept. *Load script from file…* does the same with
  another file and copies it into the project as its script. An older project records
  which script text each panel came from when it is opened, as long as its script file
  is unchanged since it was parsed.
- **Close Imanganation Project** (*File* menu or *Imanganation → Project*; also
  right-click the project's title in
  Project): offers to save pages with unsaved changes (Save, Close without saving,
  Cancel), closes the pages the workspace opened, and returns the docks to the welcome
  workspace. A closed project is not reopened at the next start; Open / Switch
  Project still starts beside it.
- **Open / Switch Project…**: choose a folder containing `project.json` to populate the
  workspace docks. Select project rows to
  inspect panels or pages; page tiles show placed-panel counts and basic progress,
  and **Open page** opens the selected page's XCF (or the selected panel's page).
  Script rows follow reading order and select the same panel in Project and Inspector.
  The Inspector shows panel placement, scene details, lettering counts, and take
  lineage. Cast rows select a character context with project notes and engine-owned
  versions/reference availability. Locations (selectable, see above) and Props
  appear under Assets with their project notes. GIMP restores dock placement and visibility through its normal
  session state.
- **Export Project…** (*Imanganation → Project*): export the saved project pages as a
  CBZ archive, a multi-page PDF, or an ordered page-image folder. Choose whether to
  include covers, reverse the project page order, set the reading direction, and export
  PNG or JPEG (with adjustable JPEG quality); CBZ can include `ComicInfo.xml` metadata.
  A **Pages** field limits it to a range such as `1-4, 7`. The export is flattened and leaves the editable XCF pages untouched. Cover composition
  guides are omitted, and generated page
  frame/template layers are omitted by default; select **Include generated panel frame
  layers** to export those lines too.

- **Render Panel into Frame…** (the main flow): needs a selection, which is the target
  frame. It sends the next panel's `seq` and the frame's size to the engine, which
  picks the ~1 MP SDXL size on the 64-px grid closest to the frame's ratio (e.g.
  522×640 → 896×1088). It polls with a progress bar, then places the result clipped
  to the frame. The frame is saved to a channel while rendering, so the artist can
  keep clicking. Seed, size and prompt are stored on the layer for a later
  regenerate.
- **Generate Page Panel Layout…**: on an open project page, chooses among built-in
  layouts with exactly the number of non-orphaned script panels matching the number in
  the page label. If no script page or panels match, it offers the full template gallery
  so the artist can choose an arrangement and panel count. The artist then chooses Fine
  ink, Classic ink, Bold ink, Slanted frames, or Double rule treatment. Layouts include
  regular grids, cinematic beats,
  overlapping insets and borderless overlays. Cyan guides mark borderless regions for
  selection; rendered overlay panels sit above and clear the base frame ink, while their
  own frame ink stays in the foreground. Pages named **Cover**, **Front Cover**, or
  **Back Cover** offer colored safe-area, title, art, credits, blurb, issue-mark, or
  barcode guides instead. Cover guides do not create panels and should be hidden before
  export. All generated layers are editable and existing page artwork is preserved.
- **Cancel, queue and takes.** While any engine job runs, a small window shows the
  queue position or elapsed time and a **Cancel** button (`POST /jobs/{id}/cancel`: a
  queued job is dropped, a running one is interrupted in ComfyUI). Cancelling isn't an
  error. In Context, every take but the active one has **Make active**, which shows
  that take on the open page and hides the other whole-picture takes. Each take row
  keeps its seed and any warning the engine gave at render time. A page's Context (and
  a placed panel's) offers **Generate all N waiting panels**: it renders, one after
  another, each panel with a frame on the page and no render yet, and stops at the first
  cancel or failure.
- **Gallery** (*Windows → Imanganation → Gallery*, a tile dock): the selection's
  pictures. A panel shows its takes as thumbnails (the active one ticked); click one to
  make it the active take. A character shows its reference images and a location its
  reference image; click to open one in GIMP. Right-click a character's reference for
  **Make default** (the one panels use unless they pin another), **Rename…** (the image
  file is renamed with it, and panels that pinned it follow) and **Delete…** (the image is
  set aside in `.deleted-versions`; panels that pinned it fall back to the default; a
  character's only reference can't be deleted). Engine: `POST
  /characters/versions/default|rename|delete`. **Duplicate panel** (right-click a panel
  in Project) copies its brief into a new panel at the end of the same script page, so a
  second beat or variation starts from the same cast, place and shot. It is kept by
  Reload script; takes and placement aren't copied.
- **Context polish.** A panel's **Seed** is a field (blank: a new random seed each
  render; a number pins the composition), beside the seed each take row records. The
  Production block shows what you act on (takes, the page and frame size, by name) and
  not internal ids. Script rows show ● rendered, ◐ has a frame but no render yet, ○ not
  started. Saving the project keeps the previous `project.json` as `project.json.bak`,
  the way back from a bad edit. The engine-not-reachable message points at *Restart
  Workspace*.
- **Design progress.** While a character, location or prop is being designed, its
  Context shows **Design: Queued behind N jobs · 12s** or **Rendering · 34s** (ticking
  while it is selected), with a **Cancel design** button, and the Gallery heads its
  images with the same line. A failed design stays in Context as **Design failed** with
  the engine's reason, until the next try. Every job that renders shows ComfyUI's
  real step as a bar: the Cancel window and GIMP's progress bar for Render, Regenerate,
  Refine, Inpaint and Develop in Stages, the Context and Gallery lines above for designs,
  and Engine Status (**▶ … pass 2 step 14/28**). A refine or face pass queues a prompt of
  its own, so the bar restarts with *pass 2*; while models load, or an LLM step runs, there
  is no step yet and it shows elapsed time. The engine reads the steps from ComfyUI's
  websocket (`render/progress.py`; `GET /jobs/{id}` → `progress`).
- **Regenerate Panel…**: select a placed panel (its layer or group). It is re-rendered
  from the **current** `panels.json`, so script edits apply, at its frame's size. The
  new take (`003_take02.png`…) goes into the same group, cover-fitted to the frame
  with the same mask, and the previous take is kept, hidden. Default is a new random
  seed. *Same seed* reuses the seed that composed the take (traced back through a
  hi-res refine to the original render), keeping the composition while applying
  your edits. The script cursor is not moved.
- **Inpaint Selection…**: draw a selection over a placed panel (any shape, feathering
  respected) and describe what to paint. The selected layer doesn't matter: if it isn't
  a panel, the panel under the selection is used. The selection is exported as a mask
  in the **take's own pixel space** (takes are shown scaled), sent with the exact take
  to `POST /inpaint`, and the result goes in at the take's exact geometry (so it lines
  up even if you moved or scaled it), with the previous take hidden. Outside the
  selection the take is pixel-identical. **Characters** (comma-separated, optional)
  names who is in the selection, so their faces stay on-model; with several, each
  covers their side of the patch in the project's reading order.
- **Develop Panel in Stages…**: start with a rendered panel, select the panel or its
  group, or start from nothing: with no render yet, select the script panel in the docks
  and a frame on its page, and the first stage begins on a blank paper-white picture in
  the frame's proportions (kept as the panel's first take, full strength so the stage
  paints freely). Then run this command for each development pass. Its guided dialog suggests
  the next phase and explains what to focus on and select: **Panel composition →
  Rough character blocking → Background and perspective → Acting, interaction, and
  props → Linework and cleanup → Black shapes and shadow design → Effects and final
  polish**. This follows the manga page from readable thumbnail and acting through
  cleanup and finish. Choose a different phase to skip ahead or revisit an earlier one.
  Make a selection around that phase's work, describe the change, and optionally name
  the characters in the area to use their project references. Each result is added as
  a separate editable patch layer above the prior artwork; the complete updated take
  becomes the source for the next pass. Prompts, phases, masks, seeds, source takes,
  and outputs are recorded on the layers and in project take history, so the XCF can
  be saved and reopened mid-process. Use existing **Create Screentone…** and Bubble
  tools for traditional tones and lettering after the visual stages. Use ordinary
  **Inpaint Selection…** when you want to replace the visible take instead of keeping
  a staged layer.
- **Set Character Reference from Layer…**: select the layer that shows a character
  (optionally drag a selection around them) and type their name or alias. The region
  is grown to a square around its centre on white, with layer masks applied, capped at
  1024 px. It's exported to `<project>/tmp/` and registered through
  `POST /characters/reference` as a **new** version (`gimp-01`, `gimp-02`…) that
  becomes the active reference. `base` and earlier versions are never overwritten.
  The project comes from any placed panel in the image; the folder argument is only
  needed for an image without one. Unknown names are refused, listing the project's
  characters, and never create a new character.
- **Refine Panel (Hi-res)…**: select a placed panel (its layer or group). The plug-in
  sends the exact take that layer shows to the engine's hi-res fix (`POST /refine`
  with `source`: polish + Real-ESRGAN, `docs/phase6a.md`). The result is swapped in
  at the same footprint and mask, and the previous take is kept, hidden. *Scale* and
  *Polish* default to `settings.yaml`.
- **Place Next Panel…**: same, for panels that are already rendered. It reads `<project>/panels.json`, takes the next panel in script
  order, and fits `panels/{seq:03d}*.png` into the current selection. A small
  `gimp_cursor.json` in the project tracks where you are, so it carries across page
  images. Set *Panel number* to place a specific panel again (this doesn't move the
  cursor). The script's page numbers only show up in a note like "the script starts a
  new page here". They never restrict which image a panel goes into.
- **Place Panel…**: places any PNG manually.
- **Render Engine…** (*Imanganation → Settings*): chooses how the open project's panels are
  drawn, saved in `project.json` (`project.render`) and sent with every render and
  regenerate. One choice per engine with what it does, its speed, its licence (a
  non-commercial one is flagged, with a warning when selected) and its install state,
  with **Install** for missing files (progress shown while the dialog stays open). The
  **face pass** checkbox repaints each face with the panel's expression after the render.
  **Style** sets the project's look: a preset (Clean modern anime, 90s cel anime, Gritty
  seinen ink, Soft shōjo, Watercolour; each with what measuring it found) and, under
  *Also*, your own words. It goes with every render, inpaint and character and location
  design, saved as `project.render.style`. Changing it offers to redesign the characters
  and locations that have designs, since panels follow those references (traits are
  kept; earlier designs stay as versions). Panels stay in colour. See `docs/styles.md`.
  **Design Locations** draws an image of each place in the script that has none yet
  (a place's notes are its description) for Qwen-Image to keep consistent. Measured trade-offs: `docs/quality/2026-10-07_combined.md`. Engine:
  `GET /engines`, `POST /engines/{id}/install`, `POST /locations/design`.
- **Set Up Models…** (*Imanganation → Settings*): the AI models aren't included with the app. This lists the files
  the engine needs (what each is for, size, state, a link to its licence), with
  **Download** for the missing ones, checkpoint first, and **Use Files I Have…** to
  link matching files from a ComfyUI or A1111 folder (found by content, no extra disk
  space). Under them, **Optional engines** (Qwen-Image 2.1, Z-Anime, the face pass)
  each have their size, licence and an **Install** button; they aren't needed to
  render, and a project picks one in **Render Engine…**. **Use Files I Have…** looks
  for the optional engines' files too, and stays available while any is missing, so
  files you downloaded before (in a ComfyUI folder, say) are linked, not fetched again.
  The dialog isn't modal: downloads run in the engine, it shows their progress
  while you work, **Pause Download** keeps the partial file, and Download resumes it.
  Its first row is the **renderer** (ComfyUI and PyTorch for your GPU, about 5.5 GB):
  missing on a fresh Flatpak install, it's installed before the models, with each step
  shown under the progress bar, and the Flatpak's engine starts ComfyUI as soon as it's
  in. (In the Flatpak the workspace starts the app's own engine; models and outputs go
  in its data folder, projects in `~/Imanganation`.) It opens by itself at startup when the renderer or the
  checkpoint is missing (rendering can't work without them). Engine: `GET /setup`,
  `POST /setup/download|link|cancel`; the same modules as `manganation setup` and
  `install-comfyui`.
- **Engine Status…**: one click, no dialog. Shows engine and ComfyUI health (GPU, free
  VRAM), running jobs with elapsed time, queued jobs, recent jobs with their errors,
  missing model files for the current settings, and the image's project progress
  (`3/5 panels rendered · next: panel 004`). If the engine is down it says so, with
  the command to start it, and still shows the project progress. Engine:
  `GET /status`. The report is also returned as a string for scripting.

**Which take gets placed:** the newest file matching `panels/{seq:03d}*.png` by
modification time. A fresh retake beats an older hi-res, and a hi-res made from the
current take beats that take. Name order can't do this, because `003_hires` sorts
before `003_take02`. Every placed layer records the exact `file` it shows.

**Template handling.** Name the frame-lines layer `Template…`. New panels go directly
beneath it. A Normal-mode (opaque white page) template is switched to Multiply so
panels show through the white. The template is re-selected after each placement, so
the next Fuzzy Select click samples the frames, not the last render.

**Engine.** `uv run manganation serve` (127.0.0.1:8790) exposes `POST /jobs`,
`GET /jobs/{id}` and `GET /health`. One worker thread means one render at a time on
the GPU. Projects must live under `projects/`. `uv run manganation render <project>
<seq> --width W --height H` does the same thing without GIMP. Code is in
`render/panel.py` and `web/api.py`, tests in `tests/test_render_panel.py`.

`gimp/smoke_test.py` (placement, no GPU) and `gimp/render_smoke_test.py` (live: needs
ComfyUI and the engine running) run headless against GIMP 3.2.6 (flatpak):

| Check | Result |
|---|---|
| Plug-in registers in GIMP 3.2 (Python, GI) | ✅ |
| Plug-in reaches local ComfyUI over HTTP from inside the flatpak sandbox | ✅ `ok 200` |
| Plug-in loads a rendered panel PNG straight from the project dir (host fs shared) | ✅ |
| Panel is fitted to the selected frame, clipped by a mask, and placed in a `Panel p.n` group | ✅ |
| PanelSpec JSON is attached as a persistent parasite and survives XCF save and reload | ✅ |
| Next Panel walks `panels.json` in order into hand-drawn frames; cursor advances | ✅ |
| Panels arrive with no text layers: dialogue and SFX are listed in Context, each with a **Bubble…** button | ✅ |
| Unrendered panel gives a clear "not rendered yet: expected panels/003*.png" and doesn't advance | ✅ |
| **Live** (`gimp/status_smoke_test.py`): with a failed job, a running render and a queued one on the engine → report shows GPU/VRAM, `▶` running with elapsed time, queued, `✗` failure with its reason, models all present, project progress; with the engine stopped → "NOT RUNNING" + how to start it, project progress still shown | ✅ |
| **Live** (`gimp/inpaint_smoke_test.py`): feathered ellipse over a moved/shrunk 1920×2176 take, page layer selected → panel found under the selection, result at the exact geometry, old take hidden, temp mask layer removed; **0 source pixels changed outside the mask** (+40 px) | ✅ |
| **Live** (`gimp/setref_smoke_test.py`): reference from a selection (420 px square crop) and from a whole panel group (800 px square, hidden takes/text excluded) → `gimp-01`, `gimp-02` become active, `base` kept; lower-case name resolves; unknown name refused, nothing created | ✅ |
| **Live** (`gimp/regenerate_smoke_test.py`): regenerate a placed hi-res take → *same seed* recovers the original render's seed, *new seed* gives a fresh take; both cover the frame tightly with its mask, the previous take hidden, the selection and cursor untouched | ✅ |
| **Live** (`gimp/refine_smoke_test.py`): refine a placed panel → 1920×2048 hi-res swapped in at the same footprint and mask, old take hidden, selection kept; next placement picks the newer `_hires` | ✅ |
| **Live:** opaque template, three Fuzzy Select clicks → three real renders sized to each frame, placed beneath the frame lines, template re-selected each time, cursor → 4 | ✅ |

Environment facts that shape the design:

- The flatpak's Python is **3.14** with `gi` only: **no numpy, PIL, pydantic or httpx**.
  The plug-in can't import `manganation`. The venv is 3.12 and its wheels are compiled
  for 3.12.
- The flatpak has `network` and `filesystems=host`, so HTTP to `127.0.0.1` and direct
  file paths both work.
- The flatpak does **not** have `org.freedesktop.Flatpak`, so the plug-in can't
  `flatpak-spawn --host` to start ComfyUI or the engine itself.
- Panels are keyed by **position in `panels.json`** (`seq`), not page/panel. The
  rooftop script legitimately repeats "page 2, panel 1" after `CUT TO:`. The Phase 3
  renderer should write `panels/{seq:03d}.png` (or `{seq:03d}_take2.png` for retakes;
  the newest match wins by name sort).
- GIMP 3.x API quirk: `new_return_values()` pre-allocates the return slots, so you
  overwrite them; `append` doesn't work. Expect more churn like this between 3.0, 3.2
  and 3.4.

## Architecture: thick engine, thin plug-in

```
GIMP (flatpak)                         host
┌───────────────────────┐   HTTP    ┌──────────────────────────┐    ┌─────────┐
│ imanganation.py       │ ───────▶  │ manganation engine       │──▶ │ ComfyUI │
│ stdlib + gi only      │  JSON +   │ (FastAPI, uv venv)       │    └─────────┘
│ - dialogs / menus     │  paths    │ script · characters ·    │    ┌─────────┐
│ - layers, masks, XCF  │ ◀───────  │ prompts · jobs · memory  │──▶ │ Ollama  │
│ - parasites (spec)    │           └──────────────────────────┘    └─────────┘
└───────────────────────┘        shared project dir (host fs)
```

- **Every bit of intelligence stays in the engine** (script parsing, character memory,
  prompt building, ComfyUI workflows). The plug-in only moves pixels and metadata in
  and out of the document.
- **Images pass by file path, not by upload.** The engine writes to
  `projects/<name>/panels/`, and the plug-in exports masks and reference crops to
  `projects/<name>/tmp/`. Both sides can see the host filesystem.
- **Jobs are async.** Use `POST /jobs` → `GET /jobs/{id}` polling. Renders take 11–16 s,
  so the plug-in polls with `Gimp.progress_update` and doesn't block on one long request.
- **The engine starts with GIMP.** In the fork build (not sandboxed), the workspace
  extension starts ComfyUI (`vendor/ComfyUI`, log `comfyui.log`) and the engine
  (`.venv/bin/manganation serve`, or `uv run`; log `engine.log`) if they aren't already
  answering. It stops them again when GIMP quits; services that were already running are
  left alone. The docks show "Engine starting…" until it answers.
  `IMANGANATION_AUTOSTART=0` turns this off, and `IMANGANATION_HOME` points at another
  checkout (the default is the one the plug-in files are symlinked from). Under the
  flatpak the plug-in still can't start anything, so it shows "engine not running".
- The CLI and the GIMP plug-in talk to the **same engine code**, so the engine stays
  testable without GIMP.

## Mapping imanganation onto a GIMP document

| imanganation | GIMP |
|---|---|
| Project | folder: `panels.json`, `panels/`, `gimp_cursor.json`, plus the artist's XCFs |
| Page | an image the artist creates, sized and laid out by hand |
| Panel frame | the artist's selection (rectangle, polygon, anything). Later it could also pass its size to the renderer |
| Panel render | layer inside a `Panel p.n` group, clipped by a frame mask |
| PanelSpec + seed + model/workflow version | persistent **parasite** on the render layer |
| Revisions | sibling layers in the group (hidden older takes) |
| Lettering / SFX | done in GIMP: Context lists each script line with a **Bubble…** button. Nothing is added to the page when a panel is placed |
| Character reference | engine registry. "Use layer as reference" exports a layer into it |

## Candidate procedures (menu *Imanganation*)

Each one is a PDB procedure, so you can also script it from Python-Fu or batch mode.

**Generate Page Panel Layout** creates page frames or cover guides; page creation,
composition, and final cover typography remain artist-directed.

1. **Render Panel into Frame** ✅ (built).
2. **Place Next Panel** ✅ (built).
3. **Regenerate Panel** ✅ (built).
4. **Inpaint Selection** ✅ (built; engine `docs/inpaint.md`, with crop-and-stitch so
   patches are painted at SDXL's ~1 MP scale).
5. **Set Character Reference from Layer** ✅ (built). Engine: `GET /characters`,
   `POST /characters/reference`.
6. **Engine Status…** ✅ (built; engine `GET /status`).
7. **Refine Panel (Hi-res)** ✅ (built on Phase 6a's `/refine`).

## Risks and trade-offs

| Risk | Notes / mitigation |
|---|---|
| **No Python dockable panels in GIMP 3** | Each action is a dialog. For a persistent "storyboard" view, open a non-modal GTK window from a long-lived plug-in with `GLib.timeout_add` polling. This works, but it's clunkier than a real dock |
| **Krita is the stronger host for live AI painting** | Krita AI Diffusion (also on ComfyUI) has dockers, live mode and regions. If interactive painting-with-AI becomes a goal, reconsider. For script → panels → lettering → page, GIMP is adequate and a better fit for lettering and print prep |
| GIMP 3.x API churn | Keep the plug-in small; pin a smoke test (`gimp/smoke_test.py`) per GIMP minor version |
| Flatpak vs distro GIMP differ (Python version, sandbox) | Stdlib-only plug-in avoids dependency problems; engine URL is configurable |
| Users without the engine/GPU | The plug-in degrades to "engine unreachable". Remote engine is possible later (the URL is just HTTP) |
| B&W pages | The engine is colour-only (`docs/color-policy.md`). B&W, screentone and tone work are done by the artist in GIMP, on a colour panel that can always be redone |

## Impact on PLAN.md (adopted 2026-10-04)

- **Interface:** the GIMP plug-in replaces the SvelteKit UI. There's no Node toolchain.
  See "Where the old web-UI jobs went" below.
- **Phase 3 renderer:** frame-sized renders (`render/panel.py`), outputs as
  `panels/{seq:03d}.png` with a sidecar `.json` (seed, size, prompt), served through the
  async job API (`web/api.py`).
- **Phase 4:** regional multi-character references were built on top of it. Still to
  add: an inpaint/img2img workflow for selection-driven fixes.
- **Quality:** the stock SDXL IP-Adapter breaks on NoobAI (tiled, glowing panels).
  Fixed by switching to NoobAI's own IP-Adapter Mark 1 + ViT-bigG, with single-figure
  references. See `docs/quality/2026-10-04_live_quality_check.md`. Open: two-shot
  identity bleed.
- **"No page compositing" holds for the whole system.** The engine never composites
  panels into pages. GIMP can draw a selected built-in frame arrangement; artists
  remain in control of layout choice and page composition.
- **New requirement:** the engine runs as a background user service, managed outside
  GIMP.

### Where the old web-UI jobs went

| Planned SvelteKit feature | Now |
|---|---|
| Script import / parse | CLI: `manganation script parse` |
| Character manager (designs, references) | CLI: `manganation character …`. GIMP: *Set Character Reference from Layer* (planned) |
| Storyboard view, per-panel regenerate | The page in GIMP is the storyboard. *Regenerate Panel* (planned) reads the layer's parasite |
| B&W / colour toggle | Gone: engine is colour-only, B&W is done in GIMP |
| Project save/load | Project folder + the artist's XCF files |
| Progress / status overview | Not covered yet. If needed: *Engine Status…* in GIMP, or a plain HTML page served by FastAPI (no build step) |

## Try it

```bash
./scripts/comfy.sh start
```

```bash
uv run manganation serve
```

```bash
flatpak run org.gimp.GIMP
```

Open your page template and name its frame-lines layer `Template`. Click inside a
frame with Fuzzy Select, then run *Imanganation → Render Panel into Frame…*
and pick the project folder (GIMP remembers it after the first run). Click the next
frame and repeat.

## Placement layers (where each character goes)

Paint a rough blob on a layer named **`placement: Yuki`** (any case, visible or
hidden, faint opacity is fine) wherever Yuki should stand. *Render Panel into Frame*
crops every `placement: …` layer to the selected frame and sends the masks with the
render (`gimp/imanganation/placement.py`, engine `placements`), so characters land
where you painted them instead of in the default reading-order bands. One layer per
character serves the whole page: only its paint inside the current frame is used, and
layers for characters not in the panel, or with no paint in the frame, are ignored.
Live (`gimp/placement_smoke_test.py`, `docs/quality/2026-10-04_placement_layers.png`):
the same seed put Yuki on the right by default and on the left where her blob was.

## Keep composition (Regenerate)

*Regenerate Panel…* has **Keep composition** (and **Composition strength**, 0 = engine
default 0.7). It sends the take the layer shows as the render's `guide`: the new take
keeps that layout and those poses while the edited script changes details (an
expression, an outfit). Live (`docs/quality/2026-10-04_keep_composition.png`): changing
Yuki's expression without it turned arms-raised into pointing; with it, the pose and
background stayed and only the face changed, even on a new seed. Through GIMP
(`gimp/keep_composition_smoke_test.py`, `…_keep_composition_gimp.png`) a new-seed
regenerate kept Akira's crouch, box and fence. Edges keep structure, not colour (his
blazer changed colour), so combine with **Same seed** to keep colours.
