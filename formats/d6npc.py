#!/usr/bin/env python3
"""
d6npc.py - NPC and guildmaster dialogue scripts (NPCDATA.PAK, GMDATA.PAK).

Every talking NPC n (D6NPC record / monster field 'npc', 0..159) has three
members in NPCDATA.PAK: slot 1+n bytecode, 161+n strings, 321+n keywords
(responses). Guildmasters (GMDATA.PAK, 32 slots each) work the same way but
run a separate interpreter (guild.c GMExecuteCode_) with renumbered opcodes.
Slot 0 of NPCDATA.PAK is the global lexicon (u32 count, u32 string base,
count x {u8 type, u32 offset}).

Bytecode (npc.c ExecuteCode_, reverse engineered; all 137 NPC and 21 GM
scripts decode exactly): one opcode byte, little endian operands, absolute
u16 addresses. Execution starts at 0 on every activation; scripts begin with
"IF NPCFLAG(0) == 1 / JUMP init / JUMP main". IF / IFKEY skip the next 3
bytes when true (always a JUMP): "IF cond / JUMP then / JUMP else" reads
naturally. SAY, REPLY, WAIT, BYE, LEAVE yield until the line is shown, the
player answers or the time is up; REPLY is always followed by ONREPLY with
one address per answer. ONACT slot, addr registers handlers for game events
(0 attacked, 1 PC leaves, 2 used/fought, 3 steal, 4 item given, 7 gold
given, 9 talk/keyword, 11 all PCs gone). See docs/formats/npc.md.

Text form (disassemble/assemble):

    .strings
    s0 "Sssstranger, a word with you!"
    .keywords
    k0 "Hail"
    .code
            IF NPCFLAG(0) == 1
            JUMP init
            JUMP main
    init:   ONACT 9, talk
            SAY s0                ; or SAY "new text" (appended to .strings)
            REPLY s4, [s5, s6]    ; options must be consecutive strings
            ONREPLY [yes, no]
            IFKEY k0              ; or IFKEY "Keyword"
            MODPC -1, HP, 10

Values (val) are a number or an expression NAME(args); see EXPR below.
Strings: '$N' = PC name, '@' splits a message, '~word~' highlights a keyword.

CLI
  d6npc.py GAMEDIR N [--gm]                 print NPC N's script
  d6npc.py --selftest GAMEDIR               disassemble + reassemble everything, byte exact
  d6npc.py --export GAMEDIR DIR / --import GAMEDIR DIR   all scripts as text files
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import d6data  # noqa: E402

# ---- EvalExpr_ kinds: kind -> (name, operand bytes) -------------------------------
EXPR = {
    0x00: ('NPCFLAG', 'i16 flag'),
    0x01: ('RANDOM', 'i16 n'),
    0x02: ('STAT18', 'i16 who'),
    0x03: ('STAT1A', 'i16 who'),
    0x04: ('STAT1C', 'i16 who'),
    0x05: ('STATF2', 'i16 who'),
    0x06: ('STATE', 'i16 who'),
    0x07: ('NPCTIMER', 'i16 npc'),
    0x08: ('ZERO4', 'i16 a,i16 b'),
    0x09: ('PCREG', 'i16 bit'),
    0x0a: ('WSTATE', 'i16 spoke,i16 idx'),
    0x0b: ('HASITEM', 'i16 item'),
    0x0c: ('NOITEM', 'i16 item'),
    0x0d: ('GOLD', 'i16 who'),
    0x0e: ('ACTITEM', ''),
    0x0f: ('ACTGOLD', ''),
    0x10: ('PCREGOF', 'i16 who,i16 bit'),
    0x11: ('ATTITUDE', ''),
    0x12: ('ATTITUDEOF', 'i16 who'),
    0x13: ('ATHOME', ''),
    0x14: ('INTALK', 'i16 who'),
    0x15: ('FIELD476', ''),
    0x16: ('TARGET', ''),
    0x17: ('TARGETISPC', ''),
    0x18: ('TARGETDIST', ''),
    0x19: ('HPLOST', 'i16 who'),
    0x1a: ('HP', 'i16 who'),
    0x1b: ('MAXHP', 'i16 who'),
    0x1c: ('INRANGE', 'i16 who'),
    0x1d: ('NPCBIT4', ''),
    0x1e: ('NPCHASITEM', 'i16 item'),
    0x1f: ('PARTYHASITEM', 'i16 item'),
    0x20: ('NPCREG', 'i16 npc,i16 bit'),
    0x21: ('QFLAG', 'i16 who,i16 q'),
    0x22: ('PARTYQFLAG', 'i16 q,i16 val'),
    0x23: ('HASTRAIT', 'i16 who,i16 trait'),
    0x24: ('MBIT', 'i16 bit'),
}

# ---- ExecuteCode_ opcodes: op -> (mnemonic, operand spec) ---------------------------
# spec tokens: u8, i16, str (i16 strBuff idx), rsp (i16 rspBuff idx), addr (i16 abs offset),
# expr (u8 kind + EvalExpr_ operands), val (u8 dt: 0 -> i16 const, 1 -> expr), cmp (u8 cmpop)
OPS = {
    0x01: ('END', []),
    0x03: ('IF', ['expr', 'cmp', 'val']),          # true -> skip 3 (the following JUMP)
    0x04: ('IFNOT', ['expr', 'cmp', 'val']),
    0x07: ('JUMP', ['addr']),
    0x08: ('SAY', ['str']),                         # yields
    0x09: ('REPLY', ['reply']),                     # u8 n, str, str[n]; yields; resumes after
    0x0a: ('ONREPLY', ['table']),                   # u8 n, addr[n]; jump to [reply]
    0x0b: ('SETFLAG', ['i16', 'val']),
    0x0c: ('GIVEITEM', ['i16']),
    0x0d: ('TAKEITEM', ['i16']),
    0x0e: ('GIVEGOLD', ['i16']),
    0x0f: ('TAKEGOLD', ['i16']),
    0x10: ('NOP4', ['i16', 'i16']),
    0x11: ('NYI11', ['i16']),
    0x12: ('NYI12', []),
    0x13: ('NOP13', ['i16']),
    0x14: ('ATTEND', []),
    0x15: ('BYE', []),                              # yields
    0x16: ('NOP', []),
    0x17: ('NOP17', ['i16']),
    0x18: ('ONACT', ['u8', 'addr']),
    0x19: ('NOP19', ['i16', 'i16', 'i16']),
    0x1a: ('SETPCREG', ['i16', 'i16']),
    0x1b: ('SETWSTATE', ['i16', 'i16', 'i16']),
    0x1c: ('MODPC', ['i16', 'i16', 'val']),
    0x1d: ('SETPCREGOF', ['i16', 'i16', 'i16']),
    0x1e: ('ADDATTITUDE', ['val']),
    0x1f: ('ADDATTITUDEOF', ['i16', 'val']),
    0x20: ('IFKEY', ['rsp']),                       # match -> skip 3
    0x21: ('WAIT', ['i16']),                        # yields; resumes after delay
    0x22: ('LEAVE', ['i16']),                       # yields
    0x23: ('SETPARTYREG', ['i16', 'i16']),
    0x24: ('CLEARWAITS', []),
    0x25: ('GOTO', ['i16', 'i16']),
    0x26: ('SETMODE', ['i16']),
    0x27: ('SETBIT20', ['i16']),
    0x28: ('SETBUSY', ['i16']),
    0x29: ('CLEARTARGET', []),
    0x2a: ('CAST', ['i16']),
    0x2b: ('FIGHT', []),
    0x2c: ('FINDTARGET', ['i16', 'i16']),
    0x2d: ('ONEXPR', ['onexpr']),
    0x2f: ('ADDFLAG', ['i16', 'val']),
    0x30: ('SUBFLAG', ['i16', 'val']),
    0x31: ('GIVENPCITEM', ['i16']),
    0x32: ('SETBIT8', ['i16']),
    0x33: ('KILLMSGQ', []),
    0x34: ('SUICIDE', []),
    0x35: ('SETNPCREG', ['i16', 'i16', 'i16']),
    0x36: ('SETPARTYNPCREG', ['i16', 'i16', 'i16']),
    0x37: ('SETQFLAG', ['i16', 'i16', 'val']),
    0x38: ('ADDQFLAG', ['i16', 'i16', 'val']),
    0x39: ('SUBQFLAG', ['i16', 'i16', 'val']),
    0x3a: ('SETTRADE', ['i16']),
    0x3b: ('TRADE', []),
    0x3c: ('SETSELL', ['i16', 'i16']),
    0x3d: ('SETE8', ['i16']),
    0x3e: ('SETEC', ['i16']),
    0x3f: ('ADDPCLIST', []),
    0x40: ('SETGROUPREG', ['i16', 'i16']),
    0x41: ('SETPARTYREGIFQ', ['i16', 'i16', 'i16', 'i16']),
    0x42: ('TAKEPARTYITEM', ['i16', 'i16']),
    0x43: ('GIVEEXP', ['i16', 'val']),
    0x44: ('NOP44', ['i16', 'val']),
    0x45: ('SETBIT4', ['i16']),
    0x46: ('TAKEPARTYGOLD', ['i16']),
    0x47: ('SETMBIT', ['i16', 'i16']),
}

# ---- Guildmaster VM (guild.c GMExecuteCode_/GMEvalExpr_) -----------------------------
GMEXPR = {
    0x00: ('GMFLAG', 'i16 flag'), 0x01: ('RANDOM', 'i16 n'),
    0x02: ('GENDER', 'i16 who'), 0x03: ('CLAN', 'i16 who'),
    0x04: ('ROLE', 'i16 who'),          # who -1 is broken in the exe (reads PC[-1]): use 0..5
    0x05: ('ALIGNMENT', 'i16 who'),     # 0..100 (50 when who > 5)
    0x06: ('STATE', 'i16 who'), 0x07: ('NPCSTATE', 'i16 npc'),
    0x08: ('ZERO4', 'i16 a,i16 b'), 0x09: ('NPCREG', 'i16 npc,i16 bit'),
    0x0a: ('WSTATE', 'i16 spoke,i16 idx'), 0x0b: ('HASITEM', 'i16 item'),
    0x0c: ('NOITEM', 'i16 item'), 0x0d: ('GOLD', 'i16 who'), 0x0e: ('ACTITEM', ''),
    0x0f: ('ACTGOLD', ''), 0x10: ('NPCREGOF', 'i16 who,i16 npc,i16 bit'),
    0x11: ('ATTITUDE', 'i16 npc'), 0x12: ('ATTITUDEOF', 'i16 who,i16 npc'),
    0x13: ('ZERO13', ''), 0x14: ('ZERO14', ''), 0x15: ('LOCALHASITEM', 'i16 item'),
    0x16: ('ACTBUTTON', ''), 0x17: ('QFLAG', 'i16 who,i16 q'), 0x18: ('GUILDRANK', 'i16 guild'),
    0x19: ('ROLEINIT', 'i16 role'), 0x1a: ('GTIMER', 'i16 which'), 0x1b: ('QTY', 'i16 item'),
    0x1c: ('LOCALQFLAG', 'i16 q,i16 val'), 0x1d: ('LOCALQTY', 'i16 item'), 0x1e: ('UBIT', 'i16 bit'),
}
GMOPS = {
    0x01: ('END', []), 0x03: ('IF', ['expr', 'cmp', 'val']), 0x04: ('IFNOT', ['expr', 'cmp', 'val']),
    0x07: ('JUMP', ['addr']), 0x08: ('SAY', ['str']), 0x09: ('REPLY', ['reply']),
    0x0a: ('ONREPLY', ['table']), 0x0b: ('SETFLAG', ['i16', 'val']), 0x0c: ('GIVEITEM', ['i16']),
    0x0d: ('TAKEITEM', ['i16']), 0x0e: ('GIVEGOLD', ['i16']), 0x0f: ('TAKEGOLD', ['i16']),
    0x10: ('NOP10', ['i16', 'i16']), 0x11: ('ONACT', ['u8', 'addr']),
    0x12: ('NOP12', ['i16', 'i16', 'i16']), 0x13: ('SETNPCREG', ['i16', 'i16', 'i16']),
    0x14: ('SETWSTATE', ['i16', 'i16', 'i16']), 0x15: ('MODPC', ['i16', 'i16', 'val']),
    0x16: ('SETNPCREGOF', ['i16', 'i16', 'i16', 'i16']), 0x17: ('ADDATTITUDE', ['i16', 'val']),
    0x18: ('ADDATTITUDEOF', ['i16', 'i16', 'val']), 0x19: ('IFKEY', ['str']),
    0x1a: ('WAIT', ['i16']), 0x1b: ('SETLOCALREG', ['i16', 'i16', 'i16']),
    0x1c: ('CLEARWAITS', []), 0x1d: ('NOP1D', ['i16']), 0x1e: ('ONEXPR', ['onexpr']),
    0x20: ('ADDFLAG', ['i16', 'val']), 0x21: ('SUBFLAG', ['i16', 'val']), 0x22: ('PURGEMSGQ', []),
    0x23: ('SETQFLAG', ['i16', 'i16', 'val']), 0x24: ('ADDQFLAG', ['i16', 'i16', 'val']),
    0x25: ('SUBQFLAG', ['i16', 'i16', 'val']), 0x26: ('EXIT', []),
    0x27: ('SETGUILDRANK', ['i16', 'val']), 0x28: ('SETROLEINIT', ['i16', 'val']),
    0x29: ('SETROLE', ['val']), 0x2a: ('JOURNAL', ['str']), 0x2b: ('CLRJOURNALFLAGS', []),
    0x2c: ('TIMER0', ['i16']), 0x2d: ('TIMER1', ['i16']), 0x2e: ('TAKEQTY', ['i16', 'i16']),
    0x2f: ('JOURNALALL', ['i16']), 0x30: ('TAKELOCALITEM', ['i16', 'i16']),
    0x31: ('TAKELOCALQTY', ['i16', 'i16']), 0x32: ('ADDSHOPITEM', ['i16', 'i16']),
    0x33: ('GIVEEXP', ['i16', 'val']), 0x34: ('NOP34', ['i16', 'val']),
    0x35: ('SETUBIT', ['i16', 'i16']),
}
CMP = {0: '==', 1: '!=', 2: '<', 3: '>'}
MODPC = {1: 'HP', 2: 'MAXHP', 3: 'ABIL+', 4: 'ABIL-', 5: 'TRAIT+', 6: 'TRAIT-',
         7: 'SPELL+', 8: 'SPELL-', 9: 'SKILL+100', 10: 'POISON'}


MODPC_NAMES = dict((v, k) for k, v in MODPC.items())
# the guildmaster VM: kind 9 adds 10 (not 100), no POISON (kind 10 does nothing)
GM_MODPC = {1: 'HP', 2: 'MAXHP', 3: 'ABIL+', 4: 'ABIL-', 5: 'TRAIT+', 6: 'TRAIT-',
            7: 'SPELL+', 8: 'SPELL-', 9: 'SKILL+10'}
GM_MODPC_NAMES = dict((v, k) for k, v in GM_MODPC.items())
# guildmaster ONACT slots = act event types; only 6 fires in the shipped game
GM_ACT_SLOTS = {0: 'PC leaves (never sent)', 4: 'talk (never sent)', 6: 'button / item button'}
# older SDK names, still accepted by the assembler
GM_ALIASES = {'ENDGAME': 'EXIT', 'STAT146': 'GENDER', 'STAT148': 'CLAN', 'STAT220': 'ALIGNMENT',
              'NPCTIMER': 'NPCSTATE'}
CMP_CODES = dict((v, k) for k, v in CMP.items())
ACT_SLOTS = {0: 'attacked', 1: 'PC leaves', 2: 'used / fought', 3: 'steal', 4: 'item given',
             7: 'gold given', 9: 'talk / keyword', 11: 'all PCs gone'}


class ScriptError(Exception):
    pass


# --------------------------------------------------------------------------
# string tables
# --------------------------------------------------------------------------
def parse_table(buf):
    """u32 count, u32 offsets[count] (from the table start), NUL-terminated strings."""
    if len(buf) < 4:
        return []
    n = struct.unpack_from('<I', buf, 0)[0]
    out = []
    for i in range(n):
        o = struct.unpack_from('<I', buf, 4 + 4 * i)[0]
        e = buf.find(b'\0', o)
        out.append(buf[o:e if e >= 0 else len(buf)].decode('latin-1'))
    return out


def parse_responses(buf):
    """GMDATA.PAK slot 65+n, the guildmaster response table (GMFindResponse_):
    u32 count, count x {i16 key, u8 type, u32 offset from the table start},
    NUL-terminated strings -> [(key, type, text)]. Used only for GM talk,
    which the shipped game never sends; kept byte for byte."""
    out = []
    if len(buf) < 4:
        return out
    n = struct.unpack_from('<I', buf, 0)[0]
    for i in range(n):
        if 4 + 7 * i + 7 > len(buf):
            break
        key, typ, off = struct.unpack_from('<hBI', buf, 4 + 7 * i)
        e = buf.find(b'\0', off)
        out.append((key, typ, buf[off:e if e >= 0 else len(buf)].decode('latin-1') if off < len(buf) else ''))
    return out


def build_table(strings):
    if not strings:
        return b''
    head = 4 + 4 * len(strings)
    offs, body = [], bytearray()
    for s in strings:
        offs.append(head + len(body))
        body += s.encode('latin-1') + b'\0'
    return struct.pack('<I%dI' % len(strings), len(strings), *offs) + bytes(body)


# --------------------------------------------------------------------------
# disassembler
# --------------------------------------------------------------------------
def quote(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


class Script(object):
    """One NPC/GM: code bytes, strings, keywords."""

    def __init__(self, code=b'', strings=None, keywords=None, gm=False):
        self.code = bytes(code)
        self.strings = list(strings or [])
        self.keywords = list(keywords or [])
        self.responses = []           # guildmasters: parse_responses() of slot 65+n (read only)
        self.gm = gm

    @property
    def ops(self):
        return GMOPS if self.gm else OPS

    @property
    def exprs(self):
        return GMEXPR if self.gm else EXPR

    def decode(self):
        """[(offset, opcode, mnemonic, [operand values], [jump targets])]; operand
        values are python objects (int, ('s', i), ('k', i), ('L', addr),
        ('x', name, args), ('cmp', str), ('list', [...]), ('default', addr))."""
        c = self.code
        ops, ex = self.ops, self.exprs
        p = [0]

        def u8():
            if p[0] >= len(c):
                raise ScriptError('read past the end')
            p[0] += 1
            return c[p[0] - 1]

        def i16():
            if p[0] + 2 > len(c):
                raise ScriptError('read past the end')
            p[0] += 2
            return struct.unpack_from('<h', c, p[0] - 2)[0]

        def expr():
            k = u8()
            if k not in ex:
                raise ScriptError('unknown expression kind 0x%02x' % k)
            name, spec = ex[k]
            return ('x', name, [i16() for t in spec.split(',') if t])

        def val():
            dt = u8()
            if dt == 0:
                return i16()
            if dt == 1:
                return expr()
            raise ScriptError('bad value type %d' % dt)

        out = []
        modpc = 0x15 if self.gm else 0x1c
        skips = (0x03, 0x04, 0x19) if self.gm else (0x03, 0x04, 0x20)
        while p[0] < len(c):
            st = p[0]
            op = u8()
            if op == 0 and p[0] == len(c):
                out.append((st, 0, 'PAD', [], []))
                break
            if op not in ops:
                raise ScriptError('unknown opcode 0x%02x at 0x%04x' % (op, st))
            mn, spec = ops[op]
            vals, tg = [], []
            for t in spec:
                if t == 'u8':
                    vals.append(u8())
                elif t == 'i16':
                    vals.append(i16())
                elif t == 'addr':
                    a = i16() & 0xffff
                    vals.append(('L', a))
                    tg.append(a)
                elif t == 'str':
                    vals.append(('s', i16()))
                elif t == 'rsp':
                    vals.append(('k', i16()))
                elif t == 'expr':
                    vals.append(expr())
                elif t == 'cmp':
                    vals.append(('cmp', CMP.get(u8(), '?')))
                elif t == 'val':
                    vals.append(val())
                elif t == 'reply':
                    n = u8()
                    vals.append(('s', i16()))
                    vals.append(('list', [('s', i16()) for _ in range(n)]))
                elif t == 'table':
                    n = u8()
                    tab = [i16() & 0xffff for _ in range(n)]
                    tg += tab
                    vals.append(('list', [('L', a) for a in tab]))
                elif t == 'onexpr':
                    n = u8()
                    vals.append(expr())
                    if u8() != 0x07:
                        raise ScriptError('ONEXPR at 0x%04x is not followed by JUMP' % st)
                    d = i16() & 0xffff
                    vals.append(('default', d))
                    tab = [i16() & 0xffff for _ in range(n)]
                    tg += [d] + tab
                    vals.append(('list', [('L', a) for a in tab]))
            mtab = GM_MODPC if self.gm else MODPC
            if op == modpc and len(vals) > 1 and vals[1] in mtab:
                vals[1] = ('kind', mtab[vals[1]])
            if op in skips:
                tg.append(p[0] + 3)
            out.append((st, op, mn, vals, tg))
        return out

    def disassemble(self, comments=True, title=None):
        ins = self.decode()
        labels = set()
        for i in ins:
            labels.update(t for t in i[4])
        # skip targets of IF/IFKEY are just "the instruction after the JUMP": no label needed
        skip_only = set()
        for k, (st, op, mn, vals, tg) in enumerate(ins):
            if mn in ('IF', 'IFNOT', 'IFKEY'):
                skip_only.add(tg[-1])
        named = set()
        for st, op, mn, vals, tg in ins:
            for v in vals:
                for x in (v[1] if isinstance(v, tuple) and v[0] == 'list' else [v]):
                    if isinstance(x, tuple) and x[0] in ('L', 'default'):
                        named.add(x[1])
        lines = []
        if title:
            lines.append('; ' + title)
        lines.append('.strings')
        for i, s in enumerate(self.strings):
            lines.append('s%d %s' % (i, quote(s)))
        if self.gm:
            if self.responses:
                lines.append('; response table (GMDATA slot 65+n, for GM talk, which the game never sends;')
                lines.append('; kept as is): ' + ', '.join('%d/%d %s' % (k, t, quote(x)) for k, t, x in self.responses))
        else:
            lines.append('.keywords')
            for i, s in enumerate(self.keywords):
                lines.append('k%d %s' % (i, quote(s)))
        lines.append('.code')

        def fmt(v):
            if isinstance(v, int):
                return str(v)
            t = v[0]
            if t in ('s', 'k'):
                return '%s%d' % (t, v[1])
            if t == 'L':
                return 'L%04x' % v[1]
            if t == 'x':
                return '%s(%s)' % (v[1], ','.join(str(a) for a in v[2]))
            if t in ('cmp', 'kind'):
                return v[1]
            if t == 'default':
                return 'default L%04x' % v[1]
            if t == 'list':
                return '[' + ', '.join(fmt(x) for x in v[1]) + ']'
            raise ValueError(v)

        def note(vals):
            out = []
            for v in vals:
                for x in (v[1] if isinstance(v, tuple) and v[0] == 'list' else [v]):
                    if isinstance(x, tuple) and x[0] == 's' and 0 <= x[1] < len(self.strings):
                        out.append(self.strings[x[1]])
                    elif isinstance(x, tuple) and x[0] == 'k' and 0 <= x[1] < len(self.keywords):
                        out.append(self.keywords[x[1]])
            return ' | '.join(out)

        for st, op, mn, vals, tg in ins:
            if st in named:
                lines.append('L%04x:' % st)
            if mn == 'PAD':
                lines.append('        PAD')
                continue
            if mn in ('IF', 'IFNOT'):
                arg = '%s %s %s' % (fmt(vals[0]), fmt(vals[1]), fmt(vals[2]))
            else:
                arg = ', '.join(fmt(v) for v in vals)
            line = '        %-12s %s' % (mn, arg)
            c = note(vals) if comments else ''
            if mn == 'ONACT' and comments:
                c = (GM_ACT_SLOTS if self.gm else ACT_SLOTS).get(vals[0], '')
            if c:
                line = '%-44s ; %s' % (line.rstrip(), c.replace('\n', ' '))
            lines.append(line.rstrip())
        return '\n'.join(lines) + '\n'


# --------------------------------------------------------------------------
# assembler
# --------------------------------------------------------------------------
_STR = r'"(?:[^"\\]|\\.)*"'


def unquote(t):
    return re.sub(r'\\(.)', r'\1', t[1:-1])


def split_args(s):
    out, depth, cur, q = [], 0, '', False
    i = 0
    while i < len(s):
        ch = s[i]
        if q:
            cur += ch
            if ch == '\\' and i + 1 < len(s):
                cur += s[i + 1]
                i += 1
            elif ch == '"':
                q = False
        elif ch == '"':
            q = True
            cur += ch
        elif ch in '([':
            depth += 1
            cur += ch
        elif ch in ')]':
            depth -= 1
            cur += ch
        elif ch == ',' and depth == 0:
            out.append(cur.strip())
            cur = ''
        else:
            cur += ch
        i += 1
    if cur.strip():
        out.append(cur.strip())
    return out


def strip_comment(line):
    q = False
    for i, ch in enumerate(line):
        if ch == '"' and (i == 0 or line[i - 1] != '\\'):
            q = not q
        elif ch == ';' and not q:
            return line[:i]
    return line


def assemble(text, gm=False):
    """Text -> Script. Raises ScriptError with the line number."""
    sc = Script(gm=gm)
    ops = GMOPS if gm else OPS
    ex = GMEXPR if gm else EXPR
    by_name = dict((mn, (op, spec)) for op, (mn, spec) in ops.items())
    ex_name = dict((n, (k, spec)) for k, (n, spec) in ex.items())
    if gm:
        for old, new in GM_ALIASES.items():
            if new in by_name:
                by_name[old] = by_name[new]
            if new in ex_name:
                ex_name[old] = ex_name[new]
    mnames = GM_MODPC_NAMES if gm else MODPC_NAMES
    modpc = 0x15 if gm else 0x1c
    section = 'code'
    strings, keywords = {}, {}
    body = []
    for no, raw in enumerate(text.splitlines(), 1):
        line = strip_comment(raw).strip()
        if not line:
            continue
        if line in ('.strings', '.keywords', '.code'):
            section = line[1:]
            continue
        if section in ('strings', 'keywords'):
            m = re.match(r'([sk])(\d+)\s+(' + _STR + r')\s*$', line)
            if not m:
                raise ScriptError('line %d: expected  %s<n> "text"' % (no, section[0]))
            (strings if section == 'strings' else keywords)[int(m.group(2))] = unquote(m.group(3))
            continue
        while True:
            m = re.match(r'([A-Za-z_][\w]*):\s*(.*)$', line)
            if not m:
                break
            body.append((no, 'label', m.group(1)))
            line = m.group(2).strip()
        if line:
            parts = line.split(None, 1)
            body.append((no, parts[0].upper(), parts[1] if len(parts) > 1 else ''))
    for d, name in ((strings, 'strings'), (keywords, 'keywords')):
        if d and sorted(d) != list(range(len(d))):
            raise ScriptError('%s must be numbered 0..n-1 without gaps' % name)
    sc.strings = [strings[i] for i in range(len(strings))]
    sc.keywords = [keywords[i] for i in range(len(keywords))]
    if gm and sc.keywords:
        raise ScriptError('guildmaster scripts have no .keywords (IFKEY takes a string; GMDATA slot 65+n '
                          'is a response table, kept as is)')

    def str_index(tok, table, prefix, no):
        tok = tok.strip()
        m = re.match(prefix + r'(\d+)$', tok)
        if m:
            i = int(m.group(1))
            if i >= len(table):
                raise ScriptError('line %d: %s%d does not exist' % (no, prefix, i))
            return i
        if re.match(_STR + '$', tok):
            s = unquote(tok)
            if s in table:
                return table.index(s)
            table.append(s)
            return len(table) - 1
        raise ScriptError('line %d: expected %s<n> or "text", got %r' % (no, prefix, tok))

    def num(tok, no):
        try:
            return int(tok, 0)
        except ValueError:
            raise ScriptError('line %d: expected a number, got %r' % (no, tok))

    def enc_expr(tok, no):
        m = re.match(r'([A-Za-z_]\w*)\((.*)\)$', tok.strip())
        if not m or m.group(1).upper() not in ex_name:
            raise ScriptError('line %d: unknown expression %r' % (no, tok))
        k, spec = ex_name[m.group(1).upper()]
        args = [a for a in split_args(m.group(2))]
        want = len([t for t in spec.split(',') if t])
        if len(args) != want:
            raise ScriptError('line %d: %s takes %d values' % (no, m.group(1), want))
        return bytes([k]) + b''.join(struct.pack('<h', num(a, no)) for a in args)

    def enc_val(tok, no):
        tok = tok.strip()
        if re.match(r'-?(0x[0-9a-fA-F]+|\d+)$', tok):
            return b'\0' + struct.pack('<h', num(tok, no))
        return b'\x01' + enc_expr(tok, no)

    def listitems(tok, no):
        tok = tok.strip()
        if not (tok.startswith('[') and tok.endswith(']')):
            raise ScriptError('line %d: expected [ ... ]' % no)
        return split_args(tok[1:-1].replace('|', ','))

    # two passes: sizes, then encoding with resolved labels
    def encode(labels):
        out = bytearray()
        fix = []
        for no, mn, arg in body:
            if mn == 'label':
                labels.setdefault(arg, None)
                if labels.get(arg) is None or labels[arg] != len(out):
                    labels[arg] = len(out)
                continue
            if mn == 'PAD':
                out.append(0)
                continue
            if mn not in by_name:
                raise ScriptError('line %d: unknown instruction %s' % (no, mn))
            op, spec = by_name[mn]
            out.append(op)

            def addr(tok):
                tok = tok.strip()
                if tok.startswith('default '):
                    tok = tok[8:].strip()
                m = re.match(r'L([0-9a-fA-F]{4})$', tok)
                if tok in labels and labels[tok] is not None:
                    return struct.pack('<H', labels[tok])
                if tok in labels or m is None:
                    fix.append((no, tok))
                    return b'\0\0'
                return struct.pack('<H', int(m.group(1), 16))
            if mn in ('IF', 'IFNOT'):
                m = re.match(r'(.+?)\s*(==|!=|<|>)\s*(.+)$', arg)
                if not m:
                    raise ScriptError('line %d: IF expr ==|!=|<|> value' % no)
                out += enc_expr(m.group(1), no) + bytes([CMP_CODES[m.group(2)]]) + enc_val(m.group(3), no)
                continue
            args = split_args(arg)
            if 'reply' in spec:
                if len(args) != 2:
                    raise ScriptError('line %d: REPLY prompt, [options]' % no)
                prompt = str_index(args[0], sc.strings, 's', no)
                opts = listitems(args[1], no)
                lits = [o for o in opts if o.startswith('"')]
                if lits and len(lits) == len(opts):
                    texts = [unquote(o) for o in opts]
                    idx = None
                    for i in range(len(sc.strings) - len(texts) + 1):
                        if sc.strings[i:i + len(texts)] == texts:
                            idx = list(range(i, i + len(texts)))
                            break
                    if idx is None:
                        idx = list(range(len(sc.strings), len(sc.strings) + len(texts)))
                        sc.strings.extend(texts)
                else:
                    idx = [str_index(o, sc.strings, 's', no) for o in opts]
                out += bytes([len(idx)]) + struct.pack('<h', prompt) + b''.join(struct.pack('<h', i) for i in idx)
                continue
            if 'onexpr' in spec:
                if len(args) != 3:
                    raise ScriptError('line %d: ONEXPR expr, default label, [labels]' % no)
                tab = listitems(args[2], no)
                out += bytes([len(tab)]) + enc_expr(args[0], no) + b'\x07' + addr(args[1])
                for t in tab:
                    out += addr(t)
                continue
            if 'table' in spec:
                tab = listitems(args[0] if args else '[]', no)
                out += bytes([len(tab)])
                for t in tab:
                    out += addr(t)
                continue
            if len(args) != len(spec):
                raise ScriptError('line %d: %s takes %d operands' % (no, mn, len(spec)))
            for k, (t, a) in enumerate(zip(spec, args)):
                if op == modpc and k == 1 and a.upper() in mnames:
                    a = str(mnames[a.upper()])
                if t == 'u8':
                    out.append(num(a, no) & 0xff)
                elif t == 'i16':
                    out += struct.pack('<h', num(a, no))
                elif t == 'addr':
                    out += addr(a)
                elif t == 'str':
                    out += struct.pack('<h', str_index(a, sc.strings, 's', no))
                elif t == 'rsp':
                    out += struct.pack('<h', str_index(a, sc.keywords, 'k', no))
                elif t == 'expr':
                    out += enc_expr(a, no)
                elif t == 'val':
                    out += enc_val(a, no)
        return bytes(out), fix

    labels = {}
    nstr, nkw = len(sc.strings), len(sc.keywords)
    code, fix = encode(labels)
    del sc.strings[nstr:], sc.keywords[nkw:]
    code, fix = encode(labels)
    if fix:
        no, tok = fix[0]
        raise ScriptError('line %d: unknown label %r' % (no, tok))
    sc.code = code
    return sc


# --------------------------------------------------------------------------
# the PAK files
# --------------------------------------------------------------------------
class DialoguePak(object):
    """NPCDATA.PAK (gm=False) or GMDATA.PAK (gm=True)."""

    def __init__(self, data, gm=False):
        self.pak = d6data.Pak.parse(data)
        self.gm = gm
        self.groups = (self.pak.nslots - 1) // 3
        self.members = [self.pak.member(i) for i in range(self.pak.nslots)]
        self.dirty = False

    @classmethod
    def load(cls, gamedir, gm=False):
        p = d6data.find_file(gamedir, 'GMDATA.PAK' if gm else 'NPCDATA.PAK')
        return cls(open(p, 'rb').read(), gm)

    def count(self):
        return self.groups

    def script(self, n):
        g = self.groups
        if self.gm:            # slot 65+n is the response table, not keywords
            sc = Script(self.members[1 + n], parse_table(self.members[1 + g + n]), [], True)
            sc.responses = parse_responses(self.members[1 + 2 * g + n])
            return sc
        return Script(self.members[1 + n], parse_table(self.members[1 + g + n]),
                      parse_table(self.members[1 + 2 * g + n]), self.gm)

    def set_script(self, n, sc):
        g = self.groups
        if sc.code and (not sc.code.endswith(b'\x01\x00')):
            sc.code = sc.code + (b'\x00' if sc.code.endswith(b'\x01') else b'\x01\x00')
        self.members[1 + n] = sc.code
        # tables are only rebuilt when their text changed (some retail tables
        # share strings or carry padding; unchanged ones stay byte identical)
        if parse_table(self.members[1 + g + n]) != sc.strings:
            self.members[1 + g + n] = build_table(sc.strings)
        if not self.gm and parse_table(self.members[1 + 2 * g + n]) != sc.keywords:
            self.members[1 + 2 * g + n] = build_table(sc.keywords)
        self.dirty = True

    def build(self):
        return self.to_bytes()

    def to_bytes(self):
        """Members are laid out in slot order after the directory."""
        n = len(self.members)
        out = bytearray(8 * n)
        for i, m in enumerate(self.members):
            if m:
                struct.pack_into('<II', out, 8 * i, len(out), len(m))
                out += m
            else:
                struct.pack_into('<II', out, 8 * i, 0, 0)
        return bytes(out)


def npc_names(gamedir=None, table=None):
    """{npc id: name} from D6NPC.DAT (record n-1 is NPC n)."""
    out = {}
    try:
        t = table or d6data.NpcTable.parse(open(d6data.find_file(gamedir, 'D6NPC.DAT'), 'rb').read())
        for i, r in enumerate(t.records):
            out[i + 1] = r.name.strip()
    except Exception:
        pass
    return out


TEMPLATE = """.strings
s0 "Greetings, $N."
s1 "Can I help you?"
s2 "Yes"
s3 "No"
s4 "Farewell."
.keywords
k0 "Hail"
k1 "Goodbye"
k2 "$BLANK$"
.code
        IF           NPCFLAG(0) == 1
        JUMP         init
        JUMP         main
