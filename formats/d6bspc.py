#!/usr/bin/env python3
"""
d6bspc.py - compile a TrenchBroom .map into a Deep6 level (.bsp v28 + .twd
+ .lgt/.rgb + .nvs/.l2n), using a qbsp with the Deep6 clip hulls.

Pipeline
  1. read the map (formats/d6map.py), give every texture a short alias
     (qbsp keeps 15 characters), mark water/lava/clip/skip brushes by alias
     ('*w..', '*lava..', 'clip', 'skip'), write a plain Valve-220 map
  2. run qbsp (ericw-tools 2.0 built by tools/compiler/build.sh, with hull 1
     set to x/y +-32, z -2..+4 and hull 2 to +-64, -2..+4 in map space,
     measured from the retail BSPs) and vis
  3. convert the Quake BSP 29 into Deep6 BSP 28 (convert_bsp29):
       map space (z up, right handed) -> engine (y up, left handed): swap y/z,
       face windings reversed (the retail BSPs are clockwise from the front
       in engine space), texinfo s/t scaled to the Deep6 projection
       (u = ((P - origin).s + s.w/4) / 32, origin 0), flags = .twd index
       (+0x8000 water, 0x2000 translucent, 0x20000 terrain, -1 nodraw),
       68 byte models (+ entity index), leaf ambient byte 0 = object light
  4. light from the light entities, shadows traced through the BSP:
     lightmaps (.lf/.ls, 32 unit luxels with a one luxel border, max 16x16,
     the hardware renderer needs them), vertex light (lump 8) and leaf light
  5. .twd from the textures (unchanged original textures are copied with
     their palettes; new images are quantised to a shared new palette)
  6. .lgt/.rgb from the light entities, a nav graph on the floors

The software-renderer lightmaps (.lfs/.lss) are not written.
Faces whose last vertex is not a corner are not drawn by the engine; the
converter rotates every face loop so it ends on a corner.

CLI
  d6bspc.py compile MAP --name NAME --out DIR [--textures DIR] [--game GAMEDIR]
                       [--qbsp PATH] [--vis PATH] [--fastvis] [--novis] [--nonav]
  d6bspc.py setup-trenchbroom PROJECTDIR [GAMEDIR]   game config + all textures as PNG
  d6bspc.py info BSP29
  (compile ... --install copies the level into --game, backing up what it replaces)
"""
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import d6level  # noqa: E402
import d6map    # noqa: E402

SDK = os.path.dirname(HERE)
SPECIAL_DIR = '_special'
TEX_EXTS = ('.png', '.tga', '.jpg', '.jpeg', '.bmp')


class CompileError(Exception):
    pass


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------
def find_tool(name, explicit=None):
    cands = []
    if explicit:
        cands.append(explicit)
    env = os.environ.get('D6_' + name.upper())
    if env:
        cands.append(env)
    for d in (os.path.join(SDK, 'tools', 'compiler', 'bin'),
              os.path.join(SDK, 'tools', 'compiler', 'ericw-tools', 'build', name)):
        cands.append(os.path.join(d, name))
        cands.append(os.path.join(d, name + '.exe'))
    w = shutil.which('d6' + name)
    if w:
        cands.append(w)
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise CompileError('%s not found: build it with tools/compiler/build.sh (Linux, macOS) or '
                       'tools/compiler/build.ps1 (Windows), or set D6_%s to the program'
                       % (name, name.upper()))


# --------------------------------------------------------------------------
# Quake BSP 29 reader (what qbsp writes)
# --------------------------------------------------------------------------
Q_TEXINFO = '<8f2i'


