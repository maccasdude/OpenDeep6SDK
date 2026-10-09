#!/usr/bin/env python3
"""
d6level.py - readers/writers for the indoor (BSP) level file set of
Wizards & Warriors (2000, Heuristic Park, engine "Deep6").

Every class has:
    X.parse(data: bytes) -> X        (classmethod)
    x.to_bytes() -> bytes             (byte identical for unmodified data)
    X.load(path) / x.save(path)

Per level <name> the game uses (see docs/formats/levels.md for details):
    <name>.bsp   Quake-1 style BSP, version 28, 15 lumps        BSPFile
    <name>.twd   texture WAD (16-bit shaded palettes + 8-bit mips) TexWad
    <name>.lf    per-face lightmap info, hardware (RGB565) path  FaceLightInfo
    <name>.lfs   per-face lightmap info, software (8-bit) path   FaceLightInfo
    <name>.ls    lightmap texel blob, RGB565                     LightData
    <name>.lss   lightmap texel blob, 8-bit (0..31) intensities  LightData
    <name>.lgt   point lights (pos + intensity)                  LightList
    <name>.rgb   per-light colour (parallel to .lgt)             LightColors
    <name>.nvs   AI navigation points (graph)                    NavPoints
    <name>.l2n   BSP leaf -> nav point lookup                    Leaf2Nav
    <name>.bol   object placements (props/monsters/items)        ObjectList
    <name>.tc    texture usage report (tool output, unused)      TexUsageReport
    <name>.pt1/.pt2 text point lists (tool output, unused)       PointList
Also (level wiring, not per level):
    SPOKExx.TOL  64-byte record list; 'B' records place BSPs      ObjectList
    D6LINKxx.DAT nav links between BSPs / terrain of one spoke   NavLinks
    SPOKE_TABLE  hard coded BSP placement of the dungeon spokes  (constant)

Run  python3 d6level.py --selftest <gamedir>  to round trip every file.
Pure python3 (numpy optional, only used by helpers).
"""
import os
import re
import struct
import sys
from array import array
from collections import namedtuple

__all__ = [
    'find_file', 'BSPFile', 'TexWad', 'FaceLightInfo', 'LightData', 'LightList',
    'LightColors', 'NavPoints', 'Leaf2Nav', 'ObjectList', 'TexUsageReport',
    'PointList', 'NavLinks', 'Level', 'SPOKE_TABLE', 'spoke_bsps',
    'BSP_UNITS_PER_WORLD', 'TILE_WORLD',
]

# 1 BSP unit = 16 world units (World2BspTrans_ scales by 1/16 = 0x3d800000)
BSP_UNITS_PER_WORLD = 1.0 / 16.0
TILE_WORLD = 1024.0          # one terrain tile in world units (_DAT_005c1c00)
NAV_MAGIC = -666             # 0xfffffd66, used by .nvs and .twd

if sys.byteorder != 'little':   # array() uses native order
    raise RuntimeError('d6level.py assumes a little endian host')


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def find_file(directory, name):
    """Case insensitive lookup (the game is DOS/Windows; names vary in case)."""
    p = os.path.join(directory, name)
    if os.path.exists(p):
        return p
    low = name.lower()
    try:
        for f in os.listdir(directory):
            if f.lower() == low:
                return os.path.join(directory, f)
    except OSError:
        pass
    return None


def _read(path):
    with open(path, 'rb') as f:
        return f.read()


def _write(path, data):
    """Write a new file and rename it over path: never writes through a hard
    link into another copy of the game (d6mod workcopy --link)."""
    tmp = path + '.d6tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


class _Base(object):
    @classmethod
    def load(cls, path):
        return cls.parse(_read(path))

    def save(self, path):
        _write(path, self.to_bytes())


def _table(name, fields):
    return namedtuple(name, fields.split())


def _unpack_list(nt, fmt, buf):
    """Parse buf into a list of namedtuples; returns (list, leftover bytes)."""
    sz = struct.calcsize(fmt)
    n = len(buf) // sz
    body = buf[:n * sz]
    return [nt._make(t) for t in struct.iter_unpack(fmt, body)], bytes(buf[n * sz:])


def _pack_list(fmt, rows):
    s = struct.Struct(fmt)
    return b''.join(s.pack(*r) for r in rows)


def _arr(code, buf):
    a = array(code)
    sz = a.itemsize
    n = len(buf) // sz
    a.frombytes(bytes(buf[:n * sz]))
    return a, bytes(buf[n * sz:])


# --------------------------------------------------------------------------
# BSP
# --------------------------------------------------------------------------
Plane = _table('Plane', 'nx ny nz dist type')
Vertex = _table('Vertex', 'x y z')
Node = _table('Node', 'planenum front back minx miny minz maxx maxy maxz firstface numfaces')
TexInfo = _table('TexInfo', 'sx sy sz soff tx ty tz toff ox oy oz flags runtime')
Face = _table('Face', 'planenum side firstedge numedges texinfo s0 s1 s2 s3 lightofs')
ClipNode = _table('ClipNode', 'planenum front back')
Leaf = _table('Leaf', 'contents visofs minx miny minz maxx maxy maxz '
                      'firstmarksurface nummarksurfaces amb0 amb1 amb2 amb3')
Edge = _table('Edge', 'v0 v1')
Model = _table('Model', 'minx miny minz maxx maxy maxz ox oy oz '
                        'head0 head1 head2 head3 visleafs firstface numfaces entity')

# lump index -> (name, struct fmt or array code, record type)
LUMP_ENTITIES, LUMP_PLANES, LUMP_TEXTURES, LUMP_VERTEXES, LUMP_VISIBILITY, \
    LUMP_NODES, LUMP_TEXINFO, LUMP_FACES, LUMP_LIGHTING, LUMP_CLIPNODES, \
    LUMP_LEAFS, LUMP_MARKSURFACES, LUMP_EDGES, LUMP_SURFEDGES, LUMP_MODELS = range(15)

