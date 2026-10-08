# Sound, music, speech and the journal

From the decompile (audioc.c, sfxcache.c, soundefx.c, playsam.c, combat.c,
monster.c, jentry.c, guild.c, npc.c). Not yet checked by playing modified
sounds in the game.

## 1. Sound effects (MONSOUND.DAT, H)

`MONSOUND.DAT` (d6data `SfxList`, documented in databases.md) maps ids to
`sounds/<name>.wav`; `R` marks resident sounds (meant to be loaded at start; a
missing resident wav is an error). In the test setup (SDL dummy audio) no
wav was opened before it was first played.

* The first number (the max line, 699) is a real limit: only ids below it
  are loaded. **To add sounds, raise it** (at most 1024; the loader does not
  check, ids >= 1024 overwrite memory). Checked in the game: max 720 and a
  new id 700, used by a monster sound slot (D6MONSND), played its wav.
* Duplicate ids: the last line wins (585-588 are defined twice). Negative ids
  are skipped.
* Engine limits: 700 wave references in total, only handles 0-499 play
  (including speech), 8 instances per wave, 64 cached buffers (purged to 2 MB).
* WAV: canonical RIFF with the `fmt ` chunk at offset 12 (the format is read
  at file offset 0x14), uncompressed PCM. Shipped: mono 16 bit 11025 Hz.
* File names are looked up case-sensitively on Linux.

### Positional playback

`NetMsg_SFX_(id)` plays 2D; `NetMsg_SFX_Ex_(id, mode, owner, pos)` plays in
3D (world units, 1 tile = 1024): volume falls off past a near distance,
pan from the listener direction.

| mode | source | near / range | slider |
|---|---|---|---|
| 0, 8 | 2D (8 = narrator / GM speech) | - | master / speech |
| 1 | monster or PC, followed | 2048 / 32768 | sfx |
| 2 | effect, followed | 2048 / 32768 | sfx |
| 3 | missile | 2048 / 32768 | sfx |
| 4 / 5 | fixed point / no falloff | 2048 / 32768 | sfx |
| 6 | fixed, followed (bell) | 7168 / 262144 | ambient |
| 7 | NPC speech, followed | 2048 / per NPC | speech |
| 9 | ambient point | 2048 / 65536 | ambient |
| 10 | terrain prop | 1024 / the prop's `soundparam` | sfx |
| 11 | event entity | 2048 / 32768 | sfx |

### Which ids the game plays (hard-coded)

| event | MONSOUND ids |
|---|---|
| melee hit (random) | 4, 18, 20, 566-568, 571-574; undead (mclass 2/3) 569, 570, 575; slime/plant (9/10) 577, 578, 575 |
| magic damage | 582, 584, 566, 571, 583 |
| punch | 5, 601, 602 |
| armour absorbs | 1, 16, 17, 21, 2, 19, 563, 564, 565 (by armour) |
| bashing doors | 686, then 1, 16, 17, 21, 2, 19, 563, 564 |
| explosion | 14 |
| PC wounds / death | 630-649 by race and gender (some races 640 + 2 * gender); gasps 660 + 3 * gender + random |
| monster alert, growl, wound, death, attack | D6MONSND slots (databases.md) |
| moving | dirt 689, stone 688, horse walk/run 677/678, lava 685, swim 683 (terrain) / 49 (BSP), underwater 684, ship 695 + 697 |
| panting | 662 male, 665 female |
| falling | scream 675, landing 687 (terrain) / 686 (BSP), splash 50 |
| mounting | horse 676, ship 590, others 696 |
| heal / NPC | 10, 61, 62, 59 |
| bell | 15 |
| doors, props, events | the SFXREC operand of event scripts; prop `sound` + `soundparam` (looping, mode 10) |

There is no per-terrain footstep table.

Ambient sounds (`PlayAmbients_`, one or two every 3-6 s around the camera):
outdoors by time of day (dawn/morning/dusk 30, 31, 612; day 27-29, 616;
night 32-36, 615, 626; spokes 3 and 11 sometimes insects 613/614); inside
BSPs by spoke (5/6/7: 41, 626-629; 2/8/12: 622-625; others 37-41, 617-621,
628, 629). The wav files can be replaced freely; the id lists are exe tables
at 0x5D79E8-0x5D7A58.

## 2. Music (playsam.c, Miles MP3 streams)

`_musicName[8]` (0x5E9A00): 0 daytime1, 1 intense1, 2 suspense1,
3 suspense2, 4 eerie1, 5 caution1, 6 theme1, 7 game_new (`.mp3` in the MUSIC
folder of SSPATH.DAT). Each track plays once; when it ends: in town and menus
5 s of silence then daytime1; in a spoke 120 s of silence, then the spoke's
track and intense1 alternate:

| spokes | tracks |
|---|---|
| 0, 3, 11 | daytime1 / theme1 |
| 1, 8 | eerie1 / intense1 |
| 2, 7 | suspense1 / intense1 |
| 4, 9 | suspense2 / intense1 |
| 5, 10 | caution1 / intense1 |
| 6, 12 | game_new / intense1 |

No combat or day/night music. Replace the mp3 files under the same names;
more tracks need an engine change.

## 3. Speech (soundefx.c)

Folders from SSPATH.DAT (`SPEECH`, `NARRATOR`), language prefix `E`:

| kind | file | when |
|---|---|---|
| NPC | `Speech/NNN-1611wav/EC<msg:4><seg:2>_<npc:3>.wav` (NNN = NPC id) | every SAY of string msg; 3D at the NPC |
| guildmaster | `Kgg-1611wav/EK<msg:4><seg:2>_<gm:3>.wav` | GM messages (only GM 21 ships) |
| narrator | `000-1611wav/EN<line:6>_<seg:3>.wav` | quest texts and dialogs |

The segment number counts up from 00 after each file; playback stops at the
first missing file, and missing files are skipped silently. **So a voice can
be added to any NPC line by dropping a wav with the right name** (msg = the
string index in the NPC's string table, see npc.md). Queue: 32 lines.
Checked in the game: a new Traveler line s64 opened
`Speech/001-1611WAV/EC006400_001.WAV` when said (file names are matched
without case on Linux).

## 4. Journal (JOURNAL.nnn, H)

Reader/writer: `formats/d6journal.py` (all shipped files round trip; `add()`
appends an entry).

| off | type | meaning |
|---|---|---|
| 0x000 | i32 | next free stream offset |
| 0x004 | (i32 first, i32 last)[192] | chain per key: 0-159 NPC id, 160-191 = 160 + guildmaster |
| 0x604 | 1 KB blocks | entries: `i32 next` (0 = end), `i16 msg` (string index of that NPC/GM), `char text[]` (cached prompt, starting with `|`) |

Entries never cross a 1 KB block. Every NPC message is journaled
automatically for the PCs talking to that NPC (checked in the game; the
journal page shows the raw string, so `$N` stays unexpanded there, and the
file is written when the game flushes the journals, not per message); guildmasters journal after
GM op JOURNAL (title) until CLRJOURNALFLAGS. Quest texts are therefore the
dialogue strings themselves; there is no separate quest file.

Quest flags: 256 per PC, set by NPC ops SET/ADD/SUBQFLAG, GM ops 23-25 and
the event ops SETQFLAGIF / PCBLESSING; read by the QFLAG expressions. Their
meaning lives only in the scripts.
