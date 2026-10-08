# Deep6 indoor level formats (Wizards & Warriors, 2000)

Status: reverse engineered from the Ghidra decompile of `deep6.exe` (module
names below are the original source modules) and checked against all retail
data. Reader/writer: `formats/d6level.py`. Running
`python3 formats/d6level.py --selftest <gamedir>` parses and rewrites
every level file and confirms each one is byte-identical (736/736 files: 66
levels plus `syswat.twd`, TOL, D6LINK, .tc, .pt*). Visual check:
`formats/render_level.py <gamedir> <level>`.

All values are little endian. Confidence levels: **H** = read directly from
loader code and confirmed by data, **M** = inferred from usage or data
statistics, **L** = guess.

## 1. Overview and coordinate systems

An indoor level `<name>` is a set of files sharing one base name. They are
opened case-insensitively (the data mixes `TEMPLEA.BOL`, `templea.bsp` and so
on).

| ext | content | loader (module) | needed |
|---|---|---|---|
| .bsp | geometry, BSP tree, collision, entities | `CreateBSPFile_` / `CompleteBSPFile_` (bspfile.c) | yes |
| .twd | textures + 16-bit shaded palettes | `Texlist_LoadBSPWadfile_` (texlist.c) | yes (fatal error if missing) |
| .ls / .lf | hardware lightmaps (RGB565) + per-face info | `BSP_LoadSurfaceLight_` (bspfile.c) | optional |
| .lss / .lfs | software lightmaps (8-bit) + per-face info | `BSP_LoadSWSurfLight_` (bspfile.c) | optional |
| .lgt / .rgb / (.int) | coloured point lights | `Load_BSP_ColoredLights_` (cardlite.c) | optional |
| .nvs / .l2n | AI nav graph, leaf->nav lookup | `LoadNavSystem_` (nav_sys.c) | optional |
| .bol | props, monsters and items placed in the BSP | `LoadBspObjects_` (scenload.c) | optional |
| .lt | per-surfedge light bytes (no retail files) | `LoadLightFile_` (bspfile.c) | unused |
| .tc, .pt1, .pt2 | text tool output | not read by the exe | - |

Coordinates:
* BSP units are Quake-like. **Y is up** (nav points, door axes and floors all
  confirm this). The renders treat X/Z as the ground plane.
* World units = BSP units * 16 (`World2BspTrans_`: `bsp = (world - origin) * 0.0625`).
* One terrain tile = 1024 world units = 64 BSP units. A 128x128 texture at
  the usual texinfo scale of 0.5 covers exactly one tile.
* Each loaded BSP has an origin in world space (bsp+0x118ec). See section 12.

## 2. .bsp (H)

This is the Quake 1 BSP layout with `version = 28` and the usual 15 lumps:
a header of `int32 version`, then 15 x `{int32 ofs, int32 len}`, 124 bytes in
total. The compiler wrote the lumps in qbsp order (1,10,3,5,6,7,9,11,13,12,14,8,4,0),
starts each lump on a 4-byte boundary with zero padding, and pads the file end
to 4 bytes. Lump 2 (miptex) is always empty with ofs=0; textures live in .twd.
Only lumps 0, 1, 5, 7, 9, 10, 14 (plus vertices) are read by
`CreateBSPFile_`. The rest (vertices, texinfo, faces, marksurfaces, surfedges,
edges, vis, lighting) are read later by `CompleteBSPFile_`, which runs when
the player comes near (`UpdateBSPFileStates_`). `RevertBSPFile_` unloads them
again.

