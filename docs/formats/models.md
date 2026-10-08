# Deep6 3D models (`models/**/*.mdl`)

Wizards & Warriors (2000, engine "Deep6"). This document covers the `.mdl` model
format well enough to draw monsters, items, props and PCs textured, and how the
D6MONS / D6ITEM / D6PROP databases select a model.

Reference implementation: `formats/d6model.py` (decoder, record->model mapping,
numpy software renderer). The region walker `d6data.Mdl` (byte-identical
rewrite) uses the same layout.

```
python3 formats/d6model.py --render GAMEDIR M 1 out.png [--yaw D] [--pitch D] [--frame N] [--size PX]
python3 formats/d6model.py --render GAMEDIR prop/chest.mdl out.png
python3 formats/d6model.py --info   GAMEDIR P 6
python3 formats/d6model.py --selftest GAMEDIR
```

Self test on the GOG install: 728 .mdl decode with 0 failures (geometry, uv and
poly index ranges checked). Every D6MONS (372), D6ITEM (857) and D6PROP (238)
record resolves to an existing file except P28 (`prop/BUTTON-CRYPT.MDL`, missing
from the retail data; only `button-crypt-flat.mdl` ships).

Sources (decompile module / function): `model.c` Model_Read_, Model_ResetTextures_,
Part_FindSprites_, CalcModelHeight_, Model_BoundSphere_, Part_Draw_,
Part_DrawSprite_, Attachment_Draw_, Attachment_DrawModified_, Model_Draw_;
`mpoly.c` drawTri_; `pal16.c` RefPal16_Calculate_, Pal16_SetRGBGamma_;
`graphobj.c` GraphObj_Create_, GraphObj_UpdateMatrix_, GraphObj_HidePolys_;
`vector.c` Mat3_*/Vec3_*; `monster.c` LoadMonsterModel_, LoadMonsterGFX_,
TwiddleHair_, TwiddleEyes_; `scenload.c` LoadItemObject_, LoadPropObject_,
GetHelmData_; `mdldata.c` tables in deep6.exe.

Confidence: **H** = read from loader/consumer code and confirmed in data and
renders; **M** = from usage, plausible; **L** = guess.

All values little-endian.

---------------------------------------------------------------------------

## 1. Conventions (units, axes, placement)

| Item | Value | Conf |
|---|---|---|
| Units | Model coordinates are world units; the engine applies **no scale** (object matrix is a pure rotation, `GraphObj_UpdateMatrix_`). 1 terrain tile = 1024 units, so 1 unit is about 1 mm: HM (human male) is 1665 tall, skeleton 1563, horse 1980, barrel 782, wooden chair 1098, long sword 925 long. | H |
| Handedness | Left-handed, y up: camera space is x right, y up, z into the screen (`Camera_ProjectVert3_`: sx = cx + x*f/z, sy = cy - y*f/z). All engine matrices are rotations, so world space is left-handed too. Text on `prop/sign-cemetery.mdl` reads correctly under this convention. | H |
| Origin | The ground point of the object: feet / base at y ~ 0 (min y is 0..20 for monsters/props); x/z roughly centred. CalcModelHeight_ = max y of frame 0. Items are authored around their grip / attach point (e.g. sword grip at z = 0, blade along +z). | H |
| Facing | At yaw 0 the model faces **+Z** (horse/monster heads point +Z; walk-cycle root motion runs along +Z). | H |
| Placement | `world = v * M + pos` (row vector, row-major 3x3). `M = RotY(yaw)` then Z and X rotations (gobj+0x18 yaw, +0x14 Z, +0x1c X, angles in 1/1024 turn, `GraphObj_UpdateMatrix_`). `RotY(a) = [[c,0,-s],[0,1,0],[s,0,c]]`, so local +Z maps to (sin a, 0, cos a), the engine's usual forward vector for heading a. | H (yaw) / M (X/Z order) |
| Front faces | Clockwise on screen (y down): drawTri_ draws when `(x0-x2)*(y1-y2)-(x1-x2)*(y0-y2) > 0`, otherwise only if poly flag 0x80 (two-sided). Checked on a closed barrel (47/51 faces agree; the rest are near edge-on). | H |
| D6PROP `scale` (0x24) | Not a render scale: copied to model+0x69c when prop flags&2, which is the collision radius (default 528.0, set in Model_BoundSphere_; monsters use D6MONS+0x98). | H |
| Model_Read_ last arg | float stored at model+0x700 (0.5 for items/props, from `_MonMDLData` for monsters); only passed to Anim_Load_ (animated-texture speed). Not a scale. | M |

