"""
d6edit - terrain editing (spokes 0, 3, 11).

A .TMR map is a grid of tiles; every value below lives on the tile corners
(W x D grid, tile (x, z) spans corners x..x+1, z..z+1):

* height  - tiles['height'][z, x] is the height of corner (x, z)
* light   - the 6 light bytes of a tile are corner values in the order
            (x,z) (x,z+1) (x+1,z) (x,z+1) (x+1,z+1) (x+1,z)  (verified: every
            shared corner has the same value in all shipped maps), so the
            editor keeps one light value per corner
* type    - terrain type letter per corner. A tile's BLT texture name is its
            four corner types: name = C[z,x+1] C[z,x] C[z+1,x] C[z+1,x+1]
            (docs/formats/terrain.md); a texture exists for a name when
            tiles/80x/<canonical rotation>.mip exists.

Painting types picks / creates the BLT entry for every touched tile, so
transitions come out right as long as the game has a tile for them.

Baked light model (fitted against the shipped maps, mean error < 1 shade):
light = clamp(ambient + k * sum over .LIT lights of max(0, 1 - d/R), ambient, 31)
with d the horizontal distance; ambient 10 (spokes 0, 3) or 24 (spoke 11),
R ~ 10000, k ~ 18. relight() recomputes it; fit_light() fits k and R.
"""
import os

import numpy as np

import d6terrain

TILE = 1024.0
DIRS = ('801', '802', '803', '804', '805', '806')
WATER_LEVEL = {1: 0.0, 2: 0.0, 3: 7936.0, 4: 7936.0, 5: 5120.0, 6: 5120.0}


def canon(name):
    return d6terrain.TerrainMap.canonical_rotation(name.upper())


class TileSet(object):
    """The tile textures the game ships (tiles/80x/*.mip), by canonical name."""

    def __init__(self, root):
        self.root = root
        self.files = {}           # canonical upper name -> (dir, path)
        for d in DIRS:
            p = d6terrain.find_ci(root, 'tiles/' + d)
            if not p:
                continue
            for f in sorted(os.listdir(p)):
                if f.lower().endswith('.mip'):
                    self.files.setdefault(f[:-4].upper(), (d, os.path.join(p, f)))
        self._pal = {}
        self._img = {}
        self.types = sorted(set(c for n in self.files for c in n))

    def has(self, name):
        return canon(name)[1] in self.files

    def image(self, name, brighten=1.6):
        """RGB (128,128,3) of a tile name in game orientation (rotation applied)."""
        k, c = canon(name)
        key = (c, k)
        if key not in self._img:
            if c not in self.files:
                return None
            d, path = self.files[c]
            if d not in self._pal:
                self._pal[d] = d6terrain.Pal16(open(d6terrain.find_ci(self.root, 'tiles/%s/pal.p16' % d), 'rb').read()).rgb(31)
            mip = d6terrain.TileMip(open(path, 'rb').read())
            img = self._pal[d][np.rot90(mip.levels[0], k)]
            self._img[key] = np.clip(img.astype(np.float32) * brighten, 0, 255).astype(np.uint8)
        return self._img[key]


