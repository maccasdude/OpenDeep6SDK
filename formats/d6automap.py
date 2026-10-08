#!/usr/bin/env python3
"""
d6automap.py - regenerate the automap images (maps/lm0b<spoke>.lm) after
terrain or level edits.

The shipped automap pages were painted by hand. This module renders new
pages in the same style and keeps each level's palette, bounding box and
"can be revealed" mask layout (docs/formats/terrain.md section 8):

* BSP levels (dungeons; header field bsp = slot): the floors of that BSP
  whose height lies in the level's height band, seen from above, textured
  with the level textures, tinted to the colours of the old page, with a
  dark outline, on a parchment background taken from the old page.
* Terrain levels (bsp = -1): the tile textures seen from above with hill
  shading, tree walls as forest, castle walls as stone, water, and the
  floors of the BSPs placed in the terrain; colours matched to the old page.

Then every page is quantised to the level's own 256 colour palette. Levels
with other bsp values (-5, -7: special origins) are left alone.

CLI
  d6automap.py GAMEDIR SPOKE [--out FILE.lm] [--preview DIR] [--levels 0,2]
  (without --out the game's file is replaced, the old one kept as .lm.bak)
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import d6level    # noqa: E402
import d6terrain  # noqa: E402


def _rgb565(pal_bytes):
    return d6terrain.rgb565_to_rgb888(np.frombuffer(pal_bytes, '<u2'))


def _noise(h, w, seed=1, scales=(64, 16, 4)):
    rng = np.random.default_rng(seed)
    from PIL import Image
    out = np.zeros((h, w), np.float32)
    amp = 1.0
    for s in scales:
        g = rng.random((max(2, h // s + 2), max(2, w // s + 2))).astype(np.float32)
        out += amp * (np.array(Image.fromarray(g).resize((w, h), Image.BICUBIC)) - 0.5)
        amp *= 0.5
    return out


def _quantize(rgb, pal):
    """Nearest palette colour (pal: [256,3])."""
    flat = rgb.reshape(-1, 3).astype(np.int32)
    p = pal.astype(np.int32)
    out = np.empty(len(flat), np.uint8)
    for i in range(0, len(flat), 65536):
        d = ((flat[i:i + 65536, None, :] - p[None]) ** 2).sum(-1)
        out[i:i + 65536] = d.argmin(1)
    return out.reshape(rgb.shape[:2])


def _match(src, ref, mask_src=None, mask_ref=None):
    """Per channel mean/std transfer of src towards ref."""
    s = src.astype(np.float32)
    out = s.copy()
    for c in range(3):
        a = s[..., c][mask_src] if mask_src is not None else s[..., c]
        b = ref[..., c][mask_ref].astype(np.float32) if mask_ref is not None else ref[..., c].astype(np.float32)
        if a.size < 16 or b.size < 16:
            continue
        out[..., c] = (s[..., c] - a.mean()) * (b.std() / max(1.0, a.std())) + b.mean()
    return np.clip(out, 0, 255)


def _pages_from_image(idx, lv):
    pages = []
    for i in range(lv.pages_w * lv.pages_h):
        py, px = divmod(i, lv.pages_w)
        pages.append(np.ascontiguousarray(idx[py * 128:(py + 1) * 128, px * 128:(px + 1) * 128]).tobytes())
    return pages


def _mask_image(lv):
    m = np.zeros((lv.pages_h * 128, lv.pages_w * 128), np.uint8)
    for i in range(lv.pages_w * lv.pages_h):
        py, px = divmod(i, lv.pages_w)
        m[py * 128:(py + 1) * 128, px * 128:(px + 1) * 128] = \
            np.unpackbits(np.frombuffer(lv.masks[i], np.uint8)).reshape(128, 128)
    return m.astype(bool)


class _Frame(object):
    """world x/z <-> page pixels of one level."""

    def __init__(self, lv):
        self.W, self.H = lv.pages_w * 128, lv.pages_h * 128
        self.x0, self.x1 = lv.bbox_min[0], lv.bbox_max[0]
        self.z0, self.z1 = lv.bbox_min[2], lv.bbox_max[2]

    def px(self, x, z):
        return ((np.asarray(x) - self.x0) * self.W / (self.x1 - self.x0),
                (self.z1 - np.asarray(z)) * self.H / (self.z1 - self.z0))

    def world(self):
        xs = self.x0 + (np.arange(self.W) + 0.5) * (self.x1 - self.x0) / self.W
        zs = self.z1 - (np.arange(self.H) + 0.5) * (self.z1 - self.z0) / self.H
        return np.meshgrid(xs, zs)


def _texture_means(wad):
    """Mean colour and 16x16 mip (RGB) per .twd texture."""
    out = []
    for i in range(len(wad.textures)):
        w, h, raw = wad.texture_rgb(i, mip=3)
        a = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
        out.append(a)
    return out


GROUND_SLACK = 512.0     # world units a BSP floor may lie below the terrain and still be drawn


def _draw_floors(fr, bsp, wad, origin, yband, img, cover, scale=16.0, local=True, ground=None):
    """Rasterise upward faces of the BSP world model into img (float RGB) /
    cover (bool). Coordinates: local BSP units * 16 (+ origin if not local).
    ground: optional per-pixel terrain height (world units); floor pixels
    more than GROUND_SLACK below it (caves under hills) are not drawn."""
    from PIL import Image, ImageDraw
    texs = _texture_means(wad) if wad is not None else []
    m = bsp.models[0]
    W, H = fr.W, fr.H
    order = []
    for fi in range(m.firstface, m.firstface + m.numfaces):
        f = bsp.faces[fi]
        p = bsp.planes[f.planenum]
        ny = -p.ny if f.side else p.ny
        if ny < 0.6:
            continue
        ti = bsp.texinfo[f.texinfo]
        if ti.flags == -1:
            continue
        pts = bsp.face_points(f)
        ys = [q[1] * scale + (0 if local else origin[1]) for q in pts]
        y = sum(ys) / len(ys)
        if yband is not None and not (yband[0] <= y < yband[1]):
            continue
        order.append((y, fi, pts, ti))
    order.sort(key=lambda t: t[0])          # higher floors drawn last
    for y, fi, pts, ti in order:
        wx = [q[0] * scale + (0 if local else origin[0]) for q in pts]
        wz = [q[2] * scale + (0 if local else origin[2]) for q in pts]
        X, Z = fr.px(wx, wz)
        x0, x1 = int(max(0, np.floor(min(X)))), int(min(W - 1, np.ceil(max(X))))
        z0, z1 = int(max(0, np.floor(min(Z)))), int(min(H - 1, np.ceil(max(Z))))
        if x1 < x0 or z1 < z0:
            continue
        mk = Image.new('L', (x1 - x0 + 1, z1 - z0 + 1), 0)
        ImageDraw.Draw(mk).polygon([(a - x0, b - z0) for a, b in zip(X, Z)], fill=255, outline=255)
        mk = np.array(mk) > 0
        if ground is not None:
            mk &= y >= ground[z0:z1 + 1, x0:x1 + 1] - GROUND_SLACK
        if not mk.any():
            continue
        tid = ti.flags & 0xfff
        if ti.flags & 0x8000 or tid >= len(texs):
            col = np.zeros(mk.shape + (3,), np.float32)
            col[:] = (40, 70, 110) if ti.flags & 0x8000 else (90, 90, 90)
        else:
            # texture lookup through the face projection (u = ((P-o).s + s.w/4)/32)
            gx, gz = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(z0, z1 + 1) + 0.5)
            wxp = fr.x0 + gx * (fr.x1 - fr.x0) / W
            wzp = fr.z1 - gz * (fr.z1 - fr.z0) / H
            bx = (wxp - (0 if local else origin[0])) / scale
            bz = (wzp - (0 if local else origin[2])) / scale
            u = ((bx - ti.ox) * ti.sx + (y / scale - ti.oy) * ti.sy + (bz - ti.oz) * ti.sz + ti.soff * 0.25) / 32.0
            v = ((bx - ti.ox) * ti.tx + (y / scale - ti.oy) * ti.ty + (bz - ti.oz) * ti.tz + ti.toff * 0.25) / 32.0
            t = texs[tid]
            th, tw = t.shape[:2]
            col = t[(np.floor(v * th).astype(int) % th), (np.floor(u * tw).astype(int) % tw)].astype(np.float32)
        sl = (slice(z0, z1 + 1), slice(x0, x1 + 1))
        img[sl][mk] = col[mk]
        cover[sl] |= mk


def _dilate(m, n):
    try:
        from scipy import ndimage
        return ndimage.binary_dilation(m, iterations=n)
    except ImportError:
        out = m.copy()
        for _ in range(n):
            o = out.copy()
            o[1:] |= out[:-1]
            o[:-1] |= out[1:]
            o[:, 1:] |= out[:, :-1]
            o[:, :-1] |= out[:, 1:]
            out = o
        return out


def render_bsp_level(game, spoke, lv, old_rgb):
    """New RGB page mosaic for a dungeon level (lv.bsp = slot)."""
    places = d6level.spoke_bsps(spoke, game)
    inst = [p for p in places if p['slot'] == lv.bsp]
    if not inst:
        return None
    name = inst[0]['name']
    bsp = d6level.BSPFile.load(d6level.find_file(game, name + '.bsp'))
    tp = d6level.find_file(game, name + '.twd')
    wad = d6level.TexWad.load(tp) if tp else None
    fr = _Frame(lv)
    img = np.zeros((fr.H, fr.W, 3), np.float32)
    cover = np.zeros((fr.H, fr.W), bool)
    _draw_floors(fr, bsp, wad, None, (lv.bbox_min[1], lv.bbox_max[1]), img, cover)
    # old page: floors are the pixels that differ from the parchment
    old = old_rgb.astype(np.float32)
    bg = np.median(old.reshape(-1, 3), 0)
    olddiff = np.abs(old - bg).sum(-1) > 60
    # parchment: the old page where it was background, else its colour + noise
    par = np.where(olddiff[..., None], bg + _noise(fr.H, fr.W, 3)[..., None] * 40, old)
    if cover.any():
        tinted = _match(img, old_rgb, cover, olddiff if olddiff.sum() > 100 else None)
    else:
        tinted = img
    # a calmer, more uniform floor like the painted pages: half texture
    # detail, half the old floor colour
    if olddiff.sum() > 100:
        fmean = old[olddiff].mean(0)
        lum = tinted.mean(-1, keepdims=True) / max(1.0, tinted[cover].mean())
        tinted = 0.5 * tinted + 0.5 * fmean * lum
    out = par.copy()
    out[cover] = tinted[cover]
    black = old.sum(-1) < 30            # outside the map: stays black
    out[black & ~cover] = old[black & ~cover]
    edge = _dilate(cover, 2) & ~cover
    out[edge] = out[edge] * 0.25
    return np.clip(out, 0, 255).astype(np.uint8)


def render_terrain_level(game, spoke, lv, old_rgb, mask):
    """New RGB page mosaic for a terrain level (lv.bsp = -1)."""
    from PIL import Image
    base = 'SPOKE%02d' % spoke
    tm = d6terrain.TerrainMap(open(d6terrain.find_ci(game, base + '.TMR'), 'rb').read())
    T = tm.tiles
    Ht, Wt = T.shape
    fr = _Frame(lv)
    # tile texture mosaic at 4 px per tile (mip level 3 = 16 px, downsampled)
    px = 4
    tex = d6terrain.load_tile_textures(game, tm, 3)
    pals = [d6terrain.Pal16(open(d6terrain.find_ci(game, p), 'rb').read())
            for p in d6terrain.TILE_PALETTES[:6]]
    palrgb = [d6terrain.rgb565_to_rgb888(p.table)[31] for p in pals]
    cache = {}
    mos = np.zeros((Ht * px, Wt * px, 3), np.uint8)
    for z in range(Ht):
        for x in range(Wt):
            k = int(T['tex'][z, x])
            t = tex.get(k)
            if t is None:
                continue
            if k not in cache:
                rgb = palrgb[t[0]][t[1]]
                cache[k] = np.array(Image.fromarray(rgb).resize((px, px), Image.BOX))
            mos[z * px:(z + 1) * px, x * px:(x + 1) * px] = cache[k]
    # hill shade and baked light at tile resolution
    h = T['height'].astype(np.float32)
    gz, gx = np.gradient(h)
    shade = np.clip(0.85 + (-gx + gz) / 2500.0, 0.5, 1.25)
    light = np.clip(T['light'].mean(-1) / 31.0, 0.35, 1.0)
    walls = T['walltype'] & 0x7f
    water = T['water'] > 0
    # resample everything into the level frame (tile (x,z) covers world x*1024..)
    X, Z = fr.world()
    tx = X / 1024.0
    tz = Z / 1024.0
    inside = (tx >= 0) & (tx < Wt) & (tz >= 0) & (tz < Ht)
    txi = np.clip(tx, 0, Wt - 1e-3)
    tzi = np.clip(tz, 0, Ht - 1e-3)
    img = mos[(tzi * px).astype(int), (txi * px).astype(int)].astype(np.float32)

    def bil(a):
        return np.array(Image.fromarray(a.astype(np.float32)).resize((Wt * px, Ht * px), Image.BILINEAR))[
            (tzi * px).astype(int), (txi * px).astype(int)]
    img *= (bil(shade) * bil(light))[..., None]
    n = _noise(fr.H, fr.W, spoke + 7)
    forest = bil((walls == 1).astype(np.float32)) + n * 0.6 > 0.5
    castle = bil((walls == 2).astype(np.float32)) > 0.5
    wet = bil(water.astype(np.float32)) + n * 0.3 > 0.5
    g = (0.55 + n[..., None] * 0.9)
    img[forest] = (np.array([28, 48, 22]) * g)[forest]
    img[castle] = (np.array([110, 104, 96]) * (0.8 + n[..., None] * 0.6))[castle]
    img[wet] = img[wet] * 0.35 + np.array([40, 80, 90]) * 0.65
    # BSPs placed in this spoke: their floors, unless buried under the terrain
    cover = np.zeros((fr.H, fr.W), bool)
    ground = bil(h)
    for pl in d6level.spoke_bsps(spoke, game):
        bp = d6level.find_file(game, pl['name'] + '.bsp')
        if not bp:
            continue
        b = d6level.BSPFile.load(bp)
        tp = d6level.find_file(game, pl['name'] + '.twd')
        _draw_floors(fr, b, d6level.TexWad.load(tp) if tp else None, pl['origin'], None,
                     img, cover, local=False, ground=ground)
    edge = _dilate(cover, 1) & ~cover
    img[edge] *= 0.4
    sel = mask & inside
    out = _match(img, old_rgb, sel, sel)
    keep = ~inside | ~mask | (img.sum(-1) < 1)      # outside / never revealed / no tile
    out[keep] = old_rgb[keep]
    return np.clip(out, 0, 255).astype(np.uint8)


def regenerate(game, spoke, levels=None, log=print):
    """Returns (LevelMap with new pages, {level: rgb})."""
    path = d6terrain.find_ci(game, 'maps/lm0b%d.lm' % spoke)
    lm = d6terrain.LevelMap(open(path, 'rb').read())
    previews = {}
    for k, lv in enumerate(lm.levels):
        if levels is not None and k not in levels:
            continue
        old = lv.mosaic_rgb()
        mask = _mask_image(lv)
        if lv.bsp == -1:
            rgb = render_terrain_level(game, spoke, lv, old, mask)
        elif lv.bsp >= 0:
            rgb = render_bsp_level(game, spoke, lv, old)
        else:
            rgb = None
        if rgb is None:
            log('level %d (bsp %d): kept' % (k, lv.bsp))
            continue
        idx = _quantize(rgb, _rgb565(lv.palette))
        lv.images = _pages_from_image(idx, lv)
        previews[k] = _rgb565(lv.palette)[idx]
        log('level %d (bsp %d): %dx%d pages redrawn' % (k, lv.bsp, lv.pages_w, lv.pages_h))
    return lm, previews


def main(argv):
    a = argv[1:]
    if len(a) < 2:
        print(__doc__)
        return 1
    game, spoke = a[0], int(a[1])
    out = prev = None
    levels = None
    if '--out' in a:
        out = a[a.index('--out') + 1]
    if '--preview' in a:
        prev = a[a.index('--preview') + 1]
    if '--levels' in a:
        levels = [int(x) for x in a[a.index('--levels') + 1].split(',')]
    lm, previews = regenerate(game, spoke, levels)
    if prev:
        from PIL import Image
        os.makedirs(prev, exist_ok=True)
        for k, rgb in previews.items():
            Image.fromarray(rgb).save(os.path.join(prev, 'lm0b%d_level%d.png' % (spoke, k)))
    data = lm.serialize()
    if out is None:
        path = d6terrain.find_ci(game, 'maps/lm0b%d.lm' % spoke)
        if not os.path.exists(path + '.bak'):
            os.replace(path, path + '.bak')
        out = path
    with open(out + '.tmp', 'wb') as f:
        f.write(data)
    os.replace(out + '.tmp', out)
    print('wrote', out)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
