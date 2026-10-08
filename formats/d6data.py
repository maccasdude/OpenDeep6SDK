#!/usr/bin/env python3
"""
d6data.py - parsers/writers for Wizards & Warriors (Deep6 engine, 2000) content data.

Covers the per-spoke scripting tables and the global record databases:

  D6TRIGnn.DAT  triggers (event invocations + parameters)        0xB8 records
  D6BOUNnn.DAT  boundary areas (AABB volumes that fire triggers)  0x28 records
  D6SPECnn.DAT  specials (timed / all-dead / party-has-item)      0x1C records
  D6SWITnn.DAT  switches (usable objects bound to states)         0x14 records
  D6LINKnn.DAT  nav-point links between terrain and BSPs          0x08 records
  D6TRAPnn.DAT  trapped/locked containers                         0x24 records
  ENTRssnn.DAT  named entry points (text, editor/dev only)
  D6MONS.DAT    monster records (0x154), D6ITEM.DAT (0x11C), D6PROP.DAT (0x38),
  D6NPC.DAT (0x24), D6TREAS.DAT (200), D6TRLIST.DAT (0x3C), D6HELM.DAT, D6MONSND.DAT (0xB8),
  MONSOUND.DAT (text SFX table)   - field layouts: docs/formats/databases.md
  D6SMITnn.DAT  shop inventory (0x800 + checksum)
  EVENTS.DCL    event declaration table (+ editor metadata: descriptions, param names)
  EVENTS.COD    event bytecode (disassembler included)
  D6STRING.DAT  id -> string table
  TEXTPAK.000   id -> text message pack
  NPCDATA.PAK / GMDATA.PAK  offset/size directory archives
  D6ARCHIV.DAT  save archive (header + embedded files)
  emitters.dat  particle emitter descriptors (0x80 records)
  models/*.lst  model name lists;  *.mdl model files (region walker)
  *.TOL / *.BOL / *.FOL  object placement lists (0x40 records)
  *.ldl         encrypted language files (opaque passthrough)

Every class has .parse(bytes) / .build() -> bytes; build() of an unmodified
parse is byte-identical (unknown bytes are kept raw).

Self test:  python3 d6data.py --selftest /path/to/game
Dump:       python3 d6data.py --dump-area 0 /path/to/game  > area00.txt
Disasm:     python3 d6data.py --disasm /path/to/game
Plain ASCII only.
"""
import os
import struct
import sys

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def find_file(root, name):
    """Case-insensitive lookup of name (may contain '/') under root."""
    cur = root
    for part in name.replace('\\', '/').split('/'):
        if not part:
            continue
        p = os.path.join(cur, part)
        if os.path.exists(p):
            cur = p
            continue
        low = part.lower()
        hit = None
        try:
            for e in os.listdir(cur):
                if e.lower() == low:
                    hit = e
                    break
        except OSError:
            return None
        if hit is None:
            return None
        cur = os.path.join(cur, hit)
    return cur


def read_file(root, name):
    p = find_file(root, name)
    if p is None:
        return None
    with open(p, 'rb') as f:
        return f.read()


def cstr(b):
    """bytes up to first NUL, decoded latin-1."""
    i = b.find(b'\0')
    if i >= 0:
        b = b[:i]
    return b.decode('latin-1')


def put_cstr(buf, off, size, s):
    """Write s into fixed field keeping stale bytes after the terminator."""
    e = s.encode('latin-1')
    if len(e) >= size:
        raise ValueError('string too long for field (%d >= %d)' % (len(e), size))
    buf[off:off + len(e)] = e
    buf[off + len(e)] = 0


SPOKE_NAMES = {
    0: 'Spoke 0 - Valeia wilderness (terrain SPOKE00)',
    1: 'Spoke 1 - Crypt (CRYPTA/CRYPTB)',
    2: 'Spoke 2 - Serpent Temple (bsp1 TEMPLEB, bsp2 TEMPLEA)',
    3: 'Spoke 3 - wilderness (terrain spoke03)',
    4: 'Spoke 4 - Shuru (SHURU)',
    5: 'Spoke 5 - Mines (MINESA..MINESD)',
    6: 'Spoke 6 - Ogre (OGREA..OGREC)',
    7: 'Spoke 7 - Dragon (DRAGONA..DRAGONC)',
    8: 'Spoke 8 - Lich (LICHA/LICHB)',
    9: 'Spoke 9 - Sunken (SUNKENA/SUNKENB)',
    10: 'Spoke 10 - Shrine (SHRINEA)',
    11: 'Spoke 11 - wilderness (terrain spoke11)',
    12: 'Spoke 12 - Pyramid (PYRAMA/PYRAMB)',
}

# BSP number -> object list file, for dungeon spokes (mapobj.c FetchObjects_).
# Terrain spokes (0,3,11) take their BSP list from the 'B' records of SPOKEnn.TOL.
SPOKE_BSPS = {
    1: {1: 'crypta.bol', 2: 'cryptb.bol'},
    2: {2: 'templea.bol', 1: 'templeb.bol'},
    4: {1: 'shuru.bol'},
    5: {1: 'minesa.bol', 2: 'minesb.bol', 3: 'minesc.bol', 4: 'minesd.bol'},
    6: {1: 'ogrea.bol', 2: 'ogreb.bol', 3: 'ogrec.bol'},
    7: {1: 'dragona.bol', 2: 'dragonb.bol', 3: 'dragonc.bol'},
    8: {1: 'licha.bol', 2: 'lichb.bol'},
    9: {1: 'sunkena.bol', 2: 'sunkenb.bol'},
    10: {1: 'shrinea.bol'},
    12: {1: 'pyrama.bol', 2: 'pyramb.bol'},
}
TERRAIN_SPOKES = {0: 'SPOKE00.TOL', 3: 'SPOKE03.TOL', 11: 'SPOKE11.TOL'}


def split_objid(objid):
    """Object reference used by triggers/switches/traps/specials.
    < 100000  -> record index in the spoke's terrain object list (TOL)
    n*100000+k -> record k of BSP n's BOL (n == 16 means BSP 0)."""
    b = objid // 100000
    if b == 0:
        return (None, objid)
    if b == 16:
        b = 0
    return (b, objid % 100000)


# ---------------------------------------------------------------------------
# Generic fixed record table: record 0 is a header whose first u32 = count,
# records 1..count follow at i*recsize (the game fseeks to i*recsize).
# ---------------------------------------------------------------------------

class Field(object):
    def __init__(self, name, off, fmt, conf='?', doc=''):
        self.name, self.off, self.fmt, self.conf, self.doc = name, off, fmt, conf, doc


class Record(object):
    """A raw record with typed accessors."""
    FIELDS = []
    SIZE = 0

    def __init__(self, raw):
        self.raw = bytearray(raw)

    @classmethod
    def field(cls, name):
        for f in cls.FIELDS:
            if f.name == name:
                return f
        raise KeyError(name)

    def get(self, name):
        f = self.field(name)
        if f.fmt.startswith('str'):
            return cstr(bytes(self.raw[f.off:f.off + int(f.fmt[3:])]))
        v = struct.unpack_from('<' + f.fmt, self.raw, f.off)
        return v[0] if len(v) == 1 else list(v)

    def set(self, name, value):
        f = self.field(name)
        if f.fmt.startswith('str'):
            put_cstr(self.raw, f.off, int(f.fmt[3:]), value)
            return
        if isinstance(value, (list, tuple)):
            struct.pack_into('<' + f.fmt, self.raw, f.off, *value)
        else:
            struct.pack_into('<' + f.fmt, self.raw, f.off, value)

    def as_dict(self):
        return dict((f.name, self.get(f.name)) for f in self.FIELDS)

    def __getattr__(self, name):
        if name in ('raw',):
            raise AttributeError(name)
        try:
            return self.get(name)
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        """rec.field = value writes the field (same as rec.set)."""
        if name != 'raw' and any(f.name == name for f in type(self).FIELDS):
            self.set(name, value)
        else:
            object.__setattr__(self, name, value)


class RecordTable(object):
    """count at offset 0 of record 0; records 1..count at i*SIZE.
    HEADER_SIZE = SIZE (record 0 is a full dummy record) unless overridden.
    If the file is shorter than SIZE (empty table) the header is what is there."""
    REC = Record
    HEADER_SIZE = None   # default: REC.SIZE
    COUNT_BIAS = 0       # stored count = len(records) + COUNT_BIAS

    def __init__(self):
        self.header = bytearray(4)
        self.records = []
        self.trailer = b''

    @classmethod
    def parse(cls, data):
        t = cls()
        size = cls.REC.SIZE
        hs = cls.HEADER_SIZE if cls.HEADER_SIZE is not None else size
        n = struct.unpack_from('<I', data, 0)[0] - cls.COUNT_BIAS
        t.header = bytearray(data[:min(hs, len(data))])
        off = hs
        for i in range(n):
            t.records.append(cls.REC(data[off:off + size]))
            off += size
        t.trailer = bytes(data[off:])
        return t

    def build(self):
        h = bytearray(self.header)
        if len(h) < 4:
            h += b'\0' * (4 - len(h))
        struct.pack_into('<I', h, 0, len(self.records) + self.COUNT_BIAS)
        if self.records:
            hs = self.HEADER_SIZE if self.HEADER_SIZE is not None else self.REC.SIZE
            if len(h) < hs:
                h += b'\0' * (hs - len(h))
        out = bytearray(h)
        for r in self.records:
            out += r.raw
        out += self.trailer
        return bytes(out)

    def new_record(self):
        r = self.REC(b'\0' * self.REC.SIZE)
        self.records.append(r)
        return r

    def __len__(self):
        return len(self.records)

    def __getitem__(self, idx1):
        """1-based access, matching the engine's indices."""
        return self.records[idx1 - 1]


# ---------------- per-spoke scripting tables ----------------

class TriggerRec(Record):
    SIZE = 0xB8
    FIELDS = [
        Field('event', 0x00, 'str40', 'high', 'event name, e.g. "@TEXTMSG" (resolved via EVENTS.DCL; bytes after NUL are stale)'),
        Field('rt_codeptr', 0x28, 'I', 'high', 'runtime: code offset in EVENTS.COD (overwritten at load; stale in file)'),
        Field('rt_eventidx', 0x2C, 'H', 'high', 'runtime: event index (overwritten at load)'),
        Field('state', 0x2E, 'h', 'high', 'world-state index (0..255) watched by CheckStates_ when enabled'),
        Field('enabled', 0x30, 'B', 'high', 'trigger active (0x3F used as per-PC mask by bound areas)'),
        Field('laststate', 0x31, 'B', 'med', 'last seen state value (runtime, saved in file)'),
        Field('oneshot', 0x32, 'B', 'high', 'disable after firing'),
        Field('nparams', 0x33, 'B', 'high', 'parameter count (overwritten from EVENTS.DCL at load)'),
        Field('mode', 0x34, 'B', 'high', 'state-trigger mode: 0 any change, 1 fire when state!=0, 2 when state==0; '
                                         'for bound areas 1 = once per PC'),
        Field('params', 0x38, '30i', 'high', 'event parameters (int32); operand i of the bytecode reads params[i]'),
        Field('rt_queue', 0xB0, '2i', 'med', 'runtime event-queue scratch (zeroed by queued ops)'),
    ]


class TriggerTable(RecordTable):
    REC = TriggerRec


