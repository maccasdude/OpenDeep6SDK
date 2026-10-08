"""
d6edit - data model.

A Spoke is one area of the game world (0..12): optional heightmap terrain plus
BSP levels, placed objects, nav points and the per-spoke scripting tables.
Everything is kept in the original record objects of the formats/ libraries so saving
writes byte-identical files except for what was edited.

Coordinates: world units, x/z horizontal, y up, 1 terrain tile = 1024 units,
1 BSP unit = 16 world units.
"""
import os
import shutil
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'formats'))

import d6level   # noqa: E402
import d6terrain  # noqa: E402
import d6data    # noqa: E402
import d6events  # noqa: E402
import terrain as terrain_mod  # noqa: E402

TILE = 1024.0
BSP_SCALE = 16.0
TERRAIN_SPOKES = (0, 3, 11)


def find(root, rel):
    return d6terrain.find_ci(root, rel)


def read(root, rel):
    p = find(root, rel)
    if not p:
        return None
    with open(p, 'rb') as f:
        return f.read()


# ---------------------------------------------------------------------------
# global game databases (names for the inspector)
# ---------------------------------------------------------------------------
class GameData(object):
    def __init__(self, root):
        self.root = root
        self.mons = d6data.MonsTable.parse(read(root, 'D6MONS.DAT'))
        self.items = d6data.ItemTable.parse(read(root, 'D6ITEM.DAT'))
        self.props = d6data.PropTable.parse(read(root, 'D6PROP.DAT'))
        self.events = d6events.EventSet(read(root, 'EVENTS.DCL'), read(root, 'EVENTS.COD'))
        self.dcl = self.events.dcl
        self.db = {'D6MONS.DAT': self.mons, 'D6ITEM.DAT': self.items, 'D6PROP.DAT': self.props}
        self.db_dirty = set()
        self.db_orig = {}

    DB_CLASSES = {'D6MONS.DAT': d6data.MonsTable, 'D6ITEM.DAT': d6data.ItemTable, 'D6PROP.DAT': d6data.PropTable,
                  'D6NPC.DAT': d6data.NpcTable, 'D6TREAS.DAT': d6data.TreasTable,
                  'D6TRLIST.DAT': d6data.TrListTable, 'D6MONSND.DAT': d6data.MonSndTable}

    def table(self, fname):
        """A game database (parsed once, shared)."""
        if fname not in self.db:
            data = read(self.root, fname)
            self.db[fname] = self.DB_CLASSES[fname].parse(data) if data else None
        t = self.db[fname]
        if t is not None and fname not in self.db_orig:
            self.db_orig[fname] = [bytes(r.raw) for r in t.records]
        return t

    def text_file(self, fname):
        """TEXTPAK.000 / D6STRING.DAT (parsed once, saved with the databases)."""
        if fname not in self.db:
            data = read(self.root, fname)
            cls = d6data.TextPak if fname.upper().startswith('TEXTPAK') else d6data.StringTable
            self.db[fname] = cls.parse(data) if data else None
        return self.db[fname]

    def message(self, tid):
        t = self.text_file('TEXTPAK.000')
        return t.text(tid) if t is not None else None

    def save_db(self):
        bdir = os.path.join(self.root, 'd6edit_backup', time.strftime('%Y%m%d-%H%M%S'))
        os.makedirs(bdir, exist_ok=True)
        out = []
        for fname in sorted(self.db_dirty):
            data = self.db[fname].build()
            path = find(self.root, fname) or os.path.join(self.root, fname)
            if os.path.exists(path):
                shutil.copy2(path, os.path.join(bdir, os.path.basename(path)))
            with open(path + '.d6edit-tmp', 'wb') as f:
                f.write(data)
            os.replace(path + '.d6edit-tmp', path)
            out.append(path)
        self.db_dirty.clear()
        return out

    def name(self, kind, n):
        tab = {'M': self.mons, 'I': self.items, 'P': self.props}.get(kind)
        if tab is None or n is None or not (1 <= n <= len(tab)):
            return ''
        try:
            return tab[n].name.strip()
        except Exception:
            return ''

    def save_events(self):
        """Write EVENTS.COD / EVENTS.DCL (backups in d6edit_backup)."""
        bdir = os.path.join(self.root, 'd6edit_backup', time.strftime('%Y%m%d-%H%M%S'))
        os.makedirs(bdir, exist_ok=True)
        out = []
        for name, data in (('EVENTS.COD', self.events.build_cod()), ('EVENTS.DCL', self.events.build_dcl())):
            path = find(self.root, name) or os.path.join(self.root, name)
            if os.path.exists(path):
                shutil.copy2(path, os.path.join(bdir, os.path.basename(path)))
            with open(path + '.d6edit-tmp', 'wb') as f:
                f.write(data)
            os.replace(path + '.d6edit-tmp', path)
            out.append(path)
        self.events._dcl_raw = self.events.build_dcl()
        self.events.dirty = False
        return out

    def names(self, kind):
        tab = {'M': self.mons, 'I': self.items, 'P': self.props}.get(kind)
        if tab is None:
            return []
        return [(i, self.name(kind, i)) for i in range(1, len(tab) + 1)]


