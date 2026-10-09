"""d6efx.py - Wizards & Warriors (deep6.exe) spell / effect data readers+writers.

Formats
  emitters.dat   particle emitter descriptors (LoadEmitterDesc_)      EmitterFile
  efxgfx/*.ant   animated sprite: 'ANT ' raw or 'CNT ' span-compressed  Ant
  efxgfx/*.alf   alpha sprite: 'ALF ' v1, u8 alpha[w*h] + u16 rgb565   Alf
  deep6.exe      spell table _gSpellData 0x5D7D80, 105 x 0x30           SpellTable
Every class: parse(bytes) -> obj, obj.to_bytes() -> bytes (byte-identical).
Usage: python3 d6efx.py <gamedir>   (round-trip self test + spell dump)
"""
import struct, sys, os

# ---------------------------------------------------------------- emitters.dat
EMIT_FIELDS = [  # (name, offset, struct fmt)
    ('name', 0x00, '24s'), ('flags', 0x18, 'I'),
    ('ang1_base', 0x1C, 'f'), ('ang1_rand', 0x20, 'f'),
    ('ang2_base', 0x24, 'f'), ('ang2_rand', 0x28, 'f'),
    ('speed_base', 0x2C, 'f'), ('speed_rand', 0x30, 'f'),
    ('life_base', 0x34, 'i'), ('life_rand', 0x38, 'i'),
    ('size_base', 0x3C, 'i'), ('size_rand', 0x40, 'i'),
    ('alpha_size', 0x44, 'i'), ('rate', 0x48, 'f'), ('unk4c', 0x4C, 'I'),
    ('gravity', 0x50, '3f'), ('unk5c', 0x5C, 'I'),
    ('frames', 0x60, 'i'), ('child', 0x64, 'i'), ('max_particles', 0x68, 'i'),
    ('duration', 0x6C, 'i'), ('preadvance', 0x70, 'i'),
    ('anim', 0x74, 'i'), ('pal', 0x78, 'i'), ('alpha', 0x7C, 'i'),
]
# selectors (resolved at load time): anim -> _g_particleAnim[anim] = FXC index
PARTICLE_ANIM = {1: 0x2a, 2: 0x2b, 3: 0x2c, 4: 0x2e, 5: 0x2d, 6: 0x2f, 7: 0x30, 8: 0x31, 9: 0x32}
PARTICLE_PAL = {1: 'particle_Pal16', 2: 'rainbow_Pal16', 3: 'expanim_Pal16', 4: 'efxgfx/light.p16'}
PARTICLE_ALPHA = {1: 'mmglow', 2: 'nukeGlow', 3: 'eyeGlow', 4: 'sparkFlare', 5: 'discGlow',
                  6: 'skullAlpha', 7: 'fistAlpha', 8: 'zapglow.alf', 9: 'fbirdFlare',
                  10: 'fireAlpha', 11: 'lightnglow.alf', 12: 'lightray.alf',
                  13: 'lightbeamGlow', 14: 'fireglow2.alf', 15: 'blueglow.alf',
                  16: 'greenglow.alf'}


class Emitter:
    def __init__(self, raw):
        self.raw = bytearray(raw)
        for n, o, f in EMIT_FIELDS:
            v = struct.unpack_from('<' + f, raw, o)
            setattr(self, n, v if len(v) > 1 else v[0])
        self.pad_name = raw[:24]

    @property
    def label(self):
        return self.name.split(b'\0')[0].decode('latin1')

    def to_bytes(self):
        b = bytearray(self.raw)
        for n, o, f in EMIT_FIELDS:
            v = getattr(self, n)
            struct.pack_into('<' + f, b, o, *(v if isinstance(v, tuple) else (v,)))
        return bytes(b)


class EmitterFile:
    def __init__(self, emitters, tail=b''):
        self.emitters, self.tail = emitters, tail

    @classmethod
    def parse(cls, d):
        n = struct.unpack_from('<i', d)[0]
        em = [Emitter(d[4 + i * 128:4 + i * 128 + 128]) for i in range(n)]
        return cls(em, d[4 + n * 128:])

    def to_bytes(self):
        return struct.pack('<i', len(self.emitters)) + b''.join(e.to_bytes() for e in self.emitters) + self.tail


