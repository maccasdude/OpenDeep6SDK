# In-game verification log

What has been checked by running the game (OpenDWWandW on Linux, the
"wide test" save, a single level 1 warrior) and what is still open. The
format docs carry the details; this page is the overview.

## Checked in the game

| feature | how | result |
|---|---|---|
| Model writer (641 models rebuilt), glTF/OBJ import | rebuilt models, imported trees | render and animate |
| Terrain sculpt, paint, walls, snow types | edited spoke 0 | as edited |
| BSP compiler (TrenchBroom map -> .bsp, .lf/.ls, .nvs) | test level in place of crypta | geometry, collision, door, lightmaps |
| BSP decompile -> recompile | CRYPTA round trip | looks like the original |
| New BSP level placed through the TOL | spoke 0, with a water pool | enters, water works |
| NPC script assembler (d6npc) | Traveler: new string, inserted SAY, edited reply branch, GIVEGOLD | as written, `$N` expanded |
| NPC speaker name | monster record renamed through text export / import | message bar shows the new name |
| Journal (NPC lines) | Characters > Traveler page | edited lines listed |
| NPC MODPC SPELL+ | Traveler teaches spell 20 | "Red learns ... spell!" |
| Spell names (D6STRING 9000 + id - 1) | renamed Burn | new name in the learn message |
| NPC speech file naming | wav for a new line, file opens traced | opened on SAY |
| Skinned glTF import | rig in place of the Traveler's model | renders, walk animation plays |
| MONSOUND.DAT limit raised, new id | id 700 in a D6MONSND slot | wav opened when played |
| Record field assignment (`rec.field = v`) | d6data | now writes the field (was silently ignored) |
| Mod workflow (d6mod) | linked working copy of the full game, played under strace; Traveler script + spell edit captured, installed into a second copy, uninstalled | game-written files found and excluded; pristine game unchanged; install equals the working copy; uninstall restores |
| Automap redraw (d6automap) | spoke 0 map tab, against the original | aligned; style differs on BSP areas |
| Guildmaster script (GMDATA.PAK) | Sir Elgar's script disassembled, line edited, reassembled; entered through the Valeia gate and Town Hall (OpenDWWandWExpanded) | edited line spoken; Leave button (ACTBUTTON 2 -> EXIT) leaves the hall |
| Spoke change with BSP rebase (d6exits) | spoke 8 exit T3 (lever W1, party in box B2) to spoke 11 | PC moved by (157696, 13056, -34816) = origin 11/3 - origin 8/1 - (0, 256, 0), exactly as documented |
| TrenchBroom 2026.2 editing | CRYPTA start corridor: wall retextured in the material browser, water volume (texture + content flag), light recoloured, new light from the entity browser; compile + install | skull wall, water surface and red light seen in the game (before/after screenshots); File > New template compiles |
| TrenchBroom 2026.2 round trip | editor: set up project, decompile TCRYPT, open in TrenchBroom (AppImage), duplicate a roof beam 512 units up, save in TrenchBroom, compile + install from the editor | configuration loads (after fixes), map opens with the right game, compiled level contains the beam, game loads it; the beam itself not seen in the game (night, forest) |
| Party entry placement trig (d6exits) | save position against the formula | z 83453.9609375 matches the exe's sin/cos(i*6.28/1024) tables (2 pi gives 83456) |

## Checked against the retail data

| approximation | check | result |
|---|---|---|
| Lightmap extents (smin, tmin, w, h) | all faces of crypta and minesb from the retail BSPs | 21743/21743 equal |
| Light model | retail BSP + lights relit, every luxel against the retail .ls | RMS error 5.7 (crypta) and 4.0 (minesb, not used for the fit) of 30; old defaults 16 and 18 |
| Vertex light / leaf object light | same, against the retail BSP lumps | own fit (ambient + 12.4, peak x 0.43): vertex RMS 7.1 / 8.5 (was 10.2), leaf light 4.4 / 7.1 |
| `InBSPArea_` / spoke BSP lookup in d6exits | terrain leaf tree (TerBSP_Calculate_) ported; 39000 random positions (terrain and dungeon spokes) against the exe rules | 0 mismatches |

## Not yet checked in the game

* Skinned glTF from a real Blender export.
* Spell table numbers (mana, levels, recovery: needs a caster; the game maps
  the patched exe, so the bytes arrive). The spellbook pages need magic skill.
* Hearing sounds/music (the test setup has no audio device; file opens can
  be traced).
* emitters.dat / .ant / .alf edits (the files are loaded at start, traced;
  the look was not checked: no torch or candle near the test position).
* Guildmaster buttons other than Leave (news, bank, shop results).
* Rotating door pivot keys.

## Notes for testing

* Do not move the PC by writing its position (0x628380) while it is on the
  terrain: the object grid lists are not updated and the game later loops
  forever in the path code (FindNearestObstruction_, GatherAmmoInRadius_).
  Loading a spoke after poking the position (\_gSegLoadSeg) can hang the
  same way. Safe ways: walking (held arrow keys), or editing the save: the
  PC position is stored twice in game00.sav (x, y, z floats, the second with
  y + 832; search for the current x), and the game snaps y to the ground on
  load. (game00.sav starts like D6ARCHIV.DAT, version 0x140, but its header
  fields differ; not decoded.)
* `DW_BIN="strace -f -e trace=openat -o FILE .../opendwwandw" dwstart.sh`
  shows which data files the game opens.
* NPCs approach on their own when their script says so; the Traveler starts
  talking when the PC reaches the road east of the start (about x 241000,
  z 70000).
