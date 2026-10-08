# Outdoor terrain ("spoke") file formats

Reverse engineered from `deep6.exe` (Watcom debug symbols, Ghidra decompile in
`re/decomp/`). Parser/writer: `formats/d6terrain.py` (byte-identical
round trip of all 951 files covered; run
`python3 formats/d6terrain.py --selftest <gamedir>`).

All multi-byte values are little endian. Confidence is given per field:
**H** = read directly from the loader code, **M** = loader plus data
statistics, **L** = guessed from the data only.

## 1. World model and coordinates

* The world is split into 13 *spokes* (`_gSpokeNumber` 0..12). Only spokes
  **0, 3 and 11** are heightmap terrain (case 0/3/0xb in `LoadSpokeSegment_`,
  d6spoke.c 0x483038). The others are pure-BSP dungeons that call
  `Terrain_Null_()` and load hardcoded BSPs:

  | spoke | type | content (BSP files) | terrain-side entrance BSP (in .TOL) |
  |---|---|---|---|
  | 0 | terrain | SPOKE00.* (forest, river) | - |
  | 1 | bsp | CRYPTA CRYPTB | `tcrypt` in spoke 0 |
  | 2 | bsp | TEMPLEB TEMPLEA | `ttemple`, `ttmpcave` in spoke 0 |
  | 3 | terrain | spoke03.* | - |
  | 4 | bsp | SHURU | `tshurua..j` in spoke 3 |
  | 5 | bsp | MINESA..D | `tmines` in spoke 3 |
  | 6 | bsp | OGREA..C | `togrea/b` in spoke 3 |
  | 7 | bsp | DRAGONA..C | `tdragona/b` in spoke 3 |
  | 8 | bsp | LICHA LICHB | `tlich` in spoke 11 |
  | 9 | bsp | SUNKENA SUNKENB | `tsunkena..c` in spoke 11 |
  | 10 | bsp | SHRINEA | `tshrinea..c` in spoke 11 |
  | 11 | terrain | spoke11.* (desert, palms) | - |
  | 12 | bsp | PYRAMA PYRAMB | `tpyram` in spoke 11 |

  (The pairing follows from the exits: the transition is the `EVENTS.COD`
  op 0x2A LOADSEGMENT, which re-bases the party onto a BSP slot of the new
  spoke; every shipped exit is listed in `exits.md`.)
* Spoke 0 also embeds BSPs `torcave`, `ttoada/b`, `truinsa..f` (ruins,
  caves) and spoke 3 `thutb`, `tshackb`; these have no dungeon spoke of
  their own.
* Units: 1 terrain tile = 1024 world units in x and z; y is up. Height map
  values and object y are in the same units. Angles are 1024 per turn
  (`_gSin/_gCos` tables, entry dirs 0x100/0x300).
* Tile arrays are row major: row = z, column = x
  (`Terrain_XYToTile_ = tiles + (x + z*width)*0x1c`). In this document
  "up" in an image means -z.

### Towns, gates and entry points (H)

`SetSpokeEntry_` (deep6.c 0x4134b8) picks the spoke when leaving a town,
`GetEntryPosition_` (d6spoke.c 0x489d20) holds hardcoded spawn points (full
analysis, party placement and dungeon exits: `exits.md`):

| from | to spoke/entry | spawn (tile x, z) | gate BSP nearby |
|---|---|---|---|
| town 0 (gate field 0) | 0 / 0 | (212.5, 81.5) | `tgatea` |
| town 0 (gate field 1) | 0 / 1 | (181.5, 72.5) | `tgateb` |
| town 1, entry 0 | 0 / 2 | (10.5, 275) | `tgatec` (spoke 0) |
| town 1, entry != 0 | 3 / 1 | (277.5, 10.5) | `TGATED` (spoke 3) |
| town 2, entry 0 | 3 / 0 | (7.8, 277.5) | `TGATEC` (spoke 3) |
| town 2, entry != 0 | 11 / (1) | (374, 25) | `TGATEE` |

(Taken literally from the code; note the gate BSP names do not line up with
the towns - `tgatec`/`TGATEC` are probably the same gate model reused. The
town side of each transition is in townhub.c, `_gSpokeEntryNum =
_TownAVI_fieldVal`.)

So the chain is: Town0 = spoke 0 = Town1 = spoke 3 = Town2 = spoke 11, with
dungeons hanging off the terrain spokes. Spokes do not connect directly to
each other; each terrain edge transition goes through a town. Dungeon spokes
use one entry each (table in `d6terrain.ENTRY_POINTS`).

