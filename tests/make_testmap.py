#!/usr/bin/env python3
"""Writes a small test level as a .map (map space, z up): a room with a
pillar, a sunken pool of water, a sliding door into a side room and lights."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'formats'))
import d6map

WALL = 'crypta/020020wall-tile'
FLOOR = 'crypta/011009-flr-rck'
CEIL = 'crypta/011064clg'
DOOR = 'crypta/011036-metal-brz'
WATER = 'crypta/020032marbtile'

def box(x0, y0, z0, x1, y1, z1, tex, contents=0, top=None, bottom=None):
    def f(p1, p2, p3, t):
        # Valve axes chosen per dominant normal, scale 0.5 (Deep6 density)
        fc = d6map.Face([p1, p2, p3], t)
        n, _ = fc.plane()
        ax = max(range(3), key=lambda i: abs(n[i]))
        if ax == 2:
            u, v = (1, 0, 0), (0, -1, 0)
        elif ax == 0:
            u, v = (0, 1, 0), (0, 0, -1)
        else:
            u, v = (1, 0, 0), (0, 0, -1)
        fc.u, fc.v, fc.rot, fc.sx, fc.sy = u + (0,), v + (0,), 0, 0.5, 0.5
        fc.contents = contents
        return fc
    t = top or tex
    bt = bottom or tex
    return d6map.Brush([
        f((x0, y0, z1), (x0, y1, z1), (x1, y1, z1), t),      # top  (+z)
        f((x0, y1, z0), (x0, y0, z0), (x1, y0, z0), bt),     # bottom (-z)
        f((x0, y0, z0), (x0, y1, z0), (x0, y1, z1), tex),    # -x
        f((x1, y1, z0), (x1, y0, z0), (x1, y0, z1), tex),    # +x
        f((x1, y0, z0), (x0, y0, z0), (x0, y0, z1), tex),    # -y
        f((x0, y1, z0), (x1, y1, z0), (x1, y1, z1), tex),    # +y
    ])

def main(out, dx=0, dy=0):
    w = []
    # main room 0..512 x 0..512, floor at z=0, ceiling 192
    w.append(box(-16, -16, -16, 528, 528, 0, WALL, top=FLOOR))      # floor
    w.append(box(-16, -16, 192, 528, 528, 208, WALL, bottom=CEIL))  # ceiling
    w.append(box(-16, -16, 0, 0, 528, 192, WALL))                   # -x wall
    w.append(box(-16, -16, 0, 528, 0, 192, WALL))                   # -y wall
    w.append(box(-16, 512, 0, 528, 528, 192, WALL))                 # +y wall
    # +x wall with a doorway (y 224..288, z 0..128) into a side room
    w.append(box(512, -16, 0, 528, 224, 192, WALL))
    w.append(box(512, 288, 0, 528, 528, 192, WALL))
    w.append(box(512, 224, 128, 528, 288, 192, WALL))
    # side room 528..784 x 128..384
    w.append(box(528, 112, -16, 800, 400, 0, WALL, top=FLOOR))
    w.append(box(528, 112, 160, 800, 400, 176, WALL, bottom=CEIL))
    w.append(box(528, 112, 0, 800, 128, 160, WALL))
    w.append(box(528, 384, 0, 800, 400, 160, WALL))
    w.append(box(784, 128, 0, 800, 384, 160, WALL))
    w.append(box(528, 128, 128, 784, 384, 160, WALL))  # lower part of side ceiling region
    # pillar
    w.append(box(224, 96, 0, 288, 160, 192, WALL))
    # pool: hole in the floor is made by a separate floor layout -> use a basin
    w.append(box(96, 320, 0, 256, 336, 16, WALL))
    w.append(box(96, 448, 0, 256, 464, 16, WALL))
    w.append(box(96, 336, 0, 112, 448, 16, WALL))
    w.append(box(240, 336, 0, 256, 448, 16, WALL))
    water = box(112, 336, 0, 240, 448, 12, WATER, contents=d6map.CONT_WATER)
    ents = [
        d6map.Entity([('classname', 'worldspawn'), ('worldtype', '0'), ('d6ambient', '10')], w + [water]),
        d6map.Entity([('classname', 'func_door'), ('d6type', '1'), ('d6axis', '1'), ('d6maxmove', '120'),
                      ('d6string', 'Test door')], [box(516, 224, 0, 524, 288, 128, DOOR)]),
        d6map.Entity([('classname', 'light'), ('origin', '256 256 150'), ('d6light', '400'), ('_color', '255 80 60')]),
        d6map.Entity([('classname', 'light'), ('origin', '96 96 120'), ('d6light', '300'), ('_color', '255 200 150')]),
        d6map.Entity([('classname', 'light'), ('origin', '660 256 120'), ('d6light', '220')]),
        d6map.Entity([('classname', 'info_player_start'), ('origin', '128 128 40')]),
    ]
    for e in ents:
        for b in e.brushes:
            for f in b.faces:
                f.pts = [(p[0] + dx, p[1] + dy, p[2]) for p in f.pts]
                # keep the texture fixed to the world grid
        o = e.get('origin')
        if o:
            x, y, z = (float(c) for c in o.split())
            e.set('origin', '%g %g %g' % (x + dx, y + dy, z))
    open(out, 'w').write(d6map.write(ents))

if __name__ == '__main__':
    main(sys.argv[1], *(float(a) for a in sys.argv[2:4]))
