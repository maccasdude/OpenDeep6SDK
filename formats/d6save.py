#!/usr/bin/env python3
"""
d6save - Wizards & Warriors save games (save/gameNN.sav).

Layout from SaveCurrentGame_ / RestoreCurrentGame_ / SegWrite_ReadSaveGameIHDI_
(segwrite.c, deep6.exe), see docs/formats/saves.md. The file is a header of
offsets, a description for the load screen, a thumbnail, two embedded
D6ARCHIV-style archives (the spoke cache and the runtime files), the party
state and one block per party member. Every part is kept byte for byte; the
writer rebuilds the file and recomputes the offsets.

usage:
  d6save.py info SAVE                    summary (party, spoke, positions, gold ...)
  d6save.py thumb SAVE OUT.png           the load-screen picture
  d6save.py extract SAVE OUTDIR          the two archives and their members
  d6save.py set SAVE OUT field=value ...   edit a party member, e.g. pc0.gold=5000
  d6save.py --selftest GAMEDIR           round-trip every save of the game
"""
import io
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERSION = 0x140
THUMB_W, THUMB_H = 136, 111          # SegWrite_WriteWorldViewPicture_(canvas, 0x188, 8, 0x88, 0x6f)
PC_SIZE = 0x27F8                     # _pc[6] (d6glob.c)

# Party state block (fixed part, in write order): (name, size). The names are
# the exe's globals.
STATE_FIELDS = [
    ('_gSpokeNumber', 4), ('_gPartyN', 4), ('_gPartyI', 0x18), ('_gPartyS', 0x18),
    ('_gPartyU', 0x18), ('_gPartyF', 0x18), ('_gBannerLeader', 0x18), ('_gPCCombatID', 0x18),
    ('_gPCNextOrder', 0x18), ('_gPCReadyMode', 0x18), ('_gPCReadyData', 0x18),
    ('_gPCReadyTarg', 0x18), ('_gPCLatchProp', 0x18), ('_gPCExpAcc', 0x18), ('_PCFollow', 0x18),
    ('_gPCidx', 4), ('_gPCaop', 4), ('_gCamMan', 4), ('_PCCamRotation', 0x48), ('_gPCCamFlag', 4),
    ('_gBallGlow', 0x12), ('_gBallGlowON', 0x12), ('_gPCTorchLight', 0x18),
    ('_gPCTorchBrite', 0x18), ('_gPCSpellLight', 0x18), ('_gPCSpellBrite', 0x18),
    ('_gPCSpiritEye', 0x18), ('_gPCInspectBits', 0x18), ('_gPCInspectTrap', 0x18),
    ('_gWorldClockTime', 4), ('_gNpcStopTime', 4), ('_gNpcStatus', 0x140), ('_gNpcMBits', 0x280),
    ('_WState', 0x1000), ('_gPortalFlag', 0x40), ('_gPortalBase', 0xC0), ('_gPortalHRet', 0x18),
    ('_gPortalHome', 0x48),
]

# Per party member block, first part (fixed sizes, write order). 'mon+X' is
# the PC's runtime monster record (_gMonster, 0x2A8 bytes each).
MEMBER_HEAD = [
    ('pc', PC_SIZE),                  # the character record, see PC_FIELDS
    ('attach_items', 0x38),           # i16[28] item of each attachment point
    ('attach_has_ext', 0x1C),         # u8[28] 1 = an 0xA8 object extension follows
]
MEMBER_MID = [                        # after the extensions
    ('pos', 0xC),                     # mon+0x10 f32 x,y,z world position
    ('rot', 0xC),                     # mon+0x1C
    ('mon_0c', 4),                    # mon+0x0C
    ('model_7d', 4),                  # u32 = byte at the model object +0x7D
    ('mon_bc', 0xC),                  # mon+0xBC
    ('spell_memory', 0x14),           # _gPCSpellMemory[pc]
    ('vampire_boon', 4),              # _gPCVampireBoon[pc]
    ('specop_mode', 4),               # _gPCSpecOpMode[pc]
    ('netman', 0x12E),                # LoadManToNetMan_ (hit points, state ...; network form of the monster)
    ('pos2', 0xC),                    # mon+0x50 position (the copy with y + 832)
    ('mon_5c', 4), ('mon_e4', 4), ('mon_e8', 4), ('mon_ec', 4), ('mon_f0', 4),
    ('mon_f4', 4), ('mon_f8', 4), ('mon_fc', 4),
    ('mon_272', 1), ('mon_274', 2),
    ('mount', 2),                     # mon+0x276 i16 mount index, -1 = none
]
MEMBER_TAIL = [                       # after the optional mount record (0x12, _gPCMount[mount])
    ('mon_270', 2), ('model_7c', 1), ('model_78', 4), ('mon_25c', 0xC), ('mon_268', 4),
    ('has_robj', 4),                  # u32 1 = a rotating-object record follows (RObj_Write_)
]

