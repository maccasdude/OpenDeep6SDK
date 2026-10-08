#!/usr/bin/env python3
"""
d6model.py - decoder for Wizards & Warriors (Deep6 engine, 2000) 3D models (models/**/*.mdl).

Decodes geometry (per-frame vertex paths), texture coordinates, polygons, textures
and palettes well enough to draw models textured in an editor, and maps D6MONS /
D6ITEM / D6PROP records to .mdl paths through the model-name tables compiled into
deep6.exe (mdldata.c: _MonMDLData, _ItemMDLData, _PropMDLData).

Source: Model_Read_ / Model_ResetTextures_ / Part_Draw_ / Model_BoundSphere_ (model.c),
drawTri_ (mpoly.c), LoadMonsterModel_ (monster.c), LoadItemObject_ / LoadPropObject_
(scenload.c), RefPal16_Calculate_ (pal16.c), GraphObj_UpdateMatrix_ / Mat3_RotateY_.
See docs/formats/models.md.

Conventions (model space == world units, no scale applied by the engine):
  * y is up; the origin is the object's ground point (feet / base), x/z centred.
  * at yaw 0 the model faces +Z.  The engine places a model with
      world = v * RotY(yaw) + pos   (row vector; yaw in 1/1024 turns),
      RotY = [[c,0,-s],[0,1,0],[s,0,c]]  ->  local +Z maps to (sin a, 0, cos a),
    which is the engine's usual "forward" vector for heading a.
  * texture coordinates in the file are texels; Model.mesh() returns them / (w, h).

API:
  m = load_model(gamedir, 'monster/skeleton.mdl')       # path relative to models/
  m.parts, m.textures (list of Texture, global index), m.anims, m.default_frame
  mesh = m.mesh(frame=None, mip=0, groups=(0,))          # dict of numpy arrays
  model_for(gamedir, 'M'|'I'|'P', recno) -> 'monster/skeleton.mdl' (relative to models/)

CLI:
  python3 d6model.py --render GAMEDIR M 1 out.png [--yaw DEG] [--frame N] [--size PX]
  python3 d6model.py --render GAMEDIR path/rel/to/models.mdl out.png
  python3 d6model.py --selftest GAMEDIR
  python3 d6model.py --info GAMEDIR M 1
Plain ASCII only.
"""
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import d6data  # noqa: E402

MAGIC = 0x4D444C20          # ' LDM' on disk
FLAG_FRAMEBITS = 0x1000000  # version bit: per-frame "stored" byte table follows nframes
PAL_SIZES = {0: 0x4000, 3: 0x4000, 1: 0x300, 2: 0x40}

# poly flag byte (Part_Draw_)
PF_GROUP = 0x1F    # mesh group; drawn only if gobj visibility[group] != 0 (group 0 on by default)
PF_TRANS = 0x20    # 50% translucent
PF_MASKED = 0x40   # colour index 0 is transparent
PF_TWOSIDED = 0x80  # no back-face culling (drawTri_ param_7)

# poly "type" byte (poly+2): number of corners; 1 = sprite (Part_DrawSprite_)


class Texture(object):
    """8-bit indexed texture + resolved colours.
    index: (h, w) uint8; rgba: (h, w, 4) uint8 (index 0 -> alpha 0)."""

    def __init__(self, w, h, index, rgb_pal, offset):
        self.w, self.h, self.offset = w, h, offset
        self.index = index
        if w and h:
            rgba = np.empty((h, w, 4), np.uint8)
            rgba[..., :3] = rgb_pal[index]
            rgba[..., 3] = np.where(index == 0, 0, 255)
        else:
            rgba = np.zeros((0, 0, 4), np.uint8)
        self.rgba = rgba

    @property
    def rgb(self):
        return self.rgba[..., :3]


class Part(object):
    pass


def _u8(d, o):
    return d[o]


def _u16(d, o):
    return struct.unpack_from('<H', d, o)[0]


def _u32(d, o):
    return struct.unpack_from('<I', d, o)[0]