| # | lump | record | fields |
|---|---|---|---|
| 0 | entities | text | Quake `{ "key" "value" }` blocks, `\n` line ends, trailing NUL (H) |
| 1 | planes | 20 | float normal[3], float dist, int32 type (0..2 axial X/Y/Z, 3..5 major axis) (H) |
| 2 | textures | - | always empty (H) |
| 3 | vertices | 12 | float x,y,z (H) |
| 4 | visibility | bytes | Quake RLE PVS, indexed by leaf.visofs (H by Quake analogy; M) |
| 5 | nodes | 24 | int32 planenum; int16 front, back (<0: leaf -(n+1)); int16 mins[3], maxs[3]; u16 firstface, numfaces (H, child order confirmed in `FindCurrentNavPt_r_`) |
| 6 | texinfo | 52 | float s[4], t[4]; float origin[3]; int32 flags; int32 runtime (see below) (H) |
| 7 | faces | 20 | int16 planenum, side; int32 firstedge; int16 numedges, texinfo; u8 styles[4] (always 255); int32 lightofs (always -1) (H) |
| 8 | lighting | 2/surfedge | u16 per-vertex (per surfedge) light; `g_fullbright` if smaller than numsurfedges; overwritten by `Bsp_SetBorderLights_` / `.lt` (M) |
| 9 | clipnodes | 8 | int32 planenum, int16 children[2] (H, Quake hulls 1 and 2; sizes measured from the data: hull 1 x/z +-32, y -2..+4; hull 2 x/z +-64, y -2..+4; the exe's `_cliphull_size` table says (-32,-4,-32)-(32,2,32) and (-32,-16,-32)-(32,96,32) but the compiled clip planes follow the measured sizes) |
| 10 | leafs | 28 | int32 contents, int32 visofs, int16 mins[3], maxs[3], u16 firstmarksurface, nummarksurfaces, u8 ambient[4] (H; ambient meaning L) |
| 11 | marksurfaces | 2 | u16 face index (H) |
| 12 | edges | 4 | u16 v0, v1 (H) |
| 13 | surfedges | 4 | int32 (negative = reversed edge) (H) |
| 14 | models | 68 | float mins[3], maxs[3], origin[3]; int32 headnode[4], visleafs, firstface, numfaces; int32 entity (runtime: index into the runtime entity table, written by `SetRuntimeEntities_`; file holds 0,1,2...) (H) |

Leaf contents (H, from `InBspWater_`): -1 empty, -2 solid, -3 water, -5 lava
(-4 slime and -6 sky are Quake values not seen in use).

Texinfo (`bsp_3DCard_PolyDraw_`, `d_span.c`, `Texlist_AssignFaceTex_`):
* Texture coordinate (H): `u = ((P - origin) . s.xyz + s.w * 0.25) / 32` gives
  normalised UV (1.0 = one texture repeat). In texels:
  `u_tex = 4 * (P - origin).s + s.w`. Unlike Quake, Deep6 stores a texture
  origin point per texinfo. Confirmed visually: floors tile seamlessly.
* `flags` (int32):
  * -1: no texture, the face is not drawn (H)
  * bits 0-11: index into the level's .twd texture list (H)
  * 0x2000: lava/translucent pass (M)
  * 0x8000: water pass, drawn with `syswat.twd` (H for the bit; the mapping to the curwater/curlava texture is M)
  * 0x20000: drawn as a terrain polygon when horizontal (M)
  * 0x1000 and 0x10000 are cleared on load (runtime use)
* `runtime`: 0 in the files; used at load time as an "already assigned" flag.

Entities (H, `SetRuntimeEntities_`): `worldspawn`, `light` (d6type 3, `origin`,
`d6light`, `d6entid`; these duplicate .lgt), and `func_door` (d6type 1 = slide
or 2 = rotate). A door has `model "*n"`, `d6axis` (0/1/2), `d6maxmove` (slide
distance in BSP units, signed), `d6rotx/y/z`, `d6flags`, `d6entid` (id that
events use to address the entity, via the table at bsp+0x5c90), and an
optional `d6string` (name shown to the player, max 15 chars). Only entities
with `model` become runtime entities. Engine limits: 9999 nodes, 19999 faces.

## 3. .twd texture wad (H)

```
[int32 -666]            present => 4 mip levels, absent => 1 (all retail files have it)
int32 npal, int32 ntex
npal x u16[32][256]     0x4000 bytes per palette: 32 shade rows x 256 RGB565 colours,
                        row 31 = full brightness, row 0 = black (M: linear ramp observed)
ntex x {
  char  name[64]        NUL padded
  int16 palette         index into this file's palettes
  int32 width, height   always 128x128 (the texture cache assumes 0x80>>mip)
  u8    mip0[w*h], mip1[w/2*h/2], mip2, mip3   8-bit palette indices
}
```
At load time palettes and textures are merged into a master table shared
between BSPs. Textures are matched on their 16x16 mip; `texinfo.flags & 0xfff`
is remapped to the slot. `syswat.twd` holds the shared water and lava
textures. `LoadTexWad_` uses the same format.

## 4. .lf / .lfs per-face lightmap info (H)

There is one 24-byte record per BSP face (record count = numfaces). Six
int32 fields:
`smin, tmin, width, height, count(=w*h), offset`. For .lf the offset is a byte
offset into the .ls blob, with 2 bytes per texel. For .lfs it is the index of
the texel byte in the .lss blob. The two files have different luxel grids (the
hardware path uses about 9x9 where the software path uses 8x8, for example).
The engine converts these into 0x34-byte runtime surface records. The
software path picks a mip level from max(w,h).
Hardware lightmaps (.lf, H, checked against every face of the retail data and
in the game with compiled levels):
* Axis pair by plane type (`MakeLightVecs_`, table `_g_tvecs`): type 0/3
  (x major) s = z, t = y; type 1/4 (y major) s = x, t = z; type 2/5 (z major)
  s = x, t = y. A type 4 plane with |ny| < 0.95 uses the type 3 pair if
  |nx| > |nz|, else the type 5 pair.
* `smin = (floor(min s / 32) - 1) * 32`, `width = ceil(max s / 32) -
  floor(min s / 32) + 3` (one luxel of border on each side), same for t.
* The card path (`bsp_3DCard_PolyDraw_`) samples the lightmap at
  `((P.s - smin) / 32) / 16`: the lightmap is put into a 16x16 RGB565
  texture (`CC_SetLMapCache_`), so luxel i is centred at `smin + 32 (i + 0.5)`.
  A face whose lightmap is wider or taller than 16 luxels is **not drawn**.
* The texels modulate the texture (31/63/31 = full brightness).
* The software files (.lfs/.lss) use another grid (L); the card renderer does
  not read them.

Without .lf/.ls the card renderer crashes (`bsp+0x5c` is NULL): a level must
have hardware lightmaps.

## 5. .ls / .lss lightmap texels (H)

`int32 nbytes`, then the texel blob. .ls holds u16 RGB565 texels, adjusted on
load by `FuckLightData_` (brightness offset) and converted to 555 if needed.
.lss holds u8 intensities 0..31 that index the palette shade rows. Both
files must be present together with their .lf/.lfs, or lighting is disabled.

## 6. .lgt / .rgb / .int coloured lights (H)

* .lgt: N x `{float x,y,z (BSP units); float intensity}`. N = filesize/16.
  The loader multiplies intensity by 16. The positions match the `light`
  entities.
* .rgb: N x uint32 colour: R = byte 0, G = byte 1, B = byte 2, byte 3 unused.
  If the file is missing every light is white.
* .int: N x int32 (purpose unknown, L). No retail files; missing means 0.
Each light becomes a `LightObj` at `Bsp2WorldTrans(pos)`.

## 7. .nvs nav points (H for layout, M for fields)

```
int32 magic (-666), int32 unknown (varies 1..1375; probably a next-id counter, L), int32 count
count x 32 bytes:
  float x,y,z           BSP units; converted to world on load (H)
  float radius?         nearly always 0 (one file has 768.0) (L)
  int16 link[6]         0-based neighbour index, -1 = none; +1 on load because runtime is 1-based (H)
  u16   flags           0x10 => also sets 0x20; 0x02 lava and 0x04 water are set at runtime;
                        0x01, 0x08, 0x20 seen in data (meaning L)
  u16   nav_id          unique id < 20000, used by D6LINK files and events (H)
```

## 8. .l2n leaf to nav (H)

One int16 per BSP leaf, giving the 0-based nav index (-1 = none; +1 on load).
Used by `FindCurrentNavPt_`. The entry count equals numleafs.

## 9. .bol object list (H for layout, M for some fields)

These are 64-byte records. TOL/FOL files use the same layout.
* Record 0 is the header: int32 total record count (including the header)
  followed by 60 zero bytes.
* Each object record:

| off | type | meaning |
|---|---|---|
| 0 | char | 'P' prop, 'M' monster, 'I' item; 0 = deleted slot (still counted for object tags) (H) |
| 1 | char[3] | ASCII decimal record number in D6PROP.DAT / D6MONS.DAT / D6ITEM.DAT (H) |
| 4 | float[3] | position, world units relative to the BSP origin (render confirms /16 = BSP units) (H) |
| 16 | float[3] | rotation x,y,z (mostly y; values such as 256, 512, -192; unit L) (M) |
| 28 | 4 bytes | zero |
| 32 | u8 | prop: mine-cart group id (spoke 5 only); monster: extra parameter passed to `MonsterEntry_` (M) |
| 33,34 | u8 | unused in .bol |
| 35 | u8 | prop: non-zero = starts switched off/emitter off (M) |
| 36 | 28 bytes | zero |

Object tags are numbered `gBspObjFirst[bsp] + record index`. Events refer to
objects as `bsp*100000 + index` (`GetObjNum_`).

## 10. .tc, .pt1, .pt2 (H: not used by the game)

* .tc: CRLF text "Texture Usage Report For <name>" followed by lines of the
  form `NAME: %-37s COUNT: %d`.
* .pt1/.pt2: CRLF text with one `%f %f %f` point per line, in BSP-like
  coordinates. They look like recorded paths. There are no references in the
  exe.

## 11. How levels are listed and connected

There is no data file that lists the levels. The world is split into 13
"spokes" (0..12). `LoadSpokeSegment_` (d6spoke.c) switches on
`_gSpokeNumber`:

* **Terrain spokes 0, 3, 11** load `SPOKExx.TMR` (terrain),
  `SPOKExx.NAV` and `SPOKExx.TOL`. The TOL 'B' records place the small "t*"
  BSPs (tgatea, tcrypt, ttemple, tshurua, ...) into the terrain
  (`LoadTerrainBsps_`). A 'B' record has: pos (x, 0, z) = tile*1024; float
  width at +16 and height at +24 (tiles); u8 slot at +32; int8 offx, offy, offz
  at +33..35; name at +48. BSP index = slot (0-based).
* **Dungeon spokes** use a null terrain plus a hard coded
  `Terrain_BSPLoad_` list (index 0 = null BSP, the dungeons are 1..n):
  1 CRYPTA, CRYPTB; 2 TEMPLEB, TEMPLEA; 4 SHURU; 5 MINESA-D; 6 OGREA-C;
  7 DRAGONA-C; 8 LICHA, LICHB; 9 SUNKENA, SUNKENB; 10 SHRINEA; 12 PYRAMA,
  PYRAMB. The parameters are in `d6level.SPOKE_TABLE` (M: decoded from Watcom
  register arguments).
  `Terrain_BSPLoad_(name, bx, by, bw, bh, offx, offy, offz, p10, p11)` sets the
  origin to `((bx-offx)*1024, -offy*1024, (by-offz)*1024)` world units and the
  tile rectangle to `(bx+p10-offx, by+p11-offz, bw, bh)`. The rectangle is used
  for streaming (`CompleteBSPFile_` within range) and for border lights.
  Example: the overworld `tcrypt` and spoke-1 `CRYPTA` share the same origin.
* Objects: terrain spokes use TOL plus each BSP's .bol
  (`LoadAllBspObjects_`). Dungeon spokes load the listed .bol files with BSP
  index 1..n. The debug viewer `FetchObjects_` (mapobj.c) holds the same list
  of names. `SPOKExx.FOL` holds static foliage ('F' records).
* **Connections inside a spoke**: `D6LINKxx.DAT`, which has an int32 count,
  4 unused bytes, then count x `{int16 nav_a, nav_b, bsp_a, bsp_b}`. bsp = -1
  means a terrain nav point (TOL 'N' id). This joins the nav graphs of
  terrain and BSPs (and BSP to BSP, for example CRYPTA nav 80 <-> CRYPTB nav
  80). `python3 d6level.py --spokes <gamedir>` prints everything.
* **Travel between spokes** (dungeon entrances and exits) is not in these
  files. It is in the per-spoke event/trigger data (D6TrigXX/D6SwitXX/D6SEG).
  See `exits.md` and `formats/d6exits.py`: LOADSEGMENT re-bases the party's
  BSP-local position onto a BSP slot of the new spoke, and GOTOBSP does the
  same between BSPs of one spoke.

## 12. Notes for an editor

* Everything round-trips, and all record types are exposed as namedtuples or
  arrays with named fields.
* Faces (H): the engine draws a face only if its **last** vertex is a corner
  (`SetWorldCornerFlags_` sets a bit per non-collinear vertex,
  `bsp_3DCard_PolyDraw_` tests the bit of the last one). Face windings are
  clockwise seen from the front, in the left-handed engine space.
* Leaf `ambient[0]` is the light level (0..31) used for objects standing in
  the leaf (`set_ambient_light_`); the other three bytes are 255 or 0.
* The compiler in this SDK: `formats/d6bspc.py` (see docs/geometry.md).
* A new or changed BSP needs a compiler. The BSP tree, clipnodes (collision),
  PVS, marksurfaces and per-face .lf/.ls lightmaps are regenerated together.
  They are standard Quake-1 structures: the SDK runs a qbsp/vis with the
  Deep6 hulls and converts the result to version 28 (68-byte models, 52-byte
  texinfo, lump 8 as u16 per surfedge, mirrored axes).
* Things that can be edited without recompiling: entities (door parameters,
  names), .bol objects, lights (.lgt/.rgb, although baked lightmaps will not
  follow), nav graph (.nvs/.l2n), textures (.twd, keep 128x128 and indices).