# Character record fields (offsets into the 0x27F8 record). Names of
# abilities, resistances, roles, clans and genders are the game's own strings.
ABILITIES = ['Strength', 'Intellect', 'Spirituality', 'Dexterity', 'Agility', 'Fortitude',
             'Will', 'Presence']
RESISTS = ['Magic', 'Fire', 'Mind', 'Paralysis', 'Death', 'Petrification', 'Cold', 'Wind',
           'Earth', 'Poison', 'Elements', 'Dispel', 'Silence', 'Light', 'Charm', 'Mavin']
ROLES = ['Warrior', 'Wizard', 'Priest', 'Rogue', 'Ranger', 'Bard', 'Samurai', 'Paladin',
         'Barbarian', 'Monk', 'Ninja', 'Warlock']
CLANS = ['Human', 'Elf', 'Dwarf', 'Gnome', 'Pixie', 'Omphaaz', 'Whiskah', 'Gourk', 'Ratling',
         'Lizzord']
GENDERS = ['Male', 'Female']
STATUS = ['OK', 'STONE', 'INANIMATE', 'DEAD', 'BONES', 'ASH', 'LOST']
PC_FIELDS = {                         # name: (offset, struct format)
    'name': (0x08, '16s'),
    'gender': (0x18, '<h'), 'clan': (0x1A, '<h'), 'role': (0x1C, '<h'),
    'alignment': (0xF2, '<h'),        # 0..100 (GM ALIGNMENT)
    'gold': (0x108, '<I'),
    'exp': (0x110, '<I'),
    'level': (0x118, '<h'),
    'hit': (0x11C, '<h'),             # shown as hit - 10
    'parry': (0x120, '<h'), 'shield': (0x124, '<h'),
    'armor': (0x128, '<i'),
    'speed_delay': (0x12C, '<i'),     # shown as (3000 - value) / 100
    'status': (0x19C, '<h'),          # STATUS index
    'kills': (0x2784, '<I'), 'assists': (0x2790, '<I'),
}
for _i, _n in enumerate(ABILITIES):
    PC_FIELDS[_n.lower()] = (0x1E + 2 * _i, '<h')
for _i, _n in enumerate(RESISTS):
    PC_FIELDS['resist_' + _n.lower()] = (0x4E + 2 * _i, '<h')
INV_OFFSET, INV_SLOTS, INV_SIZE = 0x298, 78, 0x56     # inventory instances (databases.md)
QFLAG_OFFSET = 0x1CE6                 # u8[256] quest flags
ATTITUDE_OFFSET = 0x1DE6              # i8[160] attitude to each NPC
PCREG_OFFSET = 0x1EA8                 # u32[160] NPC script register per NPC


class SaveError(Exception):
    pass


def _rd(f, n):
    b = f.read(n)
    if len(b) != n:
        raise SaveError('file ends early')
    return b


