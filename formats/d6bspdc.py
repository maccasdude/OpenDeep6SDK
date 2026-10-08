#!/usr/bin/env python3
"""
d6bspdc.py - decompile a Deep6 level (.bsp + .twd + .lgt/.rgb) into a
TrenchBroom .map, and export its textures as PNG files.

Every solid (and water/lava) leaf of the drawing tree is a convex region; it
becomes one brush. Solid regions that touch no open space (the void around
the level) are left out. Each brush face takes the texture and the exact
alignment of the BSP face lying on it (Valve 220 axes), so compiling the
map again (d6bspc.py) gives the same look. Doors (brush models) become
func_door entities with their d6* keys; the lights of .lgt/.rgb become
light entities.

The brushes are the BSP's split regions, not the designer's original
brushes, so a decompiled level has more (and thinner) brushes than a hand
made one. They can be edited, merged (TrenchBroom: CSG merge) or replaced.

Coordinates: map = engine with y and z swapped (map z is up).

CLI
  d6bspdc.py GAMEDIR LEVEL OUT.map [--textures DIR]     (DIR default: next to OUT.map)
  d6bspdc.py --export-textures GAMEDIR DIR                all level textures
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import d6level  # noqa: E402
import d6map    # noqa: E402

BIG = 1 << 16
EPS = 0.01


# --------------------------------------------------------------------------
# windings
# --------------------------------------------------------------------------
def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    l = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / l, a[1] / l, a[2] / l)


def base_winding(n, d):
    ax = max(range(3), key=lambda i: abs(n[i]))
    up = (0.0, 0.0, 1.0) if ax != 2 else (1.0, 0.0, 0.0)
    v = _dot(up, n)
    up = _norm((up[0] - v * n[0], up[1] - v * n[1], up[2] - v * n[2]))
    org = (n[0] * d, n[1] * d, n[2] * d)
    right = _cross(up, n)
    up = (up[0] * BIG, up[1] * BIG, up[2] * BIG)
    right = (right[0] * BIG, right[1] * BIG, right[2] * BIG)
    return [(org[0] - right[0] + up[0], org[1] - right[1] + up[1], org[2] - right[2] + up[2]),
            (org[0] + right[0] + up[0], org[1] + right[1] + up[1], org[2] + right[2] + up[2]),
            (org[0] + right[0] - up[0], org[1] + right[1] - up[1], org[2] + right[2] - up[2]),
            (org[0] - right[0] - up[0], org[1] - right[1] - up[1], org[2] - right[2] - up[2])]


def clip_winding(w, n, d):
    """Keep the part with dot(p, n) <= d."""
    dists = [_dot(p, n) - d for p in w]
    if all(x <= EPS for x in dists):
        return w
    if all(x >= -EPS for x in dists):
        return None
    out = []
    for i, p in enumerate(w):
        q = w[(i + 1) % len(w)]
        dp, dq = dists[i], dists[(i + 1) % len(w)]
        if dp <= EPS:
            out.append(p)
        if (dp < -EPS and dq > EPS) or (dp > EPS and dq < -EPS):
            t = dp / (dp - dq)
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t, p[2] + (q[2] - p[2]) * t))
    return out if len(out) >= 3 else None


def winding_area(w):
    a = (0.0, 0.0, 0.0)
    for i in range(1, len(w) - 1):
        c = _cross((w[i][0] - w[0][0], w[i][1] - w[0][1], w[i][2] - w[0][2]),
                   (w[i + 1][0] - w[0][0], w[i + 1][1] - w[0][1], w[i + 1][2] - w[0][2]))
        a = (a[0] + c[0], a[1] + c[1], a[2] + c[2])
    return 0.5 * math.sqrt(_dot(a, a))


def brush_ok(br, bounds, margin=64.0):
    """Rebuild the brush from its written (map space) planes like qbsp does."""
    planes = [f.plane() for f in br.faces]
    lo = [min(bounds[0][i], bounds[1][i]) - margin for i in range(3)]
    hi = [max(bounds[0][i], bounds[1][i]) + margin for i in range(3)]
    lo, hi = d6map.to_map(lo), d6map.to_map(hi)
    lo, hi = [min(a, b) for a, b in zip(lo, hi)], [max(a, b) for a, b in zip(lo, hi)]
    nfaces = 0
    for i, (n, d) in enumerate(planes):
        w = base_winding(tuple(n), d)
        for j, (n2, d2) in enumerate(planes):
            if j != i and w is not None:
                w = clip_winding(w, tuple(n2), d2)
        if w is None:
            continue
        nfaces += 1
        for p in w:
            if any(p[k] < lo[k] or p[k] > hi[k] for k in range(3)):
                return False
    return nfaces >= 4


def convex_overlap(a, b, n):
    """Area of the intersection of two convex coplanar polygons."""
    w = a
    m = len(b)
    # orientation of b relative to n
    c = _cross((b[1][0] - b[0][0], b[1][1] - b[0][1], b[1][2] - b[0][2]),
               (b[2][0] - b[0][0], b[2][1] - b[0][1], b[2][2] - b[0][2])) if m >= 3 else (0, 0, 0)
    sg = 1.0 if _dot(c, n) >= 0 else -1.0
    for i in range(m):
        p, q = b[i], b[(i + 1) % m]
        e = (q[0] - p[0], q[1] - p[1], q[2] - p[2])
        if _dot(e, e) < 1e-9:
            continue
        cn = _cross(e, n)
        cn = (cn[0] * sg, cn[1] * sg, cn[2] * sg)       # points out of b
        l = math.sqrt(_dot(cn, cn))
        cn = (cn[0] / l, cn[1] / l, cn[2] / l)
        w = clip_winding(w, cn, _dot(cn, p))
        if w is None:
            return 0.0
    return winding_area(w)


def centroid(w):
    n = float(len(w))
    return (sum(p[0] for p in w) / n, sum(p[1] for p in w) / n, sum(p[2] for p in w) / n)


def point_in_winding(w, p, n):
    for i in range(len(w)):
        a, b = w[i], w[(i + 1) % len(w)]
        e = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        c = _cross(e, (p[0] - a[0], p[1] - a[1], p[2] - a[2]))
        if _dot(c, n) < -0.01:
            return False
    return True


# --------------------------------------------------------------------------
# textures
# --------------------------------------------------------------------------
def texture_names(wad):
    """Unique PNG names per texture index (duplicates get ~1, ~2 ...)."""
    seen = {}
    out = []
    for t in wad.textures:
        n = t.name.strip().replace('/', '_').replace('\\', '_').replace('*', '_') or 'tex'
        k = seen.get(n.lower(), 0)
        seen[n.lower()] = k + 1
        out.append(n if k == 0 else '%s~%d' % (n, k))
    return out


def export_textures(wad, level, texroot):
    from PIL import Image
    d = os.path.join(texroot, level.lower())
    os.makedirs(d, exist_ok=True)
    names = texture_names(wad)
    for i, n in enumerate(names):
        w, h, raw = wad.texture_rgb(i)
        p = os.path.join(d, n + '.png')
        if not os.path.exists(p):
            Image.frombytes('RGB', (w, h), raw).save(p)
    return ['%s/%s' % (level.lower(), n) for n in names]


def write_special_textures(texroot):
    """_special/: skip, clip, nodraw, hint, water, lava (editor-only images)."""
    from PIL import Image, ImageDraw
    d = os.path.join(texroot, '_special')
    os.makedirs(d, exist_ok=True)
    spec = {'skip': (200, 60, 200), 'clip': (200, 120, 40), 'nodraw': (90, 90, 90), 'hint': (220, 220, 60),
            'water': (40, 80, 200), 'lava': (220, 70, 20)}
    for n, c in spec.items():
        p = os.path.join(d, n + '.png')
        if os.path.exists(p):
            continue
        im = Image.new('RGB', (128, 128), c)
        dr = ImageDraw.Draw(im)
        for k in range(0, 256, 32):
            dr.line([(k, 0), (k - 128, 128)], fill=tuple(int(x * 0.7) for x in c), width=6)
        dr.text((8, 56), n.upper(), fill=(255, 255, 255))
        im.save(p)


# --------------------------------------------------------------------------
# decompile
# --------------------------------------------------------------------------
class Decompiler(object):
    def __init__(self, bsp, texnames, log=print):
        self.b = bsp
        self.texnames = texnames
        self.log = log
        self.pl = []
        for p in bsp.planes:
            d = p.dist
            if max(abs(p.nx), abs(p.ny), abs(p.nz)) > 0.99999 and abs(d - round(d)) < 0.01:
                d = float(round(d))        # retail planes have 255.9999-style floats
            self.pl.append(((p.nx, p.ny, p.nz), d))
        # faces by (planenum, side) with their polygons (engine space)
        self.faces_on = {}
        for fi, f in enumerate(bsp.faces):
            self.faces_on.setdefault((f.planenum, f.side), []).append(fi)
        self._poly = {}

    def poly(self, fi):
        p = self._poly.get(fi)
        if p is None:
            p = self._poly[fi] = self.b.face_points(fi)
        return p

    def leaf_regions(self, model):
        """[(leaf index, [(n, d, planenum, side)])] for every leaf of the model's
        drawing tree; halfspaces dot(x, n) <= d (n points out of the region)."""
        m = self.b.models[model]
        out = []
        stack = [(m.head0, [])]
        while stack:
            node, hs = stack.pop()
            if node < 0:
                out.append((-(node + 1), hs))
                continue
            nd = self.b.nodes[node]
            n, d = self.pl[nd.planenum]
            # front: dot >= d  ->  outward normal -n, side 1 faces look out of the solid
            stack.append((nd.front, hs + [((-n[0], -n[1], -n[2]), -d, nd.planenum, 1)]))
            stack.append((nd.back, hs + [(n, d, nd.planenum, 0)]))
        return out

    def region_faces(self, hs, bounds):
        mn, mx = bounds
        planes = list(hs)
        for ax in range(3):
            n = [0.0, 0.0, 0.0]
            n[ax] = 1.0
            planes.append((tuple(n), mx[ax], None, None))
            n = [0.0, 0.0, 0.0]
            n[ax] = -1.0
            planes.append((tuple(n), -mn[ax], None, None))
        faces = []
        for i, (n, d, pn, side) in enumerate(planes):
            w = base_winding(n, d)
            for j, (n2, d2, _, _) in enumerate(planes):
                if j == i:
                    continue
                if _dot(n, n2) > 0.99999 and abs(d - d2) < 1e-4:
                    if j < i:
                        w = None          # duplicate plane, keep the first
                        break
                    continue
                w = clip_winding(w, n2, d2)
                if w is None:
                    break
            if w is not None and winding_area(w) > 0.01:
                faces.append((n, d, pn, side, w))
        return faces

    def texture_for(self, pn, side, w, n):
        """(texinfo index, face index) of the BSP face on this brush face."""
        if pn is None:
            return None
        cands = self.faces_on.get((pn, side), [])
        if not cands:
            return None
        c = centroid(w)
        best = None
        for fi in cands:
            fp = self.poly(fi)
            fc = centroid(fp)
            if point_in_winding(w, fc, n) or point_in_winding(fp, c, n):
                return self.b.faces[fi].texinfo
            dd = math.dist(fc, c)
            if best is None or dd < best[0]:
                best = (dd, self.b.faces[fi].texinfo)
        return best[1] if best and best[0] < 4096 else None

    def _emit(self, hs, faces, depth, bounds, contents):
        """Brushes for one region, split along texture changes. Returns
        (brushes, number of splits). A split whose pieces do not survive the
        round trip through the written planes is not made."""
        texs = []
        cut = None
        for n, d, pn, side, w in faces:
            over = self.overlapping(pn, side, w, n)
            keys = set(self.texkey(fi) for fi, _ in over)
            if len(keys) > 1 and depth < 24 and cut is None:
                cut = self.find_cut(over, w, n)
            texs.append(self.b.faces[max(over, key=lambda x: x[1])[0]].texinfo if over else None)
        if cut is not None:
            c, cd = cut
            out, ns, ok = [], 1, True
            for part in (hs + [(c, cd, None, None)], hs + [((-c[0], -c[1], -c[2]), -cd, None, None)]):
                pf = self.region_faces(part, bounds)
                if len(pf) < 4:
                    ok = False
                    break
                allp = [p for f in pf for p in f[4]]
                if min(max(d - _dot(p, n) for p in allp) for n, d, _, _, _ in pf) < 0.5:
                    ok = False
                    break
                r = self._emit(part, pf, depth + 1, bounds, contents)
                out += r[0]
                ns += r[1]
            if ok:
                return out, ns
        known = [t for t in texs if t is not None and self.b.texinfo[t].flags != -1]
        fill = max(set(known), key=known.count) if known else None
        br = d6map.Brush()
        for (n, d, pn, side, w), ti in zip(faces, texs):
            br.faces.append(self.map_face(n, d, w, ti if ti is not None else fill, contents))
        if not brush_ok(br, bounds):
            self.nbad += 1
        return [br], 0

    def texkey(self, fi):
        """Faces with the same key look the same: texture, flags and the
        projection (offset modulo one repeat)."""
        ti = self.b.texinfo[self.b.faces[fi].texinfo]
        if ti.flags == -1:
            return ('nodraw',)
        org = (ti.ox, ti.oy, ti.oz)
        su = ((ti.soff * 0.25 - _dot(org, (ti.sx, ti.sy, ti.sz))) / 32.0) % 1.0
        tv = ((ti.toff * 0.25 - _dot(org, (ti.tx, ti.ty, ti.tz))) / 32.0) % 1.0
        return (ti.flags, round(ti.sx, 4), round(ti.sy, 4), round(ti.sz, 4),
                round(ti.tx, 4), round(ti.ty, 4), round(ti.tz, 4), round(su, 2) % 1.0, round(tv, 2) % 1.0)

    def overlapping(self, pn, side, w, n):
        """[(face index, overlap area)] of BSP faces on this brush face."""
        if pn is None:
            return []
        out = []
        for fi in self.faces_on.get((pn, side), []):
            a = convex_overlap(w, self.poly(fi), n)
            if a > 0.5:
                out.append((fi, a))
        return out

    def find_cut(self, over, w, n):
        """A plane through an edge of one of the faces, across the brush face,
        that separates differently looking faces."""
        keys = {}
        for fi, a in over:
            keys.setdefault(self.texkey(fi), []).append(fi)
        for fi, _ in sorted(over, key=lambda x: x[1]):
            fp = self.poly(fi)
            for i in range(len(fp)):
                p, q = fp[i], fp[(i + 1) % len(fp)]
                e = (q[0] - p[0], q[1] - p[1], q[2] - p[2])
                if _dot(e, e) < 1e-6:
                    continue
                c = _norm(_cross(e, n))
                c = _norm(tuple(0.0 if abs(x) < 1e-5 else x for x in c))
                cd = _dot(c, p)
                if max(abs(x) for x in c) > 0.99999:
                    c = tuple(float(round(x)) for x in c)
                    cd = _dot(c, p)
                    if abs(cd - round(cd)) < 0.01:
                        cd = float(round(cd))
                a1 = clip_winding(w, c, cd)
                a2 = clip_winding(w, (-c[0], -c[1], -c[2]), -cd)
                if a1 and a2 and winding_area(a1) > 0.5 and winding_area(a2) > 0.5:
                    return c, cd
        return None

    def map_face(self, n, d, w, texinfo, contents):
        """Brush face in map space with Valve alignment from a Deep6 texinfo."""
        sw = d6map.to_map
        # three points that depend only on the plane, so every brush on the
        # same BSP plane writes exactly the same plane (no hairline cracks)
        N, D = n, d
        k = 0 if abs(N[0]) > 1e-9 else (1 if abs(N[1]) > 1e-9 else 2)
        if N[k] < 0:
            N, D = (-N[0], -N[1], -N[2]), -D
        p0 = (N[0] * D, N[1] * D, N[2] * D)
        if max(abs(c) for c in N) > 0.99999:
            ax = [i for i in range(3) if abs(N[i]) > 0.5][0]
            if abs(D - round(D)) < 0.01:          # retail planes have 255.9999-style floats
                D = float(round(D))
                p0 = (N[0] * D, N[1] * D, N[2] * D)
            p0 = tuple(float(round(c)) if i != ax else c for i, c in enumerate(p0))
        a = min(range(3), key=lambda i: abs(N[i]))
        ea = [0.0, 0.0, 0.0]
        ea[a] = 1.0
        u = _norm(_cross(N, ea))
        v = _cross(N, u)
        L = 512.0
        pts = [sw(p0), sw((p0[0] + u[0] * L, p0[1] + u[1] * L, p0[2] + u[2] * L)),
               sw((p0[0] + v[0] * L, p0[1] + v[1] * L, p0[2] + v[2] * L))]
        f = d6map.Face(pts, '_special/skip')
        fn, _ = f.plane()
        nm = sw(n)
        if _dot(fn, nm) < 0:
            f.pts = [pts[0], pts[2], pts[1]]
        flags = 0
        if texinfo is None:
            tex = '_special/nodraw'
            s, t = None, None
        else:
            ti = self.b.texinfo[texinfo]
            if ti.flags == -1:
                tex = '_special/nodraw'
                flags |= d6map.SURF_NODRAW
            else:
                idx = ti.flags & 0xfff
                tex = self.texnames[idx] if idx < len(self.texnames) else '_special/nodraw'
                if ti.flags & 0x8000:
                    flags |= d6map.SURF_WATER
                if ti.flags & 0x2000:
                    flags |= d6map.SURF_TRANS
                if ti.flags & 0x20000:
                    flags |= d6map.SURF_TERRAIN
            s = (ti.sx, ti.sy, ti.sz, ti.soff)
            t = (ti.tx, ti.ty, ti.tz, ti.toff)
            org = (ti.ox, ti.oy, ti.oz)
        f.tex = tex
        f.contents = contents
        f.flags = flags & ~(d6map.SURF_WATER if contents & d6map.CONT_WATER else 0) \
            & ~(d6map.SURF_TRANS if contents & d6map.CONT_LAVA else 0)
        if s is not None:
            # a texinfo borrowed from another face may be parallel to this one
            c = _cross(s[:3], t[:3])
            lc = math.sqrt(_dot(c, c)) or 1.0
            if abs(_dot(c, n)) / lc < 0.1:
                s = t = None
        if s is None:
            # default projection, scale 0.5
            ax = max(range(3), key=lambda i: abs(nm[i]))
            u = (1, 0, 0) if ax != 0 else (0, 1, 0)
            v = (0, 0, -1) if ax != 2 else (0, -1, 0)
            f.u, f.v, f.rot, f.sx, f.sy = u + (0,), v + (0,), 0, 0.5, 0.5
            return f
        for vec, which in ((s, 'u'), (t, 'v')):
            a = sw((vec[0] * 4, vec[1] * 4, vec[2] * 4))
            la = math.sqrt(_dot(a, a)) or 1.0
            shift = vec[3] - 4 * _dot(org, vec[:3])
            axis = (a[0] / la, a[1] / la, a[2] / la, shift)
            if which == 'u':
                f.u, f.sx = axis, 1.0 / la
            else:
                f.v, f.sy = axis, 1.0 / la
        f.rot = 0
        return f

    def model_brushes(self, model):
        b = self.b
        m = b.models[model]
        pad = 8.0
        bounds = ((m.minx - pad, m.miny - pad, m.minz - pad), (m.maxx + pad, m.maxy + pad, m.maxz + pad))
        tr = _Tracer(b, model)
        brushes = []
        nsolid = ndrop = nthin = nsplit = 0
        self.nbad = 0
        for li, hs in self.leaf_regions(model):
            cont = b.leafs[li].contents
            if cont not in (-2, -3, -4, -5):
                continue
            faces = self.region_faces(hs, bounds)
            if len(faces) < 4:
                continue
            # slivers (thinner than half a unit) only upset qbsp
            allp = [p for f in faces for p in f[4]]
            if min(max(d - _dot(p, n) for p in allp) for n, d, _, _, _ in faces) < 0.5:
                nthin += 1
                continue
            if cont == -2:
                nsolid += 1
                touching = False
                for n, d, pn, side, w in faces:
                    off = [(p[0] + n[0] * 0.5, p[1] + n[1] * 0.5, p[2] + n[2] * 0.5) for p in w]
                    if tr.poly_open(off):
                        touching = True
                        break
                if not touching:
                    ndrop += 1
                    continue
            contents = {-3: d6map.CONT_WATER, -4: d6map.CONT_WATER, -5: d6map.CONT_LAVA}.get(cont, 0)
            # one brush face can only carry one texture: split the region
            # where the BSP faces on its sides change texture or alignment
            res = self._emit(hs, faces, 0, bounds, contents)
            nsplit += res[1]
            brushes.extend(res[0])
        if nthin:
            self.log('  %d sliver regions left out' % nthin)
        if nsplit:
            self.log('  %d splits along texture boundaries' % nsplit)
        if self.nbad:
            self.log('  warning: %d brushes may be malformed' % self.nbad)
        return brushes, nsolid, ndrop


def _nudge_out(p, tr, dist=4.0):
    """Lights sit exactly on walls in the retail data; qbsp then sees the
    light inside the wall and reports a leak. Move it off the wall."""
    def free(q):
        return all(tr.contents((q[0] + dx, q[1] + dy, q[2] + dz)) != -2
                   for dx in (-1, 1) for dy in (-1, 1) for dz in (-1, 1))
    if free(p):
        return p
    best = None
    for ax in range(3):
        for sg in (-1, 1):
            for k in (1, 2, 4, 8, 16):
                q = list(p)
                q[ax] += sg * k
                if free(q):
                    if best is None or k < best[0]:
                        best = (k, tuple(q))
                    break
    return best[1] if best else p


class _Tracer(object):
    def __init__(self, b, model):
        self.b = b
        self.head = b.models[model].head0
        self.pl = [(p.nx, p.ny, p.nz, p.dist) for p in b.planes]

    def poly_open(self, w, node=None):
        """True if any part of polygon w lies in a non-solid leaf."""
        stack = [(self.head if node is None else node, w)]
        while stack:
            n, poly = stack.pop()
            if n < 0:
                if self.b.leafs[-(n + 1)].contents != -2:
                    return True
                continue
            nd = self.b.nodes[n]
            a = self.pl[nd.planenum]
            nn = (a[0], a[1], a[2])
            front = clip_winding(poly, (-nn[0], -nn[1], -nn[2]), -a[3])    # dot >= d
            back = clip_winding(poly, nn, a[3])                              # dot <= d
            if front is not None and winding_area(front) > 1e-3:
                stack.append((nd.front, front))
            if back is not None and winding_area(back) > 1e-3:
                stack.append((nd.back, back))
        return False

    def contents(self, p):
        n = self.head
        while n >= 0:
            nd = self.b.nodes[n]
            a = self.pl[nd.planenum]
            n = nd.front if a[0] * p[0] + a[1] * p[1] + a[2] * p[2] - a[3] >= 0 else nd.back
        return self.b.leafs[-(n + 1)].contents


def decompile(gamedir, level, out_map, texroot=None, log=print):
    bp = d6level.find_file(gamedir, level + '.bsp')
    if not bp:
        raise FileNotFoundError(level + '.bsp')
    b = d6level.BSPFile.load(bp)
    tp = d6level.find_file(gamedir, level + '.twd')
    wad = d6level.TexWad.load(tp) if tp else d6level.TexWad()
    texroot = texroot or os.path.join(os.path.dirname(os.path.abspath(out_map)), 'textures')
    names = export_textures(wad, level, texroot)
    write_special_textures(texroot)
    dc = Decompiler(b, names, log)
    ents = []
    world = d6map.Entity([('classname', 'worldspawn'), ('worldtype', '0'), ('d6ambient', '3.5'),
                          ('d6level', level.lower())])
    world.brushes, ns, nd = dc.model_brushes(0)
    log('%s: world %d brushes (%d solid regions, %d buried ones left out)'
        % (level, len(world.brushes), ns, nd))
    ents.append(world)
    for e in b.entity_dicts():
        mdl = e.get('model', '')
        if mdl.startswith('*'):
            mi = int(mdl[1:])
            ne = d6map.Entity([(k, v) for k, v in e.items() if k not in ('model',)])
            ne.brushes, _, _ = dc.model_brushes(mi)
            ents.append(ne)
    lp = d6level.find_file(gamedir, level + '.lgt')
    if lp:
        lights = d6level.LightList.load(lp).lights
        rp = d6level.find_file(gamedir, level + '.rgb')
        cols = d6level.LightColors.load(rp) if rp else None
        tr0 = _Tracer(b, 0)
        for i, l in enumerate(lights):
            pos = _nudge_out((l.x, l.y, l.z), tr0)
            o = d6map.to_map(pos)
            keys = [('classname', 'light'), ('origin', '%g %g %g' % tuple(round(c, 2) for c in o)),
                    ('d6light', '%g' % l.intensity)]
            if cols is not None and i < len(cols.colors):
                r, g, bb = cols.rgb(i)
                if (r, g, bb) != (255, 255, 255):
                    keys.append(('_color', '%d %d %d' % (r, g, bb)))
            ents.append(d6map.Entity(keys))
    np_ = d6level.find_file(gamedir, level + '.nvs')
    if np_:
        nv = d6level.NavPoints.load(np_)
        if nv.points:
            p = nv.points[0]
            o = d6map.to_map((p.x, p.y - 60, p.z))
            ents.append(d6map.Entity([('classname', 'info_player_start'),
                                      ('origin', '%g %g %g' % tuple(round(c) for c in o))]))
    _hull_layer(ents)
    with open(out_map, 'w', encoding='latin-1') as f:
        f.write(d6map.write(ents))
    return ents


def _hull_layer(ents):
    """Brushes that are nothing but _special/nodraw (the solid space around the
    level) go into their own TrenchBroom layer, hidden and locked, so they do
    not box in the view or catch clicks. The compiler puts layer brushes back
    into the world."""
    world = ents[0]
    hull = [b for b in world.brushes if b.faces and all(f.tex == '_special/nodraw' for f in b.faces)]
    if not hull:
        return
    keep = set(map(id, hull))
    world.brushes = [b for b in world.brushes if id(b) not in keep]
    ents.insert(1, d6map.Entity([('classname', 'func_group'), ('_tb_type', '_tb_layer'),
                                 ('_tb_name', 'Outer hull (nodraw)'), ('_tb_id', '1'),
                                 ('_tb_layer_sort_index', '0'), ('_tb_layer_hidden', '1'),
                                 ('_tb_layer_locked', '1')], hull))


def main(argv):
    a = argv[1:]
    if len(a) >= 3 and a[0] == '--export-textures':
        g, d = a[1], a[2]
        n = 0
        for f in sorted(os.listdir(g)):
            if f.lower().endswith('.twd') and f.lower() != 'syswat.twd':
                export_textures(d6level.TexWad.load(os.path.join(g, f)), f[:-4], d)
                n += 1
        write_special_textures(d)
        print('exported the textures of %d levels to %s' % (n, d))
        return 0
    if len(a) >= 3:
        tex = None
        if '--textures' in a:
            i = a.index('--textures')
            tex = a[i + 1]
            del a[i:i + 2]
        decompile(a[0], a[1], a[2], tex)
        print('wrote', a[2])
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
