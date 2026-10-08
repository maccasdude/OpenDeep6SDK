"""
d6edit - forest / castle walls and canopy of terrain spokes, built from
formats/d6walls.py (RenderTile_ rules) in 32x32 tile chunks so terrain edits
only rebuild the chunks they touch.
"""
import numpy as np

import d6walls

CHUNK = 32


class WallBuilder(object):
    def __init__(self, spoke):
        self.sp = spoke
        self.ted = spoke.ted
        self.layers = {}
        self.tex = d6walls.wall_textures(spoke.root, spoke.number)

    def layer(self, name):
        if name not in self.layers:
            img = self.tex.get(name)
            if img is None:
                img = np.full((128, 128, 4), 128, np.uint8)
            self.layers[name] = self.sp.texlib.add(('wall', name), img[..., :3])
        return self.layers[name]

    def chunks(self):
        W, D = self.ted.W, self.ted.D
        return [(cx, cz) for cz in range(0, D, CHUNK) for cx in range(0, W, CHUNK)]

    def chunk_of_rect(self, rect):
        x0, z0, x1, z1 = rect
        out = []
        for cz in range((max(0, z0 - 1) // CHUNK) * CHUNK, z1 + 2, CHUNK):
            for cx in range((max(0, x0 - 1) // CHUNK) * CHUNK, x1 + 2, CHUNK):
                if cx < self.ted.W and cz < self.ted.D:
                    out.append((cx, cz))
        return out

    def build(self, cx, cz):
        """{'walls': arrays or None, 'canopy': arrays or None} for one chunk."""
        reg = (cx, cz, min(cx + CHUNK, self.ted.W), min(cz + CHUNK, self.ted.D))
        polys = d6walls.wall_mesh(self.ted.map, self.sp.root, self.sp.number, region=reg)
        res = {}
        for kind in ('walls', 'canopy'):
            pos, uv, lay, lit = [], [], [], []
            for p in polys:
                if (p['kind'] == 'canopy') != (kind == 'canopy'):
                    continue
                v, t, L = p['verts'], p['uv'], p['light']
                li = self.layer(p['texture'])
                for k in range(1, len(v) - 1):
                    for j in (0, k, k + 1):
                        pos.append(v[j])
                        uv.append(t[j])
                        lay.append(li)
                        lit.append(L[j])
            if pos:
                lit = np.clip(np.array(lit, np.float32) / 31.0 * 1.3 + 0.2, 0, 1.3)
                res[kind] = dict(pos=np.array(pos, np.float32), uv=np.array(uv, np.float32),
                                 layer=np.array(lay, np.float32), light=lit)
            else:
                res[kind] = None
        return res
