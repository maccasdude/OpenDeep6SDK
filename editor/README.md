# d6edit - the OpenDeep6SDK map editor

Edits the Wizards & Warriors game data in place: whatever it saves is what
the game loads. Every file it writes is first copied to
`<game>/d6edit_backup/<date-time>/`, and files are written to a temporary
name and renamed, so a crash never leaves half a file.

## Install and run

Ubuntu / Debian (everything from apt):

    sudo apt install python3-pyqt6 python3-opengl python3-numpy python3-pil libxcb-cursor0
    python3 editor/d6edit.py /path/to/game [--spoke N]

Works with PyQt6 (apt) or PySide6 (`pip install PySide6 PyOpenGL numpy pillow`
in a venv). PySide6 is used when both are installed; `D6EDIT_QT=pyqt6` forces
PyQt6. Needs OpenGL 3.3.
`d6edit.sh` / `d6edit.bat` in the SDK folder set the packages up in `.venv` when
the system Python lacks them. Started directly, `d6edit.py` checks the packages
first: it switches to `.venv` if there is one, else it names what is missing
(a Python without Pillow used to start and then show an empty map). Without a folder argument it asks for the game folder
(the one with D6MONS.DAT) and remembers it.

## What it shows

One *spoke* (area, 0-12) at a time, chosen in the toolbar:

* terrain (spokes 0, 3, 11) with tile textures, baked light and water
* every BSP level of the spoke at its place in the world
* monsters, items and props with their real 3D models, facing the way the
  game turns them (the view uses the engine's left-handed world, so signs
  read correctly)
* nav points and links (green; cyan = D6LINK joins between graphs), boxes
  (magenta), terrain lights (yellow, View > Lights)
* the scripting tables in the outliner: triggers, switches, specials, traps,
  nav links

## Camera

| | |
|---|---|
| right mouse drag | look around (pans in top view) |
| W A S D, Q E | fly (Shift = fast), mouse wheel = forward/back |
| F / T | frame selection / top view |
| View > See into rooms | hides BSP walls facing away, so dungeons can be seen into from outside |
| View > Brightness | 1x - 3x |

## Objects

| | |
|---|---|
| left click / drag | select / move over the ground (Alt: up/down) |
| Ctrl + wheel | turn the selection (1/32 turn) |
| G | drop onto the floor below |
| Ctrl+1/2/3, Ctrl+D, Del | add monster/item/prop, duplicate, delete |
| Objects panel (left, Ctrl+P) | browse monsters/items/props by name with a model preview; click in the view to place (Shift+click places several) |

The inspector edits every field; monsters/items/props are chosen by name,
facing in degrees, monster flags as check boxes. Unknown fields are kept byte
for byte (grey; hover for what is known).

## Terrain (Tools > Terrain brush, Ctrl+T)

* **Raise / Lower / Smooth / Flatten / Roughen** heights; Ctrl+wheel = brush size.
* **Paint type**: terrain types (grass, rock, sand ...) are painted on tile
  corners; the editor picks or creates the right transition tile for every
  touched tile, as the original editor did. Combinations the game has no tile
  for are refused (status bar says how many).
* **Water**: water type per tile (levels 0, 7936, 5120).
* **Pick**: take the type and height under the cursor.
* The HUD shows the tile under the cursor (texture, height, water, walls, light).
* **Light**: terrain light is baked from the lights in SPOKEnn.LIT. Moving,
  adding (Edit > Add light) or deleting a light relights the ground around
  it; Tools > Relight whole terrain redoes all of it. The bake model was
  fitted to the shipped maps (mean error under one shade step).

## Nav graph (Tools > Nav graph tool, Ctrl+N)

