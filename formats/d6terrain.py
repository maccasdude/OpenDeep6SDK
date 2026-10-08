#!/usr/bin/env python3
"""d6terrain.py - parsers/writers for the outdoor terrain ("spoke") data of
Wizards & Warriors (Deep6 engine, Heuristic Park, 2000).

Every parser keeps unknown bytes raw so that  serialize(parse(x)) == x.
See docs/formats/terrain.md for the documentation of every field.

Usage:
  python3 d6terrain.py --selftest <gamedir>
  python3 d6terrain.py --render <gamedir> <spoke> <outdir>
  python3 d6terrain.py --info <file>

Formats handled (class -> files):
  TerrainMap    SPOKEnn.TMR (loaded by Terrain_Load_) and spokenn.tom (editor copy)
  RecordFile    SPOKEnn.TOL / .NAV / .FOL / .LIT (64-byte record containers;
                the dungeon *.BOL files use the same container)
  SpokePar      SPOKEnn.PAR (editor generator parameters, not read by the exe)
  TileMip       tiles/80x/*.mip, tiles/xxxx.mip (4-level 8-bit mipmap)
  Pal16         *.p16 (32 shade rows x 256 RGB565 colours)
  TileList      tilebmp.lst (editor tile list, CRLF text)
  TextureDat    texture.dat (CD-check data, cdprot.c)
  Bmp2K         skydrop.m2k (16-bit indexed sky bitmap, 2048-colour palette)
  LevelMap      maps/lm<a>b<spoke>.lm (automap images + wall masks)
  FogFile       maps/lm<a>b<spoke>.fog (explored-area masks, RLE; runtime)
  MarkerFile    maps/lm<a>b<spoke>.mrk (automap notes; runtime)
  WorldDat      D6WORLD.DAT (world state; runtime)
  SegmentGam    D6SEGnn.GAM (per-spoke state snapshot; runtime)
  EmitterDat    emitters.dat (particle emitter descriptors)
  LinkDat       D6LINKnn.DAT (terrain <-> BSP nav graph links)
  BmpFile       sky / tree-drop .bmp files (plain Windows BMP, header decoded)
"""
import os
import struct
import sys

try:
    import numpy as np
except ImportError:  # numpy only needed for array helpers / rendering
    np = None

TILE = 1024            # world units per terrain tile (x and z); y is up
ANGLE_UNITS = 1024     # full circle in engine angle units (_gSin/_gCos tables)

# Hardcoded data from deep6.exe ------------------------------------------------

# _gWaterTables (tnew.c, 0x5e8f1c): water type -> (surface y, alpha/shade)
WATER_TABLE = {0: None, 1: (0, 24), 2: (0, 24), 3: (7936, 8), 4: (7936, 8),
               5: (5120, 16), 6: (5120, 16)}

# GetEntryPosition_ (d6spoke.c 0x489d20): (spoke, entry) -> (x, y, z, dir)
ENTRY_POINTS = {
    (0, 0): (0x35200, 0, 0x14600, 0x100),
    (0, 1): (0x2d600, 0, 0x12200, 0x300),
    (0, 2): (0x2a00, 0x1c00, 0x44c00, 0x100),
    (1, 0): (0x46e00, 0x820, 0x25300, 0x100),
    (2, 0): (0x54600, 0x1428, 0x4b200, 0x300),
    (3, 0): (0x1f12, 0xc00, 0x45600, 0x100),
    (3, 1): (0x45600, 0x442, 0x2a00, 0x300),
    (4, 0): (0x1ba00, 0x400, 0x1b000, 0),
    (5, 0): (0x20000, -0xb00, 0x1f400, 0x100),
    (6, 0): (0x34b00, -0x3b00, 0x23b00, 0),
    (7, 0): (0x2c200, -0x800, 0x1ca00, 0),
    (8, 0): (0x1ac00, 0x400, 0x25a00, 0),
    (9, 0): (0x33a00, -0x900, 0x24000, 0),
    (10, 0): (0x36c00, -0x1400, 0x33400, 0x200),
    (11, 0): (0x5d800, 0x400, 0x6400, 0x300),
    (12, 0): (0x29a00, -0x1500, 0x22200, 0),
}

# LoadSpokeSegment_ (d6spoke.c 0x483038): spoke -> BSP files it loads.
# Spokes 0, 3 and 11 are heightmap terrain; the rest are pure-BSP dungeons.
SPOKES = {
    0: ('terrain', 'SPOKE00'), 1: ('bsp', 'CRYPTA CRYPTB'),
    2: ('bsp', 'TEMPLEB TEMPLEA'), 3: ('terrain', 'SPOKE03'),
    4: ('bsp', 'SHURU'), 5: ('bsp', 'MINESA MINESB MINESC MINESD'),
    6: ('bsp', 'OGREA OGREB OGREC'), 7: ('bsp', 'DRAGONA DRAGONB DRAGONC'),
    8: ('bsp', 'LICHA LICHB'), 9: ('bsp', 'SUNKENA SUNKENB'),
    10: ('bsp', 'SHRINEA'), 11: ('terrain', 'SPOKE11'),
    12: ('bsp', 'PYRAMA PYRAMB'),
}

# Terrain_LoadPalettes_: palette slot -> file (slot 6 depends on spoke 11)
TILE_PALETTES = ['tiles/801/pal.p16', 'tiles/802/pal.p16', 'tiles/803/pal.p16',
                 'tiles/804/pal.p16', 'tiles/805/pal.p16', 'tiles/806/pal.p16',
                 'tiles/forest/forwal.p16', 'tiles/forest/caswal.p16']


# Helpers ----------------------------------------------------------------------