class Terrain(object):
    def __init__(self, spoke, tmr, tileset):
        self.spoke = spoke
        self.map = tmr
        self.t = tmr.tiles
        self.tileset = tileset
        self.W, self.D = tmr.width, tmr.height
        self.blt_layer = np.zeros(1024, np.int32)     # texture-array layer per BLT entry (0 = none)
        self.corner_types = self._corner_types()
        a = self.t['light'][:, :, 0]
        used = self.t['tex'] > 0
        self.ambient = int(a[used].min()) if used.any() else 10
        self.light_k, self.light_r = (25.0, 9000.0) if self.ambient >= 20 else (18.0, 10000.0)

    # ------------------------------------------------------------ textures
    def layer_for(self, i):
        """Texture layer of BLT entry i (created on first use)."""
        if i <= 0:
            return -1
        if self.blt_layer[i]:
            return int(self.blt_layer[i])
        name = self.map.blt_name(i)
        if not name or name[0] in 'Xx' or (ord(name[3]) & 0x80):
            return -1
        img = self.tileset.image(name)
        if img is None:
            return -1
        k, c = canon(name)
        self.blt_layer[i] = self.spoke.texlib.add(('tile', c, k), np.flipud(img))
        return int(self.blt_layer[i])

    def blt_index(self, name, create=True):
        """BLT entry for a 4-letter name (existing one reused), None if the game
        has no texture for it or the table is full."""
        u = name.upper()
        for i in range(1, 1024):
            n = self.map.blt[i]
            if n[:1] and not (n[3] & 0x80) and n.decode('latin1').upper() == u:
                return i
        if not create or not self.tileset.has(u):
            return None
        free = [i for i in range(1, 1024) if not self.map.blt[i][:1]]
        if not free:     # reuse an editor placeholder (bit 7 of char 3; the engine skips those)
            in_use = set(np.unique(self.t['tex']).tolist())
            free = [i for i in range(1, 1024) if (self.map.blt[i][3] & 0x80) and i not in in_use]
        if not free:
            return None
        i = free[0]
        self.map.blt[i] = u.encode('latin1')
        self.blt_layer[i] = 0
        return i

    # ------------------------------------------------------------ corner types
    def _corner_types(self):
        D, W = self.D, self.W
        C = np.full((D, W), '?', dtype='<U1')
        tex = self.t['tex']
        zs, xs = np.nonzero(tex[:-1, :-1] > 0)
        names = {}
        for i in np.unique(tex).tolist():
            if i:
                n = self.map.blt_name(i)
                names[i] = n.upper() if n and len(n) == 4 else None
        # name = C[z,x+1] C[z,x] C[z+1,x] C[z+1,x+1]
        for z, x in zip(zs.tolist(), xs.tolist()):
            n = names.get(int(tex[z, x]))
            if not n:
                continue
            C[z, x + 1], C[z, x], C[z + 1, x], C[z + 1, x + 1] = n[0], n[1], n[2], n[3]
        return C

    def tile_name(self, x, z):
        C = self.corner_types
        return C[z, x + 1] + C[z, x] + C[z + 1, x] + C[z + 1, x + 1]

    def paint_types(self, cx, cz, radius, letter):
        """Set the type of the corners within radius (world units) of (cx, cz)
        and retexture the touched tiles. Returns (rect, nfailed): tiles whose
        corner combination has no texture in the game keep their old corners."""
        C = self.corner_types
        x0, x1, z0, z1 = self._span(cx, cz, radius)
        zz, xx = np.mgrid[z0:z1 + 1, x0:x1 + 1]
        inside = (xx * TILE - cx) ** 2 + (zz * TILE - cz) ** 2 <= radius * radius
        if not inside.any():          # small brush: nearest corner
            inside[np.unravel_index(np.argmin((xx * TILE - cx) ** 2 + (zz * TILE - cz) ** 2), inside.shape)] = True
        old = C.copy()
        sub = C[z0:z1 + 1, x0:x1 + 1]
        sub[inside] = letter
        failed = set()
        for it in range(8):
            bad = []
            for z in range(max(0, z0 - 1), min(self.D - 1, z1 + 1)):
                for x in range(max(0, x0 - 1), min(self.W - 1, x1 + 1)):
                    if self.t['tex'][z, x] == 0:
                        continue
                    n = self.tile_name(x, z)
                    if '?' in n:
                        continue
                    if n == self._name_of(x, z):
                        continue
                    i = self.blt_index(n)
                    if i is None:
                        bad.append((x, z))
                    else:
                        self.t['tex'][z, x] = i
            if not bad:
                break
            for x, z in bad:      # undo the corners of tiles that have no texture
                failed.add((x, z))
                for dz in (0, 1):
                    for dx in (0, 1):
                        C[z + dz, x + dx] = old[z + dz, x + dx]
        return (max(0, x0 - 1), max(0, z0 - 1), min(self.W - 1, x1 + 1), min(self.D - 1, z1 + 1)), len(failed)

    def _name_of(self, x, z):
        n = self.map.blt_name(int(self.t['tex'][z, x]))
        return n.upper() if n else ''

    def fill_type_info(self):
        """{letter: example full tile name} for the palette."""
        out = {}
        for c in self.tileset.types:
            if c.isalnum() and self.tileset.has(c * 4):
                out[c] = c * 4
        return out

    def used_types(self):
        return sorted(set(self.corner_types.ravel().tolist()) - {'?'})

    # ------------------------------------------------------------ heights
    def _span(self, cx, cz, radius):
        x0 = max(0, int(np.floor((cx - radius) / TILE)))
        x1 = min(self.W - 1, int(np.ceil((cx + radius) / TILE)))
        z0 = max(0, int(np.floor((cz - radius) / TILE)))
        z1 = min(self.D - 1, int(np.ceil((cz + radius) / TILE)))
        return x0, x1, z0, z1

    def sculpt(self, mode, cx, cz, radius, strength, target=None):
        """mode: raise, lower, smooth, flatten (to target), noise.
        Returns the tile rect (x0, z0, x1, z1) touched."""
        x0, x1, z0, z1 = self._span(cx, cz, radius)
        H = self.t['height']
        zz, xx = np.mgrid[z0:z1 + 1, x0:x1 + 1]
        d = np.sqrt((xx * TILE - cx) ** 2 + (zz * TILE - cz) ** 2) / max(radius, 1)
        w = np.clip(1 - d, 0, 1)
        w = w * w * (3 - 2 * w)                    # smoothstep falloff
        h = H[z0:z1 + 1, x0:x1 + 1].astype(np.float64)
        if mode == 'raise':
            h += w * strength
        elif mode == 'lower':
            h -= w * strength
        elif mode == 'flatten':
            h += (target - h) * np.clip(w * strength / 400.0, 0, 1)
        elif mode == 'smooth':
            P = H[max(0, z0 - 1):z1 + 2, max(0, x0 - 1):x1 + 2].astype(np.float64)
            pad = np.pad(P, 1, mode='edge')
            avg = (pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:] + 4 * pad[1:-1, 1:-1]) / 8
            oz, ox = z0 - max(0, z0 - 1), x0 - max(0, x0 - 1)
            avg = avg[oz:oz + h.shape[0], ox:ox + h.shape[1]]
            h += (avg - h) * np.clip(w * strength / 400.0, 0, 1)
        elif mode == 'noise':
            rng = np.random.default_rng()
            h += w * rng.normal(0, strength * 0.3, h.shape)
        H[z0:z1 + 1, x0:x1 + 1] = np.clip(np.round(h), -32768, 32767).astype(np.int16)
        return (max(0, x0 - 1), max(0, z0 - 1), x1, z1)

    def height_at(self, x, z):
        return self.spoke.ground_height(x, z)

    # ------------------------------------------------------------ tile fields
    def set_field(self, field, cx, cz, radius, value):
        """Set water / walltype / flags of the tiles within radius."""
        x0, x1, z0, z1 = self._span(cx, cz, radius)
        zz, xx = np.mgrid[z0:z1 + 1, x0:x1 + 1]
        inside = ((xx + 0.5) * TILE - cx) ** 2 + ((zz + 0.5) * TILE - cz) ** 2 <= max(radius, 600) ** 2
        sub = self.t[field][z0:z1 + 1, x0:x1 + 1]
        sub[inside] = value
        return (x0, z0, x1, z1)

    # ------------------------------------------------------------ light
    def light_grid(self):
        return self.t['light'][:, :, 0]

    def set_light_grid(self, G, rect=None):
        """Write a corner light grid back into the 6 bytes of each tile."""
        L = self.t['light']
        if rect is None:
            rect = (0, 0, self.W - 1, self.D - 1)
        x0, z0, x1, z1 = rect
        x0, z0 = max(0, x0 - 1), max(0, z0 - 1)
        x1, z1 = min(self.W - 1, x1 + 1), min(self.D - 1, z1 + 1)
        Gp = np.pad(G, ((0, 1), (0, 1)), mode='edge')
        sl = (slice(z0, z1 + 1), slice(x0, x1 + 1))
        g00 = Gp[z0:z1 + 1, x0:x1 + 1]
        g01 = Gp[z0 + 1:z1 + 2, x0:x1 + 1]     # (x, z+1)
        g10 = Gp[z0:z1 + 1, x0 + 1:x1 + 2]     # (x+1, z)
        g11 = Gp[z0 + 1:z1 + 2, x0 + 1:x1 + 2]
        L[sl + (0,)] = g00
        L[sl + (1,)] = g01
        L[sl + (2,)] = g10
        L[sl + (3,)] = g01
        L[sl + (4,)] = g11
        L[sl + (5,)] = g10

    def lights(self):
        out = []
        for o in self.spoke.objects:
            if o.cat == 'object' and o.listkey == ('LIT',) and o.kind == 'L':
                out.append(o.local_pos())
        return np.array(out).reshape(-1, 3)

    def compute_light(self, rect=None, lights=None, k=None, r=None, ambient=None):
        k = self.light_k if k is None else k
        r = self.light_r if r is None else r
        amb = self.ambient if ambient is None else ambient
        Ls = self.lights() if lights is None else lights
        if rect is None:
            rect = (0, 0, self.W - 1, self.D - 1)
        x0, z0, x1, z1 = rect
        zz, xx = np.mgrid[z0:z1 + 1, x0:x1 + 1]
        X, Z = xx * TILE, zz * TILE
        acc = np.zeros(X.shape)
        for l in Ls:
            if l[0] < X.min() - r or l[0] > X.max() + r or l[2] < Z.min() - r or l[2] > Z.max() + r:
                continue
            d = np.sqrt((X - l[0]) ** 2 + (Z - l[2]) ** 2)
            acc += np.clip(1 - d / r, 0, 1)
        return np.clip(np.round(amb + k * acc), amb, 31).astype(np.uint8)

    def relight(self, rect=None):
        G = self.light_grid().copy()
        if rect is None:
            rect = (0, 0, self.W - 1, self.D - 1)
        x0, z0, x1, z1 = rect
        G[z0:z1 + 1, x0:x1 + 1] = self.compute_light(rect)
        self.set_light_grid(G, rect)

    def fit_light(self):
        """Fit k and R to the current baked light (for relighting in the same style)."""
        Ls = self.lights()
        if not len(Ls):
            return None
        G = self.light_grid().astype(np.float64)
        used = self.t['tex'] > 0
        zs, xs = np.nonzero(used)
        rng = np.random.default_rng(1)
        sel = rng.choice(len(zs), min(6000, len(zs)), replace=False)
        zs, xs = zs[sel], xs[sel]
        X, Z = xs * TILE, zs * TILE
        y = G[zs, xs]
        d = np.sqrt((X[:, None] - Ls[None, :, 0]) ** 2 + (Z[:, None] - Ls[None, :, 2]) ** 2)
        best = None
        for r in np.arange(6000, 16001, 500):
            F = np.clip(1 - d / r, 0, 1).sum(1)
            for k in np.arange(5, 45, 0.5):
                e = np.abs(np.clip(np.round(self.ambient + k * F), self.ambient, 31) - y).mean()
                if best is None or e < best[0]:
                    best = (e, k, r)
        self.light_k, self.light_r = float(best[1]), float(best[2])
        return best

    # ------------------------------------------------------------ mesh
    def mesh_rows(self, z0, z1):
        """Vertex arrays for tile rows z0..z1-1, every tile of the row (6
        vertices per tile, layer -1 = not drawn), so row z starts at vertex
        z * (W-1) * 6 of the full mesh."""
        W = self.W
        H = self.t['height'].astype(np.float32)
        tex = self.t['tex']
        flags = self.t['flags']
        G = self.light_grid().astype(np.float32)
        zs, xs = np.mgrid[z0:z1, 0:W - 1]
        zs, xs = zs.ravel(), xs.ravel()
        X0 = xs * TILE; X1 = X0 + TILE; Z0 = zs * TILE; Z1 = Z0 + TILE
        corner = {
            '00': (np.stack([X0, H[zs, xs], Z0], -1), G[zs, xs], (0, 0)),
            '10': (np.stack([X1, H[zs, xs + 1], Z0], -1), G[zs, xs + 1], (1, 0)),
            '01': (np.stack([X0, H[zs + 1, xs], Z1], -1), G[zs + 1, xs], (0, 1)),
            '11': (np.stack([X1, H[zs + 1, xs + 1], Z1], -1), G[zs + 1, xs + 1], (1, 1)),
        }
        alt = ((flags[zs, xs] & 0x0a) != 0)[:, None]
        # normal split p00-p11: (00 10 11) (00 11 01); alternate p10-p01: (10 11 01) (10 01 00)
        order_n = ('00', '10', '11', '00', '11', '01')
        order_a = ('10', '11', '01', '10', '01', '00')
        pos = np.stack([np.where(alt, corner[a][0], corner[b][0]) for a, b in zip(order_a, order_n)], 1)
        lit = np.stack([np.where(alt[:, 0], corner[a][1], corner[b][1]) for a, b in zip(order_a, order_n)], 1)
        uv = np.stack([np.where(alt, corner[a][2], corner[b][2]) for a, b in zip(order_a, order_n)], 1)
        if not hasattr(self, '_lay_cache'):
            self.refresh_layers()
        layers = self._lay_cache
        rt = tex[zs, xs]
        for i in np.unique(rt[(layers[rt] < 0) & (rt > 0)]).tolist():
            layers[i] = self.layer_for(int(i))       # entries added by painting
        lay = np.repeat(layers[rt], 6)
        lit = np.clip(lit.reshape(-1) / 31.0 * 1.3 + 0.2, 0, 1.3)
        return dict(pos=pos.reshape(-1, 3).astype(np.float32), uv=uv.reshape(-1, 2).astype(np.float32),
                    layer=lay.astype(np.float32), light=lit.astype(np.float32))

    def refresh_layers(self):
        """Layer of every BLT entry used by a tile (creates texture layers)."""
        used = set(np.unique(self.t['tex']).tolist())
        arr = np.full(1024, -1, np.float32)
        for i in used:
            arr[i] = self.layer_for(int(i))
        self._lay_cache = arr
        return arr

    def full_mesh(self):
        self.refresh_layers()
        return self.mesh_rows(0, self.D - 1)

    def water_mesh(self):
        """Translucent water surfaces of water tiles (type -> level)."""
        w = self.t['water']
        out = []
        for z, x in zip(*np.nonzero(w[:-1, :-1])):
            y = WATER_LEVEL.get(int(w[z, x]))
            if y is None:
                continue
            x0, z0 = x * TILE, z * TILE
            q = [(x0, y, z0), (x0 + TILE, y, z0), (x0 + TILE, y, z0 + TILE), (x0, y, z0 + TILE)]
            out += [q[0], q[1], q[2], q[0], q[2], q[3]]
        return np.array(out, np.float32).reshape(-1, 3)


