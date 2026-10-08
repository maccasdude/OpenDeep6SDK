#!/usr/bin/env python3
"""
d6exits.py - list the area transitions ("exits") of Wizards & Warriors
(Deep6 engine, deep6.exe 2000) per spoke.

See docs/formats/exits.md for the reverse engineered semantics. Short version:

  LOADSEGMENT  (op 0x2A, events @[OFF]SWITCHDD/DOORTOSEGMENT)
      spoke -> spoke. Fired from a state trigger (a door lever switch sets the
      state). If every party creature is inside bound !BOUNDNUM the game
      switches to spoke SEGMENT. Each PC keeps its position *relative to the
      BSP it stands in*; in the new spoke that local position is re-based on
      BSP slot BSPNUM. Switch SEGSWITCH of the new spoke is set to SEGSTATUS.
  GOTOBSP / GOTOBSPTERR  (ops 0x21/0x22, @GOTOBSPONLY/@GOTOBSPTERR)
      BSP -> BSP inside one spoke (overlapping seam geometry). The actor's
      position relative to its current BSP is re-based on BSP BSPNUM.
      DESTNAV / LASTNAV are nav ids (in BSPNUM's .nvs) for the path AI.
  ENTERTOWN  (op 0x4F, @ENTERTHETOWN)
      leave the spoke for town hub TOWN (0 Valeia, 1 Ishad N'ha, 2 Brimloch
      Roon). Coming back goes through a town gate -> SetSpokeEntry_ ->
      GetEntryPosition_ (hard coded table, ENTRY_POINTS below).
  TELEPORT  (op 0x3B) - same-BSP teleport to x,y,z (BSP relative), listed as
      kind 'teleport' for completeness.

API:
    list_exits(gamedir, spoke)      -> list of dicts (see _exit_dict)
    list_entrances(gamedir, spoke)  -> arrivals into a spoke (town gates and
                                       LOADSEGMENT exits of other spokes)
    TOWN_GATES, ENTRY_POINTS, entry_party_positions()

CLI:
    python3 d6exits.py GAMEDIR [spoke]          human readable
    python3 d6exits.py --json GAMEDIR [spoke]   JSON
    python3 d6exits.py --markdown GAMEDIR       tables used in docs/formats/exits.md

Plain ASCII only.
"""
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import d6data as D      # noqa: E402
import d6level as L     # noqa: E402

# ---------------------------------------------------------------------------
# Hard coded tables from deep6.exe
# ---------------------------------------------------------------------------

# GetEntryPosition_ (d6spoke.c 0x489d20): (spoke, _gSpokeEntryNum) -> x, y, z, dir.
# World units; dir in 1024 units per turn (0x100 = quarter turn). Only used when
# the party walks out of a town gate (AssignPartyCamera_). Spokes other than
# 0 and 3 ignore the entry number. Spoke 0 entry >2 and spoke 3 entry >1
# leave the previous values (stale globals) - do not use them.
ENTRY_POINTS = {
    (0, 0): (0x35200, 0, 0x14600, 0x100),
    (0, 1): (0x2d600, 0, 0x12200, 0x300),
    (0, 2): (0x2a00, 0x1c00, 0x44c00, 0x100),
    (1, 0): (0x46e00, 0x820, 0x25300, 0x100),
    (2, 0): (0x54600, 0x1428, 0x4b200, 0x300),
    (3, 0): (0x1f12, 0xc00, 0x45600, 0x100),
    (3, 1): (0x45600, 0x442, 0x2a00, 0x300),
    (4, 0): (0x1ba00, 0x400, 0x1b000, 0),
    (5, 0): (0x20000, -0xb00, 0x1f400, 0x100),
    (6, 0): (0x34b00, -0x3b00, 0x23b00, 0),
    (7, 0): (0x2c200, -0x800, 0x1ca00, 0),
    (8, 0): (0x1ac00, 0x400, 0x25a00, 0),
    (9, 0): (0x33a00, -0x900, 0x24000, 0),
    (10, 0): (0x36c00, -0x1400, 0x33400, 0x200),
    (11, 0): (0x5d800, 0x400, 0x6400, 0x300),
    (12, 0): (0x29a00, -0x1500, 0x22200, 0),
}

TOWN_NAMES = {0: 'Valeia', 1: "Ishad N'ha", 2: 'Brimloch Roon'}   # D6STRING 0x21fc..

# Town hub GATE hot spots (townmsk*.avi field type 'GATE', value = gate) ->
# SetSpokeEntry_ (deep6.c 0x4134b8): (town, gate) -> (spoke, entry, label).
# Labels: D6STRING 0x2260 + (town*2+gate)*2 (two lines).
TOWN_GATES = {
    (0, 0): (0, 0, 'The Old Road To The Graveyard Ruins Of Bersault'),
    (0, 1): (0, 1, 'The Forest Trail To Nymph Lake'),
    (1, 0): (0, 2, "The Marsh Trail To The Temple Of Ishad N'ha"),
    (1, 1): (3, 1, "The Forest Road To The Knight's Castle At Shurugeon Ruins"),
    (2, 0): (3, 0, "The Mountain Pass To The Dragon's Spire"),
    (2, 1): (11, 1, 'The Wharf On The Enchanted Sea (needs the warship, _gPartyWarshipFlag)'),
}

