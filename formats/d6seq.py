#!/usr/bin/env python3
"""d6seq - reader/writer for Wizards & Warriors (Deep6) PC animation keep-lists
(models/pc/*.seq).

A .seq file is a plain-text list of animation ids (indices into the 256-entry
animation table of the matching .mdl).  The offline tool pcreduce.exe
(models/pc/MAKEPCR*.BAT) reads <model>.seq and strips from <model>.mdl every
frame that does not belong to one of the listed animations, writing the
per-frame "framebits" table (mdl version bit 0x1000000).  deep6.exe never opens
.seq files; at runtime the same id set is hard-coded in SetModelReadDefaults_
(asets.c: _gDataSetFlag[id] = 2 for ids 0, 47, 50, 91, 110) and Model_Read_
loads only those frames for PC models (load type 2).

Retail layout (all 33 files are byte-identical copies of hm.seq, makeseq.bat):
    b"0,\r\n47,\r\n50,\r\n91,\r\n110,\r\n\r\n"
i.e. one decimal id per line, each followed by ',', CRLF line ends, and an
empty line as terminator.

parse(bytes) -> Seq; Seq.to_bytes() reproduces the input byte-identically.
Lines that are not '<int><sep>' are kept verbatim so odd files still round-trip.
"""
import os
import re
import sys

ANIM_NAMES = {
    0: 'READY (idle stand)', 1: 'READYALT', 2: 'READYLOOK', 5: 'WALKINIT', 6: 'WALKLOOP',
    7: 'WALKEND', 8: 'SITDOWN? (8/9 = same frames fwd/back)', 9: 'STANDUP? (reverse of 8)',
    10: 'RUNINIT', 11: 'RUNLOOP', 12: 'RUNEND', 15: 'LEFTTURN', 16: 'RIGHTTURN',
    17: 'BACKSTEP', 20: 'WOUNDSM', 21: 'WOUNDLG', 25: 'DEATH', 26: 'DEADDOWN',
    28: 'SLEEP', 29: 'SLEEPDOWN', 30: 'FLYINIT (take off)', 31: 'FLYLOOP',
    32: 'FLYEND (landing)', 33: 'FLYREADY (hover idle)', 34: 'SNEAKINIT', 35: 'SNEAKLOOP',
    36: 'SNEAKEND', 40: 'CLIMBINIT', 47: 'COMBAT READY (fight stance)',
    49: 'SWLFTSWING', 50: 'SWORDSWING', 51: 'SWORDCHOP', 52: 'SWORDJAB',
    54: 'SWORDBLOCK', 57: 'POLECHOP', 59: 'POLEJAB', 61: '2HCHOP', 63: 'BACKHAND',
    68: 'DRAWSWORD', 69: 'HOLTSWORD', 70: 'LONGBOW', 75: 'CROSSBOW', 80: 'THROW',
    85: 'BLOCKINIT', 90: 'PUNCH', 91: 'PUNCHDOUBLE', 93: 'KICK', 94: 'KICKFLY',
    96: 'PUNCHLEFT', 100: 'SPELLA', 101: 'SPELLB', 102: 'SPELLC', 103: 'SPELLD',
    104: 'POWDER', 105: 'USELEFT', 106: 'SCROLL', 109: 'SPELLLEFT', 110: 'USEITEM',
    115: 'GAZEINIT', 119: 'HEALME', 120: 'BITEINIT', 121: 'BITELOOP', 122: 'BITEEND',
    125: 'CLAW', 126: 'CLAWLEFT', 127: 'STING', 129: 'HEADBUTT', 130: 'MODE1TO2',
    131: 'MODE2TO1', 135: 'M2READY', 136: 'M2BLOCK', 140: 'M2WOUNDSM', 141: 'M2WOUNDLG',
    142: 'M2DEATH', 143: 'M2DEADDOWN', 145: 'M2BITE', 146: 'M2ATTACK', 147: 'M2SPELL',
    150: 'BREATHINIT', 153: 'BREATH2', 159: 'LAND (reverse of JUMP)', 160: 'JUMP',
    162: 'JUMPRUN', 165: 'FLYDIVE', 166: 'FLYUP', 168: 'LAND hold (1 frame of 159)',
    170: 'STEAL', 171: 'KNEEL/DISARM init', 172: 'KNEEL loop (split of 171)',
    173: 'KNEEL end (split of 171)', 175: 'M2BREATH', 180: 'M2THROW',
    181: 'HORSEREADY', 182: 'HORSESWING', 183: 'HORSETHROW', 184: 'HORSELBOW',
    185: 'HORSEXBOW', 186: 'HORSEWOUND', 187: 'HORSEDEATH', 188: 'HORSEJUMP',
    190: 'HORSESPELL', 191: 'HORSEUSE', 192: 'HORSEMISC', 193: 'HORSEWALK',
    194: 'HORSERUN', 195: 'SWIMREADY', 196: 'SWIMWALK', 197: 'SWIMRUN',
    200: 'VICTORY', 205: 'SCOUT', 210: 'SPECIAL1', 211: 'SPECIAL2',
    220: 'H2BREATHINIT', 225: 'H3BREATHINIT', 230: 'TALKA', 231: 'TALKB', 232: 'TALKC',
    233: 'STIRPOTINIT', 234: 'STIRPOTLOOP', 235: 'STIRPOTEND', 250: 'ENTRY', 251: 'EXIT',
    252: 'CLONE',
}

