# Deep6 area transitions ("exits")

Wizards & Warriors (2000, deep6.exe). This document covers how the party moves
between areas: town gates, spoke-to-spoke travel (dungeon entrances and exits),
BSP-to-BSP seams inside a spoke, teleports, and where the party appears on
arrival. Reference implementation: `formats/d6exits.py`.

```
python3 formats/d6exits.py GAMEDIR [spoke]            # readable, with source trees
python3 formats/d6exits.py --json GAMEDIR [spoke]     # everything as JSON
python3 formats/d6exits.py --markdown GAMEDIR         # the tables in section 7
```

Confidence: **H** = read from the code (decompile and disassembly) and confirmed in
the data; **M** = from code, plausible in the data; **L** = guessed.

Related: `data.md` (trigger, bound and switch tables, event bytecode), `levels.md`
(BSP placement and nav points), `terrain.md`.

---------------------------------------------------------------------------

## 1. Overview

There are only **two** code paths that change `_gSpokeNumber` (H; every write to
the global was checked in the disassembly):

1. `SetSpokeEntry_` (deep6.c): the party leaves a **town hub** through a gate. The
   arrival point comes from the hard-coded `GetEntryPosition_` table (section 5).
2. `NewSpokeSegment_` (d6spoke.c): the script op **LOADSEGMENT** (0x2A). The party
   keeps its position *relative to the BSP it stands in*. That position is then
   re-based onto a BSP of the new spoke (section 3).

