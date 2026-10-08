"""Skinned glTF import: a two-bone column with a "walk" animation (bone 2 bends
90 degrees about z) is baked into vertex frames. Run: python3 tests/test_skin_import.py"""
import json, base64, os, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'formats'))
# column of quads from y=0..2, 2 segments; bone0 root at 0, bone1 at y=1
ys = [0, 1, 2]
P, J, W = [], [], []
for y in ys:
    for x, z in ((-.2, -.2), (.2, -.2), (.2, .2), (-.2, .2)):
        P.append((x, y, z))
        J.append((0 if y < 1 else 1, 0, 0, 0)); W.append((1, 0, 0, 0))
P = np.array(P, np.float32); J = np.array(J, np.uint16); W = np.array(W, np.float32)
I = []
for r in range(2):
    for k in range(4):
        a, b = r*4+k, r*4+(k+1)%4
        I += [a, b, b+4, a, b+4, a+4]
I = np.array(I, np.uint16)
ibm_cm = np.array([np.identity(4), np.array([[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,-1,0,1]])], np.float32)
times = np.array([0, 0.5, 1.0], np.float32)
s = np.sqrt(.5)
rots = np.array([[0,0,0,1],[0,0,s,s],[0,0,0,1]], np.float32)  # 90 deg about z
blobs = [P.tobytes(), J.tobytes(), W.tobytes(), I.tobytes(), ibm_cm.tobytes(), times.tobytes(), rots.tobytes()]
views, off, data = [], 0, b''
for bl in blobs:
    views.append(dict(buffer=0, byteOffset=len(data), byteLength=len(bl)))
    data += bl + b'\0' * ((-len(bl)) % 4)
acc = [dict(bufferView=0, componentType=5126, count=12, type='VEC3', min=P.min(0).tolist(), max=P.max(0).tolist()),
       dict(bufferView=1, componentType=5123, count=12, type='VEC4'),
       dict(bufferView=2, componentType=5126, count=12, type='VEC4'),
       dict(bufferView=3, componentType=5123, count=len(I), type='SCALAR'),
       dict(bufferView=4, componentType=5126, count=2, type='MAT4'),
       dict(bufferView=5, componentType=5126, count=3, type='SCALAR', min=[0], max=[1]),
       dict(bufferView=6, componentType=5126, count=3, type='VEC4')]
g = dict(asset=dict(version='2.0'), scene=0, scenes=[dict(nodes=[0, 1])],
    nodes=[dict(name='mesh', mesh=0, skin=0), dict(name='b0', children=[2]), dict(name='b1', translation=[0, 1, 0])],
    meshes=[dict(primitives=[dict(attributes=dict(POSITION=0, JOINTS_0=1, WEIGHTS_0=2), indices=3)])],
    skins=[dict(joints=[1, 2], inverseBindMatrices=4)],
    animations=[dict(name='Armature|Walk', samplers=[dict(input=5, output=6)], channels=[dict(sampler=0, target=dict(node=2, path='rotation'))]),
                dict(name='wiggle', samplers=[dict(input=5, output=6)], channels=[dict(sampler=0, target=dict(node=1, path='rotation'))])],
    buffers=[dict(byteLength=len(data), uri='data:application/octet-stream;base64,' + base64.b64encode(data).decode())],
    bufferViews=views, accessors=acc)
path = os.path.join(tempfile.mkdtemp(), 'rig.gltf')
json.dump(g, open(path, 'w'))
import d6mdlio, d6model
b = d6mdlio.import_file(path, scale=1.0)
assert len(b.frames) == 18 and b.anims[6] == (1, 17, 9) and len(b.warnings) == 1, (b.anims, b.warnings)
mid = b.anims[6][2]
assert np.allclose(b.frames[0][8:12], [[.2, 2, -.2], [-.2, 2, -.2], [-.2, 2, .2], [.2, 2, .2]])
assert np.allclose(b.frames[mid][8:12], [[1, .8, -.2], [1, 1.2, -.2], [1, 1.2, .2], [1, .8, .2]], atol=1e-3)
assert np.allclose(b.frames[mid][0:4], b.frames[0][0:4])
m = d6model.Model(d6mdlio.import_file(path).build(), 'rig')
assert m.nframes == 18 and tuple(m.anims[6]) == (1, 17, 9)
print('skin import OK')
