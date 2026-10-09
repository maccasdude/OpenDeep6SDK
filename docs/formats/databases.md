# Deep6 content databases - record layouts

Wizards & Warriors (2000, deep6.exe). Field-level reverse engineering of the global
content databases, aimed at a modding SDK that edits monsters, items, props and
treasure and adds new records. Companion to `data.md` (container formats) and the
reference implementation `formats/d6data.py` (FIELDS lists mirror these tables).

Conventions

* All integers little-endian. Offsets are into the on-disk record (= in-memory
  record, the game reads records verbatim).
* Ghidra pattern `*(int *)(p + X) >> 0x10` is the i16 at `X + 2`; all offsets below
  are already corrected for that.
* Confidence: **high** = read from the consuming code and consistent with data;
  **med** = from code but meaning partly inferred, or meaning from data only with a
  code reference; **low** = guess from data distribution.
* "Source" names the function(s) in `re/decomp/*.c` where the field is consumed.
* Item globals: `_Item` = 0x66E280, record i at `_Item + i*0x11C` (so
  `DAT_0066e2ac` = item field 0x2C). Monster runtime: `_gMonster` = 0x628370,
  stride 0x2A8, `+0` = pointer to the loaded D6MONS record.
* Enumeration names come from D6STRING.DAT (string id given) - they are the names
  the game itself shows.



Database file layout (all `RecordTable`s below): record 0 is a header whose first u32
is the record count; record i (1-based) is at file offset `i * SIZE`. The game always
`lseek`s to `index * SIZE`, so the index is the identity of a record everywhere (object
lists `M###`/`I###`/`P###`, scripts, treasure dice, shops, saves).

---------------------------------------------------------------------------

## 1. D6ITEM.DAT - items, 0x11C bytes, 857 records

Loader: `LoadItems_` (deep6.c) reads the u32 count (error if > 999) and then **all**
records into `_Item[1..count]` at startup. Display: `ItemBoxText_` (pcinvent.c) is the
best single reference - it prints most fields with D6STRING format strings 2701..2813.

An *inventory instance* (0x56 bytes, PC inventory at pc+0x298, 78 slots) is created by
`ItemToInv_` (townsmit.c) from the record: `+0 i16 item, +2 i16 durability (DiceRoll of
durdice, or max for shop items), +4 u8 flags (1 ?, 2 cursed, 4 identified, 8 ?, 0x10
invoked), +6 i32 charges/quantity, +0xA name, +0x20 spell, +0x22 damage[3], +0x2E
dmgextra, +0x32 resist[16], +0x42 enchant, +0x44 hitbonus, +0x46 unk108, +0x48 ac,
+0x4A regen, +0x4C tough`. Enchanting/blessing changes the instance copy; ItemBoxText_
prints instance-minus-record differences ("Dam+n", "AC+n").

