"""
d6edit - nav graph editing.

Three kinds of nav data (docs/formats/levels.md, terrain.md):
* BSP nav points (.nvs): links are 0-based indices into the same file, each
  point has a unique id (< 20000) used by D6LINK and scripts; .l2n maps every
  BSP leaf to a nav index (FindCurrentNavPt_).
* Terrain nav points ('N' records of SPOKEnn.NAV): id = record index
  (1-based, < 2000), links = u16[6] record indices at +36 (0 = none).
* D6LINKnn.DAT joins terrain <-> BSP and BSP <-> BSP by ids
  (LinkNavPoints_: bspA -1 = navA is terrain, bspB -1 = navB is terrain).

A nav "ref" in this module is ('bsp', slot, index) or ('ter', record_index).
"""
import struct

import numpy as np

import d6level

MAX_LINKS = 6


def ref_of(o):
    if o.cat == 'nav':
        return ('bsp', o.bsp.slot, o.index)
    if o.cat == 'object' and o.listkey == ('NAV',) and o.kind == 'N':
        return ('ter', o.index)
    return None


class NavGraph(object):
    def __init__(self, spoke):
        self.sp = spoke

    # ------------------------------------------------------------ access
    def bsp(self, slot):
        return self.sp.bsp_by_slot(slot)

    def ter_rec(self, idx):
        return self.sp.nav.records[idx - 1]

    def ter_links(self, idx):
        return list(struct.unpack_from('<6H', self.ter_rec(idx).raw, 36))

    def set_ter_links(self, idx, links):
        links = (list(links) + [0] * MAX_LINKS)[:MAX_LINKS]
        struct.pack_into('<6H', self.ter_rec(idx).raw, 36, *links)

    def bsp_links(self, slot, i):
        p = self.bsp(slot).nvs.points[i]
        return [p.l0, p.l1, p.l2, p.l3, p.l4, p.l5]

    def set_bsp_links(self, slot, i, links):
        links = (list(links) + [-1] * MAX_LINKS)[:MAX_LINKS]
        nv = self.bsp(slot).nvs
        nv.points[i] = nv.points[i]._replace(l0=links[0], l1=links[1], l2=links[2],
                                               l3=links[3], l4=links[4], l5=links[5])

    def nav_id(self, ref):
        if ref[0] == 'ter':
            return ref[1]
        return self.bsp(ref[1]).nvs.points[ref[2]].id

    def position(self, ref):
        if ref[0] == 'ter':
            r = self.ter_rec(ref[1])
            p = np.frombuffer(bytes(r.raw[4:16]), '<f4').astype(np.float64)
            if p[1] == 0:
                p = p.copy()
                p[1] = self.sp.ground_height(p[0], p[2])
            return p
        b = self.bsp(ref[1])
        q = b.nvs.points[ref[2]]
        return b.to_world((q.x, q.y, q.z))

    # ------------------------------------------------------------ state (undo)
    def state(self):
        st = {'bsp': {}, 'ter': None, 'link': None}
        for b in self.sp.bsps:
            if b.nvs is not None:
                l2n = b.level.l2n
                st['bsp'][b.slot] = (list(b.nvs.points), b.nvs.unknown,
                                     l2n.nav.tobytes() if l2n is not None else None)
        if self.sp.nav is not None:
            st['ter'] = [bytes(r.raw) for r in self.sp.nav.records]
        if 'link' in self.sp.tables:
            st['link'] = [bytes(r.raw) for r in self.sp.tables['link'][1].records]
        return st

    def restore(self, st):
        from array import array
        for b in self.sp.bsps:
            if b.slot in st['bsp']:
                pts, unk, l2n = st['bsp'][b.slot]
                b.nvs.points[:] = pts
                b.nvs.unknown = unk
                if l2n is not None:
                    b.level.l2n.nav = array('h', l2n)
        if st['ter'] is not None:
            recs = self.sp.nav.records
            del recs[len(st['ter']):]
            for i, raw in enumerate(st['ter']):
                if i < len(recs):
                    recs[i].raw[:] = raw
                else:
                    r = self.sp.nav.new_record()
                    r.raw[:] = raw
        if st['link'] is not None:
            t = self.sp.tables['link'][1]
            del t.records[len(st['link']):]
            for i, raw in enumerate(st['link']):
                if i < len(t.records):
                    t.records[i].raw[:] = raw
                else:
                    r = t.new_record()
                    r.raw[:] = raw

    def dirty_keys(self):
        keys = []
        for b in self.sp.bsps:
            if b.nvs is not None:
                keys.append(('NVS', b.slot))
                if b.level.l2n is not None:
                    keys.append(('L2N', b.slot))
        if self.sp.nav is not None:
            keys.append(('NAV',))
        if 'link' in self.sp.tables:
            keys.append(('TABLE', 'link'))
        return keys

    # ------------------------------------------------------------ BSP leaf lookup
    def find_leaf(self, b, p_bsp):
        bsp = b.bsp
        node = bsp.models[0].head0
        x, y, z = p_bsp
        for _ in range(4096):
            if node < 0:
                return -(node + 1)
            n = bsp.nodes[node]
            pl = bsp.planes[n.planenum]
            d = x * pl.nx + y * pl.ny + z * pl.nz - pl.dist
            node = n.front if d >= 0 else n.back
        return None

    # ------------------------------------------------------------ add
    def add(self, world, prefer_slot=None):
        """Add a nav point at a world position: into the BSP whose bounds hold
        it (prefer_slot first), else the terrain list. Returns its ref."""
        cands = [b for b in self.sp.bsps if b.nvs is not None and b.mesh]
        inside = []
        for b in cands:
            lo, hi = b.mesh['pos'].min(0), b.mesh['pos'].max(0)
            if np.all(world >= lo - 32) and np.all(world <= hi + 32):
                inside.append((np.prod(hi - lo), b))
        if prefer_slot is not None:
            inside = [x for x in inside if x[1].slot == prefer_slot] or inside
        if inside:
            b = min(inside, key=lambda x: x[0])[1]
            return self._add_bsp(b, world)
        if self.sp.nav is not None:
            return self._add_ter(world)
        if cands:
            b = min(cands, key=lambda b: np.linalg.norm(b.mesh['pos'].mean(0) - world))
            return self._add_bsp(b, world)
        return None

    def _add_bsp(self, b, world):
        nv = b.nvs
        ids = [p.id for p in nv.points]
        nid = max([nv.unknown] + [i + 1 for i in ids])
        if nid >= 20000:
            raise ValueError('no free nav id')
        q = b.to_bsp(world)
        nv.points.append(d6level.NavPoint(float(q[0]), float(q[1]), float(q[2]), 0.0,
                                          -1, -1, -1, -1, -1, -1, 0, nid))
        nv.unknown = nid + 1
        i = len(nv.points) - 1
        l2n = b.level.l2n
        if l2n is not None:
            leaf = self.find_leaf(b, q)
            if leaf is not None and 0 <= leaf < len(l2n.nav):
                l2n.nav[leaf] = i
        return ('bsp', b.slot, i)

    def _add_ter(self, world):
        recs = self.sp.nav.records
        idx = None
        for i, r in enumerate(recs, 1):
            if r.raw[0] in (0, ord('0')):
                idx = i
                break
        if idx is None:
            if len(recs) + 1 >= 2000:
                raise ValueError('terrain nav list is full (2000)')
            r = self.sp.nav.new_record()
            idx = len(recs)
        r = recs[idx - 1]
        r.raw[:] = b'\0' * len(r.raw)
        r.raw[0:4] = b'N001'                # as in the shipped files (the id is the record index)
        r.raw[4:16] = np.array([world[0], 0.0, world[2]], '<f4').tobytes()   # y 0: snapped to ground
        return ('ter', idx)

    # ------------------------------------------------------------ links
    def linked(self, a, b):
        if a[0] == b[0] == 'ter':
            return b[1] in self.ter_links(a[1])
        if a[0] == b[0] == 'bsp' and a[1] == b[1]:
            return b[2] in self.bsp_links(a[1], a[2])
        return self._find_cross(a, b) is not None

    def _cross_tuple(self, a, b):
        """(navA, navB, bspA, bspB) for a D6LINK record joining a -> b."""
        ra = (self.nav_id(a), a[1] if a[0] == 'bsp' else -1)
        rb = (self.nav_id(b), b[1] if b[0] == 'bsp' else -1)
        return (ra[0], rb[0], ra[1], rb[1])

    def _find_cross(self, a, b):
        if 'link' not in self.sp.tables:
            return None
        t = self.sp.tables['link'][1]
        want = {self._cross_tuple(a, b), self._cross_tuple(b, a)}
        hits = [i for i, r in enumerate(t.records)
                if (r.get('navA'), r.get('navB'), r.get('bspA'), r.get('bspB')) in want]
        return hits or None

    def toggle_link(self, a, b):
        """Link a and b (or unlink them if linked). Returns 'linked' / 'unlinked'."""
        if a == b:
            raise ValueError('same point')
        if a[0] == b[0] == 'ter':
            la, lb = self.ter_links(a[1]), self.ter_links(b[1])
            if b[1] in la:
                self.set_ter_links(a[1], [x for x in la if x != b[1]])
                self.set_ter_links(b[1], [x for x in lb if x != a[1]])
                return 'unlinked'
            la, lb = [x for x in la if x], [x for x in lb if x]
            if len(la) >= MAX_LINKS or len(lb) >= MAX_LINKS:
                raise ValueError('a point already has 6 links')
            self.set_ter_links(a[1], la + [b[1]])
            self.set_ter_links(b[1], lb + [a[1]])
            return 'linked'
        if a[0] == b[0] == 'bsp' and a[1] == b[1]:
            s = a[1]
            la, lb = self.bsp_links(s, a[2]), self.bsp_links(s, b[2])
            if b[2] in la:
                self.set_bsp_links(s, a[2], [x for x in la if x != b[2]])
                self.set_bsp_links(s, b[2], [x for x in lb if x != a[2]])
                return 'unlinked'
            la, lb = [x for x in la if x >= 0], [x for x in lb if x >= 0]
            if len(la) >= MAX_LINKS or len(lb) >= MAX_LINKS:
                raise ValueError('a point already has 6 links')
            self.set_bsp_links(s, a[2], la + [b[2]])
            self.set_bsp_links(s, b[2], lb + [a[2]])
            return 'linked'
        # different graphs: D6LINK
        if 'link' not in self.sp.tables:
            raise ValueError('this spoke has no D6LINK file')
        t = self.sp.tables['link'][1]
        hits = self._find_cross(a, b)
        if hits:
            for i in sorted(hits, reverse=True):
                del t.records[i]
            return 'unlinked'
        if a[0] == 'ter':
            a, b = b, a                # terrain end second: (bsp nav, terrain nav, bsp, -1)
        r = t.new_record()
        r.set('navA', self.nav_id(a)); r.set('navB', self.nav_id(b))
        r.set('bspA', a[1]); r.set('bspB', b[1] if b[0] == 'bsp' else -1)
        if b[0] == 'bsp':              # BSP <-> BSP: the shipped files list both directions
            r2 = t.new_record()
            r2.set('navA', self.nav_id(b)); r2.set('navB', self.nav_id(a))
            r2.set('bspA', b[1]); r2.set('bspB', a[1])
        return 'linked'

    def cross_links(self):
        """[(world a, world b)] of the D6LINK records."""
        out = []
        if 'link' not in self.sp.tables:
            return out
        ter_ok = self.sp.nav is not None
        idmap = {}
        for b in self.sp.bsps:
            if b.nvs is not None:
                idmap[b.slot] = dict((p.id, i) for i, p in enumerate(b.nvs.points))
        for r in self.sp.tables['link'][1].records:
            na, nb, ba, bb = r.get('navA'), r.get('navB'), r.get('bspA'), r.get('bspB')
            try:
                pa = (('ter', na) if ba == -1 else ('bsp', ba, idmap[ba][na]))
                pb = (('ter', nb) if bb == -1 else ('bsp', bb, idmap[bb][nb]))
                if (pa[0] == 'ter' or pb[0] == 'ter') and not ter_ok:
                    continue
                out.append((self.position(pa), self.position(pb)))
            except (KeyError, IndexError):
                continue
        return out

    # ------------------------------------------------------------ delete
    def references(self, ref, records):
        """Where a nav point's id is used: D6LINK records and trigger
        parameters named like NAV (scripts address BSP nav points by id)."""
        out = []
        nid = self.nav_id(ref)
        slot = ref[1] if ref[0] == 'bsp' else -1
        if 'link' in self.sp.tables:
            for i, r in enumerate(self.sp.tables['link'][1].records, 1):
                if (r.get('navA') == nid and r.get('bspA') == slot) or (r.get('navB') == nid and r.get('bspB') == slot):
                    out.append('nav link %d' % i)
        if ref[0] == 'bsp':
            dcl = self.sp.game.dcl
            for rec in records.get('trig', []):
                ev = rec.rec.get('event')
                i = dcl.index(ev) if ev else -1
                if i < 0:
                    continue
                names = dcl.params_of(i)
                params = rec.rec.get('params')
                for k, n in enumerate(names):
                    if 'NAV' in n.upper() and params[k] == nid:
                        out.append('trigger %d (%s)' % (rec.index, n))
        return out

    def delete(self, ref):
        if ref[0] == 'ter':
            idx = ref[1]
            for i, r in enumerate(self.sp.nav.records, 1):
                if r.raw[0] == ord('N'):
                    links = self.ter_links(i)
                    if idx in links:
                        self.set_ter_links(i, [x for x in links if x != idx])
            r = self.ter_rec(idx)
            r.raw[0] = ord('0')
            self.set_ter_links(idx, [])
            return
        b = self.bsp(ref[1])
        i = ref[2]
        nv = b.nvs
        n = len(nv.points)
        remap = lambda x: -1 if x == i or x < 0 else (x - 1 if x > i else x)
        pts = []
        for k, p in enumerate(nv.points):
            if k == i:
                continue
            ls = [remap(x) for x in (p.l0, p.l1, p.l2, p.l3, p.l4, p.l5)]
            ls = [x for x in ls if x >= 0] + [-1] * MAX_LINKS
            pts.append(p._replace(l0=ls[0], l1=ls[1], l2=ls[2], l3=ls[3], l4=ls[4], l5=ls[5]))
        # leaves that used the deleted point go to its nearest remaining neighbour
        old = nv.points[i]
        nv.points[:] = pts
        l2n = b.level.l2n
        if l2n is not None and pts:
            d = [(old.x - p.x) ** 2 + (old.y - p.y) ** 2 + (old.z - p.z) ** 2 for p in pts]
            near = int(np.argmin(d))
            for k in range(len(l2n.nav)):
                v = l2n.nav[k]
                if v == i:
                    l2n.nav[k] = near
                elif v > i:
                    l2n.nav[k] = v - 1
        assert len(nv.points) == n - 1


def changed_keys(a, b):
    """Dirty keys (world.Spoke._serialize) whose data differs between two states."""
    keys = []
    for slot in set(a['bsp']) | set(b['bsp']):
        pa, pb = a['bsp'].get(slot), b['bsp'].get(slot)
        if pa is None or pb is None:
            continue
        if pa[0] != pb[0] or pa[1] != pb[1]:
            keys.append(('NVS', slot))
        if pa[2] != pb[2]:
            keys.append(('L2N', slot))
    if a['ter'] != b['ter']:
        keys.append(('NAV',))
    if a['link'] != b['link']:
        keys.append(('TABLE', 'link'))
    return keys
