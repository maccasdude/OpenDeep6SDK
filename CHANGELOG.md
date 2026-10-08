# Changelog

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
