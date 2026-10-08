#!/usr/bin/env python3
"""
d6tilegen.py - make terrain tile textures for a new (or re-textured) terrain
type, including the transition tiles to its neighbour types.

A terrain tile's texture name is its four corner types (docs/formats/terrain.md):
char0 = corner (x+1,z), char1 = (x,z), char2 = (x,z+1), char3 = (x+1,z+1).
The game loads tiles/80x/<canonical rotation>.mip and rotates it, so only
canonical names are written. In the .mip (file orientation) row 0 is the +z
edge and column 0 the -x edge: char1 -> bottom-left, char0 -> bottom-right,
char2 -> top-left, char3 -> top-right.

Transitions are blended from the two base textures with a noisy corner mask
(bilinear corner weights + value noise, thresholded softly), quantised to the
palette of one tile directory (every tile of a directory shares its pal.p16;
only 801..806 exist) and mip-mapped exactly like tiles/termip.exe.

CLI
  d6tilegen.py GAMEDIR LETTER image.png PARTNER [PARTNER ...] [--dir 80x] [--repalette] [--preview out.png]
  --repalette: rebuild the palette of the directory (default 803, 9 tiles) to fit the new colours
"""
import itertools
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import d6terrain  # noqa: E402

DIRS = ('801', '802', '803', '804', '805', '806')


def canon(name):
    return d6terrain.TerrainMap.canonical_rotation(name.upper())[1]


def existing_tiles(gamedir):
    out = {}
    for d in DIRS:
        p = d6terrain.find_ci(gamedir, 'tiles/' + d)
        if not p:
            continue
        for f in os.listdir(p):
            if f.lower().endswith('.mip'):
                out.setdefault(f[:-4].upper(), (d, os.path.join(p, f)))
    return out


def dir_palette(gamedir, d):
    return d6terrain.tile_dir_palette_rgb(gamedir, d)


def tile_rgb(gamedir, name, tiles=None):
    """RGB (128,128,3) of a tile in file orientation of NAME (rotation applied)."""
    tiles = tiles or existing_tiles(gamedir)
    k, c = d6terrain.TerrainMap.canonical_rotation(name.upper())
    if c not in tiles:
        return None
    d, path = tiles[c]
    pal = d6terrain.Pal16(open(d6terrain.find_ci(gamedir, 'tiles/%s/pal.p16' % d), 'rb').read()).rgb(31)
    mip = d6terrain.TileMip(open(path, 'rb').read())
    return pal[np.rot90(mip.levels[0], k)]


def _noise(seed, size=128, octaves=(8, 16, 32)):
    rng = np.random.default_rng(seed)
    out = np.zeros((size, size))
    amp = 1.0
    for o in octaves:
        g = rng.random((o + 1, o + 1))
        y, x = np.mgrid[0:size, 0:size] / float(size) * o
        x0, y0 = x.astype(int), y.astype(int)
        fx, fy = x - x0, y - y0
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        v = (g[y0, x0] * (1 - fx) + g[y0, x0 + 1] * fx) * (1 - fy) + (g[y0 + 1, x0] * (1 - fx) + g[y0 + 1, x0 + 1] * fx) * fy
        out += (v - 0.5) * amp
        amp *= 0.5
    return out


def corner_mask(name, letter, seed=0, edge=0.18):
    """Weight (128,128) of `letter` for a tile named `name` (file orientation)."""
    c = [1.0 if ch == letter else 0.0 for ch in name]
    bl, br, tl, tr = c[1], c[0], c[2], c[3]
    t = np.linspace(0, 1, 128)
    fx = t[None, :]
    fy = 1 - t[:, None]                 # row 0 = top = +z
    w = (bl * (1 - fx) + br * fx) * (1 - fy) + (tl * (1 - fx) + tr * fx) * fy
    if min(c) == max(c):
        return w * np.ones((128, 128))
    # noise is tiled by world corner so neighbouring tiles roughly agree at the edges
    n = _noise(seed)
    # no noise at the tile border: neighbouring tiles then meet the same edge line
    d = np.minimum(np.minimum(fx, 1 - fx), np.minimum(fy, 1 - fy))
    n = n * np.clip(d * 5, 0, 1)
    v = w + n * 0.45
    return np.clip((v - (0.5 - edge)) / (2 * edge), 0, 1)


def make_tile(gamedir, name, letter, base_rgb, partner, partner_rgb, palette, seed=0):
    m = corner_mask(name, letter, seed)[..., None]
    img = base_rgb.astype(np.float64) * m + partner_rgb.astype(np.float64) * (1 - m)
    img = np.clip(img, 0, 255).astype(np.uint8)
    idx = d6terrain.quantize(img, palette)
    return img, idx


def best_dir(gamedir, rgb):
    """Tile directory whose palette reproduces the image best."""
    best = None
    sample = rgb[::4, ::4].reshape(-1, 3)
    for d in DIRS:
        try:
            pal = dir_palette(gamedir, d)
        except Exception:
            continue
        idx = d6terrain.quantize(sample, pal)
        err = float(((pal[idx].astype(np.int64) - sample) ** 2).sum(-1).mean())
        if best is None or err < best[0]:
            best = (err, d)
    return best[1], best[0]


def _write_new(path, data):
    """New file renamed over path (never writes through a hard link)."""
    with open(path + '.tmp', 'wb') as f:
        f.write(data)
    os.replace(path + '.tmp', path)