# ---------------------------------------------------------------- .ant / .alf
class Ant:
    """'ANT ': u32 n, u32 w, u32 h, u16 p16[8192], n*(u8[w*h]).
    'CNT ': u32 n, u16 w, u16 h, u16 p16[8192], n*(u32 size, u32 rowoff[h], u8 data[size]).
    CNT row (Trans_Decode_): alternating runs starting with SKIP: byte k; skip run
    -> k transparent pixels (index 0); copy run -> k literal bytes follow. Until w done."""

    def __init__(self, kind, n, w, h, p16, frames, tail=b''):
        self.kind, self.n, self.w, self.h, self.p16, self.frames, self.tail = kind, n, w, h, p16, frames, tail

    @classmethod
    def parse(cls, d):
        mag = d[:4]
        if mag == b' TNA':
            n, w, h = struct.unpack_from('<3I', d, 4)
            p = 16 + 0x4000
            fr = [d[p + i * w * h:p + (i + 1) * w * h] for i in range(n)]
            return cls('ANT', n, w, h, d[16:p], fr, d[p + n * w * h:])
        if mag == b' TNC':
            n, w, h = struct.unpack_from('<IHH', d, 4)
            p = 12 + 0x4000
            fr = []
            for i in range(n):
                sz = struct.unpack_from('<I', d, p)[0]
                rows = struct.unpack_from('<%dI' % h, d, p + 4)
                data = d[p + 4 + 4 * h:p + 4 + 4 * h + sz]
                fr.append((list(rows), data))
                p += 4 + 4 * h + sz
            return cls('CNT', n, w, h, d[12:12 + 0x4000], fr, d[p:])
        raise ValueError('not an ANT/CNT file')

    def to_bytes(self):
        if self.kind == 'ANT':
            return b' TNA' + struct.pack('<3I', self.n, self.w, self.h) + self.p16 + b''.join(self.frames) + self.tail
        out = [b' TNC', struct.pack('<IHH', self.n, self.w, self.h), self.p16]
        for rows, data in self.frames:
            out += [struct.pack('<I', len(data)), struct.pack('<%dI' % self.h, *rows), data]
        return b''.join(out) + self.tail

    def frame_pixels(self, i):
        """w*h palette indices (0 = transparent for CNT; ANT frames used masked, index 0)."""
        if self.kind == 'ANT':
            return bytes(self.frames[i])
        rows, data = self.frames[i]
        out = bytearray(self.w * self.h)
        for y, ro in enumerate(rows):
            p, x, skip = ro, 0, True
            while x < self.w:
                k = data[p]; p += 1
                if not skip:
                    out[y * self.w + x:y * self.w + x + k] = data[p:p + k]; p += k
                x += k; skip = not skip
        return bytes(out)

    @staticmethod
    def encode_row(row):
        """Inverse of the row decoder (runs <= 255); may differ from the shipped encoder."""
        out, x, skip, w = bytearray(), 0, True, len(row)
        while x < w:
            k = 0
            while x + k < w and k < 255 and ((row[x + k] == 0) == skip):
                k += 1
            out.append(k)
            if not skip:
                out += row[x:x + k]
            x += k; skip = not skip
        if not skip:  # last run was a skip: rows always end on a copy run (maybe empty)
            out.append(0)
        return bytes(out)

    def rgb565(self, idx, shade=31):
        return struct.unpack_from('<H', self.p16, (shade * 256 + idx) * 2)[0]


class Alf:
    """'ALF ' u32 1, u32 w, u32 h, u8 alpha[w*h] (0..31), u16 rgb565[w*h] (Alpha_Load_)."""

    def __init__(self, w, h, alpha, color, ver=1, tail=b''):
        self.w, self.h, self.alpha, self.color, self.ver, self.tail = w, h, alpha, color, ver, tail

    @classmethod
    def parse(cls, d):
        if d[:4] != b' FLA':
            raise ValueError('not ALF')
        ver, w, h = struct.unpack_from('<3I', d, 4)
        a = d[16:16 + w * h]
        c = d[16 + w * h:16 + 3 * w * h]
        return cls(w, h, a, c, ver, d[16 + 3 * w * h:])

    def to_bytes(self):
        return b' FLA' + struct.pack('<3I', self.ver, self.w, self.h) + self.alpha + self.color + self.tail


# ---------------------------------------------------------------- spell table (exe)
SPELL_VA, SPELL_N, SPELL_SZ = 0x5D7D80, 105, 0x30
SCHOOLS = {0: 'Spirit', 1: 'Fire', 2: 'Air/Ice', 3: 'Earth/Nature', 4: 'Stone', 5: 'Death/Blood', -1: 'none'}
SPELL_FIELDS = [('name', 0x00, '32s'), ('id', 0x20, 'h'), ('school', 0x22, 'b'), ('level', 0x23, 'B'),
                ('slot', 0x24, 'B'), ('pad25', 0x25, 'B'), ('recover_ms', 0x26, 'h'), ('mana', 0x28, 'h'),
                ('target', 0x2A, 'B'), ('flags', 0x2B, 'B'), ('category', 0x2C, 'B'),
                ('animseq', 0x2D, 'B'), ('pad2e', 0x2E, 'H')]