### Files a terrain spoke loads (order of `LoadSpokeSegment_`)

1. `SKYDROP.M2K`, `TREEDROP/TTOPDROP` (.bmp/.p16; spoke 11 uses
   `PALMDROP/PTOPDROP`), `SUNMAP.BMP`, `SUNALPHA.BMP`, `MOONMAP.BMP`
   (`LoadSkyAndTree_`), `syswat.twd` (water textures).
2. `SPOKEnn.TMR` -> `Terrain_Load_` (tiles, textures, palettes).
3. `SPOKEnn.NAV` -> `LoadTerrainNavPnts_`.
4. `SPOKEnn.TOL` -> `LoadTerrainBsps_` ('B' records only), then
   `Terrain_LockLevel_`.
5. `D6LINKnn.DAT` -> `LinkNavPoints_`.
6. `SPOKEnn.FOL` -> `LoadStaticObjects_` (unless `_gNoLoadFOL`).
7. `D6SEGnn.GAM` -> `SegRead_Segment_`; **only if that fails**
   `SPOKEnn.TOL` -> `LoadTerrainObjects_` ('I','M','P' records) and
   `LoadAllBspObjects_` (`<bsp>.BOL`).
8. `maps/lm0b<nn>.lm` (+ `.fog`, `.mrk`) -> `jhpMap_Create_(0, spoke)`.

`SPOKEnn.LIT`, `SPOKEnn.PAR`, `spokenn.tom` and `tilebmp.lst` are **never
opened by deep6.exe** (no format string exists); they are level-editor
sources. `emitters.dat` is global (particle system).

## 2. SPOKEnn.TMR - terrain map (loader `Terrain_Load_` 0x414144, `Terrain_LoadPage_` 0x4148e0)

```
0x0000  int32[1024]  header (_gTomHead)
0x1000  char[1024][4] BLT tile name table (_gTomBLT)
0x2000  width*height tile records, 16 bytes each
```
Sizes: spoke 0 and 3 are 288x288 tiles, spoke 11 is 384x384.

Header (only [0],[1] are read by the game):

| off | type | meaning | conf |
|---|---|---|---|
| 0 | int32 | width in tiles (must be multiple of 32, "acre") | H |
| 4 | int32 | height in tiles | H |
| 20 | f32 x2 | editor values (e.g. 158976.0, 56422.0); unused | L |
| 32 | int32 | 2 in all files (version?) ; unused | L |
| rest | | zero | |

BLT table: entry i is the texture for tile `tex == i`; entry 0 is empty.
Each name is 4 terrain-type characters, one per tile corner
(`1 2 3 4 5 6 B C G H K L M R S T Y E N O V P ...`). Corner order, verified
against neighbouring tiles (99.9% agreement), in array orientation:
char0 = (x+1, z), char1 = (x, z), char2 = (x, z+1), char3 = (x+1, z+1). **M**

`Terrain_LoadTextures_` (0x413c74), **H**:
* name[0] == 'X'/'x', or bit 7 set in name[3]: entry skipped (665 of 776
  entries in spoke 0 have bit 7 set; none of them is used by a tile).
* Otherwise the canonical name is the lexicographically smallest of the four
  byte rotations (`Terrain_NameToRotation_`); the canonical texture is loaded
  from `tiles/<dir>/<canon>.mip` (dir 801..806 found by `TileDir_`) and the
  entry stores rotation k. `np.rot90(canon_texture, k)` equals the
  pre-rotated file that also ships in `tiles/`, so an editor can use either.
* Palette = `tiles/<dir>/pal.p16` (slot dir-801).
* Texture rows run toward -z: draw `flipud(texture)` when z grows downward.

Tile record (16 bytes, unpacked into a 0x1c-byte runtime struct):

| off | type | runtime | meaning | conf |
|---|---|---|---|---|
| 0 | u16 | +0x00 | `tex`: BLT index (0 = no tile / void) | H |
| 2 | s16 | +0x02 | vertex height at corner `(x*1024, z*1024)`; planes are built from this and the +x, +z, +x+z neighbours (`Terrain_CalcPagePlanes_`) | H |
| 4 | u8[6] | +0x06 (each byte x3 as RGB; <<3 in hardware mode) | baked light, shade row 0..31 of the p16 palette, 2 triangles x 3 vertices | H (layout) / M (vertex order) |
| 10 | s16 | +0x04 | always 4096 in shipped maps; use unknown | L |
| 12 | u8 | +0x1a | flags, see below | H/M |
| 13 | u8 | +0x18 | water type 0..6 (`Terrain_IsWaterTile_`, `_gWaterTables`: 1,2 -> surface y 0; 3,4 -> 7936; 5,6 -> 5120) | H |
| 14 | u8 | +0x19 (&0x7f; bit 7 = runtime "seen" by ray caster) | wall type: 0 none, 1 tree wall (forwal / plmwal on spoke 11), 2 castle wall (caswal) | M |
| 15 | u8 | - | unused, 0 | H |

