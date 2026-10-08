#!/usr/bin/env python3
"""
render_level.py - sanity renders of a Deep6 indoor level.

usage: render_level.py GAMEDIR LEVEL [OUTROOT]     (OUTROOT default /tmp/levels)

Writes to OUTROOT/<level>/:
  <level>_wire.png    top-down (X right, Z down, Y is up) wireframe of all
                      faces, coloured by height; brush models (doors) in
                      red, nav graph in green, lights yellow, .bol objects
                      (P cyan, M magenta, I white)
  <level>_floors.png  top-down textured render of upward facing faces
                      (checks texinfo UV mapping + .twd decoding)
  <level>.obj/.mtl    textured mesh (BSP units, Y up) + textures/*.png
"""
import os
import sys
import math

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import d6level as d6  # noqa: E402

WATER_FLAGS = d6.TEXF_WATER | d6.TEXF_LAVA


def face_normal(bsp, f):
    p = bsp.planes[f.planenum]
    n = np.array([p.nx, p.ny, p.nz])
    return -n if f.side else n


def load_textures(lv, gamedir, outdir):
    """Decode .twd textures to PNG, returns list of (name, path, rgb ndarray)."""
    texdir = os.path.join(outdir, 'textures')
    os.makedirs(texdir, exist_ok=True)
    res = []
    for i, t in enumerate(lv.twd.textures):
        w, h, rgb = lv.twd.texture_rgb(i)
        arr = np.frombuffer(rgb, np.uint8).reshape(h, w, 3)
        fn = '%03d_%s.png' % (i, ''.join(c if c.isalnum() or c in '-_' else '_' for c in t.name))
        Image.fromarray(arr).save(os.path.join(texdir, fn))
        res.append((t.name, 'textures/' + fn, arr))
    wat = []
    p = d6.find_file(gamedir, 'syswat.twd')
    if p:
        sw = d6.TexWad.load(p)
        for i, t in enumerate(sw.textures):
            w, h, rgb = sw.texture_rgb(i)
            arr = np.frombuffer(rgb, np.uint8).reshape(h, w, 3)
            fn = 'sys_%s.png' % t.name
            Image.fromarray(arr).save(os.path.join(texdir, fn))
            wat.append((t.name, 'textures/' + fn, arr))
    return res, wat


def face_material(f, bsp):
    ti = bsp.texinfo[f.texinfo]
    if ti.flags < 0:
        return None
    if ti.flags & d6.TEXF_WATER:
        return ('water', 0)
    if ti.flags & d6.TEXF_LAVA:
        return ('water', 1)
    return ('tex', ti.flags & d6.TEXF_INDEX_MASK)


def export_obj(lv, name, outdir, texs, wat):
    bsp = lv.bsp
    objp = os.path.join(outdir, name + '.obj')
    mtlp = os.path.join(outdir, name + '.mtl')
    groups = {}
    for fi, f in enumerate(bsp.faces):
        m = face_material(f, bsp)
        if m is None:
            continue
        groups.setdefault(m, []).append(fi)
    with open(mtlp, 'w') as mf:
        for (kind, idx) in sorted(groups):
            if kind == 'tex':
                if idx >= len(texs):
                    continue
                mname, path = 'tex%03d_%s' % (idx, texs[idx][0]), texs[idx][1]
            else:
                if idx >= len(wat):
                    continue
                mname, path = 'water%d_%s' % (idx, wat[idx][0]), wat[idx][1]
            mf.write('newmtl %s\nKd 1 1 1\nmap_Kd %s\n\n' % (mname, path))
    with open(objp, 'w') as of:
        of.write('# Deep6 level %s, BSP units, Y up\nmtllib %s\n' % (name, os.path.basename(mtlp)))
        for v in bsp.vertices:
            of.write('v %.4f %.4f %.4f\n' % (v.x, v.y, -v.z))   # flip Z: right handed
        vt = 1
        for (kind, idx) in sorted(groups):
            if kind == 'tex':
                if idx >= len(texs):
                    continue
                of.write('usemtl tex%03d_%s\n' % (idx, texs[idx][0]))
            else:
                if idx >= len(wat):
                    continue
                of.write('usemtl water%d_%s\n' % (idx, wat[idx][0]))
            for fi in groups[(kind, idx)]:
                f = bsp.faces[fi]
                vids = bsp.face_vertex_indices(f)
                for vi in vids:
                    u, v = bsp.face_uv(f, bsp.vertices[vi])
                    of.write('vt %.5f %.5f\n' % (u, -v))
                # Z flip mirrors the winding, so reverse to keep outward faces
                idxs = ['%d/%d' % (vids[k] + 1, vt + k) for k in range(len(vids))][::-1]
                of.write('f %s\n' % ' '.join(idxs))
                vt += len(vids)
    return objp