---------------------------------------------------------------------------

## 2. File layout

### 2.1 Header

| Off | Type | Field | Conf |
|---|---|---|---|
| 0x00 | u32 | magic 0x4D444C20 (bytes `" LDM"`) | H |
| 0x04 | u32 | version: 9 or 10; bit 0x1000000 = framebits table present | H |
| 0x08 | u32 | file offset of the part offset table | H |
| 0x0C | u8 | paltype (0, 1, 2, 3) | H |
| 0x0D | ... | palette, size by paltype (below) | H |
| +0 | u16 | nframes (model+0x80) | H |
| +2 | u8[nframes] | framebits, only if version bit 0x1000000: 0 = frame not stored in the parts | H |
| +.. | u16[256][3] | animation table (model+0x82): (first frame, last frame, action frame) per animation id; all zero = unused; last < first plays backwards. The third value is the "event" frame (hit / SFX trigger, atqlist.c). Animation 0 is the idle/stand loop the game starts with (LoadMonsterGFX_ sets frame = anims[cur].first). | H (first/last) / M (third) |
| +.. | u8 | nattach | H |
| +.. | per attachment | u16 n; f32[n][3] translation; f32[n][3][3] rotation (row-major). n is 0 or nframes. | H |
| +.. | u8 | nparts | H |

Palette (`paltype`):

| paltype | Size | Content | Conf |
|---|---|---|---|
| 0, 3 | 0x4000 | u16 RGB565 `[shade 0..31][index 0..255]`; shade s = rgb*(s+1)/32 (RefPal16_Calculate_), so shade 31 is the full colour. 589+106 files. | H |
| 1 | 0x300 | 256 x RGB888 (PC models `pc/*.mdl`, 33 files); colour 0 is the key colour (0,255,0). The engine remaps it into a shared reduced palette. | H |
| 2 | 0x40 | char[64] name of a shared palette `models/refpal/<name>`; unused in retail (dir empty). | H (code) |

At load time Model_ResetTextures_ sets colour 0 of every shade row to 0, so
**index 0 draws black on normal polys and is transparent on masked polys and
sprites**. The engine then applies a small gamma lift (Pal16_SetRGBGamma_:
c + min(c*g/128, g)) and a per-vertex/terrain light level that selects the shade
row (0x1f = full). `d6model` returns shade 31 as `palette` and all rows as `shades`.

### 2.2 Part table and part

The part table (header +0x08) is `u32[nparts]` file offsets. 717 models have
one part, 10 have two, 1 has three. The engine keeps parts in a linked list
in **reverse** order (model+0x68c = last part read); the first-read part's group
count is what GraphObj uses, which is the same for all parts in practice.

| Off | Type | Field | Conf |
|---|---|---|---|
| +0x00 | u32 | file offset of this part's `npaths` field (start of the path/frame block) | M (holds for all files except the 33 in efx/, where it is stale; the loader ignores it) |
| +0x04 | u32 | file offset of the texture offset array (just after the `ntextures` byte) | M (holds for all files except the 33 in efx/, where it is stale; the loader ignores it) |
| +0x08 | u32 | file offset of the "sprite list" (u8 n + u32[n]); n is 0 in all retail files | M |
| +0x0C | u32 | part flags (model part+0x1c); 0 in all files; runtime bits 8 Y-lock sprite, 0x20 Y-lock, 0x40 perspective-correct, 0x10 hidden | H (runtime) |
| +0x10 | u32 | nverts | H |
| +0x14 | per LOD (1 for v9, 4 for v10) | see below | H |
| +.. | u8 | ngroups: number of mesh groups (GraphObj visibility table size) | H |
| +.. | u16 | nframes (= model nframes) | H |
| +.. | u32 | npaths | H |
| +.. | u16[npaths] | path lengths (sum = nverts) | H |
| +.. | u32 | framesize: bytes of one stored frame = sum(6 + 3*(len-1)) | H |
| +.. | per frame | frame data (framesize bytes, absent if framebits[f] == 0), then f32[3] unknown (almost always 0) and f32[3] root-motion translation | H / L / M |
| +.. | u8 | ntextures | H |
| +.. | u32[ntextures] | file offsets of textures | H |

Per LOD level (v10 has 4: engine picks 0..3 by camera depth <= 1536, 2560, 4096, else 3):

