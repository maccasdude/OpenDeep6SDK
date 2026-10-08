# Terrain walls, canopy, foliage and texture import

Reverse engineered from `deep6.exe` (Ghidra decompile in `re/decomp/`:
`tnew.c` RenderTile_, `terrain.c` Terrain_LoadTextures_ / Terrain_Draw_,
`tpoly2.c` Terrain_AddPoly_ / Terrain_NewerPoly_, `cardctrl.c`
Card_LoadTreeWall_ / Card_DrawTerrainPoly_, `scenload.c`
LoadStaticObjects_, `random.ASM` Random_). Python: `formats/d6walls.py`
(walls, canopy, foliage) and the texture writers in `d6terrain.py` /
`d6level.py`. Confidence: **H** read from code, **M** code + data,
**L** guess.

Constants read from the exe data segment are given with their address.

## 1. Tree / castle walls and canopy (RenderTile_, tnew.c 0x4f55b4)

### 1.1 Wall textures (Terrain_LoadTextures_, terrain.c 0x413c74) - H

`terrain+0x2c` holds 7 bitmap pointers (`Bitmap_LoadFromBMP_`):

| slot | spokes 0/3 | spoke 11 | size | palette |
|---|---|---|---|---|
| 0 | tiles/forest/forwal00.bmp | plmwal00.bmp | 128x512 | slot 6 (forwal.p16 / plmwal.p16) |
| 1 | forwal01.bmp | plmwal01.bmp | 128x512 | slot 6 |
| 2 | forwal02.bmp | plmwal02.bmp | 128x512 | slot 6 |
| 3 | forwal03.bmp | plmwal03.bmp | 128x512 | slot 6 |
| 4 | forcan00.bmp (canopy) | plmcan00.bmp | 128x128 | slot 6 |
| 5 | caswal00.bmp (castle wall) | same | 128x512 | slot 7 (caswal.p16) |
| 6 | cascan00.bmp (castle top) | same | 128x128 | slot 7 |

Palette slot 6 is `tiles/forest/plmwal.p16` on spoke 11, else `forwal.p16`
(Terrain_LoadPalettes_). The BMP's own RGB palette is ignored; the 8-bit
indices are looked up in the p16 shade table (32 rows x 256 RGB565).

The BMPs are drawn **opaque**: there is no colour key. Index 0 occurs
in the bitmaps (8917 pixels in forwal00) and is a normal dark colour.
Software path: `asm_PerspectiveScanline79_` (128x512) and
`Bsp_TexSpanSub16_` (128x128) write every texel. Card (D3D) path:
`Card_LoadTreeWall_` converts each bitmap to a 16-bit texture with shade
row 31 (`p16 + 0x3e00`) and no alpha. 128x512 walls are point-sampled
down to 64x256 (every 2nd column of every 2nd row) and their uv is scaled
by 0.5; 128x128 canopies keep full size (uv scale 1.0). Lighting in the
card path is per-vertex colour (light << 3, 0..255).

### 1.2 Per-tile geometry - H

Runtime tile struct (0x1c bytes) relevant fields: +0x02 height,
+0x04 `unk10` from the file (**wall height**, 4096 in every shipped tile),
+0x06.. light, +0x18 water, +0x19 walltype (bit 7 = runtime "seen"),
+0x1a flags, +0x1b runtime visibility.

For tile (x, z) with world origin `X = x*1024, Z = z*1024` RenderTile_
builds four ground vertices (all y = tile `height` of the named tile):

| name | world (x, z) | height from | light byte (file light[i]) |
|---|---|---|---|
| V0 | (X, Z+1024) | tile (x, z+1) | light[1] |
| V1 | (X+1024, Z+1024) | tile (x+1, z+1) | light[4] |
| V2 | (X+1024, Z) | tile (x+1, z) | light[2] |
| V3 | (X, Z) | tile (x, z) | light[0] |

(at the map edge the missing neighbour height is 0.)

If `flags != 0` it also builds the **top** vertices T0..T3: same x/z,
`y = ground y + unk10` of the same corner tile (so the wall/canopy top
follows the terrain 4096 units higher), light = `(l0+l1+l2+l3) >> 2`
(average of the 4 ground lights; the `>>2` is in the code, the sum is the
FPU value Ghidra lost - **M**).

Ground triangles (the default split is V0-V2; flags 2 or 8 select the
other diagonal V3-V1):

| split | tri A (plane +0x14) | skipped if | tri B (plane +0) | skipped if |
|---|---|---|---|---|
| default | V0 V1 V2 (corner +x+z) | flags & 1 | V2 V3 V0 (corner -x-z) | flags & 4 |
| alt (flags&0xa) | V3 V0 V1 (corner -x+z) | flags & 2 | V1 V2 V3 (corner +x-z) | flags & 8 |

