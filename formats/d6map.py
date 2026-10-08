#!/usr/bin/env python3
"""
d6map.py - .map files (TrenchBroom) for Deep6 levels.

Map space is Quake's: z up, right handed. The engine is y up, left handed;
engine (x, y, z) = map (x, z, y) (a mirror, so TrenchBroom shows the level
the right way round).

Two face formats are read and written:
  Valve 220:          ( p1 ) ( p2 ) ( p3 ) TEX [ ux uy uz ushift ] [ vx vy vz vshift ] rot sx sy
  Quake2 Valve (the Deep6 TrenchBroom config uses it, for the flags):
                      ... rot sx sy CONTENTS SURFACEFLAGS VALUE
Standard Quake faces (TEX xoff yoff rot sx sy) are read too.

Deep6 face/brush flags (see tools/trenchbroom/GameConfig.cfg):
  contents  1 water   2 lava   4 clip (collision only)
  surface   1 nodraw (face kept, not drawn: texinfo -1)
            2 terrain (drawn as terrain when horizontal, texinfo 0x20000)
            4 water pass (0x8000)   8 translucent pass (0x2000)
"""
import math
import re

CONT_WATER, CONT_LAVA, CONT_CLIP = 1, 2, 4
SURF_NODRAW, SURF_TERRAIN, SURF_WATER, SURF_TRANS = 1, 2, 4, 8


def to_engine(v):
    return (v[0], v[2], v[1])


to_map = to_engine          # the swap is its own inverse


class Face(object):
    __slots__ = ('pts', 'tex', 'u', 'v', 'rot', 'sx', 'sy', 'contents', 'flags', 'value', 'std')

    def __init__(self, pts, tex, u=None, v=None, rot=0.0, sx=1.0, sy=1.0,
                 contents=0, flags=0, value=0, std=None):
        self.pts = pts            # 3 points (map space)
        self.tex = tex
        self.u = u                # (x, y, z, shift) Valve axis, or None (standard)
        self.v = v
        self.rot, self.sx, self.sy = rot, sx, sy
        self.contents, self.flags, self.value = contents, flags, value
        self.std = std            # (xoff, yoff) for standard faces

    def plane(self):
        """(normal, dist) in map space, Quake convention: normal from
        (p1 - p2) x (p3 - p2) points out of the brush."""
        p1, p2, p3 = self.pts
        a = [p1[i] - p2[i] for i in range(3)]
        b = [p3[i] - p2[i] for i in range(3)]
        n = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
        l = math.sqrt(sum(c * c for c in n)) or 1.0
        n = [c / l for c in n]
        return n, sum(n[i] * p2[i] for i in range(3))

    def tex_vectors(self):
        """Quake texel projection in map space: (s xyz, s shift), (t xyz, t shift)
        with u_texel = P . s + s_shift."""
        if self.u is not None:
            sx = self.sx or 1.0
            sy = self.sy or 1.0
            s = [self.u[i] / sx for i in range(3)]
            t = [self.v[i] / sy for i in range(3)]
            return (s, self.u[3]), (t, self.v[3])
        # standard Quake projection (TextureAxisFromPlane)
        n, _ = self.plane()
        axes = [((0, 0, 1), (1, 0, 0), (0, -1, 0)), ((0, 0, -1), (1, 0, 0), (0, -1, 0)),
                ((1, 0, 0), (0, 1, 0), (0, 0, -1)), ((-1, 0, 0), (0, 1, 0), (0, 0, -1)),
                ((0, 1, 0), (1, 0, 0), (0, 0, -1)), ((0, -1, 0), (1, 0, 0), (0, 0, -1))]
        best, bi = -1, 0
        for i, (an, _, _) in enumerate(axes):
            d = sum(n[k] * an[k] for k in range(3))
            if d > best + 1e-6:
                best, bi = d, i
        sv, tv = list(axes[bi][1]), list(axes[bi][2])
        ang = math.radians(self.rot)
        sinv, cosv = math.sin(ang), math.cos(ang)
        sxi = 0 if sv[0] else (1 if sv[1] else 2)
        txi = 0 if tv[0] else (1 if tv[1] else 2)
        ns = sv[sxi] * cosv - sv[txi] * sinv, sv[sxi] * sinv + sv[txi] * cosv
        nt = tv[sxi] * cosv - tv[txi] * sinv, tv[sxi] * sinv + tv[txi] * cosv
        sv[sxi], sv[txi] = ns
        tv[sxi], tv[txi] = nt
        sx = self.sx or 1.0
        sy = self.sy or 1.0
        xo, yo = self.std or (0, 0)
        return ([c / sx for c in sv], xo), ([c / sy for c in tv], yo)


class Brush(object):
    def __init__(self, faces=None):
        self.faces = faces or []

    @property
    def contents(self):
        c = 0
        for f in self.faces:
            c |= f.contents
        return c


class Entity(object):
    def __init__(self, keys=None, brushes=None):
        self.keys = keys if keys is not None else []    # [(k, v)] in order
        self.brushes = brushes or []

    def get(self, k, default=None):
        for kk, v in self.keys:
            if kk == k:
                return v
        return default

    def set(self, k, v):
        for i, (kk, _) in enumerate(self.keys):
            if kk == k:
                self.keys[i] = (k, v)
                return
        self.keys.append((k, v))

    def delete(self, k):
        self.keys = [(kk, v) for kk, v in self.keys if kk != k]

    @property
    def classname(self):
        return self.get('classname', '')