| off | type | name | meaning | conf | source |
|---|---|---|---|---|---|
| 0x00 | char[22] | name | identified name; natural attacks: verb phrase, "$" = target | high | PCItemName_, ItemName_ |
| 0x16 | char[22] | unidname | name while unidentified ("?Sword?"); natural attacks: attack name | high | PCItemName_, ItemName_ (combat.c), InvName_ |
| 0x2C | i16 | type | 0 Special 1 Weapon 2 Ammo 3 Armor 4 Shield 5 Jewelry 6 Scroll 7 Potion 8 Powder 9 Key 10 Book 11 Food 12 Gold 13 Storage 14 Light 15 Instrument | high | ItemBoxText_, CheckEquipItem_, GetValidEquipSlots_ |
| 0x2E | i16 | subtype | Weapon: 0 hand 1 throwing 2 ranged; Ammo: 1 arrow 2 bolt; Armor: 0 body 1 leg 2 head 3 hand 4 foot 5 helm(D6HELM); Jewelry: 0 ring 1 amulet 2 bracelet 3 other | high | ItemBoxText_, CheckEquipItem_ |
| 0x30 | i16 | icon | inventory icon (exe _icondata, 246 entries: itemicon\<name>.bmp + grid w,h) | high | ItemBoxReq_ (_icondata w,h), CheckEquipItem_ |
| 0x32 | i16 | weight | weight in 0.1 lb | high | ItemBoxText_, CarryLoad_ |
| 0x34 | u8 | invoketype | book/tome effect: 1 ability+1 2 skill+100 3 give trait 4 learn spell | high | InvokeTheItem_ |
| 0x35 | u8 | invokeparam | ability 0..7 / skill 1..32 / trait 0..65 / spell 1..105 | high | InvokeTheItem_, ItemIsRestricted_ |
| 0x36 | i16 | invokeconsume | !=0 -> item destroyed after invoking | high | InvokeTheItem_ |
| 0x3C | u8 | usemode | 0 not usable 1 usable 2 give/show to NPC 3 lock pick (bonus = minstr) | high | ItemIsUsable_, OpUse_, ExecFight_ |
| 0x4C | i32[3] | durdice | durability DiceRoll(n,s,b); max = n*s+b; 0 = never wears | high | ItemToInv_, TryToRepairItem_, ItemBoxText_, ArmorDamage_ |
| 0x58 | i32 | price | base price (per unit for stackables) | high | GetSaleItemPrice_, NPC trading, traps (luck reroll keeps pricier) |
| 0x5C | i16 | spell | spell cast on use (ItemSpell_ id; >=1000 special power) | high | ItemToInv_ (-> inst +0x20), OpUse_, ItemBoxText_ |
| 0x60 | i32[3] | chargedice | charges / quantity at creation DiceRoll(n,s,b) (GetItemCharges_) | high | GetItemCharges_, GetSaleItemPrice_ |
| 0x6C | i32 | readtext | TEXTPAK id shown by the READ option (letters, inscriptions); 0 = none | high | ItemBoxOpList_, ItemBoxReq_ (READ) |
| 0x70 | u32 | restrict | NOT usable by: bits 0-9 clans, 10-24 roles, 25-26 gender, 27-29 alignment Evil/Neutral/Good (IsItemUsableByPC_) | high | IsItemUsableByPC_ |
| 0x74 | u8 | cursed | 1 = cursed (sticks when equipped) | high | CheckPCItemCurses_, ReEquip_ |
| 0x75 | u8 | curseeffect | 1 = drains 1 HP every cursetick ticks | med | CheckPCItemCurses_ |
| 0x76 | i16 | cursetick | curse period | med | CheckPCItemCurses_ |
| 0x96 | u16 | unk96 | unknown (1/2 on 35 items) | low | data only |
| 0x98 | u16 | flags98 | 1 quest 2 always identified 0x10 stackable 0x20 off-hand 0x40 two-handed 0x80 missile 0x200 show model 0x400 random charges 0x800 not sellable 0x1000 unstealable 0x2000 not enchantable 0x4000 0x8000 off-hand only | high | many (see enum) |
| 0x9C | i32 | range | weapons: max range (melee 1280, bows 6144); helms: D6HELM helm index; body armour: PC costume (MyPCModel_) | high | CombatWeaponRange_, InWeaponRange_; GetHelmData_; MyPCModel_ |
| 0xA0 | i32 | minrange | minimum range for missile weapons | high | ExecFight_, EvaluateWeaponInSlot_ |
| 0xA4 | i32 | unka4 | unknown (1280 on 20 items) | low | data only |
| 0xA8 | i16 | minstr | minimum Strength; lock picks: pick bonus | high | ItemBoxText_ ("Minimum Strength"), OpUse_ (lock pick) |
| 0xAA | i16 | ammotype | launcher: ammo subtype required (1 arrow 2 bolt) | high | EvaluateWeaponInSlot_, PCReQuiver_, ReEquip_ |
| 0xAC | u8 | atkflags | 0x40/0x80 unarmed kung-fu attack (uses Kung Fu skill / monster kungfu) | med | OpFight_, ExecFight_, ExecMissile_ |
| 0xAD | u8 | atkflags2 | 2 natural attack 4 alt anim 8 thrust anims 0x10 damage scales with level 0x20 no use/charges | med | OpFight_, ExecFight_, ItemIsUsable_ |
| 0xB0 | i32[3] | damage | damage DiceRoll(n,s,b): shown "Damage n+b - n*s+b" | high | ItemToInv_ (-> inst +0x22), ItemBoxText_ |
| 0xBC | i16[2] | dmgextra | copied to instance +0x2E/+0x30 (0..4) | low | ItemToInv_ (-> inst +0x2E) |
| 0xC0 | i32[8] | specials | 4 x (u8 type, u8 chance%, i16 a, i32 b): type = Sleep..Disease; duration (rand(a)+b) s; Poison/Drain use a,b; see specials_list() | high | SpecialAttacks_, ItemBoxText_ |
| 0xF0 | u8[16] | resist | resistance bonus % per resist type (Magic..Mavin) | high | ItemToInv_ (-> inst +0x32), ItemBoxText_ |
| 0x104 | i16 | enchant | enchantment level (+N) | high | ItemToInv_ (-> inst +0x42), ItemBoxText_ ("Enchant") |
| 0x106 | i16 | hitbonus | to-hit bonus | high | ItemToInv_ (-> +0x44), ItemBoxText_ ("Hit") |
| 0x108 | i16 | unk108 | negative on heavy armour (penalty, copied to instance +0x46) | med | ItemToInv_ (-> +0x46) |
| 0x10A | i16 | ac | armour class bonus (shields: "Rating", jewelry: "AC Protection") | high | ItemToInv_ (-> +0x48), SetMonEquipment_, ItemBoxText_ |
| 0x10C | i16 | regen | regeneration bonus | high | ItemToInv_ (-> +0x4A), ItemBoxText_ ("Regen") |
| 0x10E | i16 | unk10e | unknown | low | data only |
| 0x110 | i16 | tough | toughness % (damage resistance of the item itself) | high | ItemToInv_ (-> +0x4C), ItemBoxText_ ("Tough") |
| 0x112 | i16 | costume | PC body/robe texture set (40..43 = robes, PCInRobes_) | high | PCInRobes_, PCMSetCostume_ |
| 0x114 | i16 | model | index into exe _ItemMDLData (item/<name>.mdl), must be < 240 | high | LoadItemObject_, LoadAttachItemObject_, PCMLoadAttach_ |
| 0x118 | i16 | skill | skill used (1 Sword ... 9 Shield, see SKILLS) | high | ItemBoxText_, ExecFight_, ExecMissile_ |
| 0x11A | u16 | enchantmask | allowed enchantments bitmask: Zap Flamestrike Iceball ProFire ProIce ProMagic Hit Damage Toughness Armor Regenerate Special Recharge | high | GetItemEnchantFlags_, BasicEnchantOK_, EnchantText_, BlessText_ |

Bytes not listed (0x38..0x3B, 0x3D..0x4B, 0x78..0x95, 0xE0..0xEF, 0x100..0x103,
0x116..0x117) are zero in all 857 records, except byte 0x94 (= 6 in a single record).
Unlisted monster bytes (0x3C..0x4B, 0xA0..0xA1, 0xA6..0xA7, 0x11E..0x11F, 0x122..0x127,
0x133..0x13B) and prop bytes (0x23, 0x32..0x33) are zero in all records.

### 1.1 Enumerations

* **type** [9400]: 0 Special, 1 Weapon, 2 Ammo, 3 Armor, 4 Shield, 5 Jewelry, 6 Scroll,
  7 Potion, 8 Powder, 9 Key, 10 Book, 11 Food, 12 Gold, 13 Storage, 14 Light, 15 Instrument.
  Equipable = 1..5 and 14 (`ItemIsEquipable_`); Instrument needs Music skill or trait 65.
* **subtype**: Weapon [9500] 0 Hand, 1 Throwing, 2 Range (launcher); Ammo [9550] 1 Bow
  (arrow), 2 Crossbow (bolt); Armor [9600] 0 Body, 1 Leg, 2 Head (coif), 3 Hand, 4 Foot,
  5 Head (helm, uses D6HELM); Jewelry 0 ring, 1 amulet, 2 bracelet, 3 other (off-hand);
  Storage: resource kind (`ItemIsResource_` returns subtype+1).
