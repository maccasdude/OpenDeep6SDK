#!/usr/bin/env python3
"""
d6mdlio.py - write Deep6 .mdl models and convert them to / from other formats.

* MdlBuilder: builds a version 9 .mdl (1 LOD, paltype 0, no frame bits) from
  frames of vertices, textured triangles, textures and an animation table.
  Layout and fields: docs/formats/models.md (Model_Read_).
* export_obj(model, path)        Wavefront OBJ + MTL + PNG textures (one frame)
* export_glb(model, path)        glTF 2.0 binary: mesh, textures, every stored
                                 frame as a morph target, one glTF animation
                                 per entry of the model's animation table
                                 (named anim_<id>), attachments as empty nodes
* import_file(path) -> builder   .obj / .gltf / .glb (static meshes, or glb
                                 morph-target frames + anim_<id> animations as
                                 written by export_glb)

Coordinates: the engine is left-handed (x right, y up, +z = facing) with
about 1 unit = 1 mm. glTF / OBJ are right-handed: x is mirrored and triangle
winding reversed on the way out and back; scale 0.001 (metres) by default.

CLI
  d6mdlio.py --export-obj  GAMEDIR model.mdl|M 1  out.obj [--frame N]
  d6mdlio.py --export-glb  GAMEDIR model.mdl|M 1  out.glb
  d6mdlio.py --import      in.obj|in.glb out.mdl [--scale S] [--attach-from GAMEDIR model.mdl]
  d6mdlio.py --reencode    GAMEDIR model.mdl out.mdl      (decode + rebuild, for testing)
  d6mdlio.py --selftest    GAMEDIR                         (rebuild every model, compare meshes)
"""
import base64
import io
import json
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import d6model    # noqa: E402
import d6terrain  # noqa: E402

PF_MASKED = d6model.PF_MASKED
PF_TWOSIDED = d6model.PF_TWOSIDED
EXPORT_SCALE = 0.001


