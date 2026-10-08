# OpenDeep6SDK v1.0.0

Modding tools for **Wizards & Warriors** (Heuristic Park, 2000) and its Deep6
engine. The game never had a released SDK; this is one, built from the
reverse-engineered file formats.

It works on the data files of the game folder, so maps edited here run in the
original game and in the OpenDWWandW / OpenDWWandWExpanded ports.

## Contents

| | |
|---|---|
| `editor/` | **d6edit**, the editor: terrain (sculpt, paint types, walls, water, light), objects with their 3D models, foliage, nav graph, exits, boxes/triggers/switches/traps, event scripts, NPC dialogue, spells, game databases, models (OBJ / glTF, rigged glTF), textures, game text, level geometry (TrenchBroom), automap, mod projects, map checks, test in game and play from the camera. See `editor/README.md`. |
| `formats/` | Python readers/writers for the game files; every supported file round-trips byte for byte. Also the BSP compiler/decompiler (`d6bspc.py`, `d6bspdc.py`), NPC script assembler (`d6npc.py`), automap renderer (`d6automap.py`) and mod tool (`d6mod.py`). |
| `docs/` | **`REFERENCE.md` / `REFERENCE.pdf`: the engine and every file format in one document** (built from the chapters below), `engine.md` (how the engine fits together), `formats/` (file formats: levels, terrain, models, databases, NPC scripts, effects, audio, exits...), `tutorial.md` (a first mod, step by step), `mods.md` (mod projects), `geometry.md` (TrenchBroom workflow), `verification.md` (what was checked in the game), `exe_modules.md` (deep6.exe module map). |
| `tools/` | TrenchBroom game configuration, compiler build script (ericw-tools), `reference/build_reference.py` (rebuilds REFERENCE.md/.pdf). |
| `tests/` | Workflow tests: `test_mod.py`, `test_skin_import.py`, `lightmap_vs_retail.py`, `make_testmap.py`. |

## Quick start (Ubuntu / Debian)

    sudo apt install python3-pyqt6 python3-opengl python3-numpy python3-pil libxcb-cursor0
    python3 editor/d6edit.py /path/to/game

Or `./d6edit.sh /path/to/game`, which uses a `.venv` with the packages of
`requirements.txt` when the system packages are missing.

## Standard and EX

The editor's **Mode** menu targets either the original game (*Standard*:
deep6.exe, or the faithful Linux port OpenDWWandW) or the expanded engine
(*EX*: OpenDWWandWExpanded). Features that need the expanded engine are
disabled in Standard mode; mods record which mode they were made for.

## Windows

Install Python 3.10+ from python.org, then run `d6edit.bat` (it sets up a
`.venv` with PySide6, PyOpenGL, numpy and Pillow on the first run). All
format tools run with `python formats\...`. Hard-linked working copies
(`d6mod.py workcopy --link`) need NTFS; elsewhere files are copied.
The level compiler: `tools\compiler\build.ps1` builds qbsp.exe and
vis.exe (Visual Studio 2022 + vcpkg); the CI workflow
(`.github/workflows/ci.yml`) builds them on GitHub's Windows runners too.
*Test in game* starts the original `deep6.exe` by default; *Play from the
camera* needs OpenDWWandWExpanded, which runs on Linux only for now. The
Windows parts have not been tried on Windows yet.

Format self tests (parse and rewrite every file, compare bytes):

    python3 formats/d6level.py --selftest /path/to/game
    python3 formats/d6terrain.py --selftest /path/to/game
    python3 formats/d6data.py --selftest /path/to/game
    python3 formats/d6model.py --selftest /path/to/game
    python3 formats/d6events.py --selftest /path/to/game
    python3 formats/d6mdlio.py --selftest /path/to/game     (rebuild every model)
    python3 formats/d6textio.py selftest /path/to/game     (tables as text and back)

Mod projects (details in `docs/mods.md`):

    python3 formats/d6mod.py workcopy /path/to/game work --link
    python3 formats/d6mod.py new mymod /path/to/game --name mymod
    ... edit work/ ...
    python3 formats/d6mod.py capture mymod work --pristine /path/to/game
    python3 formats/d6mod.py pack mymod mymod.zip

Command line tools: `formats/d6mdlio.py` (model export/import),
`formats/d6tilegen.py` (terrain type tiles), `formats/d6icons.py` (item icons),
`formats/d6exits.py` (list exits), `formats/d6walls.py` (walls/canopy),
`formats/d6bspc.py` (compile TrenchBroom maps), `formats/d6npc.py` (NPC scripts),
`formats/d6automap.py` (redraw automaps), `formats/d6mod.py` (mods).

Model preview from the command line:

    python3 formats/d6model.py --render /path/to/game M 1 skeleton.png

## License

MIT, see `LICENSE`. No game data is included; you need your own copy of the
game (GOG).
