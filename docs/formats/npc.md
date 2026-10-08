# NPC and guildmaster dialogue (NPCDATA.PAK, GMDATA.PAK)

Reverse engineered from `ExecuteCode_` / `EvalExpr_` (npc.c) and
`GMExecuteCode_` / `GMEvalExpr_` (guild.c). Library and assembler:
`formats/d6npc.py` (`--selftest` disassembles and reassembles all 137 NPC and
21 guildmaster scripts byte for byte and rebuilds both PAKs identically).
Editor: Tools > NPC dialogue (Ctrl+Shift+D).

Confidence: the operand layouts are **H** (every shipped script decodes with
all jump targets on instruction boundaries); meanings of opcodes used in the
data are **M**; opcodes never used in the data are **L** (layout from the
decompile only). Checked in the game (Traveler, NPC 1, spoke 0): a SAY of a
new string inserted at the start of the main path (later addresses all
shift), `$N` replaced by the PC name, an edited REPLY branch reached through
ONREPLY with GIVEGOLD 77 (gold 200 -> 277), and the journal entries.

## 1. Files

`Pak` layout (d6data.Pak): a directory of `(u32 offset, u32 size)` pairs whose
length is the first offset.

* NPCDATA.PAK, 481 slots: 0 = lexicon, 1+n = code of NPC n, 161+n = strings,
  321+n = keywords (n = 0..159, the monster field `npc` / D6NPC record n).
  137 NPCs have scripts.
* GMDATA.PAK, 97 slots: 0 = GM lexicon, 1+n code, 33+n strings, 65+n
  response table for 32 guildmasters (21 used). The response table is not a
  string table: `u32 count, count x {i16 key, u8 type, u32 offset}` plus the
  strings (GMFindResponse_); it serves GM talk, which the game never sends,
  and all shipped copies are the same dummy. d6npc keeps it byte for byte
  and shows it as a comment.
* String and keyword tables: `u32 count, u32 offset[count]` (from the table
  start), NUL-terminated Latin-1 strings. `$N` = name of the PC talking, `@`
  starts a new message, `~word~` marks a keyword.
* Lexicon (slot 0): `u32 count, u32 string base, count x {u32 offset, u8 type}`
  (5-byte entries): the words of the talk keyword panel.
* D6NPC.DAT record n-1 holds NPC n's name, gold, inventory treasure and
  trading price (databases.md).

## 2. Execution model

A code blob has no header. Every activation (greeting, talk, reply, event)
starts at offset 0; the shipped scripts begin with

    IF NPCFLAG(0) == 1 / JUMP init / JUMP main

`init` registers the event handlers with ONACT and sets flag 0. Blobs end with
END and one pad byte.

* IF / IFNOT / IFKEY: when true the next 3 bytes are skipped. They are
  always a JUMP, so `IF c / JUMP then / JUMP else` reads as written.
* SAY, REPLY, WAIT, BYE, LEAVE yield: the interpreter returns and continues
  after the message has been shown / the player answered / the time is up.
* REPLY prompt, [options] shows up to 16 answer buttons; the engine uses only
  the first option index and the count, so the options must be consecutive
  strings. It is always followed by ONREPLY with one address per answer.
* ONACT slot, address sets the handler of a game event: 0 attacked, 1 PC
  leaves, 2 used / fought with, 3 steal attempt, 4 item given, 7 gold given,
  9 talk (keyword typed or clicked), 11 all PCs gone (8 and 10 are internal:
  reply and resume).
* NPC script flags: 32 shorts per NPC (`npc+0x24`). Per PC and NPC: a 32-bit
  register (`pcreg[pc][npc]`) and an attitude (-100..100). Per PC: 256 quest
  flags (`qflag`). World: `_WState[spoke*256+n]` (the same states the event
  scripts use).

`who` operands: -1 the PC talking, -2 this NPC, -3 its target, 0-5 a party
slot, 6+ a monster index.

## 3. NPC opcodes (ExecuteCode_)

Operand types: `i16`, `u8`, `addr` (u16 offset), `str` (string index),
`rsp` (keyword index), `expr` (u8 kind + its operands, section 4), `val`
(u8 0 + i16 constant, or u8 1 + expr), `cmp` (u8: 0 ==, 1 !=, 2 <, 3 >).

