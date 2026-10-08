#!/usr/bin/env python3
"""d6walls.py - tree / castle walls, canopy and foliage of the outdoor
terrain spokes of Wizards & Warriors (Deep6 engine).

Mirrors RenderTile_ (tnew.c 0x4f55b4), Terrain_LoadTextures_ (terrain.c
0x413c74) and LoadStaticObjects_ (scenload.c 0x4d6dcc).  Documentation:
docs/formats/walls_textures.md.

API
  wall_mesh(terrain_map, gamedir, spoke, region=None)  -> list of polygon dicts
  wall_textures(gamedir, spoke, shade=31)              -> {name: RGBA uint8 (h,w,4)}
  ground_triangles(flags)                              -> ground tris RenderTile_ draws
  foliage_choice(spoke, record_index, record_id=None, mode='id', seed=None)
  foliage_props(gamedir, spoke, mode='id', seed=None)  -> list of dicts
  render_walls(gamedir, spoke, region, out_png, ...)   -> quick oblique preview

Polygon dict (one per poly the game emits):
  kind      'wall' (straight edge face), 'diag' (diagonal face), 'canopy'
  tile      (x, z) tile index
  flag      the flag bit that produced it (0x10/0x20/0x40/0x80, 1/2/4/8)
  texture   path relative to tiles/, e.g. 'forest/forwal01.bmp'
  slot      terrain wall bitmap slot 0..6 (see WALL_SLOT_NAMES)
  palette   path relative to tiles/ of the .p16 used ('forest/forwal.p16')
  verts     list of (x, y, z) world units, y up (1 tile = 1024)
  uv        list of (u, v) normalised 0..1 (u right, v down in the bitmap)
  uv_texel  list of (u, v) in texels exactly as the engine passes them
  light     list of shade rows (0..31) per vertex (baked TMR light)
  normal    outward unit normal (x, y, z); walls are single sided
  masked    False - the engine draws walls / canopy opaque (no colour key)
Walls have 4 vertices (top, top, ground, ground); canopy polys are the
triangles of the replaced ground triangles (3 vertices).
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import d6terrain as T  # noqa: E402

TILE = 1024

# Terrain_LoadTextures_: terrain+0x2c bitmap slots
WALL_SLOT_NAMES = {
    'default': ['forest/forwal00.bmp', 'forest/forwal01.bmp', 'forest/forwal02.bmp',
                'forest/forwal03.bmp', 'forest/forcan00.bmp',
                'forest/caswal00.bmp', 'forest/cascan00.bmp'],
    11: ['forest/plmwal00.bmp', 'forest/plmwal01.bmp', 'forest/plmwal02.bmp',
         'forest/plmwal03.bmp', 'forest/plmcan00.bmp',
         'forest/caswal00.bmp', 'forest/cascan00.bmp'],
}
SLOT_CANOPY, SLOT_CASTLE_WALL, SLOT_CASTLE_TOP = 4, 5, 6

# LoadStaticObjects_ prop tables (D6PROP record numbers)
FOLIAGE_TABLE = {'default': (1, 2, 3, 4, 0xe7), 11: (0xe4, 0xe5, 0xe6)}
FOLIAGE_MODELS = {1: 'prop/tree-forest.mdl', 2: 'prop/tree-cedar.mdl',
                  3: 'prop/tree-willow.mdl', 4: 'prop/tree-sequoia.mdl',
                  0xe7: 'prop/tree-birch.mdl', 0xe4: 'prop/palm1.mdl',
                  0xe5: 'prop/palm2.mdl', 0xe6: 'prop/palm3.mdl'}

UV_FUDGE = -0.5            # _DAT_005c83f9
R2 = 0.70710677            # _s_wall_NE etc.

# outward normals of the wall faces (camera side on which the game draws them)
FACE_NORMAL = {0x20: (0, 0, 1), 0x40: (-1, 0, 0), 0x80: (0, 0, -1), 0x10: (1, 0, 0),
               1: (-R2, 0, -R2), 4: (R2, 0, R2), 2: (R2, 0, -R2), 8: (-R2, 0, R2)}


def slot_names(spoke):
    return WALL_SLOT_NAMES[11 if spoke == 11 else 'default']


def slot_palette(slot, spoke):
    """Bitmaps 0..4 use terrain palette slot 6, 5..6 slot 7."""
    if slot >= 5:
        return 'forest/caswal.p16'
    return 'forest/plmwal.p16' if spoke == 11 else 'forest/forwal.p16'


def ground_triangles(flags):
    """Ground triangles RenderTile_ draws for a tile, as tuples of corner
    names: V0 (x,z+1) V1 (x+1,z+1) V2 (x+1,z) V3 (x,z).  Flags 2/8 select
    the V3-V1 split, otherwise V0-V2.  A triangle whose flag bit is set is
    not drawn (replaced by canopy at +unk10)."""
    alt = bool(flags & 0xa)
    out = []
    if alt:
        if not flags & 2:
            out.append(('V3', 'V0', 'V1'))
        if not flags & 8:
            out.append(('V1', 'V2', 'V3'))
    else:
        if not flags & 1:
            out.append(('V0', 'V1', 'V2'))
        if not flags & 4:
            out.append(('V2', 'V3', 'V0'))
    return out


def _canopy_tris(flags):
    alt = bool(flags & 0xa)
    out = []
    if flags & 1 or flags & 2:
        out.append(('T3', 'T0', 'T1') if alt else ('T0', 'T1', 'T2'))
    if flags & 8 or flags & 4:
        out.append(('T1', 'T2', 'T3') if alt else ('T2', 'T3', 'T0'))
    return out


# (flag, vertex names, bitmap selector)  selector: A/B straight, C/D diagonal
_FACES = [(0x20, ('T1', 'T0', 'V0', 'V1'), 'A'),
          (0x40, ('T0', 'T3', 'V3', 'V0'), 'B'),
          (0x80, ('T3', 'T2', 'V2', 'V3'), 'A'),
          (0x10, ('T2', 'T1', 'V1', 'V2'), 'B')]
_DIAGS = [(1, ('T0', 'T2', 'V2', 'V0'), 'C'),
          (4, ('T2', 'T0', 'V0', 'V2'), 'C'),
          (2, ('T3', 'T1', 'V1', 'V3'), 'D'),
          (8, ('T1', 'T3', 'V3', 'V1'), 'D')]


def _bitmap_size(slot):
    return (128, 128) if slot in (SLOT_CANOPY, SLOT_CASTLE_TOP) else (128, 512)


def _uv_rect(w, h):
    a, bu, bv = 0.5, w + UV_FUDGE, h + UV_FUDGE
    return [(a, a), (bu, a), (bu, bv), (a, bv)]


def tile_corners(tiles, x, z):
    """Ground + top corner data exactly as RenderTile_ builds it.
    Returns {name: (X, y, Z, light)} for V0..V3 and T0..T3."""
    H, W = tiles.shape
    t = tiles[z, x]
    X, Z = x * TILE, z * TILE
    ex = x + 1 == W
    ez = z + 1 == H

    def hgt(tx, tz, missing):
        return 0 if missing else int(tiles[tz, tx]['height'])

    def top(tx, tz, missing):
        return 0 if missing else int(tiles[tz, tx]['unk10'])

    lt = t['light']
    v = {
        'V0': (X, hgt(x, z + 1, ez), Z + TILE, int(lt[1])),
        'V1': (X + TILE, hgt(x + 1, z + 1, ex or ez), Z + TILE, int(lt[4])),
        'V2': (X + TILE, hgt(x + 1, z, ex), Z, int(lt[2])),
        'V3': (X, int(t['height']), Z, int(lt[0])),
    }
    avg = (v['V0'][3] + v['V1'][3] + v['V2'][3] + v['V3'][3]) >> 2
    tops = {'T0': top(x, z + 1, ez), 'T1': top(x + 1, z + 1, ex or ez),
            'T2': top(x + 1, z, ex), 'T3': int(t['unk10'])}
    for k, vn in (('T0', 'V0'), ('T1', 'V1'), ('T2', 'V2'), ('T3', 'V3')):
        gx, gy, gz, _ = v[vn]
        v[k] = (gx, gy + tops[k], gz, avg)
    return v


def wall_mesh(terrain_map, gamedir=None, spoke=0, region=None, canopy=True, walls=True):
    """All wall / canopy polygons of a terrain map (or of region =
    (x0, z0, x1, z1) tile range, end exclusive).  gamedir is not needed
    (texture names are fixed) and accepted for API symmetry."""
    tiles = terrain_map.tiles
    H, W = tiles.shape
    x0, z0, x1, z1 = region if region else (0, 0, W, H)
    names = slot_names(spoke)
    flags_a = tiles['flags']
    wt_a = tiles['walltype'] & 0x7f
    zs, xs = np.nonzero(flags_a[z0:z1, x0:x1])
    polys = []
    for zz, xx in zip(zs.tolist(), xs.tolist()):
        x, z = xx + x0, zz + z0
        fl = int(flags_a[z, x])
        castle = (int(wt_a[z, x]) & 3) == 2
        c = tile_corners(tiles, x, z)
        if canopy:
            slot = SLOT_CASTLE_TOP if castle else SLOT_CANOPY
            w, h = _bitmap_size(slot)
            uvr = _uv_rect(w, h)
            uvmap = {'T0': uvr[0], 'T1': uvr[1], 'T2': uvr[2], 'T3': uvr[3]}
            for tri in _canopy_tris(fl):
                polys.append(_poly('canopy', (x, z), fl & 0xf, slot, names, spoke,
                                   [c[n] for n in tri], [uvmap[n] for n in tri],
                                   (0.0, 1.0, 0.0), w, h))
        if not walls:
            continue
        par = (x + z) & 1
        sel = {'A': par, 'B': par ^ 1, 'C': 2 + par, 'D': 2 + (par ^ 1)}
        faces = list(_FACES)
        if (fl & 0xf) not in (0, 0xf):
            faces += _DIAGS
        for bit, vn, s in faces:
            if not fl & bit:
                continue
            slot = SLOT_CASTLE_WALL if castle else sel[s]
            w, h = _bitmap_size(slot)
            polys.append(_poly('diag' if bit < 0x10 else 'wall', (x, z), bit, slot, names,
                               spoke, [c[n] for n in vn], _uv_rect(w, h),
                               FACE_NORMAL[bit], w, h))
    return polys


def _poly(kind, tile, flag, slot, names, spoke, corners, uvt, normal, w, h):
    return dict(kind=kind, tile=tile, flag=flag, slot=slot, texture=names[slot],
                palette=slot_palette(slot, spoke),
                verts=[(float(a), float(b), float(cc)) for a, b, cc, _ in corners],
                light=[int(l) for *_, l in corners],
                uv_texel=list(uvt), uv=[(u / w, v / h) for u, v in uvt],
                normal=normal, masked=False)


def to_triangles(polys):
    """Fan-triangulate polygons -> list of (poly, (i0, i1, i2))."""
    out = []
    for p in polys:
        for k in range(1, len(p['verts']) - 1):
            out.append((p, (0, k, k + 1)))
    return out


# Textures ----------------------------------------------------------------------

def _load_bmp_indices(path):
    from PIL import Image
    im = Image.open(path)
    if im.mode != 'P':
        raise ValueError('%s is not an 8-bit BMP' % path)
    return np.array(im)          # top-down, like Bitmap_LoadFromBMP_


def wall_textures(gamedir, spoke, shade=31, gamma=0, indexed=False):
    """{name: RGBA uint8 (h, w, 4)} for the 7 wall bitmaps of a spoke, coloured
    with the p16 shade row the engine uses (BMP palette ignored).  Alpha is
    255 everywhere: walls/canopy have no colour key.  indexed=True returns
    (indices, p16 table) instead.  gamma = _g_GammaVal (0 = off)."""
    out = {}
    for slot, name in enumerate(slot_names(spoke)):
        idx = _load_bmp_indices(T.find_ci(gamedir, 'tiles/' + name))
        pal = T.Pal16(open(T.find_ci(gamedir, 'tiles/' + slot_palette(slot, spoke)), 'rb').read())
        if indexed:
            out[name] = (idx, pal.table)
            continue
        rgb = T.rgb565_to_rgb888(pal.table[shade]).astype(np.int32)
        if gamma:
            rgb = np.minimum(255, rgb + np.minimum(rgb * gamma // 128, gamma))
        img = np.empty(idx.shape + (4,), np.uint8)
        img[..., :3] = rgb[idx]
        img[..., 3] = 255
        out[name] = img
    return out


# Foliage -------------------------------------------------------------------------

def foliage_table(spoke):
    return FOLIAGE_TABLE[11 if spoke == 11 else 'default']


def random_sequence(seed, n, count):
    """Random_ (random.ASM 0x44cc76): seed *= 0x1df5e0d; ((seed>>16)*n)>>16."""
    s = seed & 0xffffffff
    out = []
    for _ in range(count):
        s = (s * 0x1df5e0d) & 0xffffffff
        out.append(((s >> 16) * n) >> 16)
    return out, s


def foliage_choice(spoke, record_index, record_id=None, mode='id', seed=None):
    """D6PROP record of the tree placed by .FOL record `record_index`
    (1-based file record number, header = 0).
    mode='random': exact LoadStaticObjects_ emulation for a given start seed
      (the game seeds from the clock, so the real choice is not reproducible;
      Random_ is called once per record, whatever its kind).
    mode='id': editor convention table[record_id % n] (stable preview; the
      game ignores the id field)."""
    tab = foliage_table(spoke)
    if mode == 'random':
        if seed is None:
            raise ValueError('mode=random needs seed')
        seq, _ = random_sequence(seed, len(tab), record_index)
        return tab[seq[-1]]
    return tab[(record_id or 0) % len(tab)]


def foliage_props(gamedir, spoke, mode='id', seed=None):
    """Trees of SPOKEnn.FOL as dicts: index, pos, rot, prop, model.
    y == 0 means 'snap to terrain' (LoadPropObject_ -> D6_AdjustBase_)."""
    rf = T.RecordFile(open(T.find_ci(gamedir, 'SPOKE%02d.FOL' % spoke), 'rb').read())
    tab = foliage_table(spoke)
    seq = random_sequence(seed, len(tab), len(rf.records))[0] if mode == 'random' else None
    out = []
    for i, r in enumerate(rf.records):
        if r.kind != 'F':
            continue
        prop = tab[seq[i]] if seq is not None else tab[(r.id or 0) % len(tab)]
        out.append(dict(index=i + 1, pos=r.pos, rot=r.rot, prop=prop,
                        model=FOLIAGE_MODELS.get(prop), id=r.id))
    return out


def terrain_height(tiles, x, z):
    """Ground height at world (x, z) using the tile's two triangles."""
    H, W = tiles.shape
    tx, tz = int(x // TILE), int(z // TILE)
    if not (0 <= tx < W and 0 <= tz < H):
        return 0.0
    c = tile_corners(tiles, tx, tz)
    fx, fz = x / TILE - tx, z / TILE - tz
    h00, h10 = c['V3'][1], c['V2'][1]
    h01, h11 = c['V0'][1], c['V1'][1]
    if int(tiles[tz, tx]['flags']) & 0xa:      # split V3-V1
        if fz >= fx:
            return h00 + fz * (h01 - h00) + fx * (h11 - h01)
        return h00 + fx * (h10 - h00) + fz * (h11 - h10)
    if fx + fz <= 1:                            # split V0-V2
        return h00 + fx * (h10 - h00) + fz * (h01 - h00)
    return h11 + (1 - fx) * (h01 - h11) + (1 - fz) * (h10 - h11)


# Preview renderer ---------------------------------------------------------------

def _raster(img, zbuf, tri_s, tri_uv, tri_l, tex, pal):
    """Affine textured triangle with z test.  tri_s (3,3) screen x,y,depth;
    tri_uv (3,2) texel coords; tri_l (3,) shade; tex (h,w) indices;
    pal (32,256,3) uint8."""
    H, W = zbuf.shape
    xs, ys = tri_s[:, 0], tri_s[:, 1]
    x0, x1 = max(int(np.floor(xs.min())), 0), min(int(np.ceil(xs.max())), W - 1)
    y0, y1 = max(int(np.floor(ys.min())), 0), min(int(np.ceil(ys.max())), H - 1)
    if x0 > x1 or y0 > y1:
        return
    (ax, ay), (bx, by), (cx, cy) = tri_s[0, :2], tri_s[1, :2], tri_s[2, :2]
    den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(den) < 1e-9:
        return
    gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
    w0 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
    w1 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
    w2 = 1 - w0 - w1
    m = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
    if not m.any():
        return
    d = w0 * tri_s[0, 2] + w1 * tri_s[1, 2] + w2 * tri_s[2, 2]
    zb = zbuf[y0:y1 + 1, x0:x1 + 1]
    m &= d < zb
    if not m.any():
        return
    u = w0 * tri_uv[0, 0] + w1 * tri_uv[1, 0] + w2 * tri_uv[2, 0]
    v = w0 * tri_uv[0, 1] + w1 * tri_uv[1, 1] + w2 * tri_uv[2, 1]
    l = w0 * tri_l[0] + w1 * tri_l[1] + w2 * tri_l[2]
    th, tw = tex.shape
    ui = np.clip(u.astype(np.int64), 0, tw - 1)
    vi = np.clip(v.astype(np.int64), 0, th - 1)
    li = np.clip(np.rint(l).astype(np.int64), 0, 31)
    col = pal[li, tex[vi, ui]]
    sub = img[y0:y1 + 1, x0:x1 + 1]
    sub[m] = col[m]
    zb[m] = d[m]


def render_walls(gamedir, spoke, region, out_png, yaw=30.0, pitch=40.0, scale=0.06,
                 ground=True, foliage=True, light=True):
    """Oblique orthographic preview of region (x0, z0, x1, z1) tiles: ground
    tiles, walls, canopy (single-sided like the game) and foliage markers."""
    from PIL import Image, ImageDraw
    tm = T.TerrainMap(open(T.find_ci(gamedir, 'SPOKE%02d.TMR' % spoke), 'rb').read())
    tiles = tm.tiles
    x0, z0, x1, z1 = region
    cy, sy = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
    cp, sp = np.cos(np.radians(pitch)), np.sin(np.radians(pitch))
    cxw, czw = (x0 + x1) * TILE / 2, (z0 + z1) * TILE / 2

    # view: rotate about y by yaw, then tilt by pitch; camera looks along +depth
    def view(p):
        p = np.asarray(p, float)
        x, y, z = p[..., 0] - cxw, p[..., 1], p[..., 2] - czw
        xr = cy * x - sy * z
        zr = sy * x + cy * z
        sx = xr
        syy = -(y * cp - zr * sp)          # screen y down; +z goes up-screen
        dep = -(zr * cp + y * sp)          # smaller = nearer
        return np.stack([sx, syy, dep], -1)
    # camera direction (from scene toward camera) in world space
    # depth = -(cp*(sy*x + cy*z) + sp*y): the camera lies along -grad(depth)
    cam_dir = np.array([cp * sy, sp, cp * cy])
    corners = [(x0 * TILE, 0, z0 * TILE), (x1 * TILE, 0, z0 * TILE),
               (x0 * TILE, 0, z1 * TILE), (x1 * TILE, 0, z1 * TILE),
               (x0 * TILE, 12000, z0 * TILE), (x1 * TILE, 12000, z1 * TILE),
               (x1 * TILE, 12000, z0 * TILE), (x0 * TILE, 12000, z1 * TILE)]
    vc = view(corners)
    hmin = tiles['height'][z0:z1 + 1, x0:x1 + 1].min()
    hmax = tiles['height'][z0:z1 + 1, x0:x1 + 1].max() + 4096
    vc = np.concatenate([vc, view([(c[0], hmin, c[2]) for c in corners[:4]]),
                         view([(c[0], hmax, c[2]) for c in corners[:4]])])
    mn, mx = vc[:, :2].min(0), vc[:, :2].max(0)
    Wd, Hd = int((mx[0] - mn[0]) * scale) + 8, int((mx[1] - mn[1]) * scale) + 8

    def scr(p):
        v = view(p)
        v[..., 0] = (v[..., 0] - mn[0]) * scale + 4
        v[..., 1] = (v[..., 1] - mn[1]) * scale + 4
        return v
    img = np.zeros((Hd, Wd, 3), np.uint8)
    img[:] = (90, 120, 160)
    zbuf = np.full((Hd, Wd), np.inf)
    lightfn = (lambda l: l) if light else (lambda l: 31)
    # ground
    if ground:
        tex = T.load_tile_textures(gamedir, tm, 0)
        pals = {}
        for slot, rel in enumerate(T.TILE_PALETTES[:6]):
            p = T.Pal16(open(T.find_ci(gamedir, rel), 'rb').read()).table
            pals[slot] = T.rgb565_to_rgb888(p)
        for z in range(z0, z1):
            for x in range(x0, x1):
                t = tiles[z, x]
                if not t['tex'] or int(t['tex']) not in tex:
                    continue
                slot, tx = tex[int(t['tex'])]
                c = tile_corners(tiles, x, z)
                uvs = {'V0': (0, 127.9), 'V1': (127.9, 127.9), 'V2': (127.9, 0), 'V3': (0, 0)}
                for tri in ground_triangles(int(t['flags'])):
                    P = np.array([c[n][:3] for n in tri], float)
                    _raster(img, zbuf, scr(P), np.array([uvs[n] for n in tri], float),
                            np.array([lightfn(c[n][3]) for n in tri], float), tx, pals[slot])
    # walls + canopy
    texs = wall_textures(gamedir, spoke, indexed=True)
    wpal = {n: T.rgb565_to_rgb888(p) for n, (i, p) in texs.items()}
    polys = wall_mesh(tm, gamedir, spoke, region)
    for p in polys:
        n = np.array(p['normal'], float)
        if p['kind'] != 'canopy' and np.dot(n, cam_dir) <= 0:
            continue          # single sided, like the game's plane tests
        idx = texs[p['texture']][0]
        P = np.array(p['verts'], float)
        S = scr(P)
        uv = np.array(p['uv_texel'], float)
        L = np.array([lightfn(l) for l in p['light']], float)
        for a, b, c in ((0, 1, 2), (0, 2, 3))[:len(P) - 2]:
            k = [a, b, c]
            _raster(img, zbuf, S[k], uv[k], L[k], idx, wpal[p['texture']])
    im = Image.fromarray(img)
    if foliage:
        dr = ImageDraw.Draw(im)
        colors = {1: (255, 64, 64), 2: (255, 160, 0), 3: (255, 255, 0), 4: (255, 0, 255),
                  0xe7: (0, 255, 255), 0xe4: (255, 64, 64), 0xe5: (255, 160, 0),
                  0xe6: (255, 255, 0)}
        for f in foliage_props(gamedir, spoke):
            x, y, z = f['pos']
            if not (x0 * TILE <= x < x1 * TILE and z0 * TILE <= z < z1 * TILE):
                continue
            y = terrain_height(tiles, x, z) if y == 0 else y
            s = scr(np.array([x, y, z]))
            d = zbuf[int(np.clip(s[1], 0, Hd - 1)), int(np.clip(s[0], 0, Wd - 1))]
            r = 3 if s[2] <= d + 50 else 2
            dr.ellipse([s[0] - r, s[1] - r, s[0] + r, s[1] + r],
                       outline=colors.get(f['prop'], (255, 255, 255)))
    im.save(out_png)
    return out_png, len(polys)


def selftest(gamedir):
    ok = True
    for spoke in (0, 3, 11):
        tm = T.TerrainMap(open(T.find_ci(gamedir, 'SPOKE%02d.TMR' % spoke), 'rb').read())
        polys = wall_mesh(tm, gamedir, spoke)
        kinds = {}
        for p in polys:
            kinds[p['kind']] = kinds.get(p['kind'], 0) + 1
        texs = wall_textures(gamedir, spoke)
        missing = {p['texture'] for p in polys} - set(texs)
        print('spoke %2d: %s, textures %d, missing %s' % (spoke, kinds, len(texs), missing or '-'))
        ok &= not missing
    # Random_ check: tables
    seq, _ = random_sequence(12345, 5, 8)
    ok &= all(0 <= v < 5 for v in seq)
    print('selftest', 'OK' if ok else 'FAIL')
    return ok


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--selftest':
        sys.exit(0 if selftest(sys.argv[2]) else 1)
    if len(sys.argv) >= 9 and sys.argv[1] == '--render':
        g, sp = sys.argv[2], int(sys.argv[3])
        reg = tuple(int(v) for v in sys.argv[4:8])
        print(render_walls(g, sp, reg, sys.argv[8]))
        sys.exit(0)
    print(__doc__)
    print('usage: d6walls.py --selftest GAMEDIR | --render GAMEDIR SPOKE X0 Z0 X1 Z1 OUT.png')
