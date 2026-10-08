"""
d6edit - 3D viewport (OpenGL 3.3 core through PyOpenGL inside a QOpenGLWidget).

The engine's world is left-handed (x right, y up, z forward); the view mirrors
the projection so the editor shows the world as the game does (signs and text
read correctly, left is left).

Controls
  right mouse drag      look around (pans in top view)
  W A S D / Q E         move (hold Shift: faster)
  mouse wheel           move forward / back (zoom in top view)
  Ctrl + wheel          rotate the selection (1/32 turn steps)
  left click            select (Shift+click: add / remove)
  left drag on empty    rubber band: select the objects inside (Shift: add)
  left drag on object   move it (and the rest of the selection) over the
                        ground (Alt: up/down)
  drag a gizmo arrow    move along x (red), y (green) or z (blue)
  F                     frame the selection
  T                     toggle top view
  G                     drop the selection onto the ground / floor below it
"""
import ctypes
import math

import numpy as np
from OpenGL import GL
from qtcompat import QtCore, QtGui, QtWidgets, QOpenGLWidget, Signal

import models as models_mod
import walls as walls_mod

MESH_VS = """
#version 330 core
layout(location=0) in vec3 a_pos;
layout(location=1) in vec2 a_uv;
layout(location=2) in float a_layer;
layout(location=3) in float a_light;
uniform mat4 u_mvp;
uniform vec3 u_eye;
out vec2 v_uv; out float v_layer; out float v_light; out float v_dist;
void main(){
  v_uv = a_uv; v_layer = a_layer; v_light = a_light;
  v_dist = length(a_pos - u_eye);
  gl_Position = u_mvp * vec4(a_pos, 1.0);
}"""
MESH_FS = """
#version 330 core
uniform sampler2DArray u_tex;
uniform float u_fog;
uniform float u_bright;
in vec2 v_uv; in float v_layer; in float v_light; in float v_dist;
out vec4 o;
void main(){
  if (v_layer < -0.5) discard;
  vec4 c = texture(u_tex, vec3(v_uv, v_layer));
  if (c.a < 0.5) discard;
  vec3 col = c.rgb * clamp(v_light * u_bright, 0.05, 2.0);
  float f = clamp(v_dist * u_fog, 0.0, 0.85);
  o = vec4(mix(col, vec3(0.32, 0.36, 0.42), f), 1.0);
}"""
MODEL_VS = """
#version 330 core
layout(location=0) in vec3 a_pos;
layout(location=1) in vec2 a_uv;
layout(location=2) in float a_layer;
layout(location=3) in vec3 a_nrm;
layout(location=4) in vec3 i_pos;
layout(location=5) in vec3 i_rot;
uniform mat4 u_mvp;
uniform vec3 u_eye;
out vec2 v_uv; out float v_layer; out float v_light; out float v_dist;
vec3 rot(vec3 v, vec3 r){
  vec3 a = r * 6.28318530718 / 1024.0;
  float c = cos(a.x), s = sin(a.x);
  v = vec3(v.x, v.y * c - v.z * s, v.y * s + v.z * c);      // v . RX
  c = cos(a.z); s = sin(a.z);
  v = vec3(v.x * c - v.y * s, v.x * s + v.y * c, v.z);      // . RZ
  c = cos(a.y); s = sin(a.y);
  return vec3(v.x * c + v.z * s, v.y, -v.x * s + v.z * c);  // . RY
}
void main(){
  vec3 w = rot(a_pos, i_rot) + i_pos;
  vec3 n = rot(a_nrm, i_rot);
  v_uv = a_uv; v_layer = a_layer;
  v_light = 0.55 + 0.6 * abs(dot(normalize(n), normalize(vec3(0.4, 0.8, -0.3))));
  v_dist = length(w - u_eye);
  gl_Position = u_mvp * vec4(w, 1.0);
}"""
LINE_VS = """
#version 330 core
layout(location=0) in vec3 a_pos;
layout(location=1) in vec4 a_col;
uniform mat4 u_mvp;
out vec4 v_col;
void main(){ v_col = a_col; gl_Position = u_mvp * vec4(a_pos, 1.0); }"""
LINE_FS = """
#version 330 core
in vec4 v_col; out vec4 o;
void main(){ o = v_col; }"""

COLORS = {
    'M': (1.0, 0.25, 0.2, 1), 'I': (1.0, 0.9, 0.2, 1), 'P': (0.3, 0.7, 1.0, 1),
    'B': (1.0, 0.5, 0.0, 1), 'N': (0.2, 1.0, 0.4, 1), 'F': (0.2, 0.6, 0.2, 1),
    'L': (1.0, 1.0, 0.5, 1),
    'nav': (0.2, 1.0, 0.4, 1), 'bound': (1.0, 0.3, 1.0, 1), 'sel': (1.0, 1.0, 1.0, 1),
    'sel2': (0.6, 0.85, 1.0, 1),
    'gx': (1.0, 0.25, 0.25, 1), 'gy': (0.3, 1.0, 0.3, 1), 'gz': (0.3, 0.5, 1.0, 1),
}
GIZMO_LEN = 1600.0            # world units of the move gizmo arrows
GIZMO_AXES = {'gx': np.array([1.0, 0, 0]), 'gy': np.array([0, 1.0, 0]), 'gz': np.array([0, 0, 1.0])}
SIZES = {'M': 380, 'I': 160, 'P': 300, 'B': 500, 'N': 120, 'F': 200, 'nav': 120, 'L': 600}
MIRROR = np.diag([-1.0, 1.0, 1.0, 1.0]).astype(np.float32)


def perspective(fovy, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fovy) / 2)
    m = np.zeros((4, 4), np.float32)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1
    return m


def ortho(l, r, b, t, n, f):
    m = np.identity(4, np.float32)
    m[0, 0] = 2 / (r - l); m[1, 1] = 2 / (t - b); m[2, 2] = -2 / (f - n)
    m[0, 3] = -(r + l) / (r - l); m[1, 3] = -(t + b) / (t - b); m[2, 3] = -(f + n) / (f - n)
    return m


def look(eye, fwd, up):
    f = fwd / np.linalg.norm(fwd)
    s = np.cross(f, up); s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.identity(4, np.float32)
    m[0, :3] = s; m[1, :3] = u; m[2, :3] = -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def compile_program(vs, fs):
    p = GL.glCreateProgram()
    for src, kind in ((vs, GL.GL_VERTEX_SHADER), (fs, GL.GL_FRAGMENT_SHADER)):
        s = GL.glCreateShader(kind)
        GL.glShaderSource(s, src)
        GL.glCompileShader(s)
        if not GL.glGetShaderiv(s, GL.GL_COMPILE_STATUS):
            raise RuntimeError(GL.glGetShaderInfoLog(s).decode())
        GL.glAttachShader(p, s)
    GL.glLinkProgram(p)
    if not GL.glGetProgramiv(p, GL.GL_LINK_STATUS):
        raise RuntimeError(GL.glGetProgramInfoLog(p).decode())
    return p