# ---------------------------------------------------------------------------
# writer
# ---------------------------------------------------------------------------
class MdlBuilder(object):
    """frames: list of (V,3) float vertex arrays (model space, all frames same V)
    tris: (T,3) vertex indices; uvs: (T,3,2) in 0..1 of the triangle's texture
    tex: (T,) texture index; flags: (T,) poly flags (group | PF_*)
    textures: list of RGBA (h,w,4) uint8 images (alpha < 128 = transparent)
    anims: {id: (first, last, action)}; attachments: list of (trans (n,3), rot (n,3,3))
    Alternatively give indexed textures + palette (palette256 (256,3), textures_indexed)."""

    def __init__(self):
        self.frames = []
        self.tris = np.zeros((0, 3), np.int32)
        self.uvs = np.zeros((0, 3, 2), np.float32)
        self.tex = np.zeros(0, np.int32)
        self.flags = np.zeros(0, np.uint8)
        self.textures = []
        self.textures_indexed = None
        self.palette256 = None
        self.anims = {}
        self.attachments = []
        self.frame_vec = None
        self.ngroups = 1
        self.warnings = []

    # ---------------------------------------------------------------- palette
    def _quantize(self):
        """Shared 255-colour palette for all textures (index 0 = transparent / black)."""
        from PIL import Image
        if self.textures_indexed is not None:
            return self.palette256, self.textures_indexed
        opaque = []
        for t in self.textures:
            px = t[..., :3][t[..., 3] >= 128]
            if len(px):
                opaque.append(px.reshape(-1, 3))
        allpx = np.concatenate(opaque) if opaque else np.zeros((1, 3), np.uint8)
        if len(allpx) > 400000:
            allpx = allpx[np.random.default_rng(0).choice(len(allpx), 400000, replace=False)]
        strip = Image.fromarray(allpx.reshape(1, -1, 3).astype(np.uint8))
        q = strip.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        pal = np.array(q.getpalette()[:255 * 3], np.uint8).reshape(-1, 3)
        palette = np.zeros((256, 3), np.uint8)
        palette[1:1 + len(pal)] = pal
        out = []
        for t in self.textures:
            idx = d6terrain.quantize(t[..., :3], palette, exclude=(0,))
            idx[t[..., 3] < 128] = 0
            out.append(idx.astype(np.uint8))
        return palette, out

    # ---------------------------------------------------------------- paths
    def _paths(self, F):
        """Split vertices into paths whose consecutive deltas fit in s8 in every frame."""
        V = F.shape[1]
        lens = []
        if V == 0:
            return lens
        d = np.abs(np.diff(F, axis=1)).max(axis=(0, 2)) if V > 1 else np.zeros(0)
        n = 1
        for i in range(V - 1):
            if d[i] <= 127 and n < 65535:
                n += 1
            else:
                lens.append(n)
                n = 1
        lens.append(n)
        return lens

    def build(self):
        if not self.frames:
            raise ValueError('no frames')
        F = np.round(np.stack([np.asarray(f, np.float64) for f in self.frames])).astype(np.int64)
        if np.abs(F).max(initial=0) > 32767:
            raise ValueError('vertex coordinates exceed +-32767 units')
        # weld vertices that are identical in every frame (imports give one per corner)
        key = F.transpose(1, 0, 2).reshape(F.shape[1], -1)
        uniq, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
        if len(uniq) < F.shape[1]:
            order = np.argsort(first)                      # keep the original vertex order
            rank = np.empty_like(order)
            rank[order] = np.arange(len(order))
            F = F[:, first[order]]
            self.tris = rank[inv.reshape(-1)][np.asarray(self.tris)]
        nframes, V = F.shape[0], F.shape[1]
        if nframes > 65535:
            raise ValueError('too many frames')
        palette, tex_idx = self._quantize()
        if len(tex_idx) > 255:
            raise ValueError('at most 255 textures per part')
        lens = self._paths(F)
        # frames: per path s16 start + s8 deltas (cumulative)
        fdata = []
        for f in range(nframes):
            b = bytearray()
            k = 0
            for n in lens:
                b += struct.pack('<3h', *F[f, k])
                if n > 1:
                    b += np.diff(F[f, k:k + n], axis=0).astype(np.int8).tobytes()
                k += n
            fdata.append(bytes(b))
        framesize = len(fdata[0])
        # corners / polygons
        T = len(self.tris)
        if T * 3 > 65535:
            raise ValueError('too many triangles (%d, max 21845)' % T)
        uvrec = bytearray()
        polys = bytearray()
        pflags = bytearray()
        for t in range(T):
            ti = int(self.tex[t])
            h, w = tex_idx[ti].shape
            for j in range(3):
                u, v = self.uvs[t, j]
                uvrec += struct.pack('<3h', int(self.tris[t, j]), int(round(u * w)), int(round(v * h)))
            polys += struct.pack('<HBB', 3 * t, 3, ti)
            pflags.append(int(self.flags[t]) & 0xff)
        ngroups = max(self.ngroups, int((self.flags & 0x1f).max(initial=0)) + 1)
        anim = bytearray(1536)
        for i, (a, b, c) in self.anims.items():
            if 0 <= i < 256:
                struct.pack_into('<3H', anim, 6 * i, a & 0xffff, b & 0xffff, c & 0xffff)
        att = bytearray()
        for trans, rot in self.attachments:
            n = len(trans)
            att += struct.pack('<H', n)
            att += np.asarray(trans, '<f4').reshape(n, 3).tobytes()
            att += np.asarray(rot, '<f4').reshape(n, 9).tobytes()
        fvec = self.frame_vec if self.frame_vec is not None else np.zeros((nframes, 2, 3), np.float32)

        out = bytearray()
        out += struct.pack('<III', d6model.MAGIC, 9, 0)        # parttab patched below
        out.append(0)                                           # paltype 0
        out += d6terrain.make_p16(palette)
        out += struct.pack('<H', nframes)
        out += anim
        out.append(len(self.attachments))
        out += att
        out.append(1)                                           # nparts
        parttab = len(out)
        out += struct.pack('<I', 0)
        struct.pack_into('<I', out, 8, parttab)
        po = len(out)
        struct.pack_into('<I', out, parttab, po)
        out += bytes(20)                                        # part header, patched
        out += struct.pack('<I', T * 3) + uvrec
        out += struct.pack('<I', T) + polys + pflags
        out.append(ngroups)
        out += struct.pack('<H', nframes)
        npaths_off = len(out)
        out += struct.pack('<I', len(lens)) + struct.pack('<%dH' % len(lens), *lens)
        out += struct.pack('<I', framesize)
        for f in range(nframes):
            out += fdata[f]
            out += np.asarray(fvec[f], '<f4').reshape(6).tobytes()
        out.append(len(tex_idx))
        texarr_off = len(out)
        out += bytes(4 * len(tex_idx))
        spr_off = len(out)
        out.append(0)                                           # sprite list: none
        for i, t in enumerate(tex_idx):
            struct.pack_into('<I', out, texarr_off + 4 * i, len(out))
            h, w = t.shape
            out += struct.pack('<II', w, h) + np.ascontiguousarray(t, np.uint8).tobytes()
        struct.pack_into('<5I', out, po, npaths_off, texarr_off, spr_off, 0, V)
        return bytes(out)


def builder_from_model(m, frames=None):
    """MdlBuilder carrying a decoded model (all stored frames, LOD 0, sprites
    dropped). Keeps the palette and the indexed textures exactly."""
    b = MdlBuilder()
    fr = m.stored_frames() if frames is None else frames
    if not fr:
        raise ValueError('model has no stored frames')
    remap = {f: i for i, f in enumerate(fr)}
    b.frames = [np.concatenate([m.part_vertices(p, f) for p in m.parts]) for f in fr]
    mesh = m.mesh(frame=fr[0], groups=None)
    b.tris, b.uvs, b.tex, b.flags = mesh['tris'], mesh['uv'], mesh['tex'], mesh['flags']
    b.palette256 = m.palette.copy()
    b.textures_indexed = [t.index for t in m.textures]
    if any(len(mesh['sprites']) for _ in [0]):
        b.warnings.append('%d sprites dropped (not supported by the writer)' % len(mesh['sprites']))
    b.ngroups = m.parts[0].ngroups if m.parts else 1
    for i, (a, e, c) in enumerate(m.anims):
        if (a, e, c) != (0, 0, 0):
            if a in remap and e in remap:
                b.anims[i] = (remap[a], remap[e], remap.get(c, c))
            else:
                b.warnings.append('animation %d uses frames that are not stored' % i)
    for at in m.attachments:
        n = at['nframes']
        if n == m.nframes and n:
            b.attachments.append((at['trans'][fr], at['rot'][fr]))
        else:
            b.attachments.append((at['trans'], at['rot']))
    if m.parts and len(m.parts) == 1:
        b.frame_vec = m.parts[0].frame_vec[fr]
    return b


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def _png_bytes(rgba):
    from PIL import Image
    bio = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgba)).save(bio, 'PNG')
    return bio.getvalue()