# ---------------------------------------------------------------------------
# texture library: every 128x128 texture of the spoke as RGBA layers
# ---------------------------------------------------------------------------
class TextureLibrary(object):
    SIZE = 128

    def __init__(self):
        self.layers = []          # list of (128,128,4) uint8
        self.keys = {}

    def add(self, key, rgb):
        if key in self.keys:
            return self.keys[key]
        a = np.asarray(rgb, np.uint8)
        if a.shape[0] != self.SIZE or a.shape[1] != self.SIZE:
            from PIL import Image
            rgb = np.asarray(Image.fromarray(np.ascontiguousarray(a[..., :3])).resize((self.SIZE, self.SIZE), Image.BILINEAR))
            if a.shape[2] == 4:      # keep the transparency mask (nearest, stays crisp)
                al = np.asarray(Image.fromarray(np.ascontiguousarray(a[..., 3])).resize((self.SIZE, self.SIZE), Image.NEAREST))
                a = np.dstack([rgb, al])
            else:
                a = rgb
        if a.shape[2] == 3:
            a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, np.uint8)], 2)
        self.keys[key] = len(self.layers)
        self.layers.append(np.ascontiguousarray(a))
        return self.keys[key]

    def _prep(self, rgb):
        a = np.asarray(rgb, np.uint8)
        if a.shape[0] != self.SIZE or a.shape[1] != self.SIZE:
            from PIL import Image
            a = np.asarray(Image.fromarray(np.ascontiguousarray(a[..., :3])).resize((self.SIZE, self.SIZE), Image.BILINEAR))
        if a.shape[2] == 3:
            a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, np.uint8)], 2)
        return np.ascontiguousarray(a)

    def replace(self, key, rgb):
        """New pixels for an existing layer (the view re-uploads on version change)."""
        if key in self.keys:
            self.layers[self.keys[key]] = self._prep(rgb)
            self.version = getattr(self, 'version', 0) + 1
            return self.keys[key]
        return self.add(key, rgb)