class Member(object):
    """One party member's block: a list of (name, bytes) in file order."""

    def __init__(self, parts):
        self.parts = parts

    def get(self, name):
        for n, b in self.parts:
            if n == name:
                return b
        return None

    def set(self, name, data):
        for i, (n, b) in enumerate(self.parts):
            if n == name:
                if len(data) != len(b):
                    raise SaveError('%s must stay %d bytes' % (name, len(b)))
                self.parts[i] = (n, bytes(data))
                return
        raise KeyError(name)

    # character record fields
    def field(self, name):
        off, fmt = PC_FIELDS[name]
        v = struct.unpack_from(fmt, self.get('pc'), off)[0]
        return v.split(b'\0')[0].decode('latin-1') if isinstance(v, bytes) else v

    def set_field(self, name, value):
        off, fmt = PC_FIELDS[name]
        pc = bytearray(self.get('pc'))
        if fmt.endswith('s'):
            raw = str(value).encode('latin-1')[:struct.calcsize(fmt) - 1]
            value = raw + b'\0' * (struct.calcsize(fmt) - len(raw))
        struct.pack_into(fmt, pc, off, value)
        self.set('pc', pc)

    def position(self):
        return struct.unpack('<3f', self.get('pos'))

    def inventory(self):
        """[(slot, item record number, durability, flags, quantity)] of used slots."""
        pc = self.get('pc')
        out = []
        for i in range(INV_SLOTS):
            o = INV_OFFSET + i * INV_SIZE
            item, dur, flags = struct.unpack_from('<hhB', pc, o)
            if item > 0:
                out.append((i, item, dur, flags, struct.unpack_from('<i', pc, o + 6)[0]))
        return out

    def raw(self):
        return b''.join(b for _, b in self.parts)


