# Tutorial: a first mod

This walks through a small mod from start to a zip someone else can
install: a renamed NPC with a new line of dialogue, a group of monsters on
the road out of Valeia, a stronger spell, and a test run in the game. It
takes about half an hour. Every step names the menu or command it uses; the
reference for each tool is `editor/README.md` and the files in `docs/`.

You need the game (the GOG release), this SDK (`README.md`, Quick start) and,
for testing on Linux, OpenDWWandW or OpenDWWandWExpanded. Expanded is needed
for *Play from the camera*.

## 1. A working copy and a project

Never edit your only install. Keep it pristine and work on a copy:

    python3 formats/d6mod.py workcopy "/games/Wizards and Warriors" ~/w6/work --link
    python3 formats/d6mod.py new ~/w6/mymod "/games/Wizards and Warriors" --name mymod

`--link` makes the copy almost free on disk (the files the game writes while
running are real copies). The project folder records the pristine files'
hashes; it will hold only what you change.

The same is in the editor: **Mod > Mod project**, *Make working copy*, then
*New project*.

## 2. Open the working copy

    ./d6edit.sh ~/w6/work            (Windows: d6edit.bat C:\w6\work)

The editor opens spoke 0, the Valeia wilderness. Right drag looks around,
W A S D move, the mouse wheel moves forward (Help > Controls lists all keys).

A save game remembers the state of every spoke the party visited
(`D6SEGnn.GAM` inside the save). To see your map changes, test with a save
made before the party entered that spoke, or with a new game. The status bar
warns when a saved state hides edits.

## 3. Give the Traveler a new line

The Traveler is NPC 1, the hooded figure on the road east of the start.

1. **Tools > Game databases**, tab *Monsters*: record 4 *Traveler* has the
   field `npc` = 1. Rename it to *Wanderer*: this is the name in front of
   his lines in the message bar. In the tab *NPCs*, record 1 is the same NPC
   (NPC n = record n); rename it too, it is the name in the journal.
2. **Tools > NPC dialogue**, NPC 1. The script is assembly
   (`docs/formats/npc.md`). At the end of `.strings` add

       s64 "Well met, $N. The road is long."

   and right after the label `L0040:` (the start of the main path) add

           SAY s64

   Labels are recomputed, so jumps stay correct. Press **Check**, then
   **Apply**.
3. **File > Save** writes D6MONS.DAT and NPCDATA.PAK (with backups).

`$N` becomes the name of the PC who is talking. The game also writes the line
into the journal (Characters > Wanderer), with `$N` as written.

## 4. Monsters on the road

1. Fly to the road east of the start (around x 235000, z 73000; the
   Inspector shows positions).
2. **Edit > Grid snap > 256** keeps things tidy.
3. **Edit > Add monster** (Ctrl+1) puts a monster in front of the camera.
   In the Inspector pick the monster type (for example *Highland Rogue*).
4. Select it, Ctrl+D twice: three rogues. Shift+click to select all three,
   or drag a rubber band around them on empty ground, then drag one of them
   (or a gizmo arrow) to move the group. Ctrl+wheel turns them.
5. **Edit > Save selection as prefab** stores the group; **Insert prefab**
   puts it anywhere, in any spoke. Ctrl+C / Ctrl+V do the same through the
   clipboard.
6. **Tools > Check spoke** (Ctrl+K) lists problems, **File > Save**.

Objects with height 0 stand on the ground: the game drops them onto the
terrain, the editor shows them there.

## 5. A stronger spell

**Tools > Spells**: change the mana cost of *Burn* (school Sun, level 1)
from 10 to 5, **Save to deep6.exe**. A spell's name comes from game text
(D6STRING.DAT, string 9000 + id - 1), so renaming it in the same dialog takes
a **File > Save** as well. Damage and effects are fixed in the exe.

## 6. Test

* **File > Test in game** (F5) starts the game on the working copy.
* **File > Play from the camera** (Shift+F5, OpenDWWandWExpanded) resumes a
  save slot (File > Play from the camera settings: slot 0 by default) and puts
  the party on the ground below the editor camera, facing where the camera
  looks. Put the camera just west of your rogues and press Shift+F5.

When the party walks east the Wanderer comes over and says the new line.

## 7. Capture, pack, share

**Mod > Mod project**: the list shows what the working copy changes:

    changed  D6MONS.DAT
    changed  NPCDATA.PAK
    changed  SPOKE00.TOL
    changed  deep6.exe

Fill in version, author and description, then **Capture into project** (the
small deep6.exe change is stored as a byte patch, not the whole exe) and
**Pack zip**. Command line:

    python3 formats/d6mod.py capture ~/w6/mymod ~/w6/work --pristine "/games/Wizards and Warriors"
    python3 formats/d6mod.py pack ~/w6/mymod mymod.zip

Players install and remove it with

    python3 formats/d6mod.py install mymod.zip "/games/Wizards and Warriors"
    python3 formats/d6mod.py uninstall mymod "/games/Wizards and Warriors"

or **Mod > Mod project > Install into a game / Uninstall from a game**.
Install checks that the files are the ones the mod was made from, keeps
backups, and refuses mods that change the same bytes (`docs/mods.md`).

## 8. Keep it in git

**File > Export tables as text** writes every table and object list as JSON
lines (one record per line, fields by name), so `git diff` shows "Traveler ->
Wanderer" instead of binary noise. **Import tables from text** writes them
back, byte for byte. Command line: `formats/d6textio.py export / import`.

## Where to go next

* New dungeon rooms: **Tools > Level geometry** (TrenchBroom, `docs/geometry.md`).
* Models: **Tools > Models** (OBJ / glTF, rigged glTF animations are baked).
* Terrain: the Terrain panel (sculpt, paint, walls, water, light).
* Events and switches: **Tools > Event scripts**.
* What was checked in the game and what was not: `docs/verification.md`.
