# Changelog

## v1.1.0 (2026-10-09)

* **Save games decoded**: `formats/d6save.py` reads and writes `save/gameNN.sav`
  and `ROSTER.DAT` byte for byte, shows the slot info and thumbnail, extracts
  the two archives and edits character fields (`pc0.gold=5000`,
  `pc0.name=...`); an edited save loads in the game. Layout and the
  character record in `docs/formats/saves.md`.
* **Fonts, pointers, palette**: `formats/d6font.py` (D6FNTnn.FNT, render text
  and glyph sheets, colour fonts with their .P16), `formats/d6ui.py` (mouse
  pointer .ptr files, DEEP6.PAL); `docs/formats/ui.md` also covers the clan
  remark packs, the vehicle BSPs and the remaining small files.
* `d6level` self test includes the vehicle BSPs (models/monster/*.bsp).
* `d6efx.py --selftest` works like the other tools.
* Inventory offset in the character record corrected to +0x298.
* Database fields with no reader: hardware watchpoints in the running game
  show they are not read (`databases.md` section 11).
* New in-game checks (`docs/verification.md`): guildmaster news, bank,
  employment and shop buttons; lava and clip brushes from a map; animated
  effect texture edits.

## v1.0.0 (2026-10-08)

First release.

* **d6edit**, the editor: terrain (sculpt, paint, walls, water, light),
  objects with 3D models, foliage, nav graph, exits (with their conditions),
  boxes, triggers, switches, traps, event scripts, NPC and guildmaster
  dialogue, spells, game databases, models (OBJ, glTF, rigged glTF), textures,
  game text, level geometry through TrenchBroom, automap, mod projects, map
  checks, test in game, play from the camera; Standard / EX mode.
* **formats/**: readers and writers for every supported game file, each with a
  byte-exact self test; BSP compiler and decompiler, NPC/GM script assembler,
  event script assembler, exits analysis, mod tool, text export/import.
* **TrenchBroom 2026.2** game configuration, checked in TrenchBroom: editing,
  retexturing, water volumes, lights, new maps; the editor sets TrenchBroom
  up and finds the AppImage.
* **docs/**: an engine and file format reference (`REFERENCE.md` and
  `REFERENCE.pdf`), the format chapters, a tutorial and an in-game
  verification log.