def render_wire(lv, name, outdir, size=2048):
    bsp = lv.bsp
    vs = np.array([tuple(v) for v in bsp.vertices], dtype=np.float64)
    mn, mx = vs.min(0), vs.max(0)
    span = max(mx[0] - mn[0], mx[2] - mn[2], 1.0)
    sc = (size - 40) / span
    W = int((mx[0] - mn[0]) * sc) + 40
    H = int((mx[2] - mn[2]) * sc) + 40

    def P(x, z):
        return (20 + (x - mn[0]) * sc, 20 + (z - mn[2]) * sc)

    img = Image.new('RGB', (W, H), (10, 10, 16))
    dr = ImageDraw.Draw(img)
    world = set(bsp.model_faces(0))
    yr = max(mx[1] - mn[1], 1.0)
    order = sorted(range(len(bsp.faces)),
                   key=lambda i: np.mean([bsp.vertices[k].y for k in bsp.face_vertex_indices(i)]))
    for fi in order:
        pts = bsp.face_points(fi)
        if fi in world:
            t = (np.mean([p[1] for p in pts]) - mn[1]) / yr
            col = (int(40 + 200 * t), int(80 + 120 * (1 - abs(t - .5) * 2)), int(240 - 200 * t))
        else:
            col = (255, 60, 60)
        dr.line([P(p[0], p[2]) for p in pts] + [P(pts[0][0], pts[0][2])], fill=col, width=1)
    if lv.nvs is not None:
        nv = lv.nvs
        for i, p in enumerate(nv.points):
            for j in nv.links(i):
                if j < len(nv.points):
                    q = nv.points[j]
                    dr.line([P(p.x, p.z), P(q.x, q.z)], fill=(0, 200, 0), width=2)
        for p in nv.points:
            x, y = P(p.x, p.z)
            dr.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(0, 255, 0))
    if lv.lgt is not None:
        for l in lv.lgt.lights:
            x, y = P(l.x, l.z)
            dr.ellipse([x - 4, y - 4, x + 4, y + 4], outline=(255, 230, 0))
    if lv.bol is not None:
        cols = {b'P': (0, 230, 230), b'M': (255, 0, 255), b'I': (255, 255, 255)}
        for r in lv.bol.records:
            if r.type not in cols:
                continue
            bx, bz = r.x * d6.BSP_UNITS_PER_WORLD, r.z * d6.BSP_UNITS_PER_WORLD
            x, y = P(bx, bz)
            dr.rectangle([x - 2, y - 2, x + 2, y + 2], fill=cols[r.type])
    p = os.path.join(outdir, name + '_wire.png')
    img.save(p)
    return p


def render_floors(lv, name, outdir, texs, size=2048):
    """Top-down textured render of up-facing faces (normal.y > 0.3)."""
    bsp = lv.bsp
    vs = np.array([tuple(v) for v in bsp.vertices], dtype=np.float64)
    mn, mx = vs.min(0), vs.max(0)
    span = max(mx[0] - mn[0], mx[2] - mn[2], 1.0)
    sc = (size - 40) / span
    W = int((mx[0] - mn[0]) * sc) + 40
    H = int((mx[2] - mn[2]) * sc) + 40
    canvas = np.zeros((H, W, 3), np.uint8)
    import cv2
    faces = []
    for fi, f in enumerate(bsp.faces):
        m = face_material(f, bsp)
        if m is None:
            continue
        n = face_normal(bsp, f)
        if n[1] <= 0.3:
            continue
        pts = bsp.face_points(f)
        faces.append((np.mean([p[1] for p in pts]), fi, m, pts))
    faces.sort(key=lambda t: t[0])
    tiles = {}
    for _, fi, m, pts in faces:
        f = bsp.faces[fi]
        if m[0] == 'tex' and m[1] < len(texs):
            tex = texs[m[1]][2]
        else:
            tex = np.full((128, 128, 3), (30, 60, 160), np.uint8)
        th, tw = tex.shape[:2]
        uv = np.array([bsp.face_uv(f, p) for p in pts]) * [tw, th]
        scr = np.array([[20 + (p[0] - mn[0]) * sc, 20 + (p[2] - mn[2]) * sc] for p in pts])
        x0, y0 = np.floor(scr.min(0)).astype(int)
        x1, y1 = np.ceil(scr.max(0)).astype(int) + 1
        if x1 - x0 < 1 or y1 - y0 < 1:
            continue
        # affine texture->screen from the first non-degenerate triangle
        A = None
        for k in range(1, len(pts) - 1):
            src = np.float32([uv[0], uv[k], uv[k + 1]])
            if abs(np.linalg.det(np.c_[src, np.ones(3)])) > 1e-6:
                A = cv2.getAffineTransform(src, np.float32([scr[0], scr[k], scr[k + 1]]))
                break
        if A is None:
            continue
        # tile the texture to cover the uv range of the face
        umin, vmin = np.floor(uv.min(0) / [tw, th]).astype(int)
        umax, vmax = np.ceil(uv.max(0) / [tw, th]).astype(int)
        nu, nv = max(umax - umin, 1), max(vmax - vmin, 1)
        if nu * nv > 400:
            continue
        key = (id(tex), nu, nv)
        big = tiles.get(key)
        if big is None:
            big = np.tile(tex, (nv, nu, 1))
            tiles[key] = big
        T = np.array([[1, 0, umin * tw], [0, 1, vmin * th], [0, 0, 1]], np.float64)
        M = (np.vstack([A, [0, 0, 1]]) @ T)
        M[0, 2] -= x0
        M[1, 2] -= y0
        patch = cv2.warpAffine(big, M[:2], (x1 - x0, y1 - y0), flags=cv2.INTER_NEAREST)
        mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillPoly(mask, [np.round(scr - [x0, y0]).astype(np.int32)], 1)
        sub = canvas[y0:y1, x0:x1]
        sub[mask > 0] = patch[mask > 0]
    p = os.path.join(outdir, name + '_floors.png')
    Image.fromarray(canvas).save(p)
    return p


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    gamedir, name = argv[1], argv[2].lower()
    outroot = argv[3] if len(argv) > 3 else '/tmp/levels'
    outdir = os.path.join(outroot, name)
    os.makedirs(outdir, exist_ok=True)
    lv = d6.Level(gamedir, name)
    print(name, lv.bsp.summary())
    print('wrote', render_wire(lv, name, outdir))
    if lv.twd is not None:
        texs, wat = load_textures(lv, gamedir, outdir)
        print('wrote', export_obj(lv, name, outdir, texs, wat))
        print('wrote', render_floors(lv, name, outdir, texs))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