Add points (Shift+click adds a linked chain), link/unlink two points (also
between BSPs and terrain: written to D6LINKnn.DAT), delete (renumbers BSP
points, fixes links and the leaf table; refused while a link or script uses
the point's id).

## Event scripts (Tools > Event scripts, Ctrl+E)

All events of EVENTS.DCL with their code as editable assembly:

    IFSTATE !STATE -> open
    Q:CLOSEDOOR DOOR, SPEED, NAVPNT, SFXREC
    END2
    open:
    Q:OPENDOOR DOOR, SPEED, NAVPNT, SFXREC
    END

Operands are the event's parameter names; triggers supply the values. New,
duplicate and edited events are appended to EVENTS.COD (old code stays), so
nothing else moves. Shows which triggers in the whole game use an event and
warns before changing parameters they rely on. Instruction reference on the
right.

## Game databases (Tools > Game databases, Ctrl+B)

Monsters (D6MONS), items (D6ITEM), props (D6PROP), NPCs, treasure, treasure
lists and monster sounds: every field with what is known about it
(docs/formats/databases.md), enums as drop-downs, the model as a chooser with
a 3D preview, item icons (replace with an image, or render from the item's
model). **Duplicate** / **New blank** append records (record numbers are
ids, so nothing moves); **Revert** restores the loaded version. Limits: 999
monsters/items, 255 props, 127 treasure lists.

## Models (Tools > Models, Ctrl+M)

Every model slot of the game (the name tables in deep6.exe) with a preview
(frame, turn), the records using it, and:

* **Export OBJ** (one frame, MTL + PNG) or **glTF .glb** (every frame as a
  morph target, one animation per entry of the model's animation table,
  named anim_<id>, attachment points as empty nodes).
* **Import into this slot**: .obj, .gltf, .glb. Static meshes, or animated
  ones made of morph-target frames and anim_<id> animations (export, edit in
  Blender, import back), or rigged meshes with skeletal animations, which
  are baked into frames (animation names walk, run, attack ... or anim_<id>,
  see docs/formats/models.md section 6). Attachment points (weapon hands, helm) can be kept
  from the replaced model. Textures are quantised to a shared 255-colour
  palette, the model is written as a version 9 .mdl.
* **Point slot to another file**: renames the slot in deep6.exe (backed up),
  so a new .mdl can be added without replacing a shipped one.

Verified in the game: all 641 monster/item/prop models rebuilt by the writer
render and animate, and an imported glTF model replaced the trees.

## Textures (Tools > Textures, Ctrl+Shift+T)

* **Level textures**: the .twd of every BSP of the spoke; replace a texture
  with an image (keeps or rebuilds its palette), add new ones, export PNG,
  and **paint on faces**: click walls/floors in the view to give them the
  selected texture (undoable).
* **Terrain types**: make a new terrain type (or re-texture an existing one)
  from an image: the tool writes the full tile and every transition tile to
  the listed neighbour types, blended with a natural edge, quantised to a
  tile folder palette and mip-mapped exactly like the original tool. Optional
  palette rebuild of a small tile folder for colours the shipped palettes lack.
  Verified in the game (grass re-textured as snow).

## Game text (Tools > Game text, Ctrl+G)

TEXTPAK messages (shown by TEXTMSG triggers and READ on items) and D6STRING
strings: edit, add, see which triggers/items use a message. Trigger inspector
shows the message text of TEXTMSG triggers with a button to edit it.

## NPC dialogue (Tools > NPC dialogue, Ctrl+Shift+D)

Every NPC and guildmaster script of NPCDATA.PAK / GMDATA.PAK as editable
assembly (docs/formats/npc.md): the lines the NPC says, the reply options,
the keywords and the code (conditions, rewards, quest flags, world states,
walking, trading). Apply assembles and checks it, File > Save writes the PAK.
All 158 shipped scripts reassemble byte for byte. "New from template" starts
a script for an unused NPC id.

## Spells (Tools > Spells)

The spell table in deep6.exe (docs/formats/effects.md section 1): school,
level, slot, recovery time, mana, targeting, flags, AI category and cast
animation of all 105 spells, with range and duplicate-slot checks; Save
patches deep6.exe (backup in d6edit_backup). The Name column edits
D6STRING.DAT (string 9000 + id - 1, where the game takes the names from),
written by File > Save. Renaming was checked in the game; damage, effects and
sounds are hard-coded per spell id and cannot be changed here.

## Level geometry (Tools > Level geometry, Ctrl+Shift+G)

Edit the BSP levels in TrenchBroom (docs/geometry.md): **Set up TrenchBroom
project** (game configuration, every texture as PNG), **Decompile to .map**,
**Open in TrenchBroom**, **Compile and install** (qbsp + vis + lightmaps +
nav; the replaced files go to the backup folder, the spoke is reloaded) and
**Place new level in this spoke** (terrain spokes: a new TOL placement).
Needs `tools/compiler/build.sh` once. Verified in the game: a decompiled and
recompiled CRYPTA, a new test level replacing a dungeon level and a new level
placed in spoke 0.

## Exits (Exits panel)

Every way out of the spoke: loading another spoke (LOADSEGMENT), moving to
another BSP, town gates, teleports; drawn as orange boxes in the view with
"EXIT -> spoke N" labels. **Go to destination** loads the destination spoke
and shows the arrival point. **New exit** creates a walk-in exit (box +
trigger + the event @SDKSEGMENTEXIT): when the whole party stands in the box
the game loads the chosen spoke and BSP slot (docs/formats/exits.md). The
detail view lists what must be true before an exit works (world states,
levers, the party inside a box, items carried), worked out from the event code.

## Foliage, walls and canopy

Trees of SPOKEnn.FOL are drawn with their models (the game picks the tree type
at random each time it loads; the editor shows a stable choice). Tree walls,
castle walls and the canopy along the paths are drawn as the game builds them,
and the terrain brush paints them (**Tree wall**, **Castle wall**, **Clear
wall**; faces are recomputed around the change).

## Automap

Tools > Redraw the automap of this spoke renders the automap pages again
from the saved terrain and levels (formats/d6automap.py): dungeon floors on
parchment, terrain with forest, water and buildings, each page in its own
palette. Use it after geometry or terrain changes.

## Mode (Standard / EX)

**Mode > Standard** targets the original game (deep6.exe on Windows, or the
faithful Linux port OpenDWWandW); **Mode > EX** targets
OpenDWWandWExpanded. The mode is shown in the title bar and remembered.

* In Standard mode the features that need the expanded engine are disabled
  (greyed out): for now *Play from the camera*. The list is in
  `editor/modes.py` (EX_FEATURES), where new EX-only features are added.
* Each mode has its own *Test in game* command (File > Game command edits
  the one of the current mode): Standard `opendwwandw --game-dir {game}`
  (Windows: `{game}\deep6.exe`), EX `opendwwandwexpanded --game-dir {game}`.
* Mod projects record the mode they were captured in (`mod.json` target);
  installing an EX mod prints a note, and `d6mod.py list` shows it.

## Checks and testing

* **Errors**: an error while loading a spoke (or anywhere else in the
  editor) is shown in a dialog ("Show Details" has the full report) and
  appended to `~/.d6edit.log`; a failing load step is skipped and the rest of
  the spoke still loads. Send that file when reporting a problem.
* **Tools > Check spoke** (Ctrl+K): references that point nowhere (switch,
  trap, box, special -> missing object/trigger), unknown events, broken nav
  links and ids, tiles without texture, a saved game that hides edits.
* **File > Test in game** (F5): saves and starts the game
  (`File > Game command`, default `opendwwandw --game-dir {game}`).
* **File > Play from the camera** (Shift+F5): saves, then starts
  OpenDWWandWExpanded with `--load-slot` and `--start-at`: it resumes a save
  slot (default 0; it provides the party) and puts the party on the floor
  below the editor camera (a BSP floor, else the terrain), facing the
  camera's direction, in the spoke shown. Command and slot: `File > Play from
  the camera settings` (default `opendwwandwexpanded --game-dir {game}
  --load-slot {slot} --start-at {start}`). The party is moved through the
  game's own level change, so it can also start inside a dungeon level.

## Rules the editor follows

* **Record numbers are ids**: deleting an object blanks its record, new
  objects reuse blank records; table records are disabled, not removed.
* **Saved games override the map**: D6SEGnn.GAM holds a visited spoke's
  state; saving offers to move it into the backup folder.
* Objects at height 0 on terrain stand on the ground (the game snaps them).

## Not yet

* Sounds and music: replace the files (docs/formats/audio.md); new ids by
  raising the MONSOUND.DAT limit line; new music tracks need engine changes
* New spokes, town routes and entry points need engine changes (planned in
  OpenDWWandWExpanded); new item icons beyond the 246 table entries likewise
