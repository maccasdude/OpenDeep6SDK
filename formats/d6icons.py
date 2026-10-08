#!/usr/bin/env python3
"""
d6icons.py - inventory icons of items (itemicon/*.bmp).

The icon table is compiled into deep6.exe (_icondata, VA 0x5D1840, 246 x 8
bytes: char *name -> itemicon\\<name>.bmp, u8 grid width, u8 grid height;
LoadItemIcons_). D6ITEM field 'icon' indexes it. The bitmaps are 24-bit BMPs
(nearly all 48x43) with pure green (0,255,0) as the transparent colour.

New items can share icons, get a replaced bitmap, or an icon rendered from
their 3D model (render_icon).

CLI
  d6icons.py --list GAMEDIR
  d6icons.py --render GAMEDIR ITEMRECORD out.bmp
"""
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import d6data   # noqa: E402
import d6model  # noqa: E402

ICON_TABLE = (0x5D1840, 246, 8)
KEY = (0, 255, 0)
ICON_SIZE = (48, 43)


def icon_table(gamedir):
    """[(index, name, grid_w, grid_h)]"""
    exe = open(d6data.find_file(gamedir, 'deep6.exe'), 'rb').read()
    va, n, stride = ICON_TABLE
    o = d6model._exe_va_to_off(exe, va)
    out = []
    for i in range(n):
        p, w, h = struct.unpack_from('<IBB', exe, o + i * stride)
        try:
            name = d6data.cstr(exe[d6model._exe_va_to_off(exe, p):d6model._exe_va_to_off(exe, p) + 64]) if p else ''
        except ValueError:
            name = ''
        out.append((i, name, w, h))
    return out


def icon_path(gamedir, index):
    tab = icon_table(gamedir)
    if not 0 <= index < len(tab) or not tab[index][1]:
        return None
    name = tab[index][1]
    if not name.lower().endswith('.bmp'):
        name += '.bmp'
    return d6data.find_file(gamedir, 'itemicon/' + name) or os.path.join(gamedir, 'itemicon', name)


def load_icon(path):
    """RGBA image (green key -> alpha 0)."""
    from PIL import Image
    a = np.array(Image.open(path).convert('RGB'))
    alpha = np.where((a == KEY).all(-1), 0, 255).astype(np.uint8)
    return np.dstack([a, alpha])


def save_icon(path, rgba, size=None):
    """Write a 24-bit BMP icon; transparent pixels become the green key."""
    from PIL import Image
    a = np.asarray(rgba, np.uint8)
    if a.shape[2] == 3:
        a = np.dstack([a, np.full(a.shape[:2], 255, np.uint8)])
    if size and (a.shape[1], a.shape[0]) != size:
        rgb = np.array(Image.fromarray(np.ascontiguousarray(a[..., :3])).resize(size, Image.LANCZOS))
        al = np.array(Image.fromarray(np.ascontiguousarray(a[..., 3])).resize(size, Image.NEAREST))
        a = np.dstack([rgb, al])
    rgb = a[..., :3].copy()
    key = a[..., 3] < 128
    rgb[key] = KEY
    near = (~key) & (rgb[..., 1] > 240) & (rgb[..., 0] < 16) & (rgb[..., 2] < 16)
    rgb[near] = (8, 230, 8)                 # keep real green pixels from turning transparent
    Image.fromarray(rgb).save(path, 'BMP')


def render_icon(model, size=ICON_SIZE, yaw=30.0, pitch=35.0):
    """Icon from a 3D model: rendered on the key colour, cropped and fitted."""
    big = 4 * max(size)
    img = d6model.render(model, size=big, yaw=yaw, pitch=pitch, bg=KEY, show_axes=False, sprites=False)
    key = (img == KEY).all(-1)
    ys, xs = np.nonzero(~key)
    if not len(xs):
        return np.dstack([img, np.zeros(img.shape[:2], np.uint8)])
    img = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    key = key[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = key.shape
    tw, th = size
    s = min((tw - 4) / float(w), (th - 4) / float(h))
    from PIL import Image
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    rgb = np.array(Image.fromarray(img).resize((nw, nh), Image.LANCZOS))
    al = np.array(Image.fromarray(np.where(key, 0, 255).astype(np.uint8)).resize((nw, nh), Image.NEAREST))
    out = np.zeros((th, tw, 4), np.uint8)
    y0, x0 = (th - nh) // 2, (tw - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw, :3] = rgb
    out[y0:y0 + nh, x0:x0 + nw, 3] = al
    return out


def main(argv):
    a = argv[1:]
    if len(a) >= 2 and a[0] == '--list':
        for i, n, w, h in icon_table(a[1]):
            print('%3d %-24s %dx%d' % (i, n, w, h))
        return 0
    if len(a) >= 4 and a[0] == '--render':
        m = d6model.load_model(a[1], d6model.model_for(a[1], 'I', int(a[2])))
        save_icon(a[3], render_icon(m))
        print('wrote', a[3])
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
