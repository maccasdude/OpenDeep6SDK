# Deep6 content / scripting data formats

Wizards & Warriors (2000, Heuristic Park, engine "Deep6"). This covers the data a
map/content editor needs: per-area scripting tables, object placement, the global
record databases, the event script (EVENTS.DCL/COD), text, archives and models.

Reference implementation: `formats/d6data.py` (parse + byte-identical write,
self test, area dump, event disassembler, archive list/extract).

```
python3 formats/d6data.py --selftest GAMEDIR        # round-trip every file
python3 formats/d6data.py --dump-area 0 GAMEDIR     # readable area dump
python3 formats/d6data.py --disasm GAMEDIR          # all EVENTS.COD routines
python3 formats/d6data.py --list GAMEDIR NPCDATA.PAK
python3 formats/d6data.py --extract GAMEDIR D6ARCHIV.DAT OUTDIR
```

Self test result on the GOG install: 907 files, 0 failures (13 x 6 spoke tables,
17 ENTR files, 59 TOL/BOL/FOL lists, 728 .mdl, 5 .lst, all global tables, both PAKs,
D6ARCHIV, EVENTS.*, D6STRING, TEXTPAK, emitters, 4 .ldl). D6STRING and TEXTPAK also
re-layout identically from parsed strings; D6SMIT00 checksum verifies.

Confidence: **high** = read directly from the loader / consumer code and confirmed in
data; **med** = from usage, plausible in data; **low** = guessed or unused.

All integers little-endian. "Watcom" register calling convention in the decompile.

---------------------------------------------------------------------------

## 1. Global concepts

### 1.1 Spoke number (the `nn` in D6xxxxnn.DAT)

`nn` = `_gSpokeNumber` (0..12), the "spoke" (area) currently loaded. Files are
opened with `sprintf("D6TRIG%02d.DAT", _gSpokeNumber)` in `InitEvents_`
(events.c), `D6LINK%02d` in `LoadSpokeSegment_` (d6spoke.c), `D6TRAP%02d` in
`InitSpokeTraps_` (traps.c). Mapping from `LoadSpokeSegment_` / `FetchObjects_`:

| spoke | type | geometry | object lists (BSP number) |
|---|---|---|---|
| 0 | terrain | SPOKE00.* (Valeia wilderness) | SPOKE00.TOL + terrain BSPs from its B records |
| 1 | dungeon | CRYPTA, CRYPTB | crypta.bol (1), cryptb.bol (2) |
| 2 | dungeon | TEMPLEB, TEMPLEA | templeb.bol (1), templea.bol (2) |
| 3 | terrain | spoke03.* | SPOKE03.TOL |
| 4 | dungeon | SHURU | shuru.bol (1) |
| 5 | dungeon | MINESA..D | minesa..d.bol (1..4) |
| 6 | dungeon | OGREA..C | ogrea..c.bol (1..3) |
| 7 | dungeon | DRAGONA..C | dragona..c.bol (1..3) |
| 8 | dungeon | LICHA, LICHB | (1),(2) |
| 9 | dungeon | SUNKENA, SUNKENB | (1),(2) |
| 10 | dungeon | SHRINEA | (1) |
| 11 | terrain | spoke11.* | SPOKE11.TOL |
| 12 | dungeon | PYRAMA, PYRAMB | (1),(2) |

Dungeon spokes call `Terrain_Null_` and `Terrain_BSPLoad_(name, ...)` with hard-coded
placement arguments (in `LoadSpokeSegment_`), so BSP placement for dungeons lives in
the exe, not in data (decoded: `d6level.SPOKE_TABLE`, `levels.md` section 11).

Default entry points are hard-coded in `GetEntryPosition_` (d6spoke.c), e.g. spoke 0
entry 0 = (217600, 0, 83456) dir 0x100 (just outside the Valeia east gate).
They are only used for town-gate arrivals. Spoke changes, dungeon entrances and exits,
GOTOBSP and ENTERTOWN are covered in `exits.md` (tool: `formats/d6exits.py`).

### 1.2 World state