(`InitStuff_` also sets it to 0 at start-up, and `RestoreCurrentGame_` reads it
from a save. In multiplayer, `ProcMsgSEGLOAD_` copies the host's `_gSegLoadSeg`.)

Everything else stays inside one spoke, or returns to a town:

| mechanism | op / code | scope | arrival |
|---|---|---|---|
| town gate | townhub GATE hot spot -> `SetSpokeEntry_` | town -> spoke 0, 3 or 11 | `GetEntryPosition_` table |
| LOADSEGMENT | 0x2A | spoke -> spoke | party's BSP-local position re-based on the destination BSP |
| GOTOBSP / GOTOBSPTERR | 0x21 / 0x22 | BSP -> BSP in the same spoke | the actor's BSP-local position re-based, then a free-space search |
| TELEPORT | 0x3B | inside the actor's current BSP (or on the terrain) | given x,y,z (BSP-relative), then a free-space search |
| ENTERTOWN | 0x4F | spoke -> town hub | town hub screen |
| ENDGAME | 0x54 | spoke -> end movie, then town 2 | - |
| recall spell (SpellCollide_ effect 0x31) | combat.c | terrain spokes 0/3/11 -> the PC's home town | town hub |
| portal spells (effects 0x32/0x33) | combat.c | same spoke (`_gPortalBase[spoke]`) | the portal point |
| party wipe | `CheckPartyWipe_` -> game state 0x12 | spoke -> town | town hub |

The game always starts in a town (`PlayDeep6_` -> `InitTown_`), so a spoke is
always entered through a town gate or a LOADSEGMENT.

---------------------------------------------------------------------------

## 2. Id spaces

| name in the DCL | meaning | space |
|---|---|---|
| `SEGMENT` | destination spoke | 0..12 (`_gSpokeNumber`; file suffix `nn`) |
| `BSPNUM` | BSP **slot** in `_bspListBSP[]` | dungeon spokes: 1..n in `LoadSpokeSegment_` order (0 = null BSP). Terrain spokes: TOL 'B' record slot byte (0-based). See `d6level.spoke_bsps()`. -1 = terrain/world |
| `DESTNAV`, `LASTNAV`, `NAVPNT` | nav point **id** (`.nvs` u16 id, < 20000) of the BSP named by BSPNUM | `GetNavPntIdx_(bsp, id)` = `bsp+0x7c9c` u16 table, id -> index; an id >= 20000 is fatal (`D6Err_`) |
| `!BOUNDNUM` | D6BOUNnn record (1-based) | per spoke |
| `!SWITCH`, `SEGSWITCH` | D6SWITnn record (1-based) | SEGSWITCH is in the **destination** spoke's table |
| `STATUS`, `SEGSTATUS` | switch value 0/1 | `SetLinkSwitch_` (whole linked ring) |
| `!STATE` | WState index | `_WState[spoke*256 + n]` |
| `DOOR*` | brush entity id | `bsp*100000 + d6entid` (bsp 0 = slot 0) |
| `TOWN` | town hub | 0 Valeia, 1 Ishad N'ha, 2 Brimloch Roon (D6STRING 0x21fc..0x21fe) |
| `XTILE/YTILE/ZTILE` | position | int32 world units relative to BSPNUM's origin (absolute if BSPNUM = -1) |

BSP origin = `bsp+0x118ec` (world units). For a dungeon BSP it is
`((bx-offx)*1024, -offy*1024, (by-offz)*1024)` from the `Terrain_BSPLoad_`
arguments. For a terrain BSP it comes from the TOL 'B' record. `d6level.spoke_bsps()`
returns it as `origin`. BSP file coordinates are world/16 relative to the origin.

---------------------------------------------------------------------------

## 3. LOADSEGMENT (0x2A): spoke to spoke (H)

### 3.1 The op

`LOADSEGMENT SEGMENT, BSPNUM, SEGSWITCH, SEGSTATUS, !BOUNDNUM` (`ExecuteEventCode_`
case 0x29) only stores its five operands:

```
_gSegLoadSeg    = SEGMENT      (spoke)
_gSegLoadBsp    = BSPNUM       (BSP slot in the new spoke, -1 = none)
_gSegLoadSwitch = SEGSWITCH    (switch of the new spoke, -1 = none)
_gSegLoadStatus = SEGSTATUS
_gSegLoadBounds = !BOUNDNUM    (stored, never read again - unused)
```

`InitSpoke_` resets them to `(current spoke, 0, -1, 0, 0)`. The spoke loop
(`Deep6Spoke_`, `CheckTerminateGameLoop_`) sees `_gSegLoadSeg != _gSpokeNumber` and
calls `NewSpokeSegment_`:

1. For each of the 6 party creature slots: dismount, unlink from the BSP or terrain,
   and `slot = InBSPArea_(pos)`. If it is in a BSP: `pos -= origin(slot)`, and if
   the **old** spoke is 8 also `pos.y -= 256` (only for PCs in a BSP).
2. Save `D6SEGnn.GAM` for the old spoke plus world data, `UnloadSpokeSegment_`,
   `_gSpokeNumber = _gSegLoadSeg`, `LoadSpokeSegment_` (this loads the new spoke's
   D6SEG save if one exists, otherwise its pristine tables).
3. If `_gSegLoadBsp != -1`: `pos += origin(_gSegLoadBsp)` (if that slot is loaded)
   and, if the **new** spoke is 8, `pos.y += 256` (even when the slot is not
   loaded or the PC came from the terrain). Single player / host only. The
   position is then used as it is, with **no** free-space search.
4. `SetLinkSwitch_(-1, SEGSWITCH, SEGSTATUS)` if SEGSWITCH != -1.
   `SetLinkStateOpposed_` makes the watched state triggers fire on the next frame,
   so the arrival door opens and the arrival lever shows the right state.
5. `PCEntry_QuestInit_`. `GetEntryPosition_` is **not** used.

So the arrival position in the new spoke is

```
arrival = (pos - origin_old[InBSPArea(pos)]) (- 256y if old==8) (+ 256y if new==8) + origin_new[BSPNUM]
```

A PC that stands on the terrain (InBSPArea = -1) keeps its world coordinates and
still gets `+ origin_new[BSPNUM]`. With BSPNUM = -1 nothing is added.

Checked in the game: spoke 8 exit T3 (lever W1, party in box B2) to spoke 11
BSP 3 moved the PC by exactly (157696, 13056, -34816) =
origin 11/3 - origin 8/1 - (0, 256, 0).

The shipped game builds every spoke entrance as a pair of BSPs that share
geometry in their **local** frames. The small overworld piece (tcrypt, tshurua..,
tdragona, togrea, tmines, tlich, tsunkena, tshrinea, tpyram, ttmpcave, ttemple)
and the matching vestibule of the dungeon BSP (CRYPTA, SHURU, ...) are the same
room. The "exit box" (!BOUNDNUM) has identical local coordinates in both spokes.
For example spoke 0 B4 (bsp 0 tcrypt) and spoke 1 B1 (bsp 1 CRYPTA) are both
local `(-47360,1024,-51200)..(-37888,5120,-44032)`. tcrypt and CRYPTA even share
the same world origin (327680, 0, 197632).

### 3.2 The events that use it

| event | params | logic |
|---|---|---|
| `@SWITCHDDTOSEGMENT` | !STATE, DOORA, DOORB, SPEED, NAVPNT, !BOUNDNUM, !SWITCH, STATUS, SEGMENT, BSPNUM, SEGSWITCH, SEGSTATUS, DELAY, SFXREC | state==0: open both doors. state!=0: close the doors (queued), then IFALLINBOUND !BOUNDNUM ? (DELAY ms, LOADSEGMENT) : SETSWITCH !SWITCH:=STATUS (lever back) |
| `@SWITCHDOORTOSEGMENT` | same with one DOOR | same |
| `@OFFSWITCHDDTOSEGMENT` | as DD | inverted: state!=0 opens; state==0 closes and tests the bound |
| `@OFFSWITCHDOORTOSEGMENT` | as DOOR | inverted |

The trigger has `state = !STATE` and runs from `CheckStates_` (actor -1) when that
WState changes. The WState is driven by the lever switch (`D6SWIT.target = !STATE`).
Some levers are also flipped by a script, e.g. spoke 0 T7 `@SETASWITCH` from box B5.
`IFALLINBOUND` (op 0x2B) requires **every** existing creature in slots 0..5 to be
inside the bound's world AABB. The bound records used for this are disabled
(`enabled=0, trigger=-1`) and serve only as volumes.

Outward exits (overworld -> dungeon) use the plain variants with SEGSTATUS=1. The
lever in the dungeon is set "on" on arrival, which opens the vestibule doors. The
way back uses the OFF variants with SEGSTATUS=0.

Data check (all 13 spokes): 34 LOADSEGMENT triggers (17 pairs). Every arrival point falls
inside the !BOUNDNUM box of a LOADSEGMENT trigger in the destination spoke that
leads back (`d6exits.find_return`). For example spoke 0 T8 (crypt gate, lever W1)
-> spoke 1 bsp 1 CRYPTA, and back through spoke 1 T1 (lever W21) -> spoke 0 bsp 0
tcrypt.

---------------------------------------------------------------------------

## 4. GOTOBSP / GOTOBSPTERR (0x21 / 0x22): BSP to BSP in one spoke (H)

Both opcodes share one handler (`@GOTOBSPONLY` "Teleport To BSPOnly",
`@GOTOBSPTERR` "Teleport To TerrainBSP"; only GOTOBSPONLY is used in the data).
Operands are `BSPNUM, DESTNAV, LASTNAV`:

```
bsp  = BSPNUM (slot; D6Err "XLATETOBSP BSP IS NULL" if not loaded)
dnav = DESTNAV ? GetNavPntIdx_(bspList[bsp], DESTNAV) : 0
lnav = LASTNAV ? GetNavPntIdx_(bspList[bsp], LASTNAV) : 0
TranslateToBSP_(actor, bsp, dnav, lnav)
```

`TranslateToBSP_` does nothing if the actor is < 0 (so a state trigger cannot use
it), is not alive, is mounted, is not in a BSP (`InBSPArea_ == -1`) or is already
in `bsp`. Otherwise:

* monster+0xB0 = dnav (path target index), +0xB2 = lnav or the old +0xB8, and the
  path state +0xA8..+0xBA is cleared. Both nav ids index the **destination**
  BSP's nav table. In the data, DESTNAV is 0.5-3k units from the arrival point
  and LASTNAV is its neighbour.
* `pos = pos - origin[current] + origin[bsp]`, i.e. the same BSP-local
  position in the other BSP.
* a free-space search (`FindValidBspSpace_`, radius 16 growing to 1024) moves the
  actor slightly if needed.
* relink, recompute collision, reset the sky if it is the camera actor, and send
  `NetMsg_MONXLATE_`.

The source is always a bound box with `who=5` (all creatures), `trigger` = the
GOTOBSP trigger and `bsp` = the source BSP slot. The box sits in the overlapping
seam of two BSPs that contain the same corridor in their local frames, and each
seam has one box per direction. Monsters cross the seams as well. The path AI
uses `D6LINKnn` for the nav graph links across the seam.

Data check: 45 GOTOBSP triggers. The re-based arrival point is near DESTNAV in
every case except spoke 6 T18: DESTNAV 892 does not exist in OGREB.nvs, so the
index lookup returns whatever the id table holds (probably 0, no path target).

---------------------------------------------------------------------------

## 5. Town gates and the entry table (H)

### 5.1 Path

Town hub (`Deep6Town_`, townhub.c): the AVI mask fields of type `'GATE'` (value
0/1) set `_gSpokeEntryNum = gate` and `_gGameState = 4`. Brimloch Roon gate 1
also needs `_gPartyWarshipFlag`. `PlayDeep6_` state 4 -> `SetSpokeEntry_`:

| town | gate | label (D6STRING 0x2260+) | spoke | entry |
|---|---|---|---|---|
| 0 Valeia | 0 | The Old Road To The Graveyard Ruins Of Bersault | 0 | 0 |
| 0 Valeia | 1 | The Forest Trail To Nymph Lake | 0 | 1 |
| 1 Ishad N'ha | 0 | The Marsh Trail To The Temple Of Ishad N'ha | 0 | 2 |
| 1 Ishad N'ha | 1 | The Forest Road To The Knight's Castle At Shurugeon Ruins | 3 | 1 |
| 2 Brimloch Roon | 0 | The Mountain Pass To The Dragon's Spire | 3 | 0 |
| 2 Brimloch Roon | 1 | The Wharf On The Enchanted Sea (warship) | 11 | 1 (ignored) |

`Deep6Spoke_` -> `InitSpoke_` -> ... -> `AssignPartyCamera_(localPlayer)` ->
`GetEntryPosition_`.

### 5.2 `GetEntryPosition_` (d6spoke.c 0x489d20) = `d6terrain.ENTRY_POINTS`

This is a hard-coded `switch (_gSpokeNumber)` that writes `_Entry_X/_Y/_Z/_Dir`
(int, world units). Spokes 0 and 3 also test `_gSpokeEntryNum`. The other spokes
have a single entry. Spoke 0 entry > 2 and spoke 3 entry > 1 leave the globals
unchanged (stale values). The dungeon entries (spokes 1, 2, 4-10, 12) cannot be
reached in the retail flow, because no gate leads there. They are probably
leftovers from development or testing.

| spoke | entry | x | y | z | dir |
|---|---|---|---|---|---|
| 0 | 0 | 217600 | 0 | 83456 | 0x100 |
| 0 | 1 | 185856 | 0 | 74240 | 0x300 |
| 0 | 2 | 10752 | 7168 | 281600 | 0x100 |
| 1 | - | 290304 | 2080 | 152320 | 0x100 |
| 2 | - | 345600 | 5160 | 307712 | 0x300 |
| 3 | 0 | 7954 | 3072 | 284160 | 0x100 |
| 3 | 1 | 284160 | 1090 | 10752 | 0x300 |
| 4 | - | 113152 | 1024 | 110592 | 0 |
| 5 | - | 131072 | -2816 | 128000 | 0x100 |
| 6 | - | 215808 | -15104 | 146176 | 0 |
| 7 | - | 180736 | -2048 | 117248 | 0 |
| 8 | - | 109568 | 1024 | 154112 | 0 |
| 9 | - | 211456 | -2304 | 147456 | 0 |
| 10 | - | 224256 | -5120 | 209920 | 0x200 |
| 11 | - | 382976 | 1024 | 25600 | 0x300 |
| 12 | - | 170496 | -5376 | 139776 | 0 |

`dir` is a yaw in 1024 units per turn. It becomes the PCs' facing (monster+0x20).
Placement (`AssignPartyCamera_`, R = 1280.0 at 0x5c5a3a,
`_partyDir = {0,170,-170,340,-340,512}`):

```
C = E + R*(sin,cos)[(dir+0x200)&0x3ff]           (R behind the entry point)
r = R                                            (set once, before the PC loop)
for each PC: first k in 0..5 with C + r*(sin,cos)[(dir - partyDir[k])&0x3ff] free
             (ObjectSpaceOK_); none free: r += 1024 and retry, up to r = 9472
```

`_gSin`/`_gCos` (0x616860 / 0x615860) are filled by `BuildTrigTables_` with
`sin/cos(i * 6.28 / 1024)` (6.28, not 2 pi; x uses sin, z cos). So PC 0 stands
about 2 units from the entry point and the others on an arc behind it. Checked
against a save made after leaving Valeia: the PC stood at (217600, 83453.96),
exactly what 6.28 gives (2 pi would give 83456.0). A PC whose spot is blocked
takes the next free direction, and the radius is not reset for later PCs.
y is snapped with `D6_AdjustBase_` ("Unable To Assign New PC" when no ring
fits). Each town gate arrival is
next to the gate door switch that leads back to that town (e.g. spoke 0 entry 0
(217600, 83456) and W5 tgatea door (209648, 82944)).

### 5.3 ENTR files

`ENTRssnn.DAT` (text `x y z<TAB>name`) are **not** read by deep6.exe, and there is
no "ENTR" string in the exe. They are developer bookmarks (e.g. "Crypt Entry",
"Wharf Start Point"). The first line of ENTR0000 matches entry (0,0).

### 5.4 Leaving to a town: ENTERTOWN (0x4F) (H)

`ENTERTOWN TOWN`: if 0 <= TOWN <= 2, set `_gTownNumber = TOWN` and
`_gGameState = 0xe`. The spoke loop ends, `PlayDeep6_` calls `InitTown_(_gTownNumber)`,
and the D6SEG of the spoke is saved by the normal shutdown. The shipped
pattern is a gate door brush switch (`isentity=1`, target -> trigger
`@IFSTATECALLEVENT`) plus a "party near the gate" bound (`who=0`, trigger -1,
sets a state). The IFSTATECALLEVENT checks that state and then CALLEVENTs
`@ENTERTHETOWN`. There are 6 town exits: spoke 0 x3, spoke 3 x2, spoke 11 x1.

`ENDGAME` (0x54) sets `_gEventMovieFlag = movie+1`, `_gTownNumber = 2` and
`_gGameState = 0xe`.

---------------------------------------------------------------------------

## 6. Other movers

* **TELEPORT** (0x3B, `@EXECTELEPORT`, `@IFSTATETELEPORT`, `@IFSTATEPAINTP`)
  `x,y,z,bsp`: `p = (x,y,z) + origin[bsp]` (if bsp != -1), then `Teleport_(actor,
  bsp, p)`. This only works if the actor is alive, not mounted and
  `InBSPArea_(actor) == bsp`, so it is same-BSP only. y gets the model's height
  offset, a free-space search runs (`FindValidOBJSpace_`), and y is never lower
  than the given y (`D6_AdjustBase_`). It never changes the spoke. Used for trap
  pits and puzzle resets (spoke 2 T155-160, spoke 6 T63-73, spoke 10 T144, spoke 12 T89).
* **TELEPORTOBJ** (0x49, `@TELEPORTMONSTER`): moves a placed monster. It is not a
  party exit.
* **Portal spells** (combat.c `SpellCollide_`): effect 0x32 stores the caster's
  position in `_gPortalBase[spoke]` and sets `_gPortalFlag[spoke]` (not allowed
  in spoke 12). Effect 0x33 (`TeleportToPortal_`) moves the group there, in the
  **same** spoke. Effect 0x31 (recall, terrain spokes only) stores the PC's
  position in `_gPortalHome[pc]` and sets `_gPortalHRet[pc]=1`, then
  `_gTownNumber = pc home town` and state 0xe. `_gPortalHome/HRet` are only
  saved and loaded (segwrite.c) and are never read for placement. The return trip
  uses the normal town gate entry. All four arrays live in the save
  (D6WORLD/D6ARCHIV) and are cleared at new game.
* **Party wipe**: `CheckPartyWipe_` -> after `_gGraveTime` (5 s) game state 0x12 ->
  town.
* **D6LINKnn**: nav links only (path AI across BSP seams and terrain/BSP joins).
  They never move the party.

---------------------------------------------------------------------------

## 7. All exits in the shipped game

Generated with `d6exits.py --markdown` (GOG data). Sources: `W` switch, `B` bound,
`S` special; "via Tn" is the intermediate trigger (CALLEVENT or SETSWITCH chain).
"box / switch" is the world AABB the party must be in: the IFALLINBOUND box for
segments, the trigger box for GOTOBSP, and the "near the gate" box for towns.
"arrival" is the box centre after re-basing, in world units of the destination
spoke. `W21:=1` means switch 21 of the destination spoke is set to 1 on arrival.

#### Spoke 0 - Valeia wilderness (terrain SPOKE00)

| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |
|---|---|---|---|---|---|---|---|
| T8 | @SWITCHDDTOSEGMENT | segment | switch W1 B4 via T7 | (280320, 1024, 146432)..(289792, 5120, 153600) | 0 | spoke 1 bsp 1 CRYPTA, W21:=1 (back: T1) | (285056, 3072, 150016) |
| T12 | @SWITCHDDTOSEGMENT | segment | switch W2 B7 via T10 | (3328, -2, 219648)..(10496, 3072, 226304) | 2 | spoke 2 bsp 1 TEMPLEB, W1:=1 (back: T1) | (117504, 1535, 325376) |
| T15 | @SWITCHDDTOSEGMENT | segment | switch W3 B9 | (43520, 1260, 216064)..(54272, 7954, 221184) | 3 | spoke 2 bsp 2 TEMPLEA, W2:=1 (back: T2) | (355072, 4607, 307712) |
| T20 | @GOTOBSPONLY | bsp | box B19 | (247072, -5472, 266448)..(249776, -2784, 267568) | 4 | bsp 5 ttoadb nav 137/120 | (231016, -4128, 267008) |
| T21 | @GOTOBSPONLY | bsp | box B20 | (227696, -5472, 263120)..(230880, -2784, 264944) | 5 | bsp 4 ttoada nav 118/119 | (246696, -4128, 264032) |
| T63 | @ENTERTHETOWN | town | switch W5 via T34 | (206848, -20480, 77824)..(215040, 20480, 87040) | -1 | town 0 Valeia | - |
| T64 | @ENTERTHETOWN | town | switch W7 via T38 | (187392, -20480, 70656)..(194560, 20480, 77824) | -1 | town 0 Valeia | - |
| T65 | @ENTERTHETOWN | town | switch W8 via T39 | (4096, -20480, 278528)..(10240, 20480, 284672) | -1 | town 1 Ishad N'ha | - |

Town gate arrivals: Valeia gate 0 -> entry 0 (217600, 0, 83456) dir 0x100; Valeia gate 1 -> entry 1 (185856, 0, 74240) dir 0x300; Ishad N'ha gate 0 -> entry 2 (10752, 7168, 281600) dir 0x100

#### Spoke 1 - Crypt (CRYPTA/CRYPTB)

| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |
|---|---|---|---|---|---|---|---|
| T1 | @OFFSWITCHDDTOSEGMENT | segment | switch W21 B1 | (280320, 1024, 146432)..(289792, 5120, 153600) | 1 | spoke 0 bsp 0 tcrypt, W1:=0 (back: T8) | (285056, 3072, 150016) |
| T38 | @GOTOBSPONLY | bsp | box B25 | (289280, -10248, 224256)..(291840, -8192, 225792) | 1 | bsp 2 CRYPTB nav 77/495 | (290560, -9220, 365312) |
| T39 | @GOTOBSPONLY | bsp | box B26 | (289280, -10248, 218112)..(291840, -8192, 219648) | 1 | bsp 2 CRYPTB nav 77/76 | (290560, -9220, 359168) |
| T40 | @GOTOBSPONLY | bsp | box B27 | (272384, -7172, 177152)..(273408, -5120, 179712) | 1 | bsp 2 CRYPTB nav 101/100 | (272896, -6146, 318720) |
| T41 | @GOTOBSPONLY | bsp | box B28 | (276480, -7172, 317440)..(277504, -5120, 320000) | 2 | bsp 1 CRYPTA nav 101/83 | (276992, -6146, 178432) |

#### Spoke 2 - Serpent Temple (bsp1 TEMPLEB, bsp2 TEMPLEA)

| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |
|---|---|---|---|---|---|---|---|
| T1 | @OFFSWITCHDDTOSEGMENT | segment | switch W1 B1 | (113920, -2, 322048)..(121088, 3072, 328704) | 1 | spoke 0 bsp 2 ttmpcave, W2:=0 (back: T12) | (6912, 1535, 222976) |
| T2 | @OFFSWITCHDDTOSEGMENT | segment | switch W2 B2 via T115 | (349696, 1275, 305152)..(360448, 7936, 310272) | 2 | spoke 0 bsp 3 ttemple, W3:=0 (back: T15) | (48896, 4606, 218624) |
| T70 | @GOTOBSPONLY | bsp | box B33 | (324608, 1020, 308224)..(330240, 4096, 311296) | 2 | bsp 1 TEMPLEB nav 339/340 | (179968, 25086, 354816) |
| T71 | @GOTOBSPONLY | bsp | box B34 | (187904, 23548, 353280)..(193536, 26624, 356352) | 1 | bsp 2 TEMPLEA nav 920/969 | (338176, 2558, 309760) |
| T110 | @GOTOBSPONLY | bsp | box B39 | (264192, -8192, 306176)..(267264, -1024, 309248) | 2 | bsp 1 TEMPLEB nav 365/374 | (118272, 17920, 352768) |
| T113 | @GOTOBSPONLY | bsp | box B42 | (281088, -14848, 300544)..(286208, -11264, 304128) | 2 | bsp 1 TEMPLEB nav 314/376 | (136192, 9472, 347392) |
| T114 | @GOTOBSPONLY | bsp | box B43 | (123648, 7680, 345600)..(128512, 11264, 349184) | 1 | bsp 2 TEMPLEA nav 960/972 | (273536, -13056, 302336) |

Intra-BSP teleports: T155 @IFSTATETELEPORT B69 -> (287616, 6656, 280848); T156 @IFSTATETELEPORT B70 -> (289408, 6656, 279680); T157 @IFSTATETELEPORT B71 -> (291184, 6656, 278528); T158 @IFSTATETELEPORT B72 -> (294544, 6656, 278528); T159 @IFSTATETELEPORT B73 -> (296320, 6656, 279696); T160 @IFSTATETELEPORT B74 -> (298112, 6656, 280848)

#### Spoke 3 - wilderness (terrain spoke03)

| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |
|---|---|---|---|---|---|---|---|
| T3 | @SWITCHDDTOSEGMENT | segment | switch W1 B2 via T1 | (140896, 6592, 31744)..(146800, 12842, 38528) | 3 | spoke 4 bsp 1 SHURU, W1:=1 (back: T116) | (113128, 1525, 110912) |
| T6 | @SWITCHDOORTOSEGMENT | segment | switch W2 B4 via T4 | (167088, 304, 49568)..(175536, 6336, 58656) | 2 | spoke 4 bsp 1 SHURU, W26:=1 (back: T31) | (146736, -4872, 130912) |
| T18 | @SWITCHDOORTOSEGMENT | segment | switch W5 B11 via T16 | (85936, -224, 224224)..(109520, 13664, 240096) | 6 | spoke 7 bsp 1 DRAGONA, W1:=1 (back: T3) | (187840, -3520, 119520) |
| T21 | @SWITCHDDTOSEGMENT | segment | switch W6 B13 via T19 | (122864, 18208, 238560)..(127520, 20480, 243504) | 7 | spoke 7 bsp 2 DRAGONB, W3:=1 (back: T51) | (512264, 17296, 78216) |
| T30 | @SWITCHDDTOSEGMENT | segment | switch W8 B16 via T28,T31 | (50176, 6224, 29200)..(60432, 11520, 38672) | 10 | spoke 5 bsp 1 MINESA, W77:=1 (back: T282) | (131080, -2392, 128144) |
| T34 | @SWITCHDDTOSEGMENT | segment | switch W9 B18 via T32 | (268240, 5792, 268464)..(275376, 10976, 275504) | 8 | spoke 6 bsp 1 OGREA, W16:=1 (back: T107) | (220608, -5952, 143984) |
| T38 | @SWITCHDDTOSEGMENT | segment | switch W11 B21 via T36 | (220416, 6912, 278496)..(227104, 10368, 284080) | 9 | spoke 6 bsp 2 OGREB, W12:=1 (back: T113) | (416272, 5568, 122568) |
| T66 | @ENTERTHETOWN | town | switch W12 via T53 | (284672, -20480, 8192)..(291840, 20480, 16384) | -1 | town 1 Ishad N'ha | - |
| T67 | @ENTERTHETOWN | town | switch W13 via T54 | (1024, -20480, 279552)..(9216, 20480, 287744) | -1 | town 2 Brimloch Roon | - |

Town gate arrivals: Ishad N'ha gate 1 -> entry 1 (284160, 1090, 10752) dir 0x300; Brimloch Roon gate 0 -> entry 0 (7954, 3072, 284160) dir 0x100

#### Spoke 4 - Shuru (SHURU)

| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |
|---|---|---|---|---|---|---|---|
| T31 | @OFFSWITCHDOORTOSEGMENT | segment | switch W26 B64 via T117 | (142512, -7888, 126368)..(150960, -1856, 135456) | 1 | spoke 3 bsp 2 tshurub, W2:=0 (back: T6) | (171312, 3320, 54112) |
| T116 | @OFFSWITCHDDTOSEGMENT | segment | switch W1 B62 via T1 | (110096, -1600, 107520)..(116080, 3536, 113520) | 1 | spoke 3 bsp 3 tshuruc, W1:=0 (back: T3) | (143808, 9160, 34744) |

#### Spoke 5 - Mines (MINESA..MINESD)

| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |
|---|---|---|---|---|---|---|---|
| T6 | @GOTOBSPONLY | bsp | box B6 | (180656, -30864, 63312)..(184960, -24960, 67760) | 1 | bsp 2 MINESB nav 829/830 | (387608, -27912, 65536) |
| T7 | @GOTOBSPONLY | bsp | box B7 | (385392, -30864, 75520)..(389760, -24960, 80240) | 2 | bsp 1 MINESA nav 131/601 | (182776, -27912, 77880) |
| T8 | @GOTOBSPONLY | bsp | box B8 | (177856, -30832, 125248)..(184928, -25408, 129744) | 1 | bsp 2 MINESB nav 723/832 | (386192, -28120, 127496) |
| T9 | @GOTOBSPONLY | bsp | box B9 | (369888, -30832, 125248)..(376464, -25408, 129744) | 2 | bsp 1 MINESA nav 314/315 | (168376, -28120, 127496) |
| T19 | @GOTOBSPONLY | bsp | box B12 | (462848, -44208, 164864)..(468480, -38912, 169472) | 2 | bsp 4 MINESD nav 8/259 | (506624, -41560, 453888) |
| T20 | @GOTOBSPONLY | bsp | box B13 | (493936, -44208, 451584)..(499440, -38912, 456192) | 4 | bsp 2 MINESB nav 769/770 | (455728, -41560, 167168) |
| T32 | @GOTOBSPONLY | bsp | box B25 | (370576, -41056, 205312)..(376048, -35664, 210400) | 2 | bsp 3 MINESC nav 848/847 | (199232, -38360, 433136) |
| T33 | @GOTOBSPONLY | bsp | box B26 | (206032, -41056, 430592)..(210832, -35664, 435680) | 3 | bsp 2 MINESB nav 384/385 | (382512, -38360, 207856) |
| T34 | @GOTOBSPONLY | bsp | box B27 | (326880, -36016, 174624)..(330512, -29920, 178800) | 2 | bsp 3 MINESC nav 928/929 | (154616, -32968, 401992) |
| T35 | @GOTOBSPONLY | bsp | box B28 | (161920, -36016, 399904)..(167088, -29920, 404080) | 3 | bsp 2 MINESB nav 839/840 | (338584, -32968, 176712) |
| T282 | @OFFSWITCHDDTOSEGMENT | segment | switch W77 B161 via T280,T283 | (125952, -5040, 123408)..(136208, 256, 132880) | 1 | spoke 3 bsp 10 tmines, W8:=0 (back: T30) | (55304, 8872, 33936) |

#### Spoke 6 - Ogre (OGREA..OGREC)

| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |
|---|---|---|---|---|---|---|---|
| T18 | @GOTOBSPONLY | bsp | box B16 | (161376, -15744, 197936)..(166208, -11808, 200240) | 1 | bsp 2 OGREB nav 892/893 (nav id missing) | (320464, -3536, 141744) |
| T19 | @GOTOBSPONLY | bsp | box B17 | (317792, -5552, 134784)..(323360, -1856, 136672) | 2 | bsp 1 OGREA nav 894/897 | (163904, -13944, 193072) |
| T20 | @GOTOBSPONLY | bsp | box B18 | (152432, -16272, 153936)..(156432, -12656, 155200) | 1 | bsp 3 OGREC nav 821/911 | (154432, -14464, 379848) |
| T21 | @GOTOBSPONLY | bsp | box B19 | (152432, -16272, 385408)..(156432, -12656, 386768) | 3 | bsp 1 OGREA nav 820/917 | (154432, -14464, 160808) |
| T23 | @GOTOBSPONLY | bsp | box B21 | (154928, -16032, 139104)..(158208, -12352, 140400) | 1 | bsp 3 OGREC nav 834/908 | (156568, -14192, 365032) |
| T24 | @GOTOBSPONLY | bsp | box B22 | (154928, -16032, 368832)..(158208, -12352, 370160) | 3 | bsp 1 OGREA nav 833/915 | (156568, -14192, 144216) |
| T25 | @GOTOBSPONLY | bsp | box B23 | (223488, -8464, 165664)..(225392, -4560, 169088) | 1 | bsp 2 OGREB nav 725/733 | (381112, 3728, 110032) |
| T26 | @GOTOBSPONLY | bsp | box B24 | (373616, 1776, 108320)..(376304, 5680, 111744) | 2 | bsp 1 OGREA nav 871/870 | (218288, -6512, 167376) |
| T107 | @OFFSWITCHDOORTOSEGMENT | segment | switch W16 B56 via T105 | (217040, -8544, 140464)..(224176, -3360, 147504) | 1 | spoke 3 bsp 8 togrea, W9:=0 (back: T34) | (271808, 8384, 271984) |
| T113 | @OFFSWITCHDDTOSEGMENT | segment | switch W12 B61 via T111 | (413184, 3616, 119824)..(419808, 7536, 125280) | 2 | spoke 3 bsp 9 togreb, W11:=0 (back: T38) | (223984, 8648, 281272) |

Intra-BSP teleports: T63 @IFSTATEPAINTP B33 -> (364928, -2048, 153728); T64 @IFSTATEPAINTP B34 -> (351360, -2048, 158720); T65 @IFSTATEPAINTP B35 -> (363136, -2048, 160384); T66 @IFSTATEPAINTP B36 -> (354432, -2048, 168352); T67 @IFSTATEPAINTP B37 -> (355200, -2048, 150144); T68 @IFSTATEPAINTP B38 -> (362880, -2048, 171648); T69 @IFSTATEPAINTP B39 -> (364416, -2048, 145536); T70 @IFSTATEPAINTP B40 -> (334752, -2048, 145200); T71 @IFSTATEPAINTP B41 -> (371072, -2048, 166272); T72 @IFSTATEPAINTP B42 -> (340352, -2048, 155392); T73 @IFSTATEPAINTP B43 -> (346112, -2048, 146432)

#### Spoke 7 - Dragon (DRAGONA..DRAGONC)

| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |
|---|---|---|---|---|---|---|---|
| T3 | @OFFSWITCHDOORTOSEGMENT | segment | switch W1 B2 via T1 | (176048, -10464, 111584)..(199632, 3424, 127456) | 1 | spoke 3 bsp 6 tdragona, W5:=0 (back: T18) | (97728, 6720, 232160) |
| T34 | @GOTOBSPONLY | bsp | box B34 | (477488, -11264, 97488)..(487312, -8160, 100176) | 2 | bsp 1 DRAGONA nav 83/82 | (226400, -9712, 98832) |
| T35 | @GOTOBSPONLY | bsp | box B35 | (221840, -11264, 90000)..(230048, -8160, 93840) | 1 | bsp 2 DRAGONB nav 369/370 | (481944, -9712, 91920) |
| T36 | @GOTOBSPONLY | bsp | box B36 | (477392, -9040, 98576)..(482608, -5728, 102256) | 2 | bsp 1 DRAGONA nav 72/73 | (224000, -7384, 100416) |
| T37 | @GOTOBSPONLY | bsp | box B37 | (221392, -9040, 88448)..(225920, -5728, 93600) | 1 | bsp 2 DRAGONB nav 364/363 | (479656, -7384, 91024) |
| T40 | @GOTOBSPONLY | bsp | box B40 | (225360, 3024, 113808)..(227904, 7536, 118912) | 1 | bsp 2 DRAGONB nav 464/465 | (482632, 5280, 116360) |
| T41 | @GOTOBSPONLY | bsp | box B41 | (473152, 4448, 112688)..(475936, 9872, 118224) | 2 | bsp 1 DRAGONA nav 223/222 | (218544, 7160, 115456) |
| T42 | @GOTOBSPONLY | bsp | box B42 | (277600, 8336, 375824)..(279472, 11952, 380320) | 3 | bsp 2 DRAGONB nav 323/322 | (534536, 10144, 122072) |
| T43 | @GOTOBSPONLY | bsp | box B43 | (536784, 7104, 120368)..(538464, 11040, 124944) | 2 | bsp 3 DRAGONC nav 320/321 | (281624, 9072, 378656) |
| T44 | @GOTOBSPONLY | bsp | box B44 | (277744, -11072, 376800)..(279456, -5520, 381696) | 3 | bsp 2 DRAGONB nav 291/292 | (534600, -8296, 123248) |
| T45 | @GOTOBSPONLY | bsp | box B45 | (537568, -11072, 119808)..(539648, -5520, 124560) | 2 | bsp 3 DRAGONC nav 294/293 | (282608, -8296, 378184) |
| T51 | @OFFSWITCHDOORTOSEGMENT | segment | switch W3 B49 via T49 | (509936, 16160, 75744)..(514592, 18432, 80688) | 2 | spoke 3 bsp 7 tdragonb, W6:=0 (back: T21) | (125192, 19344, 241032) |

#### Spoke 8 - Lich (LICHA/LICHB)

| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |
|---|---|---|---|---|---|---|---|
| T3 | @OFFSWITCHDDTOSEGMENT | segment | switch W1 B2 via T1 | (106000, -1392, 150944)..(113024, 6080, 157968) | 1 | spoke 11 bsp 3 tlich, W5:=0 (back: T14) | (267208, 15400, 119640) |
| T79 | @GOTOBSPONLY | bsp | box B45 | (101504, -18912, 152496)..(103520, -13792, 153226) | 1 | bsp 2 LICHB nav 1024/1023 | (307312, -16352, 152861) |
| T197 | @GOTOBSPONLY | bsp | box B5 | (307808, -17712, 153632)..(308672, -14576, 155648) | 2 | bsp 1 LICHA nav 1021/1366 | (103440, -16144, 154640) |

#### Spoke 9 - Sunken (SUNKENA/SUNKENB)

| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |
|---|---|---|---|---|---|---|---|
| T3 | @OFFSWITCHDDTOSEGMENT | segment | switch W1 B2 via T1 | (208032, -3584, 144304)..(215472, 480, 151568) | 1 | spoke 11 bsp 4 tsunkena, W6:=0 (back: T17) | (81704, -26128, 109024) |
| T24 | @GOTOBSPONLY | bsp | box B15 | (246352, -1344, 402064)..(247888, 6400, 407536) | 2 | bsp 1 SUNKENA nav 3/4 | (247120, 2528, 200000) |
| T39 | @GOTOBSPONLY | bsp | box B25 | (254240, -1344, 196688)..(260768, 6400, 202960) | 1 | bsp 2 SUNKENB nav 9/8 | (257504, 2528, 404624) |
| T41 | @OFFSWITCHDDTOSEGMENT | segment | switch W11 B9 via T18 | (253936, -2896, 150560)..(264112, 1824, 161392) | 1 | spoke 11 bsp 5 tsunkenb, W7:=0 (back: T20) | (111568, -29208, 81224) |

#### Spoke 10 - Shrine (SHRINEA)

| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |
|---|---|---|---|---|---|---|---|
| T3 | @OFFSWITCHDOORTOSEGMENT | segment | switch W1 B2 via T1 | (216992, -6560, 205792)..(231520, 704, 211216) | 1 | spoke 11 bsp 1 tshrinea, W1:=0 (back: T3) | (285696, 5264, 257656) |
| T39 | @OFFSWITCHDOORTOSEGMENT | segment | switch W6 B24 via T50 | (172432, -27680, 132176)..(182224, -19712, 140384) | 1 | spoke 11 bsp 0 tshrineb, W2:=0 (back: T6) | (260272, -9360, 242776) |
| T48 | @OFFSWITCHDDTOSEGMENT | segment | switch W7 B32 via T46 | (208880, -28000, 28768)..(223104, -19024, 41904) | 1 | spoke 11 bsp 2 tshrinec, W4:=0 (back: T10) | (207800, -21464, 208392) |

Intra-BSP teleports: T144 @IFSTATETELEPORT B52 -> (224256, -9728, 193536)

#### Spoke 11 - wilderness (terrain spoke11)

| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |
|---|---|---|---|---|---|---|---|
| T3 | @SWITCHDOORTOSEGMENT | segment | switch W1 B2 via T1 | (278432, 1632, 254944)..(292960, 8896, 260368) | 1 | spoke 10 bsp 1 SHRINEA, W1:=1 (back: T3) | (224256, -2928, 208504) |
| T6 | @SWITCHDOORTOSEGMENT | segment | switch W2 B4 via T4 | (255376, -13344, 238672)..(265168, -5376, 246880) | 0 | spoke 10 bsp 1 SHRINEA, W6:=1 (back: T39) | (177328, -23696, 136280) |
| T10 | @SWITCHDDTOSEGMENT | segment | switch W4 B6 via T8 | (200688, -25952, 201824)..(214912, -16976, 214960) | 2 | spoke 10 bsp 1 SHRINEA, W7:=1 (back: T48) | (215992, -23512, 35336) |
| T14 | @SWITCHDDTOSEGMENT | segment | switch W5 B9 via T12 | (263696, 10784, 116339)..(270720, 18432, 123520) | 3 | spoke 8 bsp 1 LICHA, W1:=1 (back: T3) | (109512, 1552, 154746) |
| T17 | @SWITCHDDTOSEGMENT | segment | switch W6 B11 via T15 | (77664, -28656, 105888)..(84448, -24480, 111136) | 4 | spoke 9 bsp 1 SUNKENA, W1:=1 (back: T3) | (211104, -1992, 147424) |
| T20 | @SWITCHDDTOSEGMENT | segment | switch W7 B13 via T18 | (108272, -31696, 76224)..(114784, -26944, 84896) | 5 | spoke 9 bsp 1 SUNKENA, W11:=1 (back: T41) | (258984, -648, 155312) |
| T23 | @SWITCHDDTOSEGMENT | segment | switch W8 B15 via T21 | (65712, -6656, 332800)..(77408, 512, 344992) | 7 | spoke 12 bsp 1 PYRAMA, W1:=1 (back: T3) | (169864, -3072, 140240) |
| T62 | @ENTERTHETOWN | town | switch W14 via T57 | (382976, -20480, 21504)..(390144, 20480, 29696) | -1 | town 2 Brimloch Roon | - |

Town gate arrivals: Brimloch Roon gate 1 -> entry 1 (382976, 1024, 25600) dir 0x300

#### Spoke 12 - Pyramid (PYRAMA/PYRAMB)

| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |
|---|---|---|---|---|---|---|---|
| T3 | @OFFSWITCHDDTOSEGMENT | segment | switch W1 B2 via T1 | (164048, -6240, 133552)..(176000, 464, 145936) | 1 | spoke 11 bsp 7 tpyram, W8:=0 (back: T23) | (71720, -2888, 338400) |
| T64 | @GOTOBSPONLY | bsp | box B42 | (185344, -21232, 95424)..(188912, -16048, 98352) | 1 | bsp 2 PYRAMB nav 311/310 | (187128, -18640, 322168) |
| T65 | @GOTOBSPONLY | bsp | box B43 | (180224, -21232, 325696)..(184848, -16048, 328752) | 2 | bsp 1 PYRAMA nav 607/309 | (182536, -18640, 101944) |
| T168 | @GAMEOVER | endgame | special S5 | - | - | end movie 1 | - |
| T169 | @GAMEOVER | endgame | state - | - | - | end movie 0 | - |

Intra-BSP teleports: T89 @EXECTELEPORT B52 -> (171616, -10240, 122368)

---------------------------------------------------------------------------

## 8. Recipe: adding an exit from spoke A to a new place in spoke B

No exe change is needed for a new **LOADSEGMENT** exit. Its arrival point is fully
defined by data and BSP origins. What cannot change without patching the exe:
the 13 spokes, the dungeon BSP lists (`LoadSpokeSegment_`), the town gate ->
spoke/entry mapping (`SetSpokeEntry_`) and `GetEntryPosition_`. The entry table
is a code switch, not a data table. An entry point for a new gate, or a new
town -> spoke route, needs a patch to that function (or an engine port).

Steps for a LOADSEGMENT exit A -> B (and usually B -> A):

1. **Pick the frames.** Choose source BSP slot `a` in spoke A and destination
   slot `b` in spoke B. The party arrives at `local + origin_B[b]`, where `local`
   is its position relative to `origin_A[a]` (+-256 y if spoke 8 is involved).
   * Either build both pieces with the same local geometry around the exit room,
     as the shipped game does. On terrain spokes a new overworld piece is just a
     new TOL 'B' record plus a BSP, which is data only.
   * Or reuse existing BSPs and choose the exit room position in A so that
     `local + origin_B[b]` lands on valid floor in B. There is no free-space
     search on arrival.
   * Or let the party arrive anywhere valid in B and move it with a TELEPORT
     trigger fired by a bound box (who 0/1) at the arrival point. TELEPORT only
     works inside the BSP that the PC is already in.
   * With BSPNUM = -1 nothing is added. Terrain-to-terrain exits from outside any
     BSP keep their world coordinates.
2. **Spoke A tables:**
   * D6BOUN: one record, `bsp=a`, `trigger=-1`, `enabled=0`, min/max = exit room
     (local to `a`). This is the IFALLINBOUND volume.
   * D6SWIT: a lever (placed prop `isentity=0`, objid per `GetObjNum_`) or a brush
     entity, with `target = S` (a free WState index of spoke A, 1..255) and `on=0`.
   * D6TRIG: `@SWITCHDDTOSEGMENT` or `@SWITCHDOORTOSEGMENT`, `state=S`,
     `enabled=1`, `mode=0`, params !STATE=S, DOOR(A/B)=`a*100000+d6entid`, SPEED,
     NAVPNT (nav id that the door blocks/unblocks), !BOUNDNUM, !SWITCH (the
     lever), STATUS=0, SEGMENT=B, BSPNUM=b, SEGSWITCH (lever in B or -1),
     SEGSTATUS=1, DELAY ms, SFXREC.
3. **Spoke B tables:** the mirror image with the OFF variant (SEGSTATUS=0). Its
   bound box must contain the arrival point. Its lever is the SEGSWITCH of step 2,
   so arriving opens the doors.
4. **Saves:** visited spokes are restored from `D6SEGnn.GAM` / `D6ARCHIV.DAT`
   (triggers, bounds, switches, world state), and that state overrides the pristine
   tables. Test with a new game, or delete those files.
5. Check the new exit with `d6exits.py GAMEDIR A`: "way back" must find the
   return trigger.

For an exit **within** a spoke between two BSPs, use a `@GOTOBSPONLY` trigger fired
by a bound (`who=5`, `bsp=src`, `trigger=T`). The two BSPs must overlap at the box
in their local frames. Add the opposite box in the destination BSP, and D6LINK
entries so the path AI can cross.

For a new exit **to a town**, copy the gate pattern from 5.4. The way back from
the town always uses the hard-coded gate entries.

`d6exits.list_exits()` / `list_entrances()` give an editor everything it needs to
draw these exits: source box or switch in world units, destination spoke/BSP,
arrival point and the records involved.

---------------------------------------------------------------------------

## 9. Resolved questions (from the code and the data)

* **InBSPArea_** (0x45e250) is reproduced exactly by `d6exits.bsp_at`. Both it
  and `InBSPOnly_` use only the tile rectangle of each placement (`_bspBounds`,
  written by `Terrain_BSPLoad_` = d6level `tile_rect`), tiles = pos / 1024
  truncated toward zero. Dungeon spokes: the first slot whose rectangle
  contains the tile. Terrain spokes: `InBSPOnly_` never matches (its loop
  starts after the terrain BSPs); the game walks a tile-space BSP tree built
  once per spoke by `TerBSP_Calculate_` (4 splitters per placement, the first
  splitter nothing straddles, a leaf takes the slot of the last splitter on
  its path), ported as `d6exits.terrain_leaf_tree`; -1 off the map. For the
  shipped data (no overlapping rectangles in any spoke) the tree gives the
  same slot as the rectangle test on every tile of spokes 0, 3 and 11. The
  2-tile-grown box at BSP +0x118dc is only used by `InBSPBounds_`
  (collision, particles, spells).
* **Spoke 8 y shift**: the signs are right; see section 3 for the exact
  conditions (-256 only for PCs in a BSP; +256 whenever BSPNUM != -1).
* **_gSin/_gCos**: 1024 steps per turn of 6.28 (section 5.2), x = sin, z = cos;
  the arc side of PCs 1-5 is right. Checked against a save (section 5.2).
* **Dungeon rows of GetEntryPosition_**: its only caller is
  `AssignPartyCamera_`, called on a fresh spoke entry (after `SetSpokeEntry_`,
  which only picks spokes 0, 3 and 11) and by the host for a multiplayer
  client (`ProcMsgPENTRY_`, net message 0x23) using the host's spoke. So the
  dungeon rows are unreachable in single player; they serve a client joining
  while the host is in a dungeon.
* **GOTOBSPTERR** is GOTOBSP: both jump table entries (0x4dfb08) point to the
  same handler (0x4e083c), which never reads the opcode.
* **!BOUNDNUM / _gSegLoadBounds** (0x5d27f0): written by `InitSpoke_` and
  LOADSEGMENT, never read, saved or sent. Unused.
* **Conditions**: `d6exits` now prints the tests of each exit's event (and of
  the events that call it) as text: IFSTATE, IFHASITEM, IFALLINBOUND,
  IFUSINGITEM, IFNOTOCCUPIED, IFOCCUPIEDBY, IFOBJINBOUND, IFSWITCHON,
  IFEQUIPPED, IFMONALIVE (jump ops: true = jump) and SETQFLAGIF (the only
  quest flag test). A switch can also require a key item (switch +0xc).
  Event operands are indexes into the trigger's parameters; d6exits resolves
  them. It still follows CALLEVENT, SETSTATE and SETSWITCH chains to depth 4.