Flags (byte 12), **M** from statistics, split bits **H**:
* `0x0f` = solid wall tile (forest interior). Single bits 1/2/4/8 are
  diagonal wall tiles whose open corner is (-x,-z) / (+x,-z) / (+x,+z) /
  (-x,+z) respectively.
* bits 2 and 8 also select the alternate triangle split diagonal in
  `Terrain_CalcPagePlanes_` / `Terrain_AdjustBase_`; ray casting
  (`XTileSight_`) treats flag bits as sight blockers.
* `0x10` +x face, `0x20` +z face, `0x40` -x face, `0x80` -z face: the
  side(s) where a wall tile borders open ground (100% match with neighbours);
  these are where the tree-wall bitmaps (`tiles/forest/*wal0n.bmp`) are drawn.

## 3. spokenn.tom - editor twin of .TMR (not loaded)

Same header and BLT table (identical bytes), same tile count, different
16-byte tile layout and **no light bytes**:

| off | type | meaning | conf |
|---|---|---|---|
| 0 | u8[4] | per-corner terrain code (editor) | L |
| 4 | u16 | tex (equals .TMR except void tiles: .tom 745 vs .TMR 0) | M |
| 6 | u16 | 0 / 0x0ccc / 0x1000 (unknown) | L |
| 8 | s16 | height (equals .TMR) | M |
| 10 | u8 | flags (equals .TMR byte 12) | M |
| 11 | u8 | water (equals .TMR byte 13) | M |
| 12 | u8 | wall type (equals .TMR byte 14) | M |
| 13 | u8[3] | 0 | |

## 4. 64-byte record containers: .TOL .NAV .FOL .LIT (and dungeon .BOL)

Record 0 is a header: int32 at 0 = number of records **including** the
header (H); int32 at 0x1c = 1 in all files, other bytes 0 except NAV byte
0x3e (0x77); preserved raw. Every other record:

| off | type | meaning | conf |
|---|---|---|---|
| 0 | char | kind | H |
| 1 | char[3] | id as 3 ASCII digits ("001"); parsed as `(c1-'0')*100+(c2-'0')*10+(c3-'0')` | H |
| 4 | f32[3] | position x, y, z (world units) | H |
| 16 | f32[3] | rotation (pitch, yaw, roll) in 1024ths of a turn, copied to GraphObj +0x14 | H (copy) / M (units) |
| 28.. | | kind specific | |

The **record index is the object's world tag** (`_gOBJTag[index]`): events,
D6SEG saves and D6LINK refer to objects and nav points by index, so an
editor must not reorder or delete records (blank them to kind '0' instead,
as the shipped files do).

### .TOL (terrain objects) - `LoadTerrainBsps_` 0x4d6804, `LoadTerrainObjects_` 0x4d6b3c

* `'B'` terrain BSP placement (id field holds "SPT"), **H**:
  pos.x/1024 = tile x, pos.z/1024 = tile z, f32 @16 = width in tiles,
  f32 @24 = depth in tiles, u8 @32 = BSP slot (0..15, must be unique and
  dense), s8 @33/@34/@35 = offset x/y/z (passed to `Terrain_BSPLoad_`),
  char[16] @48 = BSP base name (`<name>.bsp`, objects in `<name>.bol`).
  The terrain under a BSP footprint is flattened in the TMR.
* `'I'` item, id = D6ITEM record (`LoadItemObject_`). **H**
* `'M'` monster, id = D6MONS record (`MonsterEntry_`). **H**
* `'P'` prop, id = D6PROP record (`LoadPropObject_`); u8 @32 != 0 starts the
  prop's emitter inactive (prop 0x1d gets `PropEFX_ActivateEmitter_`). **H**
* `'0'` unused slot.

### .NAV (nav graph) - `LoadTerrainNavPnts_` 0x4d664c