# AssignPartyCamera_ (0x489ef8) placement:
#   C = E + R*(sin,cos)[dir+0x200], R = 1280; each PC takes the first direction
#   k (in PARTY_DIR order) whose spot C + r*(sin,cos)[dir - PARTY_DIR[k]] is free
#   (ObjectSpaceOK_); r starts at 1280, is NOT reset between PCs and grows by 1024
#   while no direction is free (rings 1280 .. 9472). y from D6_AdjustBase_ (ground).
# _gSin/_gCos (BuildTrigTables_ 0x421570): 1024 entries of sin/cos(i * 6.28 / 1024)
# (6.28, not 2 pi); x uses sin and z cos. So PC 0 lands about 2 units from E.
PARTY_DIR = (0, 170, -170, 340, -340, 512)      # _partyDir (0x5d7ac0)
ENTRY_RADIUS = 1280.0                           # float at 0x5c5a3a
TRIG_TURN = 6.28                                # BuildTrigTables_: 6.28 per 1024 steps


def _gsin(i):
    return math.sin((i & 0x3ff) * TRIG_TURN / 1024)


def _gcos(i):
    return math.cos((i & 0x3ff) * TRIG_TURN / 1024)


def entry_party_positions(spoke, entry=0):
    """PC positions (x, y, z) for a town-gate arrival when every spot is free
    (PC k on direction k, first ring). y is the entry y; the game puts the PCs
    on the ground (D6_AdjustBase_). Blocked spots move later PCs to the next
    free direction or a wider ring (see AssignPartyCamera_ above)."""
    x, y, z, d = ENTRY_POINTS.get((spoke, entry), ENTRY_POINTS.get((spoke, 0)))
    a0 = d + 0x200
    cx = x + _gsin(a0) * ENTRY_RADIUS
    cz = z + _gcos(a0) * ENTRY_RADIUS
    out = []
    for pd in PARTY_DIR:
        a = d - pd
        out.append((round(cx + ENTRY_RADIUS * _gsin(a), 1), y, round(cz + ENTRY_RADIUS * _gcos(a), 1)))
    return out


# NewSpokeSegment_: spoke 8 BSPs are shifted by 256 world units in y when the
# party crosses a segment boundary (subtract on leaving 8, add on entering 8).
SPOKE_Y_SHIFT = {8: 256.0}

TRANSITION_EVENTS = {
    0x2A: 'segment', 0x21: 'bsp', 0x22: 'bsp', 0x4F: 'town', 0x3B: 'teleport',
    0x54: 'endgame',
}
STATE_WRITE_OPS = {0x08: 0, 0x09: 0, 0x38: 0, 0x3F: 0, 0x3A: 5}   # op -> operand that names the state
SWITCH_WRITE_OPS = {0x0A: 0, 0x0B: 0, 0x3E: 0}                   # op -> operand that names a switch


# ---------------------------------------------------------------------------
# Loading helpers (cached per gamedir)
# ---------------------------------------------------------------------------
_CACHE = {}


def _cached(key, fn):
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


def _table(gamedir, cls, pat, spoke):
    def load():
        data = D.read_file(gamedir, pat % spoke)
        return cls.parse(data) if data else cls()
    return _cached((gamedir, pat, spoke), load)


class _Events(object):
    def __init__(self, gamedir):
        self.dcl = D.EventDCL.parse(D.read_file(gamedir, 'EVENTS.DCL'))
        self.code = D.read_file(gamedir, 'EVENTS.COD')
        ends = sorted(set(self.dcl.codeoff[:self.dcl.n])) + [len(self.code)]
        self.ops = {}
        for i in range(self.dcl.n):
            s = self.dcl.codeoff[i]
            e = min(x for x in ends if x > s)
            pc, ops = s, []
            while pc < e:
                op, q, args, tgt, ln = D.ev_decode(self.code, pc)
                ops.append((op, args))
                pc += ln
            self.ops[self.dcl.name(i).upper()] = (i, ops)

    def params(self, ev):
        i = self.ops[ev][0]
        return self.dcl.params_of(i)

    def desc(self, ev):
        return self.dcl.desc(self.ops[ev][0])


def _events(gamedir):
    return _cached((gamedir, 'events'), lambda: _Events(gamedir))


def _bsps(gamedir, spoke):
    """slot -> placement dict (name, origin, tile_rect ...)."""
    def load():
        return dict((p['slot'], p) for p in L.spoke_bsps(spoke, gamedir))
    return _cached((gamedir, 'bsps', spoke), load)


def _bspfile(gamedir, name):
    def load():
        p = L.find_file(gamedir, name + '.bsp')
        return L.BSPFile.load(p) if p else None
    return _cached((gamedir, 'bsp', name.lower()), load)


def _navs(gamedir, name):
    def load():
        p = L.find_file(gamedir, name + '.nvs')
        if not p:
            return {}
        return dict((n.id, n) for n in L.NavPoints.load(p).points)
    return _cached((gamedir, 'nvs', name.lower()), load)


def _objlist(gamedir, fname):
    def load():
        p = L.find_file(gamedir, fname)
        return L.ObjectList.load(p) if p else None
    return _cached((gamedir, 'ol', fname.lower()), load)


def _origin(gamedir, spoke, slot):
    p = _bsps(gamedir, spoke).get(slot)
    return p['origin'] if p else None


def _add(a, b):
    return tuple(round(x + y, 1) for x, y in zip(a, b))


def _sub(a, b):
    return tuple(round(x - y, 1) for x, y in zip(a, b))


def _center(mn, mx):
    return tuple(round((a + b) / 2.0, 1) for a, b in zip(mn, mx))


# ---------------------------------------------------------------------------
# Position resolution
# ---------------------------------------------------------------------------

def bsp_name(gamedir, spoke, slot):
    p = _bsps(gamedir, spoke).get(slot)
    return p['name'] if p else None


def nav_world(gamedir, spoke, slot, navid):
    """World position of nav id in BSP slot (Bsp2World: p*16 + origin)."""
    name = bsp_name(gamedir, spoke, slot)
    if not name or not navid:
        return None
    n = _navs(gamedir, name).get(navid)
    if n is None:
        return None
    o = _origin(gamedir, spoke, slot)
    return (round(n.x * 16 + o[0], 1), round(n.y * 16 + o[1], 1), round(n.z * 16 + o[2], 1))


