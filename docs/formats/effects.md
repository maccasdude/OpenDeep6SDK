# Spells and visual effects

Reader/writer: `formats/d6efx.py` (`python3 formats/d6efx.py GAMEDIR` runs the
round-trip self test and prints the spell table). Round trips, byte
identical: emitters.dat 1/1, efxgfx `.ant` 132/132 (122 ANT, 10 CNT; every CNT
row re-encodes identically), `.alf` 20/20, the spell table in deep6.exe.
Confidence: **H** read from code and checked against data, **M** likely,
**L** guessed.

## 1. Spell table (deep6.exe, H)

`_gSpellData`, VA 0x5D7D80, 105 records of 0x30 bytes (`d6efx.SpellTable`,
`from_exe` / `patch_exe`). There is no data file. `ItemSpell_(id)` turns the
external id (+0x20) into a table index. Ids of 1000 and up are monster
powers, handled by hard-coded switches.

| off | type | field | meaning |
|---|---|---|---|
| 0x00 | char[32] | name | overwritten at start (see below) |
| 0x20 | i16 | id | external id 1..105 (items +0x5C, monster spells, tomes, CAST events) |
| 0x22 | i8 | school | 0 Spirit, 1 Sun, 2 Moon, 3 Vine, 4 Stone, 5 Fiend, -1 none: mana pool and spellbook page |
| 0x23 | u8 | level | 1..7 (`_gSpellIndex[school][level][slot]`, known-spell bits) |
| 0x24 | u8 | slot | 1..n within the level |
| 0x26 | i16 | recover_ms | 1000 * level (summons 4000 / 7000) |
| 0x28 | i16 | mana | 10/20/30/40/50/75/100 by level |
| 0x2A | u8 | target | 0 self/party, 1 needs a target |
| 0x2B | u8 | flags | bit 0 needs line of sight; underwater: bit 1 fizzles, bit 2 hits the caster |
| 0x2C | u8 | category | 0/1 hostile, 2 heal, 3 cure, 4 buff, 5 resurrect, 6 mind, 7 utility, 8 artifact, 9 trap/lock (ally AI targeting; 5 may target the dead) |
| 0x2D | u8 | animseq | cast animation 100..103 |
| 0x2E | u16 | pad | 0 |

**Names**: `LoadStrings_` (d6string.c) clears every name and copies
D6STRING.DAT string 9000 + id - 1 into it, so the names the game shows
(spellbook, combat log, "learns ... spell") come from D6STRING.DAT, not from
the exe (checked in the game: string 9019 renamed Burn). The other fields are
used from the mapped exe.

Damage dice, the effect type, the attach point and the sound are **not** in
the table: they are hard-coded per spell id in `CastSpell_` (0x4BABB0: queues
`ATQAnimEfx_(caster, anim, target, EFX type, spell, attach slot, flags)` and
`ATQAnimSfx_`) and `SpellCollide_` (0x4B7BC0: DiceRoll_, SpellDamage_,
RadialDamage_, RadialAffli_, HealMonster_, AddEnchant_ ...). Strength scales
with the caster's spell skill (monsters: level / 5). So names, schools,
levels, mana, costs and targeting can be changed in the table; new spell
behaviour needs code (planned for OpenDWWandWExpanded). Editor: Tools >
Spells (names go to D6STRING.DAT). Target 2 is used only by Spirit Eye (meaning unknown, L).

## 2. Effects (EFX types, H)

`EFX_Init_` (0x47D6F8) handles 97 effect types (0..0x60). Per type:
speed (`_g_VelocityInitArray` 0x468870, f32[97]), range
(`_g_MaxDistanceInitArray` 0x4689F4, all 14336) and flags (`_g_EFXFlags`
0x468B78, bit 0 = colliding projectile). `Effect_Create_` (0x472AF8) picks the
code: lightning, magic missile, burn, zap, fist, skull, spheres, buff
columns, light, vortex, flare, blood, portal, explosions, fire wall, bubbles,
shields, storms, gas balls, swarm, freeze, fire drop, particle trails, fire
breath / clouds and so on. Effect models are loaded from `models/efx`.

Sprite animations (`FXC_GetFilename_`, i = 0..93): 0 dragfire, 1 dragice,
2 pois, 3 fbomb, 4 portal, 5 nukeatom, 6 shroom, 7 bloodc1, 8 bloodc2,
9 bonec1, 10 slimec1, 11 gblood, 12 firewall, 14 dispell, 15 poof,
16 hbreath, 17 dbreath, 18 gaze, 19 gazeglow, 20 slimespit, 21 light,
22 burnex, 23 bluelight, 24 painex, 25 painexp, 26 bless, 27 spikeball,
28 sparkball, 29 sparkex, 30 yellowexp, 31 zap, 32 zapglow, 33 lightbeam,
34 greenexp, 35 mmex, 36 lightning, 37 lightnglow, 38 drain, 39 drainglow,
40 vomit, 41 eye, 42-50 coloured sparks, 51 explosion, 52 meteorex,
53 iexplode, 54 bbexp, 55 flash, 56-93 `particle/` files (blobs, bolts,
pulses, stars, fire, pois, pain, ice, fly, smoke, mm, waterdrp, spore).
**Replacing these files changes the effect look; keep the frame size.**

