#!/usr/bin/env python3
"""
d6font - Deep6 bitmap fonts (D6FNTnn.FNT, with D6FNTnn.P16 for colour fonts).

Layout from Font_Load_ / Font_DrawChar_ / Font_StrWidth_ (font.c), see
docs/formats/ui.md:

  u16 cellw, u16 cellh, u16 mode (0 one colour, 1 palette colours),
  u16 count (128), i16 spacing (added after each glyph), u16 0,
  u16 glyphsize (= 2 + cellw * cellh)
  count x { u16 width; u8 pixels[cellh][width], padded to glyphsize }
                                                glyph c at 14 + c * glyphsize

Mode 0 pixels: 0 transparent, 1 the text colour, other small values the
background / shadow colours (_gFGColor/_gBGColor/_gSGColor). Mode 1 pixels:
0 transparent, else an index into the font's 16-bit palette (D6FNTnn.P16,
32 shade rows x 256 RGB565; row 31 is drawn).

usage:
  d6font.py sheet FONT.FNT OUT.png [PAL.P16]    all glyphs on one sheet
  d6font.py text FONT.FNT "string" OUT.png [PAL.P16]
  d6font.py --selftest GAMEDIR
"""
import glob
import os
import struct
import sys

# deep6.c LoadFonts_: font slot -> file (colour fonts load the .P16 of the same name)
GAME_FONTS = ['D6FNT01', 'D6FNT03', 'D6FNT54', 'D6FNT40', 'D6FNT49', 'D6FNT42', 'D6FNT58',
              'D6FNT50', 'D6FNT47', 'D6FNT56', 'D6FNT48', 'D6FNT53', 'D6FNT57', 'D6FNT52',
              'D6FNT43', 'D6FNT26', 'D6FNT14']


class Font(object):
    @classmethod
    def parse(cls, data):
        f = cls()
        (f.cellw, f.cellh, f.mode, f.count, f.spacing, f.zero,
         f.glyphsize) = struct.unpack_from('<HHHHhHH', data, 0)
        if f.glyphsize != 2 + f.cellw * f.cellh:
            raise ValueError('glyph size %d != 2 + %d x %d' % (f.glyphsize, f.cellw, f.cellh))
        f.glyphs = []
        for c in range(f.count):
            o = 14 + c * f.glyphsize
            w = struct.unpack_from('<H', data, o)[0]
            f.glyphs.append((w, bytes(data[o + 2:o + f.glyphsize])))
        f.tail = bytes(data[14 + f.count * f.glyphsize:])
        return f

    def build(self):
        out = bytearray(struct.pack('<HHHHhHH', self.cellw, self.cellh, self.mode, self.count,
                                    self.spacing, self.zero, self.glyphsize))
        for w, px in self.glyphs:
            out += struct.pack('<H', w) + px
        return bytes(out + self.tail)

    def text_width(self, s):
        return sum(self.glyphs[ord(ch) & 0x7F][0] + self.spacing for ch in s)

    def render(self, s, palette=None, fg=(255, 255, 255), shadow=(40, 40, 40)):
        """Draw a string; returns a PIL RGBA image."""
        from PIL import Image
        img = Image.new('RGBA', (max(1, self.text_width(s)), self.cellh), (0, 0, 0, 0))
        pal = None
        if self.mode == 1 and palette is not None:
            row = struct.unpack_from('<256H', palette, 31 * 512)
            pal = [((p >> 11 & 31) << 3, (p >> 5 & 63) << 2, (p & 31) << 3) for p in row]
        x = 0
        for ch in s:
            w, px = self.glyphs[ord(ch) & 0x7F]
            for yy in range(self.cellh):
                for xx in range(w):
                    v = px[yy * w + xx]
                    if not v:
                        continue
                    if self.mode == 1:
                        col = pal[v] if pal else (v, v, v)
                    else:
                        col = fg if v == 1 else shadow
                    img.putpixel((x + xx, yy), col + (255,))
            x += w + self.spacing
        return img

    def sheet(self, palette=None):
        from PIL import Image
        cols = 16
        cw, ch = self.cellw + 2, self.cellh + 2
        img = Image.new('RGBA', (cols * cw, (self.count + cols - 1) // cols * ch), (0, 0, 0, 255))
        for c in range(self.count):
            g = self.render(chr(c), palette)
            img.paste(g, ((c % cols) * cw + 1, (c // cols) * ch + 1), g)
        return img


def load(path):
    return Font.parse(open(path, 'rb').read())


def selftest(gamedir):
    paths = sorted(p for p in glob.glob(os.path.join(gamedir, '*')) if p.upper().endswith('.FNT'))
    ok = 0
    for p in paths:
        data = open(p, 'rb').read()
        same = Font.parse(data).build() == data
        ok += same
        if not same:
            print('DIFFERS', p)
    print('TOTAL %d fonts, %d byte identical' % (len(paths), ok))
    return 0 if ok == len(paths) else 1


def _pal(argv, i):
    return open(argv[i], 'rb').read() if len(argv) > i else None


def main(argv):
    a = argv[1:]
    if not a:
        print(__doc__)
        return 1
    if a[0] == '--selftest':
        return selftest(a[1])
    if a[0] == 'sheet':
        load(a[1]).sheet(_pal(a, 3)).save(a[2])
        return 0
    if a[0] == 'text':
        load(a[1]).render(a[2], _pal(a, 4)).save(a[3])
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