class BoundRec(Record):
    SIZE = 0x28
    FIELDS = [
        Field('state', 0x00, 'h', 'high', 'world state written (1/0) every frame when trigger<1'),
        Field('bsp', 0x02, 'h', 'high', 'BSP number the box is relative to (-1 = world coords)'),
        Field('trigger', 0x04, 'h', 'high', 'trigger index fired on entry (<1: drive "state" instead)'),
        Field('enabled', 0x06, 'B', 'high', 'active (0x3F = per-PC mask when oneshot==2)'),
        Field('pcmask', 0x07, 'B', 'high', 'runtime: PCs already inside (bitmask)'),
        Field('oneshot', 0x08, 'B', 'high', '0 repeat, 1 disable after first hit, 2 once per PC'),
        Field('who', 0x09, 'B', 'high', '0 PCs, 1 party creatures 0-5, 2 all creatures (not hidden), '
                                         '3 non-party creatures, 4 specific object (objid), 5 all creatures'),
        Field('pad0a', 0x0A, 'H', 'low', 'unused'),
        Field('min', 0x0C, '3f', 'high', 'box min x,y,z (y up); BSP-relative if bsp>=0'),
        Field('max', 0x18, '3f', 'high', 'box max x,y,z'),
        Field('objid', 0x24, 'i', 'high', 'object id for who==4'),
    ]


class BoundTable(RecordTable):
    REC = BoundRec


class SpecialRec(Record):
    SIZE = 0x1C
    FIELDS = [
        Field('state', 0x00, 'h', 'high', 'world state set to the condition result when trigger<1'),
        Field('unk02', 0x02, 'h', 'low', 'unknown (always -1 seen)'),
        Field('trigger', 0x04, 'h', 'high', 'trigger fired when condition true (<1: drive state)'),
        Field('type', 0x06, 'B', 'high', '1 world-clock window [a,b), 2 all listed monsters dead, 3 party has item a'),
        Field('enabled', 0x07, 'B', 'high', 'active'),
        Field('unk08', 0x08, 'B', 'low', 'unknown'),
        Field('oneshot', 0x09, 'B', 'high', 'disable once true'),
        Field('pad0a', 0x0A, 'H', 'low', 'unused'),
        Field('args', 0x0C, '4i', 'high', 'type1: start,end clock; type2: 4 object ids; type3: item id'),
    ]


class SpecialTable(RecordTable):
    REC = SpecialRec


class SwitchRec(Record):
    SIZE = 0x14
    FIELDS = [
        Field('objid', 0x00, 'i', 'high', 'object id (isentity=0) or bsp*100000+entity (isentity=1)'),
        Field('link', 0x04, 'i', 'high', 'next switch index in a linked ring (0 = none)'),
        Field('target', 0x08, 'h', 'high', '>=0 world state set to switch state; <0 -> run trigger -target'),
        Field('sfx', 0x0A, 'h', 'high', 'SFX record on toggle (0 = prop default)'),
        Field('keyitem', 0x0C, 'h', 'high', 'required item (key) id, 0 = none'),
        Field('isentity', 0x0E, 'B', 'high', '1 = BSP entity (door etc.), 0 = placed object'),
        Field('flags', 0x0F, 'B', 'med', '1 animate, 2 consume key, 4 return key when off, 8 hide polys, '
                                         '0x10 key latch, 0x20 key always required, 0x40 use-item only'),
        Field('enabled', 0x10, 'B', 'high', 'usable'),
        Field('on', 0x11, 'B', 'high', 'current state'),
        Field('oneshot', 0x12, 'B', 'high', 'disable after one use'),
        Field('unlocked', 0x13, 'B', 'med', 'runtime: key already used'),
    ]


class SwitchTable(RecordTable):
    REC = SwitchRec


class LinkRec(Record):
    SIZE = 8
    FIELDS = [
        Field('navA', 0, 'h', 'high', 'nav point id in side A'),
        Field('navB', 2, 'h', 'high', 'nav point id in side B'),
        Field('bspA', 4, 'h', 'high', 'BSP of A (-1 = terrain nav)'),
        Field('bspB', 6, 'h', 'high', 'BSP of B (-1 = terrain nav)'),
    ]


class LinkTable(RecordTable):
    REC = LinkRec


class TrapRec(Record):
    SIZE = 0x24
    FIELDS = [
        Field('objid', 0x00, 'i', 'high', 'container object id'),
        Field('trapmask', 0x04, 'I', 'high', 'bit n = trap type n possible (1..13); 0 = no trap'),
        Field('traptype', 0x08, 'i', 'high', 'armed trap type (chosen from mask at reset)'),
        Field('locked', 0x0C, 'h', 'high', '0 open/disarmed, 1 closed, 2 reset'),
        Field('treasure', 0x0E, 'h', 'high', 'D6TREAS record for contents'),
        Field('difficulty', 0x10, 'h', 'high', 'lock/trap difficulty'),
        Field('attempts', 0x12, 'h', 'med', 'runtime attempt counter'),
        Field('power', 0x14, 'B', 'high', 'trap effect level'),
        Field('nolock', 0x15, 'B', 'med', 'non-zero disables random pick success'),
        Field('rt16', 0x16, '6s', 'med', 'runtime (zeroed on reset)'),
        Field('resetpct', 0x1C, 'B', 'high', 'chance% the trap mask is cleared at reset'),
        Field('monpct', 0x1D, 'B', 'high', 'chance% a chest monster appears'),
        Field('monrec', 0x1E, 'h', 'high', 'chest monster D6MONS record'),
        Field('monmode', 0x20, 'i', 'high', '1 mimic in chest, 2 other'),
    ]


class TrapTable(RecordTable):
    REC = TrapRec


# ---------------- global databases ----------------
# Field-level layouts: see docs/formats/databases.md (sources = functions in re/decomp).

# Enumerations (names as shown by the game, D6STRING ids in brackets)
ABILITIES = ['Strength', 'Intellect', 'Spirituality', 'Dexterity', 'Agility', 'Fortitude', 'Will',
             'Presence']                                                         # [1100..1107]
CLANS = ['Human', 'Elf', 'Dwarf', 'Gnome', 'Pixie', 'Omphaaz', 'Whiskah', 'Gourk', 'Ratling',
         'Lizzord']                                                              # [1300..1309]
ROLES = ['Warrior', 'Wizard', 'Priest', 'Rogue', 'Ranger', 'Bard', 'Samurai', 'Paladin',
         'Barbarian', 'Monk', 'Ninja', 'Warlock', 'Assassin', 'Zenmaster', 'Valkyrie']  # [1400..1414]
SKILLS = ['', 'Sword', 'Axe', 'Mace', 'Pole&Staff', 'Dagger', 'Bow', 'Throwing', '2nd Weapon',
          'Shield', 'Kung Fu', 'Sorcery', 'Spiritcraft', 'Suncraft', 'Mooncraft', 'Vinecraft',
          'Stonecraft', 'Fiendcraft', 'Leadership', 'Athletics', 'Scout', 'Traps&Locks',
          'Pickpocket', 'Stealth', 'Forge', 'Artifacts', 'Enchants', 'Blessings', 'Gallantry',
          'Prowess', 'Deathstrike', 'Incantation', 'Music']                      # [1800..1832]
RESISTS = ['Magic', 'Fire', 'Mind', 'Paralysis', 'Death', 'Petrification', 'Cold', 'Wind',
           'Earth', 'Poison', 'Elements', 'Dispel', 'Silence', 'Light', 'Charm', 'Mavin']  # [9950..]
SPECIAL_ATTACKS = ['None', 'Sleep', 'Stun', 'Knock-Out', 'Paralyze', 'Fear', 'Blind', 'Poison',
                   'Stone', 'Death Strike', 'Drain', 'Insane', 'Silence', 'Break', 'Destroy',
                   'Disease']                                                    # [9900..9915]
ITEM_TYPES = ['Special', 'Weapon', 'Ammo', 'Armor', 'Shield', 'Jewelry', 'Scroll', 'Potion',
              'Powder', 'Key', 'Book', 'Food', 'Gold', 'Storage', 'Light', 'Instrument']  # [9400..]
ITEM_SUBTYPES = {
    1: ['Hand', 'Throwing', 'Range'],                                            # [9500..]
    2: ['None', 'Bow', 'Crossbow'],                                              # [9550..]
    3: ['Body', 'Leg', 'Head', 'Hand', 'Foot', 'Head(helm)'],                    # [9600..]
    5: ['Ring', 'Amulet', 'Bracelet', 'Other(off-hand)'],
}
ITEM_ENCHANTS = ['Zap', 'Flamestrike', 'Iceball', 'Pro Fire', 'Pro Ice', 'Pro Magic', 'Hit',
                 'Damage', 'Toughness', 'Armor', 'Regenerate', 'Special', 'Recharge']  # [9300..]
ITEM_FLAGS = {0x0001: 'quest/plot item (never sold, kept in world list)',
              0x0002: 'always identified',
              0x0010: 'stackable (charges field = quantity; weight/price per unit)',
              0x0020: 'off-hand weapon (2nd weapon slot)', 0x0040: 'two-handed',
              0x0080: 'missile: special collision', 0x0200: 'shows attached model on wearer',
              0x0400: 'random charges', 0x0800: 'not sellable in shops',
              0x1000: 'cannot be stolen', 0x2000: 'cannot be enchanted/blessed',
              0x4000: 'equip conflict check (helm/armour)', 0x8000: 'off-hand only'}
MON_CLASSES = {0: 'animal', 1: 'humanoid', 2: 'undead', 3: 'undead(alt)', 4: 'vampire/seductress',
               5: 'lycanthrope', 6: 'demon/magical', 7: 'dragon/reptile', 8: 'insect/serpent',
               9: 'slime/fungus', 10: 'plant', 11: 'fish', 12: 'invulnerable boss phase',
               13: 'construct', 14: 'spirit (non-combat)', 15: 'object/inanimate'}
TREASURE_TYPES = {0: 'empty', 1: 'item = DiceRoll(a,b,c)', 2: 'item = DiceRoll(a,b,c)',
                  3: 'item from D6TRLIST lists a/b/c (chance pA%/pB%/rest)',
                  4: 'experience = DiceRoll(a,b,c) shared by the party'}


def bits_str(value, names):
    """names: list (bit i) or dict (mask -> text)."""
    if isinstance(names, dict):
        return [t for m, t in sorted(names.items()) if value & m]
    return [n for i, n in enumerate(names) if value >> i & 1]