def palette_rgb(d, paltype):
    """Return (rgb256 uint8 (256,3), shades (32,256,3) uint8 or None) for the model palette.
    paltype 0/3: u16 RGB565 [shade 0..31][index]; shade s = rgb*(s+1)/32 (RefPal16_Calculate_),
                 so shade 31 is the full colour.
    paltype 1:   256 x RGB888 (PC models; index 0 is the key colour, (0,255,0))."""
    if paltype in (0, 3):
        p = np.frombuffer(d, '<u2', 0x2000, 13).reshape(32, 256).astype(np.uint32)
        r = (p >> 11) & 31
        g = (p >> 5) & 63
        b = p & 31
        shades = np.stack([(r * 255 + 15) // 31, (g * 255 + 31) // 63, (b * 255 + 15) // 31],
                          -1).astype(np.uint8)
        return shades[31].copy(), shades
    if paltype == 1:
        rgb = np.frombuffer(d, np.uint8, 0x300, 13).reshape(256, 3).copy()
        return rgb, None
    # paltype 2: 64-byte name of a shared palette in models/refpal/ (dir is empty in retail)
    g = np.arange(256, dtype=np.uint8)
    return np.stack([g, g, g], -1), None


class Model(object):
    """Parsed .mdl.  Attributes:
      path, version (9|10), has_framebits, paltype, palette (256,3) uint8, shades or None
      nframes, framebits (bytes or None), anims [(start, end, third)]*256 (model+0x82)
      attachments [dict(nframes, trans (n,3) f32, rot (n,3,3) f32)]
      parts [Part], textures [Texture] (all parts, global index; part.tex_base maps local->global)
    Part attributes: offset, hdr (5 u32), flags, nverts, ngroups, nframes, path_len (list),
      frame_off (list of file offsets or None per frame), frame_vec (nframes,2,3) f32
        ([f][1] = root-motion translation accumulated over the animation, e.g. walk cycle +Z;
         [f][0] = unknown, ~always 0),
      mips [(uv (n,3) int16 [vidx,u,v], polys (n,3) [first_uv, ncorners, tex], pflags (n,) u8)],
      textures [Texture], tex_base, sprites [u32]
    """

    def __init__(self, data, path=''):
        d = bytes(data)
        self.data = d
        self.path = path
        if len(d) < 13 or _u32(d, 0) != MAGIC:
            raise ValueError('bad MDL magic')
        ver = _u32(d, 4)
        self.has_framebits = bool(ver & FLAG_FRAMEBITS)
        self.version = ver & ~FLAG_FRAMEBITS
        if self.version not in (9, 10):
            raise ValueError('unsupported MDL version %d' % self.version)
        self.parttab = _u32(d, 8)
        self.paltype = d[12]
        if self.paltype not in PAL_SIZES:
            raise ValueError('bad paltype %d' % self.paltype)
        self.palette, self.shades = palette_rgb(d, self.paltype)
        # Model_ResetTextures_ zeroes colour 0 of every shade row: index 0 draws black on
        # unmasked polys and is transparent on masked polys / sprites (rgba alpha 0).
        self.key_color = tuple(int(x) for x in self.palette[0])
        self.palette[0] = 0
        o = 13 + PAL_SIZES[self.paltype]
        self.nframes = _u16(d, o)
        o += 2
        self.framebits = None
        if self.has_framebits:
            self.framebits = d[o:o + self.nframes]
            o += self.nframes
        self.anims = [struct.unpack_from('<3H', d, o + 6 * i) for i in range(256)]
        o += 1536
        nattach = d[o]
        o += 1
        self.attachments = []
        for i in range(nattach):
            n = _u16(d, o)
            o += 2
            tr = np.frombuffer(d, '<f4', n * 3, o).reshape(n, 3) if n else np.zeros((0, 3), np.float32)
            o += n * 12
            rot = np.frombuffer(d, '<f4', n * 9, o).reshape(n, 3, 3) if n else np.zeros((0, 3, 3), np.float32)
            o += n * 36
            self.attachments.append(dict(nframes=n, trans=tr, rot=rot))
        nparts = d[o]
        o += 1
        self.parts = []
        self.textures = []
        for p in range(nparts):
            po = _u32(d, self.parttab + 4 * p)
            self.parts.append(self._read_part(d, po, p))

    def _read_part(self, d, po, pidx):
        pt = Part()
        pt.index = pidx
        pt.offset = po
        pt.hdr = struct.unpack_from('<5I', d, po)
        pt.sprites_off = pt.hdr[2]
        pt.flags = pt.hdr[3]
        pt.nverts = pt.hdr[4]
        o = po + 20
        pt.mips = []
        for mip in range(1 if self.version == 9 else 4):
            nuv = _u32(d, o)
            uv = np.frombuffer(d, '<i2', nuv * 3, o + 4).reshape(nuv, 3)
            o += 4 + nuv * 6
            npo = _u32(d, o)
            raw = np.frombuffer(d, np.uint8, npo * 4, o + 4).reshape(npo, 4)
            polys = np.empty((npo, 3), np.int32)
            polys[:, 0] = raw[:, 0].astype(np.int32) | (raw[:, 1].astype(np.int32) << 8)
            polys[:, 1] = raw[:, 2]
            polys[:, 2] = raw[:, 3]
            pflags = np.frombuffer(d, np.uint8, npo, o + 4 + npo * 4)
            o += 4 + npo * 5
            pt.mips.append((uv, polys, pflags))
        pt.ngroups = d[o]
        o += 1
        pt.nframes = _u16(d, o)
        o += 2
        npath = _u32(d, o)
        o += 4
        pt.path_len = list(struct.unpack_from('<%dH' % npath, d, o))
        o += 2 * npath
        pt.framesize = _u32(d, o)
        o += 4
        pt.frame_off = []
        vec = np.zeros((pt.nframes, 2, 3), np.float32)
        for f in range(pt.nframes):
            stored = self.framebits is None or (f < len(self.framebits) and self.framebits[f])
            if stored:
                pt.frame_off.append(o)
                o += pt.framesize
            else:
                pt.frame_off.append(None)
            vec[f] = np.frombuffer(d, '<f4', 6, o).reshape(2, 3)
            o += 24
        pt.frame_vec = vec
        nt = d[o]
        toffs = struct.unpack_from('<%dI' % nt, d, o + 1) if nt else ()
        pt.tex_base = len(self.textures)
        pt.textures = []
        for t in toffs:
            w, h = struct.unpack_from('<II', d, t)
            if w and h:
                idx = np.frombuffer(d, np.uint8, w * h, t + 8).reshape(h, w)
            else:
                idx = np.zeros((0, 0), np.uint8)
            tex = Texture(w, h, idx, self.palette, t)
            pt.textures.append(tex)
            self.textures.append(tex)
        ns = d[pt.sprites_off] if pt.sprites_off < len(d) else 0
        pt.sprites = list(struct.unpack_from('<%dI' % ns, d, pt.sprites_off + 1)) if ns else []
        if sum(pt.path_len) != pt.nverts:
            raise ValueError('part %d: path lengths %d != nverts %d' % (pidx, sum(pt.path_len), pt.nverts))
        return pt

    # ---------------- geometry ----------------

    @property
    def default_frame(self):
        """First frame of animation 0 (the idle/stand slot LoadMonsterGFX_ starts with),
        or the first stored frame."""
        f = self.anims[0][0] if self.anims else 0
        if self._frame_ok(f):
            return f
        for f in range(self.nframes):
            if self._frame_ok(f):
                return f
        return 0

    def _frame_ok(self, f):
        return all(f < p.nframes and p.frame_off[f] is not None for p in self.parts)

    def stored_frames(self):
        return [f for f in range(self.nframes) if self._frame_ok(f)]

    def part_vertices(self, part, frame):
        """(nverts, 3) float32 model-space vertices of part for frame.
        Each path = s16 x,y,z start + (len-1) x s8 dx,dy,dz cumulative deltas."""
        d = self.data
        o = part.frame_off[frame]
        if o is None:
            raise ValueError('frame %d not stored' % frame)
        out = np.empty((part.nverts, 3), np.float32)
        k = 0
        for n in part.path_len:
            start = np.frombuffer(d, '<i2', 3, o).astype(np.float32)
            o += 6
            out[k] = start
            if n > 1:
                deltas = np.frombuffer(d, np.int8, (n - 1) * 3, o).reshape(n - 1, 3).astype(np.float32)
                out[k + 1:k + n] = start + np.cumsum(deltas, 0)
                o += (n - 1) * 3
            k += n
        return out

    def default_groups(self):
        """Mesh groups an editor should show (the engine shows only group 0 unless game code
        calls GraphObj_HidePolys_).  9-group humanoids: 0 body, 1-4 hair variants,
        5-8 eye states (TwiddleHair_/TwiddleEyes_) -> (0, 1, 5).  If group 0 is empty
        (e.g. item/book-plain.mdl) all used groups are returned."""
        ng = self.parts[0].ngroups if self.parts else 1
        if ng >= 9:
            return (0, 1, 5)
        used, tri_groups = set(), set()
        for p in self.parts:
            if p.mips and len(p.mips[0][2]):
                uv, polys, pf = p.mips[0]
                g = (pf & PF_GROUP).tolist()
                used |= set(g)
                tri_groups |= set(gg for gg, nc in zip(g, polys[:, 1].tolist()) if nc >= 3)
        if 0 in tri_groups or not used:
            return (0,)
        # group 0 has no solid polys (book-plain: all in 1; campfire: 0 = flame sprite,
        # 1 = unlit logs, 2 = burning logs): add the lowest solid group
        extra = min(tri_groups) if tri_groups else min(used)
        return tuple(sorted(set([0, extra])))

    def attachment(self, slot, frame=None):
        """(rot (3,3), trans (3,)) of attachment slot for frame: a child model attached to
        this slot (weapon in hand, helm, rider...) is drawn at  v_child @ rot + trans  in this
        model's space (Attachment_Draw_; child.M = rot . parent.M, child.pos = parent.pos +
        trans . parent.M).  None if the slot has no data for the frame."""
        if frame is None:
            frame = self.default_frame
        a = self.attachments[slot]
        if frame >= a['nframes']:
            return None
        return a['rot'][frame].astype(np.float64), a['trans'][frame].astype(np.float64)

    def mesh(self, frame=None, mip=0, groups='default'):
        """Triangulated mesh for one frame (all parts merged).
        groups: iterable of mesh-group numbers to include, None = all, 'default' = default_groups().
        Returns dict:
          vertices (V,3) f32 model space; tris (T,3) int32 vertex indices;
          uv (T,3,2) f32 in 0..1 of the triangle's texture; tex (T,) int32 global texture index;
          flags (T,) u8 poly flags (PF_*); part (T,) int32;
          sprites: list of dict(vertex, tex, width, height, flags) for 1-corner polys:
            camera-facing quads centred on the vertex, width x height world units,
            showing the whole texture (masked)."""
        if frame is None:
            frame = self.default_frame
        if isinstance(groups, str):
            groups = self.default_groups()
        verts, tris, uvs, texs, flags, parts, sprites = [], [], [], [], [], [], []
        vbase = 0
        for pt in self.parts:
            v = self.part_vertices(pt, frame)
            verts.append(v)
            uv, polys, pflags = pt.mips[min(mip, len(pt.mips) - 1)]
            for (first, nc, tex), fl in zip(polys, pflags):
                if groups is not None and (fl & PF_GROUP) not in groups:
                    continue
                if tex >= len(pt.textures):
                    continue
                t = pt.textures[tex]
                if nc == 1:
                    e = uv[first]
                    w, h = int(e[1]), int(e[2])
                    if h <= 0 and t.w:      # bush/tree sprites store only a width (guess: keep aspect)
                        h = int(round(w * t.h / float(t.w)))
                    sprites.append(dict(vertex=int(e[0]) + vbase, tex=pt.tex_base + int(tex),
                                        width=w, height=h, flags=int(fl)))
                    continue
                if t.w == 0 or t.h == 0:
                    continue
                for k in range(nc - 2):
                    c = (uv[first], uv[first + k + 1], uv[first + k + 2])
                    tris.append([int(c[0][0]) + vbase, int(c[1][0]) + vbase, int(c[2][0]) + vbase])
                    uvs.append([[c[j][1] / float(t.w), c[j][2] / float(t.h)] for j in range(3)])
                    texs.append(pt.tex_base + int(tex))
                    flags.append(int(fl))
                    parts.append(pt.index)
            vbase += pt.nverts
        r = dict(
            vertices=np.concatenate(verts) if verts else np.zeros((0, 3), np.float32),
            tris=np.array(tris, np.int32).reshape(-1, 3),
            uv=np.array(uvs, np.float32).reshape(-1, 3, 2),
            tex=np.array(texs, np.int32),
            flags=np.array(flags, np.uint8),
            part=np.array(parts, np.int32),
            sprites=sprites,
        )
        return r

    def bounds(self, frame=None):
        if frame is None:
            frame = self.default_frame
        v = np.concatenate([self.part_vertices(p, frame) for p in self.parts])
        return v.min(0), v.max(0)

    def height(self):
        """CalcModelHeight_: max y over all parts of frame 0 (clamped >= 0)."""
        f = 0 if self._frame_ok(0) else self.default_frame
        return max(0.0, float(self.bounds(f)[1][1]))


def load_model(gamedir, path):
    """path relative to <gamedir>/models (e.g. 'monster/skeleton.mdl'), or absolute."""
    if os.path.isabs(path) and os.path.exists(path):
        full = path
    else:
        p = path.replace('\\', '/')
        if not p.lower().startswith('models/'):
            p = 'models/' + p
        full = d6data.find_file(gamedir, p)
        if full is None:
            raise IOError('model not found: %s' % path)
    with open(full, 'rb') as f:
        return Model(f.read(), full)


# ---------------------------------------------------------------------------
# record -> model mapping (tables compiled into deep6.exe, mdldata.c)
# ---------------------------------------------------------------------------

EXE_TABLES = {
    # kind: (VA, stride, count, subdir)
    'M': (0x5DFFA8, 0x55, 121, 'monster'),   # _MonMDLData; name[0x50], u8, f32 Model_Read_ param
    'I': (0x5DB3B8, 0x51, 240, 'item'),      # _ItemMDLData; name[0x50], u8 MDLExtend chain
    'P': (0x5E27D5, 0x51, 250, 'prop'),      # _PropMDLData; name[0x50], u8 MDLExtend chain
}
PC_GFX_LIMIT = 0x11   # monster gfx < 17 -> pc/<name> (player race/sex models)

_table_cache = {}


def _exe_va_to_off(exe, va):
    pe = _u32(exe, 0x3C)
    ns = struct.unpack_from('<H', exe, pe + 6)[0]
    opt = struct.unpack_from('<H', exe, pe + 20)[0]
    base = _u32(exe, pe + 24 + 28)
    for i in range(ns):
        so = pe + 24 + opt + 40 * i
        vs, va0, rs, ra = struct.unpack_from('<IIII', exe, so + 8)
        if va0 + base <= va < va0 + base + max(vs, rs) and va - va0 - base < rs:
            return va - va0 - base + ra
    raise ValueError('VA %x not in file' % va)


def model_table(gamedir, kind):
    """List of model file names (as stored, may be upper case) for kind 'M','I','P'."""
    key = (os.path.abspath(gamedir), kind)
    if key in _table_cache:
        return _table_cache[key]
    exe_path = d6data.find_file(gamedir, 'deep6.exe')
    if exe_path is None:
        raise IOError('deep6.exe not found in %s' % gamedir)
    with open(exe_path, 'rb') as f:
        exe = f.read()
    va, stride, count, sub = EXE_TABLES[kind]
    o = _exe_va_to_off(exe, va)
    names = []
    for i in range(count):
        r = exe[o + i * stride:o + i * stride + 0x50]
        names.append(d6data.cstr(r))
    _table_cache[key] = names
    return names


def _db(gamedir, kind):
    name, cls = {'M': ('D6MONS.DAT', d6data.MonsTable), 'I': ('D6ITEM.DAT', d6data.ItemTable),
                 'P': ('D6PROP.DAT', d6data.PropTable)}[kind]
    key = (os.path.abspath(gamedir), 'db' + kind)
    if key not in _table_cache:
        raw = d6data.read_file(gamedir, name)
        if raw is None:
            raise IOError('%s not found' % name)
        _table_cache[key] = cls.parse(raw)
    return _table_cache[key]


def record_model_index(gamedir, kind, recno):
    """Model-table index for a database record (1-based recno, as used by the game)."""
    kind = kind.upper()
    t = _db(gamedir, kind)
    if not 1 <= recno <= len(t.records):
        raise IndexError('%s record %d out of range 1..%d' % (kind, recno, len(t.records)))
    r = t.records[recno - 1]
    return r.get({'M': 'gfx', 'I': 'model', 'P': 'model'}[kind])


def model_for(gamedir, kind, recno):
    """Path (relative to models/, case as on disk) of the .mdl used by record recno of
    D6MONS ('M'), D6ITEM ('I') or D6PROP ('P'); None if the record has no model."""
    kind = kind.upper()
    idx = record_model_index(gamedir, kind, recno)
    names = model_table(gamedir, kind)
    if idx is None or idx < 0 or idx >= len(names) or not names[idx]:
        return None
    sub = EXE_TABLES[kind][3]
    if kind == 'M' and idx < PC_GFX_LIMIT:
        sub = 'pc'
    rel = '%s/%s' % (sub, names[idx])
    full = d6data.find_file(gamedir, 'models/' + rel)
    if full is None:
        return rel
    return os.path.relpath(full, os.path.join(gamedir, 'models')).replace(os.sep, '/')


def record_name(gamedir, kind, recno):
    try:
        return _db(gamedir, kind.upper()).records[recno - 1].get('name')
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# software renderer (numpy z-buffer, affine texture mapping)
# ---------------------------------------------------------------------------

def _look_matrix(yaw_deg, pitch_deg):
    """Camera rotation: rows are camera right, up, forward (world->camera)."""
    yw, pt = np.radians(yaw_deg), np.radians(pitch_deg)
    # camera position direction (from target): orbit around y
    fwd = -np.array([np.sin(yw) * np.cos(pt), np.sin(pt), np.cos(yw) * np.cos(pt)])
    up0 = np.array([0.0, 1.0, 0.0])
    right = np.cross(up0, fwd)
    right /= np.linalg.norm(right)
    up = np.cross(fwd, right)
    return np.stack([right, up, fwd])


def render(model, size=512, frame=None, yaw=35.0, pitch=25.0, bg=(48, 52, 60), mip=0,
           groups='default', shade=True, show_axes=True, sprites=True):
    """Render model to an (size,size,3) uint8 image (orthographic view, z-buffered,
    two-sided, nearest-texel).  The view uses the engine's left-handed convention
    (x right, y up, z into the screen at yaw 0 from the front).
    yaw: degrees, camera orbit around +y; yaw 0 = camera on +Z looking at the model's front."""
    mesh = model.mesh(frame, mip, groups)
    V = mesh['vertices'].astype(np.float64)
    T = mesh['tris']
    spr = mesh['sprites'] if sprites else []
    img = np.empty((size, size, 3), np.float32)
    img[:] = np.array(bg, np.float32) / 255.0
    zb = np.full((size, size), np.inf, np.float64)
    R = _look_matrix(yaw, pitch)
    used = np.unique(np.concatenate([T.ravel(), np.array([e['vertex'] for e in spr], np.int64)])).astype(np.int64)
    if len(used) == 0:
        return (img * 255).astype(np.uint8)
    C = V @ R.T                        # camera space: x right, y up, z depth
    pts = [C[used, :2]]
    for e in spr:
        c = C[e['vertex'], :2]
        h = np.array([e['width'] / 2.0, e['height'] / 2.0])
        pts.append(np.stack([c - h, c + h]))
    pts = np.concatenate(pts)
    lo, hi = pts.min(0), pts.max(0)
    centre = (lo + hi) / 2
    ext = max(hi - lo) * 1.08 + 1e-6
    sc = size / ext

    def scr(c):
        return ((c[..., 0] - centre[0]) * sc + size / 2, -(c[..., 1] - centre[1]) * sc + size / 2)
    sx, sy = scr(C)
    sz = C[:, 2]
    light = np.array([0.4, 0.8, -0.45])
    light /= np.linalg.norm(light)
    texs = model.textures
    # screen triangles: (x[3], y[3], z[3], uv[3,2], tex, flags, k)
    tris = []
    for ti in range(len(T)):
        a, b, c = T[ti]
        k = 1.0
        if shade:
            n = np.cross(V[b] - V[a], V[c] - V[a])
            nn = np.linalg.norm(n)
            k = 0.75 + 0.25 * abs(float(n @ light) / nn) if nn > 0 else 1.0
        tris.append((np.array([sx[a], sx[b], sx[c]]), np.array([sy[a], sy[b], sy[c]]),
                     np.array([sz[a], sz[b], sz[c]]), mesh['uv'][ti], mesh['tex'][ti],
                     int(mesh['flags'][ti]), k))
    for e in spr:
        t = texs[e['tex']]
        if not t.w:
            continue
        c = C[e['vertex']]
        hw, hh = e['width'] / 2.0, e['height'] / 2.0
        q = np.array([[c[0] - hw, c[1] + hh], [c[0] + hw, c[1] + hh], [c[0] + hw, c[1] - hh],
                      [c[0] - hw, c[1] - hh]])
        qx, qy = scr(q)
        quv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
        z = np.full(3, c[2])
        fl = e['flags'] | PF_MASKED
        for i0, i1, i2 in ((0, 1, 2), (0, 2, 3)):
            tris.append((qx[[i0, i1, i2]], qy[[i0, i1, i2]], z, quv[[i0, i1, i2]], e['tex'], fl, 1.0))
    # opaque first, then translucent back-to-front
    order = sorted(range(len(tris)), key=lambda i: (bool(tris[i][5] & PF_TRANS),
                                                     -tris[i][2].mean() if tris[i][5] & PF_TRANS else 0))
    for ti in order:
        x, y, z, uv, texi, fl, k = tris[ti]
        area = (x[1] - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (y[1] - y[0])
        if abs(area) < 1e-9:
            continue
        x0, x1 = max(int(np.floor(x.min())), 0), min(int(np.ceil(x.max())), size - 1)
        y0, y1 = max(int(np.floor(y.min())), 0), min(int(np.ceil(y.max())), size - 1)
        if x0 > x1 or y0 > y1:
            continue
        px, py = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        w0 = ((x[1] - px) * (y[2] - py) - (x[2] - px) * (y[1] - py)) / area
        w1 = ((x[2] - px) * (y[0] - py) - (x[0] - px) * (y[2] - py)) / area
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue
        depth = w0 * z[0] + w1 * z[1] + w2 * z[2]
        sub = zb[y0:y1 + 1, x0:x1 + 1]
        m = inside & (depth < sub)
        if not m.any():
            continue
        tex = texs[texi]
        u = (w0 * uv[0, 0] + w1 * uv[1, 0] + w2 * uv[2, 0]) * tex.w
        v = (w0 * uv[0, 1] + w1 * uv[1, 1] + w2 * uv[2, 1]) * tex.h
        iu = np.clip(np.floor(u).astype(np.int64), 0, tex.w - 1)
        iv = np.clip(np.floor(v).astype(np.int64), 0, tex.h - 1)
        if fl & PF_MASKED:
            m = m & (tex.index[iv, iu] != 0)
            if not m.any():
                continue
        col = tex.rgba[iv, iu, :3].astype(np.float32) * (k / 255.0)
        dst = img[y0:y1 + 1, x0:x1 + 1]
        if fl & PF_TRANS:
            col = 0.5 * col + 0.5 * dst
        else:
            sub[m] = depth[m]
        dst[m] = col[m]
    out = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    if show_axes:
        # small axis gizmo: model +X red, +Y green, +Z blue
        o = np.array([28.0, size - 28.0])
        for vec, colr in (((1, 0, 0), (255, 60, 60)), ((0, 1, 0), (60, 255, 60)), ((0, 0, 1), (80, 120, 255))):
            cv = np.array(vec, float) @ R.T
            d2 = np.array([cv[0], -cv[1]]) * 22
            for t in np.linspace(0, 1, 40):
                px = int(o[0] + d2[0] * t)
                py = int(o[1] + d2[1] * t)
                if 0 <= px < size and 0 <= py < size:
                    out[py, px] = colr
    return out


def render_png(model, out, **kw):
    from PIL import Image
    Image.fromarray(render(model, **kw)).save(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve(gamedir, args):
    """args: ['M', '1'] or ['monster/skeleton.mdl'] -> (relpath, rest, label)."""
    if len(args) >= 2 and args[0].upper() in ('M', 'I', 'P') and args[1].isdigit():
        kind, rec = args[0].upper(), int(args[1])
        rel = model_for(gamedir, kind, rec)
        return rel, args[2:], '%s%03d %s' % (kind, rec, record_name(gamedir, kind, rec))
    return args[0], args[1:], args[0]


def iter_models(gamedir):
    root = d6data.find_file(gamedir, 'models')
    for dp, dn, fn in os.walk(root):
        dn.sort()
        for f in sorted(fn):
            if f.lower().endswith('.mdl'):
                yield os.path.relpath(os.path.join(dp, f), root).replace(os.sep, '/')


def selftest(gamedir):
    ok = bad = 0
    for rel in iter_models(gamedir):
        try:
            m = load_model(gamedir, rel)
            for f in m.stored_frames()[:1] + [m.default_frame]:
                me = m.mesh(f)
                if len(me['tris']):
                    assert me['tris'].max() < len(me['vertices'])
                    assert np.all(np.isfinite(me['uv']))
            for p in m.parts:
                for (uv, polys, pflags) in p.mips:
                    if len(uv):
                        assert uv[:, 0].max() < p.nverts, 'uv vertex index out of range'
                    if len(polys):
                        assert (polys[:, 0] + polys[:, 1]).max() <= len(uv), 'poly uv range'
            ok += 1
        except Exception as e:
            bad += 1
            print('FAIL %s: %r' % (rel, e))
    # record mapping
    missing = []
    for kind in 'MIP':
        n = len(_db(gamedir, kind).records)
        for r in range(1, n + 1):
            try:
                rel = model_for(gamedir, kind, r)
            except Exception as e:
                missing.append('%s%d:%r' % (kind, r, e))
                continue
            if rel and d6data.find_file(gamedir, 'models/' + rel) is None:
                missing.append('%s%d:%s' % (kind, r, rel))
    print('models ok %d, failed %d' % (ok, bad))
    print('records with missing model file: %d %s' % (len(missing), ' '.join(missing[:40])))
    return bad == 0


def info(gamedir, rel, label):
    m = load_model(gamedir, rel)
    print('%s -> %s' % (label, rel))
    print('version %d paltype %d framebits %s nframes %d parts %d attachments %d textures %d'
          % (m.version, m.paltype, m.has_framebits, m.nframes, len(m.parts), len(m.attachments),
             len(m.textures)))
    print('anims:', ' '.join('%d:%d-%d/%d' % ((i,) + a) for i, a in enumerate(m.anims) if a != (0, 0, 0)))
    lo, hi = m.bounds()
    print('default frame %d bounds %s .. %s height %.0f' % (m.default_frame, lo, hi, m.height()))
    for p in m.parts:
        uv, polys, pf = p.mips[0]
        print(' part %d verts %d paths %d frames %d groups %d polys %d flags %#x tex %s'
              % (p.index, p.nverts, len(p.path_len), p.nframes, p.ngroups, len(polys), p.flags,
                 ['%dx%d' % (t.w, t.h) for t in p.textures]))


def main(argv):
    if len(argv) >= 2 and argv[0] == '--selftest':
        return 0 if selftest(argv[1]) else 1
    if len(argv) >= 3 and argv[0] in ('--render', '--info'):
        gamedir = argv[1]
        rel, rest, label = _resolve(gamedir, argv[2:])
        if rel is None:
            print('%s: no model' % label)
            return 1
        if argv[0] == '--info':
            info(gamedir, rel, label)
            return 0
        out = rest[0] if rest else 'out.png'
        kw = {}
        i = 1
        while i < len(rest):
            k = rest[i]
            if k == '--yaw':
                kw['yaw'] = float(rest[i + 1])
            elif k == '--pitch':
                kw['pitch'] = float(rest[i + 1])
            elif k == '--frame':
                kw['frame'] = int(rest[i + 1])
            elif k == '--size':
                kw['size'] = int(rest[i + 1])
            elif k == '--mip':
                kw['mip'] = int(rest[i + 1])
            i += 2
        m = load_model(gamedir, rel)
        render_png(m, out, **kw)
        print('%s -> %s -> %s' % (label, rel, out))
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