init:   ONACT        9, talk                  ; talk / keyword
        ONACT        1, bye                   ; PC leaves
        ONACT        11, bye                  ; all PCs gone
        SETFLAG      0, 1
        END
main:   SAY          s0
        REPLY        s1, [s2, s3]
        ONREPLY      [yes, no]
yes:    END
no:     SAY          s4
        BYE
        END
talk:   IFKEY        k0                       ; a match skips the next JUMP
        JUMP         t1
        JUMP         main
t1:     IFKEY        k1
        JUMP         t2
        JUMP         no
t2:     END
bye:    END
"""

# Guildmasters: id -> (town, building, name). Buildings read their GM from
# per-town tables in the exe (_gHallGM, _gSmitGM, ...); 21 is Gareth's intro.
TOWNS = {0: 'Valeia', 1: "Ishad N'ha", 2: 'Brimloch Roon'}
GM_INFO = {
    1: (0, 'Town Hall', 'Sir Elgar'), 2: (0, 'Temple', 'Onabe'), 3: (0, 'Smith', 'Smitty'),
    4: (0, 'Mage', 'Roendalf'), 5: (0, 'Tavern', ''), 6: (0, 'Dojo', 'Master Wu'),
    7: (0, 'Pawn shop', 'Bratsol'), 8: (1, 'Town Hall', 'Lord Barrenhawk'), 9: (1, 'Temple', 'Munsey'),
    10: (1, 'Smith', 'Damosh'), 11: (1, 'Mage', 'Xander'), 12: (1, 'Tavern', ''),
    13: (2, 'Dojo', 'Sinsei Asami'), 14: (2, 'Pawn shop', 'Miruth'), 15: (2, 'Town Hall', 'Duke Brinsly'),
    16: (2, 'Temple', 'Malakai'), 17: (2, 'Smith', 'Strumbold'), 18: (2, 'Mage', 'Sabastio'),
    19: (2, 'Tavern', 'Holthorne'), 20: (None, 'Ship yard', 'Buckly'), 21: (None, "Gareth's intro", 'Gareth'),
}
# main menu buttons per building (ACTBUTTON values; docs/formats/npc.md section 5)
GM_BUTTONS = {
    'Town Hall': '0 news, 1 bank, 2 leave',
    'Tavern': '0 news, 1 ale, 2 leave',
    'Smith': '0 buy, 1 sell, 2 repair, 3 identify, 4 guild, 5 leave',
    'Mage': '0 buy, 1 sell, 2 enchant, 3 identify, 4 guild, 5 leave',
    'Temple': '0 rites, 1 blessing, 2 donate, 3 guild, 4 leave, 5 curse lifted',
    'Dojo': '0 buy, 1 sell, 2 guild, 3 leave', 'Pawn shop': '0 buy, 1 sell, 2 guild, 3 leave',
    'Ship yard': '0 browse, 1 warship bought, 2 quest, 3 leave',
}
GM_RESULTS = ('700/701/702/703 item bought/sold/repaired/identified (ACTITEM = item), 710/711 deposit/withdraw, '
              '800+role train role, 820 quest button, 821/822 specials yes/no, 850+ability trained, '
              '865 skill/trait trained, 900 no gold / cannot join, 901 already donated, 902 no rites needed, '
              '903 item not usable, 904 nothing to repair, 905 already identified, 909 cannot carry, '
              '910 no more drinks, 1000+n drink served')

GM_TEMPLATE = """.strings
s0 "Welcome, $N."
s1 "What can I do for you?"
s2 "Here is the latest news..."
s3 "We take deposits of any amount."
s4 "Farewell."
.code
        IF           GMFLAG(0) == 1
        JUMP         init
        JUMP         main