def repalette_dir(gamedir, tiledir, extra_rgb, write=True):
    """Give a tile directory a new 256-colour palette that also covers
    extra_rgb (list of RGB images): median cut over its existing tiles plus
    the new images, existing tiles re-quantised (slight colour change) and
    re-mipped. Use a directory with few tiles (803, 805)."""
    from PIL import Image
    p = d6terrain.find_ci(gamedir, 'tiles/' + tiledir)
    old_p16 = open(os.path.join(p, 'pal.p16'), 'rb').read()
    old_pal = d6terrain.Pal16(old_p16).rgb(31)
    files = sorted(f for f in os.listdir(p) if f.lower().endswith('.mip'))
    imgs = [old_pal[d6terrain.TileMip(open(os.path.join(p, f), 'rb').read()).levels[0]] for f in files]
    px = [i.reshape(-1, 3) for i in imgs] + [np.asarray(e, np.uint8)[..., :3].reshape(-1, 3) for e in extra_rgb]
    # the new images get as much weight as all old tiles together
    allpx = np.concatenate(px)
    q = Image.fromarray(allpx.reshape(1, -1, 3)).quantize(colors=256, method=Image.Quantize.MEDIANCUT,
                                                         dither=Image.Dither.NONE)
    pal = np.array(q.getpalette()[:768], np.uint8).reshape(-1, 3)
    pal = np.vstack([pal, np.zeros((256 - len(pal), 3), np.uint8)])
    p16 = d6terrain.make_p16(pal)
    pal31 = d6terrain.Pal16(p16).rgb(31)
    if write:
        import shutil
        bdir = os.path.join(gamedir, 'd6edit_backup', 'tiles_%s_%d' % (tiledir, int(__import__('time').time())))
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(os.path.join(p, 'pal.p16'), bdir)
        for f, img in zip(files, imgs):
            shutil.copy2(os.path.join(p, f), bdir)
            idx = d6terrain.quantize(img, pal31).astype(np.uint8)
            _write_new(os.path.join(p, f), d6terrain.TileMip.from_indexed(idx, p16).serialize())
        _write_new(os.path.join(p, 'pal.p16'), p16)
    return pal31, p16


def generate(gamedir, letter, base_rgb, partners, tiledir=None, write=True, seed=1, repalette=False):
    """Write tiles for type `letter`: the full tile and every corner combination
    with each partner type. Returns [(canonical name, dir, path, rgb image)]."""
    letter = letter.upper()
    if len(letter) != 1 or not letter.isalnum():
        raise ValueError('a terrain type is one letter or digit')
    from PIL import Image
    base = np.asarray(Image.fromarray(np.asarray(base_rgb, np.uint8)[..., :3]).resize((128, 128), Image.LANCZOS))
    tiles = existing_tiles(gamedir)
    if tiledir is None:
        tiledir = '803' if repalette else best_dir(gamedir, base)[0]
    if repalette:
        extra = [base] + [tile_rgb(gamedir, p.upper() * 4, tiles) for p in partners]
        pal, p16 = repalette_dir(gamedir, tiledir, [e for e in extra if e is not None], write=write)
    else:
        pal = dir_palette(gamedir, tiledir)
        p16 = open(d6terrain.find_ci(gamedir, 'tiles/%s/pal.p16' % tiledir), 'rb').read()
    out = []
    done = set()
    jobs = [(letter * 4, None)]
    for p in partners:
        p = p.upper()
        if p == letter:
            continue
        for combo in itertools.product((letter, p), repeat=4):
            n = ''.join(combo)
            if len(set(n)) == 2:
                jobs.append((n, p))
    for name, p in jobs:
        cn = canon(name)
        if cn in done:
            continue
        done.add(cn)
        if p is None:
            img = base
            idx = d6terrain.quantize(base, pal)
        else:
            prgb = tile_rgb(gamedir, p * 4, tiles)
            if prgb is None:
                raise ValueError('no tile %s in the game for partner type %s' % (p * 4, p))
            img, idx = make_tile(gamedir, cn, letter, base, p, prgb, pal, seed)
        tdir = tiledir
        if cn in tiles and tiles[cn][0] != tiledir:
            # the name exists in another folder: replace it there, in that folder's palette
            tdir = tiles[cn][0]
            opal = dir_palette(gamedir, tdir)
            op16 = open(d6terrain.find_ci(gamedir, 'tiles/%s/pal.p16' % tdir), 'rb').read()
            idx = d6terrain.quantize(img, opal)
            mip = d6terrain.TileMip.from_indexed(idx.astype(np.uint8), op16)
            out_pal = opal
        else:
            mip = d6terrain.TileMip.from_indexed(idx.astype(np.uint8), p16)
            out_pal = pal
        path = os.path.join(d6terrain.find_ci(gamedir, 'tiles/' + tdir), cn + '.mip')
        if write:
            with open(path + '.tmp', 'wb') as f:
                f.write(mip.serialize())
            os.replace(path + '.tmp', path)
        out.append((cn, tdir, path, out_pal[idx]))
    return out


def main(argv):
    a = argv[1:]
    if len(a) < 4:
        print(__doc__)
        return 1
    from PIL import Image
    g, letter, img = a[0], a[1], np.array(Image.open(a[2]).convert('RGB'))
    rest = a[3:]
    d = None
    prev = None
    if '--dir' in rest:
        i = rest.index('--dir')
        d = rest[i + 1]
        del rest[i:i + 2]
    rp = '--repalette' in rest
    if rp:
        rest.remove('--repalette')
    if '--preview' in rest:
        i = rest.index('--preview')
        prev = rest[i + 1]
        del rest[i:i + 2]
    res = generate(g, letter, img, rest, d, write=prev is None, repalette=rp)
    for n, dd, p, _ in res:
        print(dd, n, p)
    if prev:
        sheet = np.concatenate([r[3] for r in res], 1)
        Image.fromarray(sheet.astype(np.uint8)).save(prev)
        print('preview', prev)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