def find_ci(base, rel):
    """Case-insensitive path lookup (game uses Windows names)."""
    cur = base
    for part in rel.replace('\\', '/').split('/'):
        if not part:
            continue
        p = os.path.join(cur, part)
        if os.path.exists(p):
            cur = p
            continue
        low = part.lower()
        try:
            hit = [n for n in os.listdir(cur) if n.lower() == low]
        except OSError:
            return None
        if not hit:
            return None
        cur = os.path.join(cur, hit[0])
    return cur


def rgb565_to_rgb888(v):
    """numpy u16 array (RGB565) -> uint8 array [...,3]."""
    v = np.asarray(v, dtype=np.uint32)
    r = (v >> 11) & 31
    g = (v >> 5) & 63
    b = v & 31
    return np.stack([(r * 255 + 15) // 31, (g * 255 + 31) // 63,
                     (b * 255 + 15) // 31], -1).astype(np.uint8)


def _cstr(b):
    return b.split(b'\0', 1)[0].decode('latin1')


# 64-byte record container (.TOL .NAV .FOL .LIT, also dungeon .BOL) -----------

class Record:
    """One 64-byte record. Common layout:
       0  char  kind      'B' bsp, 'I' item, 'M' monster, 'P' prop,
                          'F' foliage, 'N' nav point, 'L' light, '0'/'\\0' unused
       1  char[3] id      ASCII decimal id ("001"); 'B' records hold "SPT"
       4  f32[3] pos      world x, y, z (y up; 1 tile = 1024 units)
       16 f32[3] rot      orientation (pitch, yaw, roll) in 1024ths of a turn
       28 ...             kind specific, see accessors / docs
    """
    __slots__ = ('raw',)

    def __init__(self, raw):
        assert len(raw) == 64
        self.raw = bytearray(raw)

    kind = property(lambda s: chr(s.raw[0]))

    @property
    def id(self):
        try:
            return int(self.raw[1:4].decode('ascii'))
        except ValueError:
            return None

    @id.setter
    def id(self, v):
        self.raw[1:4] = b'%03d' % v

    def _f3(self, o):
        return struct.unpack_from('<3f', self.raw, o)

    def _setf3(self, o, v):
        struct.pack_into('<3f', self.raw, o, *v)

    pos = property(lambda s: s._f3(4), lambda s, v: s._setf3(4, v))
    rot = property(lambda s: s._f3(16), lambda s, v: s._setf3(16, v))

    # 'B' (terrain BSP placement, LoadTerrainBsps_)
    def bsp_info(self):
        x, _, z = self.pos
        w = struct.unpack_from('<f', self.raw, 16)[0]
        h = struct.unpack_from('<f', self.raw, 24)[0]
        slot = self.raw[32]
        ox, oy, oz = struct.unpack_from('<3b', self.raw, 33)
        return dict(name=_cstr(self.raw[48:64]), slot=slot,
                    tile_x=int(x) // TILE, tile_z=int(z) // TILE,
                    tiles_w=int(w), tiles_h=int(h), off=(ox, oy, oz))

    # 'N' (LoadTerrainNavPnts_)
    def nav_links(self):
        return [v for v in struct.unpack_from('<6H', self.raw, 36) if v]

    def nav_flags(self):
        return struct.unpack_from('<HH', self.raw, 48)

    # 'P' props: byte 32 != 0 -> emitter of prop starts inactive
    prop_flag = property(lambda s: s.raw[32])

    def __repr__(self):
        return 'Record(%r,%r,pos=%s)' % (self.kind, self.id,
                                         tuple(round(v, 1) for v in self.pos))


class RecordFile:
    """Header record (int32 count incl. header, rest raw) + count-1 records."""

    def __init__(self, data):
        if len(data) % 64:
            raise ValueError('record file size not a multiple of 64')
        self.header = bytearray(data[:64])
        n = struct.unpack_from('<i', data, 0)[0]
        if n != len(data) // 64:
            raise ValueError('count %d != %d records' % (n, len(data) // 64))
        self.records = [Record(data[i:i + 64]) for i in range(64, len(data), 64)]

    def serialize(self):
        struct.pack_into('<i', self.header, 0, len(self.records) + 1)
        return bytes(self.header) + b''.join(bytes(r.raw) for r in self.records)

    def of_kind(self, k):
        return [(i + 1, r) for i, r in enumerate(self.records) if r.kind == k]


# Terrain heightmap / tile map (.TMR/.tom) ------------------------------------

TILE_DTYPE = None
if np is not None:
    TILE_DTYPE = np.dtype([
        ('tex', '<u2'),      # 0  index into BLT name table (0 = none)
        ('height', '<i2'),   # 2  vertex height (world units) at tile's (x,z) corner
        ('light', 'u1', 6),  # 4  shade row 0..31 per vertex, 2 triangles x 3
        ('unk10', '<i2'),    # 10 always 4096 in shipped data -> runtime +4
        ('flags', 'u1'),     # 12 low nibble wall edges, 0x10..0x80 wall variant
        ('water', 'u1'),     # 13 water type 0..6 (_gWaterTables)
        ('walltype', 'u1'),  # 14 0 none, 1 tree wall, 2 castle wall (& 0x7f)
        ('pad', 'u1'),       # 15 unused (0)
    ])


class TerrainMap:
    DTYPE = TILE_DTYPE
    """0x0000 int32[1024] header: [0]=width [1]=height (tiles, multiple of 32),
              rest editor data (see docs)
       0x1000 char[1024][4] BLT tile-name table
       0x2000 width*height 16-byte tile records, row major (z rows of x)."""

    def __init__(self, data):
        self.header = bytearray(data[:0x1000])
        self.width, self.height = struct.unpack_from('<2i', data, 0)
        self.blt = [bytes(data[0x1000 + i * 4:0x1004 + i * 4]) for i in range(1024)]
        n = self.width * self.height
        body = data[0x2000:]
        if len(body) != n * 16:
            raise ValueError('tile data size mismatch')
        if np is not None:
            self.tiles = np.frombuffer(body, dtype=self.DTYPE).reshape(
                self.height, self.width).copy()
        else:
            self.tiles = bytearray(body)

    def serialize(self):
        struct.pack_into('<2i', self.header, 0, self.width, self.height)
        body = self.tiles.tobytes() if np is not None else bytes(self.tiles)
        return bytes(self.header) + b''.join(self.blt) + body

    def blt_name(self, i):
        """Tile texture name; '' for unused.  Bit 7 of char 3 marks an entry
        the engine skips (editor placeholder)."""
        n = self.blt[i]
        if not n[0]:
            return ''
        return n.decode('latin1')

    @staticmethod
    def canonical_rotation(name):
        """Terrain_NameToRotation_: returns (k, canonical) where canonical is
        the lexicographically smallest byte-rotation; the engine loads the
        canonical .mip and rotates it k*90 degrees (np.rot90(tex, k))."""
        b = name.encode('latin1')
        best, bk = b, 0
        cur = b
        for k in range(1, 4):
            cur = cur[3:] + cur[:3]
            if cur < best:
                best, bk = cur, k
        return bk, best.decode('latin1')

    header_floats = property(lambda s: struct.unpack_from('<2f', s.header, 20))


TOM_DTYPE = None
if np is not None:
    TOM_DTYPE = np.dtype([
        ('corners', 'u1', 4),  # 0  editor terrain-type code per corner
        ('tex', '<u2'),        # 4  BLT index (same as .TMR)
        ('unk6', '<u2'),       # 6  0 / 0x0ccc / 0x1000 (editor; unknown)
        ('height', '<i2'),     # 8  vertex height (same as .TMR)
        ('flags', 'u1'),       # 10 wall flags (same as .TMR byte 12)
        ('water', 'u1'),       # 11 water type (same as .TMR byte 13)
        ('walltype', 'u1'),    # 12 wall type (same as .TMR byte 14)
        ('pad', 'u1', 3),      # 13 zero
    ])


class TomMap(TerrainMap):
    """spokenn.tom: editor-side twin of .TMR (same header + BLT, same tile
    count) but a different 16-byte tile layout and no baked light bytes.
    Not read by deep6.exe."""
    DTYPE = TOM_DTYPE


class SpokePar:
    """72 bytes: char[4] '0.1v' (i.e. 'v1.0'), u32 64, int32[16] params."""

    def __init__(self, data):
        if len(data) != 72:
            raise ValueError('PAR must be 72 bytes')
        self.magic = data[:4]
        self.size = struct.unpack_from('<I', data, 4)[0]
        self.values = list(struct.unpack_from('<16i', data, 8))

    def serialize(self):
        return self.magic + struct.pack('<I16i', self.size, *self.values)


# Tile textures and palettes ---------------------------------------------------

class TileMip:
    """128x128 + 64x64 + 32x32 + 16x16 8-bit palette indices (21760 bytes)."""
    SIZES = (128, 64, 32, 16)

    def __init__(self, data):
        if len(data) != sum(s * s for s in self.SIZES):
            raise ValueError('bad mip size %d' % len(data))
        self.levels = []
        o = 0
        for s in self.SIZES:
            lv = data[o:o + s * s]
            self.levels.append(np.frombuffer(lv, np.uint8).reshape(s, s).copy()
                               if np is not None else bytes(lv))
            o += s * s

    def serialize(self):
        return b''.join(l.tobytes() if np is not None else l for l in self.levels)

    @classmethod
    def from_indexed(cls, img128, palette):
        """Build a .mip from a 128x128 index image exactly like
        tiles/termip.exe does (reproduces all 775 shipped .mip files):
        each level is made from the previous one; per 2x2 block and channel
        c = (sum of the 4 texels + 2 * max of the 4) // 6 in the 5-5-5 space
        of p16 shade row 31 (G uses bits 6..10), then the nearest palette
        entry (squared distance, termip tie rule).  palette: Pal16, p16 bytes,
        (32,256) u16 table or (256,3) RGB888 base colours."""
        img = np.asarray(img128, np.uint8)
        if img.shape != (128, 128):
            raise ValueError('terrain tiles must be 128x128')
        P = termip_palette(palette)
        levels = [img.copy()]
        for _ in range(3):
            levels.append(termip_downsample(levels[-1], P))
        m = cls.__new__(cls)
        m.levels = levels
        return m


def _p16_table(palette):
    """Pal16 / bytes / (32,256) table / (256,3) rgb -> (32,256) uint16 table."""
    if isinstance(palette, Pal16):
        return palette.table
    if isinstance(palette, (bytes, bytearray)):
        return np.frombuffer(palette, '<u2').reshape(32, 256)
    a = np.asarray(palette)
    if a.shape == (32, 256):
        return a.astype(np.uint16)
    if a.shape == (256, 3):
        return np.frombuffer(make_p16(a), '<u2').reshape(32, 256)
    raise ValueError('unsupported palette shape %r' % (a.shape,))


def termip_palette(palette):
    """termip.exe colour table: row 31 of the p16, R = bits 11..15,
    G = bits 6..10 (low green bit dropped), B = bits 0..4."""
    c = _p16_table(palette)[31].astype(np.int64)
    return np.stack([(c >> 11) & 31, (c >> 6) & 31, c & 31], -1)


def _termip_nearest(t, P):
    d = ((t[..., None, :] - P[None, :, :]) ** 2).sum(-1)
    idx = d.argmin(-1)
    best = d.min(-1)
    ties = (d == best[..., None]).sum(-1) > 1
    for pos in zip(*np.nonzero(ties)):
        tt = t[pos]
        dd = d[pos]
        r, g, b = (int(v) for v in tt)
        ch = 0 if (r > g and r > b) else 1 if (g > r and g > b) else 2 if (b > r and b > g) else None
        bi, bd = 0, 0xc00
        for i in range(256):
            if dd[i] < bd:
                bd, bi = dd[i], i
            elif dd[i] == bd and ch is not None and \
                    abs(int(P[bi, ch]) - int(tt[ch])) > abs(int(P[i, ch]) - int(tt[ch])):
                bi = i
        idx[pos] = bi
    return idx


def termip_downsample(src, P):
    """One termip mip step (see TileMip.from_indexed); P from termip_palette."""
    h, w = src.shape
    q = P[src].reshape(h // 2, 2, w // 2, 2, 3)
    t = (q.sum((1, 3)) + 2 * q.max((1, 3))) // 6
    return _termip_nearest(t, P).astype(np.uint8)


def make_p16(rgb256):
    """32 shade rows x 256 RGB565 from 256 RGB888 base colours, exactly like
    Pal16_Calculate_ / RefPal16_Calculate_ (pal16.c 0x44e124):
    row s = (c * (s+1)) // 32 per channel, packed 5-6-5; row 31 = c.
    All shipped tile, wall, font and model palettes follow this formula."""
    c = np.asarray(rgb256, np.int64).reshape(256, 3)
    s = np.arange(1, 33, dtype=np.int64)[:, None, None]
    v = (c[None] * s) // 32
    t = ((v[..., 0] >> 3) << 11) | ((v[..., 1] >> 2) << 5) | (v[..., 2] >> 3)
    return t.astype('<u2').tobytes()


def p16_base_rgb(palette):
    """Best-effort inverse of make_p16: 256 RGB888 colours whose make_p16
    reproduces the table where possible (row 31 expanded, then refined so
    all 32 rows match)."""
    tab = _p16_table(palette).astype(np.int64)
    out = np.zeros((256, 3), np.int64)
    s = np.arange(1, 33, dtype=np.int64)
    for ch, (sh, bits, mask) in enumerate(((11, 3, 31), (5, 2, 63), (0, 3, 31))):
        rows = (tab >> sh) & mask                     # (32, 256)
        cand = np.arange(256)[:, None]
        pred = ((cand * s[None, :]) // 32) >> bits   # (256 cand, 32)
        for i in range(256):
            ok = np.nonzero((pred == rows[:, i][None, :]).all(1))[0]
            out[i, ch] = ok[len(ok) // 2] if len(ok) else (rows[31, i] << bits)
    return out.astype(np.uint8)


def quantize(rgb, palette_rgb, exclude=(), chunk=4096):
    """RGB image (..., 3) -> nearest palette indices (squared RGB distance).
    palette_rgb: (256,3) RGB888 (e.g. Pal16.rgb(31) or p16_base_rgb).
    exclude: indices never chosen (e.g. (0,) for model textures where index
    0 is transparent/black)."""
    img = np.asarray(rgb, np.int64)
    P = np.asarray(palette_rgb, np.int64).reshape(-1, 3)
    valid = np.ones(len(P), bool)
    valid[list(exclude)] = False
    flat = img.reshape(-1, 3)
    out = np.empty(len(flat), np.uint8)
    for i in range(0, len(flat), chunk):
        d = ((flat[i:i + chunk, None, :] - P[None]) ** 2).sum(-1)
        d[:, ~valid] = 1 << 40
        out[i:i + chunk] = d.argmin(-1)
    return out.reshape(img.shape[:-1])


def tile_dir_palette_rgb(game, tiledir):
    """Base colours of tiles/<tiledir>/pal.p16 (for quantising new tiles)."""
    return p16_base_rgb(Pal16(open(find_ci(game, 'tiles/%s/pal.p16' % tiledir), 'rb').read()))


class Pal16:
    """16384 bytes: u16 RGB565 colours, [shade 0..31][index 0..255]."""

    def __init__(self, data):
        if len(data) != 0x4000:
            raise ValueError('bad p16 size')
        self.table = (np.frombuffer(data, '<u2').reshape(32, 256).copy()
                      if np is not None else bytes(data))

    def serialize(self):
        return self.table.tobytes() if np is not None else self.table

    def rgb(self, shade=31):
        return rgb565_to_rgb888(self.table[shade])


class TileList:
    """tilebmp.lst: CRLF separated list of tile .mip paths (editor only)."""

    def __init__(self, data):
        self.lines = data.split(b'\r\n')

    def serialize(self):
        return b'\r\n'.join(self.lines)

    def names(self):
        return [l.decode('latin1') for l in self.lines if l]


class TextureDat:
    """texture.dat: two u32 (version 2, checksum) used by the CD check."""

    def __init__(self, data):
        if len(data) != 8:
            raise ValueError('bad texture.dat')
        self.version, self.check = struct.unpack('<2I', data)

    def serialize(self):
        return struct.pack('<2I', self.version, self.check)


class Bmp2K:
    """skydrop.m2k: int32 w, h, ncolors; u16 palette[2048]; u16 pixels[h][w]
       (pixels index the 2048-entry RGB565 palette)."""

    def __init__(self, data):
        self.w, self.h, self.ncolors = struct.unpack_from('<3i', data, 0)
        self.palette = data[12:12 + 0x1000]
        self.pixels = data[12 + 0x1000:]
        if len(self.pixels) != self.w * self.h * 2:
            raise ValueError('bad m2k size')

    def serialize(self):
        return struct.pack('<3i', self.w, self.h, self.ncolors) + \
            self.palette + self.pixels

    def to_rgb(self):
        pal = np.frombuffer(self.palette, '<u2')
        pix = np.frombuffer(self.pixels, '<u2').reshape(self.h, self.w)
        return rgb565_to_rgb888(pal[pix])


class BmpFile:
    """Windows BMP: 14-byte file header + info header decoded, rest raw."""

    def __init__(self, data):
        if data[:2] != b'BM':
            raise ValueError('not a BMP')
        self.fh = data[:14]
        self.ih_size = struct.unpack_from('<I', data, 14)[0]
        (self.width, self.height, self.planes, self.bpp,
         self.compression) = struct.unpack_from('<iiHHI', data, 18)
        self.rest = data[14:]

    def serialize(self):
        return self.fh + self.rest


# Automap: .lm / .fog / .mrk ---------------------------------------------------

LM_LEVEL_SIZE = 0x23c


class LevelMapLevel:
    """0x23c-byte level header (jhpMap_Create_):
       +00 int img_off    +04 int img_size   +08 int mask_off  +0c int mask_size
       +10 f32 min x,y,z  +1c f32 max x,y,z  (world bbox of this map page set)
       +28 int bsp        -1 terrain, n = bsp slot, -5/-7 special offsets
       +2c int pages_w    +30 int pages_h    (128x128 pixel pages)
       +34 int page_bytes (0x4000)  +38 int mask_bytes (0x800, 1 bit/pixel)
       +3c u16 palette[256] (RGB565; converted to 555 when needed)"""

    def __init__(self, hdr):
        self.raw = bytearray(hdr)
        v = struct.unpack_from('<4i6fi4i', hdr, 0)
        (self.img_off, self.img_size, self.mask_off, self.mask_size) = v[:4]
        self.bbox_min = v[4:7]
        self.bbox_max = v[7:10]
        self.bsp, self.pages_w, self.pages_h, self.page_bytes, self.mask_bytes = v[10:]
        self.palette = hdr[0x3c:0x23c]
        self.images = []
        self.masks = []

    def header_bytes(self):
        struct.pack_into('<4i', self.raw, 0, self.img_off, self.img_size,
                         self.mask_off, self.mask_size)
        return bytes(self.raw)

    def page_image(self, i):
        return np.frombuffer(self.images[i], np.uint8).reshape(128, 128)

    def mosaic_rgb(self):
        pal = rgb565_to_rgb888(np.frombuffer(self.palette, '<u2'))
        out = np.zeros((self.pages_h * 128, self.pages_w * 128, 3), np.uint8)
        for i in range(self.pages_w * self.pages_h):
            py, px = divmod(i, self.pages_w)
            out[py * 128:(py + 1) * 128, px * 128:(px + 1) * 128] = \
                pal[self.page_image(i)]
        return out


class LevelMap:
    """int32 magic 0x4c4d ('ML'), int32 version (2), int32 x2 (0),
       int32 nlevels, nlevels*0x23c level headers, then for each level its
       image pages (pages_w*pages_h*page_bytes) followed by its masks."""

    def __init__(self, data):
        self.head = data[:16]
        magic = struct.unpack_from('<i', data, 0)[0]
        if magic != 0x4c4d:
            raise ValueError('bad lm magic')
        n = struct.unpack_from('<i', data, 16)[0]
        self.levels = [LevelMapLevel(data[20 + i * LM_LEVEL_SIZE:
                                          20 + (i + 1) * LM_LEVEL_SIZE])
                       for i in range(n)]
        pos = 20 + n * LM_LEVEL_SIZE
        for lv in self.levels:
            if lv.img_off != pos or lv.mask_off != lv.img_off + lv.img_size:
                raise ValueError('non-contiguous lm layout')
            np_ = lv.pages_w * lv.pages_h
            lv.images = [data[lv.img_off + k * lv.page_bytes:
                              lv.img_off + (k + 1) * lv.page_bytes] for k in range(np_)]
            lv.masks = [data[lv.mask_off + k * lv.mask_bytes:
                             lv.mask_off + (k + 1) * lv.mask_bytes] for k in range(np_)]
            pos = lv.mask_off + lv.mask_size
        self.tail = data[pos:]

    def serialize(self):
        pos = 20 + len(self.levels) * LM_LEVEL_SIZE
        for lv in self.levels:                    # recompute layout
            lv.img_off = pos
            lv.img_size = sum(len(x) for x in lv.images)
            lv.mask_off = pos + lv.img_size
            lv.mask_size = sum(len(x) for x in lv.masks)
            pos = lv.mask_off + lv.mask_size
        out = [self.head, struct.pack('<i', len(self.levels))]
        out += [lv.header_bytes() for lv in self.levels]
        for lv in self.levels:
            out += lv.images + lv.masks
        out.append(self.tail)
        return b''.join(out)


def rle_decode_bits(words, nbytes=0x800):
    """jhpMap_Decompress_: u16 runs, bit15 = value, bits0-14 = run length,
    0 terminates; bits are packed MSB first."""
    bits = []
    for w in words:
        if w == 0:
            break
        bits.extend([1 if w & 0x8000 else 0] * (w & 0x7fff))
    bits = (bits + [0] * (nbytes * 8))[:nbytes * 8]
    return bytes(np.packbits(np.array(bits, np.uint8)))


class FogFile:
    """Sequence of chunks, one per automap page of each level in order:
       int32 nbytes; u16 rle[nbytes/2] (last word 0).  Written by the game."""

    def __init__(self, data):
        self.chunks = []
        o = 0
        while o < len(data):
            n = struct.unpack_from('<i', data, o)[0]
            if n < 0 or o + 4 + n > len(data):
                raise ValueError('bad fog chunk')
            self.chunks.append(data[o + 4:o + 4 + n])
            o += 4 + n

    def serialize(self):
        return b''.join(struct.pack('<i', len(c)) + c for c in self.chunks)

    def page_mask(self, i):
        c = self.chunks[i]
        return rle_decode_bits(struct.unpack('<%dH' % (len(c) // 2), c))


class MarkerFile:
    """Per level: int32 count; count * (int32 x, int32 y, int32 colour,
       char text[256]).  Written by the game."""

    def __init__(self, data):
        self.levels = []
        o = 0
        while o < len(data):
            n = struct.unpack_from('<i', data, o)[0]
            o += 4
            marks = []
            for _ in range(n):
                x, y, c = struct.unpack_from('<3i', data, o)
                marks.append([x, y, c, data[o + 12:o + 268]])
                o += 268
            self.levels.append(marks)

    def serialize(self):
        out = []
        for marks in self.levels:
            out.append(struct.pack('<i', len(marks)))
            for x, y, c, t in marks:
                out.append(struct.pack('<3i', x, y, c) + t)
        return b''.join(out)


# Runtime state files ----------------------------------------------------------

class WorldDat:
    """D6WORLD.DAT (SaveWorldGameData_), 5416 bytes."""
    FIELDS = [('world_clock', 4), ('npc_stop_time', 4), ('npc_status', 0x140),
              ('npc_mbits', 0x280), ('wstate', 0x1000), ('portal_flag', 0x40),
              ('portal_base', 0xc0), ('portal_hret', 0x18), ('portal_home', 0x48)]

    def __init__(self, data):
        if len(data) != sum(n for _, n in self.FIELDS):
            raise ValueError('bad D6WORLD size')
        o = 0
        self.fields = {}
        for k, n in self.FIELDS:
            self.fields[k] = data[o:o + n]
            o += n

    def serialize(self):
        return b''.join(self.fields[k] for k, _ in self.FIELDS)


class SegmentGam:
    """D6SEGnn.GAM (SegWrite_Segment_): 100-byte header of (offset,count)
    pairs, then sections in write order: objects, monsters, NPCs, NPC msg
    queue, BSP entities, switches, traps, events.  Sections kept raw."""
    HEADER_NAMES = ['switch_off', 'switch_n', 'trap_off', 'trap_n',
                    'bspent_off', 'bspent_n', 'object_off', 'object_n',
                    'npc_off', 'npc_n', 'monster_off', 'monster_n',
                    'event_off', 'event_n'] + \
                   ['event_sub%d' % i for i in range(10)] + ['npcmsg_off']

    def __init__(self, data):
        self.header = list(struct.unpack_from('<25i', data, 0))
        self.body = data[100:]

    def serialize(self):
        return struct.pack('<25i', *self.header) + self.body

    def table(self):
        return dict(zip(self.HEADER_NAMES, self.header))


class EmitterDat:
    """emitters.dat (LoadEmitterDesc_): int32 n, n * 128-byte descriptors
    (name char[24] at 0; flags u32 at 0x18; see docs for the rest)."""

    def __init__(self, data):
        n = struct.unpack_from('<i', data, 0)[0]
        if 4 + n * 128 != len(data):
            raise ValueError('bad emitters.dat')
        self.recs = [data[4 + i * 128:4 + (i + 1) * 128] for i in range(n)]

    def serialize(self):
        return struct.pack('<i', len(self.recs)) + b''.join(self.recs)

    def names(self):
        return [_cstr(r[:24]) for r in self.recs]


class LinkDat:
    """D6LINKnn.DAT (LinkNavPoints_): int32 n, 4 unused bytes (absent when
    n == 0, file is then 4 bytes), then n records of int16 a, b, c, d:
      d == -1: terrain nav id b  <->  nav id a of BSP slot c
      c == -1: terrain nav id a  <->  nav id b of BSP slot d
      otherwise BSP-to-BSP link (nav a in slot c <-> nav b in slot d).
    Glues the terrain .NAV graph to the nav graphs inside the BSPs."""

    def __init__(self, data):
        n = struct.unpack_from('<i', data, 0)[0]
        o = len(data) - n * 8
        if o not in (4, 8):
            raise ValueError('bad D6LINK size')
        self.pad = data[4:o]
        self.links = [list(struct.unpack_from('<4h', data, o + i * 8)) for i in range(n)]

    def serialize(self):
        return struct.pack('<i', len(self.links)) + self.pad + \
            b''.join(struct.pack('<4h', *l) for l in self.links)


# Dispatch / self test ---------------------------------------------------------

def classify(path):
    n = os.path.basename(path).lower()
    ext = os.path.splitext(n)[1]
    if ext == '.tmr':
        return TerrainMap
    if ext == '.tom':
        return TomMap
    if ext in ('.tol', '.nav', '.fol', '.lit', '.bol'):
        return RecordFile
    if ext == '.par':
        return SpokePar
    if ext == '.mip':
        return TileMip
    if ext == '.p16':
        return Pal16
    if n == 'tilebmp.lst':
        return TileList
    if n == 'texture.dat':
        return TextureDat
    if ext == '.m2k':
        return Bmp2K
    if ext == '.lm':
        return LevelMap
    if ext == '.fog':
        return FogFile
    if ext == '.mrk':
        return MarkerFile
    if n == 'd6world.dat':
        return WorldDat
    if n.startswith('d6seg') and ext == '.gam':
        return SegmentGam
    if n == 'emitters.dat':
        return EmitterDat
    if n.startswith('d6link') and ext == '.dat':
        return LinkDat
    if ext == '.bmp':
        return BmpFile
    return None


def selftest_files(game):
    out = []
    for n in sorted(os.listdir(game)):
        p = os.path.join(game, n)
        if not os.path.isfile(p):
            continue
        low = n.lower()
        ext = os.path.splitext(low)[1]
        if (low.startswith('spoke') and ext in ('.tmr', '.tom', '.tol', '.nav',
                                                 '.fol', '.lit', '.par')) or \
           ext == '.bol' or low in ('tilebmp.lst', 'texture.dat', 'skydrop.m2k',
                                    'd6world.dat', 'emitters.dat') or \
           (low.startswith('d6seg') and ext == '.gam') or \
           (low.startswith('d6link') and ext == '.dat') or \
           low in ('skymtn00.bmp', 'treedrop.bmp', 'ttopdrop.bmp', 'palmdrop.bmp',
                   'ptopdrop.bmp', 'sunmap.bmp', 'sunalpha.bmp', 'moonmap.bmp') or \
           low in ('treedrop.p16', 'ttopdrop.p16', 'palmdrop.p16', 'ptopdrop.p16',
                   'treedirt.p16', 'palmsand.p16'):
            out.append(p)
    for sub in ('tiles', 'maps'):
        d = find_ci(game, sub)
        if not d:
            continue
        for root, _, files in os.walk(d):
            for n in sorted(files):
                if os.path.splitext(n.lower())[1] in ('.mip', '.p16', '.lm',
                                                      '.fog', '.mrk', '.bmp'):
                    out.append(os.path.join(root, n))
    return out


def selftest(game):
    files = selftest_files(game)
    stats = {}
    fails = []
    for p in files:
        cls = classify(p)
        if cls is None:
            continue
        data = open(p, 'rb').read()
        try:
            obj = cls(data)
            back = obj.serialize()
            ok = back == data
        except Exception as e:  # noqa
            ok = False
            back = repr(e)
        s = stats.setdefault(cls.__name__, [0, 0, 0])
        s[0] += 1
        s[2] += len(data)
        if ok:
            s[1] += 1
        else:
            fails.append((p, back if isinstance(back, str) else 'mismatch'))
    print('%-12s %6s %6s %12s' % ('format', 'files', 'ok', 'bytes'))
    for k in sorted(stats):
        f, ok, b = stats[k]
        print('%-12s %6d %6d %12d' % (k, f, ok, b))
    total = sum(s[0] for s in stats.values())
    good = sum(s[1] for s in stats.values())
    print('TOTAL %d files, %d byte-identical' % (total, good))
    for p, why in fails:
        print('FAIL', p, why)
    assert not fails, '%d round-trip failures' % len(fails)
    return 0


# Rendering (sanity images) ----------------------------------------------------

def load_tile_textures(game, tmap, level):
    """Return {blt index: (palette slot, 2D index array)} for used tiles."""
    used = set(np.unique(tmap.tiles['tex']).tolist())
    dirs = {}
    for d in ('801', '802', '803', '804', '805', '806'):
        p = find_ci(game, 'tiles/' + d)
        for n in os.listdir(p):
            dirs[n.lower()] = (int(d) - 801, os.path.join(p, n))
    tex = {}
    for i in used:
        name = tmap.blt_name(i)
        if not name or name[0] in 'Xx' or ord(name[3]) & 0x80:
            continue
        k, canon = TerrainMap.canonical_rotation(name)
        hit = dirs.get(canon.lower() + '.mip')
        if hit is None:
            continue
        m = TileMip(open(hit[1], 'rb').read())
        # Texture rows run toward -z: flip vertically for a z-down image.
        # Verified: corner chars of the BLT names then match neighbours
        # (99.9%) and texture seams are continuous.
        tex[i] = (hit[0], np.flipud(np.rot90(m.levels[level], k)))
    return tex


def render_spoke(game, spoke, outdir):
    from PIL import Image, ImageDraw
    os.makedirs(outdir, exist_ok=True)
    base = 'SPOKE%02d' % spoke
    tm = TerrainMap(open(find_ci(game, base + '.TMR'), 'rb').read())
    T = tm.tiles
    H, W = T.shape
    # 1) heightmap with hillshade
    h = T['height'].astype(np.float32)
    gy, gx = np.gradient(h)
    shade = np.clip(0.6 + (-gx - gy) / 1500.0, 0, 1.2)
    hn = (h - h.min()) / max(1, (h.max() - h.min()))
    img = np.clip(np.stack([hn * shade] * 3, -1) * 255, 0, 255).astype(np.uint8)
    water = T['water'] > 0
    img[water] = (img[water] * 0.4 + np.array([20, 60, 160]) * 0.6).astype(np.uint8)
    Image.fromarray(img).resize((W * 3, H * 3), Image.NEAREST).save(
        os.path.join(outdir, '%s_height.png' % base.lower()))
    # 2) textured tile map
    px = 16
    tex = load_tile_textures(game, tm, 3)
    pals = [Pal16(open(find_ci(game, p), 'rb').read()) for p in TILE_PALETTES[:6]]
    palrgb = [rgb565_to_rgb888(p.table) for p in pals]   # [32,256,3]
    out = np.zeros((H * px, W * px, 3), np.uint8)
    light = T['light'].mean(-1).astype(int).clip(0, 31)
    for z in range(H):
        for x in range(W):
            t = tex.get(int(T['tex'][z, x]))
            if t is None:
                continue
            out[z * px:(z + 1) * px, x * px:(x + 1) * px] = \
                palrgb[t[0]][max(int(light[z, x]), 20)][t[1]]
    lo, hi = np.percentile(out[out.sum(-1) > 0], [1, 99.5])
    out = np.clip((out.astype(np.float32) - lo) * 255.0 / max(1, hi - lo), 0, 255).astype(np.uint8)
    Image.fromarray(out).save(os.path.join(outdir, '%s_tiles.png' % base.lower()))
    # 2b) full-resolution close-up around the first entry point (town gate)
    ent = [v for (s, e), v in sorted(ENTRY_POINTS.items()) if s == spoke]
    if ent:
        cx, cz = ent[0][0] // TILE, ent[0][2] // TILE
        x0, z0 = max(0, cx - 24), max(0, cz - 24)
        x1, z1 = min(W, cx + 24), min(H, cz + 24)
        tex1 = load_tile_textures(game, tm, 1)      # 64x64 per tile
        cp = 64
        crop = np.zeros(((z1 - z0) * cp, (x1 - x0) * cp, 3), np.uint8)
        for z in range(z0, z1):
            for x in range(x0, x1):
                t = tex1.get(int(T['tex'][z, x]))
                if t is not None:
                    crop[(z - z0) * cp:(z - z0 + 1) * cp, (x - x0) * cp:(x - x0 + 1) * cp] = \
                        palrgb[t[0]][max(int(light[z, x]), 20)][t[1]]
        crop = np.clip((crop.astype(np.float32) - lo) * 255.0 / max(1, hi - lo), 0, 255).astype(np.uint8)
        Image.fromarray(crop).save(os.path.join(outdir, '%s_gate_closeup.png' % base.lower()))
    # 3) overlay: tiles (dimmed) + walls + foliage + objects + nav + bsps
    sc = 4
    base_img = out[::px // sc, ::px // sc].astype(np.float32) * 0.6
    wt = np.kron(T['walltype'], np.ones((sc, sc), np.uint8))
    base_img[wt == 1] = base_img[wt == 1] * 0.4 + np.array([0, 70, 0])   # tree wall
    base_img[wt == 2] = base_img[wt == 2] * 0.4 + np.array([90, 90, 90])  # castle wall
    ov = Image.fromarray(base_img.clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(ov)
    fl = T['flags']
    # wall faces: 0x10 +x, 0x20 +z, 0x40 -x, 0x80 -z (side facing open ground)
    for bit, (ax, az, bx, bz) in ((0x10, (1, 0, 1, 1)), (0x20, (0, 1, 1, 1)),
                                  (0x40, (0, 0, 0, 1)), (0x80, (0, 0, 1, 0))):
        for z, x in zip(*np.nonzero(fl & bit)):
            d.line([(x + ax) * sc, (z + az) * sc, (x + bx) * sc, (z + bz) * sc],
                   fill=(120, 255, 120))
    ws = lambda v: v / TILE * sc  # noqa
    fol = RecordFile(open(find_ci(game, base + '.FOL'), 'rb').read())
    for r in fol.records:
        if r.kind == 'F':
            x, _, z = r.pos
            d.rectangle([ws(x) - 1, ws(z) - 1, ws(x), ws(z)], fill=(200, 255, 200))
    nav = RecordFile(open(find_ci(game, base + '.NAV'), 'rb').read())
    pts = {i + 1: r.pos for i, r in enumerate(nav.records) if r.kind == 'N'}
    for i, p in pts.items():
        for j in nav.records[i - 1].nav_links():
            if j in pts:
                d.line([ws(p[0]), ws(p[2]), ws(pts[j][0]), ws(pts[j][2])],
                       fill=(255, 220, 0))
    for p in pts.values():
        d.ellipse([ws(p[0]) - 2, ws(p[2]) - 2, ws(p[0]) + 2, ws(p[2]) + 2],
                  fill=(255, 140, 0))
    tol = RecordFile(open(find_ci(game, base + '.TOL'), 'rb').read())
    col = {'M': (255, 40, 40), 'P': (80, 160, 255), 'I': (255, 0, 255)}
    for r in tol.records:
        x, _, z = r.pos
        if r.kind in col:
            d.rectangle([ws(x) - 2, ws(z) - 2, ws(x) + 2, ws(z) + 2], fill=col[r.kind])
        elif r.kind == 'B':
            b = r.bsp_info()
            d.rectangle([b['tile_x'] * sc, b['tile_z'] * sc,
                         (b['tile_x'] + b['tiles_w']) * sc,
                         (b['tile_z'] + b['tiles_h']) * sc], outline=(0, 255, 255), width=2)
            d.text((b['tile_x'] * sc + 3, b['tile_z'] * sc + 3), b['name'], fill=(0, 255, 255))
    lit = find_ci(game, base + '.LIT')
    if lit:
        for r in RecordFile(open(lit, 'rb').read()).records:
            x, _, z = r.pos
            d.ellipse([ws(x) - 3, ws(z) - 3, ws(x) + 3, ws(z) + 3], outline=(255, 255, 255))
    for (s, e), (x, y, z, dr) in ENTRY_POINTS.items():
        if s == spoke:
            d.ellipse([ws(x) - 6, ws(z) - 6, ws(x) + 6, ws(z) + 6], outline=(255, 0, 0), width=3)
            d.text((ws(x) + 8, ws(z) - 6), 'entry %d' % e, fill=(255, 80, 80))
    ov.save(os.path.join(outdir, '%s_overlay.png' % base.lower()))
    # 4) automap pages for this spoke
    lmp = find_ci(game, 'maps/lm0b%d.lm' % spoke)
    if lmp:
        lm = LevelMap(open(lmp, 'rb').read())
        for k, lv in enumerate(lm.levels):
            Image.fromarray(lv.mosaic_rgb()).save(
                os.path.join(outdir, 'lm0b%d_level%d.png' % (spoke, k)))
    sky = find_ci(game, 'skydrop.m2k')
    if sky:
        Image.fromarray(Bmp2K(open(sky, 'rb').read()).to_rgb()).save(
            os.path.join(outdir, 'skydrop.png'))
    print('wrote renders for spoke %d to %s' % (spoke, outdir))


def info(path):
    cls = classify(path)
    data = open(path, 'rb').read()
    obj = cls(data)
    print(cls.__name__, len(data), 'bytes')
    if isinstance(obj, TerrainMap):
        print(' size', obj.width, obj.height, 'header floats', obj.header_floats)
        print(' used textures', len(np.unique(obj.tiles['tex'])))
    elif isinstance(obj, RecordFile):
        from collections import Counter
        print(' records', len(obj.records), dict(Counter(r.kind for r in obj.records)))
        for i, r in obj.of_kind('B'):
            print('  BSP', i, r.bsp_info())
    elif isinstance(obj, LevelMap):
        for lv in obj.levels:
            print(' level bsp=%d pages=%dx%d bbox=%s..%s' % (lv.bsp, lv.pages_w,
                  lv.pages_h, lv.bbox_min, lv.bbox_max))
    elif isinstance(obj, SegmentGam):
        print(obj.table())


def main(argv):
    if len(argv) >= 3 and argv[1] == '--selftest':
        return selftest(argv[2])
    if len(argv) >= 5 and argv[1] == '--render':
        render_spoke(argv[2], int(argv[3]), argv[4])
        return 0
    if len(argv) >= 3 and argv[1] == '--info':
        info(argv[2])
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