def entity_world(gamedir, spoke, slot, entid):
    """Centre of the brush model whose entity has d6entid == entid."""
    name = bsp_name(gamedir, spoke, slot)
    if not name:
        return None
    b = _bspfile(gamedir, name)
    if b is None:
        return None
    o = _origin(gamedir, spoke, slot)
    for e in b.entity_dicts():
        if e.get('d6entid') == str(entid) and e.get('model', '').startswith('*'):
            m = b.models[int(e['model'][1:])]
            c = ((m.minx + m.maxx) / 2.0, (m.miny + m.maxy) / 2.0, (m.minz + m.maxz) / 2.0)
            return (round(c[0] * 16 + o[0], 1), round(c[1] * 16 + o[1], 1), round(c[2] * 16 + o[2], 1))
    return None


def _bol_name(gamedir, spoke, slot):
    if spoke in D.SPOKE_BSPS:
        return D.SPOKE_BSPS[spoke].get(slot)
    n = bsp_name(gamedir, spoke, slot)
    return (n + '.bol') if n else None


def object_world(gamedir, spoke, objid):
    """World position of a placed object id (GetObjNum_ numbering)."""
    slot, rec = D.split_objid(objid)
    if slot is None:
        fn = D.TERRAIN_SPOKES.get(spoke)
        if not fn:
            return None
        ol = _objlist(gamedir, fn)
        o = (0.0, 0.0, 0.0)
    else:
        fn = _bol_name(gamedir, spoke, slot)
        ol = _objlist(gamedir, fn) if fn else None
        o = _origin(gamedir, spoke, slot)
    if ol is None or o is None or not (1 <= rec <= len(ol.records)):
        return None
    r = ol.records[rec - 1]
    return (round(r.x + o[0], 1), round(r.y + o[1], 1), round(r.z + o[2], 1))