| Type | Field | Conf |
|---|---|---|
| u32 | nuv | H |
| (s16 vertex, s16 u, s16 v)[nuv] | corner list: vertex index into the part, texel coordinates in the polygon's texture (0..w, 0..h); divide by w/h for 0..1 (the D3D path does u*scale/w) | H |
| u32 | npolys | H |
| (u16 firstuv, u8 ncorners, u8 texture)[npolys] | polygon = corners uv[first .. first+ncorners-1]; drawn as a fan (0, k+1, k+2); ncorners <= 32; ncorners == 1 = sprite; texture = index into the part's texture table | H |
| u8[npolys] | poly flags | H |

Poly flags:

| Bits | Meaning | Conf |
|---|---|---|
| 0x1F | mesh group; drawn only if gobj visibility[group] != 0. GraphObj_Create_ enables group 0 only; game code toggles others with GraphObj_HidePolys_ | H |
| 0x20 | 50% translucent | H |
| 0x40 | masked: colour index 0 transparent | H |
| 0x80 | two-sided (no back-face cull) | H |

Mesh groups in data: 9-group humanoids (NPCs, PCs, several monsters) use
0 = body, 1..4 = hair variants (TwiddleHair_ shows one), 5..8 = eye states
(TwiddleEyes_, 5 = normal). Two-group props use group 1 for an alternate state
(key in a lockplate, chest contents, book on a lectern...; prop flags&0x10
"container" toggles it). `campfire.mdl`: 0 = flame sprite, 1 = unlit logs,
2 = burning logs. Some item models (book-plain, heart) put everything in group 1.
`Model.default_groups()` returns (0,1,5) for 9-group models, otherwise (0,) plus
the lowest solid group when group 0 has no triangles.

### 2.3 Frame data (vertex paths)

For each path in order: `s16 x, y, z` start vertex, then `len-1` times
`s8 dx, dy, dz` cumulative deltas. Vertices are numbered consecutively over all
paths (Part_Draw_, Model_BoundSphere_). **H**

Frames missing from framebits (PC models: e.g. HM stores 96 of 955 frames)
were removed by the developers' `pcreduce.exe`, which keeps only the
animations listed in `<model>.seq` (section 6). The game never needs the
others: PC models appear only on the character sheet.
`Model.default_frame` = anims[0].first if stored, else the first stored frame.

The second per-frame vector is the root motion of the animation (skeleton walk
anim 6: z grows 0..2790 over the cycle, y bobs ~100). The vertices themselves do
not include it; the game moves the object. **M**

### 2.4 Textures

At each texture offset: `u32 w, u32 h, u8[w*h]` row-major palette indices.
(w/h may be 0 for unused slots.) The engine optionally downsamples by
`_g_modtexmip` (and shifts uv to match). **H**

### 2.5 Sprites (1-corner polys)

Part_DrawSprite_: a camera-facing quad centred on the corner's vertex, half
size (u/2, v/2) world units (the uv entry holds the size, not texels), showing
the whole texture, masked. 44 models have sprites (flames on candles/torches/
sconces, bushes, fire). Bush/tree sprites store height 0; d6model keeps the
texture aspect there (**L**). Flame sprites usually show placeholder textures:
the real ones are animated textures attached at load time (`MDLExtend` chain in
the model-name tables, EFXGFX files, Model_AddAnimTex_), not decoded.

### 2.6 Attachments

`nattach` slots (28 in humanoid/monster files, many empty), each with a per-frame
rotation + translation. A child model (weapon, shield, helm, rider) is drawn
at `v_child @ rot[f] + trans[f]` in the parent's model space
(Attachment_Draw_: child.M = rot . parent.M, child.pos = parent.pos + trans . parent.M;
verified by disassembly of Mat3_MulMat3Copy_/Vec3_MulMat3Copy_). Slots used by
PCMSetCostume_: 8 = helm, 12 = shield, 13 = left-hand weapon (flag 0x20 items,
e.g. bows), 14 = right-hand weapon. **H** (transform) / **M** (slot names)

Helms go through Attachment_DrawModified_ with a D6HELM.DAT record
(`(race_row*16 + helm)*0x26 + 0x108`): f32 offset[3], f32 rotX/rotY/rotZ
(1/1024 turn), f32 scale[3], u16 ?, i16 slot. Child vertex =
`v @ diag(scale) @ Rz @ Ry @ Rx @ rot + offset @ rot + trans`, with
Rx = [[1,0,0],[0,c,s],[0,-s,c]], Rz = [[c,s,0],[-s,c,0],[0,0,1]]. Checked by
render: HM + helm-norman with record 0 sits exactly on the head. **H**

---------------------------------------------------------------------------

## 3. Record -> model mapping

