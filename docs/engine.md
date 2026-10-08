# The Deep6 engine: an overview for SDK authors

Wizards & Warriors (Heuristic Park / Activision, 2000) runs on an in-house
engine the developers called **Deep6** (`deep6.exe`, sources under
`D:\d6\work\`). No SDK or editor was ever released. This chapter describes
how the engine is put together: the executable, how the world is organised,
how areas are loaded, how the three script machines work, how saves
override map data, and which tables are compiled into the exe. The file
formats themselves follow in their own chapters; this one says how they fit
together and where to start if you want to write your own tools.

## 0. How this was found, and how sure it is

* **Static analysis** of `deep6.exe` (GOG build 1266995554). The exe carries
  Watcom debug information: 4,906 named functions and 3,137 named globals,
  each with its source module (no types, locals or line numbers). Names in
  this reference (`LoadSpokeSegment_`, `_gSpokeNumber`) are those symbols;
  addresses are virtual addresses in that exe (image base 0x400000).
* **Running the original code**: the OpenDWWandW loader maps the unmodified
  exe on Linux and replaces only the Windows APIs, so data changes can be
  checked in the real game logic. Memory reads (`/proc/pid/mem`) and the
  `--start-at` option of OpenDWWandWExpanded were used to test placements,
  exits and scripts.
* **Round trips**: every supported file is parsed and written back, and the
  result must be byte-identical to the shipped file (907 data files including
  the 728 models, the 66 levels, the 158 dialogue scripts).
* **Confidence marks** used in the chapters: **H** (high) read from the
  loader or consumer code and confirmed in the data; **M** (medium) from
  usage, consistent with the data; **L** (low) guessed or unused.
* `docs/verification.md` lists what was additionally checked by playing.

## 1. The executable

| | |
|---|---|
| format | 32-bit PE, Watcom C/C++ 10.x, image base 0x400000 |
| code | section BEGTEXT at 0x410000, 0x1A5000 bytes |
| data | DGROUP at 0x5C0000: 0x33600 initialised, BSS to about 0x978E00 |
| debug info | Watcom v3.0, 221,843 bytes appended to the exe |
| modules | 392: 191 game C files, 19 game asm files, 2 C++ files (cheatkey.cpp, tracklen.cpp), the rest Watcom run-time (209 game modules have named symbols) |
| linked in | libpng 1.0.5, zlib, IJG libjpeg |

Calling conventions (needed to read the code or call it):

* `name_` (trailing underscore): Watcom register call. Arguments in EAX,
  EDX, EBX, ECX, then the stack; the callee pops; every register except EAX
  is preserved.
* `_name@N`: stdcall (WinMain, window procedure, callbacks).
* Varargs functions (`sprintf_`, `fprintf_`, `LogFile_`, `open_`) are cdecl.
* Some asm routines (`_span`, `_tpoly`, `_mpoly`, `_zbuf`) may use their own
  register sets; check each by hand.

Platform layer (what a port replaces): DirectDraw 4 + Direct3D 6 (two
renderers: a 16-bit software renderer and a Direct3D one), DirectInput,
DirectSound plus the Miles Sound System (MP3 music through `Mp3dec.asi`),
Bink (two movies), Video for Windows (town hub AVIs), DirectPlay/Winsock
(multiplayer), the registry and a CD check. The appendix
*deep6.exe modules* lists the 209 game modules that have named symbols,
with their function counts.

The game logic runs in 640x480; the 3D view is the rectangle
(100, 0)-(540, 374), the side panels and message bar are 2D bitmaps.

## 2. The game folder

Files are opened **case-insensitively** (the data mixes `TEMPLEA.BOL`,
`templea.bsp`, `D6Boun04.dat`), so tools must match names that way. The
appendix at the end of this chapter lists every file kind with its chapter.
Broadly:

* `D6*.DAT` - global databases (items, monsters, props, NPCs, treasure,
  shop stock) and per-spoke scripting tables (`D6TRIGnn`, `D6BOUNnn`, ...).
* `EVENTS.DCL` / `EVENTS.COD` - the event script (names and bytecode).
* `NPCDATA.PAK` / `GMDATA.PAK` - dialogue bytecode.
* `D6STRING.DAT`, `TEXTPAK.000`, `pctalk/TALKPAK.nnn` - text.
* Per indoor level `<name>`: `.bsp .twd .lf .lfs .ls .lss .lgt .rgb .nvs
  .l2n .bol`.
* Per terrain spoke `nn`: `SPOKEnn.TMR .TOL .NAV .FOL` (+ editor sources
  `.LIT .PAR .tom` the game never opens), `tiles/80x/`, `maps/lm0b<nn>.lm`.
* `models/**/*.mdl` (728 models), `efxgfx/` (effects),
  `emitters.dat` (particles), `itemicon/` (246 icons used).
* `sounds/`, `Speech/`, `music/`, `movie/`, `townavi/`.
* Runtime files written by the game: `save/`, `D6SEGnn.GAM`,
  `D6ARCHIV.DAT`, `D6WORLD.DAT`, `ROSTER.DAT`, `JOURNAL.nnn`, `PCSNAP.nnn`,
  `maps/*.fog`, `maps/*.mrk`, `gameopt.dat`, `default.kbd`, `debug.log`
  (and `D6SMIT00.DAT`, shop stock, which is data but rewritten in play).

## 3. The world

### 3.1 Towns and spokes

The world is three **town hubs** (2D screens: a panning AVI with clickable
buildings) and thirteen **spokes** (3D areas, `_gSpokeNumber` 0..12).

| spoke | kind | geometry | reached from |
|---|---|---|---|
| 0 | terrain | `SPOKE00` (Valeia wilderness) + 14 BSPs | Valeia (2 gates), Ishad N'ha |
| 1 | dungeon | CRYPTA, CRYPTB | `tcrypt` in spoke 0 |
| 2 | dungeon | TEMPLEB, TEMPLEA | `ttemple`, `ttmpcave` in spoke 0 |
| 3 | terrain | `spoke03` + BSPs | Ishad N'ha, Brimloch Roon |
| 4 | dungeon | SHURU | `tshurub`, `tshuruc` in spoke 3 |
| 5 | dungeon | MINESA..D | `tmines` in spoke 3 |
| 6 | dungeon | OGREA..C | `togrea`, `togreb` in spoke 3 |
| 7 | dungeon | DRAGONA..C | `tdragona`, `tdragonb` in spoke 3 |
| 8 | dungeon | LICHA, LICHB | `tlich` in spoke 11 |
| 9 | dungeon | SUNKENA, SUNKENB | `tsunkena`, `tsunkenb` in spoke 11 |
| 10 | dungeon | SHRINEA | `tshrinea..c` in spoke 11 |
| 11 | terrain | `spoke11` (desert) + BSPs | Brimloch Roon |
| 12 | dungeon | PYRAMA, PYRAMB | `tpyram` in spoke 11 |

The towns are 0 Valeia, 1 Ishad N'ha, 2 Brimloch Roon. The chain is
Valeia - spoke 0 - Ishad N'ha - spoke 3 - Brimloch Roon - spoke 11; the
dungeons hang off the terrain spokes. The game always starts in a town, so a
spoke is entered either through a town gate or through the script op
LOADSEGMENT. **Nothing in the data lists the spokes**: the spoke switch in
`LoadSpokeSegment_` and the gate table in `GetEntryPosition_` are code. New
spokes, routes and entry points therefore need engine changes.

### 3.2 Units and axes

* **World units**: x and z horizontal, **y up**, left-handed. One terrain
  tile is 1024 units.
* **BSP units** = world units / 16, relative to the BSP's origin
  (`World2BspTrans_`: `bsp = (world - origin) * 0.0625`). One tile = 64 BSP
  units. Map editors (TrenchBroom, Quake style) are z up; the SDK swaps y and
  z when converting.
* **Angles**: 1024 per turn. The sine/cosine tables are filled with
  `sin(i * 6.28 / 1024)` - 6.28, not 2 pi; positions computed by the game
  (party placement) depend on that (checked against a saved position).
* **Terrain arrays** are row major, row = z, column = x.

### 3.3 BSP slots and origins

Every spoke has a list of loaded BSPs, `_bspListBSP[]`; scripts address them
by **slot** (`BSPNUM`). Each BSP has an origin in world units (`bsp+0x118EC`).

* **Terrain spokes**: the `.TOL` 'B' records place the small entrance BSPs
  (`tcrypt`, `tgatea`, ...). The record gives the tile position, the slot
  byte and signed tile offsets; origin = position - offsets x 1024.
* **Dungeon spokes**: slot 0 is a null BSP, the dungeon levels are slots
  1..n, loaded with hard-coded `Terrain_BSPLoad_` calls (terrain, name, bx,
  by, bw, bh, offx, offy, offz, p10, p11, defer); origin = `((bx-offx)*1024, -offy*1024,
  (by-offz)*1024)`. The SDK carries the table (`d6level.SPOKE_TABLE`).
* When the spoke changes, LOADSEGMENT keeps the party's position relative
  to the BSP it stands in and re-bases it onto the destination BSP's origin
  (subtract the old origin, add the new one). A dungeon and its terrain
  entrance are built with matching local geometry; their world origins may
  be equal (`tcrypt` and `CRYPTA`) or not (`tshurub` and `SHURU`).
* "Which BSP am I in" (`InBSPArea_`) uses each BSP's tile rectangle: on
  terrain through a leaf tree built by `TerBSP_Calculate_`, in dungeons by
  the first rectangle containing the tile.

### 3.4 Id spaces

| id | meaning |
|---|---|
| object id `< 100000` | 1-based record of the spoke's `.TOL` |
| object id `n*100000 + k` | record k of BSP slot n's `.BOL` (n = 16 means slot 0) |
| door / entity id | `bsp*100000 + d6entid` of a brush entity (slot 0 is 0 here) |
| nav id | the `.nvs` u16 id (< 20000), mapped per BSP to an index |
| table records | 1-based record numbers of `D6TRIG/BOUN/SWIT/SPEC/TRAP`nn |
| world state | `_WState[spoke*256 + n]`, one byte each, 256 per spoke |
| quest flags | 256 per PC (`qflag`), set by NPC scripts and some events |

Record numbers are ids throughout: deleting a record moves every later id,
so editors blank records instead (d6edit does).

## 4. Loading a spoke

`LoadSpokeSegment_` (d6spoke.c) for a terrain spoke, in order:

1. Sky and tree backdrops (`SKYDROP.M2K`, `TREEDROP`/`TTOPDROP`, palm
   versions for spoke 11, sun and moon maps), `syswat.twd` (water).
2. `SPOKEnn.TMR` - terrain tiles, heights, textures, walls.
3. `SPOKEnn.NAV` - terrain nav points.
4. `SPOKEnn.TOL` 'B' records - the BSPs (each loads its level files).
5. `D6LINKnn.DAT` - joins terrain and BSP nav graphs.
6. `SPOKEnn.FOL` - foliage.
7. `D6SEGnn.GAM` - **the saved state of this spoke**; only if it does not
   exist are the objects read from `SPOKEnn.TOL` ('I', 'M', 'P' records)
   and each BSP's `.BOL`.
8. `maps/lm0b<nn>.lm` (+ `.fog`, `.mrk`) - automap.

A dungeon spoke loads a null terrain, its BSP list, `D6LINKnn.DAT` and the
same step 7. In both cases the scripting tables (`D6TRIGnn`, `D6BOUNnn`,
`D6SWITnn`, `D6SPECnn`, `D6TRAPnn`) are loaded by `InitEvents_` /
`InitSpokeTraps_`.

**Consequence for tools**: once a spoke has been visited, `D6SEGnn.GAM`
holds its objects and the placement files are ignored. An editor must move
those files (and the archive of a save) out of the way, or the player will
not see the edits.

## 5. Saves and runtime state

* `save/` - the save slots (`game00.sav` ...). Their header resembles
  `D6ARCHIV.DAT` (version 0x140) but was not decoded; the PC position is
  stored twice (x, y, z floats; the second copy has y + 832).
* `D6ARCHIV.DAT` - archive of the whole runtime state (header of 173 u32:
  version, flags, offsets and sizes of `ROSTER`, `D6WORLD`, each
  `D6SEGnn.GAM`, the journals and 24 shop files). Decoded for listing and
  extraction (`data.md` section 7).
* `D6WORLD.DAT` - world states; `ROSTER.DAT` - the party;
  `D6SEGnn.GAM` - per-spoke objects (positions, health, opened chests).
* Guildmaster flags are **not** saved: they reset on every visit.

## 6. Scripting: three machines

### 6.1 Triggers and events (events.c)

The world reacts through five per-spoke tables and one global script:

* **Bound areas** (`D6BOUNnn`, boxes) test who is inside (any PC, the whole
  party, a monster, an object) and set a world state or fire a trigger.
* **Switches** (`D6SWITnn`) are clickable objects or brush entities; they
  can be linked in rings and fire triggers.
* **Specials** (`D6SPECnn`) and **traps** (`D6TRAPnn`: locks, traps and
  treasure of containers).
* **Triggers** (`D6TRIGnn`, 0xB8 bytes) name an event of `EVENTS.DCL`,
  carry 30 parameter slots (shipped events use up to 14) and the world
  state they watch.
* **`EVENTS.COD`** holds 115 events as bytecode: `u16 opcode` (bit 15 =
  queued op that runs over time), `i16 n`, `n x i16` parameter indices,
  and a `u32` target for jumps and queued ops. 77 opcodes (0x01-0x56): doors,
  states, switches, items, monsters, messages, sounds, teleports, spoke
  changes (LOADSEGMENT 0x2A, GOTOBSP 0x21, ENTERTOWN 0x4F, ENDGAME 0x54).

Exits between areas are such events; `exits.md` lists all of them and the
rules the engine uses to place the party (re-basing on BSP origins, a
256-unit y shift for spoke 8, the party ring search).

### 6.2 NPC dialogue (npc.c)

`NPCDATA.PAK` holds one bytecode program per NPC id (137 used of 160),
with a string table and keywords. Execution always starts at offset 0; the
script registers handlers (attacked, talk, item given, ...) with ONACT.
SAY, REPLY, WAIT and similar ops yield until the player has read or
answered. State: 32 flags per NPC, a register and an attitude per PC and
NPC, 256 quest flags per PC, and the world states.

### 6.3 Guildmasters (guild.c)

`GMDATA.PAK` holds 32 programs (21 used), one per guildmaster; the town
buildings pick theirs from tables in the exe (`_gHallGM`, `_gSmitGM`, ...).
Only the building's buttons drive them (ACTBUTTON); flags reset per visit;
EXIT (0x26) leaves the building. The response table in the PAK is never
used by the game.

## 7. Rendering

* **BSP levels** are Quake-1 derived: `.bsp` version 28 with 15 lumps
  (lump 0 entities with `d6*` keys, an empty texture lump), mirrored axes,
  68-byte models, 52-byte texinfo, u16 edges, and a lighting lump of one u16
  per surfedge. Textures come from the
  level's `.twd` (128x128 8-bit with 16-bit shaded palettes). Lightmaps are
  separate files: `.lf`/`.ls` (Direct3D, RGB565) and `.lfs`/`.lss`
  (software, 8-bit). Coloured point lights `.lgt`/`.rgb` light the models.
  A face is drawn only if its last vertex is a corner.
* **Terrain** is a tile grid (`.TMR`): heights at tile corners, a texture
  per tile from `tiles/80x/` (transition tiles between terrain types),
  baked light, water per tile, and walls (tree walls, castle walls, canopy)
  built from per-tile flags at draw time.
* **Shaded palettes** (`.p16`, model palettes): 32 shade rows of 256
  16-bit colours, row 0 black, rising with the row (the exact scale differs
  between terrain and models, see their chapters).
* **Models** (`.mdl` versions 9 and 10): parts, morph-target frames stored
  as packed vertex paths, an animation table of 256 entries, attachment
  points (weapons, helm), up to 4 levels of detail. The `models/pc/*.seq`
  files are build-time frame lists for `pcreduce.exe`; the game never opens
  them.
* **Effects**: particle emitters (`emitters.dat`), animated textures
  (`.ant`), glows (`.alf`), sprites, and a spell table in the exe.
* **Automap**: pre-drawn pages per spoke (`maps/lm0b<nn>.lm`) plus the
  fog-of-war and markers the game writes.

## 8. Tables compiled into deep6.exe

An SDK cannot get everything from data files. These live in the exe and
are read (and in a few cases patched) by the SDK:

| table | where | SDK |
|---|---|---|
| spoke -> BSP list and placement | `LoadSpokeSegment_` code | `d6level.SPOKE_TABLE` (read) |
| town gate arrival points | `GetEntryPosition_` code | `d6terrain.ENTRY_POINTS` (read) |
| model file names (items, monsters, props) | `mdldata.c` tables, `_ItemMDLData` 0x5DB3B8, `_PropMDLData` 0x5E27D5 | read; d6edit can rename a slot |
| spell table (105 spells) | deep6.exe data | read and patched (`effects.md`) |
| item icon table (246 entries) | exe | read |
| guildmaster per building | `_gHallGM`, `_gSmitGM`, ... | read |
| record limits (999 items/monsters, 255 props, ...) | loaders | documented (`databases.md` 9.1) |
| sin/cos tables | filled at start-up (6.28 / 1024) | reproduced |

## 9. Writing your own SDK: a suggested route

1. **Start with the containers.** Most files are simple record arrays:
   record 0 is a count header, record i lives at `i * size`. Read, write,
   and compare bytes with the original before decoding fields; a byte-exact
   round trip is the best early test.
2. **Keep unknown bytes.** Several structures have fields nobody has
   explained; a tool that carries them through unchanged stays safe.
3. **Get the coordinates right early.** World y is up and left-handed, BSP
   files are world/16 relative to an origin that comes partly from the exe
   (section 3.3); terrain BSP origins come from the `.TOL`.
4. **Treat record numbers as ids.** Blank records instead of deleting;
   append new ones.
5. **Mind the saves.** `D6SEGnn.GAM` and `D6ARCHIV.DAT` override map data
   (section 4).
6. **Geometry needs a compiler.** A changed BSP needs new tree, collision
   hulls, PVS, lightmaps and nav data. Standard Quake tools (qbsp/vis) work
   with the Deep6 collision hulls and a converter to version 28 (`geometry.md`).
7. **Test in the game.** The original exe running under the OpenDWWandW
   loader, with a way to start at any position (`--start-at` in
   OpenDWWandWExpanded), turns most questions into a quick experiment.

The SDK's Python modules in `formats/` are working references for every
format in this document; each has a `--selftest` that round-trips the
shipped files.

## 10. What is still unknown

Collected from the chapters (details there):

* The `.ldl` files (launcher strings, XOR-encrypted, key not recovered; the
  game does not read them).
* Save slot files (`save/game*.sav`): header not decoded.
* Fonts (`.FNT`, `Font_Load_` font.c) and pointer files (`.ptr`, text, loaded
  by `LoadPointers_` lmouse.c): loaders known, layouts not decoded.
* A few database fields (`databases.md` section 11), terrain header floats
  and some tile bytes (`terrain.md`), two NPC opcodes (`npc.md`), part
  header words in models (`models.md` section 5).
* Effects hard-coded per spell id (damage, visuals and sounds are code, not
  data).

## Appendix: file inventory of the game folder

| files | count | what | chapter |
|---|---|---|---|
| `deep6.exe` | 1 | the game | this chapter |
| `D6MONS.DAT D6ITEM.DAT D6PROP.DAT D6NPC.DAT D6TREAS.DAT D6TRLIST.DAT D6HELM.DAT D6MONSND.DAT` | 8 | global databases | databases, data |
| `D6SMIT00.DAT` | 1 | shop stock (the game rewrites it; the other building files - MAGE, PAWN, DOJO, YARD, TMPL, HALL, TVRN, 00..02 - only exist inside save archives) | data, databases |
| `D6TRIGnn D6BOUNnn D6SWITnn D6SPECnn D6TRAPnn D6LINKnn .DAT` | 6 x 13 | per-spoke scripting tables | data |
| `ENTRssnn.DAT` | 17 | named entry points (text) | data, exits |
| `EVENTS.DCL`, `EVENTS.COD` | 2 | event script | data |
| `NPCDATA.PAK`, `GMDATA.PAK` | 2 | dialogue bytecode | npc |
| `D6STRING.DAT`, `TEXTPAK.000` | 2 | strings, messages | data |
| `pctalk/TALKPAK.000-009` | 10 | PC remarks ("On my way...") per voice; same layout as TEXTPAK (u32 n, n x {i32 id, u32 offset, u32 size}, NUL strings) (H for layout) | this appendix |
| `*.bsp .twd .lf .lfs .ls .lss .lgt .rgb .nvs .l2n` | 66 each | indoor levels | levels |
| `*.BOL` | 53 | objects per level | levels, data |
| `.tc .pt1 .pt2` | 6 | tool output, not read | levels |
| `SPOKEnn.TMR .TOL .NAV .FOL` | 3 each | terrain spokes | terrain, data |
| `SPOKEnn.LIT .PAR`, `spokenn.tom`, `tilebmp.lst` | | editor sources, never opened by the game | terrain |
| `tiles/80x/` | | terrain tile textures and palettes | terrain, walls_textures |
| `skydrop.m2k`, `*DROP.bmp/.p16`, `SUNMAP`, `MOONMAP` | | sky and tree backdrops | terrain |
| `maps/lm0b<nn>.lm`, `.fog`, `.mrk` | 13 + runtime | automap | terrain |
| `models/**/*.mdl` | 728 | models | models |
| `models/pc/*.seq` | 33 | build-time frame lists for pcreduce.exe, not read by the game | models |
| `models/*.lst`, `models/monster/model.lst` | 6 | model lists (only chardemo reads model.lst) | data, models |
| `models/monster/*.bsp` | 6 | catapult, minecart, warship, raft, horse, hydra: not examined | - |
| `emitters.dat`, `efxgfx/*.ant .alf .p16 .bmp`, `gfx/` | | particles, animated textures, glows | effects |
| `itemicon/`, `I99Icon/`, `dragicon/`, `roleicon/`, `portrait/` | | interface bitmaps (item icons: 246 in the exe table) | databases |
| `D6FNT*.FNT` + `D6FNT*.P16` | 51 + 33 | fonts and their palettes (`Font_Load_`, `Font_LoadPal16_`) | not decoded |
| `palmdrop palmsand ptopdrop treedirt treedrop ttopdrop .p16` | 6 | backdrop palettes | terrain |
| `*.ptr` + bitmaps | 32 | mouse pointers: text with the bitmap name, sizes, hot spot and per-frame "index time" lines (M) | not decoded |
| `DEEP6.PAL` | 1 | 8960 bytes = 768-byte RGB palette + 8192 bytes (32 x 256) (M) | not decoded |
| `MONSOUND.DAT`, `sounds/` | | sound effect table and wavs | audio |
| `Speech/*/*.wav` | 5954 | spoken lines: NPCs, narrator (479 in 000-1611WAV), Gareth (27 in k21) | audio |
| `music/*.mp3` | 8 | music (Miles MP3) | audio |
| `speech.tag`, `music/MUSIC.TAG`, `Speech/SPEECH.DIR` | 3 | small text tag files (CD-era data location markers) (L) | - |
| `JOURNAL.nnn` | | journal pages (runtime) | audio |
| `townavi/townhubN.avi`, `townmskN.avi` | | town hub pans (MS Video 1) and click masks (uncompressed DIB) | terrain |
| `Town000.hub` | 1 | not opened by deep6.exe (no file name string) | - |
| `movie/*.bik` | 2 | Bink movies | - |
| `*.ldl` | 4 | launcher strings, encrypted, not read by the game | data |
| `D6ARCHIV.DAT`, `D6WORLD.DAT`, `ROSTER.DAT`, `D6SEGnn.GAM`, `PCSNAP.nnn`, `save/` | | runtime state (section 5) | data |
| `gameopt.dat`, `*.kbd` | | options, key bindings (runtime) | - |