# ---------------------------------------------------------------------------
# one BSP level placed in the spoke
# ---------------------------------------------------------------------------
class BspInst(object):
    def __init__(self, spoke, slot, name, origin, placement):
        self.spoke = spoke
        self.slot = slot
        self.name = name
        self.origin = np.array(origin, np.float64)
        self.placement = placement
        root = spoke.root
        self.level = d6level.Level(root, name)
        self.bsp = self.level.bsp
        self.twd = self.level.twd
        self.nvs = self.level.nvs
        bol = read(root, name + '.bol')
        self.bol_path = find(root, name + '.bol')
        self.bol = d6data.ObjList.parse(bol) if bol else None
        self.nvs_path = self.level.paths.get('nvs')
        self.mesh = None

    def to_world(self, p):
        return self.origin + np.asarray(p, np.float64) * BSP_SCALE

    def to_bsp(self, w):
        return (np.asarray(w, np.float64) - self.origin) / BSP_SCALE

    def build_mesh(self, texlib, water_layers):
        """Triangles in world space: pos, uv, layer, light. Also keeps a
        numpy copy of all triangles for picking."""
        b = self.bsp
        tex_layers = []
        if self.twd:
            for i in range(len(self.twd.textures)):
                w, h, rgb = self.twd.texture_rgb(i)
                arr = np.frombuffer(rgb, np.uint8).reshape(h, w, 3)
                tex_layers.append(texlib.add(('bsp', self.name, i), arr))
        verts = np.array([(v.x, v.y, v.z) for v in b.vertices], np.float64)
        light = np.array(b.vertex_light, np.float32) if len(b.vertex_light) else None
        # faces of the world model and of door models (all drawn)
        pos, uv, lay, lit = [], [], [], []
        face_of_tri = []
        for fi, f in enumerate(b.faces):
            ti = b.texinfo[f.texinfo]
            if ti.flags < 0:
                continue
            if ti.flags & d6level.TEXF_WATER:
                layer = water_layers[0] if water_layers else 0
            elif ti.flags & d6level.TEXF_LAVA:
                layer = water_layers[1] if len(water_layers) > 1 else 0
            else:
                ix = ti.flags & d6level.TEXF_INDEX_MASK
                layer = tex_layers[ix] if ix < len(tex_layers) else 0
            idx = b.face_vertex_indices(f)
            n = len(idx)
            if n < 3:
                continue
            P = verts[idx]
            d = P - np.array([ti.ox, ti.oy, ti.oz])
            u = (d @ np.array([ti.sx, ti.sy, ti.sz]) + ti.soff * 0.25) / 32.0
            v = (d @ np.array([ti.tx, ti.ty, ti.tz]) + ti.toff * 0.25) / 32.0
            if light is not None and f.firstedge + n <= len(light):
                L = light[f.firstedge:f.firstedge + n] / 31.0
            else:
                L = np.ones(n, np.float32)
            for k in range(1, n - 1):
                for j in (0, k, k + 1):
                    pos.append(P[j])
                    uv.append((u[j], v[j]))
                    lay.append(layer)
                    lit.append(L[j])
                face_of_tri.append(fi)
        if not pos:
            self.mesh = None
            return
        pos = self.to_world(np.array(pos))
        self.mesh = dict(pos=pos.astype(np.float32), uv=np.array(uv, np.float32),
                         layer=np.array(lay, np.float32), light=np.array(lit, np.float32))
        self.tris = pos.reshape(-1, 3, 3)
        self.face_of_tri = np.array(face_of_tri)