class Bsp29(object):
    def __init__(self, data):
        ver = struct.unpack_from('<i', data)[0]
        if ver != 29:
            raise CompileError('qbsp wrote BSP version %d (need 29; the map is too large '
                               'for BSP 29?)' % ver)
        lumps = [struct.unpack_from('<2i', data, 4 + 8 * i) for i in range(15)]

        def lump(i):
            o, n = lumps[i]
            return data[o:o + n]

        def rows(i, fmt, nt):
            buf = lump(i)
            sz = struct.calcsize(fmt)
            return [nt(*struct.unpack_from(fmt, buf, k * sz)) for k in range(len(buf) // sz)]

        L = d6level
        self.entities = L.parse_entities(bytes(lump(0)))
        self.planes = rows(1, '<4fi', L.Plane)
        tex = lump(2)
        self.miptex = []
        if tex:
            n = struct.unpack_from('<i', tex)[0]
            for k in range(n):
                o = struct.unpack_from('<i', tex, 4 + 4 * k)[0]
                self.miptex.append(tex[o:o + 16].split(b'\0')[0].decode('latin-1') if o >= 0 else '')
        self.vertices = rows(3, '<3f', L.Vertex)
        self.visibility = bytes(lump(4))
        self.nodes = rows(5, '<i2h6h2H', L.Node)
        buf = lump(6)
        self.texinfo = [struct.unpack_from(Q_TEXINFO, buf, k * 40) for k in range(len(buf) // 40)]
        self.faces = rows(7, '<2hi2h4Bi', L.Face)
        self.clipnodes = rows(9, '<i2h', L.ClipNode)
        self.leafs = rows(10, '<2i6h2H4B', L.Leaf)
        self.marksurfaces = array('H', lump(11))
        self.edges = rows(12, '<2H', L.Edge)
        self.surfedges = array('i', lump(13))
        buf = lump(14)
        self.models = [struct.unpack_from('<9f7i', buf, k * 64) for k in range(len(buf) // 64)]


# --------------------------------------------------------------------------
# map preprocessing
# --------------------------------------------------------------------------
class TexAliases(object):
    """real texture name + kind -> short qbsp name, and back."""

    def __init__(self):
        self.by_key = {}
        self.info = {}          # alias -> (real name, kind, surface flags)
        self.n = 0

    def alias(self, name, kind, flags):
        lname = name.lower()
        if lname in (SPECIAL_DIR + '/skip', 'skip'):
            return 'skip'
        if lname in (SPECIAL_DIR + '/clip', 'clip') or kind == 'clip':
            return 'clip'
        if lname in (SPECIAL_DIR + '/hint', 'hint'):
            return 'hint'
        if lname in (SPECIAL_DIR + '/nodraw', 'nodraw'):
            flags |= d6map.SURF_NODRAW
        key = (lname, kind, flags)
        a = self.by_key.get(key)
        if a is None:
            self.n += 1
            a = {'water': '*w%04d', 'lava': '*lava%04d'}.get(kind, 't%04d') % self.n
            self.by_key[key] = a
            self.info[a.lower()] = (name, kind, flags)
        return a


def prepare_map(ents):
    """Rewrite the map for qbsp: aliases, flags -> contents. Returns
    (qbsp map text, TexAliases, light entities, other point entities)."""
    al = TexAliases()
    out = []
    lights = []
    # TrenchBroom layers and groups (func_group) are world geometry
    world = d6map.Entity(list(ents[0].keys), list(ents[0].brushes))
    rest = []
    for e in ents[1:]:
        if e.classname == 'func_group':
            world.brushes.extend(e.brushes)
        else:
            rest.append(e)
    for e in [world] + rest:
        cn = e.classname
        if cn == 'light' or cn.startswith('light_'):
            lights.append(e)
            continue        # lights are ours; for qbsp they only cause false leaks in the clip hulls
        if cn in ('d6_comment',):
            continue
        ne = d6map.Entity(list(e.keys), [])
        for b in e.brushes:
            c = b.contents
            kind = 'water' if c & d6map.CONT_WATER else 'lava' if c & d6map.CONT_LAVA else \
                'clip' if c & d6map.CONT_CLIP else 'solid'
            nb = d6map.Brush()
            for f in b.faces:
                flags = f.flags
                if kind == 'water':
                    flags |= d6map.SURF_WATER
                elif kind == 'lava':
                    flags |= d6map.SURF_TRANS
                nf = d6map.Face(f.pts, al.alias(f.tex, kind, flags), f.u, f.v, f.rot, f.sx, f.sy,
                                std=f.std)
                nb.faces.append(nf)
            ne.brushes.append(nb)
        out.append(ne)
    return d6map.write(out, flags=False, header='// Game: Quake\n// Format: Valve\n'), al, lights


def write_alias_wad(path, aliases):
    """A WAD2 with a blank 128x128 miptex per alias, so qbsp knows every
    name (sizes only matter for face subdivision)."""
    names = sorted(set(list(aliases.info.keys()) + ['skip', 'clip', 'hint']))
    lumps = []
    data = bytearray(b'WAD2' + b'\0' * 8)
    for n in names:
        mt = bytearray(n.encode('latin-1')[:15].ljust(16, b'\0'))
        w = h = 128
        mt += struct.pack('<2I', w, h)
        o = 40
        offs = []
        for k in range(4):
            offs.append(o)
            o += (w >> k) * (h >> k)
        mt += struct.pack('<4I', *offs)
        mt += bytes(o - 40)
        lumps.append((len(data), len(mt), n))
        data += mt
    dirofs = len(data)
    for o, ln, n in lumps:
        data += struct.pack('<3i4b', o, ln, ln, 0x44, 0, 0, 0) + n.encode('latin-1')[:15].ljust(16, b'\0')
    struct.pack_into('<2i', data, 4, len(lumps), dirofs)
    with open(path, 'wb') as f:
        f.write(data)


# --------------------------------------------------------------------------
# textures
# --------------------------------------------------------------------------
def find_texture_file(name, texdirs):
    parts = name.replace('\\', '/').split('/')
    for d in texdirs:
        cur = d
        ok = True
        for p in parts[:-1]:
            nxt = None
            if os.path.isdir(cur):
                for f in os.listdir(cur):
                    if f.lower() == p.lower() and os.path.isdir(os.path.join(cur, f)):
                        nxt = os.path.join(cur, f)
                        break
            if nxt is None:
                ok = False
                break
            cur = nxt
        if not ok or not os.path.isdir(cur):
            continue
        want = parts[-1].lower()
        for f in os.listdir(cur):
            b, ext = os.path.splitext(f)
            if b.lower() == want and ext.lower() in TEX_EXTS:
                return os.path.join(cur, f)
    return None


def load_rgb(path):
    import numpy as np
    from PIL import Image
    im = Image.open(path).convert('RGB')
    if im.size != (128, 128):
        im = im.resize((128, 128), Image.LANCZOS)
    return np.array(im)


def source_texture(name, gamedir, cache):
    """If NAME is 'level/texname' and level.twd in the game has that texture,
    return (wad, index)."""
    if not gamedir or '/' not in name:
        return None
    lvl, tn = name.split('/', 1)
    key = lvl.lower()
    if key not in cache:
        p = d6level.find_file(gamedir, lvl + '.twd')
        cache[key] = d6level.TexWad.load(p) if p else None
    w = cache[key]
    if w is None:
        return None
    for i, t in enumerate(w.textures):
        if t.name.lower() == tn.lower():
            return w, i
    # exported names are made unique with ~N when a wad has duplicates
    if '~' in tn:
        base, _, k = tn.rpartition('~')
        hits = [i for i, t in enumerate(w.textures) if t.name.lower() == base.lower()]
        if k.isdigit() and int(k) < len(hits):
            return w, hits[int(k)]
    return None


def texture_rgb_np(w, i):
    import numpy as np
    wd, ht, raw = w.texture_rgb(i)
    return np.frombuffer(raw, np.uint8).reshape(ht, wd, 3)


def build_twd(names, texdirs, gamedir, log):
    """names: ordered real texture names. Returns (TexWad, {name: index})."""
    import numpy as np
    wad = d6level.TexWad()
    index = {}
    cache = {}
    palmap = {}
    new = []
    for name in names:
        path = find_texture_file(name, texdirs)
        src = source_texture(name, gamedir, cache)
        rgb = load_rgb(path) if path else None
        if src is not None:
            w, i = src
            orig = texture_rgb_np(w, i)
            if rgb is None or np.abs(orig.astype(int) - rgb.astype(int)).max() <= 8:
                t = w.textures[i]
                pk = w.palettes[t.palette]
                if pk not in palmap:
                    palmap[pk] = len(wad.palettes)
                    wad.palettes.append(pk)
                wad.textures.append(d6level.Texture(t.name_raw, palmap[pk], t.width, t.height, list(t.mips)))
                index[name] = len(wad.textures) - 1
                continue
        if rgb is None:
            log('warning: texture %s not found, using a checker' % name)
            yy, xx = np.mgrid[0:128, 0:128]
            c = (((yy // 16) + (xx // 16)) & 1).astype(np.uint8)
            rgb = np.dstack([c * 160 + 60, c * 60 + 60, c * 160 + 60]).astype(np.uint8)
        new.append((name, rgb))
    # new textures: shared palettes, up to 8 textures per palette
    for k in range(0, len(new), 8):
        group = new[k:k + 8]
        sheet = np.concatenate([g[1] for g in group], 1)
        pal = wad._new_palette(sheet)
        for name, rgb in group:
            short = name.split('/')[-1]
            index[name] = wad.add_texture(short, rgb, palette=pal)
    if len(wad.textures) > 4095:
        raise CompileError('too many textures (%d)' % len(wad.textures))
    return wad, index


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------
def plane_type(n):
    ax = [abs(c) for c in n]
    for i in range(3):
        if ax[i] == 1.0:
            return i
    return 3 + ax.index(max(ax))


class Tracer(object):
    """Line and point tests through a Deep6 BSP (engine space)."""

    def __init__(self, bsp, model=0):
        self.b = bsp
        m = bsp.models[model]
        self.head0 = m.head0
        self.heads = (m.head0, m.head1, m.head2)
        self.pl = [(p.nx, p.ny, p.nz, p.dist) for p in bsp.planes]
        self.nodes = [(n.planenum, n.front, n.back) for n in bsp.nodes]
        self.clip = [(c.planenum, c.front, c.back) for c in bsp.clipnodes]
        self.cont = [l.contents for l in bsp.leafs]

    def leaf(self, p):
        n = self.head0
        nodes, pl = self.nodes, self.pl
        while n >= 0:
            pn, f, bk = nodes[n]
            a = pl[pn]
            n = f if a[0] * p[0] + a[1] * p[1] + a[2] * p[2] - a[3] >= 0 else bk
        return -(n + 1)

    def contents(self, p):
        return self.cont[self.leaf(p)]

    def hull_contents(self, p, hull=1):
        n = self.heads[hull]
        while n >= 0:
            pn, f, bk = self.clip[n]
            a = self.pl[pn]
            n = f if a[0] * p[0] + a[1] * p[1] + a[2] * p[2] - a[3] >= 0 else bk
        return n

    def blocked(self, p1, p2, hull=0):
        """True if the segment touches solid."""
        return self._r(self.heads[hull], p1, p2, hull)

    def _r(self, n, p1, p2, hull):
        while True:
            if n < 0:
                if hull == 0:
                    return self.cont[-(n + 1)] == -2
                return n == -2
            pn, f, bk = self.nodes[n] if hull == 0 else self.clip[n]
            a = self.pl[pn]
            d1 = a[0] * p1[0] + a[1] * p1[1] + a[2] * p1[2] - a[3]
            d2 = a[0] * p2[0] + a[1] * p2[1] + a[2] * p2[2] - a[3]
            if d1 >= 0 and d2 >= 0:
                n = f
                continue
            if d1 < 0 and d2 < 0:
                n = bk
                continue
            t = d1 / (d1 - d2)
            mid = (p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t, p1[2] + (p2[2] - p1[2]) * t)
            near, far = (f, bk) if d1 >= 0 else (bk, f)
            if self._r(near, p1, mid, hull):
                return True
            n, p1 = far, mid


def _first_corner(q, loop):
    vs = []
    for e in loop:
        ed = q.edges[e] if e >= 0 else q.edges[-e]
        vi = ed.v0 if e >= 0 else ed.v1
        v = q.vertices[vi]
        vs.append((v.x, v.y, v.z))
    n = len(vs)
    for k in range(n):
        a, b, c = vs[k - 1], vs[k], vs[(k + 1) % n]
        u = (c[0] - b[0], c[1] - b[1], c[2] - b[2])
        w = (a[0] - b[0], a[1] - b[1], a[2] - b[2])
        cr = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
        if abs(cr[0]) + abs(cr[1]) + abs(cr[2]) > 1e-3:
            return k
    return 0


# --------------------------------------------------------------------------
# BSP 29 -> Deep6 28
# --------------------------------------------------------------------------
def convert_bsp29(q, aliases, texindex):
    """q: Bsp29 (map space). Returns a d6level.BSPFile (engine space) without
    light (vertex_light zero) and entities left as qbsp wrote them."""
    L = d6level
    b = L.BSPFile()
    sw = d6map.to_engine
    for p in q.planes:
        n = sw((p.nx, p.ny, p.nz))
        b.planes.append(L.Plane(n[0], n[1], n[2], p.dist, plane_type(n)))
    b.vertices = [L.Vertex(*sw(v)) for v in q.vertices]
    b.visibility = q.visibility
    for n in q.nodes:
        b.nodes.append(L.Node(n.planenum, n.front, n.back, n.minx, n.minz, n.miny,
                              n.maxx, n.maxz, n.maxy, n.firstface, n.numfaces))
    # texinfo
    for ti in q.texinfo:
        s = ti[0:4]
        t = ti[4:8]
        mt = ti[8]
        alias = q.miptex[mt].lower() if 0 <= mt < len(q.miptex) else ''
        real, kind, sflags = aliases.info.get(alias, (None, 'solid', 0))
        if real is None or sflags & d6map.SURF_NODRAW:
            flags = -1
        else:
            flags = texindex.get(real, 0)
            if sflags & d6map.SURF_WATER:
                flags |= 0x8000
            if sflags & d6map.SURF_TRANS:
                flags |= 0x2000
            if sflags & d6map.SURF_TERRAIN:
                flags |= 0x20000
        ss = sw(s[:3])
        tt = sw(t[:3])
        # Deep6: u = (P.s' + s'w/4)/32 = u_texel/128 with 128 texel textures
        b.texinfo.append(L.TexInfo(ss[0] / 4.0, ss[1] / 4.0, ss[2] / 4.0, s[3],
                                   tt[0] / 4.0, tt[1] / 4.0, tt[2] / 4.0, t[3],
                                   0.0, 0.0, 0.0, flags, 0))
    # faces: reverse winding (mirror)
    se = array('i')
    for f in q.faces:
        loop = q.surfedges[f.firstedge:f.firstedge + f.numedges]
        first = len(se)
        rev = [-x for x in reversed(loop)]
        # the engine skips a face whose last vertex is not a corner
        # (SetWorldCornerFlags_ bit n-1, tested in bsp_3DCard_PolyDraw_)
        k = _first_corner(q, rev)
        se.extend(rev[k + 1:] + rev[:k + 1])
        b.faces.append(L.Face(f.planenum, f.side, first, f.numedges, f.texinfo, 255, 255, 255, 255, -1))
    b.surfedges = se
    b.edges = list(q.edges)
    b.vertex_light = array('H', [0] * len(se))
    b.clipnodes = list(q.clipnodes)
    for l in q.leafs:
        b.leafs.append(L.Leaf(l.contents, l.visofs, l.minx, l.minz, l.miny, l.maxx, l.maxz, l.maxy,
                              l.firstmarksurface, l.nummarksurfaces, 0, 255, 255, 255))
    b.marksurfaces = array('H', q.marksurfaces)
    for i, m in enumerate(q.models):
        mn, mx, org = sw(m[0:3]), sw(m[3:6]), sw(m[6:9])
        b.models.append(L.Model(mn[0], mn[1], mn[2], mx[0], mx[1], mx[2], org[0], org[1], org[2],
                                m[9], m[10], m[11], m[12], m[13], m[14], m[15], 0 if i == 0 else 1))
    b.entities = q.entities
    return b


# --------------------------------------------------------------------------
# lights
# --------------------------------------------------------------------------
class Light(object):
    def __init__(self, pos, intensity, color=(255, 255, 255)):
        self.pos = pos            # engine space, BSP units
        self.intensity = intensity
        self.color = color


def lights_from_entities(ents):
    out = []
    for e in ents:
        if not (e.classname == 'light' or e.classname.startswith('light_')):
            continue
        try:
            o = [float(x) for x in e.get('origin', '0 0 0').split()]
        except ValueError:
            continue
        inten = e.get('d6light') or e.get('light') or '200'
        try:
            inten = float(inten)
        except ValueError:
            inten = 200.0
        col = e.get('_color') or e.get('color')
        rgb = (255, 255, 255)
        if col:
            try:
                c = [float(x) for x in col.split()[:3]]
                if max(c) <= 1.0:
                    c = [x * 255 for x in c]
                rgb = tuple(int(max(0, min(255, round(x)))) for x in c)
            except ValueError:
                pass
        out.append(Light(d6map.to_engine(o), inten, rgb))
    return out


def face_normal(b, f):
    p = b.planes[f.planenum]
    s = -1.0 if f.side else 1.0
    return (p.nx * s, p.ny * s, p.nz * s)


LM_VECS = {0: ((0, 0, 1), (0, 1, 0)), 1: ((1, 0, 0), (0, 0, 1)), 2: ((1, 0, 0), (0, 1, 0))}
LUXEL = 32.0          # BSP units per luxel (bsp_3DCard_PolyDraw_: (P.v - smin) / 32 / 16)
LM_MAX = 16           # CC_SetLMapCache_ refuses lightmaps over 16x16 (the face is then not drawn)


def lightmap_axes(p):
    """MakeLightVecs_: axis pair by plane type; a type 4 (y major) plane
    that is not nearly horizontal uses the x or z pair."""
    t = p.type
    if t == 4 and abs(p.ny) < 0.95:
        t = 3 if abs(p.nx) > abs(p.nz) else 5
    return LM_VECS[t % 3]


def face_lightmap_extent(b, f):
    p = b.planes[f.planenum]
    s, t = lightmap_axes(p)
    pts = b.face_points(f)
    ss = [q[0] * s[0] + q[1] * s[1] + q[2] * s[2] for q in pts]
    ts = [q[0] * t[0] + q[1] * t[1] + q[2] * t[2] for q in pts]
    # as in all retail .lf files: one luxel of border on each side
    s0 = math.floor(min(ss) / LUXEL) - 1
    t0 = math.floor(min(ts) / LUXEL) - 1
    w = int(math.ceil(max(ss) / LUXEL)) - s0 + 2
    h = int(math.ceil(max(ts) / LUXEL)) - t0 + 2
    return s, t, int(s0 * LUXEL), int(t0 * LUXEL), w, h


# Fitted to the retail lightmaps (14279 luxels of crypta, minesa, ogrea, licha,
# pyrama, sunkena; RMSE 5.8 of 0..30): ambient + peak * (1 - d/R) * (0.4 + 0.6 cos)
# per visible light, R = the light's intensity in BSP units.
DEFAULT_AMBIENT = 3.5
DEFAULT_PEAK = 9.6
# The retail vertex light (per surfedge; the leaf object light is its mean) is
# much brighter and flatter: fitted ambient 15.9, peak 4.1 (RMSE 7 of 30).
VERTEX_AMBIENT_ADD = 12.4    # vertex ambient = ambient + this
VERTEX_PEAK_SCALE = 0.43     # vertex peak = peak * this


class LightModel(object):
    """Shared light function for vertex light and lightmaps: value 0..30
    (shade row) and an RGB tint."""

    CELL = 128.0

    def __init__(self, lights, ambient=DEFAULT_AMBIENT, peak=DEFAULT_PEAK, tracer=None):
        self.lp = [(l.pos, float(l.intensity), l.color) for l in lights]
        self.ambient = ambient
        self.peak = peak
        self.tr = tracer
        self.grid = {}
        c = self.CELL
        for i, (pos, R, _) in enumerate(self.lp):
            lo = [int(math.floor((pos[k] - R) / c)) for k in range(3)]
            hi = [int(math.floor((pos[k] + R) / c)) for k in range(3)]
            for x in range(lo[0], hi[0] + 1):
                for y in range(lo[1], hi[1] + 1):
                    for z in range(lo[2], hi[2] + 1):
                        self.grid.setdefault((x, y, z), []).append(i)

    def at(self, P, n):
        Q = (P[0] + n[0] * 0.5, P[1] + n[1] * 0.5, P[2] + n[2] * 0.5)
        tot = self.ambient
        rgb = [self.ambient] * 3
        c = self.CELL
        cell = (int(math.floor(P[0] / c)), int(math.floor(P[1] / c)), int(math.floor(P[2] / c)))
        for i in self.grid.get(cell, ()):
            lpos, R, col = self.lp[i]
            dx, dy, dz = lpos[0] - P[0], lpos[1] - P[1], lpos[2] - P[2]
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            if d >= R:
                continue
            cs = (dx * n[0] + dy * n[1] + dz * n[2]) / (d or 1.0)
            if cs <= 0 and d > 1.0:
                continue
            if self.tr is not None and self.tr.blocked(lpos, Q):
                continue
            a = self.peak * (1.0 - d / R) * (0.4 + 0.6 * max(0.0, cs))
            tot += a
            for k in range(3):
                rgb[k] += a * col[k] / 255.0
        return min(30.0, tot), rgb


def compute_lightmaps(b, model, log=print):
    """.lf/.ls (hardware, RGB565) and .lfs/.lss (software, 0..31) for every face.
    Returns (lf, ls, lfs, lss) d6level objects."""
    L = d6level
    tr = model.tr
    rows_hw, rows_sw = [], []
    blob_hw = bytearray()
    blob_sw = bytearray()
    over = 0
    for fi, f in enumerate(b.faces):
        n = face_normal(b, f)
        p = b.planes[f.planenum]
        s, t, smin, tmin, w, h = face_lightmap_extent(b, f)
        if w > LM_MAX or h > LM_MAX:
            over += 1
        pts = b.face_points(f)
        cx = sum(q[0] for q in pts) / len(pts)
        cy = sum(q[1] for q in pts) / len(pts)
        cz = sum(q[2] for q in pts) / len(pts)
        # third axis = the one not in s/t; solve the plane for it
        ax3 = [k for k in range(3) if s[k] == 0 and t[k] == 0][0]
        ks = s.index(1)
        kt = t.index(1)
        nn = (p.nx, p.ny, p.nz)
        vals = []
        rgbs = []
        for j in range(h):
            for i in range(w):
                P = [0.0, 0.0, 0.0]
                P[ks] = smin + (i + 0.5) * LUXEL
                P[kt] = tmin + (j + 0.5) * LUXEL
                if abs(nn[ax3]) > 1e-6:
                    P[ax3] = (p.dist - nn[ks] * P[ks] - nn[kt] * P[kt]) / nn[ax3]
                else:
                    P[ax3] = (cx, cy, cz)[ax3]
                # pull samples that are inside solid towards the face centre
                q = tuple(P)
                for step in range(6):
                    if tr is None or tr.contents((q[0] + n[0], q[1] + n[1], q[2] + n[2])) != -2:
                        break
                    q = ((q[0] + cx) / 2, (q[1] + cy) / 2, (q[2] + cz) / 2)
                v, rgb = model.at(q, n)
                vals.append(v)
                rgbs.append(rgb)
        off_hw = len(blob_hw)
        for v, rgb in zip(vals, rgbs):
            # RGB565, full white at value 30
            r = int(max(0, min(31, round(rgb[0] / 30.0 * 31))))
            g = int(max(0, min(63, round(rgb[1] / 30.0 * 63))))
            bb = int(max(0, min(31, round(rgb[2] / 30.0 * 31))))
            blob_hw += struct.pack('<H', (r << 11) | (g << 5) | bb)
        rows_hw.append(L.FaceLight(smin, tmin, w, h, w * h, off_hw))
        off_sw = len(blob_sw)
        blob_sw += bytes(int(max(0, min(31, round(v)))) for v in vals)
        rows_sw.append(L.FaceLight(smin, tmin, w, h, w * h, off_sw))
    if over:
        log('warning: %d faces have lightmaps over 16x16 luxels and will not be drawn; '
            'split them (smaller brushes) or lower qbsp -subdivide' % over)
    lf = L.FaceLightInfo(rows_hw)
    lfs = L.FaceLightInfo(rows_sw)
    ls = L.LightData(bytes(blob_hw))
    lss = L.LightData(bytes(blob_sw))
    return lf, ls, lfs, lss


def compute_vertex_light(b, lights, ambient=DEFAULT_AMBIENT, peak=DEFAULT_PEAK, tracer=None, log=None,
                         model=None):
    """Fill b.vertex_light (0..30 per surfedge) and leaf light bytes, with the
    lightmap light model brightened like the retail vertex light
    (VERTEX_AMBIENT_ADD, VERTEX_PEAK_SCALE)."""
    lm = model or LightModel(lights, ambient + VERTEX_AMBIENT_ADD, peak * VERTEX_PEAK_SCALE, tracer or Tracer(b))
    vl = array('H', [0] * len(b.surfedges))
    verts = [(v.x, v.y, v.z) for v in b.vertices]
    for f in b.faces:
        n = face_normal(b, f)
        for k, vi in enumerate(b.face_vertex_indices(f)):
            v, _ = lm.at(verts[vi], n)
            vl[f.firstedge + k] = int(max(0, min(30, round(v))))
    b.vertex_light = vl
    # leaf light: mean light of the leaf's faces
    L = d6level
    newleafs = []
    for i, l in enumerate(b.leafs):
        vals = []
        for m in range(l.firstmarksurface, l.firstmarksurface + l.nummarksurfaces):
            f = b.faces[b.marksurfaces[m]]
            vals.extend(vl[f.firstedge:f.firstedge + f.numedges])
        amb = int(round(sum(vals) / len(vals))) if vals else (0 if l.contents == -2 else int(ambient))
        newleafs.append(L.Leaf(l.contents, l.visofs, l.minx, l.miny, l.minz, l.maxx, l.maxy, l.maxz,
                               l.firstmarksurface, l.nummarksurfaces, amb if i else 0,
                               255 if i else 0, 255 if i else 0, 255 if i else 0))
    b.leafs = newleafs


# --------------------------------------------------------------------------
# entities
# --------------------------------------------------------------------------
def convert_entities(qents, lights):
    """qbsp's entity lump (map space) -> Deep6 entity lump."""
    out = []
    used_ids = set()
    for e in qents:
        d = dict(e)
        if d.get('d6entid'):
            try:
                used_ids.add(int(d['d6entid']))
            except ValueError:
                pass
    nid = max(used_ids | {0}) + 1
    for e in qents:
        d = dict(e)
        cn = d.get('classname', '')
        if cn == 'worldspawn':
            out.append([('classname', 'worldspawn'), ('worldtype', d.get('worldtype', '0'))])
        elif 'model' in d:
            keys = [('model', d['model'])]
            if not d.get('d6entid'):
                d['d6entid'] = str(nid)
                nid += 1
            dflt = {'d6flags': '0', 'd6rotz': '0', 'd6roty': '0', 'd6rotx': '0',
                    'd6maxmove': '0', 'd6axis': '1', 'd6type': '1'}
            for k in ('d6flags', 'd6rotz', 'd6roty', 'd6rotx', 'd6entid', 'd6maxmove', 'd6axis', 'd6type'):
                keys.append((k, d.get(k) or dflt.get(k, '0')))
            if d.get('d6string'):
                keys.append(('d6string', d['d6string'][:15]))
            keys.append(('classname', 'func_door' if cn.startswith('func_') else cn))
            out.append(keys)
    # lights go to .lgt/.rgb only (the retail entity lumps list a few of them,
    # nothing reads them there)
    return out


# --------------------------------------------------------------------------
# nav graph
# --------------------------------------------------------------------------
def generate_nav(b, tracer=None, spacing=128.0, height=64.0, max_link=320.0, first_id=0):
    """Nav points on walkable floors (spacing BSP units apart, `height` above
    the floor like the retail graphs), linked by line of sight."""
    tr = tracer or Tracer(b)
    m = b.models[0]
    cand = []
    for fi in range(m.firstface, m.firstface + m.numfaces):
        f = b.faces[fi]
        n = face_normal(b, f)
        if n[1] < 0.7:
            continue
        ti = b.texinfo[f.texinfo]
        if ti.flags != -1 and ti.flags & 0xA000:
            continue
        pts = b.face_points(f)
        xs = [p[0] for p in pts]
        zs = [p[2] for p in pts]
        x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
        samples = []
        gx = math.floor(x0 / spacing) * spacing + spacing / 2
        while gx < x1:
            gz = math.floor(z0 / spacing) * spacing + spacing / 2
            while gz < z1:
                samples.append((gx, gz))
                gz += spacing
            gx += spacing
        for (x, z) in samples:
            if not _in_poly_xz(pts, x, z):
                continue
            # height of the plane at x, z
            p = b.planes[f.planenum]
            if abs(p.ny) < 1e-6:
                continue
            y = (p.dist - p.nx * x - p.nz * z) / p.ny
            cand.append((x, y, z))
    pts = []
    for c in cand:
        q = (c[0], c[1] + height, c[2])
        if tr.contents(q) == -2 or tr.contents((c[0], c[1] + 4, c[2])) == -2:
            continue
        if tr.blocked((c[0], c[1] + 2, c[2]), q):
            continue                         # no head room
        if any(abs(q[0] - o[0]) < spacing * 0.5 and abs(q[2] - o[2]) < spacing * 0.5
               and abs(q[1] - o[1]) < 32 for o in pts):
            continue
        pts.append(q)
    links = [[] for _ in pts]
    for i, a in enumerate(pts):
        near = sorted((math.dist(a, c), j) for j, c in enumerate(pts) if j != i)
        for d, j in near:
            if d > max_link or len(links[i]) >= 6:
                break
            if j in links[i] or len(links[j]) >= 6:
                continue
            c = pts[j]
            if abs(a[1] - c[1]) > 48:
                continue
            if tr.blocked(a, c) or tr.blocked((a[0], a[1] - height + 8, a[2]), (c[0], c[1] - height + 8, c[2])):
                continue
            # drop links that skip over a closer point in the same direction
            links[i].append(j)
            links[j].append(i)
    nv = d6level.NavPoints()
    for i, p in enumerate(pts):
        l = (links[i] + [-1] * 6)[:6]
        nv.points.append(d6level.NavPoint(p[0], p[1], p[2], 0.0, l[0], l[1], l[2], l[3], l[4], l[5],
                                          0, first_id + i))
    nv.unknown = first_id + len(pts) + 1
    return nv, build_leaf2nav(b, pts, tr)


def build_leaf2nav(b, pts, tracer=None):
    """.l2n for a BSP: per leaf the nav point inside it, else the nearest one
    in sight (600 units)."""
    tr = tracer or Tracer(b)
    l2n = d6level.Leaf2Nav()
    leafpts = {}
    for i, p in enumerate(pts):
        leafpts.setdefault(tr.leaf(p), []).append(i)
    nav = array('h', [-1] * len(b.leafs))
    for li, l in enumerate(b.leafs):
        if l.contents == -2 or li == 0:
            continue
        if li in leafpts:
            nav[li] = leafpts[li][0]
            continue
        c = ((l.minx + l.maxx) / 2.0, (l.miny + l.maxy) / 2.0, (l.minz + l.maxz) / 2.0)
        best = None
        for d, i in sorted((math.dist(c, p), i) for i, p in enumerate(pts)):
            if d > 600:
                break
            if not tr.blocked(c, pts[i]):
                best = i
                break
        if best is not None:
            nav[li] = best
    l2n.nav = nav
    return l2n


def reuse_nav(b, nvs, tracer=None):
    """Keep an existing nav graph (ids are used by events and D6LINK files)
    and rebuild only the leaf table for the new BSP."""
    pts = [(p.x, p.y, p.z) for p in nvs.points]
    return nvs, build_leaf2nav(b, pts, tracer)


def _in_poly_xz(pts, x, z):
    inside = False
    n = len(pts)
    for i in range(n):
        x1, z1 = pts[i][0], pts[i][2]
        x2, z2 = pts[(i + 1) % n][0], pts[(i + 1) % n][2]
        if (z1 > z) != (z2 > z):
            xi = x1 + (z - z1) * (x2 - x1) / (z2 - z1)
            if x < xi:
                inside = not inside
    return inside


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def default_texdirs(map_path):
    d = []
    md = os.path.dirname(os.path.abspath(map_path))
    for c in (os.path.join(md, 'textures'), os.path.join(os.path.dirname(md), 'textures'),
              os.path.join(SDK, 'tools', 'trenchbroom', 'textures')):
        if os.path.isdir(c):
            d.append(c)
    return d


def compile_map(map_path, name, outdir, texdirs=None, gamedir=None, qbsp=None, vis=None,
                fastvis=False, novis=False, nav=True, log=print, keep_temp=False, keep_nav=None):
    """Compile MAP into OUTDIR/NAME.{bsp,twd,lgt,rgb,nvs,l2n}. Returns a dict
    with statistics and the list of written files."""
    texdirs = list(texdirs or []) + default_texdirs(map_path)
    ents = d6map.load(map_path)
    if not ents or ents[0].classname != 'worldspawn':
        raise CompileError('the first entity of the map must be worldspawn')
    qmap, aliases, light_ents = prepare_map(ents)
    lights = lights_from_entities(light_ents)
    tmp = tempfile.mkdtemp(prefix='d6bspc_')
    try:
        mp = os.path.join(tmp, 'level.map')
        write_alias_wad(os.path.join(tmp, 'aliases.wad'), aliases)
        qmap = qmap.replace('"classname" "worldspawn"\n', '"classname" "worldspawn"\n"wad" "aliases.wad"\n', 1)
        with open(mp, 'w', encoding='latin-1') as f:
            f.write(qmap)
        qb = find_tool('qbsp', qbsp)
        log('qbsp: %s' % qb)
        r = subprocess.run([qb, '-nopercent', '-tjunc', 'rotate', mp], cwd=tmp, capture_output=True, text=True)
        out = r.stdout + r.stderr
        bspp = os.path.join(tmp, 'level.bsp')
        kinds = {}
        for line in out.splitlines():
            if 'WARNING' in line or 'LEAK' in line.upper() or 'ERROR' in line.upper():
                k = re.sub(r'[-0-9.(), ]+', ' ', line.strip())[:60]
                kinds.setdefault(k, [0, line.strip()])[0] += 1
        for k, (n, first) in sorted(kinds.items(), key=lambda x: -x[1][0])[:12]:
            log('  %s%s' % (first[:160], ' (x%d)' % n if n > 1 else ''))
        if r.returncode != 0 or not os.path.exists(bspp):
            raise CompileError('qbsp failed:\n' + out[-3000:])
        # only a leak in hull 0 (the drawn geometry) matters; the clip hulls
        # (64 units wide) often "leak" through entities standing near walls
        h0 = out.split('Processing hull 1')[0]
        leaked = 'leak file' in h0.lower() or 'reached occupant' in h0.lower()
        if leaked:
            log('warning: the map leaks (it is not sealed); the outside is kept, '
                'vis is skipped. See level.pts in TrenchBroom (File > Load Point File).')
            if keep_temp or True:
                os.makedirs(outdir, exist_ok=True)
                shutil.copy(os.path.join(tmp, 'level.pts'), os.path.join(outdir, name + '.pts'))
        if not novis and not leaked:
            vt = find_tool('vis', vis)
            args = [vt] + (['-fast'] if fastvis else []) + [bspp]
            r = subprocess.run(args, cwd=tmp, capture_output=True, text=True)
            if r.returncode != 0:
                log('warning: vis failed, every leaf sees every other\n' + (r.stdout + r.stderr)[-1500:])
        q = Bsp29(open(bspp, 'rb').read())
    finally:
        if not keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            log('temp: %s' % tmp)
    # textures actually used
    used = []
    for ti in q.texinfo:
        mt = ti[8]
        a = q.miptex[mt].lower() if 0 <= mt < len(q.miptex) else ''
        info = aliases.info.get(a)
        if info and not info[2] & d6map.SURF_NODRAW and info[0] not in used:
            used.append(info[0])
    wad, texindex = build_twd(used, texdirs, gamedir, log)
    b = convert_bsp29(q, aliases, texindex)
    b.entities = convert_entities(q.entities, lights)
    ws = ents[0]
    def wsfloat(key, default):
        try:
            return float(ws.get(key, default))
        except ValueError:
            return default
    ambient = wsfloat('d6ambient', DEFAULT_AMBIENT)
    peak = wsfloat('d6lightpeak', DEFAULT_PEAK)
    tr = Tracer(b)
    log('lighting %d faces, %d lights (ambient %g, peak %g)' % (len(b.faces), len(lights), ambient, peak))
    lmodel = LightModel(lights, ambient, peak, tracer=tr)
    compute_vertex_light(b, lights, ambient, peak, tracer=tr)
    lf, ls, lfs, lss = compute_lightmaps(b, lmodel, log)
    lim = []
    if len(b.nodes) > 9999:
        lim.append('nodes %d > 9999' % len(b.nodes))
    if len(b.faces) > 19999:
        lim.append('faces %d > 19999' % len(b.faces))
    if lim:
        raise CompileError('the level is over the engine limits: ' + ', '.join(lim))
    os.makedirs(outdir, exist_ok=True)
    files = {}
    files['bsp'] = b.to_bytes()
    files['twd'] = wad.to_bytes()
    files['lf'] = lf.to_bytes()
    files['ls'] = ls.to_bytes()
    ll = d6level.LightList()
    ll.lights = [d6level.PointLight(l.pos[0], l.pos[1], l.pos[2], l.intensity) for l in lights]
    files['lgt'] = ll.to_bytes()
    lc = d6level.LightColors()
    lc.colors = array('I', [c[0] | (c[1] << 8) | (c[2] << 16) for c in (l.color for l in lights)])
    files['rgb'] = lc.to_bytes()
    nnav = 0
    if keep_nav is None and gamedir:
        keep_nav = d6level.find_file(gamedir, name + '.nvs')
    if nav and keep_nav:
        nv, l2n = reuse_nav(b, d6level.NavPoints.load(keep_nav), tr)
        log('kept the nav graph of %s (%d points), leaf table rebuilt' % (keep_nav, len(nv.points)))
        nnav = len(nv.points)
        files['nvs'] = nv.to_bytes()
        files['l2n'] = l2n.to_bytes()
    elif nav:
        nv, l2n = generate_nav(b, tracer=tr)
        nnav = len(nv.points)
        files['nvs'] = nv.to_bytes()
        files['l2n'] = l2n.to_bytes()
    written = []
    for ext, data in files.items():
        p = os.path.join(outdir, name + '.' + ext)
        with open(p + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(p + '.tmp', p)
        written.append(p)
    stats = dict(files=written, faces=len(b.faces), leafs=len(b.leafs), nodes=len(b.nodes),
                 models=len(b.models), textures=len(wad.textures), lights=len(lights),
                 nav=nnav, leaked=leaked, vis=bool(q.visibility))
    log('%s: %d faces, %d leafs, %d models, %d textures, %d lights, %d nav points%s'
        % (name, stats['faces'], stats['leafs'], stats['models'], stats['textures'],
           stats['lights'], nnav, ' (LEAKS)' if leaked else ''))
    return stats


LEVEL_EXTS = ('bsp', 'twd', 'lf', 'ls', 'lfs', 'lss', 'lgt', 'rgb', 'int', 'nvs', 'l2n', 'lt')


def install_level(outdir, name, gamedir, log=print):
    """Copy a compiled level into the game folder. Every file of that level
    already there is first copied to <game>/d6edit_backup/<date-time>/; stale
    files the compiler does not write (software lightmaps .lfs/.lss, .lt)
    are moved there. The .bol (objects) is left alone. Returns the backup dir."""
    import time
    bdir = os.path.join(gamedir, 'd6edit_backup', time.strftime('%Y%m%d-%H%M%S') + '-' + name)
    os.makedirs(bdir, exist_ok=True)
    new = {}
    for ext in LEVEL_EXTS:
        src = os.path.join(outdir, name + '.' + ext)
        if os.path.exists(src):
            new[ext] = src
    for ext in LEVEL_EXTS:
        old = d6level.find_file(gamedir, name + '.' + ext)
        if old:
            shutil.copy2(old, bdir)
            if ext not in new:
                os.remove(old)
                log('moved stale %s to the backup' % os.path.basename(old))
    for ext, src in new.items():
        old = d6level.find_file(gamedir, name + '.' + ext)
        dst = old or os.path.join(gamedir, name.lower() + '.' + ext)
        # write a new inode (game folders are often hard linked copies)
        with open(src, 'rb') as f:
            data = f.read()
        with open(dst + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(dst + '.tmp', dst)
    log('installed %s into %s (backup: %s)' % (name, gamedir, bdir))
    return bdir


def _tb_game_path(tb_user_dir, project, log=print):
    """Set Preferences > Games > Deep6 > Game path to the project folder
    (TrenchBroom's Preferences.json; the key uses the game's name)."""
    import json
    pf = os.path.join(tb_user_dir, 'Preferences.json')
    prefs = {}
    if os.path.exists(pf):
        try:
            with open(pf, encoding='utf-8') as f:
                prefs = json.load(f)
        except (OSError, ValueError) as e:
            log('TrenchBroom preferences not readable (%s): set the game path by hand' % e)
            return
    prefs['Games/%s/Path' % d6map.TB_GAME] = os.path.abspath(project)
    try:
        with open(pf + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(prefs, f, indent=4)
        os.replace(pf + '.tmp', pf)
        log('TrenchBroom game path set to %s (close TrenchBroom first, it rewrites its '
            'preferences on exit)' % os.path.abspath(project))
    except OSError as e:
        log('cannot write %s (%s): set the game path by hand' % (pf, e))


def setup_trenchbroom(project, gamedir=None, tb_user_dir=None, log=print):
    """Prepare a TrenchBroom project folder: the Deep6 game configuration is
    copied to TrenchBroom's user games folder, all level textures are
    exported to PROJECT/textures and the special textures written. In
    TrenchBroom: Preferences > Deep6 > Game path = PROJECT."""
    import d6bspdc
    src = os.path.join(SDK, 'tools', 'trenchbroom', 'Deep6')
    if tb_user_dir is None:
        if sys.platform.startswith('win'):
            tb_user_dir = os.path.join(os.environ.get('APPDATA', ''), 'TrenchBroom')
        elif sys.platform == 'darwin':
            tb_user_dir = os.path.expanduser('~/Library/Application Support/TrenchBroom')
        else:
            tb_user_dir = os.path.expanduser('~/.TrenchBroom')
    dst = os.path.join(tb_user_dir, 'games', 'Deep6')
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(src):
        shutil.copy2(os.path.join(src, f), dst)
    log('TrenchBroom game configuration: %s' % dst)
    _tb_game_path(tb_user_dir, project, log)
    tex = os.path.join(project, 'textures')
    os.makedirs(os.path.join(project, 'maps'), exist_ok=True)
    if gamedir:
        n = 0
        for f in sorted(os.listdir(gamedir)):
            if f.lower().endswith('.twd') and f.lower() != 'syswat.twd':
                d6bspdc.export_textures(d6level.TexWad.load(os.path.join(gamedir, f)), f[:-4], tex)
                n += 1
        log('exported the textures of %d levels to %s' % (n, tex))
    d6bspdc.write_special_textures(tex)
    return dst


def main(argv):
    a = argv[1:]
    if not a:
        print(__doc__)
        return 1
    if a[0] == 'compile':
        import argparse
        ap = argparse.ArgumentParser(prog='d6bspc.py compile')
        ap.add_argument('map')
        ap.add_argument('--name', required=True)
        ap.add_argument('--out', required=True)
        ap.add_argument('--textures', action='append', default=[])
        ap.add_argument('--game')
        ap.add_argument('--qbsp')
        ap.add_argument('--vis')
        ap.add_argument('--fastvis', action='store_true')
        ap.add_argument('--novis', action='store_true')
        ap.add_argument('--nonav', action='store_true')
        ap.add_argument('--newnav', action='store_true', help='generate a new nav graph even if NAME.nvs exists in --game')
        ap.add_argument('--nav', help='keep the nav points of this .nvs')
        ap.add_argument('--keep', action='store_true')
        ap.add_argument('--install', action='store_true', help='copy the result into --game (with backup)')
        o = ap.parse_args(a[1:])
        try:
            compile_map(o.map, o.name, o.out, o.textures, o.game, o.qbsp, o.vis, o.fastvis,
                        o.novis, not o.nonav, keep_temp=o.keep,
                        keep_nav=(o.nav or None) if not o.newnav else '')
            if o.install:
                if not o.game:
                    raise CompileError('--install needs --game')
                install_level(o.out, o.name, o.game)
        except CompileError as e:
            print('error:', e)
            return 2
        return 0
    if a[0] == 'setup-trenchbroom' and len(a) >= 2:
        setup_trenchbroom(a[1], a[2] if len(a) > 2 else None)
        return 0
    if a[0] == 'info' and len(a) > 1:
        q = Bsp29(open(a[1], 'rb').read())
        print('planes %d nodes %d faces %d leafs %d clip %d models %d miptex %s'
              % (len(q.planes), len(q.nodes), len(q.faces), len(q.leafs), len(q.clipnodes),
                 len(q.models), q.miptex[:20]))
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