class SaveGame(object):
    def __init__(self):
        self.header = [0] * 21

    # ------------------------------------------------------------------ read
    @classmethod
    def parse(cls, data):
        s = cls()
        f = io.BytesIO(data)
        s.header = list(struct.unpack('<21I', _rd(f, 0x54)))
        if s.header[0] != VERSION:
            raise SaveError('not a save game (version 0x%x)' % s.header[0])
        f.seek(s.header[3])
        s.description = _rd(f, 0x50)
        s.info = _rd(f, 0x40)
        n = struct.unpack('<i', _rd(f, 4))[0]
        s.roster = [(_rd(f, 16), _rd(f, 16)) for _ in range(n)]
        s.thumb = _rd(f, THUMB_W * THUMB_H * 2)
        s.archive_cache = data[s.header[5]:s.header[5] + s.header[6]]     # D6ARCHIV.DAT at save time
        s.archive_state = data[s.header[7]:s.header[7] + s.header[8]]     # SaveArchive_ output
        f.seek(s.header[4])
        s.state = [(nm, _rd(f, sz)) for nm, sz in STATE_FIELDS]
        nf = struct.unpack('<i', _rd(f, 4))[0]
        s.fountains = [_rd(f, 0x18) for _ in range(nf)]
        s.members = [None] * 6
        for i in range(6):
            if s.header[9 + i]:
                f.seek(s.header[15 + i])
                s.members[i] = cls._parse_member(f)
        s._check_order(len(data))
        s._orig = data
        return s

    @staticmethod
    def _parse_member(f):
        parts = [(nm, _rd(f, sz)) for nm, sz in MEMBER_HEAD]
        for k, flag in enumerate(parts[2][1]):
            if flag:
                parts.append(('attach_ext%d' % k, _rd(f, 0xA8)))
        parts += [(nm, _rd(f, sz)) for nm, sz in MEMBER_MID]
        mount = struct.unpack('<h', parts[-1][1])[0]
        if mount != -1:
            parts.append(('mount_record', _rd(f, 0x12)))
        parts += [(nm, _rd(f, sz)) for nm, sz in MEMBER_TAIL]
        if struct.unpack('<I', parts[-1][1])[0]:
            flags = _rd(f, 4)
            robj = flags
            if struct.unpack('<I', flags)[0] & 2:
                robj += _rd(f, 4 + 0x1C + 4 + 0x1C + 4 + 0x1C + 4)
            parts.append(('robj', robj))
        return Member(parts)

    def _check_order(self, size):
        """The writer below lays the parts out in the game's order; refuse
        files that differ (so a rebuild can never reorder them)."""
        h = self.header
        exp = 0x54
        if h[3] != exp:
            raise SaveError('unexpected description offset')
        exp += 0x50 + 0x40 + 4 + 32 * len(self.roster) + len(self.thumb)
        if h[5] != exp:
            raise SaveError('unexpected archive offset')
        if h[7] != h[5] + h[6] or h[4] != h[7] + h[8]:
            raise SaveError('unexpected section order')

    # ----------------------------------------------------------------- write
    def build(self):
        out = bytearray(0x54)
        h = list(self.header)
        h[3] = len(out)
        out += self.description + self.info + struct.pack('<i', len(self.roster))
        for name, status in self.roster:
            out += name + status
        out += self.thumb
        h[5], h[6] = len(out), len(self.archive_cache)
        out += self.archive_cache
        h[7], h[8] = len(out), len(self.archive_state)
        out += self.archive_state
        h[4] = len(out)
        for _, b in self.state:
            out += b
        out += struct.pack('<i', len(self.fountains)) + b''.join(self.fountains)
        for i in range(6):
            h[9 + i] = 1 if self.members[i] is not None else 0
            h[15 + i] = 0
            if self.members[i] is not None:
                h[15 + i] = len(out)
                out += self.members[i].raw()
        struct.pack_into('<21I', out, 0, *h)
        return bytes(out)

    # ------------------------------------------------------------ helpers
    def state_value(self, name):
        for n, b in self.state:
            if n == name:
                return b
        raise KeyError(name)

    def spoke(self):
        return struct.unpack('<i', self.state_value('_gSpokeNumber'))[0]

    def wstate(self, spoke, n):
        return self.state_value('_WState')[spoke * 256 + n]

    def thumb_image(self):
        """The load-screen picture as a PIL image (RGB565)."""
        from PIL import Image
        px = struct.unpack('<%dH' % (THUMB_W * THUMB_H), self.thumb)
        rgb = bytearray()
        for p in px:
            r, g, b = (p >> 11) & 31, (p >> 5) & 63, p & 31
            rgb += bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
        return Image.frombytes('RGB', (THUMB_W, THUMB_H), bytes(rgb))


class Roster(object):
    """ROSTER.DAT, the characters at the inn (OpenRoster_, SaveRosterHead_,
    LoadPC_, SavePC_, pccreate.c): u32 offset[15] (0 = empty slot), u32
    value[15] (third argument of SavePC_, meaning not known), then character
    records of 0x27F8 bytes; slot i is written at 0x78 + i * 0x27F8."""

    def __init__(self, data):
        self.data = bytes(data)
        self.offsets = list(struct.unpack_from('<15I', self.data, 0))
        self.values = list(struct.unpack_from('<15I', self.data, 0x3C))

    def slots(self):
        """{slot: Member} (the record only, as 'pc')."""
        out = {}
        for i, o in enumerate(self.offsets):
            if o and o + PC_SIZE <= len(self.data):
                out[i] = Member([('pc', self.data[o:o + PC_SIZE])])
        return out

    def set_record(self, slot, member):
        o = self.offsets[slot]
        if not o:
            raise SaveError('roster slot %d is empty' % slot)
        self.data = self.data[:o] + member.get('pc') + self.data[o + PC_SIZE:]

    def build(self):
        return struct.pack('<15I', *self.offsets) + struct.pack('<15I', *self.values) + self.data[0x78:]


def load(path):
    return SaveGame.parse(open(path, 'rb').read())


def _cstr(b):
    return b.split(b'\0')[0].decode('latin-1')