def _to_rh(v, scale):
    v = np.asarray(v, np.float64) * scale
    return v * np.array([-1.0, 1.0, 1.0])


def export_obj(m, path, frame=None, scale=EXPORT_SCALE, groups='default'):
    mesh = m.mesh(frame=frame, groups=groups)
    base = os.path.splitext(path)[0]
    name = os.path.basename(base)
    V = _to_rh(mesh['vertices'], scale)
    used = sorted(set(mesh['tex'].tolist()))
    with open(base + '.mtl', 'w') as f:
        for t in used:
            png = '%s_tex%d.png' % (name, t)
            with open(os.path.join(os.path.dirname(path) or '.', png), 'wb') as g:
                g.write(_png_bytes(m.textures[t].rgba))
            f.write('newmtl tex%d\nKd 1 1 1\nmap_Kd %s\n' % (t, png))
            if any(mesh['flags'][mesh['tex'] == t] & PF_MASKED):
                f.write('map_d %s\n' % png)
            f.write('\n')
    with open(path, 'w') as f:
        f.write('# exported from %s by OpenDeep6SDK d6mdlio (right-handed, %g units)\n' % (m.path, scale))
        f.write('mtllib %s.mtl\n' % name)
        for v in V:
            f.write('v %.6f %.6f %.6f\n' % tuple(v))
        k = 1
        for t in used:
            f.write('usemtl tex%d\n' % t)
            for ti in np.nonzero(mesh['tex'] == t)[0]:
                for j in range(3):
                    u, vv = mesh['uv'][ti, j]
                    f.write('vt %.6f %.6f\n' % (u, 1 - vv))
                a, b, c = mesh['tris'][ti] + 1
                # mirrored x: reverse winding to keep the front side
                f.write('f %d/%d %d/%d %d/%d\n' % (a, k, c, k + 2, b, k + 1))
                k += 3
    return path


def export_glb(m, path, scale=EXPORT_SCALE, groups='default'):
    """glTF 2.0 binary with morph-target frames and anim_<id> animations."""
    frames = m.stored_frames()
    if not frames:
        raise ValueError('no stored frames')
    f0 = m.default_frame if m.default_frame in frames else frames[0]
    mesh = m.mesh(frame=f0, groups=groups)
    tris = mesh['tris']
    T = len(tris)
    # unshared vertices (uv per corner)
    corner_v = tris.reshape(-1)
    order = np.arange(T * 3).reshape(T, 3)[:, [0, 2, 1]].reshape(-1)       # reversed winding
    allv = {f: np.concatenate([m.part_vertices(p, f) for p in m.parts]) for f in frames}
    pos0 = _to_rh(allv[f0][corner_v[order]], scale).astype(np.float32)
    uv = mesh['uv'].reshape(-1, 2)[order].astype(np.float32)
    texid = np.repeat(mesh['tex'], 3)[order]
    blob = bytearray()
    views, accessors = [], []

    def add(arr, target=None, typ='VEC3', comp=5126, minmax=False):
        a = np.ascontiguousarray(arr)
        while len(blob) % 4:
            blob.append(0)
        off = len(blob)
        blob.extend(a.tobytes())
        v = dict(buffer=0, byteOffset=off, byteLength=a.nbytes)
        if target:
            v['target'] = target
        views.append(v)
        acc = dict(bufferView=len(views) - 1, componentType=comp, count=len(a), type=typ)
        if minmax:
            acc['min'] = [float(x) for x in np.atleast_1d(a.min(0))]
            acc['max'] = [float(x) for x in np.atleast_1d(a.max(0))]
        accessors.append(acc)
        return len(accessors) - 1

    used = sorted(set(texid.tolist()))
    images, textures, materials = [], [], []
    for t in used:
        png = _png_bytes(m.textures[t].rgba)
        while len(blob) % 4:
            blob.append(0)
        off = len(blob)
        blob.extend(png)
        views.append(dict(buffer=0, byteOffset=off, byteLength=len(png)))
        images.append(dict(bufferView=len(views) - 1, mimeType='image/png', name='tex%d' % t))
        textures.append(dict(source=len(images) - 1, sampler=0))
        masked = bool(any(mesh['flags'][mesh['tex'] == t] & PF_MASKED))
        mat = dict(name='tex%d' % t, pbrMetallicRoughness=dict(baseColorTexture=dict(index=len(textures) - 1),
                                                             metallicFactor=0.0, roughnessFactor=1.0))
        if masked:
            mat['alphaMode'] = 'MASK'
        materials.append(mat)
    prims = []
    targets_by_prim = []
    for k, t in enumerate(used):
        sel = np.nonzero(texid == t)[0]
        p_acc = add(pos0[sel], 34962, minmax=True)
        uv_acc = add(uv[sel], 34962, 'VEC2')
        tg = []
        for f in frames:
            d = (_to_rh(allv[f][corner_v[order]], scale).astype(np.float32) - pos0)[sel]
            tg.append(dict(POSITION=add(d, 34962, minmax=True)))
        prims.append(dict(attributes=dict(POSITION=p_acc, TEXCOORD_0=uv_acc), material=k, mode=4, targets=tg))
        targets_by_prim.append(tg)
    nT = len(frames)
    gl_mesh = dict(name=os.path.basename(m.path), primitives=prims, weights=[0.0] * nT,
                   extras=dict(targetNames=['frame_%d' % f for f in frames]))
    # animations: weights channel, one key per frame at 15 fps (step)
    fidx = {f: i for i, f in enumerate(frames)}
    anims = []
    for aid, (a, e, c) in enumerate(m.anims):
        if (a, e, c) == (0, 0, 0):
            continue
        rng = list(range(a, e + 1)) if e >= a else list(range(a, e - 1, -1))
        rng = [f for f in rng if f in fidx]
        if not rng:
            continue
        times = np.arange(len(rng), dtype=np.float32) / 15.0
        w = np.zeros((len(rng), nT), np.float32)
        for i, f in enumerate(rng):
            w[i, fidx[f]] = 1.0
        ta = add(times, None, 'SCALAR', minmax=True)
        wa = add(w.reshape(-1), None, 'SCALAR')
        anims.append(dict(name='anim_%d' % aid, samplers=[dict(input=ta, output=wa, interpolation='STEP')],
                          channels=[dict(sampler=0, target=dict(node=0, path='weights'))],
                          extras=dict(action_frame=c, first=a, last=e)))
    nodes = [dict(name=os.path.splitext(os.path.basename(m.path))[0], mesh=0)]
    for i, at in enumerate(m.attachments):
        if at['nframes']:
            tr = _to_rh(at['trans'][min(f0, at['nframes'] - 1)], scale)
            nodes.append(dict(name='attach_%d' % i, translation=[float(x) for x in tr]))
    if len(nodes) > 1:
        nodes[0]['children'] = list(range(1, len(nodes)))
    gltf = dict(asset=dict(version='2.0', generator='OpenDeep6SDK d6mdlio'),
                scene=0, scenes=[dict(nodes=[0])], nodes=nodes, meshes=[gl_mesh],
                materials=materials, textures=textures, images=images,
                samplers=[dict(magFilter=9728, minFilter=9728)],
                accessors=accessors, bufferViews=views, buffers=[dict(byteLength=len(blob))],
                extras=dict(deep6=dict(source=os.path.basename(m.path), frames=frames, scale=scale)))
    if anims:
        gltf['animations'] = anims
    js = json.dumps(gltf, separators=(',', ':')).encode()
    while len(js) % 4:
        js += b' '
    while len(blob) % 4:
        blob.append(0)
    out = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
    out += struct.pack('<II', len(js), 0x4E4F534A) + js
    out += struct.pack('<II', len(blob), 0x004E4942) + bytes(blob)
    with open(path, 'wb') as f:
        f.write(out)
    return path


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------
def _load_image(path_or_bytes):
    from PIL import Image
    im = Image.open(io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes)
    return np.array(im.convert('RGBA'))