_LUMP_TABLES = {
    LUMP_PLANES: ('planes', '<4fi', Plane),
    LUMP_VERTEXES: ('vertices', '<3f', Vertex),
    LUMP_NODES: ('nodes', '<i2h6h2H', Node),
    LUMP_TEXINFO: ('texinfo', '<8f3f2i', TexInfo),
    LUMP_FACES: ('faces', '<2hi2h4Bi', Face),
    LUMP_CLIPNODES: ('clipnodes', '<i2h', ClipNode),
    LUMP_LEAFS: ('leafs', '<2i6h2H4B', Leaf),
    LUMP_EDGES: ('edges', '<2H', Edge),
    LUMP_MODELS: ('models', '<9f4i3ii', Model),
}
_LUMP_ARRAYS = {
    LUMP_LIGHTING: ('vertex_light', 'H'),       # one u16 per surfedge
    LUMP_MARKSURFACES: ('marksurfaces', 'H'),
    LUMP_SURFEDGES: ('surfedges', 'i'),
}
# order the original qbsp-derived compiler writes lumps (all 66 files)
BSP_WRITE_ORDER = [1, 10, 3, 5, 6, 7, 9, 11, 13, 12, 14, 8, 4, 0, 2]

# leaf contents values used by the engine
CONTENTS_EMPTY, CONTENTS_SOLID, CONTENTS_WATER, CONTENTS_SLIME, CONTENTS_LAVA = -1, -2, -3, -4, -5

# texinfo.flags bits (engine usage, see docs)
TEXF_INDEX_MASK = 0x00000fff   # index into the level .twd texture list
TEXF_LAVA = 0x00002000         # translucent/"lava" water pass (bsp.c: byte 0x2d & 0x20)
TEXF_WATER = 0x00008000        # water pass (byte 0x2d & 0x80)
TEXF_TERRAIN = 0x00020000      # drawn as terrain poly if horizontal (byte 0x2e & 2)


def parse_entities(text):
    """Entity lump text -> list of entities, each a list of (key, value) str pairs."""
    s = text.rstrip(b'\0')
    ents = []
    for blk in re.findall(rb'\{\n(.*?)\}\n', s, re.S):
        ents.append([(k.decode('latin-1'), v.decode('latin-1'))
                     for k, v in re.findall(rb'"([^"]*)" "([^"]*)"\n', blk)])
    return ents


def build_entities(ents):
    out = []
    for e in ents:
        out.append(b'{\n')
        for k, v in e:
            out.append(b'"' + k.encode('latin-1') + b'" "' + v.encode('latin-1') + b'"\n')
        out.append(b'}\n')
    return b''.join(out) + b'\0'