`'N'` records (max 2000): pos (y = 0, snapped to ground by
`AdjustTerrainNavPnts_`), u32 @32 copied to runtime +0xc (0 in data),
u16[6] @36 = links to other nav records by **record index** (0 = none),
u16 @48 flags (bits 1,2 cleared on load; 2/4/16 seen), u16 @50 copied, bytes
52..63 editor scratch (two floats + u32), ignored by the game. **H**

### .FOL (foliage) - `LoadStaticObjects_` 0x4d6dcc

`'F'` records only (others ignored, spoke 11 has three '\0' blanks): pos +
rot of a tree prop placed at load time. **The id is ignored**: the prop type is
`Random()` from {1,2,3,4,0xe7} on spokes 0/3 and {0xe4,0xe5,0xe6} (palms) on
spoke 11. Trees line the forest-wall edges of the paths. **H**

### .LIT (lights, editor only)

`'L'` records: pos (y 8192 or 9015), rest 0. Not loaded by the exe; the
editor baked them into the TMR light bytes. **H** (not loaded) / **L** (meaning)

## 5. SPOKEnn.PAR (editor only)

72 bytes: char[4] "0.1v", u32 64 (payload size), int32[16]. Values look like
generator parameters (e.g. 16383/-16384 and 196/-196 ranges, 0x3f000000 =
0.5f, 20). Not loaded by the exe. **L**

## 6. Tile textures and palettes

* `tiles/80x/NAME.mip`, `tiles/xxxx.mip` (`Load_TerrainMipMap_` 0x413bcc):
  raw 8-bit indices, 128x128 then 64x64, 32x32, 16x16 (21760 bytes). **H**
* `*.p16` (`Pal16_Load_` 0x44df2c): 0x4000 bytes = u16 RGB565
  `[shade 0..31][index 0..255]`; shade 31 is the brightest but still only
  ~25% of full scale (the engine applies gamma, `Pal16_SetRGBGamma_`). **H/M**
* Terrain palette slots (`Terrain_LoadPalettes_`): 0..5 = `tiles/801..806/pal.p16`,
  6 = `tiles/forest/forwal.p16` (`plmwal.p16` on spoke 11), 7 = `caswal.p16`.
  Wall bitmaps: `tiles/forest/{forwal,plmwal}0[0-3].bmp`, `*can00.bmp`
  (canopy), `caswal00.bmp`, `cascan00.bmp`.
* `tiles/pal.p16`, `tiles/water.p16`, `tiles/termip.exe`, `MIPTILE.BAT`,
  `tiles/forest/bmp2p16.exe` are tool leftovers.
* `tilebmp.lst`: CRLF list of 136 `tiles/80x/NAME.mip` paths; not read by the
  exe. **H**
* `texture.dat`: 8 bytes, u32 2 + u32 check value; read by the CD check
  (cdprot.c), unrelated to terrain. **H**

## 7. Sky

* `skydrop.m2k` (`Bmp2K_LoadFromM2K_` 0x53e1f0): int32 width (1024), height
  (90), ncolors (1532); u16[2048] RGB565 palette (copied to
  `gSkyOrigPalette` for time-of-day tinting); u16 pixels[h][w] (palette
  indices). Loaded on spokes 0/3/11 only. **H**
* `skymtn00.bmp`, `treedrop/ttopdrop/palmdrop/ptopdrop.bmp` + matching
  `.p16`, `sunmap.bmp`, `sunalpha.bmp`, `moonmap.bmp`: standard BMPs
  (`BmpFile` keeps them raw).

## 8. Automap: maps/lm0b<spoke>.lm, .fog, .mrk (jhpmap.c)

`.lm` (`jhpMap_Create_` 0x5a5d70, `jhpMap_SetBlocks_`), **H**:
```
0   int32 magic 0x4c4d ("ML"), int32 version 2, int32 0, int32 0
16  int32 nlevels
20  nlevels * 0x23c level headers
    then per level: image pages, then mask pages (contiguous)
```
Level header: +0 image offset, +4 image size, +8 mask offset, +0xc mask
size, +0x10 f32[3] bbox min (x,y,z), +0x1c f32[3] bbox max, +0x28 int bsp
(-1 terrain, n = BSP slot, -5/-7 special origins), +0x2c pages_w,
+0x30 pages_h, +0x34 page bytes (0x4000 = 128x128 8-bit), +0x38 mask bytes
(0x800 = 128x128 1-bit), +0x3c u16[256] RGB565 palette. Pages are row
major; pixel x = (x - min.x) * pages_w*128 / (max.x - min.x), pixel row =
(max.z - z) * pages_h*128 / (max.z - min.z) (rows run from +z to -z).
Levels select by viewer position/bsp (several height bands per dungeon).
These are hand-painted map images, not derived from the TMR at runtime.
`formats/d6automap.py` (editor: Tools > Redraw the automap of this spoke)
renders new pages in the same style, keeping each level's palette, bbox and
mask: dungeon levels from the BSP floors in the level's height band, terrain
levels from the tiles, walls, water and placed BSPs.
BSP floors more than 512 units below the terrain (caves under hills) are
left out. Checked in the game (spoke 0): the redrawn page lines up with the
original (same revealed shapes and PC marker); the hand-painted pages show
some BSP areas as rock where the redraw shows their top floors.