def _fit_texture(img, maxsize=256):
    """Keep sizes <= maxsize (the engine draws arbitrary sizes; shipped models
    use up to ~256)."""
    from PIL import Image
    h, w = img.shape[:2]
    s = min(1.0, maxsize / float(max(w, h)))
    if s < 1.0:
        img = np.array(Image.fromarray(img).resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS))
    return img


def import_obj(path, scale=1.0 / EXPORT_SCALE):
    verts, vts, faces = [], [], []
    mtl, cur = {}, None
    base = os.path.dirname(os.path.abspath(path))
    for line in open(path, errors='replace'):
        p = line.split()
        if not p:
            continue
        if p[0] == 'v':
            verts.append([float(x) for x in p[1:4]])
        elif p[0] == 'vt':
            vts.append([float(p[1]), float(p[2]) if len(p) > 2 else 0.0])
        elif p[0] == 'usemtl':
            cur = p[1]
        elif p[0] == 'mtllib':
            mp = os.path.join(base, ' '.join(p[1:]))
            if os.path.exists(mp):
                name = None
                for l2 in open(mp, errors='replace'):
                    q = l2.split()
                    if not q:
                        continue
                    if q[0] == 'newmtl':
                        name = q[1]
                        mtl[name] = {}
                    elif q[0] == 'map_Kd' and name:
                        mtl[name]['tex'] = os.path.join(base, ' '.join(q[1:]))
                    elif q[0] == 'map_d' and name:
                        mtl[name]['masked'] = True
        elif p[0] == 'f':
            idx = []
            for c in p[1:]:
                s = c.split('/')
                vi = int(s[0])
                ti = int(s[1]) if len(s) > 1 and s[1] else 0
                idx.append((vi - 1 if vi > 0 else len(verts) + vi, ti - 1 if ti > 0 else (len(vts) + ti if ti < 0 else -1)))
            for k in range(1, len(idx) - 1):
                faces.append((cur, idx[0], idx[k], idx[k + 1]))
    V = np.array(verts, np.float64)
    texnames = sorted(set(f[0] for f in faces), key=lambda x: str(x))
    b = MdlBuilder()
    texmap = {}
    for n in texnames:
        info = mtl.get(n, {})
        if info.get('tex') and os.path.exists(info['tex']):
            img = _fit_texture(_load_image(info['tex']))
        else:
            img = np.full((8, 8, 4), 200, np.uint8)
            b.warnings.append('material %s: no texture, grey used' % n)
        texmap[n] = len(b.textures)
        b.textures.append(img)
    tris, uvs, tex, flags = [], [], [], []
    for n, a, c, d in faces:
        tris.append([a[0], d[0], c[0]])          # mirror x -> reverse winding
        uv = []
        for corner in (a, d, c):
            if corner[1] >= 0:
                u, v = vts[corner[1]]
                uv.append([u, 1 - v])
            else:
                uv.append([0, 0])
        uvs.append(uv)
        tex.append(texmap[n])
        flags.append(PF_MASKED if mtl.get(n, {}).get('masked') else 0)
    b.frames = [(_to_rh(V, 1.0) * scale)]
    b.tris = np.array(tris, np.int32).reshape(-1, 3)
    b.uvs = np.array(uvs, np.float32).reshape(-1, 3, 2)
    b.tex = np.array(tex, np.int32)
    b.flags = np.array(flags, np.uint8)
    return b


