# Save games and the character roster

Save slots (`save/gameNN.sav`), the inn roster (`ROSTER.DAT`) and the
character record they share. Reference implementation: `formats/d6save.py`
(read, write, edit, thumbnail, archive extraction, `--selftest`).

Sources: `SaveCurrentGame_` 0x526F08, `RestoreCurrentGame_` 0x527D68,
`SegWrite_ReadSaveGameIHDI_` 0x5260D8 (load screen), `WriteFountainTimers_`,
`RObj_Write_` (rotobj.c), `OpenRoster_`, `LoadRoster_`, `LoadPC_`, `SavePC_`
(pccreate.c). H unless marked.

    python3 formats/d6save.py info  save/game00.sav
    python3 formats/d6save.py set   save/game00.sav out.sav pc0.gold=5000 pc0.strength=25 pc0.name=Redux
    python3 formats/d6save.py thumb save/game00.sav thumb.png
    python3 formats/d6save.py extract save/game00.sav OUTDIR
    python3 formats/d6save.py --selftest GAMEDIR

Checked in the game: a save edited with `set` (name, gold, strength) loads,
the new name shows on the party panel and the values are in the character
record in memory.

## 1. Files

* `save/SAVEGAME.DIR` - 14 bytes of text, `SAVEGAME.DIR` + CRLF: a marker.
* `save/gameNN.sav` - one per slot, below.
* `ROSTER.DAT` - characters waiting at the inn (section 5).
* While saving, the game writes `SAVEARCH.$$$` (the runtime archive) and
  deletes it; while loading, `RESTARCH.$$$`.

## 2. gameNN.sav layout

All integers little-endian. Header, 0x54 bytes = 21 u32 (`_SavegameHeader`):

| u32 | meaning |
|---|---|
| 0 | version, must be 0x140 |
| 1 | `_gPartyWarshipFlag` |
| 2 | `_gGMUniqueBits` (guildmaster unique bits, GM op SETUBIT) |
| 3 | offset of the description (always 0x54) |
| 4 | offset of the party state (section 3) |
| 5, 6 | offset, size of the copy of `D6ARCHIV.DAT` at save time |
| 7, 8 | offset, size of the runtime archive (`SaveArchive_`) |
| 9..14 | per party slot 0..5: 1 = member block present |
| 15..20 | per party slot: offset of the member block (section 4) |

The game writes the parts in this order, and `d6save` keeps it:

| part | size | content |
|---|---|---|
| header | 0x54 | above (rewritten at the end with the offsets) |
| description | 0x50 | text typed on the save screen |
| info | 0x40 | date string (`SR_MakeDateString_`), e.g. `October  2, 2026   1:29 PM` |
| party count | 4 | i32 n |
| roster lines | n x 32 | per member: name char[16], status text char[16] (`_statusstr`) |
| thumbnail | 0x75F0 | 136 x 111 pixels, 16 bits each, copied from the screen canvas at (392, 8) (the right part of the 3D view) in the screen's pixel format (RGB565 on current systems, M) |
| archive copy | header 6 | `D6ARCHIV.DAT` as it was: the cache of visited spokes |
| runtime archive | header 8 | `SaveArchive_` output: D6WORLD, D6SEGnn.GAM, journals, fog/markers, ROSTER, shop files (`data.md` section 7) |
| party state | | section 3 |
| member blocks | | section 4, one per present member |

Loading: the runtime archive is unpacked into the game folder
(`RestoreArchive_`), the archive copy becomes `D6ARCHIV.DAT`, then the party
state and members are read and the spoke is entered. Both archives use the
D6ARCHIV layout and are listed / extracted by `d6data.py --list/--extract`.

## 3. Party state

