# Fonts, pointers, palettes and other small files

The interface files and the remaining small files of the game folder.
Reference implementations: `formats/d6font.py` (fonts), `formats/d6ui.py`
(pointers, DEEP6.PAL), `d6data.TextPak` (TALKPAK), `d6level.BSPFile`
(vehicle BSPs). H unless marked.

## 1. Fonts: D6FNTnn.FNT (+ D6FNTnn.P16)

`Font_Load_`, `Font_DrawChar_`, `Font_StrWidth_` (font.c), `LoadFonts_`
(deep6.c). All 51 files round-trip (`d6font.py --selftest`).

| offset | type | field |
|---|---|---|
| 0x00 | u16 | cell width (largest glyph) |
| 0x02 | u16 | height |
| 0x04 | u16 | mode: 0 one colour, 1 palette colours |
| 0x06 | u16 | glyph count (always 128, ASCII 0..127) |
| 0x08 | i16 | spacing added after each glyph (0, -1, -2, -3, 1) |
| 0x0A | u16 | 0 |
| 0x0C | u16 | bytes per glyph = 2 + cell width x height |
| 0x0E | | glyph c at 0x0E + c x glyph size: u16 width, then height rows of *width* pixels (packed at the glyph's own width, padded to the glyph size) |

Pixels: 0 is transparent. Mode 0: 1 is the text colour, the other small
values are the background and shadow colours set by the caller
(`_gFGColor`, `_gBGColor`, `_gSGColor`). Mode 1: an index into the font's
own palette, `D6FNTnn.P16` (32 shade rows x 256 RGB565, `Font_LoadPal16_`).

The game loads 17 fonts (`LoadFonts_`, slot: file): 0 D6FNT01, 1 D6FNT03,
2 D6FNT54, 3 D6FNT40, 4 D6FNT49, 5 D6FNT42, 6 D6FNT58 (the gothic dialogue
font of the message bar), 7 D6FNT50, 8 D6FNT47, 9 D6FNT56, 10 D6FNT48,
11 D6FNT53, 12 D6FNT57, 13 D6FNT52, 14 D6FNT43, 15 D6FNT26, 16 D6FNT14.
Slots 5..16 are colour fonts with the .P16 of the same name. The other
font files are not loaded.

    python3 formats/d6font.py sheet D6FNT58.FNT sheet.png d6fnt58.p16
    python3 formats/d6font.py text  D6FNT01.FNT "On my way..." out.png

## 2. Mouse pointers: *.ptr

`LoadPointers_` (lmouse.c) loads 23 pointers (hourglas, vxpoint, vxfight,
vxsorcer, vxuse, vxdrag, vxthief, vxtalk, vxgive, mptritem, vtarget,
vblutarg, vnotarg, vnobtarg, vxlook, vxwalk, vxwing, vxbreath, vxgaze,
vxvamp, vxhide, vxcurse, vxfly); `CreateMousePointer_` (mousetim.c) reads
them with fscanf. Text, one value per line (CRLF):

    vtarget.bmp      bitmap (8-bit BMP; frames side by side)
    14               hot spot x  (reset to 0 when outside the frame)
    14               hot spot y
    8                frame count
    30               frame width  (at most 128)
    30               frame height (at most 128)
    8                sequence length (at most 16)
    0 60             then per step: frame index, time in ms (10..1000, else 100)
    1 60
    ...

The bitmap must be at least frame count x frame width wide and frame height
high, or the pointer is not created.

## 3. DEEP6.PAL

`Palette_Load_` (palette.c) reads 0x2300 bytes (the file is 8960 bytes):

| offset | size | content |
|---|---|---|
| 0 | 768 | 256 RGB entries, the game's default palette; `Pal16_Calculate_` turns it into the 32-row 16-bit shade table `_gDefaultPal16` (row r = rgb x (r + 1) / 32) |
| 0x300 | 8192 | 32 rows x 256 bytes, a lookup table used by the asm span routine `myscan.ASM` (`_gPalette + 0x300 + row x 0x100`), M |

## 4. PC remarks: pctalk/TALKPAK.000-009

`LoadPCTalkMessages_` (pctalk.c) opens `pctalk/TALKPAK.%03d` with the
character's **clan** (record +0x1A), so each of the ten clans has its own
remarks ("Ready", "Help me!", "On my way...", "$ taken", "Giddy-Up!" ...;
`$` is replaced by a name). Same layout as TEXTPAK.000: u32 count, count x
{i32 id, u32 offset, u32 size}, NUL-terminated strings; read and written by
`d6data.TextPak` (10/10 round trip).

## 5. Vehicle BSPs: models/monster/*.bsp

Ordinary version-28 BSP files (`d6level.BSPFile`, 6/6 round trip; two of
them keep 2 non-zero padding bytes after the last lump and all record the
offset of the empty visibility lump, which the reader preserves).

| file | faces | loaded by deep6.exe |
|---|---|---|
| warship.bsp | 318 | yes, `models/monster/WARSHIP.BSP` (the ship; "Unable To Load" message) |
| raft.bsp | 140 | yes, `models/monster/RAFT.BSP` |
| catapult.bsp, hydra.bsp | 14, 10 | no file name string in the exe |
| horse.bsp, minecart.bsp | 6 (a box) | no file name string in the exe |

They are collision / walk-on geometry for the vehicle models of the same
name (warship.mdl, raft ...), M.

## 6. Other files

| file | content |
|---|---|
| `speech.tag`, `music/MUSIC.TAG` | text marker files ("Speech Tag File" ...): mark where the data folders are (CD era), L |
| `Speech/SPEECH.DIR`, `save/SAVEGAME.DIR` | text markers (`SPEECH`, `SAVEGAME.DIR`) |
| `Town000.hub` | 3264 bytes; no file name string in deep6.exe, so not loaded. Starts with u32 0xCC, 0x1E0, 0x1E0, 0, then 16-byte records (u16, u16 2, u32 offset, u32 0, u32 1), L |
| `*.ldl` | launcher strings, XOR-encrypted with a fixed per-position key (not recovered); not read by deep6.exe (`data.md` section 6) |
| `default.kbd`, `autoexec.kbd`, `master.kbd` | key bindings (runtime) |