def _interleave(arrays, sizes):
    n = len(arrays[0])
    data = np.concatenate([np.asarray(a, np.float32).reshape(n, s) for a, s in zip(arrays, sizes)], 1)
    return np.ascontiguousarray(data, np.float32)


class Buffer(object):
    """A VAO with interleaved float attributes (locations 0..n-1)."""

    def __init__(self, arrays, sizes, dynamic=False):
        data = _interleave(arrays, sizes)
        self.sizes = sizes
        self.count = len(data)
        self.vao = GL.glGenVertexArrays(1)
        self.vbo = GL.glGenBuffers(1)
        self.inst = None
        GL.glBindVertexArray(self.vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_DYNAMIC_DRAW if dynamic else GL.GL_STATIC_DRAW)
        stride = sum(sizes) * 4
        off = 0
        for i, s in enumerate(sizes):
            GL.glEnableVertexAttribArray(i)
            GL.glVertexAttribPointer(i, s, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(off))
            off += s * 4
        GL.glBindVertexArray(0)

    def update(self, first, arrays):
        """Replace vertices first.. with new data (same layout)."""
        data = _interleave(arrays, self.sizes)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferSubData(GL.GL_ARRAY_BUFFER, first * sum(self.sizes) * 4, data.nbytes, data)

    def set_instances(self, data, sizes):
        """Per-instance attributes after the vertex attributes (divisor 1)."""
        data = np.ascontiguousarray(data, np.float32).reshape(len(data), -1)
        GL.glBindVertexArray(self.vao)
        if self.inst is None:
            self.inst = GL.glGenBuffers(1)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.inst)
            stride = sum(sizes) * 4
            off = 0
            for j, s in enumerate(sizes):
                loc = len(self.sizes) + j
                GL.glEnableVertexAttribArray(loc)
                GL.glVertexAttribPointer(loc, s, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(off))
                GL.glVertexAttribDivisor(loc, 1)
                off += s * 4
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.inst)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, max(data.nbytes, 4), data if data.nbytes else None, GL.GL_DYNAMIC_DRAW)
        GL.glBindVertexArray(0)
        self.ninst = len(data)

    def draw(self, mode):
        if self.count:
            GL.glBindVertexArray(self.vao)
            GL.glDrawArrays(mode, 0, self.count)

    def draw_instanced(self, mode):
        if self.count and getattr(self, 'ninst', 0):
            GL.glBindVertexArray(self.vao)
            GL.glDrawArraysInstanced(mode, 0, self.count, self.ninst)

    def delete(self):
        GL.glDeleteBuffers(1, [self.vbo])
        if self.inst is not None:
            GL.glDeleteBuffers(1, [self.inst])
        GL.glDeleteVertexArrays(1, [self.vao])


def cube_lines(c, h):
    x, y, z = c
    p = [(x - h, y, z - h), (x + h, y, z - h), (x + h, y, z + h), (x - h, y, z + h),
         (x - h, y + 2 * h, z - h), (x + h, y + 2 * h, z - h), (x + h, y + 2 * h, z + h), (x - h, y + 2 * h, z + h)]
    e = [0, 1, 1, 2, 2, 3, 3, 0, 4, 5, 5, 6, 6, 7, 7, 4, 0, 4, 1, 5, 2, 6, 3, 7]
    return [p[i] for i in e]


def box_lines(a, b):
    (x0, y0, z0), (x1, y1, z1) = a, b
    p = [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1),
         (x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)]
    e = [0, 1, 1, 2, 2, 3, 3, 0, 4, 5, 5, 6, 6, 7, 7, 4, 0, 4, 1, 5, 2, 6, 3, 7]
    return [p[i] for i in e]


def obb_lines(corners):
    """corners in obb_corners order (x outer, y, z inner)."""
    idx = lambda x, y, z: x * 4 + y * 2 + z
    e = []
    for y in (0, 1):
        e += [idx(0, y, 0), idx(1, y, 0), idx(1, y, 0), idx(1, y, 1), idx(1, y, 1), idx(0, y, 1), idx(0, y, 1), idx(0, y, 0)]
    for x in (0, 1):
        for z in (0, 1):
            e += [idx(x, 0, z), idx(x, 1, z)]
    return [tuple(corners[i]) for i in e]