_LINE = re.compile(rb'^[ \t]*(-?\d+)[ \t]*(,?)[ \t]*$')


class Seq:
    """entries: list of ('id', int, raw_line_bytes) or ('raw', None, raw_line_bytes).
    eol: line terminator (b'\\r\\n' in retail); final: True if the data ends with eol."""

    def __init__(self, ids=(), eol=b'\r\n', terminator=True):
        self.entries = [('id', int(i), None) for i in ids]
        self.eol = eol
        self.final = True
        if terminator:
            self.entries.append(('raw', None, b''))

    @property
    def ids(self):
        return [v for k, v, _ in self.entries if k == 'id']

    def to_bytes(self):
        out = []
        for kind, val, raw in self.entries:
            if kind == 'id' and raw is None:
                raw = b'%d,' % val
            out.append(raw)
        data = self.eol.join(out)
        if self.final:
            data += self.eol
        return data

    def frames(self, anims):
        """Frame numbers kept for an mdl anim table [(first,last,event)]*256."""
        keep = set()
        for i in self.ids:
            a, b = anims[i][0], anims[i][1]
            if a or b:
                keep.update(range(min(a, b), max(a, b) + 1))
        return sorted(keep)


def parse(data):
    if isinstance(data, str):
        data = data.encode('latin1')
    eol = b'\r\n' if b'\r\n' in data or not data else b'\n'
    s = Seq(terminator=False)
    s.eol = eol
    s.final = data.endswith(eol)
    body = data[:-len(eol)] if s.final else data
    for line in body.split(eol) if body or s.final else []:
        m = _LINE.match(line)
        if m:
            v = int(m.group(1))
            canon = b'%d,' % v
            s.entries.append(('id', v, None if line == canon else line))
        else:
            s.entries.append(('raw', None, line))
    return s


def load(path):
    with open(path, 'rb') as f:
        return parse(f.read())


def _main(argv):
    if len(argv) < 2:
        print('usage: d6seq.py FILE.seq [...] | --verify DIR | --frames FILE.seq FILE.mdl')
        return 2
    if argv[1] == '--verify':
        import glob, os
        files = [p for p in glob.glob(os.path.join(argv[2], '**', '*'), recursive=True)
                 if p.lower().endswith('.seq')]
        bad = 0
        for p in sorted(files):
            d = open(p, 'rb').read()
            if parse(d).to_bytes() != d:
                bad += 1
                print('MISMATCH', p)
        print('%d .seq files, %d round-trip byte-identical, %d mismatches'
              % (len(files), len(files) - bad, bad))
        return 1 if bad else 0
    if argv[1] == '--frames':
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import d6model
        s = load(argv[2])
        mp = os.path.abspath(argv[3])
        gd = mp[:mp.lower().rindex(os.sep + 'models' + os.sep)]
        m = d6model.load_model(gd, os.path.relpath(mp, os.path.join(gd, 'models')))
        fr = s.frames(m.anims)
        stored = [i for i in range(m.nframes) if m.framebits is None or m.framebits[i]]
        print('%d frames kept by seq, %d stored in mdl, equal=%s' % (len(fr), len(stored), fr == stored))
        return 0
    for p in argv[1:]:
        s = load(p)
        print('%s: %d ids, eol=%r' % (p, len(s.ids), s.eol))
        for i in s.ids:
            print('  %3d  %s' % (i, ANIM_NAMES.get(i, '?')))
        for k, _, raw in s.entries:
            if k == 'raw' and raw:
                print('  (raw line) %r' % raw)
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv))