def exe_off(d, va):
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    ns = struct.unpack_from('<H', d, pe + 6)[0]; opt = struct.unpack_from('<H', d, pe + 20)[0]
    ib = struct.unpack_from('<I', d, pe + 52)[0]
    for i in range(ns):
        _, vs, rva, rs, rp = struct.unpack_from('<8sIIII', d, pe + 24 + opt + 40 * i)
        if rva + ib <= va < rva + ib + rs:
            return va - rva - ib + rp
    raise ValueError(hex(va))


class Spell:
    def __init__(self, raw):
        self.raw = raw
        for n, o, f in SPELL_FIELDS:
            setattr(self, n, struct.unpack_from('<' + f, raw, o)[0])

    @property
    def label(self):
        return self.name.split(b'\0')[0].decode('latin1')

    def to_bytes(self):
        b = bytearray(self.raw)
        for n, o, f in SPELL_FIELDS:
            struct.pack_into('<' + f, b, o, getattr(self, n))
        return bytes(b)


class SpellTable:
    @classmethod
    def parse(cls, block):
        t = cls(); t.spells = [Spell(block[i * SPELL_SZ:(i + 1) * SPELL_SZ]) for i in range(SPELL_N)]
        return t

    @classmethod
    def from_exe(cls, exe):
        o = exe_off(exe, SPELL_VA)
        return cls.parse(exe[o:o + SPELL_N * SPELL_SZ])

    def to_bytes(self):
        return b''.join(s.to_bytes() for s in self.spells)

    def patch_exe(self, exe):
        o = exe_off(exe, SPELL_VA)
        return exe[:o] + self.to_bytes() + exe[o + SPELL_N * SPELL_SZ:]


# ---------------------------------------------------------------- self test
def selftest(root):
    res = {}
    def chk(kind, path, cls):
        d = open(path, 'rb').read()
        ok = cls.parse(d).to_bytes() == d
        r = res.setdefault(kind, [0, 0, []]); r[0] += 1; r[1] += ok
        if not ok: r[2].append(path)
    chk('emitters.dat', os.path.join(root, 'emitters.dat'), EmitterFile)
    for dp, _, fs in os.walk(os.path.join(root, 'efxgfx')):
        for f in fs:
            p, e = os.path.join(dp, f), f.lower().rsplit('.', 1)[-1]
            if e == 'ant': chk('ant/cnt', p, Ant)
            elif e == 'alf': chk('alf', p, Alf)
    exe = open(os.path.join(root, 'deep6.exe'), 'rb').read()
    st = SpellTable.from_exe(exe)
    r = res.setdefault('spelltable', [1, int(st.patch_exe(exe) == exe), []])
    # CNT decode -> re-encode check (row-level)
    enc = [0, 0]
    for dp, _, fs in os.walk(os.path.join(root, 'efxgfx')):
        for f in fs:
            if f.lower().endswith('.ant'):
                a = Ant.parse(open(os.path.join(dp, f), 'rb').read())
                if a.kind != 'CNT': continue
                for i in range(a.n):
                    px = a.frame_pixels(i); rows, data = a.frames[i]
                    for y in range(a.h):
                        nxt = sorted([r for r in rows if r > rows[y]] + [len(data)])[0]
                        enc[0] += 1; enc[1] += Ant.encode_row(px[y * a.w:(y + 1) * a.w]) == data[rows[y]:nxt]
    res['cnt rows re-encoded identically'] = [enc[0], enc[1], []]
    return res, st


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a not in ('--selftest', 'selftest')]
    root = args[0] if args else '.'
    res, st = selftest(root)
    for k, (n, ok, bad) in res.items():
        print('%-34s %d/%d %s' % (k, ok, n, bad[:5]))
    if '--selftest' in sys.argv or 'selftest' in sys.argv:
        sys.exit(0 if all(ok == n for n, ok, _ in res.values()) else 1)
    for i, s in enumerate(st.spells):
        print(i, s.label, s.id, s.school, s.level, s.slot, s.recover_ms, s.mana, s.target, s.flags, s.category, s.animseq)