class MonsRec(Record):
    """D6MONS.DAT record (MonRec_Load_, InitMONSToMonster_, combat.c, genenc.c)."""
    SIZE = 0x154
    FIELDS = [
        Field('name', 0x00, 'str22', 'high', 'display name (MyName_ returns the record pointer); 21 chars + NUL'),
        Field('mclass', 0x16, 'h', 'med', 'creature class: 0 animal 1 humanoid 2 undead 3 undead(alt) 4 vampire/seductress '
                                          '5 lycanthrope 6 demon/magical 7 dragon/reptile 8 insect/serpent 9 slime/fungus '
                                          '10 plant 11 fish 12 invulnerable(boss phase) 13 construct 14 spirit/non-combat '
                                          '15 object (statue, cart). 12/14/15 are invulnerable or passive'),
        Field('npc', 0x18, 'h', 'high', 'NPC id (D6NPC record / NPCDATA.PAK slot); !=0 -> talking NPC (NpcInitNPC_)'),
        Field('gender', 0x1A, 'h', 'high', '0 male 1 female (NPC script GENDER, PC +0x18 equivalent)'),
        Field('clan', 0x1C, 'h', 'med', 'clan/race 0..9 (Human..Lizzord); 6/7/8 = furred, no hair twiddling'),
        Field('role', 0x1E, 'h', 'med', 'role/class 0..14 (Warrior..Valkyrie), read by NPC scripts'),
        Field('alignment', 0x20, 'h', 'high', '0..99 (/33: 0 Evil 1 Neutral 2 Good); default 50'),
        Field('unk22', 0x22, 'h', 'low', 'unknown, 0 or 50'),
        Field('weight', 0x24, 'h', 'high', 'mass for knock-back / pushing (pushy.c object_weight_, wcoll.c)'),
        Field('unk26', 0x26, 'h', 'low', 'unknown (128..2048, probably a radius); not read by code found so far'),
        Field('speed', 0x28, 'i', 'high', 'movement speed (-> float runtime +0x70; x constants on Hard)'),
        Field('abilities', 0x2C, '8h', 'high', 'STR INT SPI DEX AGI FOR WIL PRE (0..25); FOR*2 = air supply, WIL = spell power'),
        Field('resist', 0x4C, '16h', 'high', 'resistance % per type: Magic Fire Mind Paralysis Death Petrification Cold Wind '
                                            'Earth Poison Elements Dispel Silence Light Charm Mavin (ResistValue_)'),
        Field('hpdice', 0x6C, '3i', 'high', 'hit points = DiceRoll(count, sides, bonus); count is also the monster LEVEL '
                                           '(ResistValue_, TestForSummoning_, level-scaled weapons)'),
        Field('unk78', 0x78, 'h', 'low', 'unknown (0..30000, scales with toughness)'),
        Field('hit', 0x7A, 'h', 'high', 'to-hit rating (runtime +0x202 = PC +0x11C); -2 Easy, +2 Hard'),
        Field('parry', 0x7C, 'h', 'high', 'parry/defence rating (runtime +0x204); -2 Easy, +2 Hard'),
        Field('armor', 0x7E, 'h', 'high', 'armour rating (runtime +0x206); -2 Easy, +2 Hard'),
        Field('kungfu', 0x80, 'h', 'high', 'Kung Fu level used by natural punch/kick attacks (item flags 0xAC&0xC0)'),
        Field('bloodtype', 0x82, 'h', 'high', 'hit effect: 0 red blood (can be vampire-drained) 1/2/3 other effects (0x22/0x23/0x2A)'),
        Field('npcanimA', 0x84, 'h', 'med', 'extra animation set id loaded / NPC talk mode anim (GetNPCModeAnim_)'),
        Field('npcanimB', 0x86, 'h', 'med', 'second NPC mode animation set id'),
        Field('shadow', 0x88, 'h', 'high', 'shadow texture (-1 = no shadow) (SetMonsterShadow_)'),
        Field('reach', 0x8A, 'h', 'high', 'melee engage distance, <1 -> 1536 (MonsterUpdateMON_)'),
        Field('unk8c', 0x8C, 'h', 'low', 'unknown (0/256/512)'),
        Field('hitsound', 0x8E, 'h', 'low', 'per-monster hit sound/variant used by ExecFight_ (0..5)'),
        Field('specialA', 0x90, 'h', 'high', 'special power A: <1000 item-spell id (ItemSpell_), >=1000 monster power '
                                            '(breath etc.); used with chance specApct'),
        Field('specialB', 0x92, 'h', 'high', 'special power B (chance specBpct)'),
        Field('specrange', 0x94, 'i', 'high', 'range of steal / special powers (0 -> 8192, steal 1536)'),
        Field('modelheight', 0x98, 'i', 'high', 'overrides model collision height (model +0x69C); <0 -> 0'),
        Field('xp', 0x9C, 'i', 'high', 'experience award (x4/5 Easy if >=7, x5/4 Hard)'),
        Field('treasureA', 0xA2, 'h', 'high', 'D6TREAS record rolled into the drop inventory (positive ids, dropped on death)'),
        Field('treasureB', 0xA4, 'h', 'high', 'D6TREAS record for the remaining slots (stored negated, never dropped)'),
        Field('rt_treasA', 0xA8, 'I', 'high', 'runtime: pointer to loaded treasure A (zeroed by MonRec_Load_)'),
        Field('rt_treasB', 0xAC, 'I', 'high', 'runtime: pointer to loaded treasure B'),
        Field('gfx', 0xB0, 'h', 'high', 'model index into exe _MonMDLData (17..120; 0..16 = PC bodies); also MONSND default'),
        Field('overlays', 0xB2, '5h', 'med', 'skin overlay texture ids (low byte used), applied in order 1,4,2,0,3; '
                                           '[2]!=0 -> no hair'),
        Field('equip', 0xBC, '16I', 'high', '16 x (u8 kind, u8 chance%, i16 item): kind 1 main weapon 2 off-hand '
                                          '6 missile, others = attachments; see equip_list()'),
        Field('mana', 0xFC, '6h', 'high', 'mana per realm Spirit Sun Moon Vine Stone Fiend (100 default, 9999 = endless)'),
        Field('spells', 0x108, '16B', 'high', 'spell book: up to 16 spell ids (ItemSpell_) chosen by MonsterAISpell_'),
        Field('meleepct', 0x118, 'B', 'high', '!=0 -> fights with weapons (MonsterCombatAI_)'),
        Field('spellpct', 0x119, 'B', 'high', 'chance % per AI tick to cast from the spell book (range 8192)'),
        Field('unk11a', 0x11A, 'B', 'low', 'unknown'),
        Field('specApct', 0x11B, 'B', 'high', 'chance % to use specialA'),
        Field('specBpct', 0x11C, 'B', 'high', 'chance % to use specialB'),
        Field('stealpct', 0x11D, 'B', 'high', 'chance % to try stealing (OpSteal_)'),
        Field('unk120', 0x120, 'B', 'low', 'unknown'),
        Field('flypct', 0x121, 'B', 'med', '!=0 -> flying monster (fly/land anim 0x1F..0x21, MonsterUpdateMON_)'),
        Field('atkdelay', 0x128, 'i', 'high', 'attack recovery time ms (x4/3 Easy, x3/4 Hard)'),
        Field('unk12c', 0x12C, 'i', 'low', '11 on 31 small critters, else 0'),
        Field('flags130', 0x130, 'B', 'med', '1 humanoid hair 2 hood/hat hair 4 ? 8 animal 0x10 ? 0x20 rogue 0x40 zombie'),
        Field('flags131', 0x131, 'B', 'med', '1 flyer(bat) 2 mount (horse, trolley) 4 translucent 8 swimmer 0x10 eyes '
                                           '0x20 amphibian 0x40 ship (BSP rot object) 0x80 shipwreck'),
        Field('flags132', 0x132, 'B', 'med', '1 raft 2/4 big flyer 8 can be placed in "hold" (ambush) mode '
                                           '0x10 ambient critter (no difficulty scaling, idle anims) 0x20 ? 0x40 unique'),
        Field('groupdice', 0x13C, '3i', 'high', 'group size = DiceRoll(count, sides, bonus) for encounters/summons'),
        Field('companionpct', 0x148, 'h', 'high', 'chance % that an encounter adds a group of companionA/B (GenerateEncounter_)'),
        Field('companionA', 0x14A, 'h', 'high', 'D6MONS record of the companion group'),
        Field('companionB', 0x14C, 'h', 'high', 'alternative companion (50/50 when non-zero)'),
        Field('soundrec', 0x14E, 'h', 'high', 'D6MONSND record (0 -> no sounds); normally = gfx'),
        Field('rt_sound', 0x150, 'I', 'high', 'runtime: pointer to the loaded D6MONSND record'),
    ]

    def equip_list(self):
        out = []
        for k in range(16):
            kind, pct, item = struct.unpack_from('<BBh', self.raw, 0xBC + 4 * k)
            if kind:
                out.append(dict(kind=kind, pct=pct, item=item))
        return out


class MonsTable(RecordTable):
    REC = MonsRec


class ItemRec(Record):
    """D6ITEM.DAT record (LoadItems_; consumers in pcinvent.c, combat.c, townsmit.c)."""
    SIZE = 0x11C
    FIELDS = [
        Field('name', 0x00, 'str22', 'high', 'identified name; natural attacks: verb phrase, "$" = target'),
        Field('unidname', 0x16, 'str22', 'high', 'name while unidentified ("?Sword?"); natural attacks: attack name'),
        Field('type', 0x2C, 'h', 'high', '0 Special 1 Weapon 2 Ammo 3 Armor 4 Shield 5 Jewelry 6 Scroll 7 Potion 8 Powder '
                                       '9 Key 10 Book 11 Food 12 Gold 13 Storage 14 Light 15 Instrument'),
        Field('subtype', 0x2E, 'h', 'high', 'Weapon: 0 hand 1 throwing 2 ranged; Ammo: 1 arrow 2 bolt; Armor: 0 body 1 leg '
                                          '2 head 3 hand 4 foot 5 helm(D6HELM); Jewelry: 0 ring 1 amulet 2 bracelet 3 other'),
        Field('icon', 0x30, 'h', 'high', 'inventory icon (exe _icondata, 246 entries: itemicon\\<name>.bmp + grid w,h)'),
        Field('weight', 0x32, 'h', 'high', 'weight in 0.1 lb'),
        Field('invoketype', 0x34, 'B', 'high', 'book/tome effect: 1 ability+1 2 skill+100 3 give trait 4 learn spell'),
        Field('invokeparam', 0x35, 'B', 'high', 'ability 0..7 / skill 1..32 / trait 0..65 / spell 1..105'),
        Field('invokeconsume', 0x36, 'h', 'high', '!=0 -> item destroyed after invoking'),
        Field('usemode', 0x3C, 'B', 'high', '0 not usable 1 usable 2 give/show to NPC 3 lock pick (bonus = minstr)'),
        Field('durdice', 0x4C, '3i', 'high', 'durability DiceRoll(n,s,b); max = n*s+b; 0 = never wears'),
        Field('price', 0x58, 'i', 'high', 'base price (per unit for stackables)'),
        Field('spell', 0x5C, 'h', 'high', 'spell cast on use (ItemSpell_ id; >=1000 special power)'),
        Field('chargedice', 0x60, '3i', 'high', 'charges / quantity at creation DiceRoll(n,s,b) (GetItemCharges_)'),
        Field('readtext', 0x6C, 'i', 'high', 'TEXTPAK id shown by the READ option (letters, inscriptions); 0 = none'),
        Field('restrict', 0x70, 'I', 'high', 'NOT usable by: bits 0-9 clans, 10-24 roles, 25-26 gender, 27-29 '
                                           'alignment Evil/Neutral/Good (IsItemUsableByPC_)'),
        Field('cursed', 0x74, 'B', 'high', '1 = cursed (sticks when equipped)'),
        Field('curseeffect', 0x75, 'B', 'med', '1 = drains 1 HP every cursetick ticks'),
        Field('cursetick', 0x76, 'h', 'med', 'curse period'),
        Field('unk96', 0x96, 'H', 'low', 'unknown (1/2 on 35 items)'),
        Field('flags98', 0x98, 'H', 'high', '1 quest 2 always identified 0x10 stackable 0x20 off-hand 0x40 two-handed '
                                          '0x80 missile 0x200 show model 0x400 random charges 0x800 not sellable '
                                          '0x1000 unstealable 0x2000 not enchantable 0x4000 0x8000 off-hand only'),
        Field('range', 0x9C, 'i', 'high', 'weapons: max range (melee 1280, bows 6144); helms: D6HELM helm index; '
                                        'body armour: PC costume (MyPCModel_)'),
        Field('minrange', 0xA0, 'i', 'high', 'minimum range for missile weapons'),
        Field('unka4', 0xA4, 'i', 'low', 'unknown (1280 on 20 items)'),
        Field('minstr', 0xA8, 'h', 'high', 'minimum Strength; lock picks: pick bonus'),
        Field('ammotype', 0xAA, 'h', 'high', 'launcher: ammo subtype required (1 arrow 2 bolt)'),
        Field('atkflags', 0xAC, 'B', 'med', '0x40/0x80 unarmed kung-fu attack (uses Kung Fu skill / monster kungfu)'),
        Field('atkflags2', 0xAD, 'B', 'med', '2 natural attack 4 alt anim 8 thrust anims 0x10 damage scales with level '
                                           '0x20 no use/charges'),
        Field('damage', 0xB0, '3i', 'high', 'damage DiceRoll(n,s,b): shown "Damage n+b - n*s+b"'),
        Field('dmgextra', 0xBC, '2h', 'low', 'copied to instance +0x2E/+0x30 (0..4)'),
        Field('specials', 0xC0, '8i', 'high', '4 x (u8 type, u8 chance%, i16 a, i32 b): type = Sleep..Disease; '
                                            'duration (rand(a)+b) s; Poison/Drain use a,b; see specials_list()'),
        Field('resist', 0xF0, '16B', 'high', 'resistance bonus % per resist type (Magic..Mavin)'),
        Field('enchant', 0x104, 'h', 'high', 'enchantment level (+N)'),
        Field('hitbonus', 0x106, 'h', 'high', 'to-hit bonus'),
        Field('unk108', 0x108, 'h', 'med', 'negative on heavy armour (penalty, copied to instance +0x46)'),
        Field('ac', 0x10A, 'h', 'high', 'armour class bonus (shields: "Rating", jewelry: "AC Protection")'),
        Field('regen', 0x10C, 'h', 'high', 'regeneration bonus'),
        Field('unk10e', 0x10E, 'h', 'low', 'unknown'),
        Field('tough', 0x110, 'h', 'high', 'toughness % (damage resistance of the item itself)'),
        Field('costume', 0x112, 'h', 'high', 'PC body/robe texture set (40..43 = robes, PCInRobes_)'),
        Field('model', 0x114, 'h', 'high', 'index into exe _ItemMDLData (item/<name>.mdl), must be < 240'),
        Field('skill', 0x118, 'h', 'high', 'skill used (1 Sword ... 9 Shield, see SKILLS)'),
        Field('enchantmask', 0x11A, 'H', 'high', 'allowed enchantments bitmask: Zap Flamestrike Iceball ProFire ProIce '
                                               'ProMagic Hit Damage Toughness Armor Regenerate Special Recharge'),
    ]

    def specials_list(self):
        out = []
        for k in range(4):
            t, pct, a, b = struct.unpack_from('<BBhi', self.raw, 0xC0 + 8 * k)
            if t:
                out.append(dict(type=t, name=SPECIAL_ATTACKS[t & 15], pct=pct, a=a, b=b))
        return out