A skipped ground triangle is replaced by the same triangle of the top
vertices (**canopy**), textured with slot 4 (tree) or slot 6 (walltype 2,
castle). So:

* `flags & 0x0f == 0x0f`: whole tile is canopy at ground+4096 (forest interior).
* flags 1 / 4 / 2 / 8: half tile canopy, half ground; the open corner is
  (-x,-z) / (+x,+z) / (+x,-z) / (-x,+z).

Canopy uv (texel units, `_DAT_005c83f9 = -0.5`): T0 (0.5, 0.5),
T1 (w-0.5, 0.5), T2 (w-0.5, h-0.5), T3 (0.5, h-0.5), w = h = 128: one
canopy bitmap per tile, row 0 at the +z edge. Canopy triangles are only
drawn if the camera is above the triangle's plane (backface test
against the ground plane normal shifted to the top vertex, eps -1.0 at
`_DAT_005c8401`), and not when the camera is under water.

Vertical wall quads, 4 vertices each, uv (0.5,0.5) (w-0.5,0.5)
(w-0.5,h-0.5) (0.5,h-0.5) in that vertex order (w=128, h=512, i.e. the
whole bitmap spans one tile edge and the full 4096 height; bitmap row 0 at
the top):

| flag | quad (top, top, ground, ground) | outward normal (`_s_wall_*` 0x5e8f8c..) | bitmap slot |
|---|---|---|---|
| 0x20 | T1 T0 V0 V1 (edge z+1) | +z (`_s_wall_N` 0,0,1) | A |
| 0x40 | T0 T3 V3 V0 (edge x) | -x (`_s_wall_W`) | B |
| 0x80 | T3 T2 V2 V3 (edge z) | -z (`_s_wall_S`) | A |
| 0x10 | T2 T1 V1 V2 (edge x+1) | +x (`_s_wall_E`) | B |
| 1 | T0 T2 V2 V0 (diag V0-V2) | toward -x-z (`_s_wall_NE` normal is +x+z, drawn when the camera is on the *negative* side) | C |
| 4 | T2 T0 V0 V2 | toward +x+z | C |
| 2 | T3 T1 V1 V3 (diag V3-V1) | toward +x-z | D |
| 8 | T1 T3 V3 V1 | toward -x+z | D |

Diagonal quads are only emitted when `flags & 0xf` is neither 0 nor 0xf.
Straight faces are culled unless the camera is on the outward side
(`dot(cam, n) - d >= -1`), i.e. single sided.

Bitmap choice (no hashing, no flags 0x10..0x80 involvement):
`c = (x + z) & 1` (checkerboard), walltype 1: A = slot c, B = slot c^1,
C = slot 2 + c, D = slot 2 + (c^1); walltype 2 (castle): every face uses
slot 5 (caswal00) and the canopy slot 6 (cascan00). So forwal00/01 alternate
on straight faces and forwal02/03 on diagonals. The diagonal quad is
1448 units wide but uses the same 128 texel width (stretched by sqrt 2).

All wall polys use draw mode 3 (`Terrain_AddPoly_` CL=3 -> perspective
correct in software when 1/z > 0x39000000), canopy mode 0 and ground mode
5 (software stipple) / 0 (card).

### 1.3 Data rules seen in the shipped maps - M

* `flags != 0` <=> `walltype != 0` (all three maps, no exceptions).
* face bits 0x10..0x80 only occur together with 0x0f (plus a single 0x18
  tile in spokes 3 and 11), diagonal tiles never carry face bits.
* walltype 2 (castle) exists only in spoke 3 (827 tiles).
* `unk10` = 4096 everywhere; editing it changes the wall height per
  corner.

### 1.4 Visibility and distance - H

* Terrain_Draw_ walks tiles in a square of +-`_gViewDist` tiles around the
  camera (`_gViewDist` default 16, adjustable 8..16 in the options /
  with the view distance keys), front to back, per terrain BSP leaf;
  tiles with runtime byte +0x1b == 0 are skipped.
* RenderTile_ first ray casts (`RayTerr_RayCast_`, rayterr.c) when the
  camera is less than 2560 above the tile (`_DAT_005c83d5`) or less than
  4096 above its lowest corner, and skips occluded tiles.
* Tiles inside a terrain BSP footprint (leaf with a BSP) draw no
  ground/walls/canopy, only their objects.
* Under water (`_gUnderwater` and no transparent water) no walls/canopy
  are drawn.

## 2. Foliage (.FOL, LoadStaticObjects_ scenload.c 0x4d6dcc) - H

