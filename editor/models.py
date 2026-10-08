"""
d6edit - 3D models of placed objects (monsters, items, props).

Turns d6model meshes into flat vertex arrays for the viewport (one entry per
distinct model, drawn instanced) and provides the engine's object transform.

Engine transform (GraphObj_UpdateMatrix_, vector.c): the record's rot = (x, y, z)
in 1/1024 turns builds M = RX(x) . RZ(z) . RY(y) (each Mat3_Rotate* premultiplies)
and a model vertex goes to world = v . M + pos (row vector), with
  RY = [[c,0,-s],[0,1,0],[s,0,c]]  RX = [[1,0,0],[0,c,s],[0,-s,c]]  RZ = [[c,s,0],[-s,c,0],[0,0,1]]
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'formats'))
import d6model  # noqa: E402

TURN = 1024.0


def rot_matrix(rot):
    """3x3 row-vector matrix for a record's rot (x, y, z) in 1/1024 turns."""
    ax, ay, az = [float(r) * 2 * math.pi / TURN for r in rot]
    c, s = math.cos(ax), math.sin(ax)
    RX = np.array([[1, 0, 0], [0, c, s], [0, -s, c]])
    c, s = math.cos(ay), math.sin(ay)
    RY = np.array([[c, 0, -s], [0, 1, 0], [s, 0, c]])
    c, s = math.cos(az), math.sin(az)
    RZ = np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]])
    return RX @ RZ @ RY


class ModelMesh(object):
    """Drawable data of one model: flat triangle arrays in model space."""

    def __init__(self, path, model, texlib):
        self.path = path
        self.model = model
        m = model.mesh()
        v = m['vertices'].astype(np.float32)
        tris = m['tris']
        pos, uv, layer, nrm = [], [], [], []
        layers = {}

        def lay(ti, masked):
            k = (ti, masked)
            if k not in layers:
                t = model.textures[ti]
                img = t.rgba.copy()
                if not masked:
                    img[..., 3] = 255          # index 0 draws black on normal polys
                layers[k] = texlib.add(('mdl', path, ti, masked), img)
            return layers[k]
        if len(tris):
            P = v[tris]                                        # (T,3,3)
            n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
            n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-6)
            L = np.array([lay(int(t), bool(f & d6model.PF_MASKED)) for t, f in zip(m['tex'], m['flags'])],
                         np.float32)
            pos.append(P.reshape(-1, 3))
            uv.append(m['uv'].reshape(-1, 2))
            layer.append(np.repeat(L, 3))
            nrm.append(np.repeat(n, 3, axis=0))
        # sprites (flames, bushes): two crossed vertical quads
        for sp in m['sprites']:
            t = model.textures[sp['tex']] if sp['tex'] < len(model.textures) else None
            if t is None or not t.w:
                continue
            c = v[sp['vertex']]
            w, h = max(sp['width'], 1) / 2.0, max(sp['height'], 1) / 2.0
            L = lay(sp['tex'], True)
            for ax in ((1, 0, 0), (0, 0, 1)):
                a = np.array(ax, np.float32) * w
                up = np.array([0, h, 0], np.float32)
                q = [c - a - up, c + a - up, c + a + up, c - a + up]
                quv = [(0, 1), (1, 1), (1, 0), (0, 0)]
                for k in (0, 1, 2, 0, 2, 3):
                    pos.append(q[k][None])
                    uv.append(np.array([quv[k]], np.float32))
                    layer.append(np.array([L], np.float32))
                    nrm.append(np.array([[0, 1, 0]], np.float32))
        if pos:
            self.pos = np.concatenate(pos).astype(np.float32)
            self.uv = np.concatenate(uv).astype(np.float32)
            self.layer = np.concatenate(layer).astype(np.float32)
            self.normal = np.concatenate(nrm).astype(np.float32)
            self.lo = self.pos.min(0)
            self.hi = self.pos.max(0)
        else:
            self.pos = None
            self.lo = np.array([-100, 0, -100], np.float32)
            self.hi = np.array([100, 200, 100], np.float32)
        self.tris = self.pos.reshape(-1, 3, 3).astype(np.float64) if self.pos is not None else None


class ModelCache(object):
    """Models by (kind, record number), shared by all spokes of a game."""

    def __init__(self, root):
        self.root = root
        self.by_path = {}
        self.by_rec = {}
        self.errors = {}

    def path_for(self, kind, recno):
        k = (kind, recno)
        if k not in self.by_rec:
            try:
                self.by_rec[k] = d6model.model_for(self.root, kind, recno) if recno else None
            except Exception as e:
                self.errors[k] = str(e)
                self.by_rec[k] = None
        return self.by_rec[k]

    def get(self, kind, recno, texlib):
        """ModelMesh for a record, None if it has none. Textures go into texlib
        (one per spoke), so a mesh is built once per (model, texlib)."""
        path = self.path_for(kind, recno)
        if not path:
            return None
        key = (path, id(texlib))
        if key not in self.by_path:
            try:
                m = d6model.load_model(self.root, path)
                self.by_path[key] = ModelMesh(path, m, texlib)
            except Exception as e:
                self.errors[path] = str(e)
                self.by_path[key] = None
        return self.by_path[key]

    def drop_texlib(self, texlib):
        for k in [k for k in self.by_path if k[1] == id(texlib)]:
            del self.by_path[k]


def ray_local(o, d, pos, rot):
    """Ray (world) into model space of an object at pos with rot."""
    M = rot_matrix(rot)
    # world = v @ M + pos  ->  v = (world - pos) @ M^T   (M is orthonormal)
    return (np.asarray(o) - pos) @ M.T, np.asarray(d) @ M.T


def obb_corners(lo, hi, pos, rot):
    M = rot_matrix(rot)
    c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    return c @ M + pos