class BSPFile(_Base):
    """Deep6 .bsp: Quake-1 BSP layout, version 28, 15 lumps.

    Attributes: version, entities (list of [(k,v)]), planes, vertices,
    visibility (bytes), nodes, texinfo, faces, vertex_light (array H),
    clipnodes, leafs, marksurfaces (array H), edges, surfedges (array i),
    models, textures_raw (lump 2, always empty), and tail bytes per lump.
    """
    HEADER_FMT = '<i30i'

    def __init__(self):
        self.version = 28
        self.entities = []
        self.textures_raw = b''
        self.visibility = b''
        self._tails = {}         # lump -> leftover bytes not a multiple of rec size
        self._order = list(BSP_WRITE_ORDER)
        self._offsets = None     # original header (for info only)
        for name, _, _ in _LUMP_TABLES.values():
            setattr(self, name, [])
        for name, code in _LUMP_ARRAYS.values():
            setattr(self, name, array(code))

    @classmethod
    def parse(cls, data):
        b = cls()
        h = struct.unpack_from(cls.HEADER_FMT, data, 0)
        b.version = h[0]
        lumps = [(h[1 + 2 * i], h[2 + 2 * i]) for i in range(15)]
        b._offsets = lumps
        # empty lumps with a non-zero offset (models/monster/*.bsp write the
        # offset the lump would have had); kept so the file rebuilds byte for byte
        b._empty_offsets = dict((i, o) for i, (o, n) in enumerate(lumps) if n == 0 and o)
        end = max([o + n for o, n in lumps if n] or [124])
        b._end_pad = bytes(data[end:])           # padding after the last lump (not always zero)
        nz = sorted((o, i) for i, (o, n) in enumerate(lumps) if n > 0)
        b._order = [i for _, i in nz] + [i for i in BSP_WRITE_ORDER if lumps[i][1] == 0]
        for i, (o, n) in enumerate(lumps):
            buf = data[o:o + n]
            if i == LUMP_ENTITIES:
                b.entities = parse_entities(buf)
                if build_entities(b.entities) != buf:      # never happens in retail data
                    b._tails['entities_raw'] = bytes(buf)
            elif i == LUMP_TEXTURES:
                b.textures_raw = bytes(buf)
            elif i == LUMP_VISIBILITY:
                b.visibility = bytes(buf)
            elif i in _LUMP_TABLES:
                name, fmt, nt = _LUMP_TABLES[i]
                rows, tail = _unpack_list(nt, fmt, buf)
                setattr(b, name, rows)
                if tail:
                    b._tails[i] = tail
            else:
                name, code = _LUMP_ARRAYS[i]
                a, tail = _arr(code, buf)
                setattr(b, name, a)
                if tail:
                    b._tails[i] = tail
        return b

    def lump_bytes(self, i):
        if i == LUMP_ENTITIES:
            return self._tails.get('entities_raw') or build_entities(self.entities)
        if i == LUMP_TEXTURES:
            return self.textures_raw
        if i == LUMP_VISIBILITY:
            return self.visibility
        if i in _LUMP_TABLES:
            name, fmt, _ = _LUMP_TABLES[i]
            return _pack_list(fmt, getattr(self, name)) + self._tails.get(i, b'')
        name, _ = _LUMP_ARRAYS[i]
        return getattr(self, name).tobytes() + self._tails.get(i, b'')

    def to_bytes(self):
        out = bytearray(124)
        hdr = [(0, 0)] * 15
        for i in self._order:
            lb = self.lump_bytes(i)
            if not lb:
                continue
            while len(out) & 3:
                out.append(0)
            hdr[i] = (len(out), len(lb))
            out += lb
        end = len(out)
        while len(out) & 3:
            out.append(0)
        pad = getattr(self, '_end_pad', b'')
        if pad and len(pad) == len(out) - end:
            out[end:] = pad
        for i, o in getattr(self, '_empty_offsets', {}).items():
            if hdr[i] == (0, 0):
                hdr[i] = (o, 0)
        flat = [self.version]
        for o, n in hdr:
            flat += [o, n]
        struct.pack_into(self.HEADER_FMT, out, 0, *flat)
        return bytes(out)

    # ---- convenience ----------------------------------------------------
    def face_vertex_indices(self, face):
        """Vertex indices of a face polygon in winding order."""
        if isinstance(face, int):
            face = self.faces[face]
        idx = []
        for k in range(face.firstedge, face.firstedge + face.numedges):
            se = self.surfedges[k]
            e = self.edges[se] if se >= 0 else self.edges[-se]
            idx.append(e.v0 if se >= 0 else e.v1)
        return idx

    def face_points(self, face):
        return [tuple(self.vertices[i]) for i in self.face_vertex_indices(face)]

    def face_uv(self, face, point, tex_w=128, tex_h=128):
        """Normalised texture coordinate (1.0 = one 128 texel repeat) as computed
        by bsp_3DCard_PolyDraw_: u = ((P - origin).s + soff*0.25) / 32.
        Equivalent texel form (software path): u_tex = 4*(P - origin).s + soff."""
        if isinstance(face, int):
            face = self.faces[face]
        ti = self.texinfo[face.texinfo]
        dx, dy, dz = point[0] - ti.ox, point[1] - ti.oy, point[2] - ti.oz
        u = (dx * ti.sx + dy * ti.sy + dz * ti.sz + ti.soff * 0.25) / 32.0
        v = (dx * ti.tx + dy * ti.ty + dz * ti.tz + ti.toff * 0.25) / 32.0
        return u, v

    def model_faces(self, model_index=0):
        m = self.models[model_index]
        return range(m.firstface, m.firstface + m.numfaces)

    def decompress_vis(self, leaf_index):
        """Quake RLE PVS row for a leaf (bit per leaf, leaf 0 excluded)."""
        visofs = self.leafs[leaf_index].visofs
        nbytes = (self.models[0].visleafs + 7) >> 3
        out = bytearray()
        if visofs < 0 or not self.visibility:
            return bytes([0xff] * nbytes)
        p = visofs
        vis = self.visibility
        while len(out) < nbytes:
            c = vis[p]
            p += 1
            if c:
                out.append(c)
            else:
                out += bytes(vis[p])
                p += 1
        return bytes(out[:nbytes])

    def entity_dicts(self):
        return [dict(e) for e in self.entities]

    def summary(self):
        return ('v%d planes=%d verts=%d nodes=%d texinfo=%d faces=%d clip=%d leafs=%d '
                'mark=%d edges=%d surfedges=%d models=%d ents=%d vis=%d' % (
                    self.version, len(self.planes), len(self.vertices), len(self.nodes),
                    len(self.texinfo), len(self.faces), len(self.clipnodes), len(self.leafs),
                    len(self.marksurfaces), len(self.edges), len(self.surfedges),
                    len(self.models), len(self.entities), len(self.visibility)))


# --------------------------------------------------------------------------
# TWD texture wad
# --------------------------------------------------------------------------
class Texture(object):
    __slots__ = ('name_raw', 'palette', 'width', 'height', 'mips')

    def __init__(self, name_raw, palette, width, height, mips):
        self.name_raw = name_raw   # 64 bytes, NUL padded (keep raw for round trip)
        self.palette = palette     # int16 index into TexWad.palettes
        self.width = width
        self.height = height
        self.mips = mips           # list of bytes, level i is (w>>i)*(h>>i)

    @property
    def name(self):
        return self.name_raw.split(b'\0', 1)[0].decode('latin-1')