## 3. emitters.dat (particle emitters, H unless marked)

`i32 n` (31), then n records of 0x80 bytes (`d6efx.EmitterFile`):

| off | type | field |
|---|---|---|
| 0x00 | char[24] | name |
| 0x18 | u32 | flags: 1 angles relative to the emit direction, 2 pass particles to the child, 4 child emitter on destroy, 8 fixed count per frame (M) |
| 0x1C/0x20 | f32 | pitch base / random (radians) |
| 0x24/0x28 | f32 | yaw base / random |
| 0x2C/0x30 | f32 | speed base / random (units/s) |
| 0x34/0x38 | i32 | particle life base / random (ms) |
| 0x3C/0x40 | i32 | particle size base / random |
| 0x44 | i32 | glow sprite size (<<3) |
| 0x48 | f32 | emission rate, particles/s (M) |
| 0x4C | u32 | unknown (L) |
| 0x50 | f32[3] | acceleration per second (gravity y = -1200 / -2048) |
| 0x5C | u32 | 0 |
| 0x60 | i32 | frame count (0 = from the animation, 255 none) |
| 0x64 | i32 | child emitter index + 1 (spawned when a particle dies / hits) |
| 0x68 | i32 | particle limit |
| 0x6C | i32 | emitter duration ms (< 1 = endless) |
| 0x70 | i32 | ms to pre-advance new particles |
| 0x74 | i32 | animation selector 1..9 = coloured spark FXC 0x2A..0x32; 0 = dots |
| 0x78 | i32 | palette selector (1 particle, 2 rainbow, 3 expanim, 4 light.p16) |
| 0x7C | i32 | glow selector 1..16: mmglow, nukeGlow, eyeGlow, sparkFlare, discGlow, skullAlpha, fistAlpha, zapglow, fbirdFlare, fireAlpha, lightnglow, lightray, lightbeamGlow, fireglow2, blueglow, greenglow |

The selectors are replaced by pointers at load time. An emitter calls
`SpellCollide_` when it is destroyed or hits something. (This table replaces
the emitter notes in data.md / terrain.md, whose offsets from +0x44 on were
wrong.) A second, separate particle system (`jhp_part.c`) has 31 descriptors
compiled into the exe (not decoded).

## 4. Sprite files

* `.ant` (`Anim_Load_`, H): `'ANT '` (bytes ` TNA`), `u32 frames, w, h`, a
  0x4000 byte shade table (32 rows x 256 RGB565, as the level .twd), then
  `frames x w*h` palette indices; index 0 is transparent.
* CNT `.ant` (`Anim_Compressed_Load_`, H): `'CNT '`, `u32 frames, u16 w, h`,
  shade table, then per frame `u32 size, u32 rowoff[h], u8 data[size]`. A row
  is alternating runs: skip k (transparent), copy k (k literal bytes follow),
  until w pixels; rows always end on a copy run (a trailing skip gets a 0).
  `Ant.encode_row` writes them exactly like the shipped tool.
* `.alf` (`Alpha_Load_`, H): `'ALF '`, `u32 1, w, h`, `u8 alpha[w*h]` (0..31),
  `u16 rgb565[w*h]`: additive / alpha glow sprites.
* `.p16`: shade tables.

## 5. Animated model textures and glows (_MDLExtend, H)

`_MDLExtend` (0x5E76F0, 64 x 0x54): `char name[0x50]` (`EFXGFX\name`),
`u8 kind, u8 next, i16 param`. Kind 1: animated texture, `Model_AddAnimTex_`
puts the .ant on texture slot `param` of the model (BIGFIRE, BALFIRE,
FIREWALL, TORCH-WOOD, CANDLE, FOUNTAIN ...). Kind 2: glow, `param` indexes
`_MDLExAlpha` (0x5E8BF0, 0x14 stride: i16 glow, u32 size (M), f32[3] offset,
i16 next). Replacing the named .ant files animates those models differently.

## 6. Spellbook UI

`spellmask.bmp` (230x238, 8 bit, at screen 100,136): pixel 0..6 = school
tab / button (`SpellMaskField_`). `vxspell.bmp` is a 31x31 cursor;
`VXSPELL.PTR` is not referenced by the exe (leftover, L).