class ItemTable(RecordTable):
    REC = ItemRec


class PropRec(Record):
    """D6PROP.DAT record (LoadProp_, LoadPropObject_; record index must be < 256)."""
    SIZE = 0x38
    FIELDS = [
        Field('name', 0x00, 'str24', 'high', 'display name'),
        Field('model', 0x18, 'i', 'high', 'index into exe _PropMDLData (prop/<name>.mdl), must be < 250'),
        Field('objtype', 0x1C, 'h', 'high', 'graph object class -> gobj+0x6C (1 = tree/foliage, else 0)'),
        Field('dragicon', 0x1E, 'h', 'high', 'drag icon shown when targeted (0..37, exe _dragiconname)'),
        Field('flags', 0x20, 'B', 'high', '1 switch, 2 custom scale, 4 collide, 8 terrain-follow 4 corners, '
                                         '0x10 container (chest), 0x20 snap in BSP, 0x40 anim-texture, 0x80 animated'),
        Field('flags2', 0x21, 'B', 'med', '1,2 render flags, 4 y-lock sprite, 8/0x10 perspective, 0x20 float, '
                                          '0x40 smashable (crate/barrel), 0x80 fountain (drink)'),
        Field('flags3', 0x22, 'B', 'high', '1 floats on water, 2 can be pushed/dragged (wcoll.c)'),
        Field('scale', 0x24, 'f', 'high', 'float copied to model+0x69C (height/scale) when flags&2'),
        Field('height', 0x28, 'f', 'med', 'mass (float) for push/drag physics; >0 = movable, disables ground adjust'),
        Field('broken', 0x2C, 'h', 'high', 'D6PROP record that replaces a smashed prop'),
        Field('smashefx', 0x2E, 'h', 'high', 'effect index (_gCrateEfx) when smashed'),
        Field('sound', 0x30, 'h', 'high', 'ambient SFX (MONSOUND id)'),
        Field('soundparam', 0x34, 'i', 'med', 'ambient SFX radius'),
    ]


class PropTable(RecordTable):
    REC = PropRec


class NpcRec(Record):
    """D6NPC.DAT record (LoadNPC_, LoadNPCNames_); record index = MonsRec.npc."""
    SIZE = 0x24
    FIELDS = [
        Field('name', 0x00, 'str24', 'high', 'NPC name'),
        Field('gold', 0x18, 'I', 'high', 'gold carried (npc+0x20; NPCGoldToBag_, trading)'),
        Field('treasureA', 0x1C, 'H', 'high', 'D6TREAS record for the NPC inventory (npc+0x68, LoadNPCInventory_)'),
        Field('pricepct', 0x1E, 'H', 'med', 'price % used when trading (npc+0xAC, GetNPCSellItemPrice_)'),
        Field('trader', 0x20, 'H', 'med', '>0 enables pricepct (npc+0xAE)'),
        Field('treasureB', 0x22, 'H', 'high', 'second D6TREAS record (npc+0x6A)'),
    ]


class NpcTable(RecordTable):
    REC = NpcRec


class TreasRec(Record):
    """D6TREAS.DAT record: 10 entries x 20 bytes (SetChestTreasure_, MakeMonInventory_, LoadNPCInventory_)."""
    SIZE = 200
    FIELDS = [Field('entries', 0, '50i', 'high', '10 x (i16 type, i16 chance%, i32 a, i32 b, i32 c, i16 pA, i16 pB); '
                                                'type 1/2 item=DiceRoll(a,b,c), 3 D6TRLIST lists a/b/c, 4 exp; '
                                                'see entries_list()')]

    def entries_list(self):
        out = []
        for i in range(10):
            t, pct, a, b, c, pa, pb = struct.unpack_from('<hhiiihh', self.raw, i * 20)
            out.append(dict(type=t, pct=pct, a=a, b=b, c=c, pa=pa, pb=pb))
        return out


class TreasTable(RecordTable):
    REC = TreasRec


class TrListRec(Record):
    """D6TRLIST.DAT record (LoadTreasureLists_, GetTreasureListItem_); at most 127 lists."""
    SIZE = 0x3C
    FIELDS = [
        Field('name', 0x00, 'str20', 'high', 'list name (editor only)'),
        Field('ranges', 0x14, '20h', 'high', '10 (first,last) D6ITEM ranges; first<=0 = unused, last<=first = single item'),
    ]


class TrListTable(RecordTable):
    REC = TrListRec


class MonSndRec(Record):
    """D6MONSND.DAT record, 0xB8 bytes, index = MonsRec.soundrec (one per monster model)."""
    SIZE = 0xB8
    FIELDS = [
        Field('modelno', 0x00, 'I', 'high', 'model number (editor label, = record index)'),
        Field('label', 0x04, 'str20', 'high', 'editor label, e.g. "SKELETON.MDL"'),
        Field('slots', 0x18, '80h', 'high', '16 slots x (u8 count 0..3, u8 extra, i16 timer (runtime), i16 sfx[3]); '
                                          'slot 0 alert/move 1 idle growl 2 wound 3 death 4 attack, 5.. attack-anim '
                                          'sounds; sfx = MONSOUND.DAT ids; see slot_list()'),
    ]

    def slot_list(self):
        out = []
        for k in range(16):
            n, extra, timer, s0, s1, s2 = struct.unpack_from('<BBh3h', self.raw, 0x18 + 10 * k)
            out.append(dict(count=n, extra=extra, timer=timer, sfx=[s0, s1, s2][:n]))
        return out


class MonSndTable(RecordTable):
    REC = MonSndRec


class HelmRec(Record):
    """D6HELM.DAT 0x26 record = helmet attachment transform for one (monster model, helm) pair."""
    SIZE = 0x26
    FIELDS = [
        Field('pos', 0x00, '3f', 'med', 'attachment translation'),
        Field('rot', 0x0C, '3f', 'high', 'rotation vector (chardemo editor writes _AttachRotVec)'),
        Field('scale', 0x18, '3f', 'high', 'scale (_AttachScale)'),
        Field('hair', 0x24, 'h', 'high', 'hair style used with this helmet (TwiddleHair_)'),
    ]


class SfxList(object):
    """MONSOUND.DAT (text, read by sndread.c): 'max' line, then 'id,"wavname"[,R]' lines;
    '*' starts a comment; ids < 1024; R = resident (preloaded). Kept verbatim on build."""
    @classmethod
    def parse(cls, data):
        s = cls()
        s.raw = bytes(data)
        s.max = None
        s.sounds = {}
        for line in s.raw.split(b'\n'):
            t = line.split(b'*')[0].strip()
            if not t:
                continue
            if s.max is None:
                try:
                    s.max = int(t.split()[0])
                except ValueError:
                    s.max = 0
                continue
            parts = t.split(b',')
            try:
                sid = int(parts[0])
            except ValueError:
                continue
            if sid < 0:
                break
            name = parts[1].strip().strip(b'"').decode('latin-1') if len(parts) > 1 else ''
            res = len(parts) > 2 and parts[2].strip().upper().startswith(b'R')
            s.sounds[sid] = (name, res)
        return s

    def build(self):
        return self.raw


class EmitterRec(Record):
    SIZE = 0x80
    FIELDS = [
        Field('name', 0x00, 'str24', 'high', 'emitter name'),
        Field('anim', 0x74, 'i', 'med', 'animation / FXC item (index resolved at load)'),
        Field('sprite', 0x7C, 'i', 'med', 'alpha sprite index (resolved at load)'),
    ]


class EmitterTable(RecordTable):
    """u32 count then count*0x80 (no dummy record 0)."""
    REC = EmitterRec
    HEADER_SIZE = 4


class ObjListRec(Record):
    SIZE = 0x40
    FIELDS = [
        Field('tag', 0x00, '4s', 'high', "type letter + 3 ASCII digits: I=item M=monster P=prop B=BSP F=foliage '0'=empty"),
        Field('pos', 0x04, '3f', 'high', 'position (world, or BSP-relative in .BOL)'),
        Field('rot', 0x10, '3f', 'high', 'rotation / for B records: BSP size'),
        Field('b20', 0x20, 'B', 'high', 'monster: entry flags (1 ready,2 dead,4 fade,8 no ground snap,0x10 hold/ambush,0x20,0x40); '
                                       'B: bsp number; P in mines: group'),
        Field('b21', 0x21, '3b', 'med', 'B: tile offsets; P: [2]=start disabled'),
        Field('bspname', 0x30, 'str16', 'high', 'B records: BSP base name'),
    ]

    @property
    def kind(self):
        return chr(self.raw[0])

    @property
    def recno(self):
        t = bytes(self.raw[1:4])
        try:
            return int(t.decode('ascii'))
        except ValueError:
            return None


class ObjList(RecordTable):
    """TOL/BOL/FOL: 0x40-byte records; record 0 header, u32 at 0 = record count INCLUDING
    the header. Record index (1-based) is the object id used by scripts."""
    REC = ObjListRec
    COUNT_BIAS = 1


# ---------------- D6HELM ----------------