# ---------------------------------------------------------------------------
# editable things
# ---------------------------------------------------------------------------
class EdObject(object):
    """A placed object record (TOL or BOL): monster, item, prop, BSP placement,
    foliage or terrain nav point (also stored as a 64 byte record)."""
    cat = 'object'

    def __init__(self, spoke, listkey, index, rec, bsp=None):
        self.spoke = spoke
        self.listkey = listkey      # ('TOL',) ('BOL', slot) ('NAV',) ('FOL',)
        self.index = index          # 1-based record index (= object tag)
        self.rec = rec
        self.bsp = bsp

    # record field helpers (raw layout shared by all 64 byte lists)
    @property
    def kind(self):
        return chr(self.rec.raw[0]) if self.rec.raw[0] else '0'

    @property
    def recno(self):
        try:
            return int(bytes(self.rec.raw[1:4]).decode('ascii'))
        except ValueError:
            return None

    def local_pos(self):
        return np.frombuffer(bytes(self.rec.raw[4:16]), '<f4').astype(np.float64)

    def set_local_pos(self, p):
        self.rec.raw[4:16] = np.asarray(p, '<f4').tobytes()

    def world_pos(self):
        p = self.local_pos()
        if self.bsp is not None:
            p = self.bsp.origin + p          # BOL positions are world units relative to the BSP origin
        if self.kind in 'MIPNF' and p[1] == 0 and self.bsp is None and self.spoke.terrain is not None:
            p = p.copy()
            p[1] = self.spoke.ground_height(p[0], p[2])
        return p

    def set_world_pos(self, w):
        w = np.asarray(w, np.float64).copy()
        if self.bsp is not None:
            w = w - self.bsp.origin
        self.set_local_pos(w)

    def label(self):
        k = self.kind
        if k == 'B':
            return 'BSP %s' % self.rec.bspname
        if k in 'MIP':
            n = self.recno
            return '%s%03d %s' % (k, n or 0, self.spoke.game.name(k, n))
        if k == 'N':
            return 'Nav %d' % self.index
        if k == 'F':
            p = self.tree_prop()
            return 'Tree %d (%s)' % (self.index, self.spoke.game.name('P', p) if p else '?')
        if k == 'L':
            return 'Light %d' % self.index
        return 'empty'

    def tree_prop(self):
        """Foliage: the game picks the tree type at random each load (clock
        seeded); the editor shows a stable choice by record index."""
        import d6walls
        tab = d6walls.foliage_table(self.spoke.number)
        return tab[self.index % len(tab)]

    def key(self):
        return ('obj',) + tuple(self.listkey) + (self.index,)


class EdNav(object):
    """Nav point of a BSP (.nvs)."""
    cat = 'nav'

    def __init__(self, spoke, bsp, index):
        self.spoke, self.bsp, self.index = spoke, bsp, index

    @property
    def pt(self):
        return self.bsp.nvs.points[self.index]

    def world_pos(self):
        p = self.pt
        return self.bsp.to_world((p.x, p.y, p.z))

    def set_world_pos(self, w):
        b = self.bsp.to_bsp(w)
        self.bsp.nvs.points[self.index] = self.pt._replace(x=float(b[0]), y=float(b[1]), z=float(b[2]))

    def label(self):
        return 'Nav %d (id %d)' % (self.index, self.pt.id)

    def key(self):
        return ('nav', self.bsp.slot, self.index)


class EdBound(object):
    """Boundary box (D6Boun): enter/leave box that sets a state or fires a trigger."""
    cat = 'bound'

    def __init__(self, spoke, index, rec):
        self.spoke, self.index, self.rec = spoke, index, rec

    def _org(self):
        b = self.rec.get('bsp')
        inst = self.spoke.bsp_by_slot(b) if b is not None and b >= 0 else None
        return inst.origin if inst is not None else np.zeros(3)

    def box(self):
        o = self._org()
        return o + np.array(self.rec.get('min')), o + np.array(self.rec.get('max'))

    def world_pos(self):
        a, b = self.box()
        return (a + b) / 2

    def set_world_pos(self, w):
        a, b = self.box()
        d = np.asarray(w) - (a + b) / 2
        self.rec.set('min', tuple(float(x) for x in np.array(self.rec.get('min')) + d))
        self.rec.set('max', tuple(float(x) for x in np.array(self.rec.get('max')) + d))

    def label(self):
        t = self.rec.get('trigger')
        return 'Box %d -> %s' % (self.index, ('T%d' % t) if t and t > 0 else 'state %d' % self.rec.get('state'))

    def key(self):
        return ('bound', self.index)