`.fog` (written by the game): one chunk per page of every level in order:
int32 nbytes, u16 runs[nbytes/2]: bit 15 = bit value, bits 0..14 = run
length, 0 terminates; bits MSB-first make the 0x800-byte explored mask. **H**

`.mrk` (written by the game): per level int32 n, n * (int32 x, int32 y,
int32 colour, char text[256]). **H**

## 9. D6LINKnn.DAT - nav graph glue (`LinkNavPoints_` 0x511fac)

int32 n, 4 unused bytes (absent if n = 0), n * int16[4] (a, b, c, d):
`d == -1`: terrain nav b <-> nav a of BSP slot c; `c == -1`: terrain nav a
<-> nav b of BSP slot d; else BSP-to-BSP. **H**

## 10. Runtime state (written by the game, not level data)

* `D6WORLD.DAT` (`Save/LoadWorldGameData_`), 5416 bytes: world clock (4),
  NPC stop time (4), NPC status 0x140, NPC mbits 0x280, world state flags
  `WState` 0x1000, portal flag 0x40, base 0xc0, hret 0x18, home 0x48. **H**
* `D6SEGnn.GAM` (`SegWrite_Segment_`): 100-byte header of 25 int32
  (offset,count) pairs: switches, traps, BSP entities, objects, NPCs,
  monsters, events (+ sub-tables), NPC message queue offset; sections are
  kept raw by the parser. If this file exists the .TOL objects are **not**
  loaded. **H** (header) / not decoded (sections)
* `ResetAdventures_` deletes D6WORLD.DAT, D6SEG00..15.GAM and JOURNAL.*.
  The copies in the analysed game dir are from a play session.

## 11. emitters.dat (`LoadEmitterDesc_` 0x47bb10)

int32 n (31), n * 128-byte descriptors: +0 char[24] name, +0x18 u32 flags
(bits 1/2/4/8 tested), +0x44 sprite size (<<3), +0x60 frame count (0 =
from anim), +0x64 child emitter index+1, +0x68 lifetime, +0x6c rate,
+0x74 anim id (0 -> palette mode), +0x78 palette id, +0x7c alpha id. **M**

## 12. Notes for a map editor

* Editable at runtime level: TMR heights/tex/flags/water/walls/lights,
  TOL, NAV, FOL, D6LINK. Keep record indices stable.
* Light bytes are baked; there is no in-game relighting from .LIT. An editor
  must recompute them (or copy neighbours) after changing heights.
* Wall flags must stay consistent with neighbours (face bits 0x10..0x80,
  wall type) or tree walls render with gaps.
* BSP footprints ('B' records) are flattened in the height map and the
  automap .lm pages are pre-painted; both need regenerating if moved.
* Delete D6SEGnn.GAM (start a new adventure) to see .TOL edits.
* Unknowns: TMR header floats, tile bytes 10-11, TOM bytes 0-3/6-7, PAR
  values, NAV bytes 52..63 - all preserved raw.

## 13. Findings from the editor (d6edit)

* **Light bytes are per corner.** The six bytes of a tile are the light of
  corners (x,z) (x,z+1) (x+1,z) (x,z+1) (x+1,z+1) (x+1,z); every shared
  corner has the same value in all three shipped maps. **H**
* **Bake model.** light = clamp(ambient + k * sum over .LIT lights of
  max(0, 1 - d/R), ambient, 31), d = horizontal distance. Fitted: spokes 0
  and 3 ambient 10, k 17-18, R 10000; spoke 11 ambient 24, k 25, R 9000
  (mean error 0.4 - 0.9 shade). **M** (fitted, not read from code)
* Terrain nav ids are record indices (LoadTerrainNavPnts_ fills
  terrnavid2idx[record] = record); D6LINK and scripts use those ids, BSP nav
  points are addressed by their nav_id (GetNavPntIdx_). **H**