def _paint_walls(self, cx, cz, radius, walltype):
    """Forest (1) / castle (2) wall tiles, or clear them (0), within radius.
    Solid wall tiles get flags 0x0f; the face bits (0x10 +x, 0x20 +z, 0x40 -x,
    0x80 -z: sides that border open ground, where the wall bitmaps are drawn)
    are recomputed around the change. Returns the touched tile rect."""
    t = self.t
    x0, x1, z0, z1 = self._span(cx, cz, radius)
    x1, z1 = min(x1, self.W - 2), min(z1, self.D - 2)
    zz, xx = np.mgrid[z0:z1 + 1, x0:x1 + 1]
    inside = ((xx + 0.5) * TILE - cx) ** 2 + ((zz + 0.5) * TILE - cz) ** 2 <= max(radius, 600) ** 2
    zs, xs = zz[inside], xx[inside]
    if walltype:
        t['flags'][zs, xs] = 0x0f
        t['walltype'][zs, xs] = walltype
        t['unk10'][zs, xs] = 4096
    else:
        t['flags'][zs, xs] = 0
        t['walltype'][zs, xs] = 0
    rect = (max(0, x0 - 1), max(0, z0 - 1), min(self.W - 1, x1 + 1), min(self.D - 1, z1 + 1))
    self.fix_wall_faces(rect)
    return rect


def _fix_wall_faces(self, rect):
    t = self.t
    x0, z0, x1, z1 = rect
    solid = (t['flags'] & 0x0f) == 0x0f
    for z in range(z0, z1 + 1):
        for x in range(x0, x1 + 1):
            f = int(t['flags'][z, x])
            if (f & 0x0f) != 0x0f:
                continue
            bits = 0
            for bit, dx, dz in ((0x10, 1, 0), (0x20, 0, 1), (0x40, -1, 0), (0x80, 0, -1)):
                nx, nz = x + dx, z + dz
                if 0 <= nx < self.W - 1 and 0 <= nz < self.D - 1 and not solid[nz, nx] and t['tex'][nz, nx]:
                    bits |= bit
            t['flags'][z, x] = 0x0f | bits


Terrain.paint_walls = _paint_walls
Terrain.fix_wall_faces = _fix_wall_faces