_TOK = re.compile(r'//[^\n]*|"(?:[^"\\]|\\.)*"|[(){}\[\]]|[^\s(){}\[\]"]+')


def _tokens(text):
    for m in _TOK.finditer(text):
        t = m.group(0)
        if t.startswith('//'):
            continue
        yield t


def parse(text):
    """Map text -> [Entity]."""
    toks = list(_tokens(text))
    i = 0
    ents = []

    def num(t):
        return float(t)

    while i < len(toks):
        if toks[i] != '{':
            raise ValueError('map: expected { at token %d (%r)' % (i, toks[i]))
        i += 1
        e = Entity()
        while toks[i] != '}':
            t = toks[i]
            if t.startswith('"'):
                e.keys.append((t[1:-1], toks[i + 1][1:-1]))
                i += 2
            elif t == '{':
                i += 1
                b = Brush()
                while toks[i] != '}':
                    if toks[i] in ('brushDef', 'patchDef2', 'brushDef3'):
                        raise ValueError('map: Quake 3 brush primitives / patches are not supported')
                    pts = []
                    for _ in range(3):
                        assert toks[i] == '(', toks[i:i + 5]
                        pts.append((num(toks[i + 1]), num(toks[i + 2]), num(toks[i + 3])))
                        assert toks[i + 4] == ')'
                        i += 5
                    tex = toks[i]
                    if tex.startswith('"'):
                        tex = tex[1:-1]
                    i += 1
                    if toks[i] == '[':
                        u = tuple(num(x) for x in toks[i + 1:i + 5])
                        assert toks[i + 5] == ']' and toks[i + 6] == '['
                        v = tuple(num(x) for x in toks[i + 7:i + 11])
                        assert toks[i + 11] == ']'
                        i += 12
                        rot, sx, sy = num(toks[i]), num(toks[i + 1]), num(toks[i + 2])
                        i += 3
                        f = Face(pts, tex, u, v, rot, sx, sy)
                    else:
                        xo, yo, rot, sx, sy = (num(x) for x in toks[i:i + 5])
                        i += 5
                        f = Face(pts, tex, None, None, rot, sx, sy, std=(xo, yo))
                    # optional Quake 2 contents / flags / value
                    extra = []
                    while toks[i] not in ('(', '}') and len(extra) < 3:
                        extra.append(int(float(toks[i])))
                        i += 1
                    if extra:
                        f.contents = extra[0]
                        f.flags = extra[1] if len(extra) > 1 else 0
                        f.value = extra[2] if len(extra) > 2 else 0
                    b.faces.append(f)
                i += 1
                e.brushes.append(b)
            else:
                raise ValueError('map: unexpected token %r' % t)
        i += 1
        ents.append(e)
    return ents


def load(path):
    with open(path, encoding='latin-1') as f:
        return parse(f.read())


def _n(x):
    if abs(x - round(x)) < 1e-7:
        return '%d' % round(x)
    s = '%.6f' % x
    return s.rstrip('0').rstrip('.')


# TrenchBroom matches the "// Game:" line against the "name" of
# tools/trenchbroom/Deep6/GameConfig.cfg (and keys its game path by it).
TB_GAME = 'Deep6 (Wizards & Warriors)'


def write(ents, flags=True, header='// Game: %s\n// Format: Quake2 (Valve)\n' % TB_GAME):
    """[Entity] -> text. flags=True writes Quake2 (Valve) faces (contents,
    surface flags, value); False writes plain Valve 220."""
    out = [header] if header else []
    for ei, e in enumerate(ents):
        out.append('// entity %d\n{\n' % ei)
        for k, v in e.keys:
            out.append('"%s" "%s"\n' % (k, v))
        for bi, b in enumerate(e.brushes):
            out.append('// brush %d\n{\n' % bi)
            for f in b.faces:
                p = ' '.join('( %s %s %s )' % tuple(_n(c) for c in pt) for pt in f.pts)
                if f.u is None:
                    (s, ss), (t, ts) = f.tex_vectors()
                    u, v, sx, sy = s + [ss], t + [ts], 1.0, 1.0
                    lu = math.sqrt(sum(c * c for c in s)) or 1
                    lv = math.sqrt(sum(c * c for c in t)) or 1
                    u = [c / lu for c in s] + [ss]
                    v = [c / lv for c in t] + [ts]
                    sx, sy = 1 / lu, 1 / lv
                    rot = 0
                else:
                    u, v, rot, sx, sy = f.u, f.v, f.rot, f.sx, f.sy
                line = '%s %s [ %s ] [ %s ] %s %s %s' % (
                    p, f.tex, ' '.join(_n(c) for c in u), ' '.join(_n(c) for c in v),
                    _n(rot), _n(sx), _n(sy))
                if flags:
                    line += ' %d %d %d' % (f.contents, f.flags, f.value)
                out.append(line + '\n')
            out.append('}\n')
        out.append('}\n')
    return ''.join(out)
