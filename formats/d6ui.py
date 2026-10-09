#!/usr/bin/env python3
"""
d6ui - mouse pointer files (*.ptr) and DEEP6.PAL. Fonts are in d6font.py.
See docs/formats/ui.md.

usage:
  d6ui.py ptr FILE.ptr                 show a pointer description
  d6ui.py pal DEEP6.PAL OUT.png        the palette as a 16x16 swatch
  d6ui.py --selftest GAMEDIR
"""
import glob
import os
import struct
import sys

# LoadPointers_ (lmouse.c), in load order
GAME_POINTERS = ['hourglas', 'vxpoint', 'vxfight', 'vxsorcer', 'vxuse', 'vxdrag', 'vxthief',
                 'vxtalk', 'vxgive', 'mptritem', 'vtarget', 'vblutarg', 'vnotarg', 'vnobtarg',
                 'vxlook', 'vxwalk', 'vxwing', 'vxbreath', 'vxgaze', 'vxvamp', 'vxhide',
                 'vxcurse', 'vxfly']


class Pointer(object):
    """CreateMousePointer_ (mousetim.c): bitmap name, hot spot x / y, frame
    count, frame width / height (<= 128), sequence length (<= 16), then per
    step 'frame time_ms'."""

    def __init__(self):
        self.bitmap = ''
        self.hotx = self.hoty = 0
        self.frames, self.width, self.height = 1, 32, 32
        self.sequence = [(0, 100)]
        self._raw = None

    @classmethod
    def parse(cls, data):
        p = cls()
        text = data.decode('latin-1')
        tok = text.split()
        p.bitmap = tok[0]
        p.hotx, p.hoty, p.frames, p.width, p.height, n = (int(t) for t in tok[1:7])
        rest = [int(t) for t in tok[7:7 + 2 * n]]
        p.sequence = [(rest[2 * i], rest[2 * i + 1]) for i in range(n)]
        p._raw = bytes(data)
        p._parsed = p._values()
        return p

    def _values(self):
        return (self.bitmap, self.hotx, self.hoty, self.frames, self.width, self.height,
                tuple(self.sequence))

    def build(self):
        if self._raw is not None and self._values() == self._parsed:
            return self._raw                     # unchanged: keep the original spacing
        lines = [self.bitmap] + ['%d' % v for v in (self.hotx, self.hoty, self.frames,
                                                   self.width, self.height, len(self.sequence))]
        lines += ['%d %d' % fs for fs in self.sequence]
        return ('\r\n'.join(lines) + '\r\n\r\n').encode('latin-1')

    def check(self):
        """Problems the game would reject or silently change."""
        out = []
        if self.width > 128 or self.height > 128:
            out.append('frame larger than 128 (clamped)')
        if len(self.sequence) > 16:
            out.append('more than 16 steps (cut)')
        for f, t in self.sequence:
            if not 0 <= f < self.frames:
                out.append('step frame %d outside 0..%d (becomes 0)' % (f, self.frames - 1))
            if not 10 <= t <= 1000:
                out.append('step time %d outside 10..1000 (becomes 100)' % t)
        return out


class Palette(object):
    """DEEP6.PAL (Palette_Load_): 256 RGB triples + a 32 x 256 byte table."""

    @classmethod
    def parse(cls, data):
        p = cls()
        p.rgb = [tuple(data[3 * i:3 * i + 3]) for i in range(256)]
        p.table = bytes(data[768:768 + 8192])
        p.tail = bytes(data[768 + 8192:])
        return p

    def build(self):
        return bytes(c for t in self.rgb for c in t) + self.table + self.tail

    def swatch(self, cell=16):
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (16 * cell, 16 * cell))
        d = ImageDraw.Draw(img)
        for i, c in enumerate(self.rgb):
            x, y = (i % 16) * cell, (i // 16) * cell
            d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=c)
        return img


def _find(gamedir, name):
    for f in os.listdir(gamedir):
        if f.lower() == name.lower():
            return os.path.join(gamedir, f)
    return None


def selftest(gamedir):
    ptrs = sorted(p for p in glob.glob(os.path.join(gamedir, '*')) if p.lower().endswith('.ptr'))
    ok = 0
    for p in ptrs:
        data = open(p, 'rb').read()
        ptr = Pointer.parse(data)
        same = ptr.build() == data
        # also check the regenerated text parses back to the same values
        again = Pointer.parse(Pointer.parse(data)._rebuild_test())._values() == ptr._values()
        ok += same and again
        if not (same and again):
            print('DIFFERS', p)
    pp = _find(gamedir, 'DEEP6.PAL')
    n = len(ptrs)
    if pp:
        data = open(pp, 'rb').read()
        n += 1
        ok += Palette.parse(data).build() == data
    print('TOTAL %d files (%d pointers + DEEP6.PAL), %d identical' % (n, len(ptrs), ok))
    return 0 if ok == n else 1


def _rebuild_test(self):
    raw, self._raw = self._raw, None
    try:
        return self.build()
    finally:
        self._raw = raw


Pointer._rebuild_test = _rebuild_test


def main(argv):
    a = argv[1:]
    if not a:
        print(__doc__)
        return 1
    if a[0] == '--selftest':
        return selftest(a[1])
    if a[0] == 'ptr':
        p = Pointer.parse(open(a[1], 'rb').read())
        print('bitmap %s, hot spot (%d, %d), %d frames of %dx%d, sequence %s' % (
            p.bitmap, p.hotx, p.hoty, p.frames, p.width, p.height, p.sequence))
        for x in p.check():
            print('  warning:', x)
        return 0
    if a[0] == 'pal':
        Palette.parse(open(a[1], 'rb').read()).swatch().save(a[2])
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
