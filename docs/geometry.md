# Level geometry: TrenchBroom -> Deep6

The indoor levels (BSPs) of Wizards & Warriors can be edited in
[TrenchBroom](https://trenchbroom.github.io/) and compiled back into the
game. The SDK pieces:

| piece | what it does |
|---|---|
| `tools/compiler/build.sh` | builds qbsp + vis (ericw-tools 2.0) with the Deep6 collision hulls into `tools/compiler/bin` |
| `tools/trenchbroom/Deep6/` | TrenchBroom game configuration (`GameConfig.cfg`, `deep6.fgd`) |
| `formats/d6bspdc.py` | decompiler: level -> `.map` + textures as PNG |
| `formats/d6bspc.py` | compiler driver: `.map` -> `.bsp .twd .lf .ls .lgt .rgb .nvs .l2n`, install, TrenchBroom setup |
| `formats/d6map.py` | `.map` reader/writer (Valve 220 and Quake 2 Valve faces) |
| editor: Tools > Level geometry (Ctrl+Shift+G) | the same steps with buttons |

## Setting up

    sudo apt install git cmake build-essential libtbb-dev libembree-dev
    tools/compiler/build.sh
    python3 formats/d6bspc.py setup-trenchbroom ~/Deep6Maps /path/to/game

`setup-trenchbroom` copies the game configuration to `~/.TrenchBroom/games/Deep6`
(Windows: `%APPDATA%\TrenchBroom`, macOS: `~/Library/Application Support/TrenchBroom`)
and exports the textures of every level to `~/Deep6Maps/textures/<level>/`
(plus `_special/` skip, clip, nodraw, hint, water, lava). It also sets
TrenchBroom's game path (Preferences > Games > Deep6 (Wizards & Warriors) >
Game path) to `~/Deep6Maps` in TrenchBroom's `Preferences.json`; close
TrenchBroom before running it, as TrenchBroom rewrites that file on exit.
Run it again after updating the SDK: it replaces an older copy of the
configuration, which TrenchBroom 2026.2 may refuse to load.

TrenchBroom: the official Linux build is `TrenchBroom.AppImage`. The editor
finds it on the PATH or in `~/Applications`, `~/Downloads` (also in
sub-folders), `~/bin`, `~/.local/bin`, `~/Desktop` or `/opt`, and marks it
executable if needed; otherwise set *TrenchBroom command* in the dialog.

## Editing an existing level

    python3 formats/d6bspdc.py /path/to/game crypta ~/Deep6Maps/maps/crypta.map
    trenchbroom ~/Deep6Maps/maps/crypta.map
    python3 formats/d6bspc.py compile ~/Deep6Maps/maps/crypta.map --name crypta \
        --out ~/Deep6Maps/build --game /path/to/game --install

`--install` copies the result into the game folder; the files it replaces go
to `<game>/d6edit_backup/<date-time>-<level>/` (and stale `.lfs/.lss`, which the
compiler does not write, are moved there). The objects (`.bol`) and the
scripting tables are not touched. When `<level>.nvs` exists in the game, its
nav points are kept (events and `D6LINKnn.DAT` use their ids) and only the
leaf table (`.l2n`) is rebuilt; `--newnav` makes a new graph instead.

Decompiled maps start with `// Game: Deep6 (Wizards & Warriors)`, so
TrenchBroom opens them with the Deep6 configuration without asking. The
brushes that are only solid space around the level (every face
`_special/nodraw`) are put in the layer *Outer hull (nodraw)*, hidden and
locked, so they do not box in the view or catch clicks; the compiler treats
layer and group brushes (`func_group`) as world brushes. TrenchBroom's camera
starts at the origin: Ctrl+A, Ctrl+U (select all, focus) brings the level
into view, Esc clears the selection.

The decompiler turns every solid leaf of the BSP into a brush and splits
brushes where the texture or its alignment changes, so the look survives the
round trip (checked on CRYPTA: 4457 of 4463 matched faces have the same
texture and alignment; walked through it in the game). The brushes are the
compiler's split pieces, not the designers' brushes: expect a few thousand
small brushes per level.

## Making a new level

TrenchBroom: File > New, game Deep6, starts from
`tools/trenchbroom/Deep6/initial.map`: a sealed 768 x 768 x 256 room with
CRYPTA textures, one light and a player start, which compiles as it is.

* **Units**: map units = BSP units. 1 BSP unit = 16 game world units, a
  terrain tile is 64 units, the shipped corridors are 128-256 wide. Texture
  scale 0.5 gives the density of the shipped levels.