Model names live in three tables compiled into deep6.exe (`mdldata.c`), not in
the `.lst` files (those are alphabetical tool lists). `d6model.model_table()`
reads them from the exe (PE section mapping; DGROUP VA 0x5C0000 -> file 0x1A5400).

| Kind | Record field | Table (VA) | Stride | Count | Path | Conf |
|---|---|---|---|---|---|---|
| Monster | D6MONS +0xB0 (s16 gfx) | `_MonMDLData` 0x5DFFA8 | 0x55: char[0x50] name, u8 ext, f32 Model_Read_ arg | 121 | gfx < 17: `pc/<name>` (HM, HF, EM ... LM); else `monster/<name>` | H |
| Item | D6ITEM +0x114 (s16 model) | `_ItemMDLData` 0x5DB3B8 | 0x51: char[0x50] name, u8 MDLExtend chain | 240 (index must be < 0xF0) | `item/<name>` | H |
| Prop | D6PROP +0x18 (s32 model) | `_PropMDLData` 0x5E27D5 | 0x51: char[0x50] name, u8 MDLExtend chain | 250 (< 0xFA) | `prop/<name>` | H |

Record numbers are 1-based (record 0 of each .DAT is the header). Names are
stored in mixed case (`CHEST.MDL`); open files case-insensitively.
For PCs in robes (PCInRobes_) the engine appends a robe suffix to the race name
(`HMrobe.mdl` etc.); the polymorph/costume paths are not modelled by `model_for`.

Examples: M001 Graveyard Skeleton -> monster/skeleton.mdl, M006 Worgur ->
monster/ratwolf.mdl, M015 Horse -> monster/horse.mdl, P006 Treasure Chest ->
prop/chest.mdl, P013 Barrel -> prop/barrel-1.mdl, P097 Sconce ->
prop/sconce-dungeon.mdl, I010 Short Sword -> item/sword-short-plain.mdl.

Load-time modifications not stored in the file: monster-specific animation
copies (CopyAnimSeq_ in LoadMonsterModel_), texture overlays from D6MONS
(+0xB2.., Model_AddOverlay_), animated textures / particle attachments from the
MDLExtend chain, prop flags2 (Y-lock sprites, perspective, float).

---------------------------------------------------------------------------

## 4. Python API (`formats/d6model.py`)

```python
import d6model as dm
m = dm.load_model(gamedir, 'monster/skeleton.mdl')    # or dm.model_for(gamedir, 'M', 1)
m.version, m.paltype, m.nframes, m.anims, m.default_frame, m.height()
m.palette        # (256,3) uint8 full-bright colours (index 0 -> black)
m.textures       # [Texture]: .w .h .index (h,w) uint8, .rgba (h,w,4) uint8 (index 0 alpha 0)
m.parts          # [Part]: nverts, ngroups, path_len, frame_off, frame_vec, mips, textures, tex_base
mesh = m.mesh(frame=None, mip=0, groups='default')
#   vertices (V,3) f32, tris (T,3) i32, uv (T,3,2) f32 0..1, tex (T,) global texture index,
#   flags (T,) u8 PF_*, part (T,), sprites [dict(vertex, tex, width, height, flags)]
m.part_vertices(part, frame)    # (nverts,3)
m.attachment(slot, frame)       # (rot 3x3, trans 3) -> child v @ rot + trans
dm.model_for(gamedir, 'M'|'I'|'P', recno)   # 'monster/skeleton.mdl' (relative to models/)
dm.render(m, size=512, yaw=35, pitch=25)    # (size,size,3) uint8 preview
```

---------------------------------------------------------------------------

## 5. Not decoded / limitations

* Animated textures and particle emitters attached via MDLExtend / EFXGFX
  are decoded (effects.md) but the editor preview does not animate them
  (flame sprites show placeholder textures).
* Texture overlays (D6MONS +0xB2, Model_AddOverlay_) and helm data selection per item.
* LOD 1..3 of v10 files decode but were only spot-checked.
* `prop/bushgrp*.mdl` have an all-black palette in the file (terrain foliage, not
  in the prop table; probably drawn with another palette).
* Part header words +0x00/+0x04 and the first per-frame vector are not understood
  (not needed for drawing).

## 6. Animation ids and PC .seq files (H unless marked)

The 256-entry `anim` table of a model is indexed by animation id. The engine
asks for ids through `Anim_*` helpers (asets.c, combat.c, monster.c, npc.c,
traps.c); a missing id falls back to 0 (the bit set at model+0x6D4 says which
ids exist). The developers' own id names are in `mdlinfo.prn` in the game
folder. Exported glTF animations are named `anim_<id>`.