class LabelOverlay(QtWidgets.QWidget):
    """Transparent child widget drawing object names over the 3D view."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground)
        self.labels = []
        self.band = None              # rubber band rect (x0, y0, x1, y1)
        self.hud = ''
        self.label_font = QtGui.QFont(self.font())
        self.label_font.setPointSize(8)

    def paintEvent(self, e):
        painter = QtGui.QPainter(self)
        painter.setFont(self.label_font)
        for x, y, txt, sel in self.labels:
            painter.setPen(QtGui.QColor(0, 0, 0))
            painter.drawText(QtCore.QPointF(x + 1, y + 1), txt)
            painter.setPen(QtGui.QColor(255, 255, 0) if sel else QtGui.QColor(255, 255, 210))
            painter.drawText(QtCore.QPointF(x, y), txt)
        if self.band is not None:
            x0, y0, x1, y1 = self.band
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 1, QtCore.Qt.DashLine))
            painter.setBrush(QtGui.QColor(120, 170, 255, 40))
            painter.drawRect(QtCore.QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
        if self.hud:
            painter.setPen(QtGui.QColor(0, 0, 0))
            painter.drawText(QtCore.QPointF(11, self.height() - 9), self.hud)
            painter.setPen(QtGui.QColor(255, 255, 255))
            painter.drawText(QtCore.QPointF(10, self.height() - 10), self.hud)
        painter.end()


class Viewport(QOpenGLWidget):
    selectionChanged = Signal(object)
    objectMoved = Signal(object, object, object)   # obj, old state, new state
    groupMoved = Signal(object)                     # [(obj, old state, new state)]
    statusText = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)
        self.spoke = None
        self.models = None            # models.ModelCache (set by the window)
        self.selected = None          # primary selection (inspector, gizmo)
        self.extra = []               # further selected objects (Shift+click, rubber band)
        self.snap = 0.0               # grid snap for moves and new objects (0 = off)
        self.band = None
        self.eye = np.array([0.0, 20000.0, 0.0])
        self.yaw, self.pitch = 0.0, -0.4
        self.top = False
        self.top_zoom = 60000.0
        self.keys = set()
        self.show = {'M': True, 'I': True, 'P': True, 'B': True, 'N': False, 'nav': True,
                     'bound': True, 'F': True, 'L': False, 'labels': True, 'models': True,
                     'water': True, 'cull': True, 'walls': True, 'canopy': True}
        self.wall_bufs = {}           # chunk -> {'walls': Buffer, 'canopy': Buffer}
        self.exit_boxes = []          # (min, max, label) from the Exits panel
        self.exit_sel = None
        self.wallb = None
        self.brightness = 1.0
        self.front_face = 'ccw'
        self.static = []              # BSP meshes
        self.terrain_buf = None
        self.water_buf = None
        self.model_bufs = {}          # path -> Buffer
        self.lines = None
        self.sel_lines = None
        self.brush_lines = None
        self.brush = None             # (center, radius) of the active terrain tool
        self.tool = None              # active tool (tools.py), None = select / move
        self.tex = None
        self.tex_layers = 0
        self.gl_ready = False
        self.dirty_scene = True
        self.drag = None
        self.last_mouse = None
        self.overlay = LabelOverlay(self)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    # ------------------------------------------------------------- scene
    def set_spoke(self, spoke):
        self.spoke = spoke
        self.selected = None
        self.extra = []
        self.dirty_scene = True
        import d6terrain
        e = d6terrain.ENTRY_POINTS.get((spoke.number, 0))
        if e:
            x, y, z, d = e
            gy = spoke.ground_height(x, z) if spoke.terrain is not None else y
            self.eye = np.array([x, max(y, gy) + 3000.0, z - 6000.0])
            self.yaw, self.pitch = 0.0, -0.35
            self.yaw = math.pi            # look towards +z
        elif spoke.bsps and spoke.bsps[0].mesh:
            p = spoke.bsps[0].mesh['pos']
            c = (p.min(0) + p.max(0)) / 2
            self.eye = c + (0, 4000, 0)
        self.update()

    def model_of(self, o):
        if self.models is None or o.cat != 'object' or o.kind not in 'MIPF' or not self.show.get('models'):
            return None
        if o.kind == 'F':
            return self.models.get('P', o.tree_prop(), self.spoke.texlib)
        return self.models.get(o.kind, o.recno, self.spoke.texlib)

    def _upload_textures(self, force=False):
        layers = self.spoke.texlib.layers
        ver = getattr(self.spoke.texlib, 'version', 0)
        if self.tex is not None and len(layers) == self.tex_layers and not force and ver == getattr(self, 'tex_version', 0):
            return
        self.tex_version = ver
        if self.tex is not None:
            GL.glDeleteTextures([self.tex])
        self.tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, self.tex)
        data = np.ascontiguousarray(np.stack(layers), np.uint8)
        GL.glTexImage3D(GL.GL_TEXTURE_2D_ARRAY, 0, GL.GL_RGBA8, 128, 128, len(layers), 0,
                        GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, data)
        GL.glGenerateMipmap(GL.GL_TEXTURE_2D_ARRAY)
        GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
        GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
        self.tex_layers = len(layers)

    def _upload(self):
        for m in self.static:
            m.delete()
        self.static = []
        for b in (self.terrain_buf, self.water_buf):
            if b is not None:
                b.delete()
        self.terrain_buf = self.water_buf = None
        for d in self.wall_bufs.values():
            for b in d.values():
                if b is not None:
                    b.delete()
        self.wall_bufs = {}
        for b in self.model_bufs.values():
            if b is not None:
                b.delete()
        self.model_bufs = {}
        if self.tex is not None:
            GL.glDeleteTextures([self.tex])
            self.tex = None
        sp = self.spoke
        if sp is None:
            return
        if sp.terrain is not None:
            m = sp.ted.full_mesh()
            self.terrain_buf = Buffer([m['pos'], m['uv'], m['layer'], m['light']], [3, 2, 1, 1], dynamic=True)
            self._upload_water()
            self.wallb = walls_mod.WallBuilder(sp)
            for c in self.wallb.chunks():
                self._build_wall_chunk(c)
        self.bsp_bufs = {}
        for b in sp.bsps:
            if b.mesh:
                m = b.mesh
                buf = Buffer([m['pos'], m['uv'], m['layer'], m['light']], [3, 2, 1, 1])
                self.static.append(buf)
                self.bsp_bufs[b.slot] = buf
        self.rebuild_markers()

    def rebuild_bsp(self, inst):
        """Re-mesh one BSP (after retexturing faces)."""
        if not self.gl_ready:
            return
        self.makeCurrent()
        inst.build_mesh(self.spoke.texlib, getattr(self.spoke, 'water_layers', []))
        old = self.bsp_bufs.pop(inst.slot, None)
        if old is not None:
            self.static.remove(old)
            old.delete()
        if inst.mesh:
            m = inst.mesh
            buf = Buffer([m['pos'], m['uv'], m['layer'], m['light']], [3, 2, 1, 1])
            self.static.append(buf)
            self.bsp_bufs[inst.slot] = buf
        self._upload_textures(force=True)
        self.update()

    def bsp_hit(self, o, d):
        """Distance along the ray o + t*d to the nearest BSP triangle (both
        sides), or None."""
        o = np.asarray(o, np.float64)
        d = np.asarray(d, np.float64)
        best = None
        for b in self.spoke.bsps if self.spoke else []:
            tris = getattr(b, 'tris', None)
            if tris is None:
                continue
            v0 = tris[:, 0]; e1 = tris[:, 1] - v0; e2 = tris[:, 2] - v0
            p = np.cross(d, e2)
            det = (e1 * p).sum(1)
            ok = np.abs(det) > 1e-9
            inv = np.where(ok, 1.0 / np.where(ok, det, 1), 0)
            s = o - v0
            u = (s * p).sum(1) * inv
            q = np.cross(s, e1)
            v = (q * d).sum(1) * inv
            t = (q * e2).sum(1) * inv
            hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-3)
            if hit.any():
                tm = float(t[hit].min())
                if best is None or tm < best:
                    best = tm
        return best

    def pick_bsp_face(self, x, y):
        """(BspInst, face index, distance) under the mouse, or None."""
        o, d = self.ray(x, y)
        best = None
        for b in self.spoke.bsps:
            tris = getattr(b, 'tris', None)
            if tris is None:
                continue
            v0 = tris[:, 0]; e1 = tris[:, 1] - v0; e2 = tris[:, 2] - v0
            p = np.cross(d, e2)
            det = (e1 * p).sum(1)
            ok = np.abs(det) > 1e-9
            inv = np.where(ok, 1.0 / np.where(ok, det, 1), 0)
            s = o - v0
            u = (s * p).sum(1) * inv
            q = np.cross(s, e1)
            v = (q * d).sum(1) * inv
            t = (q * e2).sum(1) * inv
            hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-3)
            if self.show.get('cull'):
                hit &= det < 0 if self.front_face == 'ccw' else det > 0
            if hit.any():
                k = np.nonzero(hit)[0][np.argmin(t[hit])]
                if best is None or t[k] < best[2]:
                    best = (b, int(b.face_of_tri[k]), float(t[k]))
        return best

    def _build_wall_chunk(self, c):
        old = self.wall_bufs.pop(c, None)
        if old:
            for b in old.values():
                if b is not None:
                    b.delete()
        res = self.wallb.build(*c)
        d = {}
        for k, m in res.items():
            d[k] = Buffer([m['pos'], m['uv'], m['layer'], m['light']], [3, 2, 1, 1]) if m is not None else None
        self.wall_bufs[c] = d

    def _upload_water(self):
        if self.water_buf is not None:
            self.water_buf.delete()
            self.water_buf = None
        w = self.spoke.ted.water_mesh()
        if len(w):
            self.water_buf = Buffer([w, np.tile([0.15, 0.35, 0.6, 0.45], (len(w), 1))], [3, 4])

    def terrain_changed(self, rect, water=False):
        """Re-mesh the tile rows of rect (x0, z0, x1, z1) after an edit."""
        if self.terrain_buf is None or not self.gl_ready:
            return
        self.makeCurrent()
        ted = self.spoke.ted
        z0 = max(0, rect[1] - 1)
        z1 = min(ted.D - 1, rect[3] + 1)
        m = ted.mesh_rows(z0, z1)
        self._upload_textures()
        self.terrain_buf.update(z0 * (ted.W - 1) * 6, [m['pos'], m['uv'], m['layer'], m['light']])
        if water:
            self._upload_water()
        if self.wallb is not None:
            for c in self.wallb.chunk_of_rect(rect):
                self._build_wall_chunk(c)
            self._upload_textures()
        self.update()

    def visible(self, o):
        if o.cat == 'object':
            return self.show.get(o.kind, False) and o.kind != '0'
        return self.show.get(o.cat, False)

    def rebuild_markers(self):
        if not self.gl_ready:
            return
        self.makeCurrent()
        if self.lines is not None:
            self.lines.delete()
        pts, cols = [], []
        inst = {}
        sp = self.spoke
        if sp is not None:
            for o in sp.objects:
                if not self.visible(o):
                    continue
                if o.cat == 'bound':
                    a, b = o.box()
                    seg = box_lines(a, b)
                    c = COLORS['bound']
                else:
                    mm = self.model_of(o)
                    if mm is not None and mm.pos is not None:
                        inst.setdefault(mm.path, (mm, []))[1].append(
                            list(o.world_pos()) + list(o.rec.get('rot')))
                        continue
                    k = o.kind if o.cat == 'object' else 'nav'
                    seg = cube_lines(o.world_pos(), SIZES.get(k, 200) / 2)
                    c = COLORS.get(k, (1, 1, 1, 1))
                pts += seg
                cols += [c] * len(seg)
            if self.show.get('nav'):
                for b in sp.bsps:
                    if b.nvs is None:
                        continue
                    for i, p in enumerate(b.nvs.points):
                        a = b.to_world((p.x, p.y, p.z))
                        for l in (p.l0, p.l1, p.l2, p.l3, p.l4, p.l5):
                            if l > i and l < len(b.nvs.points):
                                q = b.nvs.points[l]
                                pts += [a + (0, 60, 0), b.to_world((q.x, q.y, q.z)) + (0, 60, 0)]
                                cols += [(0.2, 0.8, 0.3, 0.8)] * 2
            ng = getattr(sp, 'navgraph', None)
            if ng is not None and (self.show.get('nav') or self.show.get('N')):
                for a, b in ng.cross_links():
                    pts += [a + (0, 80, 0), b + (0, 80, 0)]
                    cols += [(0.2, 0.9, 1.0, 1.0)] * 2
            if self.show.get('N') and sp.nav is not None:
                navs = {o.index: o for o in sp.objects if o.cat == 'object' and o.listkey == ('NAV',)}
                for i, o in navs.items():
                    a = o.world_pos() + (0, 60, 0)
                    for l in np.frombuffer(bytes(o.rec.raw[36:48]), '<u2'):
                        if l > i and int(l) in navs:
                            pts += [a, navs[int(l)].world_pos() + (0, 60, 0)]
                            cols += [(0.2, 0.8, 0.3, 0.8)] * 2
        for k, (a, b, lab) in enumerate(self.exit_boxes):
            c = (1.0, 0.85, 0.1, 1) if k == self.exit_sel else (1.0, 0.55, 0.0, 1)
            for grow in ((0, 0, 0), (30, 30, 30)) if k == self.exit_sel else ((0, 0, 0),):
                seg = box_lines(a - grow, b + grow)
                pts += seg
                cols += [c] * len(seg)
        self.lines = Buffer([np.array(pts), np.array(cols)], [3, 4]) if pts else None
        # model instances
        self._upload_textures()
        for path in list(self.model_bufs):
            if path not in inst and self.model_bufs[path] is not None:
                self.model_bufs[path].set_instances(np.zeros((0, 6)), [3, 3])
        for path, (mm, rows) in inst.items():
            b = self.model_bufs.get(path)
            if b is None:
                b = Buffer([mm.pos, mm.uv, mm.layer, mm.normal], [3, 2, 1, 3])
                self.model_bufs[path] = b
            b.set_instances(np.array(rows, np.float32), [3, 3])
        self._rebuild_sel()
        self.update()

    def obj_box(self, o):
        """Selection outline: oriented box of the model, else a cube."""
        mm = self.model_of(o)
        if mm is not None:
            return obb_lines(models_mod.obb_corners(mm.lo - 20, mm.hi + 20, o.world_pos(), o.rec.get('rot')))
        k = o.kind if o.cat == 'object' else 'nav'
        h = SIZES.get(k, 200) / 2 + 60
        return cube_lines(o.world_pos() - (0, 60, 0), h)

    def selection(self):
        """Selected things, primary first."""
        return ([self.selected] if self.selected is not None else []) + \
            [o for o in self.extra if o is not self.selected]

    def _sel_segments(self, o):
        if o.cat == 'bound':
            a, b = o.box()
            return box_lines(a - 20, b + 20)
        c = o.world_pos()
        seg = self.obj_box(o)
        seg += [c, c - (0, 30000, 0)]          # drop line to see where it stands
        if o.cat == 'object' and o.kind in 'MIP':
            M = models_mod.rot_matrix(o.rec.get('rot'))
            f = np.array([0, 0, 1.0]) @ M     # model +Z = facing
            a = c + (0, 40, 0)
            b = a + f * 700
            side = np.cross(f, (0, 1, 0)) * 150
            seg += [a, b, b, b - f * 200 + side, b, b - f * 200 - side]
        return seg

    def gizmo_ok(self):
        o = self.selected
        return o is not None and o.cat in ('object', 'nav') and not (o.cat == 'object' and o.kind == 'B')

    def _rebuild_sel(self):
        if self.sel_lines is not None:
            self.sel_lines.delete()
            self.sel_lines = None
        sel = self.selection()
        if not sel:
            return
        seg, col = [], []
        for i, o in enumerate(sel):
            sg = self._sel_segments(o)
            seg += sg
            col += [COLORS['sel' if i == 0 else 'sel2']] * len(sg)
        if self.gizmo_ok() and self.tool is None:
            c = self.selected.world_pos()
            for k, u in GIZMO_AXES.items():
                b = c + u * GIZMO_LEN
                side = np.cross(u, (0, 1, 0) if k != 'gy' else (1, 0, 0)) * 120
                g = [c, b, b, b - u * 300 + side, b, b - u * 300 - side]
                seg += g
                col += [COLORS[k]] * len(g)
        self.sel_lines = Buffer([np.array(seg), np.array(col)], [3, 4])

    def set_brush(self, center, radius):
        """Ring on the ground showing the terrain tool's brush (None = hide)."""
        self.brush = None if center is None else (np.array(center), radius)
        if not self.gl_ready:
            return
        self.makeCurrent()
        if self.brush_lines is not None:
            self.brush_lines.delete()
            self.brush_lines = None
        if self.brush is not None:
            c, r = self.brush
            pts = []
            n = 64
            for i in range(n + 1):
                a = 2 * math.pi * i / n
                x, z = c[0] + r * math.cos(a), c[2] + r * math.sin(a)
                pts.append((x, self.spoke.ground_height(x, z) + 40, z))
            seg = []
            for i in range(n):
                seg += [pts[i], pts[i + 1]]
            seg += [tuple(c + (0, 40, 0)), tuple(c + (0, 600, 0))]
            self.brush_lines = Buffer([np.array(seg), np.array([(1, 1, 0.3, 1)] * len(seg))], [3, 4])
        self.update()

    # ------------------------------------------------------------- GL
    def initializeGL(self):
        self.mesh_prog = compile_program(MESH_VS, MESH_FS)
        self.model_prog = compile_program(MODEL_VS, MESH_FS)
        self.line_prog = compile_program(LINE_VS, LINE_FS)
        self.gl_ready = True
        GL.glClearColor(0.32, 0.36, 0.42, 1)

    def view_matrix(self):
        if self.top:
            return look(self.eye, np.array([0, -1.0, 0]), np.array([0, 0, 1.0]))
        return look(self.eye, self.forward(), np.array([0, 1.0, 0]))

    def matrices(self):
        w, h = max(1, self.width()), max(1, self.height())
        if self.top:
            z = self.top_zoom
            a = w / h
            proj = ortho(-z * a / 2, z * a / 2, -z / 2, z / 2, -400000, 400000)
        else:
            proj = perspective(60, w / h, 50, 600000)
        return MIRROR @ proj, self.view_matrix()

    def screen_axes(self):
        """World directions of screen right and screen up (mirrored view)."""
        v = self.view_matrix()
        return -v[0, :3].astype(np.float64), v[1, :3].astype(np.float64)

    def forward(self):
        return np.array([math.sin(self.yaw) * math.cos(self.pitch), math.sin(self.pitch),
                         -math.cos(self.yaw) * math.cos(self.pitch)])

    def _use_mesh_prog(self, prog, mvp):
        GL.glUseProgram(prog)
        GL.glUniformMatrix4fv(GL.glGetUniformLocation(prog, 'u_mvp'), 1, GL.GL_TRUE, mvp)
        GL.glUniform3f(GL.glGetUniformLocation(prog, 'u_eye'), *self.eye)
        GL.glUniform1f(GL.glGetUniformLocation(prog, 'u_fog'), 0.0 if self.top else 1.0 / 250000)
        GL.glUniform1f(GL.glGetUniformLocation(prog, 'u_bright'), self.brightness)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, self.tex)
        GL.glUniform1i(GL.glGetUniformLocation(prog, 'u_tex'), 0)

    def paintGL(self):
        if self.dirty_scene:
            self.dirty_scene = False
            self._upload()
        GL.glViewport(0, 0, int(self.width() * self.devicePixelRatioF()), int(self.height() * self.devicePixelRatioF()))
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_CULL_FACE)
        proj, view = self.matrices()
        mvp = (proj @ view).astype(np.float32)
        if self.tex is not None:
            self._use_mesh_prog(self.mesh_prog, mvp)
            if self.terrain_buf is not None:
                self.terrain_buf.draw(GL.GL_TRIANGLES)
            for d in self.wall_bufs.values():
                for k, b in d.items():
                    if b is not None and self.show.get(k):
                        b.draw(GL.GL_TRIANGLES)
            if self.show.get('cull'):     # BSP walls facing away are not drawn: look into rooms from outside
                GL.glEnable(GL.GL_CULL_FACE)
                GL.glCullFace(GL.GL_BACK)
                GL.glFrontFace(GL.GL_CW if self.front_face == 'cw' else GL.GL_CCW)
            for m in self.static:
                m.draw(GL.GL_TRIANGLES)
            GL.glDisable(GL.GL_CULL_FACE)
            if self.model_bufs:
                self._use_mesh_prog(self.model_prog, mvp)
                for b in self.model_bufs.values():
                    if b is not None:
                        b.draw_instanced(GL.GL_TRIANGLES)
        GL.glUseProgram(self.line_prog)
        GL.glUniformMatrix4fv(GL.glGetUniformLocation(self.line_prog, 'u_mvp'), 1, GL.GL_TRUE, mvp)
        if self.water_buf is not None and self.show.get('water'):
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glDepthMask(GL.GL_FALSE)
            self.water_buf.draw(GL.GL_TRIANGLES)
            GL.glDepthMask(GL.GL_TRUE)
            GL.glDisable(GL.GL_BLEND)
        if self.lines is not None:
            self.lines.draw(GL.GL_LINES)
        GL.glDisable(GL.GL_DEPTH_TEST)
        if self.brush_lines is not None:
            self.brush_lines.draw(GL.GL_LINES)
        if self.sel_lines is not None:
            self.sel_lines.draw(GL.GL_LINES)
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)
        self._labels(mvp)

    def project(self, mvp, p):
        v = mvp @ np.array([p[0], p[1], p[2], 1.0])
        if v[3] <= 1e-3:
            return None
        x = (v[0] / v[3] * 0.5 + 0.5) * self.width()
        y = (1 - (v[1] / v[3] * 0.5 + 0.5)) * self.height()
        return x, y, v[3]

    def _labels(self, mvp):
        sp = self.spoke
        out = []
        if sp is not None and self.show.get('labels'):
            cand = []
            for o in sp.objects:
                if not self.visible(o) or o.cat == 'nav':
                    continue
                if o.cat == 'object' and o.kind == 'F' and o is not self.selected:
                    continue                  # hundreds of trees: no labels
                p = o.world_pos()
                d = np.linalg.norm(p - self.eye)
                if not self.top and d > 40000:
                    continue
                s = self.project(mvp, p + (0, 500, 0))
                if s is None or not (0 <= s[0] < self.width() and 0 <= s[1] < self.height()):
                    continue
                cand.append((d, s, o))
            for a, b, lab in self.exit_boxes:
                p = (a + b) / 2
                s = self.project(mvp, p)
                if s is not None and 0 <= s[0] < self.width() and 0 <= s[1] < self.height():
                    out.append((s[0], s[1], lab, True))
            cand.sort(key=lambda c: c[0])
            fm = QtGui.QFontMetrics(self.overlay.label_font)
            taken = []
            cand = [c for c in cand if c[2] is self.selected] + [c for c in cand if c[2] is not self.selected]
            for d, s, o in cand:
                txt = o.label()
                r = QtCore.QRectF(s[0], s[1] - fm.ascent(), fm.horizontalAdvance(txt), fm.height())
                if any(r.intersects(t) for t in taken):
                    continue
                taken.append(r)
                out.append((s[0], s[1], txt, o is self.selected))
                if len(out) >= 60:
                    break
        self.overlay.labels = out
        self.overlay.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.overlay.setGeometry(self.rect())

    # ------------------------------------------------------------- picking
    def ray(self, x, y):
        proj, view = self.matrices()
        inv = np.linalg.inv((proj @ view).astype(np.float64))
        nx = 2 * x / self.width() - 1
        ny = 1 - 2 * y / self.height()
        a = inv @ np.array([nx, ny, -1, 1.0]); a = a[:3] / a[3]
        b = inv @ np.array([nx, ny, 1, 1.0]); b = b[:3] / b[3]
        d = b - a
        return a, d / np.linalg.norm(d)

    def pick_object(self, x, y, filt=None):
        o0, d = self.ray(x, y)
        best = None
        for o in self.spoke.objects:
            if not self.visible(o) or (filt is not None and not filt(o)):
                continue
            if o.cat == 'bound':
                a, b = o.box()
                t = self._ray_box(o0, d, a, b)
                if t is not None and (best is None or t + 1e6 < best[0]):
                    best = (t + 1e6, o)            # boxes lose against objects
                continue
            mm = self.model_of(o)
            if mm is not None:
                lo_, ld = models_mod.ray_local(o0, d, o.world_pos(), o.rec.get('rot'))
                t = self._ray_box(lo_, ld, mm.lo - 30, mm.hi + 30)
                if t is not None and (best is None or t < best[0]):
                    best = (t, o)
                continue
            k = o.kind if o.cat == 'object' else 'nav'
            c = o.world_pos() + (0, SIZES.get(k, 200) / 2, 0)
            r = SIZES.get(k, 200) * 0.75
            oc = c - o0
            t = oc @ d
            if t < 0:
                continue
            if np.linalg.norm(oc - t * d) < r and (best is None or t < best[0]):
                best = (t, o)
        return best[1] if best else None

    @staticmethod
    def _ray_box(o, d, a, b):
        with np.errstate(divide='ignore', invalid='ignore'):
            t1 = (a - o) / d
            t2 = (b - o) / d
        tmin = np.nanmax(np.minimum(t1, t2))
        tmax = np.nanmin(np.maximum(t1, t2))
        if tmax >= max(tmin, 0):
            return max(tmin, 0)
        return None

    def terrain_hit(self, o, d):
        """Ray against the height map only."""
        sp = self.spoke
        if sp is None or sp.terrain is None:
            return None
        t, step, prev = 0.0, 150.0, None
        for i in range(6000):
            p = o + d * t
            above = bool(p[1] > sp.ground_height(p[0], p[2]))
            if prev is True and not above:
                lo, hi = t - step, t          # refine
                for _ in range(12):
                    mid = (lo + hi) / 2
                    q = o + d * mid
                    if q[1] > sp.ground_height(q[0], q[2]):
                        lo = mid
                    else:
                        hi = mid
                return o + d * hi
            prev = above
            t += step
            step = min(step * 1.01, 1500)
            if t > 800000:
                break
        if self.top and abs(d[1]) > 1e-6:         # top view looking at void: hit y=0
            return o + d * ((0 - o[1]) / d[1])
        return None

    def ground_hit(self, o, d):
        """First hit of a ray with BSP triangles or terrain; None if nothing."""
        best = None
        for b in self.spoke.bsps:
            if getattr(b, 'tris', None) is None:
                continue
            t = ray_tris(o, d, b.tris)
            if t is not None and (best is None or t < best):
                best = t
        h = self.terrain_hit(o, d)
        if h is not None:
            t = float(np.linalg.norm(h - o))
            if best is None or t < best:
                best = t
        return None if best is None else o + d * best

    # ------------------------------------------------------------- input
    def mousePressEvent(self, e):
        self.setFocus()
        self.last_mouse = e.position()
        if self.spoke is None:
            return
        if self.tool is not None and e.button() == QtCore.Qt.LeftButton:
            self.tool.press(self, e)
            return
        if e.button() != QtCore.Qt.LeftButton:
            return
        x, y = e.position().x(), e.position().y()
        add = bool(e.modifiers() & QtCore.Qt.ShiftModifier)
        axis = self.gizmo_pick(x, y) if not add else None
        if axis is not None:
            self._start_drag(self.selected, axis=axis, x=x, y=y)
            return
        o = self.pick_object(x, y)
        if o is None:
            if not add:
                self.select(None)
            self.band = (x, y, x, y)
            return
        if add:
            self.select(o, add=True)
            return
        if o not in self.selection():
            self.select(o)
        elif o is not self.selected:
            self.select(o, keep=True)           # make it primary, keep the group
        if not (o.cat == 'object' and o.kind == 'B'):
            self._start_drag(o)

    def _start_drag(self, o, axis=None, x=None, y=None):
        group = [g for g in self.selection() if g is not o and g.cat in ('object', 'nav')
                 and not (g.cat == 'object' and g.kind == 'B')]
        self.drag = dict(obj=o, start=snapshot(o), plane_y=o.world_pos()[1], grab=None, moved=False,
                         axis=axis, origin=o.world_pos().copy(),
                         group=[(g, snapshot(g), g.world_pos().copy()) for g in group])
        if axis is not None:
            self.drag['grab'] = self._axis_point(x, y, self.drag['origin'], GIZMO_AXES[axis])

    def _axis_point(self, x, y, a, u):
        """Parameter along axis a + s*u closest to the mouse ray."""
        r0, d = self.ray(x, y)
        w0 = a - r0
        b = float(u @ d)
        c = float(d @ d)
        den = c - b * b                # u is unit length
        if abs(den) < 1e-9:
            return 0.0
        return (b * float(d @ w0) - c * float(u @ w0)) / den

    def gizmo_pick(self, x, y):
        if not self.gizmo_ok() or self.tool is not None:
            return None
        proj, view = self.matrices()
        mvp = proj @ view
        c = self.selected.world_pos()
        a = self.project(mvp, c)
        if a is None:
            return None
        best = None
        for k, u in GIZMO_AXES.items():
            b = self.project(mvp, c + u * GIZMO_LEN)
            if b is None:
                continue
            ax, ay, bx, by = a[0], a[1], b[0], b[1]
            vx, vy = bx - ax, by - ay
            L2 = vx * vx + vy * vy
            if L2 < 25:
                continue
            t = max(0.15, min(1.0, ((x - ax) * vx + (y - ay) * vy) / L2))   # not the shared centre
            dist = math.hypot(ax + vx * t - x, ay + vy * t - y)
            if dist < 8 and (best is None or dist < best[0]):
                best = (dist, k)
        return best[1] if best else None

    def snap_point(self, p, y_too=False):
        if self.snap <= 0:
            return p
        q = np.array(p, np.float64)
        q[0] = round(q[0] / self.snap) * self.snap
        q[2] = round(q[2] / self.snap) * self.snap
        if y_too:
            q[1] = round(q[1] / self.snap) * self.snap
        return q

    def mouseMoveEvent(self, e):
        p = e.position()
        if self.last_mouse is None:
            self.last_mouse = p
        dx, dy = p.x() - self.last_mouse.x(), p.y() - self.last_mouse.y()
        self.last_mouse = p
        if e.buttons() & QtCore.Qt.RightButton:
            if self.top:
                s = self.top_zoom / max(1, self.height())
                r, u = self.screen_axes()
                self.eye = self.eye - r * dx * s + u * dy * s
            else:
                self.yaw -= dx * 0.005          # mirrored view: screen right is -s
                self.pitch = max(-1.5, min(1.5, self.pitch - dy * 0.005))
            self.update()
            if self.tool is not None:
                self.tool.hover(self, e)
        elif self.tool is not None:
            if e.buttons() & QtCore.Qt.LeftButton:
                self.tool.drag(self, e)
            else:
                self.tool.hover(self, e)
        elif self.band is not None and e.buttons() & QtCore.Qt.LeftButton:
            self.band = (self.band[0], self.band[1], p.x(), p.y())
            self.overlay.band = self.band
            self.overlay.update()
        elif self.drag is not None and e.buttons() & QtCore.Qt.LeftButton:
            self._drag_to(p.x(), p.y(), e.modifiers(), dy)

    def _drag_to(self, x, y, mods, dy):
        dr = self.drag
        o = dr['obj']
        cur = o.world_pos()
        if dr['axis'] is not None:
            u = GIZMO_AXES[dr['axis']]
            s = self._axis_point(x, y, dr['origin'], u) - dr['grab']
            new = dr['origin'] + u * s
            if self.snap > 0:
                k = int(np.argmax(np.abs(u)))
                new[k] = round(new[k] / self.snap) * self.snap
        elif mods & QtCore.Qt.AltModifier:
            new = cur + np.array([0, -dy * max(20.0, np.linalg.norm(cur - self.eye) / 400), 0])
        else:
            r0, d = self.ray(x, y)
            if abs(d[1]) < 1e-6:
                return
            t = (dr['plane_y'] - r0[1]) / d[1]
            if t <= 0:
                return
            hit = r0 + d * t
            if dr['grab'] is None:
                dr['grab'] = cur - hit
            new = self.snap_point(hit + dr['grab'])
            new[1] = cur[1]

        def ground_rule(ob, q):
            if ob.cat == 'object' and ob.bsp is None and ob.kind in 'MIPNF' and ob.local_pos()[1] == 0 \
                    and dr['axis'] != 'gy':
                q[1] = 0       # keep "stand on the ground" objects at y 0 (the game snaps them)
            return q
        new = ground_rule(o, new)
        o.set_world_pos(new)
        delta = o.world_pos() - dr['origin']
        for g, _, p0 in dr['group']:
            g.set_world_pos(ground_rule(g, p0 + delta))
        dr['moved'] = True
        self.rebuild_markers()

    def mouseReleaseEvent(self, e):
        if self.tool is not None and e.button() == QtCore.Qt.LeftButton:
            self.tool.release(self, e)
            return
        if self.band is not None and e.button() == QtCore.Qt.LeftButton:
            b = self.band
            self.band = None
            self.overlay.band = None
            self.overlay.update()
            if abs(b[2] - b[0]) > 4 or abs(b[3] - b[1]) > 4:
                self.select_rect(b, add=bool(e.modifiers() & QtCore.Qt.ShiftModifier))
            return
        if self.drag is not None and e.button() == QtCore.Qt.LeftButton:
            dr = self.drag
            self.drag = None
            if dr['moved']:
                if dr['group']:
                    self.groupMoved.emit([(dr['obj'], dr['start'], snapshot(dr['obj']))] +
                                         [(g, s0, snapshot(g)) for g, s0, _ in dr['group']])
                else:
                    self.objectMoved.emit(dr['obj'], dr['start'], snapshot(dr['obj']))

    def select_rect(self, b, add=False):
        """Select the visible objects (not BSPs, trees only when shown) whose
        position projects into the screen rectangle b."""
        x0, x1 = sorted((b[0], b[2]))
        y0, y1 = sorted((b[1], b[3]))
        proj, view = self.matrices()
        mvp = proj @ view
        hits = []
        for o in self.spoke.objects:
            if not self.visible(o) or o.cat == 'bound' or (o.cat == 'object' and o.kind in 'B0'):
                continue
            s = self.project(mvp, o.world_pos())
            if s is not None and x0 <= s[0] <= x1 and y0 <= s[1] <= y1:
                hits.append(o)
        if not add:
            self.selected, self.extra = None, []
        for o in hits:
            if o not in self.selection():
                if self.selected is None:
                    self.selected = o
                else:
                    self.extra.append(o)
        self.select(self.selected, keep=True)
        self.statusText.emit('%d selected' % len(self.selection()))

    def wheelEvent(self, e):
        d = e.angleDelta().y() / 120.0
        if e.modifiers() & QtCore.Qt.ControlModifier and self.selected is not None \
                and self.selected.cat == 'object' and self.tool is None:
            ch = []
            for o in self.selection():
                if o.cat != 'object':
                    continue
                try:
                    r = list(o.rec.get('rot'))
                except Exception:
                    continue
                start = snapshot(o)
                r[1] = (r[1] + (32 if d > 0 else -32)) % 1024
                o.rec.set('rot', r)
                ch.append((o, start, snapshot(o)))
            self.rebuild_markers()
            if len(ch) == 1:
                self.objectMoved.emit(*ch[0])
            elif ch:
                self.groupMoved.emit(ch)
            return
        if self.tool is not None and e.modifiers() & QtCore.Qt.ControlModifier:
            self.tool.wheel(self, d)
            return
        if self.top:
            self.top_zoom = max(2000.0, self.top_zoom * (0.85 ** d))
        else:
            self.eye += self.forward() * d * (3000 if e.modifiers() & QtCore.Qt.ShiftModifier else 800)
        self.update()

    def keyPressEvent(self, e):
        k = e.key()
        if k == QtCore.Qt.Key_T:
            self.top = not self.top
            self.update()
        elif k == QtCore.Qt.Key_F:
            self.frame_selection()
        elif k == QtCore.Qt.Key_G:
            self.drop_selection()
        else:
            self.keys.add(k)
        if e.modifiers() & QtCore.Qt.ShiftModifier:
            self.keys.add('shift')
        else:
            self.keys.discard('shift')

    def keyReleaseEvent(self, e):
        if not e.isAutoRepeat():
            self.keys.discard(e.key())
        if not (e.modifiers() & QtCore.Qt.ShiftModifier):
            self.keys.discard('shift')

    def focusOutEvent(self, e):
        self.keys.clear()

    def _tick(self):
        if not self.keys:
            return
        sp = 2500.0 if 'shift' in self.keys else 500.0
        if self.top:
            sp *= self.top_zoom / 60000.0
        r, u = self.screen_axes()
        f = u if self.top else self.forward()
        if not self.top:
            r = np.array([r[0], 0, r[2]])
            r /= max(1e-6, np.linalg.norm(r))
        m = np.zeros(3)
        K = QtCore.Qt
        if K.Key_W in self.keys: m += f
        if K.Key_S in self.keys: m -= f
        if K.Key_D in self.keys: m += r
        if K.Key_A in self.keys: m -= r
        if K.Key_E in self.keys: m += (0, 1, 0)
        if K.Key_Q in self.keys: m -= (0, 1, 0)
        if m.any():
            self.eye += m * sp
            self.update()

    # ------------------------------------------------------------- actions
    def select(self, o, add=False, keep=False):
        """Select o. add: toggle o in the selection (Shift+click). keep: make o
        the primary selection and keep the others."""
        if add and o is not None:
            if o in self.selection():
                rest = [x for x in self.selection() if x is not o]
                self.selected = rest[0] if rest else None
                self.extra = rest[1:]
            elif self.selected is None:
                self.selected = o
            else:
                self.extra.append(o)
        elif keep:
            if o is not None and o is not self.selected:
                rest = [x for x in self.selection() if x is not o]
                self.selected, self.extra = o, rest
        else:
            self.selected = o
            self.extra = []
        if self.gl_ready:
            self.makeCurrent()
            self._rebuild_sel()
        self.selectionChanged.emit(self.selected)
        self.update()

    def frame_selection(self):
        sel = self.selection()
        if not sel:
            return
        p = np.mean([o.world_pos() for o in sel], axis=0)
        if self.top:
            self.eye = np.array([p[0], self.eye[1], p[2]])
        else:
            self.eye = p - self.forward() * 5000 + (0, 800, 0)
        self.update()

    def drop_selection(self):
        ch = []
        for o in self.selection():
            if o.cat == 'bound':
                continue
            start = snapshot(o)
            p = o.world_pos()
            hit = self.ground_hit(p + (0, 200, 0), np.array([0, -1.0, 0]))
            if hit is None:
                continue
            p[1] = hit[1]
            o.set_world_pos(p)
            ch.append((o, start, snapshot(o)))
        self.rebuild_markers()
        if len(ch) == 1:
            self.objectMoved.emit(*ch[0])
        elif ch:
            self.groupMoved.emit(ch)

    def center_point(self):
        """Ground point in front of the camera (for adding objects)."""
        hit = self.ground_hit(self.eye, self.forward() if not self.top else np.array([0, -1.0, 0]))
        if hit is None:
            hit = self.eye + self.forward() * 3000
        return hit


def ray_tris(o, d, tris):
    """Moller-Trumbore over an (n,3,3) array; nearest positive t or None."""
    v0 = tris[:, 0]; e1 = tris[:, 1] - v0; e2 = tris[:, 2] - v0
    p = np.cross(d, e2)
    det = (e1 * p).sum(1)
    ok = np.abs(det) > 1e-9
    inv = np.where(ok, 1.0 / np.where(ok, det, 1), 0)
    s = o - v0
    u = (s * p).sum(1) * inv
    q = np.cross(s, e1)
    v = (q * d).sum(1) * inv
    t = (q * e2).sum(1) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-3)
    if not hit.any():
        return None
    return float(t[hit].min())


def snapshot(o):
    """Restorable copy of an editable thing."""
    if o.cat == 'nav':
        return ('nav', o.pt)
    return ('raw', bytes(o.rec.raw))


def restore(o, snap):
    if snap[0] == 'nav':
        o.bsp.nvs.points[o.index] = snap[1]
    else:
        o.rec.raw[:] = snap[1]