`_WState[spoke*256 + n]` - 256 byte "world states" per spoke. Triggers watch them,
boundary areas / specials / switches write them, script ops read/write them. Saved
in D6WORLD.DAT / segment files. `!STATE` parameters in the DCL are such indices.

### 1.3 Object ids (`GetObjNum_`, scenload.c) - high confidence

Placed objects are referenced by an int:

* `id < 100000` - 1-based record index in the terrain object list (SPOKEnn.TOL).
* `id = n*100000 + k` - record k (1-based) of BSP n's object list (.BOL). n == 16
  means BSP 0 (since 0*100000 would collide with terrain ids).

Used by switches, traps, specials, bound areas (who=4) and script params such as
`MONA..MOND`, `PROPIDA..D`, `OBJNUM`, `PROP`.

**Door / entity ids** (script `DOOR`, `DOORA/B`, switch with `isentity=1`) are a
different namespace: `bsp*100000 + entity` where the entity is a BSP brush entity
and bsp 0 is literally 0 (`ProcessEventQueue_` -> `MoveEntity_(bspListBSP[id/100000], id%100000)`).

### 1.4 Coordinates

World units, x/z horizontal, y up, terrain tile = 1024 units. Rotations in the object
lists are floats in 1024-units-per-turn. Coordinates in a .BOL, in a bound area with
`bsp>=0` and in script params with `BSPNUM>=0` are relative to the BSP origin
(`bsp + 0x118EC`); `Vec3_AddVec3_` adds it. For terrain BSPs the origin is the TOL
B-record position minus its signed tile offsets x 1024 (bytes +0x21..+0x23, see
`FetchTerrainObjects_`). Script coordinates (`XTILE/YTILE/ZTILE`) are int32 world
units despite the name.

---------------------------------------------------------------------------

## 2. Per-spoke scripting tables

Common container (all five D6xxxxnn files): record 0 is a dummy whose first u32 is
the record count N; record i (1..N) is at file offset `i*recsize` (loaders fseek
there). File size = (N+1)*recsize, except D6LINK04/10 and D6SPEC07 which are just the
4-byte count 0. Bytes 4..recsize of record 0 are editor garbage (stale memory) - keep.
Indices used everywhere (trigger numbers etc.) are these 1-based record numbers.

### 2.1 D6TRIGnn.DAT - triggers, 0xB8 bytes (`LoadTriggerData_`, events.c) max 511

| off | type | name | meaning | conf |
|---|---|---|---|---|
| 0x00 | char[40] | event | event routine name, e.g. `@TEXTMSG`; resolved with `FindEventAddress_` (case-insensitive) against EVENTS.DCL. Bytes after NUL are stale | high |
| 0x28 | u32 | rt_codeptr | overwritten at load with `evdecl[idx]` (stale in file) | high |
| 0x2C | u16 | rt_eventidx | overwritten at load | high |
| 0x2E | i16 | state | world-state index watched by `CheckStates_` | high |
| 0x30 | u8 | enabled | 0/1; 0x3F = per-PC bitmask mode (bound oneshot 2) | high |
| 0x31 | u8 | laststate | last seen value of `state` | med |
| 0x32 | u8 | oneshot | clear `enabled` after firing (2 = per PC) | high |
| 0x33 | u8 | nparams | overwritten from DCL count at load | high |
| 0x34 | u8 | mode | state trigger: 0 fire on any change, 1 when state becomes !=0, 2 when ==0. For bound-area use: 1 = once per PC | high |
| 0x35 | u8[3] | - | padding | low |
| 0x38 | i32[30] | params | event parameters; bytecode operand n reads params[n] | high |
| 0xB0 | i32[2] | rt_queue | runtime scratch for queued ops (zeroed) | med |

A trigger fires: (a) from `CheckStates_` when `state` changes per `mode`, (b) from a
bound area / special / switch (`ExecuteEventCode_(code, actor, trig+0x38)`), (c) from
script `CALLEVENT`. Param meaning comes from the DCL parameter names (2.7).

### 2.2 D6BOUNnn.DAT - boundary areas, 0x28 bytes (`LoadBoundAreaData_`, `CheckBounds_`) max 255