### Importing rigged (skinned) glTF

`d6mdlio.import_gltf` bakes skeletal animation into vertex frames, since the
.mdl format only stores frames: when every mesh primitive has a `skin` with
`JOINTS_0` / `WEIGHTS_0`, frame 0 is the rest pose and each glTF animation is
sampled at 16 frames per second (`SKIN_FPS`; LINEAR, STEP and CUBICSPLINE
channels, linear blend skinning with up to 4 weights). The animation name
picks the id: `anim_<id>`, or a name such as `walk`, `run`, `attack`,
`death`, `cast` (`ANIM_NAMES`; Blender's `Armature|Walk.001` works).
Animations without an id are skipped with a warning; the action frame is the
middle frame. Keep clips short: every baked frame stores every vertex.
Tested on a synthetic rig (`tests/test_skin_import.py`) and in the game (a
two-bone column with idle / walk / run in place of the Traveler's
Inquisitor.mdl, attachments copied from it: it renders and bends while
walking). Not yet tried with a Blender export (M).

| id | meaning |
|---|---|
| 0 / 1 / 2 | READY (idle) / READYALT / READYLOOK |
| 5, 6, 7 | walk start / loop / end (`Anim_Walk_` uses 6) |
| 10, 11, 12 | run start / loop / end (11) |
| 15 / 16 / 17 | left turn / right turn / back step |
| 20 / 21 | wound small / large |
| 25 / 26 | death / dead on the ground |
| 28 / 29 | sleep / asleep |
| 30, 31, 32, 33 | fly start / loop / end / flying idle |
| 34-36 | sneak |
| 47 | combat stance (ready) |
| 49 | left-hand swing |
| 50, 51, 52 | sword swing / chop / jab (random of 3) |
| 54 | sword block |
| 56-59 | pole arm attacks (57 chop, 59 jab) |
| 61 / 63 | two-handed chop / backhand |
| 70 / 75 | long bow / crossbow |
| 80 | throw (falls back to 51, then 50) |
| 85 | block |
| 90 / 91 | punch / double punch (unarmed) |
| 93 / 94 | kick / flying kick |
| 100-103 | spell casts A-D (per spell, effects.md `animseq`) |
| 105 / 110 | use item left / use item |
| 115 | gaze |
| 120 | bite |
| 125 / 126 | claw / claw left |
| 130 / 131 | change to mode 2 / back (two-mode monsters, e.g. flying) |
| 135-147 | mode 2 set: ready, block 136, wound 140, death 142, dead 143, attacks 145-147 |
| 150 | breath attack |
| 159 / 160 | land / jump |
| 170 | steal |
| 171, 172, 173 | kneel start / loop / end (disarm traps, use) |
| 181-192 | mounted set: ready, swing, throw, bows, wound, death, jump, spell (190, M), use, misc |
| 193 / 194 | mounted walk / run (M) |
| 195-197 | swim ready / walk / run (M) |
| 200 | victory |
| 210 / 211 | special 1 / 2 |
| 220 / 225 | two- / three-headed breath |
| 230-232 | talk A-C (NPCs, random) |
| 233-235 | stir pot |
| 250 / 251 | entry / exit |

`LoadMonsterModel_` copies or splits sequences for particular monster
graphics at load time (`CopyAnimSeq_`, `SplitAnimSeq_`, `MakeAnimSeq_`, e.g.
graphic 0x45: 33 <- 0, 6/11 <- 31, 50/80/110 <- 100). So an imported model
needs at least 0 (ready), 6 (walk), 20 (wound), 25/26 (death), an attack (50)
and, for spell casters, 100.

**PC `.seq` files** (`models/pc/*.seq`, all 33 identical, reader
`formats/d6seq.py`, 33/33 round trip): ASCII, one animation id per line
followed by `,`, CRLF, ending with an empty line: `0, 47, 50, 91, 110`.
They are build-time keep lists for `pcreduce.exe`, which keeps the frames of
the listed animations (the stored frames of all 33 PC models equal exactly
those ranges) and drops the rest. deep6.exe never opens them; it hard-codes
the same five ids (`SetModelReadDefaults_`, `_gDataSetFlag`). PC models are
only shown on the character sheet: stance 47, fidgets 50 / 91 / 110 at
about 16 frames per second. Equipment is drawn at the attachment points
(8 helm, 12 shield, 13 left hand, 14 right hand); robes are separate
`<race>robe.mdl` models.