* **Axes**: map z is up. The game's y is up and its x/z plane is mirrored;
  the compiler swaps y and z, so the level looks the same in the game as in
  TrenchBroom.
* **Collision**: the party is a box 64 wide and 6 high (hull 1); keep gaps
  that should be walked through at least 64 wide.
* **Water / lava**: set the content flag *water* or *lava* on a brush (face
  attributes). The faces are drawn with the animated water/lava texture.
* **Surface flags**: *nodraw* (face kept, not drawn), *terrain* (horizontal
  face drawn as terrain, used by the overworld pieces), *waterpass*,
  *translucent*. Content flag *clip*: collision only.
* **Textures**: any PNG under the project's `textures` folder; textures of
  the shipped levels are copied with their palettes, other images are
  resized to 128x128 and quantised (8 per new palette).
* **Lights**: `light` entities with `d6light` (radius in BSP units, shipped
  values 180-300) and `_color`. Light at a point = `d6ambient` + the sum
  over visible lights of `d6lightpeak` * (1 - d/radius) * (0.4 + 0.6 cos),
  capped at 30; both worldspawn keys default to the fit to the retail
  lightmaps (3.5 and 9.6). Raise them for a brighter level. The vertex
  light and the object light per leaf use ambient + 12.4 and peak x 0.43,
  like the brighter, flatter retail vertex light. The compiler bakes lightmaps (32 unit luxels) with
  shadows, vertex light and the object light per leaf, and writes the lights
  to `.lgt/.rgb`, which the game loads as light objects.
* **Doors**: `func_door` brush entities (`d6type` 1 slide / 2 rotate,
  `d6axis`, `d6maxmove`, `d6entid`, `d6string`). Events address them as
  `bsp slot * 100000 + d6entid`.
* Put one `info_player_start` (or any light) inside the level: qbsp uses it to
  find the outside and throw away the faces nobody can see. A leak is
  reported with a `.pts` file (TrenchBroom: File > Load Point File).

### Placing it in the world

* **Terrain spokes 0, 3, 11**: a level is placed by a 'B' record of
  `SPOKEnn.TOL` (`d6level.ObjectList.add_bsp_placement`, or the editor's
  *Place new level in this spoke*): map origin at a terrain tile, height in
  steps of 64 map units. A spoke has at most 16 BSP slots (spoke 0 uses 14,
  spoke 3 all 16, spoke 11 nine). Verified in the game (spoke 0).
* **Dungeon spokes**: their level list is fixed in deep6.exe; compile over
  one of their levels (same name). New entries need engine changes
  (planned for OpenDWWandWExpanded).
* Getting there: exits and teleports (Exits panel, event scripts) and nav
  links (`D6LINKnn.DAT`, nav tool) as for any level.

## What the compiler does

1. reads the map, gives every texture a short alias (qbsp keeps 15
   characters), marks water/lava/clip/skip brushes, writes a Valve 220 map
   without the lights (lights near walls make the 64 unit clip hulls "leak")
2. runs qbsp (`-tjunc rotate`) and vis; only a leak in hull 0 counts
3. converts Quake BSP 29 into Deep6 BSP 28: y/z swapped, face windings
   reversed and rotated so they end on a corner (the engine does not draw a
   face whose last vertex is not a corner), texinfo rescaled to the Deep6
   projection, 68 byte models, entities with `d6*` keys
4. light: lightmaps (.lf/.ls), vertex light, leaf object light
5. textures (.twd), lights (.lgt/.rgb), nav graph (.nvs/.l2n)

Engine limits checked: 19999 faces, 9999 nodes, lightmaps of at most 16x16
luxels (larger faces are not drawn; the compiler warns).

## Known limits

* The software-renderer lightmaps (.lfs/.lss) are not written.
* Light is an approximation of the original tool: fitted to 14279 retail
  luxels of six levels it is off by 5.8 of 30 on average (RMS); the retail
  maps have soft detail this model does not reproduce.
* Rotating doors: the pivot keys `d6rotx/y/z` are in game coordinates (y up),
  as in the retail data.
* Checked with TrenchBroom 2026.2 (Linux AppImage): the game configuration
  loads; a decompiled level opens with its textures, lights and doors; in
  CRYPTA a wall was retextured from the material browser, a water volume made
  (water texture + the *water* content flag checkbox), a light recoloured and
  a new light dragged in from the entity browser (it carries only an origin;
  the compiler uses the FGD defaults); compiled and installed from the editor,
  all of it shows in the game. File > New gives the template room, which
  compiles. Lava and clip brushes (content flags, same path as water) were
  checked in the game from a map written by script; TrenchBroom on
  Windows/macOS was not tried.