def info(s):
    lines = ['description: %r' % _cstr(s.description), 'info: %r' % _cstr(s.info),
             'spoke: %d' % s.spoke(),
             'world clock: %d' % struct.unpack('<i', s.state_value('_gWorldClockTime'))[0],
             'archives: cache %d bytes, state %d bytes' % (len(s.archive_cache), len(s.archive_state))]
    try:
        import d6data
        for nm, a in (('cache', s.archive_cache), ('state', s.archive_state)):
            lines.append('  %s archive: %s' % (nm, ' '.join(m[0] for m in d6data.Archive.parse(a).members())))
    except Exception:
        pass
    for i, m in enumerate(s.members):
        if m is None:
            continue
        g = m.field
        lines.append('pc%d: %s, %s %s %s, level %d, exp %d, gold %d, %s, at (%.0f, %.0f, %.0f)' % (
            i, g('name'), GENDERS[g('gender')] if 0 <= g('gender') < 2 else g('gender'),
            CLANS[g('clan')] if 0 <= g('clan') < len(CLANS) else g('clan'),
            ROLES[g('role')] if 0 <= g('role') < len(ROLES) else g('role'),
            g('level'), g('exp'), g('gold'),
            STATUS[g('status')] if 0 <= g('status') < len(STATUS) else g('status'),
            *m.position()))
        lines.append('     ' + ', '.join('%s %d' % (a, g(a.lower())) for a in ABILITIES))
        lines.append('     %d items: %s' % (len(m.inventory()),
                                            ' '.join('%d' % it[1] for it in m.inventory())))
    return '\n'.join(lines)


def selftest(gamedir):
    import glob
    paths = sorted(glob.glob(os.path.join(gamedir, '[sS][aA][vV][eE]', '*.[sS][aA][vV]')))
    ok = 0
    for p in paths:
        data = open(p, 'rb').read()
        s = SaveGame.parse(data)
        same = s.build() == data
        ok += same
        print('%s: %s' % (os.path.basename(p), 'identical' if same else 'DIFFERS'))
        # the embedded archives use the D6ARCHIV layout
        import d6data
        for nm, a in (('cache', s.archive_cache), ('state', s.archive_state)):
            if a and hasattr(d6data, 'Archive'):
                d6data.Archive.parse(a)
    rp = os.path.join(gamedir, 'ROSTER.DAT')
    if os.path.exists(rp):
        data = open(rp, 'rb').read()
        r = Roster(data)
        same = r.build() == data
        print('ROSTER.DAT: %d characters (%s), %s' % (len(r.slots()), ', '.join(
            m.field('name') for m in r.slots().values()), 'identical' if same else 'DIFFERS'))
        ok += same
        paths.append(rp)
    print('TOTAL %d files, %d byte identical' % (len(paths), ok))
    return 0 if ok == len(paths) else 1


def main(argv):
    a = argv[1:]
    if not a:
        print(__doc__)
        return 1
    if a[0] == '--selftest':
        return selftest(a[1])
    if a[0] == 'info':
        print(info(load(a[1])))
        return 0
    if a[0] == 'thumb':
        load(a[1]).thumb_image().save(a[2])
        return 0
    if a[0] == 'extract':
        s = load(a[1])
        os.makedirs(a[2], exist_ok=True)
        for nm, data in (('D6ARCHIV.DAT', s.archive_cache), ('SAVEARCH.DAT', s.archive_state)):
            open(os.path.join(a[2], nm), 'wb').write(data)
        print('wrote', os.path.join(a[2], 'D6ARCHIV.DAT'), 'and SAVEARCH.DAT '
              '(list or extract them with: d6data.py --list/--extract)')
        return 0
    if a[0] == 'set':
        s = load(a[1])
        for kv in a[3:]:
            k, v = kv.split('=', 1)
            who, field = k.split('.', 1)
            m = s.members[int(who[2:])]
            if m is None:
                raise SaveError('%s is not in the party' % who)
            m.set_field(field, v if PC_FIELDS[field][1].endswith('s') else int(v, 0))
        open(a[2], 'wb').write(s.build())
        print('wrote', a[2])
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