Fixed sizes, in this order (names are the exe's globals):

| field | size | field | size |
|---|---|---|---|
| `_gSpokeNumber` | 4 | `_gPCCamFlag` | 4 |
| `_gPartyN` | 4 | `_gBallGlow` | 0x12 |
| `_gPartyI` | 0x18 | `_gBallGlowON` | 0x12 |
| `_gPartyS` | 0x18 | `_gPCTorchLight` | 0x18 |
| `_gPartyU` | 0x18 | `_gPCTorchBrite` | 0x18 |
| `_gPartyF` | 0x18 | `_gPCSpellLight` | 0x18 |
| `_gBannerLeader` | 0x18 | `_gPCSpellBrite` | 0x18 |
| `_gPCCombatID` | 0x18 | `_gPCSpiritEye` | 0x18 |
| `_gPCNextOrder` | 0x18 | `_gPCInspectBits` | 0x18 |
| `_gPCReadyMode` | 0x18 | `_gPCInspectTrap` | 0x18 |
| `_gPCReadyData` | 0x18 | `_gWorldClockTime` | 4 |
| `_gPCReadyTarg` | 0x18 | `_gNpcStopTime` | 4 |
| `_gPCLatchProp` | 0x18 | `_gNpcStatus` | 0x140 |
| `_gPCExpAcc` | 0x18 | `_gNpcMBits` | 0x280 |
| `_PCFollow` | 0x18 | `_WState` | 0x1000 (16 spokes x 256 world states) |
| `_gPCidx` | 4 | `_gPortalFlag` | 0x40 |
| `_gPCaop` | 4 | `_gPortalBase` | 0xC0 |
| `_gCamMan` | 4 | `_gPortalHRet` | 0x18 |
| `_PCCamRotation` | 0x48 | `_gPortalHome` | 0x48 |

Then the fountain timers: i32 count (must equal the number of fountains the
game knows, else "FOUNTAIN COUNT" error), count x 0x18 bytes.

## 4. Member block

`mon` is the member's runtime monster record (`_gMonster`, 0x2A8 bytes).

| part | size | content |
|---|---|---|
| pc | 0x27F8 | character record (section 6) |
| attach_items | 0x38 | i16[28] item at each attachment point |
| attach_has_ext | 0x1C | u8[28]: 1 = an object extension follows |
| extensions | n x 0xA8 | one per flagged attachment (`ObjExt`) |
| pos | 0xC | f32 x, y, z world position (`mon+0x10`) |
| rot | 0xC | `mon+0x1C` |
| | 4 | `mon+0x0C` |
| | 4 | u32 = byte at the model object +0x7D |
| | 0xC | `mon+0xBC` |
| spell memory | 0x14 | `_gPCSpellMemory[pc]` |
| vampire boon | 4 | `_gPCVampireBoon[pc]` |
| spec-op mode | 4 | `_gPCSpecOpMode[pc]` |
| netman | 0x12E | `LoadManToNetMan_`: the monster state in its network form (hit points ...), M |
| pos2 | 0xC | `mon+0x50`, a second position (y + 832) |
| | 8 x 4 | `mon+0x5C`, `+0xE4` .. `+0xFC` |
| | 1, 2 | `mon+0x272`, `mon+0x274` |
| mount | 2 | i16 `mon+0x276`, -1 = none; else a 0x12-byte `_gPCMount` record follows |
| | 2, 1, 4 | `mon+0x270`, model +0x7C, model +0x78 |
| | 0xC, 4 | `mon+0x25C`, `mon+0x268` |
| has_robj | 4 | u32 1 = a rotating-object record follows: u32 flags; if flags & 2: u32, 0x1C, u32, 0x1C, u32, 0x1C, u32 |

## 5. ROSTER.DAT

| offset | content |
|---|---|
| 0x00 | u32 offset[15]: file offset of each roster slot, 0 = empty |
| 0x3C | u32 value[15]: third argument of `SavePC_` per slot (meaning not known, M) |
| 0x78 + i x 0x27F8 | character record of slot i (section 6) |

## 6. Character record (0x27F8 bytes, `_pc[6]`)

The same record is used for the party (`_pc`, 0x6500F0), the local party in
towns (`_lpc`, 0x65F220), the roster and saves. Fields found so far (the
rest is carried as raw bytes). Name lists are the game's own strings.

| offset | type | field | values / notes |
|---|---|---|---|
| 0x08 | char[16] | name | |
| 0x18 | i16 | gender | 0 Male, 1 Female (GM GENDER) |
| 0x1A | i16 | clan | 0 Human, 1 Elf, 2 Dwarf, 3 Gnome, 4 Pixie, 5 Omphaaz, 6 Whiskah, 7 Gourk, 8 Ratling, 9 Lizzord (GM CLAN) |
| 0x1C | i16 | role | 0 Warrior, 1 Wizard, 2 Priest, 3 Rogue, 4 Ranger, 5 Bard, 6 Samurai, 7 Paladin, 8 Barbarian, 9 Monk, 10 Ninja, 11 Warlock (GM ROLE) |
| 0x1E | i16[8] | abilities | Strength, Intellect, Spirituality, Dexterity, Agility, Fortitude, Will, Presence (`PCDrawAbil_`) |
| 0x4E | i16[16] | resistances | Magic, Fire, Mind, Paralysis, Death, Petrification, Cold, Wind, Earth, Poison, Elements, Dispel, Silence, Light, Charm, Mavin (`PCStatDisplay_`) |
| 0xF2 | i16 | alignment | 0..100 (GM ALIGNMENT) |
| 0x108 | u32 | gold | (GM / NPC GOLD) |
| 0x110 | u32 | experience | (`AwardExp_`) |
| 0x118 | i16 | level | (`PCLevelUp_`) |
| 0x11C | i16 | hit | shown as hit - 10 |
| 0x120 | i16 | parry | |
| 0x124 | i16 | shield | |
| 0x128 | i32 | armor | `DrawStatAC_` |
| 0x12C | i32 | speed delay | shown as (3000 - value) / 100 |
| 0x19C | i16 | status | 0 OK, 1 STONE, 2 INANIMATE, 3 DEAD, 4 BONES, 5 ASH, 6 LOST |
| 0x1A0..0x1D4 | | afflictions | `SetAffli_`, `CureMonster_`, `PoisonMonster_` (M) |
| 0x1E4 | i16[6] | | spell school entries tested by the spell book (M) |
| 0x298 | 78 x 0x56 | inventory | item instances: +0 i16 item record, +2 i16 durability, +4 u8 flags (2 cursed, 4 identified, 0x10 invoked), +6 i32 charges / quantity, +0xA name ... (`databases.md`) |
| 0x1CCA.. | | equipped weapon / quiver slots | (`ExecFight_`, `PCReQuiver_`, M) |
| 0x1CE6 | u8[256] | quest flags | (NPC QFLAG) |
| 0x1DE6 | i8[160] | attitude per NPC | (NPC ATTITUDE) |
| 0x1EA8 | u32[160] | NPC script register per NPC | (NPC PCREG) |
| 0x2758 | | party group / link | (`PCPartyGroup_`, `SplitTheParty_`, M) |
| 0x2760, 0x2762 | i16 | polymorph state | (`ReversePolymorph_`, M) |
| 0x2784 | u32 | kills | |
| 0x2790 | u32 | assists | |

Hit points and mana are kept in the runtime monster record, so in a save
they are part of the member block (`netman`), not of the character record.