| off | type | name | meaning | conf |
|---|---|---|---|---|
| 0x00 | i16 | state | if trigger<1: WState[state] = (something inside) each frame | high |
| 0x02 | i16 | bsp | BSP the box is relative to; -1 = world. Loader adds BSP origin to min/max | high |
| 0x04 | i16 | trigger | trigger index fired for each actor inside (<1 = state mode) | high |
| 0x06 | u8 | enabled | 1 on; 0x3F = per-PC mask (oneshot 2) | high |
| 0x07 | u8 | pcmask | runtime: PCs already handled | high |
| 0x08 | u8 | oneshot | 0 repeat, 1 disable after hit, 2 once per PC | high |
| 0x09 | u8 | who | 0 PCs, 1 party slots 0-5, 2 creatures not hidden, 3 non-party creatures, 4 one object (objid), 5 all creatures | high |
| 0x0A | u16 | - | unused | low |
| 0x0C | f32[3] | min | AABB min x,y,z | high |
| 0x18 | f32[3] | max | AABB max | high |
| 0x24 | i32 | objid | object for who==4 | high |

### 2.3 D6SPECnn.DAT - specials, 0x1C bytes (`LoadSpecials_`, `CheckSpecials_`) max 127

| off | type | name | meaning | conf |
|---|---|---|---|---|
| 0x00 | i16 | state | set to condition result when trigger<1 | high |
| 0x02 | i16 | ? | always -1 | low |
| 0x04 | i16 | trigger | fired when condition true | high |
| 0x06 | u8 | type | 1 world clock in [a,b) (wraps), 2 all listed objects dead, 3 party has item a | high |
| 0x07 | u8 | enabled | | high |
| 0x08 | u8 | ? | | low |
| 0x09 | u8 | oneshot | disable once true | high |
| 0x0C | i32[4] | args | type 1: start,end clock; type 2: up to 4 object ids; type 3: item id | high |

### 2.4 D6SWITnn.DAT - switches, 0x14 bytes (`LoadSwitches_`, `HitTheSwitch_`, `SetLinkSwitch_`) max 254