| op | mnemonic | operands | meaning |
|---|---|---|---|
| 01 | END | | stop |
| 03 / 04 | IF / IFNOT | expr, cmp, val | skip 3 when true / false |
| 07 | JUMP | addr | |
| 08 | SAY | str | NPC says the string (yields) |
| 09 | REPLY | u8 n, str prompt, str opt[n] | answer buttons (yields) |
| 0A | ONREPLY | u8 n, addr[n] | jump by the chosen answer |
| 0B | SETFLAG | i16 f, val | npcflag[f] = v |
| 0C / 0D | GIVEITEM / TAKEITEM | i16 item | |
| 0E / 0F | GIVEGOLD / TAKEGOLD | i16 amount | between the PC and the NPC's purse |
| 14 | ATTEND | | the NPC turns to the PC |
| 15 | BYE | | end of conversation (yields) |
| 18 | ONACT | u8 slot, addr | event handler |
| 1A | SETPCREG | i16 bit, i16 v | pcreg[PC][this] bit |
| 1B | SETWSTATE | i16 spoke, i16 n, i16 v | world state |
| 1C | MODPC | i16 who, i16 kind, val | kind HP (0 = full heal), MAXHP, ABIL+, ABIL-, TRAIT+, TRAIT-, SPELL+, SPELL-, SKILL+100, POISON |
| 1D | SETPCREGOF | i16 who, i16 bit, i16 v | |
| 1E / 1F | ADDATTITUDE / ADDATTITUDEOF | [i16 who,] val | |
| 20 | IFKEY | rsp | talk text matches the keyword (`&` = wildcard, `$BLANK$` = anything); skip 3 |
| 21 | WAIT | i16 ms | (yields) |
| 22 | LEAVE | i16 | the NPC leaves (yields) |
| 23 | SETPARTYREG | i16 bit, i16 v | for every linked party PC |
| 24 | CLEARWAITS | | |
| 25 | GOTO | i16 bsp, i16 nav id | walk to a nav point (bsp -1 = terrain) |
| 26 | SETMODE | i16 | NPC behaviour mode |
| 27 / 28 / 32 / 45 | SETBIT20 / SETBUSY / SETBIT8 / SETBIT4 | i16 on | monster flag bits (SETBUSY defers talking) |
| 29 | CLEARTARGET | | |
| 2A | CAST | i16 spell item | at the target |
| 2B | FIGHT | | attack the target |
| 2C | FINDTARGET | i16 kind, i16 | |
| 2D | ONEXPR | u8 n, expr, JUMP default, addr[n] | switch |
| 2F / 30 | ADDFLAG / SUBFLAG | i16 f, val | |
| 31 | GIVENPCITEM | i16 item | from the NPC's inventory |
| 33 | KILLMSGQ | | |
| 34 | SUICIDE | | |
| 35 / 36 | SETNPCREG / SETPARTYNPCREG | i16 npc, i16 bit, i16 v | pcreg of another NPC |
| 37 / 38 / 39 | SETQFLAG / ADDQFLAG / SUBQFLAG | i16 who, i16 q, val | quest flags |
| 3A / 3B | SETTRADE / TRADE | i16 / - | allow / open the trade window |
| 3C | SETSELL | i16 item, i16 on | NPC sell list (8 items) |
| 3D / 3E | SETE8 / SETEC | i16 | unknown NPC fields |
| 3F | ADDPCLIST | | remember the PC |
| 40 | SETGROUPREG | i16 bit, i16 v | |
| 41 | SETPARTYREGIFQ | i16 bit, i16 v, i16 q, i16 qv | |
| 42 | TAKEPARTYITEM | i16 item, i16 once | |
| 43 | GIVEEXP | i16 who, val | experience (who >= 0: party) |
| 46 | TAKEPARTYGOLD | i16 amount | |
| 47 | SETMBIT | i16 bit, i16 v | global per-NPC bits |

Placeholders that only consume operands: 10 (i16,i16), 13 / 17 (i16), 16,
19 (i16,i16,i16), 44 (i16,val); 11 and 12 report "not implemented".

## 4. NPC expressions (EvalExpr_)

NPCFLAG(f), RANDOM(n), STAT18/STAT1A/STAT1C/STATF2(who) (unnamed short
fields), STATE(who) (alive/dead), NPCTIMER(npc), ZERO4(a,b), PCREG(bit),
WSTATE(spoke,n), HASITEM(item), NOITEM(item), GOLD(who), ACTITEM(),
ACTGOLD() (what was just given), PCREGOF(who,bit), ATTITUDE(),
ATTITUDEOF(who), ATHOME(), INTALK(who), FIELD476(), TARGET(), TARGETISPC(),
TARGETDIST(), HPLOST(who), HP(who), MAXHP(who), INRANGE(who), NPCBIT4()
(busy), NPCHASITEM(item), PARTYHASITEM(item), NPCREG(npc,bit),
QFLAG(who,q), PARTYQFLAG(q,v), HASTRAIT(who,t), MBIT(bit).