* **Equip slots** (`CheckEquipItem_` / `GetValidEquipSlots_`, inventory slot numbers):
  0x3C helm (armor 5), 0x3D head (2), 0x3E body (0), 0x3F legs (1), 0x40 feet (4),
  0x41 hands (3), 0x42 main hand (hand weapon), 0x43 off-hand (shield, light, jewelry 3,
  flag 0x20/0x8000 weapons, 2nd hand weapon), 0x44-0x46 ammo/throwing, 0x47 amulet,
  0x48/0x49 rings, 0x4A bracelet, 0x4B-0x4D belt (only 1x1 icons of types 0,6..11).
  Two-handed (flags 0x40) blocks the off-hand slot.
* **flags98** (u16): 0x0001 quest/plot item, 0x0002 always identified, 0x0010 stackable
  (charges = quantity), 0x0020 off-hand weapon, 0x0040 two-handed, 0x0080 missile special
  collision, 0x0200 shows a model on the wearer (`LoadAttachItemObject_`), 0x0400 random
  charges, 0x0800 not sellable, 0x1000 cannot be stolen, 0x2000 cannot be enchanted,
  0x4000 helm/armour equip conflict check, 0x8000 off-hand only.
* **restrict** (u32, set bit = may NOT use): bits 0-9 clans Human, Elf, Dwarf, Gnome,
  Pixie, Omphaaz, Whiskah, Gourk, Ratling, Lizzord [1300]; bits 10-24 roles Warrior,
  Wizard, Priest, Rogue, Ranger, Bard, Samurai, Paladin, Barbarian, Monk, Ninja,
  Warlock, Assassin, Zenmaster, Valkyrie [1400]; bits 25-26 gender (male, female);
  bits 27-29 alignment Evil/Neutral/Good (PC alignment/33). Verified: Long Sword
  excludes Wizard Priest Rogue Bard Monk Warlock; Ninja Cowl only Ninja+Assassin.
* **skill** [1800]: 1 Sword 2 Axe 3 Mace 4 Pole&Staff 5 Dagger 6 Bow 7 Throwing
  8 2nd Weapon 9 Shield 10 Kung Fu 11 Sorcery 12 Spiritcraft 13 Suncraft 14 Mooncraft
  15 Vinecraft 16 Stonecraft 17 Fiendcraft 18 Leadership 19 Athletics 20 Scout
  21 Traps&Locks 22 Pickpocket 23 Stealth 24 Forge 25 Artifacts 26 Enchants 27 Blessings
  28 Gallantry 29 Prowess 30 Deathstrike 31 Incantation 32 Music.
* **specials** type [9900]: 1 Sleep 2 Stun 3 Knock-Out 4 Paralyze 5 Fear 6 Blind 7 Poison
  8 Stone 9 Death Strike 10 Drain (mana, amount a) 11 Insane 12 Silence 13 Break
  14 Destroy 15 Disease. Effect time = (Random(a) + b) * 1000 ms, resisted via ResistValue_.
* **resist** order [9950]: Magic, Fire, Mind, Paralysis, Death, Petrification, Cold, Wind,
  Earth, Poison, Elements, Dispel, Silence, Light, Charm, Mavin (same order as the
  monster resist array and PC +0x4E).
* **enchantmask** [9300]: bit 0 Zap, 1 Flamestrike, 2 Iceball, 3 Pro Fire, 4 Pro Ice,
  5 Pro Magic, 6 Hit, 7 Damage, 8 Toughness, 9 Armor, 10 Regenerate, 11 Special,
  12 Recharge (weapons typically 0x19C7, armour 0x0338).
* **invoketype**: 1 ability[param] +1 (max 24), 2 skill[param] +100 (if level < 12),
  3 give trait[param] (0..65, names [2400]), 4 learn spell[param] (1..105). 103 books use 4.
* **usemode**: 0 none, 1 generic use, 2 give/show to the targeted NPC (SetNPCAct_),
  3 lock pick (PryTheBox_ with bonus minstr + Traps&Locks skill).

### 1.2 Data sanity

Long Sword: damage 1d4+2 (3-6), weight 8.0 lb, min STR 10, range 1280, skill Sword,
price 100, durability 2d10+80. Long Bow: subtype 2, ammotype 1 (arrows), range 6144,
minrange 1024, skill Bow. Plate Mail: AC 12, unk108 -6, weight 20.0 lb. Natural attacks
(items 1-4, 500.. "swings at $", "bites at $") are type 1 subtype 0 items equipped by
monsters through MonsRec.equip.

---------------------------------------------------------------------------

## 2. D6MONS.DAT - monsters, 0x154 bytes, 372 records

Loader: `MonRec_Load_` (monster.c) - **on demand**, lseek(index*0x154), cached per area
in `_gMonRec[64]` (error "MAX MONRECS EXCEEDED" on the 64th distinct record; cache
cleared by `InitMonsters_`/`DoneMonsters_`). The header count is **not** read or checked.
It zeroes the runtime pointers 0xA8/0xAC/0x150 and loads treasureA/B (D6TREAS) and the
sound record (D6MONSND, preloading its non-resident SFX). `MonsterEntry_` then calls
`LoadMonsterGFX_(gfx)`, `InitMONSToMonster_` (stats -> runtime struct, difficulty
scaling), `MakeMonInventory_`, `MakeMonAttachList_` + `SetMonEquipment_`.