class TexWad(_Base):
    """<level>.twd (and syswat.twd).
    [int32 -666 => 4 mip levels, else 1] int32 npal, int32 ntex,
    npal * 0x4000 palette (u16 RGB565 [32 shades][256]),
    ntex * { char name[64]; int16 pal; int32 w; int32 h; u8 mips[] }"""
    PAL_SIZE = 0x4000

    def __init__(self):
        self.mipmapped = True
        self.palettes = []
        self.textures = []

    @classmethod
    def parse(cls, data):
        w = cls()
        p = 0
        first, = struct.unpack_from('<i', data, p)
        if first == NAV_MAGIC:
            w.mipmapped = True
            p += 4
        else:
            w.mipmapped = False
        npal, ntex = struct.unpack_from('<2i', data, p)
        p += 8
        for _ in range(npal):
            w.palettes.append(bytes(data[p:p + cls.PAL_SIZE]))
            p += cls.PAL_SIZE
        levels = 4 if w.mipmapped else 1
        for _ in range(ntex):
            name = bytes(data[p:p + 64])
            pal, tw, th = struct.unpack_from('<h2i', data, p + 64)
            p += 74
            mips = []
            for m in range(levels):
                n = (tw >> m) * (th >> m)
                mips.append(bytes(data[p:p + n]))
                p += n
            w.textures.append(Texture(name, pal, tw, th, mips))
        w._tail = bytes(data[p:])
        return w

    def to_bytes(self):
        out = bytearray()
        if self.mipmapped:
            out += struct.pack('<i', NAV_MAGIC)
        out += struct.pack('<2i', len(self.palettes), len(self.textures))
        for pal in self.palettes:
            out += pal
        for t in self.textures:
            out += t.name_raw + struct.pack('<h2i', t.palette, t.width, t.height)
            for m in t.mips:
                out += m
        out += getattr(self, '_tail', b'')
        return bytes(out)

    def build(self):
        """Serialise (byte identical to the input when unchanged)."""
        return self.to_bytes()

    # -- writing new textures (needs numpy; uses d6terrain helpers) --------
    def _np_texture(self, rgb, pal_index):
        import numpy as np
        from d6terrain import quantize, p16_base_rgb, termip_palette, termip_downsample
        img = np.asarray(rgb)[..., :3]
        if img.shape[:2] != (128, 128):
            raise ValueError('BSP textures must be 128x128 (Texlist buffers are fixed)')
        tab = np.frombuffer(self.palettes[pal_index], '<u2').reshape(32, 256)
        idx = quantize(img, p16_base_rgb(tab)).astype(np.uint8)
        P = termip_palette(tab)
        mips = [idx]
        for _ in range(3):
            mips.append(termip_downsample(mips[-1], P))
        return [m.tobytes() for m in mips]

    def _new_palette(self, rgb):
        """Median-cut 256 colours from the image (PIL), append its p16."""
        import numpy as np
        from PIL import Image
        from d6terrain import make_p16
        im = Image.fromarray(np.asarray(rgb, np.uint8)[..., :3]).quantize(256, method=0)
        pal = np.array(im.getpalette()[:768] + [0] * (768 - len(im.getpalette()[:768])))
        self.palettes.append(make_p16(pal.reshape(256, 3)))
        return len(self.palettes) - 1

    def add_texture(self, name, rgb, palette=None):
        """Append a 128x128 RGB texture; returns its index (= texinfo
        flags & 0xfff).  palette=int: quantise to that existing wad palette;
        palette=None: build a new 256-colour palette for it (median cut,
        shade rows via make_p16).  Mips are made like termip.exe."""
        if palette is None:
            palette = self._new_palette(rgb)
        mips = self._np_texture(rgb, palette)
        nm = name.encode('latin-1')[:63]
        self.textures.append(Texture(nm + b'\0' * (64 - len(nm)), palette, 128, 128, mips))
        self._warn_mip3(len(self.textures) - 1)
        return len(self.textures) - 1

    def replace_texture(self, index, rgb, palette='keep'):
        """Replace texture pixels; palette='keep' quantises to its current
        palette, None makes a new palette, int selects a palette."""
        t = self.textures[index]
        if palette is None:
            palette = self._new_palette(rgb)
        elif palette == 'keep':
            palette = t.palette
        t.palette = palette
        t.mips = self._np_texture(rgb, palette)
        self._warn_mip3(index)

    def _warn_mip3(self, i):
        """The engine merges textures whose 16x16 mip is identical
        (Texlist_FindTextureSlot_), keeping the first one's palette."""
        m = self.textures[i].mips[-1]
        for j, t in enumerate(self.textures):
            if j != i and t.mips[-1] == m:
                import warnings
                warnings.warn('texture %d has the same 16x16 mip as %d; the engine '
                              'will merge them' % (i, j))

    def palette_rgb(self, pal_index, shade=31):
        """256 (r,g,b) 8-bit tuples for one shade row (31 = full brightness)."""
        row = array('H')
        off = shade * 512
        row.frombytes(self.palettes[pal_index][off:off + 512])
        res = []
        for c in row:
            r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
            res.append(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
        return res

    def texture_rgb(self, index, shade=31, mip=0):
        """Return (w, h, bytes RGB24) for a texture."""
        t = self.textures[index]
        lut = self.palette_rgb(t.palette, shade)
        flat = bytearray()
        for px in t.mips[mip]:
            flat += bytes(lut[px])
        return t.width >> mip, t.height >> mip, bytes(flat)


# --------------------------------------------------------------------------
# lightmaps
# --------------------------------------------------------------------------
FaceLight = _table('FaceLight', 'smin tmin width height count offset')


class FaceLightInfo(_Base):
    """.lf / .lfs : one 24 byte record per BSP face (int32 x6):
    smin, tmin (lightmap origin in luxel space), width, height,
    count (= w*h texels), offset (byte offset into .ls data blob for .lf,
    texel==byte offset into .lss blob for .lfs)."""
    FMT = '<6i'

    def __init__(self, rows=None):
        self.faces = rows or []
        self._tail = b''

    @classmethod
    def parse(cls, data):
        o = cls()
        o.faces, o._tail = _unpack_list(FaceLight, cls.FMT, data)
        return o

    def to_bytes(self):
        return _pack_list(self.FMT, self.faces) + self._tail


class LightData(_Base):
    """.ls / .lss : int32 byte count, then the lightmap texel blob.
    .ls = u16 RGB565 texels, .lss = u8 intensities 0..31 (software path)."""

    def __init__(self, data=b''):
        self.data = data
        self._tail = b''
        self._size = None

    @classmethod
    def parse(cls, data):
        o = cls()
        n, = struct.unpack_from('<i', data, 0)
        o.data = bytes(data[4:4 + n])
        o._tail = bytes(data[4 + n:])
        return o

    def to_bytes(self):
        return struct.pack('<i', len(self.data)) + self.data + self._tail

    def texels16(self, offset, count):
        a = array('H')
        a.frombytes(self.data[offset:offset + 2 * count])
        return a


# --------------------------------------------------------------------------
# lights
# --------------------------------------------------------------------------
PointLight = _table('PointLight', 'x y z intensity')


class LightList(_Base):
    """.lgt : N * {float x, y, z (BSP units); float intensity}.
    Loader multiplies intensity by 16 (Load_BSP_ColoredLights_)."""
    FMT = '<4f'

    def __init__(self):
        self.lights = []
        self._tail = b''

    @classmethod
    def parse(cls, data):
        o = cls()
        o.lights, o._tail = _unpack_list(PointLight, cls.FMT, data)
        return o

    def to_bytes(self):
        return _pack_list(self.FMT, self.lights) + self._tail


class LightColors(_Base):
    """.rgb : N * uint32 colour, R = bits 0-7, G = 8-15, B = 16-23, top byte unused.
    Parallel to .lgt (missing file -> white 255,255,255)."""

    def __init__(self):
        self.colors = array('I')
        self._tail = b''

    @classmethod
    def parse(cls, data):
        o = cls()
        o.colors, o._tail = _arr('I', data)
        return o

    def to_bytes(self):
        return self.colors.tobytes() + self._tail

    def rgb(self, i):
        c = self.colors[i]
        return c & 255, (c >> 8) & 255, (c >> 16) & 255


# --------------------------------------------------------------------------
# navigation
# --------------------------------------------------------------------------
NavPoint = _table('NavPoint', 'x y z radius l0 l1 l2 l3 l4 l5 flags id')


class NavPoints(_Base):
    """.nvs : int32 magic (-666), int32 unknown (id counter?), int32 count,
    count * 32 byte {float x,y,z (BSP units); float radius?; int16 links[6]
    (0 based index into this list, -1 none); u16 flags; u16 nav_id}."""
    FMT = '<4f6hHH'

    def __init__(self):
        self.magic = NAV_MAGIC
        self.unknown = 0
        self.points = []
        self._tail = b''

    @classmethod
    def parse(cls, data):
        o = cls()
        o.magic, o.unknown, n = struct.unpack_from('<3i', data, 0)
        body = data[12:12 + 32 * n]
        o.points, _ = _unpack_list(NavPoint, cls.FMT, body)
        o._tail = bytes(data[12 + 32 * n:])
        return o

    def to_bytes(self):
        return (struct.pack('<3i', self.magic, self.unknown, len(self.points)) +
                _pack_list(self.FMT, self.points) + self._tail)

    def links(self, i):
        p = self.points[i]
        return [l for l in (p.l0, p.l1, p.l2, p.l3, p.l4, p.l5) if l >= 0]


class Leaf2Nav(_Base):
    """.l2n : int16 per BSP leaf: 0 based nav point index (-1 = none)."""

    def __init__(self):
        self.nav = array('h')
        self._tail = b''

    @classmethod
    def parse(cls, data):
        o = cls()
        o.nav, o._tail = _arr('h', data)
        return o

    def to_bytes(self):
        return self.nav.tobytes() + self._tail


# --------------------------------------------------------------------------
# object lists (.bol, also SPOKExx.TOL/.FOL which use the same 64 byte records)
# --------------------------------------------------------------------------
ObjRec = _table('ObjRec', 'type ident x y z rx ry rz pad b20 b21 b22 b23 rest')


class ObjectList(_Base):
    """64 byte records. Record 0 is a header: int32 total record count
    (including the header) + 60 bytes (zero). Each object record:
      +0  char  type   'P' prop, 'M' monster, 'I' item, 0 = deleted
                       (TOL also: 'B' bsp placement, 'N' nav point, ...)
      +1  char[3] ident ASCII 3 digit record number in D6PROP/D6MONS/D6ITEM
      +4  float x,y,z  position, world units, relative to BSP origin
      +16 float rx,ry,rz rotation
      +28 4 bytes pad
      +32 u8 b20 (prop: mine group id in spoke 5; monster: extra param)
      +33..35 u8 b21,b22,b23 (b23 != 0: prop starts 'off')
      +36 28 bytes (TOL 'B': name at +48)"""
    FMT = '<c3s6f4s4B28s'

    def __init__(self):
        self.header_extra = bytes(60)
        self.records = []
        self._tail = b''

    @classmethod
    def parse(cls, data):
        o = cls()
        n, = struct.unpack_from('<i', data, 0)
        o.header_extra = bytes(data[4:64])
        o.records, _ = _unpack_list(ObjRec, cls.FMT, data[64:64 * n])
        o._tail = bytes(data[64 * n:])
        return o

    def to_bytes(self):
        return (struct.pack('<i', len(self.records) + 1) + self.header_extra +
                _pack_list(self.FMT, self.records) + self._tail)

    @staticmethod
    def kind(rec):
        return rec.type.decode('latin-1') if rec.type != b'\0' else ''

    @staticmethod
    def number(rec):
        try:
            return int(rec.ident.decode('latin-1'))
        except ValueError:
            return None

    def bsp_placements(self):
        """TOL 'B' records -> list of dicts (see LoadTerrainBsps_)."""
        out = []
        for r in self.records:
            if r.type != b'B':
                continue
            raw = _pack_list(self.FMT, [r])
            w, h = struct.unpack_from('<f4xf', raw, 16)
            slot, ox, oy, oz = struct.unpack_from('<B3b', raw, 32)
            name = raw[48:64].split(b'\0', 1)[0].decode('latin-1')
            bx, by = int(r.x / TILE_WORLD), int(r.z / TILE_WORLD)
            out.append(dict(name=name, slot=slot, bx=bx, by=by, bw=int(w), bh=int(h),
                            offx=ox, offy=oy, offz=oz, p10=ox, p11=oz))
        return out

    MAX_BSP_SLOTS = 16       # LoadTerrainBsps_: slot 0..15, each used once

    def add_bsp_placement(self, name, origin_tile, floor_steps, bsp_bounds, slot=None):
        """Append a TOL 'B' record that places level NAME in a terrain spoke.
        origin_tile = (tx, tz): the tile where BSP x/z 0 lies; floor_steps:
        BSP origin height in 1024 world unit steps (int8). bsp_bounds =
        (minx, minz, maxx, maxz) of the BSP in BSP units; the streaming
        rectangle covers it plus one tile. Returns the slot."""
        used = {b['slot'] for b in self.bsp_placements()}
        if slot is None:
            free = [i for i in range(self.MAX_BSP_SLOTS) if i not in used]
            if not free:
                raise ValueError('all %d terrain BSP slots of this spoke are used' % self.MAX_BSP_SLOTS)
            slot = free[0]
        elif slot in used:
            raise ValueError('slot %d is used' % slot)
        nm = name.encode('latin-1')
        if len(nm) > 15:
            raise ValueError('level names have at most 15 characters')
        import math as _m
        tx, tz = origin_tile
        minx, minz, maxx, maxz = bsp_bounds
        t = 1024.0 * BSP_UNITS_PER_WORLD          # BSP units per tile (64)
        offx = int(_m.floor(minx / t)) - 1
        offz = int(_m.floor(minz / t)) - 1
        bw = int(_m.ceil(maxx / t)) - offx + 1
        bh = int(_m.ceil(maxz / t)) - offz + 1
        bx, by = tx + offx, tz + offz
        offy = -int(floor_steps)
        for v in (offx, offy, offz):
            if not -128 <= v <= 127:
                raise ValueError('placement offset out of range')
        raw = bytearray(64)
        raw[0:4] = b'BSPT'
        struct.pack_into('<3f', raw, 4, bx * TILE_WORLD, 0.0, by * TILE_WORLD)
        struct.pack_into('<f4xf', raw, 16, float(bw), float(bh))
        struct.pack_into('<B3b', raw, 32, slot, offx, offy, offz)
        raw[48:48 + len(nm)] = nm
        rec, _ = _unpack_list(ObjRec, self.FMT, bytes(raw))
        self.records.append(rec[0])
        return slot


# --------------------------------------------------------------------------
# text tool outputs
# --------------------------------------------------------------------------
class TexUsageReport(_Base):
    """.tc : CRLF text 'Texture Usage Report' from the level tool; not read
    by deep6.exe. entries = [(name, count)]."""
    LINE = re.compile(rb'^NAME: (.*?) +COUNT: (-?\d+)$')

    def __init__(self):
        self.title = ''
        self.entries = []
        self._raw = None

    @classmethod
    def parse(cls, data):
        o = cls()
        lines = data.split(b'\r\n')
        o.title = lines[0].decode('latin-1')
        for ln in lines[1:]:
            m = cls.LINE.match(ln)
            if m:
                o.entries.append((m.group(1).decode('latin-1'), int(m.group(2))))
        if o._build() != data:
            o._raw = bytes(data)
        return o

    def _build(self):
        s = self.title.encode('latin-1') + b'\r\n\r\n\r\n'
        for n, c in self.entries:
            s += b'NAME: ' + n.encode('latin-1').ljust(37) + b'COUNT: ' + str(c).encode() + b'\r\n'
        return s

    def to_bytes(self):
        return self._raw if self._raw is not None else self._build()


class PointList(_Base):
    """.pt1 / .pt2 : CRLF text, one 'x y z' (%f) point per line. Tool output
    (probably a recorded path); not read by deep6.exe."""

    def __init__(self):
        self.points = []
        self._raw = None

    @classmethod
    def parse(cls, data):
        o = cls()
        for ln in data.split(b'\r\n'):
            if ln.strip():
                o.points.append(tuple(float(v) for v in ln.split()))
        if o._build() != data:
            o._raw = bytes(data)
        return o

    def _build(self):
        return b''.join(('%f %f %f\r\n' % p).encode() for p in self.points)

    def to_bytes(self):
        return self._raw if self._raw is not None else self._build()


# --------------------------------------------------------------------------
# level wiring
# --------------------------------------------------------------------------
NavLink = _table('NavLink', 'nav_a nav_b bsp_a bsp_b')


class NavLinks(_Base):
    """D6LINKxx.DAT : int32 count, 4 bytes unused, count * {int16 nav_a, nav_b,
    bsp_a, bsp_b}. bsp_* index the spoke BSP list (slot 0 = null BSP, the
    dungeon BSPs are 1..n in load order), -1 = terrain nav point. nav_* are
    nav_id values (NavPoint.id / TOL 'N' record ids)."""
    FMT = '<4h'

    def __init__(self):
        self.extra = b'\0\0\0\0'
        self.links = []
        self._tail = b''

    @classmethod
    def parse(cls, data):
        o = cls()
        n, = struct.unpack_from('<i', data, 0)
        o.extra = bytes(data[4:8])
        o.links, _ = _unpack_list(NavLink, cls.FMT, data[8:8 + 8 * n])
        o._tail = bytes(data[8 + 8 * n:])
        return o

    def to_bytes(self):
        return struct.pack('<i', len(self.links)) + self.extra + _pack_list(self.FMT, self.links) + self._tail


# Hard coded BSP placement for the pure dungeon spokes, from LoadSpokeSegment_
# (d6spoke.c). Arguments of Terrain_BSPLoad_(terrain, name, bx, by, bw, bh,
# offx, offy, offz, p10, p11, defer). Origin of the BSP in world units:
#   ((bx-offx)*1024, -offy*1024, (by-offz)*1024); world = bsp*16 + origin.
# Terrain spokes 0, 3 and 11 read their BSP list from SPOKExx.TOL 'B' records.
SPOKE_TABLE = {
    1: [('CRYPTA', 0x101, 0x8f, 0x96, 0x77, -0x3f, 0, -0x32, -0x44, -0x3d),
        ('CRYPTB', 0x101, 0x118, 100, 0x6f, -0x3f, 0, -0x32, -0x7c, -0x48)],
    2: [('TEMPLEB', 0x6e, 0x122, 0x71, 0x53, -0x43, -0x1b, -0x20, -0x63, -9),
        ('TEMPLEA', 0x154, 0x122, 0x92, 0x6c, 0x13, -5, 0xc, -0x6a, -0x24)],
    4: [('SHURU', 100, 100, 0x45, 0x27, -9, 0, -6, -0x1a, -3)],
    5: [('MINESA', 0x5a, 0x78, 0x4a, 0x68, -0x73, 0x36, -0x1f, -0x54, -0x75),
        ('MINESB', 0x122, 0x78, 0x91, 0xb5, -0x73, 0x36, -0x1f, -0x5a, -0x5e),
        ('MINESC', 0x78, 0x154, 0x5b, 0x69, -0x73, 0x36, -0x1f, -0x76, -0x28),
        ('MINESD', 0x14a, 400, 0x62, 0x96, -0x73, 0x36, -0x1f, 0x1c, -0x3d)],
    6: [('OGREA', 200, 0x78, 0xa2, 0x4b, 0x26, 0x10, -0x27, -0x5c, -0x20),
        ('OGREB', 400, 0x78, 0x67, 0x50, 0x55, 6, 0x11, -6, -7),
        ('OGREC', 200, 0x154, 0x43, 0x61, 0x26, 0x10, -0x27, -0x42, -0x5d)],
    7: [('DRAGONA', 0x96, 100, 0x5d, 0x49, -0x2f, -2, -5, -0x1a, -0x19),
        ('DRAGONB', 400, 100, 0x52, 0x37, -0x2f, -2, -5, 9, -0x20),
        ('DRAGONC', 0x96, 0x15e, 0x5e, 0x43, -0x2f, -2, -5, -9, 5)],
    8: [('LICHA', 100, 0x78, 0x4e, 0x6f, -7, 0, -0x1b, -0x24, -2),
        ('LICHB', 300, 0x78, 0x5b, 0x45, -7, 0, -0x1b, -0x2a, -2)],
    9: [('SUNKENA', 200, 0x78, 0x96, 0x41, -0x23, 0, -0x35, -0x69, -0x21),
        ('SUNKENB', 200, 0x140, 0x57, 0x51, -0x23, 0, -0x35, -0xd, 10)],
    10: [('SHRINEA', 200, 200, 0x49, 0xb4, -0x13, 4, 0x40, -0x38, -0x6d)],
    12: [('PYRAMA', 0x96, 100, 0x6c, 0x4a, 0x10, 0x10, 4, -0x20, -7),
         ('PYRAMB', 0x96, 0x140, 0x77, 0x5b, 0x10, 0x10, 4, -7, -0x4f)],
}
TERRAIN_SPOKES = (0, 3, 11)


def _placement(name, bx, by, bw, bh, offx, offy, offz, p10, p11, slot=None):
    return dict(name=name, slot=slot, bx=bx, by=by, bw=bw, bh=bh, offx=offx, offy=offy,
                offz=offz, p10=p10, p11=p11,
                origin=((bx - offx) * TILE_WORLD, -offy * TILE_WORLD, (by - offz) * TILE_WORLD),
                tile_rect=(bx + p10 - offx, by + p11 - offz, bw, bh))


def spoke_bsps(spoke, gamedir=None):
    """List of BSP placements for a spoke, in bspList order (index 1..n;
    index 0 is the null BSP added by Terrain_BSPNull_ for dungeon spokes).
    For terrain spokes the TOL is read from gamedir, ordered by slot."""
    if spoke in SPOKE_TABLE:
        return [_placement(*e, slot=i + 1) for i, e in enumerate(SPOKE_TABLE[spoke])]
    if gamedir is None:
        raise ValueError('terrain spoke %d needs gamedir to read SPOKE%02d.TOL' % (spoke, spoke))
    p = find_file(gamedir, 'SPOKE%02d.TOL' % spoke)
    res = []
    for b in ObjectList.load(p).bsp_placements():
        res.append(_placement(b['name'], b['bx'], b['by'], b['bw'], b['bh'], b['offx'],
                              b['offy'], b['offz'], b['p10'], b['p11'], slot=b['slot']))
    res.sort(key=lambda d: d['slot'])
    return res


# --------------------------------------------------------------------------
# a whole level
# --------------------------------------------------------------------------
LEVEL_EXTS = {
    'bsp': BSPFile, 'twd': TexWad, 'lf': FaceLightInfo, 'lfs': FaceLightInfo,
    'ls': LightData, 'lss': LightData, 'lgt': LightList, 'rgb': LightColors,
    'nvs': NavPoints, 'l2n': Leaf2Nav, 'bol': ObjectList, 'tc': TexUsageReport,
    'pt1': PointList, 'pt2': PointList,
}


class Level(object):
    """All files of one level. Missing optional files are None."""

    def __init__(self, gamedir, name):
        self.gamedir = gamedir
        self.name = name
        self.files = {}
        self.paths = {}
        for ext, cls in LEVEL_EXTS.items():
            p = find_file(gamedir, name + '.' + ext)
            self.paths[ext] = p
            self.files[ext] = cls.load(p) if p else None

    def __getattr__(self, k):
        f = self.__dict__.get('files')
        if f is not None and k in f:
            return f[k]
        raise AttributeError(k)

    def save(self, outdir):
        os.makedirs(outdir, exist_ok=True)
        for ext, obj in self.files.items():
            if obj is not None:
                obj.save(os.path.join(outdir, os.path.basename(self.paths[ext])))


def list_levels(gamedir):
    return sorted({os.path.splitext(f)[0].lower() for f in os.listdir(gamedir)
                   if f.lower().endswith('.bsp')})


# --------------------------------------------------------------------------
# self test
# --------------------------------------------------------------------------
def _consistency(gamedir, name, problems):
    lv = Level(gamedir, name)
    b = lv.bsp
    nf, nl, nse = len(b.faces), len(b.leafs), len(b.surfedges)
    chk = []
    if lv.lf is not None:
        chk.append(('lf records == faces', len(lv.lf.faces) == nf))
    if lv.lfs is not None:
        chk.append(('lfs records == faces', len(lv.lfs.faces) == nf))
    if lv.l2n is not None:
        chk.append(('l2n entries == leafs', len(lv.l2n.nav) == nl))
    if lv.lgt is not None and lv.rgb is not None:
        chk.append(('rgb == lgt count', len(lv.rgb.colors) == len(lv.lgt.lights)))
    chk.append(('lighting lump == surfedges*2', len(b.vertex_light) in (0, nse)))
    if lv.twd is not None:
        mx = max([t.flags & TEXF_INDEX_MASK for t in b.texinfo if t.flags >= 0] or [0])
        chk.append(('texinfo tex index < ntex', mx < len(lv.twd.textures)))
    if lv.ls is not None and lv.lf is not None and lv.lf.faces:
        last = max(lv.lf.faces, key=lambda r: r.offset)
        chk.append(('ls blob covers lf', last.offset + 2 * last.count <= len(lv.ls.data)))
    if lv.lss is not None and lv.lfs is not None and lv.lfs.faces:
        last = max(lv.lfs.faces, key=lambda r: r.offset)
        chk.append(('lss blob covers lfs', last.offset + last.count <= len(lv.lss.data)))
    for k, ok in chk:
        if not ok:
            problems.append('%s: %s' % (name, k))


def selftest(gamedir):
    exts = set(LEVEL_EXTS)
    files = sorted(f for f in os.listdir(gamedir)
                   if os.path.splitext(f)[1][1:].lower() in exts)
    extra = sorted(f for f in os.listdir(gamedir)
                   if re.match(r'(?i)^(d6link\d\d\.dat|spoke\d\d\.tol)$', f))
    mdir = find_file(gamedir, 'models')
    mon = os.path.join(mdir, 'monster') if mdir else None
    if mon and os.path.isdir(mon):              # vehicle / collision BSPs (warship, raft, ...)
        files += sorted(os.path.relpath(os.path.join(mon, f), gamedir) for f in os.listdir(mon)
                        if f.lower().endswith('.bsp'))
    stats = {}
    fails = []
    for f in files + extra:
        ext = os.path.splitext(f)[1][1:].lower()
        if ext in LEVEL_EXTS:
            cls = LEVEL_EXTS[ext]
        elif ext == 'dat':
            cls = NavLinks
        else:
            cls = ObjectList
        data = _read(os.path.join(gamedir, f))
        try:
            obj = cls.parse(data)
            out = obj.to_bytes()
            ok = out == data
            raw = getattr(obj, '_raw', None) is not None or 'entities_raw' in getattr(obj, '_tails', {})
        except Exception as e:  # noqa
            ok, raw = False, False
            fails.append('%s: exception %r' % (f, e))
        tail = getattr(obj, '_tail', b'') if ok else b''
        s = stats.setdefault(ext, [0, 0, 0, 0])
        s[0] += 1
        s[1] += ok
        s[2] += bool(raw)
        s[3] += bool(tail)
        if not ok and not any(x.startswith(f + ':') for x in fails):
            fails.append('%s: round trip mismatch (%d vs %d bytes)' % (f, len(out), len(data)))
    problems = []
    for name in list_levels(gamedir):
        _consistency(gamedir, name, problems)
    print('round trip summary (files / identical / raw-text fallback / with tail bytes):')
    total = good = 0
    for ext in sorted(stats):
        n, ok, raw, tail = stats[ext]
        total += n
        good += ok
        print('  %-4s %4d %4d %4d %4d' % (ext, n, ok, raw, tail))
    print('TOTAL %d files, %d byte identical' % (total, good))
    for x in fails:
        print('FAIL', x)
    print('cross-file consistency problems: %d' % len(problems))
    for p in problems:
        print('  ', p)
    return not fails


def main(argv):
    if len(argv) >= 3 and argv[1] == '--selftest':
        sys.exit(0 if selftest(argv[2]) else 1)
    if len(argv) >= 3 and argv[1] == '--info':
        lv = Level(argv[2], argv[3])
        print(lv.bsp.summary())
        for ext, obj in sorted(lv.files.items()):
            print('  %-4s %s' % (ext, 'missing' if obj is None else lv.paths[ext]))
        return
    if len(argv) >= 3 and argv[1] == '--spokes':
        for sp in sorted(set(SPOKE_TABLE) | set(TERRAIN_SPOKES)):
            print('spoke %2d:' % sp)
            for b in spoke_bsps(sp, argv[2]):
                print('   [%s] %-9s origin=%s tiles=%s' % (b['slot'], b['name'], b['origin'], b['tile_rect']))
            lp = find_file(argv[2], 'D6LINK%02d.DAT' % sp)
            if lp:
                for l in NavLinks.load(lp).links:
                    print('      link bsp%d:nav%d <-> bsp%d:nav%d' % (l.bsp_a, l.nav_a, l.bsp_b, l.nav_b))
        return
    print(__doc__)
    print('usage: d6level.py --selftest GAMEDIR | --info GAMEDIR LEVEL | --spokes GAMEDIR')


if __name__ == '__main__':
    main(sys.argv)