## 5. Guildmasters (GMExecuteCode_ 0x54c834, GMEvalExpr_ 0x54bc1c; H unless marked)

Every operand layout in d6npc (GMOPS, GMEXPR) was checked against the
interpreter, including the opcodes no shipped script uses.

### 5.1 Who is who

Each building reads its GM from a per-town table in the exe (`_gHallGM`,
`_gSmitGM`, `_gTempleGM`, `_gMageGM`, `_gTavernGM`, `_gDojoGM`, `_gPawnGM`,
`_gYardGM`), so the ids are fixed:

| GM | town | building | name |
|---|---|---|---|
| 1-7 | Valeia | hall, temple, smith, mage, tavern, dojo, pawn | Sir Elgar, Onabe, Smitty, Roendalf, (tavern), Master Wu, Bratsol |
| 8-12 | Ishad N'ha | hall, temple, smith, mage, tavern | Lord Barrenhawk, Munsey, Damosh, Xander, (tavern) |
| 13-19 | Brimloch Roon | dojo, pawn, hall, temple, smith, mage, tavern | Sinsei Asami, Miruth, Duke Brinsly, Malakai, Strumbold, Sabastio, Holthorne |
| 20 | - | ship yard | Buckly |
| 21 | - | Gareth's intro (InitGareth_) | Gareth |

The dojo and pawn tables hold 6 and 7 for Ishad N'ha as well (M: whether
that town has those buildings was not checked).

### 5.2 How a GM script runs

* Entering the building: the script runs from offset 0 with PC 0 (`GMFLAG(0)`
  is 0): the *init* path sets ONACT 6 (and 0, 4) and flag 0, then ENDs.
* Greeting: whenever a PC steps up (entry, party portrait, prev/next), the
  script runs again from offset 0 for that PC: the *main* path.
* Buttons: every button and item action runs ONACT 6 with `ACTBUTTON()` =
  the code and `ACTITEM()` = the item (0 for plain buttons). This is the
  only event slot that fires: 0 (PC leaves) and 4 (talk) are set by the
  shipped scripts but nothing sends them, so GM talk, IFKEY and the
  response table are dead in the shipped game.
* SAY, REPLY and WAIT yield; the script resumes after them. Every event
  starts a new run, so a button pressed during a SAY starts a second flow
  (the shipped Leave handlers use CLEARWAITS + PURGEMSGQ for that).
  A SAY of an empty string never resumes.
* GMFLAGs reset on every visit. Lasting state lives in QFLAG (per PC),
  NPCREG (per PC, GMs use 161..166), ATTITUDE, WSTATE, UBIT (32 global bits,
  saved), GUILDRANK, ROLEINIT and the timers.
* "Local" means the party members present at the building.

### 5.3 Opcodes

01-0F as for NPCs (section 3), with these differences:

| op | mnemonic | operands | meaning |
|---|---|---|---|
| 0E / 0F | GIVEGOLD / TAKEGOLD | i16 | to / from the PC (GMs have no purse) |
| 10, 12, 1D, 34 | NOP | as listed in d6npc | no effect |
| 11 | ONACT | u8 slot, addr | slot = event: 6 button (the only one sent), 0 PC leaves, 4 talk |
| 13 | SETNPCREG | i16 reg, i16 bit, i16 v | pcreg[PC][reg] bit |
| 14 | SETWSTATE | i16 spoke, i16 n, i16 v | world state (v is a constant) |
| 15 | MODPC | i16 who, i16 kind, val | kinds 1 HP (not capped while alive), 2 MAXHP, 3 ABIL+, 4 ABIL-, 5 TRAIT+, 6 TRAIT-, 7 SPELL+, 8 SPELL-, 9 SKILL+10 (+10, not +100); no POISON |
| 16 | SETNPCREGOF | i16 who, i16 reg, i16 bit, i16 v | for PC who |
| 17 / 18 | ADDATTITUDE(OF) | [i16 who,] i16 npc, val | -100..100 |
| 19 | IFKEY | str | whole-string match, `&` = prefix wildcard, no `$BLANK$` (talk only, never sent) (M) |
| 1A | WAIT | i16 ms | yields; ignored when 16 waits are queued |
| 1B | SETLOCALREG | i16 reg, i16 bit, i16 v | for every local PC |
| 1C | CLEARWAITS | | drops pending waits and greetings, journal flags off |
| 1E | ONEXPR | u8 n, expr, JUMP default, addr[n] | switch |
| 20 / 21 | ADDFLAG / SUBFLAG | i16 f, val | |
| 22 | PURGEMSGQ | | drops queued messages, ends reply mode, journal flags off |
| 23-25 | SET/ADD/SUBQFLAG | i16 who, i16 q, val | who -1 PC, -2 every local PC, 0-5 slot |
| 26 | EXIT (was ENDGAME) | | `_gGameState = 2`: leaves the building (or ends Gareth's intro); the script goes on |
| 27 | SETGUILDRANK | i16 guild, val | 0 smith, 1 mage, 2 temple, 3 pawn, 4 dojo; ranks 1-7 |
| 28 | SETROLEINIT | i16 role, val | 0 none, -1 role quest running, 1 granted |
| 29 | SETROLE | val | class change: keeps the old level, role trait and starter spells, exp 0, level 1, drops unusable items |
| 2A | JOURNAL | str | journal title (key 160 + GM id) and journaling on: following SAYs are journaled |
| 2B | CLRJOURNALFLAGS | | journaling off |
| 2C / 2D | TIMER0 / TIMER1 | i16 on | start (reset) / stop the per-PC timers (who advances them: not found, M) |
| 2E | TAKEQTY | i16 item, i16 n | |
| 2F | JOURNALALL | i16 on | journal for every local PC (always on for GM 21) |
| 30 | TAKELOCALITEM | i16 item, i16 once | once > 0: one item from the first local PC; else every copy |
| 31 | TAKELOCALQTY | i16 item, i16 n | n units over the local PCs |
| 32 | ADDSHOPITEM | i16 shop, i16 item | +1 stock in the open building (shop = town); an unlimited item becomes 1 |
| 33 | GIVEEXP | i16 who, val | who < 0: the PC, else every local PC (no level-up here) |
| 35 | SETUBIT | i16 bit, i16 v | global unique bits (saved) |

Opcodes 00, 02, 05, 06, 1F and > 0x35 print "COMMAND ERROR".

### 5.4 Expressions

GMFLAG(f), RANDOM(n), GENDER(who), CLAN(who), ROLE(who) (who -1 is broken
in the exe: it reads PC -1; use 0-5), ALIGNMENT(who) (0-100, 50 for
who > 5), STATE(who) (3 = dead), NPCSTATE(npc) (`_gNpcStatus`, M), ZERO4,
NPCREG(reg,bit), WSTATE(spoke,n), HASITEM(item), NOITEM(item), GOLD(who),
ACTITEM() (item of the last item button), ACTGOLD() (NPC VM value, 0 for
GMs), NPCREGOF(who,reg,bit), ATTITUDE(npc), ATTITUDEOF(who,npc), ZERO13,
ZERO14, LOCALHASITEM(item), ACTBUTTON(), QFLAG(who,q), GUILDRANK(guild),
ROLEINIT(role), GTIMER(0) (timer 0 in hours) / GTIMER(1) (raw),
QTY(item), LOCALQFLAG(q,v) (v 0: no local PC has it; else any has v),
LOCALQTY(item), UBIT(bit). The assembler still accepts the old names
STAT146, STAT148, STAT220, NPCTIMER and ENDGAME.

### 5.5 Buttons (ACTBUTTON)

| building | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| Hall | news | bank | leave | | | |
| Tavern | news | ale | leave | | | |
| Smith | buy | sell | repair | identify | guild | leave |
| Mage | buy | sell | enchant | identify | guild | leave |
| Temple | rites | blessing (M) | donate | guild | leave | curse lifted |
| Dojo, Pawn | buy | sell | guild | leave | | |
| Ship yard | browse | warship bought | quest | leave | | |

Results: 700 / 701 / 702 / 703 item bought / sold / repaired / identified
(ACTITEM = item), 710 / 711 bank deposit / withdraw, 800 + role train role,
820 quest button, 821 / 822 guild specials yes / no, 850 + ability ability
trained, 865 skill or trait trained, 900 not enough gold / cannot join,
901 already donated, 902 no rites needed, 903 item not usable, 904 nothing
to repair, 905 already identified, 909 cannot carry, 910 no more drinks,
1000 + n drink served.

Roles: 0 Warrior, 1 Wizard, 2 Priest, 3 Rogue, 4 Ranger, 5 Bard, 6 Samurai,
7 Paladin, 8 Barbarian, 9 Monk, 10 Ninja, 11 Warlock, 12 Assassin,
13 Zenmaster, 14 Valkyrie.

Editor: Tools > NPC dialogue, *Guildmasters*; "New from template" gives a
hall-style script (greeting, news, bank, leave).

## 6. Adding an NPC

1. D6NPC.DAT record for the id (name, gold, inventory, prices): Game
   databases.
2. A script in the NPCDATA.PAK slot (NPC dialogue, "New from template").
3. A monster record with field `npc` = the id, placed in a spoke.
Ids are limited to 0..159 by the PAK layout.