* For every record after the header (all kinds, including blanks) the
  loader calls `Random_(n)` **before** testing the kind; only `'F'`
  records create a prop: `LoadPropObject_(n, table[Random_(n)], pos, rot)`.
  table = {1, 2, 3, 4, 0xe7} (n=5) on every spoke except 11,
  {0xe4, 0xe5, 0xe6} (n=3) on spoke 11.
* D6PROP record -> model: 1 Forest Tree (tree-forest.mdl), 2 Cedar
  (tree-cedar.mdl), 3 Willow (tree-willow.mdl), 4 Sequoia
  (tree-sequoia.mdl, objtype 0 = drawn as model, others objtype 1 = tree
  sprite via AddTreeSprite_), 0xe7 Birch (tree-birch.mdl); 0xe4..0xe6 Palm
  Tree (palm1/2/3.mdl).
* `Random_` (random.ASM 0x44cc76): `seed = seed * 0x1df5e0d (mod 2^32);
  return ((seed >> 16) * n) >> 16`. The seed is set once at start-up from
  `MMClockTicks_()` (deep6.c, InitRandom_ and WinMain init) and the same
  generator is used by all game code, so **the tree choice is not
  reproducible**: it changes on every spoke load. The record id field
  (0..4 in the data, distribution e.g. spoke 0: 118/1001/864/712/20) is
  ignored by the game; it is probably the editor's choice.
  `d6walls.foliage_choice()` therefore offers (a) `mode='id'`: table[id % n]
  as a stable editor preview, and (b) `mode='random', seed=s`: an exact
  emulation of the loader for a given seed.
* Position: record pos; y is 0 in all shipped records -> LoadPropObject_
  snaps it to the terrain (`D6_AdjustBase_`) when not inside a BSP area.
  Rotation: record rot copied to GraphObj+0x14 (yaw values 0..6 / 1024 in
  the data, i.e. effectively unrotated; tree sprites are billboards anyway).