class Helm(object):
    """u32 nModels, u32 nHelms(=16), i16 modelIdx[128], then nModels*16 records of 0x26."""
    @classmethod
    def parse(cls, data):
        h = cls()
        h.nmodels, h.nhelms = struct.unpack_from('<II', data, 0)
        h.modelidx = list(struct.unpack_from('<128h', data, 8))
        h.records = [bytes(data[0x108 + i * 0x26:0x108 + (i + 1) * 0x26])
                     for i in range((len(data) - 0x108) // 0x26)]
        h.trailer = bytes(data[0x108 + len(h.records) * 0x26:])
        return h

    def build(self):
        return (struct.pack('<II', self.nmodels, self.nhelms) + struct.pack('<128h', *self.modelidx)
                + b''.join(self.records) + self.trailer)

    def rec(self, model, helm):
        """HelmRec for monster model number `model` (searched in modelidx[:nmodels]) and helm
        index `helm` (0..15 = ItemRec.range of a helm) - mirrors GetHelmData_ (scenload.c)."""
        try:
            slot = self.modelidx[:self.nmodels].index(model)
        except ValueError:
            return None
        return HelmRec(self.records[slot * self.nhelms + helm])


# ---------------- shop inventory ----------------

def d6_checksum(data):
    """CheckSum_ (checksum.c) - uint32 arithmetic."""
    n = len(data)
    q, r = n >> 2, n & 3
    s = (n + q + r) & 0xFFFFFFFF
    for i in range(n - 1, n - r - 1, -1):
        s = (s + data[i]) & 0xFFFFFFFF
    words = struct.unpack_from('<%dI' % q, data, 0)
    for i, w in enumerate(words):
        if (i & 1) == 0:
            s = ((s + w) * 2) & 0xFFFFFFFF
        else:
            s = ((s + w) & 0xFFFFFFFF) >> 1
        s = (~s) & 0xFFFFFFFF
    return s


class SmitInv(object):
    """0x800 bytes = 256 * (u16 item, u16 pad, i32 count) then u32 CheckSum_."""
    @classmethod
    def parse(cls, data):
        s = cls()
        s.entries = [list(struct.unpack_from('<HHi', data, i * 8)) for i in range(256)]
        s.checksum = struct.unpack_from('<I', data, 0x800)[0]
        s.trailer = bytes(data[0x804:])
        return s

    def body(self):
        return b''.join(struct.pack('<HHi', *e) for e in self.entries)

    def build(self, fix_checksum=False):
        b = self.body()
        c = d6_checksum(b) if fix_checksum else self.checksum
        return b + struct.pack('<I', c) + self.trailer

    def checksum_ok(self):
        return d6_checksum(self.body()) == self.checksum


# ---------------- D6STRING ----------------

class StringTable(object):
    """u32 n, n*(u32 id, u32 fileoffset) sorted by id, then NUL-terminated strings."""
    @classmethod
    def parse(cls, data):
        t = cls()
        n = struct.unpack_from('<I', data, 0)[0]
        t.entries = [list(struct.unpack_from('<II', data, 4 + 8 * i)) for i in range(n)]
        t.pool_off = 4 + 8 * n
        t.pool = bytes(data[t.pool_off:])
        t.strings = {}
        for sid, off in t.entries:
            t.strings[sid] = cstr(data[off:off + 4096])
        t.dirty = False
        return t

    def set(self, sid, text):
        self.strings[sid] = text
        if sid not in [e[0] for e in self.entries]:
            self.entries.append([sid, 0])
            self.entries.sort()
        self.dirty = True

    def build(self, rebuild=None):
        if rebuild is None:
            rebuild = self.dirty
        n = len(self.entries)
        if not rebuild:
            out = struct.pack('<I', n) + b''.join(struct.pack('<II', *e) for e in self.entries) + self.pool
            return out
        pool = bytearray()
        base = 4 + 8 * n
        ents = []
        for sid, _ in self.entries:
            ents.append((sid, base + len(pool)))
            pool += self.strings[sid].encode('latin-1') + b'\0'
        return struct.pack('<I', n) + b''.join(struct.pack('<II', *e) for e in ents) + bytes(pool)


# ---------------- TEXTPAK ----------------

class TextPak(object):
    """u32 n, n*(i32 id, u32 offset, u32 size) then text blobs ('@' = paragraph break)."""
    @classmethod
    def parse(cls, data):
        t = cls()
        n = struct.unpack_from('<I', data, 0)[0]
        t.dir = [list(struct.unpack_from('<iII', data, 4 + 12 * i)) for i in range(n)]
        t.data_off = 4 + 12 * n
        t.blob = bytes(data[t.data_off:])
        t.texts = dict((i, bytes(data[o:o + s])) for i, o, s in t.dir)
        t.dirty = False
        return t

    def text(self, tid):
        b = self.texts.get(tid)
        return None if b is None else cstr(b)

    def build(self, rebuild=None):
        if rebuild is None:
            rebuild = self.dirty
        n = len(self.dir)
        if not rebuild:
            return struct.pack('<I', n) + b''.join(struct.pack('<iII', *e) for e in self.dir) + self.blob
        blob = bytearray()
        base = 4 + 12 * n
        d2 = []
        for i, _, _ in self.dir:
            b = self.texts[i]
            d2.append((i, base + len(blob), len(b)))
            blob += b
        return struct.pack('<I', n) + b''.join(struct.pack('<iII', *e) for e in d2) + bytes(blob)


# ---------------- PAK archives ----------------

class Pak(object):
    """Header = array of (u32 offset, u32 size); header length = offset of first member.
    NPCDATA.PAK: 0xF08 = 481 slots = [0] lexicon keys, [1+n] NPC n code, [161+n] NPC n strings,
    [321+n] NPC n responses.  GMDATA.PAK: 0x308 = 97 slots, same layout with 32 guildmasters."""
    @classmethod
    def parse(cls, data):
        p = cls()
        first = struct.unpack_from('<I', data, 0)[0]
        p.nslots = first // 8
        p.dir = [list(struct.unpack_from('<II', data, 8 * i)) for i in range(p.nslots)]
        p.data = bytes(data)
        return p

    def members(self):
        out = []
        for i, (o, s) in enumerate(self.dir):
            if s:
                out.append((i, o, s))
        return out

    def member(self, i):
        o, s = self.dir[i]
        return self.data[o:o + s]

    def slot_name(self, i):
        groups = (self.nslots - 1) // 3
        if i == 0:
            return 'keys'
        g, n = divmod(i - 1, groups)
        return ('code', 'strings', 'responses')[g] + '%03d' % n

    def build(self):
        out = bytearray(self.data)
        for i, (o, s) in enumerate(self.dir):
            struct.pack_into('<II', out, 8 * i, o, s)
        return bytes(out)


# ---------------- D6ARCHIV ----------------

ARCH_SHOPS = ['D6SMIT%02d.DAT', 'D6MAGE%02d.DAT', 'D6PAWN%02d.DAT', 'D6DOJO%02d.DAT',
              'D6YARD%02d.DAT', 'D6TMPL%02d.DAT', 'D6HALL%02d.DAT', 'D6TVRN%02d.DAT']


class Archive(object):
    """Save archive written by SaveArchive_ (segwrite.c). Header 0x2B4 bytes (173 u32):
    [0] version 0x140, [1] party warship flag, [2] GM unique bits, [3] fog/marker blob offset,
    [4] ROSTER offset (= fog end), [5] ROSTER size, [6] D6WORLD offset, [7] D6WORLD size,
    [8..23] seg present, [24..39] seg offset, [40..55] seg size (D6SEGnn.GAM),
    [56..70] journal present, [71..85] offset, [86..100] size (JOURNAL.nnn),
    [101..124] shop present, [125..148] offset, [149..172] size (24 shop files)."""
    @classmethod
    def parse(cls, data):
        a = cls()
        a.h = list(struct.unpack_from('<173I', data, 0))
        a.data = bytes(data)
        return a

    def members(self):
        h = self.h
        out = [('D6WORLD.DAT', h[6], h[7])]
        for i in range(16):
            if h[8 + i]:
                out.append(('D6SEG%02d.GAM' % i, h[24 + i], h[40 + i]))
        for i in range(15):
            if h[56 + i]:
                out.append(('JOURNAL.%03d' % i, h[71 + i], h[86 + i]))
        out.append(('FOGMARKERS.BIN', h[3], h[4] - h[3]))
        out.append(('ROSTER.DAT', h[4], h[5]))
        for i in range(24):
            if h[101 + i]:
                out.append((ARCH_SHOPS[i // 3] % (i % 3), h[125 + i], h[149 + i]))
        return out

    def build(self):
        out = bytearray(self.data)
        struct.pack_into('<173I', out, 0, *self.h)
        return bytes(out)


# ---------------- EVENTS.DCL / EVENTS.COD ----------------

class EventDCL(object):
    """Game part (LoadEventDCL_ reads 55300 bytes):
       u32 n; u32 codeoff[1024]; char name[1024][42]; u32 firstparam[1024]; u32 nparams[1024]
    Editor part (not read by the game):
       u32 descoff[1024]; u32 descpoolsize; char descpool[descpoolsize];
       u32 nparamnames; char paramname[2048][42]"""
    @classmethod
    def parse(cls, data):
        e = cls()
        e.n = struct.unpack_from('<I', data, 0)[0]
        e.codeoff = list(struct.unpack_from('<1024I', data, 4))
        e.names_raw = bytes(data[0x1004:0x1004 + 42 * 1024])
        o = 0x1004 + 42 * 1024
        e.firstparam = list(struct.unpack_from('<1024I', data, o))
        e.nparams = list(struct.unpack_from('<1024I', data, o + 4096))
        o += 8192
        e.game_end = o
        e.extra = bytes(data[o:])
        e.descs = []
        e.paramnames = []
        if len(data) > o + 4100:
            e.descoff = list(struct.unpack_from('<1024I', data, o))
            psz = struct.unpack_from('<I', data, o + 4096)[0]
            pool = data[o + 4100:o + 4100 + psz]
            e.descs = [cstr(pool[e.descoff[i]:]) for i in range(e.n)]
            po = o + 4100 + psz
            e.nparamnames = struct.unpack_from('<I', data, po)[0]
            po += 4
            e.paramnames = [cstr(data[po + 42 * i:po + 42 * i + 42]) for i in range((len(data) - po) // 42)]
        return e

    def name(self, i):
        return cstr(self.names_raw[42 * i:42 * i + 42])

    def index(self, name):
        u = name.upper()
        for i in range(self.n):
            if self.name(i).upper() == u:
                return i
        return -1

    def params_of(self, i):
        f, c = self.firstparam[i], self.nparams[i]
        if self.paramnames:
            return self.paramnames[f:f + c]
        return ['P%d' % k for k in range(c)]

    def desc(self, i):
        return self.descs[i] if i < len(self.descs) else ''

    def build(self):
        out = struct.pack('<I', self.n) + struct.pack('<1024I', *self.codeoff) + self.names_raw
        out += struct.pack('<1024I', *self.firstparam) + struct.pack('<1024I', *self.nparams)
        return out + self.extra


# opcode -> (mnemonic, has_target, note)   operand counts come from the instruction itself
EV_OPS = {
    0x01: ('END', False, 'stop'),
    0x02: ('END2', False, 'stop'),
    0x03: ('GOTO', True, 'jump'),
    0x04: ('IFSTATE', True, 'jump if WState[p0]!=0'),
    0x05: ('OPENDOOR', True, 'queued: door,speed,navpnt,sfx; resume at target'),
    0x06: ('CLOSEDOOR', True, 'queued: door,speed,navpnt,sfx'),
    0x07: ('DELAY', True, 'queued: delay ms'),
    0x08: ('SETSTATE', False, 'WState[p0]=p1'),
    0x09: ('TOGGLESTATE', False, 'WState[p0]^=1'),
    0x0A: ('SETSWITCH', False, 'switch p0 := p1 (linked)'),
    0x0B: ('TOGGLESWITCH', False, 'switch p0 toggled'),
    0x0C: ('ENABLETRIG', False, 'trigger p0 enabled'),
    0x0D: ('DISABLETRIG', False, 'trigger p0 disabled'),
    0x0E: ('ENABLEBOUND', False, 'bound p0 enabled'),
    0x0F: ('DISABLEBOUND', False, 'bound p0 disabled'),
    0x10: ('ENABLESWITCH', False, ''),
    0x11: ('DISABLESWITCH', False, ''),
    0x12: ('CREATEITEM', False, 'item,x,y,z,bsp'),
    0x17: ('PCBLESSING', False, 'type,value,questflag (shrine/pool reward)'),
    0x19: ('SETWEATHER', False, 'weather'),
    0x1B: ('GENMONSTER', False, 'monrec,x,y,z,bsp,init,maxcount (random encounter)'),
    0x1C: ('TEXTMSG', False, 'textpak id, time'),
    0x1E: ('IFHASITEM', True, 'jump if PC has item p0'),
    0x21: ('GOTOBSP', False, 'bsp,destnav,lastnav'),
    0x22: ('GOTOBSPTERR', False, 'bsp,destnav,lastnav'),
    0x23: ('OPENDBL', True, 'queued: doorA,doorB,speed,navpnt,sfx'),
    0x24: ('CLOSEDBL', True, 'queued: doorA,doorB,speed,navpnt,sfx'),
    0x25: ('RELEASEMONS', False, '4 monster object ids leave hold mode'),
    0x26: ('EFXATOBJ', False, 'efx,sfx,spell,obj,yoffset'),
    0x27: ('MAGICPOOL', False, 'pool type'),
    0x28: ('MAGICFIRE', False, 'fire type'),
    0x29: ('TWIDDLEPROP', False, 'obj,type,value'),
    0x2A: ('LOADSEGMENT', False, 'segment,bsp,switch,status,bound'),
    0x2B: ('IFALLINBOUND', True, 'jump if all party inside bound p0'),
    0x2C: ('IFUSINGITEM', True, 'jump if PC is using item p0'),
    0x2D: ('DAMAGEPC', False, 'damage,poison'),
    0x2E: ('SHOWTEXT', False, 'combat-log text id'),
    0x2F: ('MOVEENTITY', True, 'queued: 7 operands (entity move, limited distance)'),
    0x30: ('ZEROUSEITEM', False, 'remove used item p0'),
    0x31: ('USEITEMCHARGE', False, 'decrement charges of used item p0'),
    0x32: ('IFNOTOCCUPIED', True, 'jump if WOccupied[p0] empty'),
    0x33: ('IFOCCUPIEDBY', True, 'jump if WOccupied[p0] holds item p1 (-1 any)'),
    0x34: ('PLACEOCCUPY', False, 'item,x,y,z,bsp,slot,rot,flag'),
    0x35: ('CLEAROCCUPIED', False, 'slot'),
    0x36: ('EFXFROMTO', False, 'efx,sfx,spell,obj,yoff,x,y,z,bsp'),
    0x37: ('CALLEVENT', False, 'run trigger p0'),
    0x38: ('SETNOTSTATE', False, 'WState[p0] = !p1'),
    0x39: ('MOVETONAV', False, 'bsp,nav (move actor)'),
    0x3A: ('COMBO4', False, 's0,s1,s2,s3,value,dststate,setval'),
    0x3B: ('TELEPORT', False, 'x,y,z,bsp'),
    0x3C: ('IFOBJINBOUND', True, 'jump if object p0 inside bound p1'),
    0x3D: ('ROTATETONAV', False, 'bsp,nav'),
    0x3E: ('SWITCHFROMSTATE', False, 'switch p0 := WState[p1]'),
    0x3F: ('STATEFROMSWITCH', False, 'WState[p0] := switch p1'),
    0x40: ('ANIMOBJ', False, 'obj,anim'),
    0x41: ('SWAPOBJPOS', False, 'objA,objB'),
    0x42: ('EFXATPC', False, 'efx,sfx,spell,obj,yoff'),
    0x43: ('IFSWITCHON', True, 'jump if switch p0 on'),
    0x44: ('PLAYSFX', False, 'sfx'),
    0x45: ('MOVETONAVSPD', False, 'bsp,nav,speed'),
    0x46: ('HIDEPOLYS', False, 'obj,level,flag'),
    0x47: ('CHANGEITEMS', False, 'all world items p0 -> p1'),
    0x48: ('PLACEOCCUPYY', False, 'item,x,y,z,bsp,slot,rot,flag,yoff'),
    0x49: ('TELEPORTOBJ', False, 'x,y,z,bsp,obj'),
    0x4A: ('LADDER', False, '4 object ids'),
    0x4B: ('EFXATOBJ2', False, 'like EFXFROMTO (flag variant)'),
    0x4C: ('SPAWNMONSTER', False, 'monrec,x,y,z,bsp,rot,radius,init'),
    0x4D: ('SETNAVFLAG', False, 'nav,bsp,on'),
    0x4E: ('EFXPOINTS', False, 'efx,sfx,spell,x1,y1,z1,x2,y2,z2,bsp'),
    0x4F: ('ENTERTOWN', False, 'town 0..2'),
    0x50: ('ITEMTOITEM', False, 'used item p0 becomes p1'),
    0x51: ('IFEQUIPPED', True, 'jump if PC has item p0 equipped'),
    0x52: ('MOVEPROP', False, 'obj,x,y,z,bsp'),
    0x53: ('SETQFLAGIF', False, 'flag,expect,value'),
    0x54: ('ENDGAME', False, 'movie 0..2'),
    0x55: ('IFMONALIVE', True, 'jump if a monster of record p0 is alive'),
    0x56: ('PROPWALK', False, 'obj,x,y,z,bsp,speed'),
}
EV_JUMPS = set(k for k, v in EV_OPS.items() if v[1])


def ev_decode(code, pc):
    """Decode one instruction: returns (op, queued, operands, target, length)."""
    op, na = struct.unpack_from('<Hh', code, pc)
    q = bool(op & 0x8000)
    op &= 0x7FFF
    args = list(struct.unpack_from('<%dh' % na, code, pc + 4)) if na > 0 else []
    ln = 4 + 2 * max(na, 0)
    tgt = None
    if q or op in EV_JUMPS:
        tgt = struct.unpack_from('<I', code, pc + ln)[0]
        ln += 4
    return op, q, args, tgt, ln


class EventCode(object):
    """EVENTS.COD: flat bytecode; entry points from EVENTS.DCL codeoff.
    Instruction: u16 opcode (bit15 = queued op), i16 noperands, i16 operand[n]
    (each = index into the trigger's params[]), then u32 absolute target for
    jumps / queued ops (queued ops resume there when finished)."""
    @classmethod
    def parse(cls, data):
        c = cls()
        c.code = bytes(data)
        return c

    def build(self):
        return self.code

    def disasm(self, dcl, idx, paramnames=None):
        start = dcl.codeoff[idx]
        ends = sorted(set(dcl.codeoff[:dcl.n])) + [len(self.code)]
        end = min(x for x in ends if x > start)
        pn = paramnames or dcl.params_of(idx)
        lines = []
        pc = start
        while pc < end:
            op, q, args, tgt, ln = ev_decode(self.code, pc)
            mn = EV_OPS.get(op, ('OP_%02X' % op, False, ''))[0]
            a = ', '.join(pn[x] if 0 <= x < len(pn) else 'p%d' % x for x in args)
            s = '  %04x: %s%s %s' % (pc, 'Q:' if q else '', mn, a)
            if tgt is not None:
                s += ' -> %04x' % tgt
            lines.append(s)
            pc += ln
        return lines


# ---------------- ENTR text ----------------

class EntryList(object):
    """ENTRssnn.DAT (ss = spoke, nn = index): lines 'x y z<TAB>name' CRLF.
    Not read by deep6.exe (developer teleport list). Coordinates are world units."""
    @classmethod
    def parse(cls, data):
        e = cls()
        e.raw = bytes(data)
        e.lines = data.split(b'\n')
        e.entries = []
        for ln in e.lines:
            t = ln.rstrip(b'\r')
            if not t.strip():
                continue
            if b'\t' in t:
                nums, name = t.split(b'\t', 1)
            else:
                nums, name = t, b''
            v = nums.split()
            try:
                xyz = [int(x) for x in v[:3]]
            except ValueError:
                xyz = v[:3]
            e.entries.append((xyz, name.decode('latin-1')))
        e.dirty = False
        return e

    def build(self):
        if not self.dirty:
            return self.raw
        return b''.join(('%d %d %d\t%s\r\n' % (x, y, z, n)).encode('latin-1')
                        for (x, y, z), n in self.entries)


# ---------------- model lists ----------------

class ModelList(object):
    """models/*.lst: first line count, then '$<T><file>' lines (T: M monster, I item, P prop)."""
    @classmethod
    def parse(cls, data):
        m = cls()
        m.raw = bytes(data)
        lines = data.replace(b'\r\n', b'\n').split(b'\n')
        m.count = int(lines[0].strip() or 0)
        m.names = [l.strip().decode('latin-1') for l in lines[1:] if l.strip()]
        return m

    def build(self):
        return self.raw


# ---------------- MDL ----------------

class Mdl(object):
    """Region walker for models/*.mdl (Model_Read_, model.c). Records every structure
    with its file offset; build() re-places them, preserving unreferenced bytes."""
    @classmethod
    def parse(cls, data):
        m = cls()
        d = bytes(data)
        m.size = len(d)
        regs = []

        def R(o, n, k):
            regs.append((o, n, k))
            return o + n
        u32 = lambda o: struct.unpack_from('<I', d, o)[0]
        if d[:4] != b' LDM':
            raise ValueError('bad MDL magic')
        ver = u32(4)
        m.has_framebits = bool(ver & 0x1000000)
        m.version = ver & ~0x1000000
        m.parttab = u32(8)
        m.paltype = d[12]
        o = R(0, 13, 'header')
        o = R(o, {0: 16384, 3: 16384, 1: 0x300, 2: 0x40}[m.paltype], 'palette')
        m.nframes = struct.unpack_from('<H', d, o)[0]
        o = R(o, 2, 'nframes')
        fbits = None
        if m.has_framebits:
            fbits = d[o:o + m.nframes]
            o = R(o, m.nframes, 'framebits')
        m.anims = [struct.unpack_from('<3H', d, o + 6 * i) for i in range(256)]
        o = R(o, 1536, 'animtable')
        m.nattach = d[o]
        o = R(o, 1, 'nattach')
        m.attach_frames = []
        for i in range(m.nattach):
            n = struct.unpack_from('<H', d, o)[0]
            m.attach_frames.append(n)
            o = R(o, 2, 'attach_n')
            o = R(o, n * 48, 'attach_xform')
        m.nparts = d[o]
        o = R(o, 1, 'nparts')
        R(m.parttab, 4 * m.nparts, 'parttable')
        m.parts = []
        for p in range(m.nparts):
            po = u32(m.parttab + 4 * p)
            a, spr, c, flags, nverts = struct.unpack_from('<5I', d, po)
            part = dict(offset=po, sprites_off=spr, flags=flags, nverts=nverts, mips=[])
            o = R(po, 20, 'part_head')
            for mip in range(1 if m.version == 9 else 4):
                nuv = u32(o)
                o = R(o, 4 + nuv * 6, 'uv')
                npo = u32(o)
                o = R(o, 4 + npo * 5, 'poly')
                part['mips'].append((nuv, npo))
            o = R(o, 1, 'p_88')
            pnf = struct.unpack_from('<H', d, o)[0]
            o = R(o, 2, 'p_nframes')
            npath = u32(o)
            o = R(o, 4 + 2 * npath, 'paths')
            fsz = u32(o)
            o = R(o, 4, 'framesize')
            for f in range(pnf):
                if (fbits is None) or fbits[f]:
                    o = R(o, fsz, 'frame')
                o = R(o, 24, 'frame_bbox')
            nt = d[o]
            toffs = struct.unpack_from('<%dI' % nt, d, o + 1) if nt else ()
            o = R(o, 1 + 4 * nt, 'textable')
            part['textures'] = []
            for t in toffs:
                w, h = struct.unpack_from('<II', d, t)
                R(t, 8 + w * h, 'texture')
                part['textures'].append((t, w, h))
            ns = d[spr]
            R(spr, 1 + 4 * ns, 'sprites')
            part['nframes'] = pnf
            part['npaths'] = npath
            m.parts.append(part)
        # coverage / gaps
        cov = bytearray(len(d))
        for o, n, k in regs:
            for i in range(o, min(o + n, len(d))):
                cov[i] = 1
        gaps = []
        i = 0
        while i < len(d):
            if not cov[i]:
                j = i
                while j < len(d) and not cov[j]:
                    j += 1
                gaps.append((i, j - i, 'unreferenced'))
                i = j
            else:
                i += 1
        m.regions = [(o, n, k, d[o:o + n]) for o, n, k in regs + gaps]
        return m

    def build(self):
        out = bytearray(self.size)
        for o, n, k, b in self.regions:
            out[o:o + n] = b
        return bytes(out)


# ---------------- LDL (opaque) ----------------

class Opaque(object):
    """Raw passthrough (encrypted .ldl language files: fixed-keystream XOR, key not recovered;
    not referenced by deep6.exe)."""
    @classmethod
    def parse(cls, data):
        o = cls()
        o.raw = bytes(data)
        return o

    def build(self):
        return self.raw


# ---------------------------------------------------------------------------
# registry / self test
# ---------------------------------------------------------------------------

def spoke_files():
    out = []
    for s in range(13):
        out += [('D6Trig%02d.dat' % s, TriggerTable), ('D6Boun%02d.dat' % s, BoundTable),
                ('D6Spec%02d.dat' % s, SpecialTable), ('D6Swit%02d.dat' % s, SwitchTable),
                ('D6Link%02d.dat' % s, LinkTable), ('D6Trap%02d.dat' % s, TrapTable)]
    return out


GLOBAL_FILES = [
    ('D6MONS.DAT', MonsTable), ('D6ITEM.DAT', ItemTable), ('D6PROP.DAT', PropTable),
    ('D6NPC.DAT', NpcTable), ('D6TREAS.DAT', TreasTable), ('D6TRLIST.DAT', TrListTable),
    ('D6HELM.DAT', Helm), ('D6SMIT00.DAT', SmitInv), ('EVENTS.DCL', EventDCL),
    ('EVENTS.COD', EventCode), ('D6STRING.DAT', StringTable), ('TEXTPAK.000', TextPak),
    ('GMDATA.PAK', Pak), ('NPCDATA.PAK', Pak), ('D6ARCHIV.DAT', Archive),
    ('emitters.dat', EmitterTable), ('D6MONSND.DAT', MonSndTable), ('MONSOUND.DAT', SfxList),
    ('language.ldl', Opaque), ('string.ldl', Opaque),
    ('string_e.ldl', Opaque), ('default.ldl', Opaque),
]


def check_fields():
    """Every Record subclass: fields inside SIZE and not overlapping."""
    bad = []
    for cls in Record.__subclasses__():
        spans = []
        for f in cls.FIELDS:
            n = int(f.fmt[3:]) if f.fmt.startswith('str') else struct.calcsize('<' + f.fmt)
            spans.append((f.off, f.off + n, f.name))
        spans.sort()
        for (a0, a1, an), (b0, b1, bn) in zip(spans, spans[1:]):
            if b0 < a1:
                bad.append('%s: %s overlaps %s' % (cls.__name__, an, bn))
        if spans and cls.SIZE and spans[-1][1] > cls.SIZE:
            bad.append('%s: %s beyond SIZE' % (cls.__name__, spans[-1][2]))
    return bad


def selftest(root):
    results = []
    ov = check_fields()
    for x in ov:
        print('  FIELD LAYOUT: ' + x)
    assert not ov, 'field layout errors'
    files = spoke_files() + GLOBAL_FILES
    for e in sorted(os.listdir(root)):
        if e.upper().startswith('ENTR') and e.upper().endswith('.DAT'):
            files.append((e, EntryList))
        if e.upper().endswith(('.TOL', '.BOL', '.FOL')):
            files.append((e, ObjList))
    mdir = find_file(root, 'models')
    if mdir:
        for e in sorted(os.listdir(mdir)):
            if e.lower().endswith('.lst'):
                files.append(('models/' + e, ModelList))
        for dp, dn, fn in os.walk(mdir):
            for f in sorted(fn):
                if f.lower().endswith('.mdl'):
                    files.append((os.path.relpath(os.path.join(dp, f), root), Mdl))
    ok = bad = 0
    per_cls = {}
    extra = []
    for name, cls in files:
        data = read_file(root, name)
        if data is None:
            results.append((name, 'MISSING'))
            continue
        try:
            obj = cls.parse(data)
            out = obj.build()
            good = (out == data)
        except Exception as ex:
            good = False
            results.append((name, 'EXC %r' % ex))
        k = cls.__name__
        per_cls.setdefault(k, [0, 0])
        if good:
            ok += 1
            per_cls[k][0] += 1
        else:
            bad += 1
            per_cls[k][1] += 1
            results.append((name, 'MISMATCH'))
        if good and cls in (StringTable, TextPak):
            rb = obj.build(rebuild=True) == data
            extra.append('%s: full re-layout from parsed strings is %s' % (name, 'identical' if rb else 'DIFFERENT'))
        if good and cls is SmitInv:
            extra.append('%s: checksum %s' % (name, 'valid' if obj.checksum_ok() else 'INVALID'))
    print('d6data self test on %s' % root)
    for k in sorted(per_cls):
        print('  %-14s ok=%-4d fail=%d' % (k, per_cls[k][0], per_cls[k][1]))
    for x in extra:
        print('  ' + x)
    for n, r in results:
        print('  %s: %s' % (n, r))
    print('TOTAL ok=%d fail=%d' % (ok, bad))
    assert bad == 0, 'round trip failures'
    return ok, bad


# ---------------------------------------------------------------------------
# exe-side name tables (optional, for dumps)
# ---------------------------------------------------------------------------

def exe_model_tables(root):
    """Item (_ItemMDLData 0x5db3b8) and prop (0x5e27d4+1) model name tables, stride 0x51."""
    p = find_file(root, 'deep6.exe')
    if not p:
        return {}, {}
    try:
        import pefile
        pe = pefile.PE(p, fast_load=True)
        img = pe.get_memory_mapped_image()
    except Exception:
        return {}, {}

    def tab(va, n):
        out = {}
        for i in range(n):
            o = va - 0x400000 + i * 0x51
            out[i] = cstr(img[o:o + 0x40])
        return out
    return tab(0x5DB3B8, 240), tab(0x5E27D5, 250)


# Compiled model tables in deep6.exe (mdldata.c). Entry = char name[0x50] + u8 first
# _MDLExtend index (chain, 0 = none) [+ monster only: i32 Model_Read_ parameter at +0x51].
EXE_MODEL_TABLES = {
    'item': (0x5DB3B8, 240, 0x51),     # LoadItemObject_ etc.: index must be <= 0xEF
    'mon': (0x5DFFA8, 121, 0x55),      # LoadMonsterModel_: 0..16 PC bodies, 0x77 polymorph, 0x78 special
    'prop': (0x5E27D5, 250, 0x51),     # LoadPropObject_: index must be <= 0xF9
}
EXE_MDLEXTEND = (0x5E76F0, 64, 0x54)  # name[0x50], u8 kind (0 alpha-attach/1 anim-tex/2 alpha chain), u8 next, i16 param


def exe_model_table(root, kind):
    """[(index, name, extend, param)] for kind 'item' / 'mon' / 'prop'."""
    p = find_file(root, 'deep6.exe')
    if not p:
        return []
    try:
        import pefile
        pe = pefile.PE(p, fast_load=True)
        img = pe.get_memory_mapped_image()
    except Exception:
        return []
    va, n, stride = EXE_MODEL_TABLES[kind]
    out = []
    for i in range(n):
        o = va - 0x400000 + i * stride
        param = struct.unpack_from('<i', img, o + 0x51)[0] if stride == 0x55 else None
        out.append((i, cstr(img[o:o + 0x50]), img[o + 0x50], param))
    return out


# ---------------------------------------------------------------------------
# area dump
# ---------------------------------------------------------------------------

def dump_area(root, spoke, out=sys.stdout):
    def W(s=''):
        out.write(''.join(c if 32 <= ord(c) < 127 else '?' for c in s) + '\n')
    dcl = EventDCL.parse(read_file(root, 'EVENTS.DCL'))
    cod = EventCode.parse(read_file(root, 'EVENTS.COD'))
    mons = MonsTable.parse(read_file(root, 'D6MONS.DAT'))
    items = ItemTable.parse(read_file(root, 'D6ITEM.DAT'))
    props = PropTable.parse(read_file(root, 'D6PROP.DAT'))
    treas = TreasTable.parse(read_file(root, 'D6TREAS.DAT'))
    tpak = TextPak.parse(read_file(root, 'TEXTPAK.000'))
    itemmdl, propmdl = exe_model_tables(root)
    trig = TriggerTable.parse(read_file(root, 'D6Trig%02d.dat' % spoke))
    boun = BoundTable.parse(read_file(root, 'D6Boun%02d.dat' % spoke))
    spec = SpecialTable.parse(read_file(root, 'D6Spec%02d.dat' % spoke))
    swit = SwitchTable.parse(read_file(root, 'D6Swit%02d.dat' % spoke))
    link = LinkTable.parse(read_file(root, 'D6Link%02d.dat' % spoke))
    trap = TrapTable.parse(read_file(root, 'D6Trap%02d.dat' % spoke))

    def mname(i):
        return mons[i].name if i is not None and 1 <= i <= len(mons) else '?'

    def iname(i):
        return items[i].name if i is not None and 1 <= i <= len(items) else '?'

    def pname(i):
        return props[i].name if i is not None and 1 <= i <= len(props) else '?'

    # object lists
    objlists = {}
    bspnames = {}
    if spoke in TERRAIN_SPOKES:
        tol = ObjList.parse(read_file(root, TERRAIN_SPOKES[spoke]))
        objlists[None] = (TERRAIN_SPOKES[spoke], tol)
        for r in tol.records:
            if r.kind == 'B':
                # BSP origin = record pos - signed tile offsets (+0x21..0x23) * 1024 (mapobj.c)
                org = [r.pos[i] - r.b21[i] * 1024.0 for i in range(3)]
                bspnames[r.b20] = (r.bspname, org)
        for b, (nm, pos) in bspnames.items():
            d = read_file(root, nm + '.bol')
            if d:
                objlists[b] = (nm + '.bol', ObjList.parse(d))
    else:
        for b, nm in SPOKE_BSPS.get(spoke, {}).items():
            d = read_file(root, nm)
            if d:
                objlists[b] = (nm, ObjList.parse(d))
                bspnames[b] = (nm[:-4], None)

    def objdesc(objid):
        if objid == 0:
            return '0'
        b, k = split_objid(objid)
        if b not in objlists:
            return '%d(bsp%s#%d ?)' % (objid, b, k)
        fn, ol = objlists[b]
        if not (1 <= k <= len(ol)):
            return '%d(%s#%d out of range)' % (objid, fn, k)
        r = ol[k]
        n = r.recno
        nm = {'M': mname, 'I': iname, 'P': pname}.get(r.kind, lambda x: '')(n) if n is not None else ''
        return '%d(%s#%d %s%s "%s")' % (objid, fn, k, r.kind, '' if n is None else '%03d' % n, nm)

    W('=' * 78)
    W('AREA DUMP  spoke %02d  -  %s' % (spoke, SPOKE_NAMES.get(spoke, '')))
    W('=' * 78)
    W('Coordinates: world units, x/z horizontal, y up. Terrain tile = 1024 units.')
    W('Object ids: <100000 = record in terrain list; n*100000+k = record k of BSP n (16 = BSP 0).')
    W('')
    W('BSP list:')
    for b in sorted(bspnames, key=lambda x: -1 if x is None else x):
        nm, pos = bspnames[b]
        W('  bsp %2s  %-10s %s' % (b, nm, '' if pos is None else 'origin %.0f,%.0f,%.0f (BSP-relative coords add this)' % tuple(pos)))
    W('')

    # placed objects
    for b in sorted(objlists, key=lambda x: -1 if x is None else x):
        fn, ol = objlists[b]
        W('-' * 78)
        W('OBJECT LIST %s (bsp %s): %d records' % (fn, b, len(ol)))
        for i, r in enumerate(ol.records, 1):
            k = r.kind
            n = r.recno
            if n is None:
                n = -1
            pos = r.pos
            if k == 'M':
                W('  #%-3d M%03d %-22s pos %9.0f %7.0f %9.0f  flags 0x%02x' % (i, n, mname(n), pos[0], pos[1], pos[2], r.b20))
            elif k == 'I':
                W('  #%-3d I%03d %-22s pos %9.0f %7.0f %9.0f' % (i, n, iname(n), pos[0], pos[1], pos[2]))
            elif k == 'P':
                W('  #%-3d P%03d %-22s pos %9.0f %7.0f %9.0f  rot %s' % (i, n, pname(n), pos[0], pos[1], pos[2],
                                                                          ','.join('%g' % v for v in r.rot)))
            elif k == 'B':
                W('  #%-3d BSP %-10s bsp#%d pos %9.0f %7.0f %9.0f' % (i, r.bspname, r.b20, pos[0], pos[1], pos[2]))
            else:
                W('  #%-3d %r (empty)' % (i, bytes(r.raw[:4])))
    W('')

    # monsters near the default entry point
    if None in objlists:
        ex, ez = {0: (217600.0, 83456.0), 3: (7954.0, 284160.0), 11: (382976.0, 25600.0)}[spoke]
        W('-' * 78)
        W('Monsters nearest to the default entry point (%.0f, %.0f):' % (ex, ez))
        lst = []
        for i, r in enumerate(objlists[None][1].records, 1):
            if r.kind == 'M':
                p = r.pos
                lst.append((((p[0] - ex) ** 2 + (p[2] - ez) ** 2) ** 0.5, i, r))
        for dist, i, r in sorted(lst)[:15]:
            W('  %7.0f units (%5.1f tiles)  #%-3d %s' % (dist, dist / 1024, i, mname(r.recno)))
        W('')

    W('-' * 78)
    W('TRIGGERS (%d)  D6Trig%02d.dat' % (len(trig), spoke))
    for i, t in enumerate(trig.records, 1):
        ev = t.event
        ei = dcl.index(ev)
        pn = dcl.params_of(ei) if ei >= 0 else []
        np_ = len(pn)
        ps = t.params[:max(np_, 1)]
        W('  T%-3d %-28s en=%d oneshot=%d state=%d mode=%d  %s' % (i, ev, t.enabled, t.oneshot, t.state, t.mode,
                                                                 dcl.desc(ei) if ei >= 0 else '(unknown event)'))
        parts = []
        for k in range(np_):
            nm = pn[k]
            v = ps[k]
            s = '%s=%d' % (nm, v)
            u = nm.lstrip('!')
            if u in ('MONREC', 'MONA', 'MONB', 'MONC', 'MOND') and u == 'MONREC' and v > 0:
                s += '("%s")' % mname(v)
            elif u in ('MSG',) and tpak.text(v):
                s += '("%s")' % tpak.text(v)[:60].replace('@', ' / ')
            elif u in ('ITEM', 'USEITEM', 'XITEM') and v > 0:
                s += '("%s")' % iname(v)
            elif u in ('DOOR', 'DOORA', 'DOORB') and v >= 0:
                b = v // 100000
                s += '[bsp %d %s entity %d]' % (b, bspnames.get(b, ('?',))[0], v % 100000)
            elif u in ('OBJNUM', 'PROP', 'MONA', 'MONB', 'MONC', 'MOND', 'PROPIDA', 'PROPIDB', 'PROPIDC',
                       'PROPIDD', 'OBJECT', 'OBJ') and v > 0:
                s += '[%s]' % objdesc(v)
            parts.append(s)
        if parts:
            W('        ' + '  '.join(parts))
    W('')
    W('-' * 78)
    W('BOUND AREAS (%d)  D6Boun%02d.dat' % (len(boun), spoke))
    who = {0: 'PCs', 1: 'party', 2: 'creatures', 3: 'non-party', 4: 'object', 5: 'all'}
    for i, b in enumerate(boun.records, 1):
        mn, mx = b.min, b.max
        tg = 'trigger T%d (%s)' % (b.trigger, trig[b.trigger].event) if 0 < b.trigger <= len(trig) else 'state %d' % b.state
        W('  B%-3d en=%d %-9s oneshot=%d bsp=%-2d  box (%.0f,%.0f,%.0f)-(%.0f,%.0f,%.0f) -> %s%s' % (
            i, b.enabled, who.get(b.who, b.who), b.oneshot, b.bsp, mn[0], mn[1], mn[2], mx[0], mx[1], mx[2], tg,
            ' obj %s' % objdesc(b.objid) if b.who == 4 else ''))
        org = bspnames.get(b.bsp, (None, None))[1] if b.bsp >= 0 else None
        if org is not None:
            W('        world box x %.0f..%.0f  z %.0f..%.0f' % (mn[0] + org[0], mx[0] + org[0], mn[2] + org[2], mx[2] + org[2]))
    W('')
    W('-' * 78)
    W('SPECIALS (%d)  D6Spec%02d.dat' % (len(spec), spoke))
    for i, s in enumerate(spec.records, 1):
        a = s.args
        if s.type == 2:
            det = 'all dead: ' + ', '.join(objdesc(x) for x in a if x > 0)
        elif s.type == 3:
            det = 'party has item %d ("%s")' % (a[0], iname(a[0]))
        elif s.type == 1:
            det = 'clock in [%d,%d)' % (a[0], a[1])
        else:
            det = 'type %d %s' % (s.type, a)
        tg = 'trigger T%d' % s.trigger if s.trigger > 0 else 'state %d' % s.state
        W('  S%-3d en=%d oneshot=%d %s -> %s' % (i, s.enabled, s.oneshot, det, tg))
    W('')
    W('-' * 78)
    W('SWITCHES (%d)  D6Swit%02d.dat' % (len(swit), spoke))
    for i, s in enumerate(swit.records, 1):
        tgt = 'state %d' % s.target if s.target >= 0 else 'trigger T%d (%s)' % (-s.target, trig[-s.target].event if -s.target <= len(trig) else '?')
        o = ('entity bsp%d#%d' % (s.objid // 100000, s.objid % 100000)) if s.isentity else objdesc(s.objid)
        W('  W%-3d %s en=%d on=%d flags=0x%02x key=%d sfx=%d link=%d -> %s' % (
            i, o, s.enabled, s.on, s.flags, s.keyitem, s.sfx, s.link, tgt))
    W('')
    W('-' * 78)
    W('TRAPS / LOCKED CONTAINERS (%d)  D6Trap%02d.dat' % (len(trap), spoke))
    for i, t in enumerate(trap.records, 1):
        W('  X%-3d %s locked=%d trapmask=0x%04x diff=%d power=%d treasure=%d monster=%d@%d%%' % (
            i, objdesc(t.objid), t.locked, t.trapmask, t.difficulty, t.power, t.treasure, t.monrec, t.monpct))
    W('')
    W('-' * 78)
    W('NAV LINKS (%d)  D6Link%02d.dat' % (len(link), spoke))
    for i, l in enumerate(link.records, 1):
        W('  L%-3d nav %d (bsp %d) <-> nav %d (bsp %d)' % (i, l.navA, l.bspA, l.navB, l.bspB))
    W('')
    for e in sorted(os.listdir(root)):
        if e.upper().startswith('ENTR%02d' % spoke) and e.upper().endswith('.DAT'):
            el = EntryList.parse(read_file(root, e))
            W('-' * 78)
            W('ENTRY LIST %s' % e)
            for xyz, nm in el.entries:
                W('  %-30s %s' % (nm, xyz))
    W('')
    W('-' * 78)
    W('EVENT CODE used by this spoke:')
    used = sorted(set(dcl.index(t.event) for t in trig.records if dcl.index(t.event) >= 0))
    for ei in used:
        W('%s  ; %s  params(%s)' % (dcl.name(ei), dcl.desc(ei), ', '.join(dcl.params_of(ei))))
        for l in cod.disasm(dcl, ei):
            W(l)


def disasm_all(root):
    dcl = EventDCL.parse(read_file(root, 'EVENTS.DCL'))
    cod = EventCode.parse(read_file(root, 'EVENTS.COD'))
    for i in range(dcl.n):
        print('%3d %s  ; %s' % (i, dcl.name(i), dcl.desc(i)))
        print('     params: %s' % ', '.join(dcl.params_of(i)))
        for l in cod.disasm(dcl, i):
            print(l)


def extract(root, name, outdir):
    data = read_file(root, name)
    os.makedirs(outdir, exist_ok=True)
    if name.upper().endswith('.PAK'):
        p = Pak.parse(data)
        for i, o, s in p.members():
            with open(os.path.join(outdir, '%03d_%s.bin' % (i, p.slot_name(i))), 'wb') as f:
                f.write(p.member(i))
    elif name.upper() == 'D6ARCHIV.DAT':
        a = Archive.parse(data)
        for nm, o, s in a.members():
            with open(os.path.join(outdir, nm), 'wb') as f:
                f.write(data[o:o + s])
    else:
        raise SystemExit('not an archive: %s' % name)


def main(argv):
    if len(argv) >= 3 and argv[1] == '--selftest':
        selftest(argv[2])
    elif len(argv) >= 4 and argv[1] == '--dump-area':
        dump_area(argv[3], int(argv[2]))
    elif len(argv) >= 3 and argv[1] == '--disasm':
        disasm_all(argv[2])
    elif len(argv) >= 5 and argv[1] == '--extract':
        extract(argv[2], argv[3], argv[4])
    elif len(argv) >= 4 and argv[1] == '--list':
        data = read_file(argv[2], argv[3])
        obj = Pak.parse(data) if argv[3].upper().endswith('.PAK') else Archive.parse(data)
        for m in obj.members():
            if isinstance(obj, Pak):
                print('%3d %-14s off=%-8d size=%d' % (m[0], obj.slot_name(m[0]), m[1], m[2]))
            else:
                print('%-16s off=%-8d size=%d' % m)
    else:
        print(__doc__)
        print('usage: d6data.py --selftest GAMEDIR | --dump-area N GAMEDIR | --disasm GAMEDIR |'
              ' --list GAMEDIR FILE | --extract GAMEDIR FILE OUTDIR')


if __name__ == '__main__':
    main(sys.argv)