Runtime monster struct `_gMonster[240]` (stride 0x2A8, slots 0-5 are the PCs): +0 record
pointer, +0x1CC abilities[8], +0x1DC resist[16], +0x1FC/+0x1FE HP, +0x202 hit,
+0x204 parry, +0x206 armor, +0x208 shield AC (sum of equipped shields' ac), +0x20C
attack delay, +0x24C mana[6], +0x258 xp, +0x114 npc id, +0x130 i16 inventory[4]
(treasureA items positive = dropped, treasureB items negative = never dropped).

Difficulty (`_gMonsterDifficulty`, skipped for flags132&0x10 critters and NPCs): Easy
`HP*3/4` (if > 3), hit/parry/armor -2, `atkdelay*4/3`, `xp*4/5`; Hard `HP*3/2`, +2, `atkdelay*3/4`,
`xp*5/4`, speed scaled.

| off | type | name | meaning | conf | source |
|---|---|---|---|---|---|
| 0x00 | char[22] | name | display name (MyName_ returns the record pointer); 21 chars + NUL | high | MyName_ (combat.c) |
| 0x16 | i16 | mclass | creature class: 0 animal 1 humanoid 2 undead 3 undead(alt) 4 vampire/seductress 5 lycanthrope 6 demon/magical 7 dragon/reptile 8 insect/serpent 9 slime/fungus 10 plant 11 fish 12 invulnerable(boss phase) 13 construct 14 spirit/non-combat 15 object (statue, cart). 12/14/15 are invulnerable or passive | med | InitMONSToMonster_, ExecFight_, MonsterInvulnerable_, DivvyExp_ |
| 0x18 | i16 | npc | NPC id (D6NPC record / NPCDATA.PAK slot); !=0 -> talking NPC (NpcInitNPC_) | high | MonsterEntry_ -> NpcInitNPC_, LoadMonsterModel_ |
| 0x1A | i16 | gender | 0 male 1 female (NPC script GENDER, PC +0x18 equivalent) | high | EvalExpr_ (npc.c) |
| 0x1C | i16 | clan | clan/race 0..9 (Human..Lizzord); 6/7/8 = furred, no hair twiddling | med | EvalExpr_, SetMonEquipment_ |
| 0x1E | i16 | role | role/class 0..14 (Warrior..Valkyrie), read by NPC scripts | med | EvalExpr_ |
| 0x20 | i16 | alignment | 0..99 (/33: 0 Evil 1 Neutral 2 Good); default 50 | high | EvalExpr_ |
| 0x22 | i16 | unk22 | unknown, 0 or 50 | low | data only |
| 0x24 | i16 | weight | mass for knock-back / pushing (pushy.c object_weight_, wcoll.c) | high | object_weight_ (pushy.c), wcoll.c |
| 0x26 | i16 | unk26 | unknown (128..2048, probably a radius); not read by code found so far | low | data only |
| 0x28 | i32 | speed | movement speed (-> float runtime +0x70; x constants on Hard) | high | InitMONSToMonster_ |
| 0x2C | i16[8] | abilities | STR INT SPI DEX AGI FOR WIL PRE (0..25); FOR*2 = air supply, WIL = spell power | high | InitMONSToMonster_ (-> runtime +0x1CC), GetMaxAirSupply_ |
| 0x4C | i16[16] | resist | resistance % per type: Magic Fire Mind Paralysis Death Petrification Cold Wind Earth Poison Elements Dispel Silence Light Charm Mavin (ResistValue_) | high | InitMONSToMonster_ (-> +0x1DC), ResistValue_ |
| 0x6C | i32[3] | hpdice | hit points = DiceRoll(count, sides, bonus); count is also the monster LEVEL (ResistValue_, TestForSummoning_, level-scaled weapons) | high | InitMONSToMonster_ (DiceRoll), ResistValue_, TestForSummoning_ |
| 0x78 | i16 | unk78 | unknown (0..30000, scales with toughness) | low | data only |
| 0x7A | i16 | hit | to-hit rating (runtime +0x202 = PC +0x11C); -2 Easy, +2 Hard | high | InitMONSToMonster_ |
| 0x7C | i16 | parry | parry/defence rating (runtime +0x204); -2 Easy, +2 Hard | high | InitMONSToMonster_ |
| 0x7E | i16 | armor | armour rating (runtime +0x206); -2 Easy, +2 Hard | high | InitMONSToMonster_ |
| 0x80 | i16 | kungfu | Kung Fu level used by natural punch/kick attacks (item flags 0xAC&0xC0) | high | ExecFight_, ExecMissile_, OpFight_ |
| 0x82 | i16 | bloodtype | hit effect: 0 red blood (can be vampire-drained) 1/2/3 other effects (0x22/0x23/0x2A) | high | DamageMonster_, VampireSuckBlood_ |
| 0x84 | i16 | npcanimA | extra animation set id loaded / NPC talk mode anim (GetNPCModeAnim_) | med | LoadMonsterModel_, GetNPCModeAnim_ |
| 0x86 | i16 | npcanimB | second NPC mode animation set id | med | LoadMonsterModel_, GetNPCModeAnim_ |
| 0x88 | i16 | shadow | shadow texture (-1 = no shadow) (SetMonsterShadow_) | high | SetMonsterShadow_ |
| 0x8A | i16 | reach | melee engage distance, <1 -> 1536 (MonsterUpdateMON_) | high | MonsterUpdateMON_, MonsterAICheckLOC_ |
| 0x8C | i16 | unk8c | unknown (0/256/512) | low | data only |
| 0x8E | i16 | hitsound | per-monster hit sound/variant used by ExecFight_ (0..5) | low | ExecFight_, ExecMissile_ |
| 0x90 | i16 | specialA | special power A: <1000 item-spell id (ItemSpell_), >=1000 monster power (breath etc.); used with chance specApct | high | MonsterCombatAI_ |
| 0x92 | i16 | specialB | special power B (chance specBpct) | high | MonsterCombatAI_ |
| 0x94 | i32 | specrange | range of steal / special powers (0 -> 8192, steal 1536) | high | MonsterCombatAI_ |
| 0x98 | i32 | modelheight | overrides model collision height (model +0x69C); <0 -> 0 | high | LoadMonsterModel_ |
| 0x9C | i32 | xp | experience award (x4/5 Easy if >=7, x5/4 Hard) | high | InitMONSToMonster_ (-> +0x258) |
| 0xA2 | i16 | treasureA | D6TREAS record rolled into the drop inventory (positive ids, dropped on death) | high | MonRec_Load_, MakeMonInventory_ |
| 0xA4 | i16 | treasureB | D6TREAS record for the remaining slots (stored negated, never dropped) | high | MonRec_Load_, MakeMonInventory_ |
| 0xA8 | u32 | rt_treasA | runtime: pointer to loaded treasure A (zeroed by MonRec_Load_) | high | MonRec_Load_, DamageMonster_ -> AddMonTreasure_ |
| 0xAC | u32 | rt_treasB | runtime: pointer to loaded treasure B | high | MonRec_Load_ |
| 0xB0 | i16 | gfx | model index into exe _MonMDLData (17..120; 0..16 = PC bodies); also MONSND default | high | MonsterEntry_, MyMONModel_, LoadMonsterModel_ |
| 0xB2 | i16[5] | overlays | skin overlay texture ids (low byte used), applied in order 1,4,2,0,3; [2]!=0 -> no hair | med | LoadMonsterModel_ (_ovlyorder), SetMonEquipment_ |
| 0xBC | u32[16] | equip | 16 x (u8 kind, u8 chance%, i16 item): kind 1 main weapon 2 off-hand 6 missile, others = attachments; see equip_list() | high | MakeMonAttachList_, GetMonWeaponType_, ResetMonsterWeapon_ |
| 0xFC | i16[6] | mana | mana per realm Spirit Sun Moon Vine Stone Fiend (100 default, 9999 = endless) | high | InitMONSToMonster_ (-> +0x24C), ManaFood_ |
| 0x108 | u8[16] | spells | spell book: up to 16 spell ids (ItemSpell_) chosen by MonsterAISpell_ | high | MonsterAISpell_ |
| 0x118 | u8 | meleepct | !=0 -> fights with weapons (MonsterCombatAI_) | high | MonsterCombatAI_ |
| 0x119 | u8 | spellpct | chance % per AI tick to cast from the spell book (range 8192) | high | MonsterCombatAI_ |
| 0x11A | u8 | unk11a | unknown | low | data only |
| 0x11B | u8 | specApct | chance % to use specialA | high | MonsterCombatAI_ |
| 0x11C | u8 | specBpct | chance % to use specialB | high | MonsterCombatAI_ |
| 0x11D | u8 | stealpct | chance % to try stealing (OpSteal_) | high | MonsterCombatAI_ |
| 0x120 | u8 | unk120 | unknown | low | data only |
| 0x121 | u8 | flypct | !=0 -> flying monster (fly/land anim 0x1F..0x21, MonsterUpdateMON_) | med | MonsterCombatAI_, MonsterUpdateMON_ |
| 0x128 | i32 | atkdelay | attack recovery time ms (x4/3 Easy, x3/4 Hard) | high | InitMONSToMonster_ (-> +0x20C) |
| 0x12C | i32 | unk12c | 11 on 31 small critters, else 0 | low | data only |
| 0x130 | u8 | flags130 | 1 humanoid hair 2 hood/hat hair 4 ? 8 animal 0x10 ? 0x20 rogue 0x40 zombie | med | SetMonEquipment_, SegRead_SetMonAttachments_ |
| 0x131 | u8 | flags131 | 1 flyer(bat) 2 mount (horse, trolley) 4 translucent 8 swimmer 0x10 eyes 0x20 amphibian 0x40 ship (BSP rot object) 0x80 shipwreck | med | InitMONSToMonster_, LoadMonsterGFX_, SetMonEquipment_, MonsterUpdateMON_ |
| 0x132 | u8 | flags132 | 1 raft 2/4 big flyer 8 can be placed in "hold" (ambush) mode 0x10 ambient critter (no difficulty scaling, idle anims) 0x20 ? 0x40 unique | med | InitMONSToMonster_, MonsterEntry_, LoadMonsterModel_ |
| 0x13C | i32[3] | groupdice | group size = DiceRoll(count, sides, bonus) for encounters/summons | high | SummonMonsters_ (genenc.c) |
| 0x148 | i16 | companionpct | chance % that an encounter adds a group of companionA/B (GenerateEncounter_) | high | GenerateEncounter_ |
| 0x14A | i16 | companionA | D6MONS record of the companion group | high | GenerateEncounter_ |
| 0x14C | i16 | companionB | alternative companion (50/50 when non-zero) | high | GenerateEncounter_ |
| 0x14E | i16 | soundrec | D6MONSND record (0 -> no sounds); normally = gfx | high | MonRec_Load_ |
| 0x150 | u32 | rt_sound | runtime: pointer to the loaded D6MONSND record | high | MonRec_Load_, GetMonSound_ |

### 2.1 Enumerations / notes

* **equip** (16 x 4 bytes): `u8 kind, u8 chance%, i16 D6ITEM`. Each entry is rolled with
  its chance; kind 1 = main weapon slot, 2 = second weapon, 6 = missile weapon
  (`GetMonWeaponType_`), other kinds index the attachment list (only one item per kind).
  Natural attacks are ordinary items (e.g. 284 "claws at $"). 322 records use
  `01 64 xxxx` (kind 1, 100%).
* **mclass** values 2 and 3 select the "bone" hit SFX set and skip experience sharing
  rules (`DivvyExp_`); 9/10 select slime/plant SFX; 12 = invulnerable
  (`MonsterInvulnerable_`); 13/15 = inanimate (flag bits |5, no AI); 14 = passive.
  The names in the table are inferred from the records using each value.
* **flags131 / flags132** bit meanings are partly inferred from which records set them
  (e.g. 0x40 only on "Ship O' The Sea", 0x80 only on "Marooned Shipwreck", 132&1 only on
  "Wooden Raft", 132&0x10 on birds, fish, rats, butterflies).
* **specialA/B**: values >= 1000 are monster powers (1001.. breath/gaze; spell table
  entries with id >= 1000 in the exe spell table), < 1000 go through `ItemSpell_`.
  Summon powers (0x3FB, 0x3FC, 0x402, 0x403, 0x406, 0x407, 0x409, 0x40D-0x40F, 0x416)
  are limited to level/5+2 (max 6) conjured monsters (`TestForSummoning_`).
* **gfx**: model number = index into the exe table `_MonMDLData` (121 entries; 0-16 are
  PC bodies and use the PC path in `LoadMonsterModel_`). `LoadMonsterModel_` contains
  ~30 hard-coded per-model animation fix-ups (`gfx == 0x44`, `0x5A`, `0x30`, ...): reuse a
  model number only for a monster that should get the same fix-ups.

---------------------------------------------------------------------------

## 3. D6MONSND.DAT - monster sounds, 0xB8 bytes, 120 records (+ header)

Indexed by `MonsRec.soundrec` (normally = gfx, one record per monster model; record
label holds the model file name). Loaded by `MonRec_Load_`; `soundrec < 1` gives an
all-zero record (silent monster).

| off | type | name | meaning | conf | source |
|---|---|---|---|---|---|
| 0x00 | u32 | modelno | model number (editor label, = record index) | high | - |
| 0x04 | char[20] | label | editor label, e.g. "SKELETON.MDL" | high | - |
| 0x18 | i16[80] | slots | 16 slots x (u8 count 0..3, u8 extra, i16 timer (runtime), i16 sfx[3]); slot 0 alert/move 1 idle growl 2 wound 3 death 4 attack, 5.. attack-anim sounds; sfx = MONSOUND.DAT ids; see slot_list() | high | MonRec_Load_, GetMonSound_, MonsterUpdateMON_ |

Slot use (`GetMonSound_(mon, slot)` picks one of `count` sfx at random): 0 alert/move
(played while hunting, timer at +0x1A), 1 idle growl (timer +0x24, every 10-20 s),
2 wound (`DamageMonster_` path), 3 death, 4 melee attack (`OpFight_`), 5+ played by
`ATQAnimSfx_` for special attack animations. Verified on SKELETON.MDL: 68 move,
69 growl, 70 wound, 72 death, 71 attack (MONSOUND.DAT comments). The byte at slot+1
(25/100 on some records) is not read by any code found (low).

### 3.1 MONSOUND.DAT - SFX table (text)

Read by `sndread.c` at startup: first number = declared maximum (699), then lines
`id,"wavname"[,R]` (`*` starts a comment, `-1` ends). ids index `_gMonSoundName[1024]`
(hard limit 1024); `R` = resident (preloaded), others are loaded on demand
(`LoadNonResidentSound_`). All SFX numbers in the databases (MONSND slots, prop
`sound`, switch/script `sfx`) are ids in this file. `formats/d6data.py` class `SfxList`.

---------------------------------------------------------------------------

## 4. D6PROP.DAT - props, 0x38 bytes, 238 records

Loader: `LoadProp_` (scenload.c) - on demand, cached in `_Prop[256]`: **record index
must be < 256** ("MAX PROPS EXCEEDED") and <= the header count ("INVALID PROP RECNO").
`LoadPropObject_` checks `model <= 0xF9`.

| off | type | name | meaning | conf | source |
|---|---|---|---|---|---|
| 0x00 | char[24] | name | display name | high | DrawTargetName_, traps.c |
| 0x18 | i32 | model | index into exe _PropMDLData (prop/<name>.mdl), must be < 250 | high | LoadPropObject_ |
| 0x1C | i16 | objtype | graph object class -> gobj+0x6C (1 = tree/foliage, else 0) | high | LoadPropObject_ |
| 0x1E | i16 | dragicon | drag icon shown when targeted (0..37, exe _dragiconname) | high | DrawReadyData_ (DrawShadedDragIcon_) |
| 0x20 | u8 | flags | 1 switch, 2 custom scale, 4 collide, 8 terrain-follow 4 corners, 0x10 container (chest), 0x20 snap in BSP, 0x40 anim-texture, 0x80 animated | high | LoadPropObject_, pcorders.c, traps.c, jhpmap.c |
| 0x21 | u8 | flags2 | 1,2 render flags, 4 y-lock sprite, 8/0x10 perspective, 0x20 float, 0x40 smashable (crate/barrel), 0x80 fountain (drink) | med | LoadPropObject_, combat.c (smash), fountain.c, pcorders.c |
| 0x22 | u8 | flags3 | 1 floats on water, 2 can be pushed/dragged (wcoll.c) | high | wcoll.c (float / latch-drag) |
| 0x24 | f32 | scale | float copied to model+0x69C (height/scale) when flags&2 | high | LoadPropObject_ |
| 0x28 | f32 | height | mass (float) for push/drag physics; >0 = movable, disables ground adjust | med | LoadPropObject_, pushy.c, pcorders.c |
| 0x2C | i16 | broken | D6PROP record that replaces a smashed prop | high | combat.c (smash prop, ~l.12001) |
| 0x2E | i16 | smashefx | effect index (_gCrateEfx) when smashed | high | combat.c (_gCrateEfx) |
| 0x30 | i16 | sound | ambient SFX (MONSOUND id) | high | LoadPropObject_, events.c, traps.c |
| 0x34 | i32 | soundparam | ambient SFX radius | med | LoadPropObject_ (SetPropSFX_) |

Hard-coded props: record 0x4E and 0x1D get a particle emitter (type 0x14) in
`LoadPropObject_`; record 0xA8 chest spawns treasure higher; records 0xD/0xE are
special in the smash code. Smashable props: flags2 0x40 + `broken` + `smashefx`
(e.g. Barrel 13 -> Broken Barrel 223).

---------------------------------------------------------------------------

## 5. D6TREAS.DAT - treasure records, 200 bytes, 236 records

Readers: `SetChestTreasure_` (traps.c, chests/traps), `MakeMonInventory_` (monster.c,
via MonRec_Load_), `LoadNPCInventory_` (npc.c). Each lseeks `index*200`; no count check.

| off | type | name | meaning | conf | source |
|---|---|---|---|---|---|
| 0x00 | i32[50] | entries | 10 x (i16 type, i16 chance%, i32 a, i32 b, i32 c, i16 pA, i16 pB); type 1/2 item=DiceRoll(a,b,c), 3 D6TRLIST lists a/b/c, 4 exp; see entries_list() | high | SetChestTreasure_ (traps.c), MakeMonInventory_, LoadNPCInventory_ |

Entry (20 bytes): `i16 type, i16 chance%, i32 a, i32 b, i32 c, i16 pA, i16 pB`.

| type | meaning |
|---|---|
| 0 | unused |
| 1, 2 | item id = DiceRoll(a, b, c) (count a, sides b, bonus c; fixed item = `0,0,id`) |
| 3 | item from treasure lists: list a with chance pA%, list b with pB%, else list c (b,c default to the previous) |
| 4 | experience DiceRoll(a,b,c) shared by the party (`TreasureDivvyExp_`, chests only) |

Monsters take at most 4 items (treasureA first, then treasureB). With high luck (or
trait 0x35) chests roll twice and keep the more expensive item. Data: 598 type-3,
436 type-1, 249 type-2, 39 type-4 entries.

## 6. D6TRLIST.DAT - treasure lists, 0x3C bytes, 62 lists

`LoadTreasureLists_` (treasure.c) reads the u32 count (**max 127**, `_gTreasureList[128]`)
and all records at startup.

| off | type | name | meaning | conf | source |
|---|---|---|---|---|---|
| 0x00 | char[20] | name | list name (editor only) | high | - |
| 0x14 | i16[20] | ranges | 10 (first,last) D6ITEM ranges; first<=0 = unused, last<=first = single item | high | GetTreasureListItem_ |

`GetTreasureListItem_`: pick one of the ranges with first > 0 uniformly, then an item
uniformly in [first, last] (single item when last <= first).

---------------------------------------------------------------------------

## 7. D6NPC.DAT - NPCs, 0x24 bytes, 137 records

`LoadNPC_` (npc.c) lseeks `npc*0x24` when a monster with `MonsRec.npc != 0` enters;
`LoadNPCNames_` (jentry.c) reads names for the journal. NPC dialogue lives in
NPCDATA.PAK slots 1+n / 161+n / 321+n, so **NPC ids are limited to 0..159**.

| off | type | name | meaning | conf | source |
|---|---|---|---|---|---|
| 0x00 | char[24] | name | NPC name | high | LoadNPC_, LoadNPCNames_ |
| 0x18 | u32 | gold | gold carried (npc+0x20; NPCGoldToBag_, trading) | high | LoadNPC_, NPCGoldToBag_, GetNPCSellItemPrice_ |
| 0x1C | u16 | treasureA | D6TREAS record for the NPC inventory (npc+0x68, LoadNPCInventory_) | high | LoadNPCInventory_ |
| 0x1E | u16 | pricepct | price % used when trading (npc+0xAC, GetNPCSellItemPrice_) | med | GetNPCSellItemPrice_, NPCBuyItem_ |
| 0x20 | u16 | trader | >0 enables pricepct (npc+0xAE) | med | GetNPCSellItemPrice_ |
| 0x22 | u16 | treasureB | second D6TREAS record (npc+0x6A) | high | LoadNPCInventory_ |

---------------------------------------------------------------------------

## 8. D6HELM.DAT - helmet attachment per model

`InitHelmData_`: `u32 nModels (24), u32 nHelms (must be 16), i16 modelIdx[128]` (only
the first nModels used), then records of 0x26 at `0x108 + (slot*16 + helm)*0x26`.
`GetHelmData_(model, helm)` finds `slot` with modelIdx[slot] == monster gfx and reads the
record; helm = `ItemRec.range` of the worn helm (0..15). Monsters/PCs whose model is not
in modelIdx show no helmet.

| off | type | name | meaning | conf | source |
|---|---|---|---|---|---|
| 0x00 | f32[3] | pos | attachment translation | med | model.c attach |
| 0x0C | f32[3] | rot | rotation vector (chardemo editor writes _AttachRotVec) | high | chardemo.c |
| 0x18 | f32[3] | scale | scale (_AttachScale) | high | chardemo.c |
| 0x24 | i16 | hair | hair style used with this helmet (TwiddleHair_) | high | SetMonEquipment_, model.c |

---------------------------------------------------------------------------

## 9. Exe-side tables (deep6.exe, mdldata.c) and limits

| table | VA | entries x stride | entry layout | readers / bound checks |
|---|---|---|---|---|
| `_ItemMDLData` | 0x5DB3B8 | 240 x 0x51 | char name[0x50] (`item\<name>`), u8 first _MDLExtend | `LoadItemObject_` (`model > 0xEF` -> error, also item id must be 1..gNumItems), `LoadAttachItemObject_` (`< 0xF0`), `LoadShipWheelAttachObject_` (`< 0xF0`), `PCMLoadAttach_` (`< 0xF0`); models cached in `_gItemModel` |
| `_MonMDLData` | 0x5DFFA8 | 121 x 0x55 | char name[0x50], u8 first _MDLExtend, i32 Model_Read_ param (+0x51) | `LoadMonsterModel_`, `PCMLoadModel_`, `PCMChangeModel_` (index < 0x11 = PC body path), `LoadMonMDLExtend_`/`SetMonMDLExtend_` (monant.c), `PrintModelData_`; **no bound check**; cache `_gModelPtr[134]` where 0x80+pc (128..133) are the PC slots, so gfx must be < 121 (table) and never >= 128 |
| `_PropMDLData` | 0x5E27D5 | 250 x 0x51 | as items (`prop\<name>`) | `LoadPropObject_` (`model > 0xF9` -> error); cache `_gPropModel[250]` |
| `_MDLExtend` | 0x5E76F0 | 64 x 0x54 | char name[0x50] (`EFXGFX\<name>`), u8 kind (0/1 anim texture, 2 alpha sprite chain), u8 next, i16 param | chained from the model tables; kind-2 param indexes `_MDLExAlpha` (0x5E8BF0, 0x14 stride) |
| `_icondata` | 0x5D1840 | 246 x 8 | char *name (`itemicon\%s.bmp`), u8 grid w, u8 grid h | `LoadItemIcons_` (`_gNumItemIcons = 0xF6` hard-coded), ItemRec.icon |
| `_dragiconname` | - | 38 | `dragicon\%s.bmp` | `LoadItemIcons_` (`_gNumDragIcons = 0x26`), PropRec.dragicon |
| `_gSpellData` + table at 0x5D7D9E | - | 105 x 0x30 | spell table (names from D6STRING 9000+id) | spells are compiled into the exe; no spell database file |

All model references located through relocations (`.reloc`, every absolute address use):
LoadMonsterModel_ 0x499D8E/0x499DC9/0x499DD9/0x499E4E, LoadItemObject_ 0x4D5934..0x4D5A0F,
LoadShipWheelAttachObject_ 0x4D5D2A, LoadPropObject_ 0x4D5E71..0x4D5FB6,
LoadAttachItemObject_ 0x4D7631..0x4D76FE, PCMLoadAttach_ 0x4FB57A..0x4FB650,
PCMLoadModel_ 0x4FBDDA..0x4FBE02, PCMChangeModel_ 0x4FC029..0x4FC051, PrintModelData_
0x508868, LoadMonMDLExtend_ 0x58638D..0x586438, SetMonMDLExtend_ 0x58646D..0x586490.
`exe_model_table(root, kind)` in d6data.py dumps them.

### 9.1 Record-count limits

| database | count source | hard limit | where |
|---|---|---|---|
| D6ITEM | header u32, all loaded | 999 (`MAX_ITEMS`) ; also 3-digit `I###` in object lists | `LoadItems_`, `_Item[1000]` |
| D6MONS | none (lseek) | i16 index; `M###` in object lists = 999; 63 distinct records per area | `MonRec_Load_` (`_gMonRec[64]`) |
| D6PROP | header u32 checked | index < 256; `P###` | `LoadProp_` (`_Prop[256]`) |
| D6TREAS | none | i16 index | readers above |
| D6TRLIST | header u32, all loaded | 127 | `LoadTreasureLists_` |
| D6NPC | none | 0..159 (NPCDATA.PAK slots), 127 active NPCs | `LoadNPC_`, `MonsterEntry_` |
| D6MONSND | none | i16 index | `MonRec_Load_` |
| MONSOUND.DAT | declared max | 1024 ids | sndread.c |
| D6HELM | header | 16 helms per model, 128 model slots | `InitHelmData_` |
| models | exe tables | items 240, monsters 121 (<128), props 250 | see above |
| live objects | - | 240 monster slots, 5120 placed objects, 32 hold triggers | `_gMonster`, `_gTerrObj` |

---------------------------------------------------------------------------

## 10. Adding records (what an SDK must do)

1. **Items**: append a 0x11C record to D6ITEM.DAT and bump the header count (<= 999).
   Fill name + unidname (<= 21 chars each), type/subtype, icon (< 246), weight, price,
   model (< 240, existing `_ItemMDLData` entry), skill, damage/ac, durdice, restrict
   mask, flags98 (0x0002 to skip identification), enchantmask. Reference it from object
   lists as `I%03d`, from D6TREAS/D6TRLIST ranges, from shops (D6SMITnn u16 item ids)
   or scripts (CREATEITEM). Item records are global: saves store item ids only, so
   appending is save-compatible; changing an existing id is not.
2. **Monsters**: append a 0x154 record (D6MONS header count is not used by the game,
   but keep it correct for tools). Required: name, gfx (existing model 17..120), soundrec
   (= gfx, or 0), hpdice (level in [0]), hit/parry/armor, abilities, resist, speed,
   atkdelay, xp, equip entries with natural attack items, treasureA/B, groupdice
   (1,1,0 for a single monster). npc = 0 unless a D6NPC record + NPCDATA.PAK slots exist.
   Place via `M%03d` (so index <= 999) or scripts GENMONSTER/SPAWNMONSTER/chest traps.
   Keep <= 63 distinct monster records per area.
3. **Props**: append a 0x38 record, index must stay < 256 and the header count must be
   updated (it is checked). model < 250.
4. **Treasure**: append 200-byte records (no count check, but keep the header right);
   new treasure lists: append to D6TRLIST (<= 127, header count is used).
5. **NPCs**: D6NPC record + NPCDATA.PAK members (code/strings/responses) for id < 160.
6. **Monster sounds**: a new D6MONSND record (index referenced by soundrec) and,
   for new wav files, a new `id,"name"` line in MONSOUND.DAT (id < 1024, raise the
   declared maximum on the first line).
7. **New models / icons / spells** cannot be added through data files: `_ItemMDLData`,
   `_MonMDLData`, `_PropMDLData`, `_MDLExtend`, `_icondata`, the drag icon list and the
   spell table are compiled into deep6.exe with fixed sizes (240/121/250/64/246/38/105).
   Options: (a) replace an unused existing entry's .mdl file under the same name, (b) patch
   the table string in the exe in place (same entry size), or (c) in the OpenDWWandW
   port, make the tables data-driven and raise the limits (and the cache arrays
   `_gItemModel`, `_gModelPtr` (PC slots at 128), `_gPropModel`).
8. Always rebuild with `RecordTable.build()` (count in record 0) and keep untouched bytes;
   run `python3 formats/d6data.py --selftest GAMEDIR`.

## 11. Open questions / low confidence

* Monster fields marked low (0x22, 0x26, 0x78, 0x8C, 0x11A, 0x120, 0x12C) have values in
  the data but no reader was found in the decompile. `InitMONSToMonster_` reads the
  record only at 0x18, 0x28, 0x2C (16 bytes), 0x34, 0x4C (32 bytes), 0x68, 0x7A, 0x7C,
  0x7E, 0x9C, 0xFC, 0x128, 0x131, 0x132. Hardware read watchpoints on the Crypt
  Skeleton record in the running game (two runs, about 45 s of melee each) saw no
  read of any of these fields, nor of item field 0x96 on the Rusted War Axe record.
  They are most likely editor-only data (medium confidence): changing them should
  have no effect in the game.
* Exact meaning of several flag bits (MonsRec flags130/131/132, ItemRec atkflags/
  atkflags2, flags98 0x0080/0x4000/0x8000) is inferred from the tests and the records
  that set them.
* Item 0xBC/0xBE (dmgextra), 0x108, 0x10E, 0xA4, 0x96: copied or present but meaning
  unknown.
* NPC pricepct/trader exact semantics (buy vs sell) - med.
* D6MONSND slot byte +1 and the HelmRec translation are not confirmed by a reader.