* Trees stand on open ground next to the walls (spoke 0: 1554 on flag-0
  tiles, ~1160 on diagonal wall tiles' open half).

## 3. Shaded palettes (.p16, twd palettes, model palettes)

### 3.1 Shade rows - H (code) / verified on all files

`Pal16_Calculate_` / `RefPal16_Calculate_` (pal16.c 0x44e124) build a
0x4000-byte table from 256 RGB888 base colours:

```
for s in 0..31:  for i in 0..255:
    r = (R[i] * (s+1)) // 32 ; g = (G[i] * (s+1)) // 32 ; b = (B[i] * (s+1)) // 32
    p16[s][i] = (r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)
```

Row 31 is the full colour, row 0 is 1/32 of it (not black). The file holds
the un-gamma'd table; at load time the engine applies
`Pal16_SetRGBGamma_` (per channel `c8 + min(c8*g/128, g)`, g =
`_g_GammaVal` from the options) and, if the display is not 565,
`Pal16_ConvertRBGBits_` (555). An SDK must write the plain table.

Checked against every shipped file (exists a base colour reproducing all
32 rows of every entry):

* all `tiles/80x/pal.p16`, `tiles/forest/*.p16`, `tiles/pal.p16`, font
  and sky `.p16` files, `efxgfx/*.p16`: formula above, 0 mismatches.
  `tiles/forest/forwal.p16` row 31 equals the BMP palette of
  forwal00.bmp truncated to 565 (the BMP palette is the base colour set).
* `tiles/water.p16`, `gfx/particle/*.p16`: hand-made gradients (not this
  formula).
* `.twd` palettes: 392 of 435 use the formula above; 43 (e.g. all of
  crypta.twd) use `(c * s) // 32` instead (row 0 black, row 31 = 31/32).
  Both work; new palettes should use the engine formula.
* model palettes (paltype 0, 695 files): formula above (index 0 is
  forced to black at load time by Model_ResetTextures_).

`d6terrain.make_p16(rgb256)` implements it (bytes, 0x4000).

## 4. BSP level textures (.twd) for import - H (texlist.c Texlist_LoadBSPWadfile_ 0x5507cc)

Layout (see levels.md section 3): `[int32 -666]`, int32 npal, int32 ntex,
npal x 0x4000 p16 tables (**per wad**, any texture may pick any of them by
its int16 palette index), ntex x {char name[64], int16 palette, int32 w,
int32 h, mip0..mip3}.

* Size: **128x128 only.** The loader reads into fixed 0x4000/0x1000/0x400/
  0x100 buffers and copies `(0x80>>mip)^2` bytes to the master slot; larger
  textures overflow, smaller ones get garbage. (One shipped texture,
  shrinea.twd `420009snakewall`, is 128x127 and works only by luck.)
* Mips: the shipped wads were mipped with the same algorithm as termip.exe
  (section 5) using the texture's palette: 507/507 sampled textures
  reproduce exactly. Keep the -666 header (without it only mip0 is read but
  the master copy still uses 4 levels).
* Palettes: one p16 per wad entry, built with the section 3 formula; each
  goes into a new master slot (max 512 palettes over all loaded BSPs,
  `Texlist_FindPaletteSlot_`), gamma applied at load.
* Master texture table: max 1024 slots (`Texlist_FindTextureSlot_`) shared
  by all loaded BSPs. **Textures are de-duplicated by their 16x16 mip only**
  (256-byte compare, name and palette ignored): a new texture whose mip3 equals
  one already loaded reuses that slot and its palette. Make mip3 unique.
* Texinfo: `flags & 0xfff` = index in this .twd, remapped to the master slot
  (`Texlist_AssignFaceTex_`); faces with flags -1, 0x8000 (water) or 0x2000
  (lava) are not assigned. Water faces draw syswat.twd texture 0 (warped,
  `_g_watersurf`), lava faces (0x2000) syswat texture 1; selection is by
  position in syswat.twd, not by name.
* Names: copied into the master entry but never compared; no name-based
  animation, water or sky (unlike Quake). Shipped names are library ids
  like `020522wall-top`. Sky is not a BSP texture (skydrop.m2k).
* Writer: `TexWad.add_texture(name, rgb, palette=None|int)` and
  `replace_texture(i, rgb, palette='keep'|None|int)`. palette=int quantises
  to that wad palette (nearest RGB to its base colours recovered by
  `p16_base_rgb`); None builds a new 256-colour palette (PIL median cut) and
  appends its p16. Mips via termip algorithm; warns on mip3 collisions.
  `build()` == `to_bytes()`, byte identical for all 67 shipped wads;
  replacing a texture with its own decoded image reproduces the same indices.

## 5. Terrain tile textures (tiles/80x) - H

* `tiles/<dir>/NAME.mip`: 128x128 + 64 + 32 + 16 indices (21760 bytes),
  no header. `tiles/<dir>/pal.p16` is the only palette for every tile of
  that dir (Terrain_LoadTextures_ sets texture palette = `_gPalettes[dir-801]`),
  so **all tiles of one dir must share the dir palette**; a tile with new
  colours needs its own dir or a re-quantised dir.
* Dirs: exactly 801..806 (`LoadTilesFromDir_(0x321..0x326)`, palette slot
  check `0 <= dir-801 <= 8`), no more can be added without code changes.
  `TileDir_` returns the dir whose listing (InitDirList_) contains the
  canonical name; first match in 801..806 order (M: dir list built in that
  order). Names are the canonical (lexicographically smallest rotation)
  4-char corner code, so a new tile must be stored under its canonical name.
* Mips (tiles/termip.exe, disassembled; reproduces **all 775** shipped .mip
  files byte for byte): colour table = p16 row 31 split as R bits 11..15,
  G bits 6..10, B bits 0..4 (5 bits each). Level k from level k-1: for each
  2x2 block and channel `c = (a+b+c+d + 2*max(a,b,c,d)) // 6`, then the
  palette index with the smallest squared distance (first minimum wins,
  except on ties when one channel of the target is strictly largest: the
  candidate closer in that channel wins). Not decimation, not a plain box.
* API: `TileMip.from_indexed(img128, palette)`, `termip_downsample`,
  `termip_palette`, `make_p16(rgb256)`, `p16_base_rgb(p16)` (inverse; round
  trips the shipped tables), `quantize(rgb, palette_rgb, exclude=())`,
  `tile_dir_palette_rgb(game, '803')`.
* tiles/forest/bmp2p16.exe (`[-nolight] in out`) is the tool that made the
  wall p16s from the BMP palettes (output matches section 3).

## 6. Model textures (paltype 0/3) - H/M

Same 32x256 RGB565 table, same formula (695/695 model palettes fit).
Index 0 is forced to black / transparent at load, so quantise new model
textures with `quantize(..., exclude=(0,))` and write
`make_p16(base_rgb)` as the palette; the engine selects the shade row from
the vertex / terrain light (31 = full).

## 7. Uncertain

* Canopy-top light = average of the 4 corner lights: the `>> 2` is in the
  code, the summed FPU value is inferred.
* The editor meaning of the .FOL id field (0..4) is a guess; the game ignores it.
* TileDir_ dir order on duplicate names not traced in detail.
* Software-path ground uses draw mode 5 (stipple scanline) for all tiles; its
  visual effect was not investigated.
* Preview render: `python3 formats/d6walls.py --render <game> 0 196 66 228 98 out.png`
  (oblique ortho, single-sided walls, foliage as circles) shows the clearing at
  the spoke 0 town gate enclosed by tree walls with canopy on top, as in the game.