def _gltf_load(path):
    data = open(path, 'rb').read()
    if data[:4] == b'glTF':
        jl = struct.unpack_from('<I', data, 12)[0]
        js = json.loads(data[20:20 + jl])
        o = 20 + jl
        bins = []
        if o < len(data):
            bl = struct.unpack_from('<I', data, o)[0]
            bins.append(data[o + 8:o + 8 + bl])
        base = os.path.dirname(os.path.abspath(path))
        buffers = []
        for i, bf in enumerate(js.get('buffers', [])):
            if 'uri' in bf:
                buffers.append(_uri(bf['uri'], base))
            else:
                buffers.append(bins[0])
    else:
        js = json.loads(data)
        base = os.path.dirname(os.path.abspath(path))
        buffers = [_uri(bf['uri'], base) for bf in js.get('buffers', [])]
    return js, buffers, os.path.dirname(os.path.abspath(path))


def _uri(uri, base):
    if uri.startswith('data:'):
        return base64.b64decode(uri.split(',', 1)[1])
    return open(os.path.join(base, uri), 'rb').read()


def _accessor(js, buffers, i):
    a = js['accessors'][i]
    bv = js['bufferViews'][a['bufferView']]
    comp = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8, 5122: np.int16, 5120: np.int8}[a['componentType']]
    n = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}[a['type']]
    buf = buffers[bv['buffer']]
    off = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
    item = np.dtype(comp).itemsize * n
    stride = bv.get('byteStride', item)
    cnt = a['count']
    if stride == item:
        arr = np.frombuffer(buf, comp, cnt * n, off).reshape(cnt, n)
    else:
        arr = np.stack([np.frombuffer(buf, comp, n, off + k * stride) for k in range(cnt)])
    if a.get('normalized') and comp != np.float32:
        arr = arr.astype(np.float32) / np.iinfo(comp).max
    return arr.astype(np.float32) if comp == np.float32 or a.get('normalized') else arr


def _node_matrices(js):
    """World matrix of every node (column-vector convention)."""
    def local(n):
        if 'matrix' in n:
            return np.array(n['matrix'], np.float64).reshape(4, 4).T
        t = np.array(n.get('translation', [0, 0, 0]), np.float64)
        q = n.get('rotation', [0, 0, 0, 1])
        s = np.array(n.get('scale', [1, 1, 1]), np.float64)
        x, y, z, w = q
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        M = np.identity(4)
        M[:3, :3] = R * s[None, :]
        M[:3, 3] = t
        return M
    nodes = js.get('nodes', [])
    world = [None] * len(nodes)
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get('children', []):
            parent[c] = i

    def get(i):
        if world[i] is None:
            M = local(nodes[i])
            world[i] = (get(parent[i]) @ M) if i in parent else M
        return world[i]
    return [get(i) for i in range(len(nodes))]


# names accepted for glTF animations (besides anim_<id>); ids: docs/formats/models.md section 6
ANIM_NAMES = {
    'ready': 0, 'idle': 0, 'stand': 0, 'readyalt': 1, 'look': 2, 'walk': 6, 'run': 11,
    'turnleft': 15, 'turnright': 16, 'backstep': 17, 'wound': 20, 'hit': 20, 'woundbig': 21,
    'death': 25, 'die': 25, 'dead': 26, 'sleep': 28, 'fly': 31, 'flyidle': 33, 'sneak': 35,
    'combatready': 47, 'stance': 47, 'swing': 50, 'attack': 50, 'chop': 51, 'jab': 52,
    'block': 85, 'punch': 90, 'kick': 93, 'bow': 70, 'crossbow': 75, 'throw': 80,
    'cast': 100, 'spell': 100, 'castb': 101, 'castc': 102, 'castd': 103, 'use': 110,
    'bite': 120, 'claw': 125, 'breath': 150, 'jump': 160, 'steal': 170, 'kneel': 171,
    'victory': 200, 'talk': 230, 'entry': 250, 'exit': 251,
}
SKIN_FPS = 16.0          # frames per second when baking skeletal animations


def anim_id_from_name(name):
    """'anim_6', 'Armature|walk', 'Walk.001' -> animation id, or None."""
    import re
    m = re.search(r'anim_(\d+)', name)
    if m:
        return int(m.group(1))
    key = re.sub(r'[^a-z]', '', name.lower().split('|')[-1].split('.')[0])
    return ANIM_NAMES.get(key)