def switch_world(gamedir, spoke, sw):
    if sw.isentity:
        return entity_world(gamedir, spoke, sw.objid // 100000, sw.objid % 100000)
    return object_world(gamedir, spoke, sw.objid)


def bound_world(gamedir, spoke, b):
    """(min, max, src_slot) in world units. LoadBoundAreaData_ adds the
    origin of BSP b.bsp when b.bsp >= 0."""
    mn, mx = tuple(b.min), tuple(b.max)
    if b.bsp >= 0:
        o = _origin(gamedir, spoke, b.bsp)
        if o is None:
            return None, None, b.bsp
        mn, mx = _add(mn, o), _add(mx, o)
    else:
        mn, mx = tuple(round(v, 1) for v in mn), tuple(round(v, 1) for v in mx)
    return mn, mx, b.bsp


def _rects(gamedir, spoke):
    """_bspBounds: slot -> (x0, z0, x1, z1) tile rectangle (half open)."""
    out = {}
    for slot, p in _bsps(gamedir, spoke).items():
        x0, z0, w, h = p['tile_rect']
        out[slot] = (x0, z0, x0 + w, z0 + h)
    return out


def _terrain_size(gamedir, spoke):
    """(W, H) in tiles from SPOKEnn.TMR, or None for dungeon spokes."""
    def load():
        data = D.read_file(gamedir, 'SPOKE%02d.TMR' % spoke)
        return struct.unpack_from('<ii', data, 0) if data else None
    return _cached((gamedir, 'tmrsize', spoke), load)


def terrain_leaf_tree(W, H, rects):
    """TerBSP_Calculate_ (0x5495b8): the tile-space BSP the game builds over
    the BSP placements of a terrain spoke. rects: slot -> (x0, z0, x1, z1).
    Returns (nodes [axis, val, front, back], leafs [(x0, z0, x1, z1, slot)]);
    a child >= 0 is a node, < 0 is -(leaf index); axis 0 tests z, 1 tests x."""
    cands = [i for i in sorted(rects) if rects[i][0] < W and rects[i][2] >= 0
             and rects[i][1] < H and rects[i][3] >= 0]
    if not cands:
        return [], [(0, 0, W, H, -1)]
    splits = []                # (axis, val, lo, hi, slot, side) in the game's order
    for i in cands:
        x0, z0, x1, z1 = rects[i]
        splits += [(1, x0, z0, z1, i, 0), (1, x1, z0, z1, i, 1), (0, z0, x0, x1, i, 0), (0, z1, x0, x1, i, 1)]
    nodes, leafs = [], [None]

    def fetch(ids):            # TerBSP_FetchSplitter_: first splitter nothing straddles
        for k, sidx in enumerate(ids):
            S = splits[sidx]
            front, back, ok = [], [], True
            for j, t in enumerate(ids):
                if j == k:
                    continue
                T = splits[t]
                if T[0] == S[0]:
                    if T[1] != S[1]:
                        (front if T[1] > S[1] else back).append(t)
                elif T[2] >= S[1]:
                    front.append(t)
                elif S[1] < T[3]:
                    ok = False
                    break
                else:
                    back.append(t)
            if ok:
                return sidx, front, back
        return -1, None, None

    def build(region, ids, slot):
        if not ids:
            leafs.append(tuple(region) + (slot,))
            return -(len(leafs) - 1)
        sidx, front, back = fetch(ids)
        if sidx < 0:
            raise ValueError('TerBSP: splitter not found (overlapping BSP placements)')
        S = splits[sidx]
        fr, bk = list(region), list(region)
        if S[0] == 0:
            fr[1] = bk[3] = S[1]
        else:
            fr[0] = bk[2] = S[1]
        n = len(nodes)
        nodes.append([S[0], S[1], None, None])
        nodes[n][2] = build(fr, front, S[4] if S[5] == 0 else -1)
        nodes[n][3] = build(bk, back, -1 if S[5] == 0 else S[4])
        return n
    build([0, 0, W, H], list(range(len(splits))), -1)
    return nodes, leafs


def _leaf_tree(gamedir, spoke):
    def load():
        size = _terrain_size(gamedir, spoke)
        return terrain_leaf_tree(size[0], size[1], _rects(gamedir, spoke))
    return _cached((gamedir, 'terbsp', spoke), load)


def bsp_at(gamedir, spoke, pos):
    """InBSPArea_ (0x45e250): BSP slot a world position belongs to, or -1.
    Tiles are pos / 1024 truncated toward zero. Dungeon spokes: the first slot
    whose tile rectangle (_bspBounds) contains the tile (InBSPOnly_). Terrain
    spokes: the slot of the leaf of the terrain leaf tree, -1 off the map."""
    tx, tz = int(pos[0] / 1024.0), int(pos[2] / 1024.0)
    size = _terrain_size(gamedir, spoke)
    if size is None:
        for slot, (x0, z0, x1, z1) in sorted(_rects(gamedir, spoke).items()):
            if x0 <= tx < x1 and z0 <= tz < z1:
                return slot
        return -1
    W, H = size
    if not (0 <= tx < W and 0 <= tz < H):
        return -1
    nodes, leafs = _leaf_tree(gamedir, spoke)
    if not nodes:
        return leafs[0][4]
    n = 0
    while n >= 0:
        axis, val, front, back = nodes[n]
        n = front if (tz if axis == 0 else tx) >= val else back
    return leafs[-n][4]


# ---------------------------------------------------------------------------
# Conditions in event code (ExecuteEventCode_ jump ops, docs/formats/exits.md)
# ---------------------------------------------------------------------------
COND_TEXT = {
    0x04: 'world state {0} is set', 0x1E: 'the acting PC has item {0}',
    0x2B: 'the whole party is inside box B{0}', 0x2C: 'the acting PC is using item {0}',
    0x32: 'occupy slot {0} is empty', 0x33: 'occupy slot {0} holds item {1} (-1 = any)',
    0x3C: 'object {0} is inside box B{1}', 0x43: 'switch W{0} is on',
    0x51: 'the acting PC has item {0} equipped', 0x55: 'a monster of record {0} is alive',
}
GATE_TEXT = {
    0x53: 'quest flag {0} of the acting PC == {1}: set it to {2}',
}


def event_conditions(gamedir, spoke, ti):
    """Readable tests in trigger ti's event before its first level change
    (the transition happens on one branch of them only)."""
    sp = _Spoke(gamedir, spoke)
    t = sp.trig(ti)
    if t is None:
        return []
    ev = t.event.upper()
    if ev not in sp.ev.ops:
        return []
    tp = t.params
    out = []
    for op, args in sp.ev.ops[ev][1]:
        if op in TRANSITION_EVENTS:
            break
        txt = COND_TEXT.get(op) or GATE_TEXT.get(op)
        if txt:
            vals = [tp[a] if 0 <= a < len(tp) else '?' for a in args]
            out.append('%s %s' % ('IF' if op in COND_TEXT else 'DO', txt.format(*(vals + ['?'] * 3))))
    return out


# ---------------------------------------------------------------------------
# Trigger source resolution
# ---------------------------------------------------------------------------

class _Spoke(object):
    def __init__(self, gamedir, spoke):
        self.g, self.s = gamedir, spoke
        self.T = _table(gamedir, D.TriggerTable, 'D6TRIG%02d.DAT', spoke)
        self.B = _table(gamedir, D.BoundTable, 'D6BOUN%02d.DAT', spoke)
        self.W = _table(gamedir, D.SwitchTable, 'D6SWIT%02d.DAT', spoke)
        self.S = _table(gamedir, D.SpecialTable, 'D6SPEC%02d.DAT', spoke)
        self.ev = _events(gamedir)

    def trig(self, ti):
        if 1 <= ti <= len(self.T):
            return self.T[ti]
        return None

    def tparams(self, ti):
        t = self.trig(ti)
        ev = t.event.upper()
        if ev not in self.ev.ops:
            return ev, {}
        return ev, dict(zip(self.ev.params(ev), t.params))

    def box_src(self, bi, role):
        b = self.B[bi]
        mn, mx, slot = bound_world(self.g, self.s, b)
        return dict(kind='box', role=role, bound=bi, bsp=b.bsp, who=b.who, enabled=b.enabled,
                    oneshot=b.oneshot, local_min=tuple(round(v, 1) for v in b.min),
                    local_max=tuple(round(v, 1) for v in b.max), min=mn, max=mx,
                    pos=_center(mn, mx) if mn else None)

    def switch_src(self, wi, role):
        w = self.W[wi]
        return dict(kind='switch', role=role, switch=wi, objid=w.objid, isentity=w.isentity,
                    keyitem=w.keyitem, enabled=w.enabled, on=w.on, target=w.target,
                    pos=switch_world(self.g, self.s, w))

    def sources(self, ti, depth=0, seen=None):
        """How trigger ti gets run: list of source dicts."""
        seen = set() if seen is None else seen
        if ti in seen or depth > 4:
            return []
        seen = seen | {ti}
        out = []
        t = self.trig(ti)
        if t is None:
            return out
        for bi, b in enumerate(self.B.records, 1):
            if b.trigger == ti:
                out.append(self.box_src(bi, 'fires trigger'))
        for wi, w in enumerate(self.W.records, 1):
            if w.target == -ti:
                out.append(self.switch_src(wi, 'runs trigger'))
        for si, sp in enumerate(self.S.records, 1):
            if sp.trigger == ti:
                out.append(dict(kind='special', role='fires trigger', special=si, type=sp.type,
                                args=list(sp.args)))
        # CALLEVENT / !EVENT* parameters of other triggers
        for tj in range(1, len(self.T) + 1):
            if tj == ti:
                continue
            ev, p = self.tparams(tj)
            if ev not in self.ev.ops:
                continue
            pn = self.ev.params(ev)
            for op, args in self.ev.ops[ev][1]:
                if op != 0x37:
                    continue
                for a in args:
                    if 0 <= a < len(pn) and self.trig(tj).params[a] == ti:
                        out.append(dict(kind='call', role='CALLEVENT %s' % pn[a], trigger=tj,
                                        event=ev, sources=self.sources(tj, depth + 1, seen),
                                        conditions=self.conditions(tj, depth, seen)))
        # state trigger: enabled triggers run when WState[state] changes (CheckStates_)
        st = t.state
        if st > 0 and t.enabled:
            out.extend(self.state_sources(st, ti, depth, seen))
        return out

    def conditions(self, tj, depth, seen):
        """World states tested with IFSTATE by trigger tj's event (e.g. the
        'party is near the gate' state of @IFSTATECALLEVENT) and who sets them."""
        ev, p = self.tparams(tj)
        tp = self.trig(tj).params
        out = []
        for op, args in self.ev.ops.get(ev, (0, []))[1]:
            if op == 0x04 and args and 0 <= args[0] < len(tp):
                st = tp[args[0]]
                srcs = [s for s in self.state_sources(st, tj, depth + 1, seen | {tj})
                        if s['kind'] not in ('script', 'call')]
                out.append(dict(state=st, setters=srcs))
        return out

    def state_sources(self, st, ti, depth, seen):
        out = []
        for wi, w in enumerate(self.W.records, 1):
            if w.target == st:
                out.append(self.switch_src(wi, 'sets state %d' % st))
        for bi, b in enumerate(self.B.records, 1):
            if b.trigger < 1 and b.state == st:
                out.append(self.box_src(bi, 'sets state %d' % st))
        for si, sp in enumerate(self.S.records, 1):
            if sp.trigger < 1 and sp.state == st:
                out.append(dict(kind='special', role='sets state %d' % st, special=si,
                                type=sp.type, args=list(sp.args)))
        for tj in range(1, len(self.T) + 1):
            if tj == ti:
                continue
            ev, p = self.tparams(tj)
            if ev not in self.ev.ops:
                continue
            pn = self.ev.params(ev)
            tp = self.trig(tj).params
            hit = False
            for op, args in self.ev.ops[ev][1]:
                if op in STATE_WRITE_OPS and len(args) > STATE_WRITE_OPS[op]:
                    a = args[STATE_WRITE_OPS[op]]
                    hit = hit or (0 <= a < len(tp) and tp[a] == st)
                if op in SWITCH_WRITE_OPS and len(args) > SWITCH_WRITE_OPS[op]:
                    a = args[SWITCH_WRITE_OPS[op]]
                    if 0 <= a < len(tp) and 1 <= tp[a] <= len(self.W):
                        hit = hit or self.W[tp[a]].target == st
            if hit:
                out.append(dict(kind='script', role='writes state %d' % st, trigger=tj, event=ev,
                                sources=self.sources(tj, depth + 1, seen | {ti})))
        if not out:
            out.append(dict(kind='state', role='WState[%d] (no writer found)' % st, state=st))
        return out


def _flatten(srcs):
    """Depth first list of leaf sources (box/switch/special/state)."""
    out = []
    for s in srcs:
        if s['kind'] in ('call', 'script'):
            sub = _flatten(s.get('sources', []))
            out.extend(sub if sub else [s])
        else:
            out.append(s)
    return out


def _primary_kind(srcs):
    leaves = _flatten(srcs)
    for k in ('switch', 'box', 'special', 'script', 'call', 'state'):
        if any(s['kind'] == k for s in leaves):
            return k
    return 'none'


# ---------------------------------------------------------------------------
# Exits
# ---------------------------------------------------------------------------

def _rebase(gamedir, src_spoke, src_slot, dst_spoke, dst_slot, pos):
    """NewSpokeSegment_ / TranslateToBSP_ position mapping."""
    if pos is None:
        return None
    p = pos
    if src_slot is not None and src_slot >= 0:
        o = _origin(gamedir, src_spoke, src_slot)
        if o is None:
            return None
        p = _sub(p, o)
        if src_spoke != dst_spoke and src_spoke in SPOKE_Y_SHIFT:
            p = _sub(p, (0, SPOKE_Y_SHIFT[src_spoke], 0))
    if dst_slot is not None and dst_slot >= 0:
        o = _origin(gamedir, dst_spoke, dst_slot)
        if o is None:
            return None
        if src_spoke != dst_spoke and dst_spoke in SPOKE_Y_SHIFT:
            p = _add(p, (0, SPOKE_Y_SHIFT[dst_spoke], 0))
        p = _add(p, o)
    return p


def list_exits(gamedir, spoke, include_teleports=True):
    """All triggers in `spoke` whose event leaves the area (LOADSEGMENT,
    GOTOBSP[TERR], ENTERTOWN, ENDGAME) or teleports the party (TELEPORT).
    Each dict:
      kind         activation: 'switch' | 'box' | 'special' | 'script' | 'state' | 'none'
      action       'segment' (spoke change) | 'bsp' (BSP->BSP) | 'town' | 'teleport' | 'endgame'
      spoke, trigger, event, op, params (dict name->value), sources (tree)
      source       compact record ids {'bound':..,'switch':..,...}
      source_pos   world x,y,z of the switch/box centre; box_min/box_max (world)
      src_bsp      BSP slot the box (party) is in
      dest_spoke, dest_bsp, dest_bsp_name, dest_nav, dest_lastnav,
      dest_pos     world position (in dest spoke) of the box centre after re-basing
      dest_box_min/max, dest_switch, dest_status, dest_town, description
    """
    sp = _Spoke(gamedir, spoke)
    ev = sp.ev
    res = []
    for ti in range(1, len(sp.T) + 1):
        evname, p = sp.tparams(ti)
        if evname not in ev.ops:
            continue
        ops = [(op, a) for op, a in ev.ops[evname][1] if op in TRANSITION_EVENTS]
        if not ops:
            continue
        op, args = ops[0]
        action = TRANSITION_EVENTS[op]
        if action == 'teleport' and not include_teleports:
            continue
        pn = ev.params(evname)
        tp = sp.trig(ti).params

        def arg(k):
            return tp[args[k]] if k < len(args) and 0 <= args[k] < len(tp) else None

        srcs = sp.sources(ti)
        d = dict(kind=_primary_kind(srcs), action=action, spoke=spoke, trigger=ti,
                 event=evname, op=op, opname=D.EV_OPS[op][0], params=p, sources=srcs,
                 source={'trigger': ti}, source_pos=None, box_min=None, box_max=None,
                 src_bsp=None, dest_spoke=None, dest_bsp=None, dest_bsp_name=None,
                 dest_nav=None, dest_lastnav=None, dest_pos=None, dest_box_min=None,
                 dest_box_max=None, dest_switch=None, dest_status=None, dest_town=None,
                 description='')
        leaves = _flatten(srcs)
        for s in leaves:
            if s['kind'] == 'switch' and 'switch' not in d['source']:
                d['source']['switch'] = s['switch']
                d['source_pos'] = s['pos']
            if s['kind'] == 'box' and 'bound' not in d['source']:
                d['source']['bound'] = s['bound']
                if d['source_pos'] is None:
                    d['source_pos'] = s['pos']
                d['box_min'], d['box_max'], d['src_bsp'] = s['min'], s['max'], s['bsp']
            if s['kind'] == 'special' and 'special' not in d['source']:
                d['source']['special'] = s['special']
        if d['box_min'] is None:       # e.g. town gates: the "party near the gate" box
            for s in srcs:
                for c in s.get('conditions', []):
                    for b in c['setters']:
                        if b['kind'] == 'box' and d['box_min'] is None:
                            d['source']['condition_bound'] = b['bound']
                            d['box_min'], d['box_max'], d['src_bsp'] = b['min'], b['max'], b['bsp']
        calls = [s['trigger'] for s in srcs if s['kind'] in ('call', 'script')]
        if calls:
            d['source']['via_triggers'] = calls
        d['conditions'] = event_conditions(gamedir, spoke, ti)
        for tj in calls:
            d['conditions'] += ['(T%d) %s' % (tj, c) for c in event_conditions(gamedir, spoke, tj)]

        if action == 'segment':
            seg, slot, dsw, dst, bnd = arg(0), arg(1), arg(2), arg(3), arg(4)
            d.update(dest_spoke=seg, dest_bsp=slot, dest_switch=dsw, dest_status=dst)
            d['dest_bsp_name'] = bsp_name(gamedir, seg, slot) if seg is not None else None
            if bnd is not None and 1 <= bnd <= len(sp.B):
                bs = sp.box_src(bnd, 'party must all be inside (IFALLINBOUND)')
                d['source']['bound'] = bnd
                d['box_min'], d['box_max'], d['src_bsp'] = bs['min'], bs['max'], bs['bsp']
                if d['source_pos'] is None:
                    d['source_pos'] = bs['pos']
                src_slot = bs['bsp'] if bs['bsp'] >= 0 else (
                    bsp_at(gamedir, spoke, bs['pos']) if bs['pos'] else -1)
                d['src_bsp'] = src_slot
                d['dest_pos'] = _rebase(gamedir, spoke, src_slot, seg, slot, bs['pos'])
                d['dest_box_min'] = _rebase(gamedir, spoke, src_slot, seg, slot, bs['min'])
                d['dest_box_max'] = _rebase(gamedir, spoke, src_slot, seg, slot, bs['max'])
            if dsw and dsw > 0:
                W2 = _table(gamedir, D.SwitchTable, 'D6SWIT%02d.DAT', seg)
                if 1 <= dsw <= len(W2):
                    d['dest_switch_pos'] = switch_world(gamedir, seg, W2[dsw])
            d['description'] = 'spoke %d -> spoke %d (%s), BSP slot %s %s; set switch %s=%s on arrival' % (
                spoke, seg, D.SPOKE_NAMES.get(seg, '?'), slot, d['dest_bsp_name'], dsw, dst)
        elif action == 'bsp':
            slot, dnav, lnav = arg(0), arg(1), arg(2)
            d.update(dest_spoke=spoke, dest_bsp=slot, dest_nav=dnav, dest_lastnav=lnav,
                     dest_bsp_name=bsp_name(gamedir, spoke, slot))
            if d['box_min'] is not None:
                src_slot = d['src_bsp'] if d['src_bsp'] is not None and d['src_bsp'] >= 0 else \
                    bsp_at(gamedir, spoke, d['source_pos'])
                d['src_bsp'] = src_slot
                d['dest_pos'] = _rebase(gamedir, spoke, src_slot, spoke, slot, d['source_pos'])
                d['dest_box_min'] = _rebase(gamedir, spoke, src_slot, spoke, slot, d['box_min'])
                d['dest_box_max'] = _rebase(gamedir, spoke, src_slot, spoke, slot, d['box_max'])
            d['dest_nav_pos'] = nav_world(gamedir, spoke, slot, dnav)
            d['description'] = 'BSP %s (%s) -> BSP %s (%s) in spoke %d, nav %s (from %s)' % (
                d['src_bsp'], bsp_name(gamedir, spoke, d['src_bsp']) if d['src_bsp'] is not None else '?',
                slot, d['dest_bsp_name'], spoke, dnav, lnav)
        elif action == 'town':
            town = arg(0)
            d['dest_town'] = town
            gates = [(g, TOWN_GATES[(town, g)]) for g in (0, 1) if (town, g) in TOWN_GATES]
            d['town_gates'] = [dict(gate=g, spoke=v[0], entry=v[1], label=v[2]) for g, v in gates]
            d['description'] = 'leave to town %s (%s); town gates lead to %s' % (
                town, TOWN_NAMES.get(town, '?'),
                ', '.join('spoke %d entry %d' % (v[0], v[1]) for g, v in gates))
        elif action == 'teleport':
            x, y, z, slot = arg(0), arg(1), arg(2), arg(3)
            d.update(dest_spoke=spoke, dest_bsp=slot, dest_bsp_name=bsp_name(gamedir, spoke, slot))
            if None not in (x, y, z):
                o = _origin(gamedir, spoke, slot) if slot is not None and slot >= 0 else (0, 0, 0)
                d['dest_pos'] = _add((x, y, z), o) if o else None
            d['description'] = 'teleport actor inside BSP %s (%s) to local %s (only if the actor is in that BSP)' % (
                slot, d['dest_bsp_name'], (x, y, z))
        elif action == 'endgame':
            d['description'] = 'end the game, ending movie %s; returns to town 2' % arg(0)
        res.append(d)
    return res


def list_entrances(gamedir, spoke):
    """Ways into `spoke`: town gates (entry table) and LOADSEGMENT exits of
    every other spoke whose SEGMENT == spoke."""
    out = []
    for (town, gate), (s, e, label) in sorted(TOWN_GATES.items()):
        if s != spoke:
            continue
        ep = ENTRY_POINTS.get((s, e)) or ENTRY_POINTS.get((s, 0))
        out.append(dict(kind='town_gate', town=town, town_name=TOWN_NAMES[town], gate=gate,
                        entry=e, label=label, pos=ep[:3], dir=ep[3],
                        party=entry_party_positions(s, e if (s, e) in ENTRY_POINTS else 0)))
    for s in range(13):
        if s == spoke:
            continue
        for x in list_exits(gamedir, s, include_teleports=False):
            if x['action'] == 'segment' and x['dest_spoke'] == spoke:
                out.append(dict(kind='segment', from_spoke=s, trigger=x['trigger'],
                                bsp=x['dest_bsp'], bsp_name=x['dest_bsp_name'], pos=x['dest_pos'],
                                box_min=x['dest_box_min'], box_max=x['dest_box_max'],
                                switch=x['dest_switch'], status=x['dest_status']))
    return out


def find_return(gamedir, x):
    """For a segment exit, the exit in the destination spoke whose box contains
    the arrival point (the matching way back), or None."""
    if x['action'] != 'segment' or x['dest_pos'] is None:
        return None
    for y in list_exits(gamedir, x['dest_spoke'], include_teleports=False):
        if y['action'] != 'segment' or y['box_min'] is None:
            continue
        if all(y['box_min'][i] - 64 <= x['dest_pos'][i] <= y['box_max'][i] + 64 for i in range(3)):
            return y
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fmt(v):
    if v is None:
        return '-'
    if isinstance(v, tuple):
        return '(%s)' % ', '.join('%g' % c for c in v)
    return str(v)


def _src_lines(srcs, ind='      '):
    out = []
    for s in srcs:
        k = s['kind']
        if k == 'box':
            out.append('%sbox B%d bsp%d who%d en%d [%s] world %s..%s' % (
                ind, s['bound'], s['bsp'], s['who'], s['enabled'], s['role'], _fmt(s['min']), _fmt(s['max'])))
        elif k == 'switch':
            out.append('%sswitch W%d obj %d%s key%d [%s] at %s' % (
                ind, s['switch'], s['objid'], ' (entity)' if s['isentity'] else '', s['keyitem'],
                s['role'], _fmt(s['pos'])))
        elif k == 'special':
            out.append('%sspecial S%d type %d args %s [%s]' % (ind, s['special'], s['type'], s['args'], s['role']))
        elif k in ('call', 'script'):
            out.append('%sT%d %s [%s]' % (ind, s['trigger'], s['event'], s['role']))
            for c in s.get('conditions', []):
                out.append('%s  only if WState[%d] != 0, set by:' % (ind, c['state']))
                out.extend(_src_lines(c['setters'], ind + '    '))
            out.extend(_src_lines(s['sources'], ind + '  '))
        else:
            out.append('%s%s' % (ind, s['role']))
    return out


def print_spoke(gamedir, spoke):
    print('=== spoke %d: %s' % (spoke, D.SPOKE_NAMES.get(spoke, '?')))
    for x in list_exits(gamedir, spoke):
        print('  T%d %s (%s, activation %s)' % (x['trigger'], x['event'], x['action'], x['kind']))
        print('    %s' % x['description'])
        if x['box_min'] is not None:
            print('    box (world) %s .. %s  src bsp %s' % (_fmt(x['box_min']), _fmt(x['box_max']), x['src_bsp']))
        if x['dest_pos'] is not None:
            print('    arrival (world, dest spoke) %s' % _fmt(x['dest_pos']))
        if x.get('dest_nav_pos'):
            print('    dest nav %s at %s' % (x['dest_nav'], _fmt(x['dest_nav_pos'])))
        for c in x.get('conditions', []):
            print('    %s' % c)
        if x['action'] == 'segment':
            r = find_return(gamedir, x)
            print('    way back: %s' % ('spoke %d T%d' % (r['spoke'], r['trigger']) if r else 'none found'))
        for l in _src_lines(x['sources']):
            print(l)
    ent = list_entrances(gamedir, spoke)
    if ent:
        print('  entrances:')
        for e in ent:
            if e['kind'] == 'town_gate':
                print('    town %d %s gate %d "%s" -> entry %d at %s dir 0x%x' % (
                    e['town'], e['town_name'], e['gate'], e['label'], e['entry'], _fmt(e['pos']), e['dir']))
            else:
                print('    from spoke %d T%d -> BSP %s %s arrival %s' % (
                    e['from_spoke'], e['trigger'], e['bsp'], e['bsp_name'], _fmt(e['pos'])))


def _short_src(x):
    parts = []
    for k in ('switch', 'bound', 'special'):
        if k in x['source']:
            parts.append('%s%d' % ({'switch': 'W', 'bound': 'B', 'special': 'S'}[k], x['source'][k]))
    if 'via_triggers' in x['source']:
        parts.append('via ' + ','.join('T%d' % t for t in x['source']['via_triggers']))
    return ' '.join(parts) or '-'


def _p(v):
    return '-' if v is None else '(%s)' % ', '.join('%d' % round(c) for c in v)


def print_markdown(gamedir, spokes):
    for s in spokes:
        ex = [x for x in list_exits(gamedir, s) if x['action'] != 'teleport']
        tp = [x for x in list_exits(gamedir, s) if x['action'] == 'teleport']
        print('#### Spoke %d - %s\n' % (s, D.SPOKE_NAMES.get(s, '?').split(' - ', 1)[-1]))
        if ex:
            print('| trig | event | action | source | box / switch (world) | src bsp | destination | arrival (world) |')
            print('|---|---|---|---|---|---|---|---|')
        for x in ex:
            where = '%s..%s' % (_p(x['box_min']), _p(x['box_max'])) if x['box_min'] else _p(x['source_pos'])
            if x['action'] == 'segment':
                dest = 'spoke %d bsp %s %s, W%s:=%s' % (x['dest_spoke'], x['dest_bsp'], x['dest_bsp_name'],
                                                     x['dest_switch'], x['dest_status'])
                r = find_return(gamedir, x)
                if r:
                    dest += ' (back: T%d)' % r['trigger']
            elif x['action'] == 'bsp':
                dest = 'bsp %s %s nav %s/%s' % (x['dest_bsp'], x['dest_bsp_name'], x['dest_nav'], x['dest_lastnav'])
                if x.get('dest_nav_pos') is None:
                    dest += ' (nav id missing)'
            elif x['action'] == 'town':
                dest = 'town %d %s' % (x['dest_town'], TOWN_NAMES.get(x['dest_town'], '?'))
            else:
                dest = 'end movie %s' % x['params'].get('ENDNUM')
            print('| T%d | %s | %s | %s %s | %s | %s | %s | %s |' % (
                x['trigger'], x['event'], x['action'], x['kind'], _short_src(x), where,
                '-' if x['src_bsp'] is None else x['src_bsp'], dest, _p(x['dest_pos'])))
        if tp:
            print('\nIntra-BSP teleports: ' + '; '.join(
                'T%d %s %s -> %s' % (x['trigger'], x['event'], _short_src(x), _p(x['dest_pos'])) for x in tp))
        ent = list_entrances(gamedir, s)
        gates = [e for e in ent if e['kind'] == 'town_gate']
        if gates:
            print('\nTown gate arrivals: ' + '; '.join(
                '%s gate %d -> entry %d %s dir 0x%x' % (e['town_name'], e['gate'], e['entry'], _p(e['pos']), e['dir'])
                for e in gates))
        print()


def main(argv):
    js = '--json' in argv
    md = '--markdown' in argv
    argv = [a for a in argv if a not in ('--json', '--markdown')]
    if not argv:
        print(__doc__)
        return 1
    g = argv[0]
    spokes = [int(argv[1])] if len(argv) > 1 else list(range(13))
    if md:
        print_markdown(g, spokes)
        return 0
    if js:
        out = dict((s, dict(exits=list_exits(g, s), entrances=list_entrances(g, s))) for s in spokes)
        print(json.dumps(out, indent=1, default=str))
        return 0
    for s in spokes:
        print_spoke(g, s)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))


def set_live(gamedir, spoke, tables=None, dcl_bytes=None, cod_bytes=None):
    """Editor hook: use in-memory (unsaved) tables instead of the files.
    tables: {'trig': TriggerTable, 'boun': ..., 'swit': ..., 'spec': ...}.
    Object lists are always read from disk (positions of saved objects)."""
    pats = {'trig': 'D6TRIG%02d.DAT', 'boun': 'D6BOUN%02d.DAT', 'swit': 'D6SWIT%02d.DAT', 'spec': 'D6SPEC%02d.DAT'}
    for k, t in (tables or {}).items():
        if k in pats:
            _CACHE[(gamedir, pats[k], spoke)] = t
    if dcl_bytes is not None and cod_bytes is not None:
        ev = _Events.__new__(_Events)
        D_ = D
        orig = D_.read_file
        try:
            D_.read_file = lambda g, n: dcl_bytes if n.upper() == 'EVENTS.DCL' else (
                cod_bytes if n.upper() == 'EVENTS.COD' else orig(g, n))
            ev.__init__(gamedir)
        finally:
            D_.read_file = orig
        _CACHE[(gamedir, 'events')] = ev


def clear_cache():
    _CACHE.clear()
