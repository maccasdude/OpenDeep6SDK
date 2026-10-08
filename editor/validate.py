"""
d6edit - map checks (Tools > Check spoke): references that point nowhere,
broken nav links, unknown events, missing textures/models.
"""
import struct

import numpy as np


ERR, WARN, INFO = 'error', 'warning', 'info'


def check(win):
    """[(level, text, thing or None)]"""
    sp, game = win.spoke, win.game
    out = []
    add = lambda lvl, txt, o=None: out.append((lvl, txt, o))
    ntab = lambda k: len(sp.tables[k][1].records) if k in sp.tables else 0
    db = {'M': len(game.mons), 'I': len(game.items), 'P': len(game.props)}

    # objects
    for o in sp.objects:
        if o.cat != 'object' or o.kind not in 'MIP':
            continue
        n = o.recno
        if not n or not (1 <= n <= db[o.kind]):
            add(ERR, '%s: record %s is not in the %s database' % (o.label(), n, o.kind), o)
        elif win.models is not None and win.models.path_for(o.kind, n) is None:
            add(INFO, '%s: no 3D model in the game for this record' % o.label(), o)

    def obj_ok(oid):
        t = win.object_by_id(oid)
        return t is not None and t.kind != '0'

    recs = win.records
    for r in recs.get('swit', []):
        g = r.rec
        if not g.get('isentity') and g.get('objid') and not obj_ok(g.get('objid')):
            add(ERR, '%s: object %d does not exist (or is blank)' % (r.label(), g.get('objid')), r)
        if g.get('target') < 0 and -g.get('target') > ntab('trig'):
            add(ERR, '%s: trigger %d does not exist' % (r.label(), -g.get('target')), r)
        if g.get('keyitem') and not (1 <= g.get('keyitem') <= db['I']):
            add(ERR, '%s: key item %d does not exist' % (r.label(), g.get('keyitem')), r)
    for r in recs.get('trap', []):
        g = r.rec
        if g.get('objid') and not obj_ok(g.get('objid')):
            add(ERR, '%s: container object %d does not exist' % (r.label(), g.get('objid')), r)
        if g.get('monrec') and not (1 <= g.get('monrec') <= db['M']):
            add(ERR, '%s: monster record %d does not exist' % (r.label(), g.get('monrec')), r)
    for r in recs.get('spec', []):
        g = r.rec
        if g.get('trigger') > 0 and g.get('trigger') > ntab('trig'):
            add(ERR, '%s: trigger %d does not exist' % (r.label(), g.get('trigger')), r)
        if g.get('type') == 2:
            for a in g.get('args'):
                if a and not obj_ok(a):
                    add(ERR, '%s: monster object %d does not exist' % (r.label(), a), r)
    for o in sp.objects:
        if o.cat != 'bound':
            continue
        g = o.rec
        if g.get('trigger') > 0 and g.get('trigger') > ntab('trig'):
            add(ERR, '%s: trigger %d does not exist' % (o.label(), g.get('trigger')), o)
        b = g.get('bsp')
        if b is not None and b >= 0 and sp.bsp_by_slot(b) is None:
            add(ERR, '%s: BSP %d is not loaded in this spoke' % (o.label(), b), o)
        if g.get('who') == 4 and not obj_ok(g.get('objid')):
            add(ERR, '%s: watched object %d does not exist' % (o.label(), g.get('objid')), o)
        mn, mx = np.array(g.get('min')), np.array(g.get('max'))
        if np.any(mx < mn):
            add(WARN, '%s: max is below min (empty box)' % o.label(), o)

    dcl = game.dcl
    for r in recs.get('trig', []):
        ev = r.rec.get('event')
        if not ev:
            continue
        i = dcl.index(ev)
        if i < 0:
            add(ERR, '%s: event %s is not in EVENTS.DCL' % (r.label(), ev), r)
        elif r.rec.get('nparams') != dcl.nparams[i]:
            add(INFO, '%s: stored parameter count %d, event has %d (the game uses the event\'s)' % (
                r.label(), r.rec.get('nparams'), dcl.nparams[i]), r)

    # nav
    for b in sp.bsps:
        if b.nvs is None:
            continue
        pts = b.nvs.points
        ids = {}
        for i, p in enumerate(pts):
            if p.id in ids:
                add(ERR, '%s nav %d: id %d also used by nav %d' % (b.name.upper(), i, p.id, ids[p.id]), None)
            ids[p.id] = i
            for l in (p.l0, p.l1, p.l2, p.l3, p.l4, p.l5):
                if l >= len(pts):
                    add(ERR, '%s nav %d: link to %d out of range' % (b.name.upper(), i, l), None)
                elif l >= 0:
                    q = pts[l]
                    if i not in (q.l0, q.l1, q.l2, q.l3, q.l4, q.l5):
                        add(INFO, '%s nav %d -> %d is one-way' % (b.name.upper(), i, l), None)
    if sp.nav is not None:
        recs_n = sp.nav.records
        for i, r in enumerate(recs_n, 1):
            if r.raw[0] != ord('N'):
                continue
            for l in struct.unpack_from('<6H', r.raw, 36):
                if l and (l > len(recs_n) or recs_n[l - 1].raw[0] != ord('N')):
                    add(ERR, 'terrain nav %d: link to %d which is not a nav point' % (i, l), None)
    if 'link' in sp.tables:
        idsets = dict((b.slot, set(p.id for p in b.nvs.points)) for b in sp.bsps if b.nvs is not None)
        for k, r in enumerate(sp.tables['link'][1].records, 1):
            for nav, bsp in ((r.get('navA'), r.get('bspA')), (r.get('navB'), r.get('bspB'))):
                if bsp == -1:
                    if sp.nav is None or not (1 <= nav <= len(sp.nav.records)) or sp.nav.records[nav - 1].raw[0] != ord('N'):
                        add(ERR, 'nav link %d: terrain nav %d does not exist' % (k, nav), None)
                elif bsp not in idsets:
                    add(ERR, 'nav link %d: BSP %d is not in this spoke' % (k, bsp), None)
                elif nav not in idsets[bsp]:
                    add(ERR, 'nav link %d: BSP %d has no nav id %d' % (k, bsp, nav), None)

    # terrain
    ted = sp.ted
    if ted is not None:
        used = np.unique(ted.t['tex']).tolist()
        for i in used:
            if i and ted.layer_for(int(i)) < 0:
                n = int((ted.t['tex'] == i).sum())
                add(WARN, 'terrain: BLT entry %d (%s) has no texture; %d tiles show nothing' % (
                    i, ted.map.blt_name(int(i)) or '-', n), None)

    for p in sp.saved_state_files():
        add(INFO, '%s exists: the game shows the saved state of this spoke, not the edited objects' %
            p.split('/')[-1], None)
    order = {ERR: 0, WARN: 1, INFO: 2}
    out.sort(key=lambda x: order[x[0]])
    return out