def _quat_mat(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _sample(times, values, t, interp, path):
    if t <= times[0]:
        return values[0]
    if t >= times[-1]:
        return values[-1]
    i = int(np.searchsorted(times, t) - 1)
    if interp == 'STEP':
        return values[i]
    f = (t - times[i]) / max(1e-9, times[i + 1] - times[i])
    a, b = values[i], values[i + 1]
    if path == 'rotation':
        if np.dot(a, b) < 0:
            b = -b
        q = a * (1 - f) + b * f
        return q / (np.linalg.norm(q) or 1.0)
    return a * (1 - f) + b * f


def _bake_skin(js, buffers, mesh_prims, skin_index):
    """Skeletal animations -> list of (anim id or None, name, [vertex arrays]).
    mesh_prims: [(P (n,3), JOINTS (n,4), WEIGHTS (n,4))] in mesh order."""
    nodes = js['nodes']
    skin = js['skins'][skin_index]
    joints = skin['joints']
    ibm = _accessor(js, buffers, skin['inverseBindMatrices']).reshape(-1, 4, 4).transpose(0, 2, 1) \
        if 'inverseBindMatrices' in skin else np.tile(np.identity(4), (len(joints), 1, 1))
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get('children', []):
            parent[c] = i
    base = []
    for n in nodes:
        base.append({'translation': np.array(n.get('translation', [0, 0, 0]), np.float64),
                     'rotation': np.array(n.get('rotation', [0, 0, 0, 1]), np.float64),
                     'scale': np.array(n.get('scale', [1, 1, 1]), np.float64),
                     'matrix': np.array(n['matrix'], np.float64).reshape(4, 4).T if 'matrix' in n else None})

    def world_mats(over):
        local = []
        for i, b in enumerate(base):
            o = over.get(i, {})
            if b['matrix'] is not None and not o:
                local.append(b['matrix'])
                continue
            M = np.identity(4)
            M[:3, :3] = _quat_mat(o.get('rotation', b['rotation'])) * o.get('scale', b['scale'])[None, :]
            M[:3, 3] = o.get('translation', b['translation'])
            local.append(M)
        world = [None] * len(nodes)

        def get(i):
            if world[i] is None:
                world[i] = (get(parent[i]) @ local[i]) if i in parent else local[i]
            return world[i]
        return [get(i) for i in range(len(nodes))]

    def pose(over):
        W = world_mats(over)
        S = np.array([W[j] @ ibm[k] for k, j in enumerate(joints)])
        out = []
        for P, J, Wt in mesh_prims:
            Ph = np.c_[P, np.ones(len(P))]
            acc = np.zeros((len(P), 3))
            for k in range(4):
                M = S[J[:, k].astype(int)]
                acc += Wt[:, k:k + 1] * np.einsum('nij,nj->ni', M, Ph)[:, :3]
            out.append(acc)
        return np.concatenate(out)
    result = []
    for an in js.get('animations', []):
        ch = []
        t_end = 0.0
        for c in an['channels']:
            tgt = c['target']
            if tgt.get('path') not in ('translation', 'rotation', 'scale') or 'node' not in tgt:
                continue
            smp = an['samplers'][c['sampler']]
            times = _accessor(js, buffers, smp['input']).reshape(-1).astype(np.float64)
            vals = _accessor(js, buffers, smp['output']).astype(np.float64)
            vals = vals.reshape(len(times) * (3 if smp.get('interpolation') == 'CUBICSPLINE' else 1), -1)
            if smp.get('interpolation') == 'CUBICSPLINE':
                vals = vals[1::3]
            ch.append((tgt['node'], tgt['path'], times, vals, smp.get('interpolation', 'LINEAR')))
            t_end = max(t_end, times[-1])
        if not ch:
            continue
        n = max(1, int(round(t_end * SKIN_FPS)) + 1)
        frames = []
        for k in range(n):
            t = k / SKIN_FPS
            over = {}
            for node, path, times, vals, interp in ch:
                over.setdefault(node, {})[path] = _sample(times, vals, t, interp, path)
            frames.append(pose(over))
        result.append((anim_id_from_name(an.get('name', '')), an.get('name', ''), frames))
    rest = pose({})
    return rest, result


def import_gltf(path, scale=1.0 / EXPORT_SCALE):
    js, buffers, base = _gltf_load(path)
    mats = _node_matrices(js)
    b = MdlBuilder()
    texcache = {}

    def mat_texture(mi):
        if mi is None:
            return None, False
        m = js['materials'][mi]
        ti = m.get('pbrMetallicRoughness', {}).get('baseColorTexture', {}).get('index')
        masked = m.get('alphaMode') in ('MASK', 'BLEND')
        if ti is None:
            return None, masked
        if ti not in texcache:
            im = js['images'][js['textures'][ti]['source']]
            if 'bufferView' in im:
                bv = js['bufferViews'][im['bufferView']]
                raw = buffers[bv['buffer']][bv.get('byteOffset', 0):bv.get('byteOffset', 0) + bv['byteLength']]
            else:
                raw = _uri(im['uri'], base)
            texcache[ti] = len(b.textures)
            b.textures.append(_fit_texture(_load_image(bytes(raw))))
        return texcache[ti], masked
    pos_all, morph_all, tris, uvs, tex, flags = [], [], [], [], [], []
    vbase = 0
    ntargets = None
    mesh_node = None
    skin_prims, skin_index = [], None
    for ni, node in enumerate(js.get('nodes', [])):
        if 'mesh' not in node:
            continue
        M = mats[ni]
        mesh = js['meshes'][node['mesh']]
        if mesh_node is None:
            mesh_node = ni
        for pr in mesh['primitives']:
            if pr.get('mode', 4) != 4:
                continue
            P = _accessor(js, buffers, pr['attributes']['POSITION']).astype(np.float64)
            n = len(P)
            UV = _accessor(js, buffers, pr['attributes']['TEXCOORD_0']) if 'TEXCOORD_0' in pr['attributes'] \
                else np.zeros((n, 2), np.float32)
            idx = _accessor(js, buffers, pr['indices']).reshape(-1) if 'indices' in pr else np.arange(n)
            ti, masked = mat_texture(pr.get('material'))
            if ti is None:
                ti = len(b.textures)
                b.textures.append(np.full((8, 8, 4), 200, np.uint8))
            Pw = (np.c_[P, np.ones(n)] @ M.T)[:, :3]
            at = pr['attributes']
            if 'skin' in node and 'JOINTS_0' in at and 'WEIGHTS_0' in at:
                if skin_index is None:
                    skin_index = node['skin']
                Wt = _accessor(js, buffers, at['WEIGHTS_0']).astype(np.float64)
                if Wt.max() > 1.5:           # normalized integer weights
                    Wt = Wt / Wt.max()
                Wt = Wt / np.maximum(Wt.sum(1, keepdims=True), 1e-9)
                skin_prims.append((P, _accessor(js, buffers, at['JOINTS_0']), Wt))
            tg = pr.get('targets', [])
            if ntargets is None:
                ntargets = len(tg)
            elif len(tg) != ntargets:
                ntargets = 0
            morphs = [(_accessor(js, buffers, t['POSITION']).astype(np.float64) @ M[:3, :3].T) for t in tg if 'POSITION' in t]
            pos_all.append(Pw)
            morph_all.append(morphs)
            for k in range(0, len(idx) - 2, 3):
                a, c, d = int(idx[k]), int(idx[k + 1]), int(idx[k + 2])
                tris.append([a + vbase, d + vbase, c + vbase])           # reverse (mirror x)
                uvs.append([UV[a], UV[d], UV[c]])
                tex.append(ti)
                flags.append(PF_MASKED if masked else 0)
            vbase += n
    if not pos_all:
        raise ValueError('no triangle meshes in %s' % path)
    base_pos = np.concatenate(pos_all)
    frames = [base_pos]
    if ntargets:
        for t in range(ntargets):
            frames.append(base_pos + np.concatenate([m_[t] for m_ in morph_all]))
        frames = frames[1:]          # exported files: target t = frame t, base = default frame
    skinned = skin_index is not None and len(skin_prims) == len(pos_all)
    baked = []
    if skinned:
        rest, baked = _bake_skin(js, buffers, skin_prims, skin_index)
        frames = [rest]
        for aid, name, fr in baked:
            if aid is None:
                b.warnings.append('animation %r skipped: no id (name it anim_<id> or walk, run, attack ...)' % name)
                continue
            first = len(frames)
            frames.extend(fr)
            b.anims[aid] = (first, len(frames) - 1, first + (len(fr) - 1) // 2)
        if 0 not in b.anims:
            b.anims[0] = (0, 0, 0)
    b.frames = [_to_rh(f, 1.0) * scale for f in frames]
    b.tris = np.array(tris, np.int32).reshape(-1, 3)
    b.uvs = np.array(uvs, np.float32).reshape(-1, 3, 2)
    b.tex = np.array(tex, np.int32)
    b.flags = np.array(flags, np.uint8)
    for an in ([] if skinned else js.get('animations', [])):
        name = an.get('name', '')
        if not name.startswith('anim_') or not ntargets:
            continue
        try:
            aid = int(name[5:])
        except ValueError:
            continue
        ch = an['channels'][0]
        smp = an['samplers'][ch['sampler']]
        w = _accessor(js, buffers, smp['output']).reshape(-1)
        keys = len(_accessor(js, buffers, smp['input']))
        w = w.reshape(keys, -1)
        seq = [int(np.argmax(r)) for r in w]
        ex = an.get('extras', {})
        act = ex.get('action_frame', seq[0])
        b.anims[aid] = (seq[0], seq[-1], act if isinstance(act, int) and 0 <= act < len(frames) else seq[0])
        if seq != list(range(seq[0], seq[-1] + 1)) and seq != list(range(seq[0], seq[-1] - 1, -1)):
            b.warnings.append('animation %s frames are not consecutive; stored as range %d..%d' % (name, seq[0], seq[-1]))
    return b


def import_file(path, scale=1.0 / EXPORT_SCALE):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.obj':
        return import_obj(path, scale)
    if ext in ('.gltf', '.glb'):
        return import_gltf(path, scale)
    raise ValueError('unsupported format %s (use .obj, .gltf, .glb)' % ext)


def copy_attachments(b, template):
    """Attachment slots (weapon hands, helm...) from a template model, so a
    new monster holds items like the original. Frame counts must match or the
    template's frame 0 data is repeated."""
    nf = len(b.frames)
    b.attachments = []
    for at in template.attachments:
        n = at['nframes']
        if n == 0:
            b.attachments.append((np.zeros((0, 3)), np.zeros((0, 3, 3))))
        elif n >= nf:
            b.attachments.append((at['trans'][:nf], at['rot'][:nf]))
        else:
            idx = [min(i, n - 1) for i in range(nf)]
            b.attachments.append((at['trans'][idx], at['rot'][idx]))


# ---------------------------------------------------------------------------
def _write_new(path, data):
    tmp = path + '.d6tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def _resolve(gamedir, args):
    if len(args) >= 2 and args[0].upper() in ('M', 'I', 'P') and args[1].isdigit():
        return d6model.load_model(gamedir, d6model.model_for(gamedir, args[0], int(args[1]))), args[2:]
    return d6model.load_model(gamedir, args[0]), args[1:]


def _mesh_close(a, b):
    ma, mb = a.mesh(groups=None), b.mesh(groups=None)
    if len(ma['tris']) != len(mb['tris']):
        return False
    if not np.allclose(ma['vertices'][ma['tris']], mb['vertices'][mb['tris']], atol=0.5):
        return False
    return np.allclose(ma['uv'], mb['uv'], atol=0.02) and (ma['tex'] == mb['tex']).all()


def selftest(gamedir):
    bad = n = 0
    for root, ds, fs in os.walk(d6terrain.find_ci(gamedir, 'models')):
        for f in sorted(fs):
            if not f.lower().endswith('.mdl'):
                continue
            p = os.path.join(root, f)
            try:
                m = d6model.load_model(gamedir, p)
                if not m.stored_frames():
                    continue
                b = builder_from_model(m)
                m2 = d6model.Model(b.build(), p + '#rebuilt')
                n += 1
                if not _mesh_close(m, m2) or m2.nframes != len(m.stored_frames()):
                    bad += 1
                    print('MISMATCH', p)
            except Exception as e:
                bad += 1
                print('FAIL', p, e)
    print('rebuilt %d models, %d problems' % (n, bad))
    return bad == 0


def main(argv):
    a = argv[1:]
    if not a:
        print(__doc__)
        return 1
    if a[0] == '--selftest':
        return 0 if selftest(a[1]) else 1
    if a[0] in ('--export-obj', '--export-glb'):
        m, rest = _resolve(a[1], a[2:])
        frame = None
        if '--frame' in rest:
            i = rest.index('--frame')
            frame = int(rest[i + 1])
            del rest[i:i + 2]
        out = rest[0]
        if a[0] == '--export-obj':
            export_obj(m, out, frame=frame)
        else:
            export_glb(m, out)
        print('wrote', out)
        return 0
    if a[0] == '--reencode':
        m, rest = _resolve(a[1], a[2:])
        b = builder_from_model(m)
        _write_new(rest[0], b.build())
        print('wrote', rest[0], *b.warnings)
        return 0
    if a[0] == '--import':
        src, out = a[1], a[2]
        scale = 1.0 / EXPORT_SCALE
        if '--scale' in a:
            scale = float(a[a.index('--scale') + 1])
        b = import_file(src, scale)
        if '--attach-from' in a:
            i = a.index('--attach-from')
            copy_attachments(b, d6model.load_model(a[i + 1], a[i + 2]))
        _write_new(out, b.build())
        print('wrote', out, '(%d frames, %d triangles, %d textures)' % (len(b.frames), len(b.tris), len(b.textures)))
        for w in b.warnings:
            print('warning:', w)
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))