init:   ONACT        0, done                  ; PC leaves (never sent)
        ONACT        4, done                  ; talk (never sent)
        ONACT        6, button                ; button / item button
        SETFLAG      0, 1
        SETFLAG      1, 0
        END
main:   SAY          s0                       ; greeting: runs when a PC steps up
        SAY          s1
        END
button: IF           GMFLAG(1) == 1           ; already leaving: ignore
        JUMP         go
        JUMP         done
go:     CLEARWAITS                            ; stop the greeting that may still run
        PURGEMSGQ
        ONEXPR       ACTBUTTON(), default done, [news, bank, leave]
news:   SAY          s2
        END
bank:   SAY          s3
        END
leave:  SETFLAG      1, 1
        SAY          s4
        EXIT                                  ; back to the town map
done:   END
"""


def selftest(gamedir):
    ok = bad = 0
    for gm in (False, True):
        dp = DialoguePak.load(gamedir, gm)
        for n in range(dp.count()):
            sc = dp.script(n)
            if not sc.code:
                continue
            txt = sc.disassemble()
            try:
                sc2 = assemble(txt, gm)
            except ScriptError as e:
                print('%s %d: %s' % ('GM' if gm else 'NPC', n, e))
                bad += 1
                continue
            same = (sc2.code == sc.code and sc2.strings == sc.strings and sc2.keywords == sc.keywords)
            before = list(dp.members)
            dp.set_script(n, sc2)
            tab = dp.members == before
            if same and tab:
                ok += 1
            else:
                bad += 1
                print('%s %d: differs (code %s, tables %s)' % ('GM' if gm else 'NPC', n,
                                                             sc2.code == sc.code, tab))
        orig = open(d6data.find_file(gamedir, 'GMDATA.PAK' if gm else 'NPCDATA.PAK'), 'rb').read()
        print('%s rebuilt identical: %s' % ('GMDATA' if gm else 'NPCDATA', dp.to_bytes() == orig))
    print('scripts: %d round trip exactly, %d differ' % (ok, bad))
    return bad == 0


def main(argv):
    a = argv[1:]
    if len(a) >= 2 and a[0] == '--selftest':
        return 0 if selftest(a[1]) else 1
    if len(a) >= 3 and a[0] in ('--export', '--import'):
        g, d = a[1], a[2]
        for gm in (False, True):
            dp = DialoguePak.load(g, gm)
            pre = 'gm' if gm else 'npc'
            if a[0] == '--export':
                os.makedirs(d, exist_ok=True)
                for n in range(dp.count()):
                    sc = dp.script(n)
                    if sc.code:
                        with open(os.path.join(d, '%s%03d.txt' % (pre, n)), 'w', encoding='latin-1') as f:
                            f.write(sc.disassemble(title='%s %d' % (pre.upper(), n)))
            else:
                for n in range(dp.count()):
                    p = os.path.join(d, '%s%03d.txt' % (pre, n))
                    if os.path.exists(p):
                        dp.set_script(n, assemble(open(p, encoding='latin-1').read(), gm))
                if dp.dirty:
                    fn = d6data.find_file(g, 'GMDATA.PAK' if gm else 'NPCDATA.PAK')
                    with open(fn + '.tmp', 'wb') as f:
                        f.write(dp.to_bytes())
                    os.replace(fn + '.tmp', fn)
                    print('wrote', fn)
        return 0
    if len(a) >= 2:
        gm = '--gm' in a
        dp = DialoguePak.load(a[0], gm)
        sys.stdout.write(dp.script(int(a[1])).disassemble(title='%s %s' % ('GM' if gm else 'NPC', a[1])))
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