| off | type | name | meaning | conf |
|---|---|---|---|---|
| 0x00 | i32 | objid | object id (1.3) or, if isentity, bsp*100000+entity | high |
| 0x04 | i32 | link | next switch in a ring of linked switches (0 none) | high |
| 0x08 | i16 | target | >=0: WState[target] = on; <0: run trigger -target | high |
| 0x0A | i16 | sfx | sound record on toggle (0 = prop's own sound) | high |
| 0x0C | i16 | keyitem | item required (0 none) | high |
| 0x0E | u8 | isentity | 1 BSP entity, 0 placed object | high |
| 0x0F | u8 | flags | 1 animate model, 2 consume key, 4 give key back when switched off, 8 hide polys, 0x10 key latches, 0x20 key needed even when on, 0x40 only via "use item" | med |
| 0x10 | u8 | enabled | usable | high |
| 0x11 | u8 | on | current state (initial value applied to WState at load) | high |
| 0x12 | u8 | oneshot | disable after use | high |
| 0x13 | u8 | unlocked | runtime | med |

### 2.5 D6LINKnn.DAT - nav links, 8 bytes (`LinkNavPoints_`, pathai.c)

Record 0 = u32 count + 4 pad (file is only the 4-byte count when empty).

| off | type | name | meaning | conf |
|---|---|---|---|---|
| 0 | i16 | navA | nav point id on side A | high |
| 2 | i16 | navB | nav point id on side B | high |
| 4 | i16 | bspA | BSP of A, -1 = terrain nav (SPOKEnn.NAV) | high |
| 6 | i16 | bspB | BSP of B, -1 = terrain | high |

Builds terrain<->BSP and BSP<->BSP path-finding portals (`_gBTNavLink`, `_gBBNavLink`).

### 2.6 D6TRAPnn.DAT - locked / trapped containers, 0x24 bytes (`InitSpokeTraps_`, traps.c) max 255

| off | type | name | meaning | conf |
|---|---|---|---|---|
| 0x00 | i32 | objid | chest/crate object id | high |
| 0x04 | u32 | trapmask | bit n => trap type n allowed (1..13, effects in `TrapEffect_`); 0 = untrapped | high |
| 0x08 | i32 | traptype | armed type (picked from mask by `FixTheTrap_`) | high |
| 0x0C | i16 | locked | 0 opened, 1 closed/active, 2 re-armed | high |
| 0x0E | i16 | treasure | D6TREAS record spawned on open (`SetChestTreasure_`) | high |
| 0x10 | i16 | difficulty | lock/trap difficulty for skill checks | med |
| 0x12 | i16 | attempts | runtime | med |
| 0x14 | u8 | power | trap effect level | high |
| 0x15 | u8 | nolock | non-zero: no random success | med |
| 0x16 | u8[6] | - | runtime, zeroed on reset | med |
| 0x1C | u8 | resetpct | chance the trap is removed at reset | high |
| 0x1D | u8 | monpct | chance of a chest monster | high |
| 0x1E | i16 | monrec | chest monster D6MONS record | high |
| 0x20 | i32 | monmode | 1/2 spawn style | med |

### 2.7 ENTRssnn.DAT - named entry points (text) - med

`ss` = spoke, `nn` = index. CRLF lines `x y z<TAB>name` (world units), e.g.
`217600 1024 83456<TAB>Town East Gate` (matches spoke 0 entry 0). Not referenced
by deep6.exe (developer teleport list); useful as editor bookmarks.

---------------------------------------------------------------------------

## 3. Object placement lists (.TOL terrain / .BOL BSP / .FOL foliage)

`LoadTerrainObjects_`, `LoadBspObjects_`, `LoadStaticObjects_` (scenload.c),
`FetchTerrainObjects_` (mapobj.c). 0x40-byte records; record 0 header, u32 at 0 =
record count **including** the header. Record index = object id (1.3).

| off | type | meaning | conf |
|---|---|---|---|
| 0x00 | char | kind: `I` item, `M` monster, `P` prop, `B` BSP placement (TOL), `F` foliage (FOL, random prop 1-4/0xE7 or 0xE4-0xE6), `0` deleted | high |
| 0x01 | char[3] | ASCII decimal record number in D6ITEM / D6MONS / D6PROP | high |
| 0x04 | f32[3] | position (BOL: BSP-relative) | high |
| 0x10 | f32[3] | rotation (1024/turn); B: BSP size | high |
| 0x20 | u8 | M: entry flags (1 ready anim, 2 dead, 4 faded-in, 8 no ground snap, 0x10 hold/ambush until released, 0x20 no hold trigger, 0x40 hold in dungeon); B: BSP number; P (mines): group | high/med |
| 0x21 | i8[3] | B: tile offsets to BSP origin; P/I: [2] (+0x23) start inactive | med |
| 0x30 | char[16] | B: BSP base name (`tcrypt` -> tcrypt.bsp/.bol) | high |

Monsters with flag 0x10 wait for `RELEASEMONGROUP` (or proximity hold radius 4096).

---------------------------------------------------------------------------

## 4. Global databases (record 0 = count header, record i at i*size)

Field-level layouts, enums, limits and how to add records: see `databases.md`
(this section is the short summary).

### 4.1 D6MONS.DAT 0x154 (`MonRec_Load_`, monster.c) - 372 records

| off | type | meaning | conf |
|---|---|---|---|
| 0x00 | char[20] | name | high |
| 0xA2 | i16 | treasure record A (D6TREAS) | high |
| 0xA4 | i16 | treasure record B | high |
| 0xB0 | i16 | graphics/model index for `LoadMonsterGFX_` | high |
| 0x132 | u8 | bit 8: may use hold/ambush | med |
| 0x14E | i16 | D6MONSND record (0x B8 bytes each) | high |
| rest | | stats/resists/attacks - kept raw, not decoded | - |

### 4.2 D6ITEM.DAT 0x11C (`LoadItems_`, deep6.c; count <= 999) - 857 records

name at 0 (high), price i32 at 0x58 (high), flags u8 at 0x98 (0x10 = charges) (med),
model index i16 at 0x114 into the compiled item model table `_ItemMDLData` (stride
0x51, `item/<name>.mdl`) (high). Other fields raw.

### 4.3 D6PROP.DAT 0x38 (`LoadProp_`, `LoadPropObject_`) - 238 records

name char[24] at 0 (high); 0x18 i32 model index into compiled prop table
(`prop/<name>.mdl`, < 250) (high); 0x1C i16 object class (med); 0x20 flags: 1 switch,
2 custom scale (0x24), 4 collide, 8 follow terrain, 0x10 container/chest, 0x20 snap
in BSP, 0x40 animated texture, 0x80 animated (high/med); 0x21 render flags (med);
0x28 f32 (>0 disables ground adjust) (low); 0x30 i16 ambient sound, 0x34 i32 its
parameter (high).

### 4.4 D6NPC.DAT 0x24 (`LoadNPC_`, `LoadNPCNames_`) - 137 records
name char[24] (high) + u32/4xu16 copied into the NPC runtime struct (low).

### 4.5 D6TREAS.DAT 200 bytes (`SetChestTreasure_`, `MonRec_Load_`) - 236 records
10 entries x 20 bytes: `i16 type, i16 chance%, i32 a, i32 b, i32 c, i16 pA, i16 pB`.
type 1/2: item = DiceRoll(a,b,c); type 3: item from treasure lists a/b/c with
chances pA/pB (else c); type 4: gold/exp DiceRoll(a,b,c). (high)

### 4.6 D6TRLIST.DAT 0x3C (`LoadTreasureLists_`) - 62 lists
name char[20]; 10 x (i16 first, i16 last) item ranges (last 0 = single item). (high)

### 4.7 D6HELM.DAT (`InitHelmData_`, `GetHelmData_`)
u32 nModels (24), u32 nHelms (must be 16), i16 modelIdx[128]; then `nModels*16`
records of 0x26 at `0x108 + (model*16+helm)*0x26` (helmet attachment per PC model). Record
contents raw (low).

### 4.8 D6SMITnn.DAT shop inventory (townsmit.c) - high
0x800 bytes = 256 x (u16 item, u16 pad, i32 count), then u32 `CheckSum_` of the
0x800 bytes (algorithm in `d6_checksum`, verified). Same layout for D6MAGE/PAWN/
DOJO/YARD/TMPL/HALL/TVRN nn. Created at runtime from `_gSmitInitInv` if missing -
the copy in the install dir is runtime-generated (Oct 2 timestamp), as are
D6SEG00.GAM and D6ARCHIV.DAT.

### 4.9 emitters.dat (`LoadEmitterDesc_`, particle.c) - med
u32 n (31), n x 0x80 descriptors (no dummy record). name at 0; +0x74 anim/FXC index,
+0x78 alt sprite, +0x7C sprite index (resolved at load); +0x60 frame count. Rest raw.

---------------------------------------------------------------------------

## 5. Event script: EVENTS.DCL + EVENTS.COD

### 5.1 EVENTS.DCL (`LoadEventDCL_`) - high

Game part (55300 bytes):
```
u32  n                     (115)
u32  codeoff[1024]         entry offset in EVENTS.COD
char name[1024][42]        "@OPENWCLOSE" ...
u32  firstparam[1024]      index of first parameter name
u32  nparams[1024]         (byte used -> trigger+0x33)
```
Editor part (ignored by the game, decoded):
```
u32  descoff[1024]; u32 poolsize; char pool[poolsize]   one-line descriptions
u32  nparamnames (0x253); char paramname[2048][42]      "DOOR","SPEED","!STATE",...
```
Parameter names of event i = `paramname[firstparam[i] .. +nparams[i]]`. A leading
`!` marks an index into another table (state, switch, bound, trigger).

### 5.2 EVENTS.COD bytecode (`ExecuteEventCode_`, `ProcessEventQueue_`) - high

Flat code; routine i starts at `codeoff[i]`. Instruction:
```
u16 opcode   bit15 = "queued" op (runs over time in ProcessEventQueue_)
i16 n        operand count
i16 arg[n]   each = index into the trigger's params[] (value = params[arg])
u32 target   only for jumps and queued ops (absolute COD offset;
             queued ops resume at target when done, see QEventDone_)
```
Every routine decodes cleanly to the next routine (verified). Opcodes (n = observed
operand count):

| op | mnemonic | n | semantics |
|---|---|---|---|
| 01/02 | END / END2 | 0 | stop |
| 03 | GOTO | 0 | jump |
| 04 | IFSTATE | 1 | jump if WState[a0] |
| 05 Q | OPENDOOR | 4 | door entity,speed,navpnt(unblocked),sfx |
| 06 Q | CLOSEDOOR | 4 | same |
| 07 Q | DELAY | 1 | ms |
| 08 | SETSTATE | 2 | WState[a0]=a1 |
| 09 | TOGGLESTATE | 1 | |
| 0A/0B | SETSWITCH/TOGGLESWITCH | 2/1 | via linked switch ring |
| 0C/0D | ENABLE/DISABLETRIG | 1 | |
| 0E/0F | ENABLE/DISABLEBOUND | 1 | |
| 10/11 | ENABLE/DISABLESWITCH | 1 | |
| 12 | CREATEITEM | 5 | item,x,y,z,bsp |
| 13-16,18,1A,1D,1F,20 | no-op | 0 | |
| 17 | PCBLESSING | 3 | type,value,questflag (shrine/pool rewards, abilities, cures) |
| 19 | SETWEATHER | 1 | |
| 1B | GENMONSTER | 7 | monrec,x,y,z,bsp,init,maxcount (encounter group) |
| 1C | TEXTMSG | 2 | TEXTPAK id, time |
| 1E | IFHASITEM | 1 | jump |
| 21/22 | GOTOBSP/GOTOBSPTERR | 3 | bsp,destnav,lastnav |
| 23/24 Q | OPENDBL/CLOSEDBL | 5 | doorA,doorB,speed,nav,sfx |
| 25 | RELEASEMONS | 4 | object ids leave hold mode |
| 26 | EFXATOBJ | 5 | efx,sfx,spell,obj,yoff |
| 27/28 | MAGICPOOL/MAGICFIRE | 1 | |
| 29 | TWIDDLEPROP | 3 | obj,type,value |
| 2A | LOADSEGMENT | 5 | seg,bsp,switch,status,bound |
| 2B | IFALLINBOUND | 1 | jump |
| 2C | IFUSINGITEM | 1 | jump |
| 2D | DAMAGEPC | 2 | dmg,poison |
| 2E | SHOWTEXT | 1 | |
| 2F Q | MOVEENTITY | 7 | |
| 30/31 | ZEROUSEITEM/USEITEMCHARGE | 1 | |
| 32/33 | IFNOTOCCUPIED/IFOCCUPIEDBY | 1/2 | WOccupied slots, jump |
| 34/48 | PLACEOCCUPY(Y) | 8/9 | item,x,y,z,bsp,slot,rot,flag(,yoff) |
| 35 | CLEAROCCUPIED | 1 | |
| 36/4B | EFXFROMTO | 9 | |
| 37 | CALLEVENT | 1 | run trigger |
| 38 | SETNOTSTATE | 2 | |
| 39/45 | MOVETONAV(SPD) | 3 | |
| 3A | COMBO4 | 7 | 4 states as decimal digits == value -> set state |
| 3B | TELEPORT | 4 | x,y,z,bsp |
| 3C | IFOBJINBOUND | 2 | jump |
| 3D | ROTATETONAV | 2 | |
| 3E/3F | SWITCHFROMSTATE/STATEFROMSWITCH | 2 | |
| 40 | ANIMOBJ | 2 | |
| 41 | SWAPOBJPOS | 2 | |
| 42 | EFXATPC | 5 | |
| 43 | IFSWITCHON | 1 | jump |
| 44 | PLAYSFX | 1 | |
| 46 | HIDEPOLYS | 3 | |
| 47 | CHANGEITEMS | 2 | |
| 49 | TELEPORTOBJ | 5 | |
| 4A | LADDER | 4 | |
| 4C | SPAWNMONSTER | 8 | monrec,x,y,z,bsp,dir,radius,init |
| 4D | SETNAVFLAG | 3 | |
| 4E | EFXPOINTS | 10 | |
| 4F | ENTERTOWN | 1 | 0..2 |
| 50 | ITEMTOITEM | 2 | |
| 51 | IFEQUIPPED | 1 | jump |
| 52 | MOVEPROP | 5 | |
| 53 | SETQFLAGIF | 3 | |
| 54 | ENDGAME | 1 | movie |
| 55 | IFMONALIVE | 1 | jump |
| 56 | PROPWALK | 6 | |

Disassembler: `--disasm` (uses DCL param names as operand names). Example:
```
@LOPENWCLOSEW  params DOOR, SPEED, DELAY, !STATE, NAVPNT, SFXREC
  002e: Q:OPENDOOR DOOR, SPEED, NAVPNT, SFXREC -> 003e
  003e: Q:DELAY DELAY -> 0048
  0048: Q:CLOSEDOOR DOOR, SPEED, NAVPNT, SFXREC -> 0058
  0058: IFSTATE !STATE -> 0066
  0062: END2
  0066: Q:DELAY DELAY -> 0070
  0070: GOTO -> 002e
```
New script routines can be added by appending code and a DCL entry (max 1024); an
assembler is straightforward from this table.

---------------------------------------------------------------------------

### 5.3 Assembler

`formats/d6events.py` disassembles every routine to re-assemblable text
(labels instead of offsets, operands as parameter names) and assembles it
back byte for byte (`--selftest`). `EventSet.set_event()` adds or replaces an
event: the code is appended to EVENTS.COD, parameter names to the end of the
name table, and the DCL editor part (descriptions, parameter names) is
rewritten.

## 6. Text

* **D6STRING.DAT** (`LoadStrings_`, `StringAddress_`, d6string.c) - high. u32 n;
  n x (u32 id, u32 file offset) sorted by id (binary searched); NUL strings follow.
* **TEXTPAK.000** (`OpenTextMessages_`, textmsg.c) - high. u32 n; n x (i32 id,
  u32 offset, u32 size); text blobs, `@` = paragraph break. `TEXTMSG` param `MSG`.
* **NPCDATA.PAK / GMDATA.PAK** (`LoadNpcPakHead_`, `ReadNpcPakFile_`,
  `LoadGmPakHead_`) - high for container. Header = (u32 offset, u32 size) slots;
  header length = first offset. NPCDATA: 481 slots = lexicon, 160 x code, 160 x
  strings, 160 x keywords (indexed by NPC id); GMDATA: 97 slots = lexicon,
  32 x code, 32 x strings, 32 x a response table the game never uses. The
  bytecode (two VMs, npc.c and guild.c) is decoded in `npc.md`
  (`formats/d6npc.py`).
* **\*.ldl** (language.ldl, string.ldl, string_e.ldl, default.ldl) - low. Encrypted
  with a fixed per-position XOR keystream (same key for all files; plaintext is a
  `#`-commented "global_strings { ... }" text). Not referenced by deep6.exe
  (launcher data). Key not recovered (common LCGs ruled out); passthrough only.

## 7. D6ARCHIV.DAT (save archive, `SaveArchive_`/`RestoreArchive_`, segwrite.c) - high

Header 0x2B4 = 173 u32: [0] 0x140 version, [1] warship flag, [2] GM unique bits,
[3] fog/marker blob offset, [4] ROSTER offset, [5] ROSTER size, [6] D6WORLD offset,
[7] D6WORLD size, [8..55] D6SEGnn.GAM present/offset/size x16, [56..100]
JOURNAL.nnn x15, [101..172] 24 shop files (D6SMIT/MAGE/PAWN/DOJO/YARD/TMPL/HALL/
TVRN 00..02). `--list` / `--extract` supported. This is save state, not content.

## 8. Models

* **models/\*.lst** - first line count, then `$M`/`$I`/`$P` + file name. The game
  itself uses compiled tables (`_ItemMDLData`, prop table `_PropMDLData` at 0x5E27D5) and only
  chardemo reads model.lst. (high for format, med for role)
* **\*.mdl** (`Model_Read_`, model.c) - structure walker, high:
```
" LDM" magic; u32 version (9|10, bit24 = has frame bitmap); u32 parttable offset;
u8 paltype: 0/3 -> 32x256 u16 shade palette (16384 B), 1 -> 768 B RGB, 2 -> char[64]
   shared palette name (models/refpal/);
u16 nframes; [u8 framebits[nframes]]; anim table 256 x (u16 start,u16 end,u16 ?);
u8 nattach; per attach: u16 nf, nf x 12 (translation), nf x 36 (3x3 matrix);
u8 nparts; parttable: u32 offset per part. Part:
  u32 ?, u32 sprite_section_off, u32 ?, u32 flags, u32 nverts;
  mip levels (1 for v9, 4 for v10): u32 nuv, nuv x 6; u32 npoly, npoly x 4, npoly x 1;
  u8 ?, u16 nframes, u32 npaths, u16 pathlen[npaths], u32 framesize;
  per frame: [framesize bytes: per path 6 + (len-1)*3 packed verts] (omitted when
  framebit==0), then 24 bytes bbox;
  u8 ntex, u32 texoffset[ntex] -> each: u32 w, u32 h, w*h 8-bit texels;
  at sprite_section_off: u8 n, n x u32.
```
  728/728 files walk with full coverage; PC models and a few others carry
  unreferenced tail data (kept). Vertex packing, UVs, animation and the writer
  are in `models.md` (`formats/d6model.py`, `formats/d6mdlio.py`).

---------------------------------------------------------------------------

## 9. Sanity check: spoke 00 (Valeia wilderness), `/tmp/data/area00.txt`

* 238 TOL records: 14 terrain BSPs (tcrypt, torcave, ttemple, ttmpcave, ttoada/b,
  tgatea/b/c = Valeia gates, TRUINSA..F ruins), 152 props, 70 monsters.
* Signposts (P030) at (218568, 89020) - 5.6k units from the default entry
  (217600, 83456) - plus four more along the roads.
* Trolls: Troll Guni/Mangu groups (#104-111, #144-148) held in ambush (flag 0x01/
  0x10) and released by triggers T56/T57/T72 via bound areas B44/B45/B57 (state 33);
  T51 `@MAKEMONSTER Troll Guni x5` at (241963, 103623) fires from bound B42 just
  east of the gate road. Nearest placed monsters to the entry are Worgur packs
  released by T48/T49 (state 32, bound B38).
* B49/B50/B51 (PCs near tgatea/tgateb/tgatec) set states 34/35/36; switches W5/W7/W8
  on the gate entities call T34/T38/T39 -> `@ENTERTHETOWN` (T63..T65).
* Graveyard by the crypt (tcrypt): skeletons with hold flag, crypt doors T1 (state 1,
  lever W6 "Graveyard Guardian"), crypt text T2/T4 from bound B1/B3 (BSP-relative
  boxes land at x 265728.., z 148736.., next to tcrypt).
* 12 treasure chests with D6TRAP entries (treasure records 83..96).

## 10. Open items listed when this document was started, and where they went

1. Terrain heightfield, BSP geometry, nav points, lights: `terrain.md`,
   `levels.md`.
2. Dungeon BSP placement (hard-coded in `LoadSpokeSegment_`): decoded,
   `d6level.SPOKE_TABLE` (`levels.md` section 11).
3. Monster/item/prop records: field by field in `databases.md`.
4. NPC/GM dialogue bytecode: `npc.md`, `formats/d6npc.py`.
5. .ldl key: still not recovered; the game does not read these files.
6. MDL vertex packing, UVs, animations, writer: `models.md`.
7. D6SEGnn.GAM supersedes the placement files once a spoke has been visited;
   editors must move it away (d6edit offers this on save).
8. Model tables compiled into deep6.exe: `databases.md` section 9 and
   `models.md` section 3; d6edit can point a slot at a new file.