# ---------------------------------------------------------------------------
# model slots (name tables compiled into deep6.exe)
# ---------------------------------------------------------------------------
SLOT_KINDS = {'M': ('monster', 0x55), 'I': ('item', 0x51), 'P': ('prop', 0x51)}


def slot_path(gamedir, kind, index):
    """Path relative to models/ of table entry index (kind M/I/P)."""
    names = d6model.model_table(gamedir, kind)
    name = names[index]
    sub = 'pc' if kind == 'M' and index < d6model.PC_GFX_LIMIT else SLOT_KINDS[kind][0]
    rel = '%s/%s' % (sub, name)
    full = d6model.d6data.find_file(gamedir, 'models/' + rel)
    if full:
        return os.path.relpath(full, os.path.join(gamedir, 'models')).replace(os.sep, '/')
    return rel


def set_slot_name(gamedir, kind, index, name, backup_dir=None):
    """Rename a model table entry in deep6.exe (the game then loads
    models/<sub>/<name> for every record using that slot). Max 79 chars."""
    import shutil
    if len(name.encode('latin1')) > 0x4F:
        raise ValueError('name too long')
    exe_path = d6model.d6data.find_file(gamedir, 'deep6.exe')
    exe = bytearray(open(exe_path, 'rb').read())
    va, stride, count, sub = d6model.EXE_TABLES[kind]
    if not 0 <= index < count:
        raise ValueError('slot out of range')
    o = d6model._exe_va_to_off(bytes(exe), va) + index * stride
    b = name.encode('latin1')
    exe[o:o + 0x50] = b + b'\0' * (0x50 - len(b))
    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(exe_path, os.path.join(backup_dir, os.path.basename(exe_path)))
    with open(exe_path + '.tmp', 'wb') as f:
        f.write(exe)
    os.replace(exe_path + '.tmp', exe_path)
    d6model._table_cache.pop((os.path.abspath(gamedir), kind), None)