# ---------------------------------------------------------------------------
# the spoke
# ---------------------------------------------------------------------------
class Spoke(object):
    TABLES = [('trig', 'D6Trig%02d.dat', d6data.TriggerTable),
              ('boun', 'D6Boun%02d.dat', d6data.BoundTable),
              ('spec', 'D6Spec%02d.dat', d6data.SpecialTable),
              ('swit', 'D6Swit%02d.dat', d6data.SwitchTable),
              ('trap', 'D6Trap%02d.dat', d6data.TrapTable),
              ('link', 'D6Link%02d.dat', d6data.LinkTable)]

    def __init__(self, game, number, progress=None):
        self.game = game
        self.root = game.root
        self.number = number
        self.dirty = set()
        self.progress = progress or (lambda msg: None)
        self.terrain = None
        self.tol = self.nav = self.fol = self.lit = None
        self.ted = None
        self.bsps = []
        self.texlib = TextureLibrary()
        self.texlib.add(('white',), np.full((128, 128, 3), 200, np.uint8))
        self._load()

    # --------------------------------------------------------------- loading
    def _load(self):
        n = self.number
        r = self.root
        if n in TERRAIN_SPOKES:
            self.progress('terrain')
            self.tmr_path = find(r, 'SPOKE%02d.TMR' % n)
            self.terrain = d6terrain.TerrainMap(open(self.tmr_path, 'rb').read())
            self.tol_path = find(r, 'SPOKE%02d.TOL' % n)
            self.tol = d6data.ObjList.parse(open(self.tol_path, 'rb').read())
            self.nav_path = find(r, 'SPOKE%02d.NAV' % n)
            self.nav = d6data.ObjList.parse(open(self.nav_path, 'rb').read()) if self.nav_path else None
            self.fol_path = find(r, 'SPOKE%02d.FOL' % n)
            self.fol = d6data.ObjList.parse(open(self.fol_path, 'rb').read()) if self.fol_path else None
            self.lit_path = find(r, 'SPOKE%02d.LIT' % n)      # editor-only light list (baking)
            self.lit = d6data.ObjList.parse(open(self.lit_path, 'rb').read()) if self.lit_path else None
        water = []
        sw = find(r, 'syswat.twd')
        if sw:
            wad = d6level.TexWad.load(sw)
            for i in range(len(wad.textures)):
                w, h, rgb = wad.texture_rgb(i)
                water.append(self.texlib.add(('water', i), np.frombuffer(rgb, np.uint8).reshape(h, w, 3)))
        self.water_layers = water
        for pl in d6level.spoke_bsps(n, r):
            self.progress('BSP %s' % pl['name'])
            try:
                inst = BspInst(self, pl['slot'], pl['name'], pl['origin'], pl)
                inst.build_mesh(self.texlib, water)
                self.bsps.append(inst)
            except Exception as e:   # keep going: one broken level should not stop the editor
                print('d6edit: cannot load BSP %s: %s' % (pl['name'], e))
        self.tables = {}
        for key, pat, cls in self.TABLES:
            p = find(r, pat % n)
            if p:
                self.tables[key] = (p, cls.parse(open(p, 'rb').read()))
        if self.terrain is not None:
            self.progress('terrain textures')
            if not hasattr(self.game, 'tileset'):
                self.game.tileset = terrain_mod.TileSet(r)
            self.ted = terrain_mod.Terrain(self, self.terrain, self.game.tileset)
            self.ted.refresh_layers()
        self.build_objects()

    def build_objects(self):
        self.objects = []
        if self.tol is not None:
            for i, rec in enumerate(self.tol.records, 1):
                self.objects.append(EdObject(self, ('TOL',), i, rec))
        if self.nav is not None:
            for i, rec in enumerate(self.nav.records, 1):
                if rec.raw[0] == ord('N'):
                    self.objects.append(EdObject(self, ('NAV',), i, rec))
        if self.fol is not None:
            for i, rec in enumerate(self.fol.records, 1):
                if rec.raw[0] == ord('F'):
                    self.objects.append(EdObject(self, ('FOL',), i, rec))
        if self.lit is not None:
            for i, rec in enumerate(self.lit.records, 1):
                self.objects.append(EdObject(self, ('LIT',), i, rec))
        for b in self.bsps:
            if b.bol is not None:
                for i, rec in enumerate(b.bol.records, 1):
                    self.objects.append(EdObject(self, ('BOL', b.slot), i, rec, bsp=b))
            if b.nvs is not None:
                for i in range(len(b.nvs.points)):
                    self.objects.append(EdNav(self, b, i))
        if 'boun' in self.tables:
            for i, rec in enumerate(self.tables['boun'][1].records, 1):
                self.objects.append(EdBound(self, i, rec))

    def bsp_by_slot(self, slot):
        if slot == 16:
            slot = 0
        for b in self.bsps:
            if b.slot == slot:
                return b
        return None

    # --------------------------------------------------------------- terrain
    def ground_height(self, x, z):
        t = self.terrain
        if t is None:
            return 0.0
        h = t.tiles['height']
        fx, fz = x / TILE, z / TILE
        ix, iz = int(np.floor(fx)), int(np.floor(fz))
        if ix < 0 or iz < 0 or ix >= t.width - 1 or iz >= t.height - 1:
            return 0.0
        ax, az = fx - ix, fz - iz
        h00, h10 = float(h[iz, ix]), float(h[iz, ix + 1])
        h01, h11 = float(h[iz + 1, ix]), float(h[iz + 1, ix + 1])
        return (h00 * (1 - ax) + h10 * ax) * (1 - az) + (h01 * (1 - ax) + h11 * ax) * az

    def terrain_mesh(self):
        return self.ted.full_mesh()

    # ------------------------------------------------------------------ save
    def mark_dirty(self, what):
        self.dirty.add(what)

    def save(self):
        """Write every changed file, keeping a backup of the original next to
        the game in <game>/d6edit_backup/<timestamp>/."""
        if not self.dirty:
            return []
        stamp = time.strftime('%Y%m%d-%H%M%S')
        bdir = os.path.join(self.root, 'd6edit_backup', stamp)
        written = []
        for what in sorted(self.dirty, key=str):
            path, data = self._serialize(what)
            if path is None:
                continue
            os.makedirs(bdir, exist_ok=True)
            if os.path.exists(path):
                shutil.copy2(path, os.path.join(bdir, os.path.basename(path)))
            tmp = path + '.d6edit-tmp'
            with open(tmp, 'wb') as f:      # write then rename: never leaves half a file
                f.write(data)
            os.replace(tmp, path)
            written.append(path)
        self.dirty.clear()
        return written

    def _serialize(self, what):
        if what == ('TOL',):
            return self.tol_path, self.tol.build()
        if what == ('NAV',):
            return self.nav_path, self.nav.build()
        if what == ('FOL',):
            return self.fol_path, self.fol.build()
        if what == ('LIT',):
            return self.lit_path, self.lit.build()
        if what == ('TMR',):
            return self.tmr_path, self.terrain.serialize()
        if what[0] == 'BOL':
            b = self.bsp_by_slot(what[1])
            return b.bol_path, b.bol.build()
        if what[0] == 'NVS':
            b = self.bsp_by_slot(what[1])
            return b.nvs_path, b.nvs.to_bytes()
        if what[0] == 'BSP':
            b = self.bsp_by_slot(what[1])
            return b.level.paths.get('bsp'), b.bsp.to_bytes()
        if what[0] == 'TWD':
            b = self.bsp_by_slot(what[1])
            return b.level.paths.get('twd'), b.twd.build()
        if what[0] == 'L2N':
            b = self.bsp_by_slot(what[1])
            return b.level.paths.get('l2n'), b.level.l2n.to_bytes()
        if what[0] == 'TABLE':
            p, t = self.tables[what[1]]
            return p, t.build()
        return None, None

    def saved_state_files(self):
        """D6SEGnn.GAM in the game folder overrides object placement once the
        spoke has been visited; editing objects needs it out of the way."""
        p = find(self.root, 'D6SEG%02d.GAM' % self.number)
        return [p] if p else []
