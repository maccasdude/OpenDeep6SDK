# Mod projects

A mod is kept as the set of files it changes, separate from any game install.
Library and command line: `formats/d6mod.py`; editor: **Mod > Mod project**.
Test: `python3 tests/test_mod.py GAMEDIR`.

## Workflow

1. **Working copy.** Keep one unmodified install (the *pristine* game) and
   edit a copy of it:

       python3 formats/d6mod.py workcopy PRISTINE WORK [--link]

   `--link` hard-links the files the game never writes, so a copy costs
   little disk space; files the game writes (saves, journals) are copied.
   Every SDK tool and the editor replace files (write a new file, then
   rename) instead of writing into them, so the pristine game stays intact.
   Other tools must do the same on a linked copy.

2. **Project.** It records the size and SHA-1 of every pristine file (the
   base, `base.json`):

       python3 formats/d6mod.py new MYMOD PRISTINE --name mymod

3. **Edit** WORK with d6edit, TrenchBroom + the compiler, the format tools;
   test with the game started on WORK (File > Test in game, Play from the
   camera).

4. **Capture** what WORK changes against the base into the project:

       python3 formats/d6mod.py status  MYMOD WORK
       python3 formats/d6mod.py capture MYMOD WORK --pristine PRISTINE

   Capture replaces what was captured before, so it always mirrors WORK.
   Runtime files are ignored: `save/`, `JOURNAL.*`, `PCSNAP.*`,
   `D6SEGnn.GAM`, `D6ARCHIV.DAT`, `D6WORLD.DAT`, `ROSTER.DAT`,
   `gameopt.dat`, `default.kbd`, `debug.log`, `maps/*.fog`, `maps/*.mrk`,
   `d6edit_backup/`, `_mods/`, `*.bak` (the game writes them while running,
   found by tracing it). `D6SMIT00.DAT` (shop stock) is game data the game
   also rewrites while playing: status marks it `changed*`; restore it from
   the pristine game before capturing unless the mod edits the shop.

5. **Share and install:**

       python3 formats/d6mod.py pack MYMOD mymod.zip
       python3 formats/d6mod.py install mymod.zip GAMEDIR     (or the project folder)
       python3 formats/d6mod.py list GAMEDIR
       python3 formats/d6mod.py uninstall mymod GAMEDIR

## What a project stores

| entry | when | stored as |
|---|---|---|
| file | new files, and changed files that are not a small same-size edit | `files/PATH` (whole file) |
| patch | same size as the base and at most 10% of the bytes changed (deep6.exe tables, database records) | `patches/PATH.json`: ranges `[offset, old bytes, new bytes]` |
| delete | a base file missing from WORK | nothing |

`mod.json` lists every entry with the base SHA-1 and the result SHA-1.
Without `--pristine` changed files are stored whole (no patches). Patches
keep deep6.exe itself out of the mod.

## Installing several mods

* A whole-file entry installs only over the base version of the file (or
  where the file is missing, for new files); otherwise install stops and
  names the mod that changed it (`--force` replaces it anyway).
* A patch applies when the bytes at its offsets are still the base bytes (or
  already the new ones), so mods that patch different spells, records or
  table entries of the same file can be installed together; overlapping
  patches are a conflict.
* Install keeps the replaced files in `GAMEDIR/_mods/NAME/backup` and a
  manifest (`installed.json`, with the install order). Uninstall restores
  them; a mod installed later that touches the same files has to be
  uninstalled first, and files changed after the install are reported.

## Notes

* Saved games keep a visited spoke's state (`D6SEGnn.GAM` inside the save),
  which hides map edits in that spoke: test map changes with a new game or a
  save made before entering the spoke.
* New spokes, more model slots than the exe tables hold and new engine
  behaviour need OpenDWWandWExpanded; such mods should say so in their
  description.
