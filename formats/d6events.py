#!/usr/bin/env python3
"""
d6events.py - assembler / disassembler for the EVENTS.COD event scripts and
editing of EVENTS.DCL (event names, parameter names, descriptions).

An event is a small routine with named parameters; triggers (D6TRIGnn.DAT)
name the event and supply the parameter values. Operands of an instruction
are parameter *indices*: OPENDOOR DOOR, SPEED, NAVPNT, SFXREC means "open the
door whose number is the trigger's DOOR value ...".

Assembly syntax (one instruction per line, ';' comments):

    start:
        IFSTATE !STATE -> open           ; conditional jump when the state is set
        Q:CLOSEDOOR DOOR, SPEED, NAVPNT, SFXREC
        END2
    open:
        Q:OPENDOOR DOOR, SPEED, NAVPNT, SFXREC
        END

* operands: parameter names of the event, or p0, p1 ... (index)
* 'Q:' = queued op (runs over time; the routine resumes at the next
  instruction when it finishes, unless '-> label' says otherwise)
* jumps (GOTO, IF...) need '-> label'; labels end with ':'
* unknown opcodes: OP_xx with xx in hex

CLI:
    d6events.py --list GAMEDIR
    d6events.py --show GAMEDIR @NAME
    d6events.py --selftest GAMEDIR     (disassemble + reassemble every event, compare bytes)
"""
import re
import struct
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import d6data  # noqa: E402

OPS = d6data.EV_OPS
MNEMONIC = dict((v[0], k) for k, v in OPS.items())
JUMPS = d6data.EV_JUMPS
# operand counts seen in the shipped EVENTS.COD (validation hint only)
SEEN_COUNTS = {0x01: 0, 0x02: 0, 0x03: 0, 0x04: 1, 0x05: 4, 0x06: 4, 0x07: 1, 0x08: 2, 0x09: 1, 0x0A: 2,
               0x0B: 1, 0x0E: 1, 0x0F: 1, 0x10: 1, 0x11: 1, 0x12: 5, 0x17: 3, 0x19: 1, 0x1B: 7, 0x1C: 2,
               0x1E: 1, 0x21: 3, 0x22: 3, 0x23: 5, 0x24: 5, 0x25: 4, 0x26: 5, 0x27: 1, 0x28: 1, 0x29: 3,
               0x2A: 5, 0x2B: 1, 0x2C: 1, 0x2D: 2, 0x2E: 1, 0x2F: 7, 0x30: 1, 0x31: 1, 0x32: 1, 0x33: 2,
               0x34: 8, 0x35: 1, 0x36: 9, 0x37: 1, 0x38: 2, 0x39: 3, 0x3A: 7, 0x3B: 4, 0x3C: 2, 0x3D: 2,
               0x3E: 2, 0x3F: 2, 0x40: 2, 0x41: 2, 0x42: 5, 0x43: 1, 0x44: 1, 0x45: 3, 0x46: 3, 0x47: 2,
               0x48: 9, 0x49: 5, 0x4A: 4, 0x4B: 9, 0x4C: 8, 0x4D: 3, 0x4E: 10, 0x4F: 1, 0x50: 2, 0x51: 1,
               0x52: 5, 0x53: 3, 0x54: 1, 0x55: 1, 0x56: 6}


class AsmError(Exception):
    def __init__(self, line, msg):
        super().__init__('line %d: %s' % (line, msg))
        self.line = line


def event_range(dcl, code, idx):
    start = dcl.codeoff[idx]
    ends = sorted(set(dcl.codeoff[:dcl.n])) + [len(code)]
    later = [x for x in ends if x > start]
    return start, (min(later) if later else len(code))


def disassemble(code, start, end, pnames):
    """Text with labels (re-assemblable)."""
    ins = []
    pc = start
    while pc < end:
        op, q, args, tgt, ln = d6data.ev_decode(code, pc)
        ins.append((pc, op, q, args, tgt, ln))
        pc += ln
    labels = {}
    for pc, op, q, args, tgt, ln in ins:
        if tgt is not None and not (q and tgt == pc + ln):
            if tgt not in labels:
                labels[tgt] = 'L%d' % (len(labels) + 1)
    out = []
    for pc, op, q, args, tgt, ln in ins:
        if pc in labels:
            out.append('%s:' % labels[pc])
        mn = OPS[op][0] if op in OPS else 'OP_%02X' % op
        a = ', '.join(pnames[x] if 0 <= x < len(pnames) and pnames[x] else 'p%d' % x for x in args)
        s = '    %s%s%s' % ('Q:' if q else '', mn, (' ' + a) if a else '')
        if tgt is not None and not (q and tgt == pc + ln):
            s += ' -> %s' % labels.get(tgt, '0x%04x' % tgt)
        note = OPS[op][2] if op in OPS else ''
        if note:
            s = s.ljust(52) + ' ; ' + note
        out.append(s)
    for t, name in labels.items():
        if t >= end or t < start:
            pass
    return '\n'.join(out) + '\n'


def assemble(text, pnames, base):
    """Assemble text for code placed at absolute offset base. Returns
    (bytes, warnings)."""
    pidx = dict((n.upper(), i) for i, n in enumerate(pnames) if n)
    items = []          # (lineno, op, queued, args, target_label_or_int)
    labels = {}
    warnings = []
    pc = base
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.split(';', 1)[0].strip()
        if not line:
            continue
        m = re.match(r'^([A-Za-z_][\w]*)\s*:(?!\S)(.*)$', line)
        if m and m.group(1).upper() not in ('Q',):
            lab = m.group(1)
            if lab in labels:
                raise AsmError(ln, 'label %s defined twice' % lab)
            labels[lab] = pc
            line = m.group(2).strip()
            if not line:
                continue
        q = False
        if line.upper().startswith('Q:'):
            q = True
            line = line[2:].strip()
        tgt = None
        if '->' in line:
            line, t = line.split('->', 1)
            tgt = t.strip()
            line = line.strip()
        parts = line.split(None, 1)
        mn = parts[0].upper()
        if mn in MNEMONIC:
            op = MNEMONIC[mn]
        elif re.match(r'^OP_[0-9A-F]{1,4}$', mn):
            op = int(mn[3:], 16)
        else:
            raise AsmError(ln, 'unknown instruction %s' % parts[0])
        args = []
        if len(parts) > 1 and parts[1].strip():
            for a in parts[1].split(','):
                a = a.strip()
                if not a:
                    continue
                if a.upper() in pidx:
                    args.append(pidx[a.upper()])
                elif re.match(r'^[pP]\d+$', a):
                    args.append(int(a[1:]))
                else:
                    raise AsmError(ln, 'unknown parameter %s (event parameters: %s)' % (a, ', '.join(pnames)))
        for x in args:
            if x >= 30:
                raise AsmError(ln, 'parameter index %d >= 30 (triggers hold 30 values)' % x)
        if op in JUMPS and tgt is None and not q:
            raise AsmError(ln, '%s needs a target: -> label' % mn)
        if op in SEEN_COUNTS and SEEN_COUNTS[op] != len(args):
            warnings.append('line %d: %s usually takes %d operand(s), got %d' % (ln, mn, SEEN_COUNTS[op], len(args)))
        size = 4 + 2 * len(args) + (4 if (q or op in JUMPS) else 0)
        if tgt is not None and not (q or op in JUMPS):
            raise AsmError(ln, '%s takes no target' % mn)
        items.append((ln, op, q, args, tgt, pc + size))
        pc += size
    out = bytearray()
    for ln, op, q, args, tgt, nxt in items:
        out += struct.pack('<Hh', op | (0x8000 if q else 0), len(args))
        out += struct.pack('<%dh' % len(args), *args) if args else b''
        if q or op in JUMPS:
            if tgt is None:
                t = nxt
            elif tgt in labels:
                t = labels[tgt]
            elif re.match(r'^(0x[0-9a-fA-F]+|\d+)$', tgt):
                t = int(tgt, 0)
            else:
                raise AsmError(ln, 'unknown label %s' % tgt)
            out += struct.pack('<I', t)
    if items and items[-1][1] not in (0x01, 0x02, 0x03):
        warnings.append('the routine does not end with END / END2 / GOTO')
    return bytes(out), warnings


# ---------------------------------------------------------------------------
# EVENTS.DCL editing
# ---------------------------------------------------------------------------
class EventSet(object):
    """EVENTS.DCL + EVENTS.COD together, with editing."""

    def __init__(self, dcl_bytes, cod_bytes):
        self.dcl = d6data.EventDCL.parse(dcl_bytes)
        self.code = bytearray(cod_bytes)
        self.dirty = False
        self._dcl_raw = bytes(dcl_bytes)

    @property
    def n(self):
        return self.dcl.n

    def name(self, i):
        return self.dcl.name(i)

    def params(self, i):
        return self.dcl.params_of(i)

    def desc(self, i):
        return self.dcl.desc(i)

    def source(self, i):
        s, e = event_range(self.dcl, self.code, i)
        return disassemble(bytes(self.code), s, e, self.params(i))

    def users(self, i, all_tables):
        """Trigger records (any spoke) that use event i: [(spoke, trigger index)]."""
        nm = self.name(i).upper()
        out = []
        for sp, tab in all_tables:
            for k, r in enumerate(tab.records, 1):
                if r.get('event').upper() == nm:
                    out.append((sp, k))
        return out

    def _set_name(self, i, name):
        b = name.encode('latin1')[:41]
        raw = bytearray(self.dcl.names_raw)
        raw[42 * i:42 * i + 42] = b + b'\0' * (42 - len(b))
        self.dcl.names_raw = bytes(raw)

    def _alloc_params(self, names):
        """Parameter names go to the end of the name table (old ranges stay,
        so nothing else moves)."""
        pn = self.dcl.paramnames
        start = self.dcl.nparamnames
        if start + len(names) > len(pn):
            raise ValueError('parameter name table is full')
        for k, n in enumerate(names):
            pn[start + k] = n[:41]
        self.dcl.nparamnames = start + len(names)
        return start

    def set_event(self, i, name, params, desc, text):
        """Replace (i < n) or add (i == n) an event. The new code is appended
        to EVENTS.COD (old code stays, unreferenced). Returns warnings."""
        if not re.match(r'^@[A-Z0-9_]+$', name):
            raise ValueError('event names look like @NAME (A-Z, 0-9, _)')
        if len(name) > 41:
            raise ValueError('name too long (41 characters max)')
        if len(params) > 30:
            raise ValueError('30 parameters max')
        for k in range(self.dcl.n):
            if k != i and self.dcl.name(k).upper() == name.upper():
                raise ValueError('%s exists already' % name)
        if i > self.dcl.n or i >= 1024:
            raise ValueError('event table is full')
        base = len(self.code)
        blob, warn = assemble(text, params, base)
        if not blob:
            raise ValueError('no code')
        old_params = self.params(i) if i < self.dcl.n else None
        if old_params is not None and [p.upper() for p in old_params] == [p.upper() for p in params]:
            first = self.dcl.firstparam[i]
        else:
            first = self._alloc_params(params)
        self.code += blob
        self.dcl.codeoff[i] = base
        self.dcl.firstparam[i] = first
        self.dcl.nparams[i] = len(params)
        self._set_name(i, name)
        while len(self.dcl.descs) <= i:
            self.dcl.descs.append('')
        self.dcl.descs[i] = desc
        if i == self.dcl.n:
            self.dcl.n += 1
        self.dirty = True
        return warn

    def build_dcl(self):
        if not self.dirty:
            return self._dcl_raw
        d = self.dcl
        game = struct.pack('<I', d.n) + struct.pack('<1024I', *d.codeoff) + d.names_raw
        game += struct.pack('<1024I', *d.firstparam) + struct.pack('<1024I', *d.nparams)
        pool = bytearray()
        offs = [0] * 1024
        for i in range(d.n):
            offs[i] = len(pool)
            pool += (d.descs[i] if i < len(d.descs) else '').encode('latin1', 'replace') + b'\0'
        ed = struct.pack('<1024I', *offs) + struct.pack('<I', len(pool)) + bytes(pool)
        ed += struct.pack('<I', d.nparamnames)
        for n in d.paramnames:
            b = n.encode('latin1')[:41]
            ed += b + b'\0' * (42 - len(b))
        return game + ed

    def build_cod(self):
        return bytes(self.code)


def selftest(gamedir):
    es = EventSet(d6data.read_file(gamedir, 'EVENTS.DCL'), d6data.read_file(gamedir, 'EVENTS.COD'))
    bad = 0
    for i in range(es.n):
        s, e = event_range(es.dcl, es.code, i)
        txt = es.source(i)
        blob, warn = assemble(txt, es.params(i), s)
        if blob != bytes(es.code[s:e]):
            bad += 1
            print('MISMATCH', es.name(i))
    # DCL rebuild without changes must be identical once marked dirty
    es.dirty = True
    same = es.build_dcl() == es._dcl_raw
    print('events %d, reassembly mismatches %d, DCL rebuild identical: %s' % (es.n, bad, same))
    return bad == 0 and same


def main(argv):
    if len(argv) >= 3 and argv[1] == '--selftest':
        return 0 if selftest(argv[2]) else 1
    if len(argv) >= 3 and argv[1] == '--list':
        es = EventSet(d6data.read_file(argv[2], 'EVENTS.DCL'), d6data.read_file(argv[2], 'EVENTS.COD'))
        for i in range(es.n):
            print('%3d %-28s %s  %s' % (i, es.name(i), ','.join(es.params(i)), es.desc(i)))
        return 0
    if len(argv) >= 4 and argv[1] == '--show':
        es = EventSet(d6data.read_file(argv[2], 'EVENTS.DCL'), d6data.read_file(argv[2], 'EVENTS.COD'))
        i = es.dcl.index(argv[3])
        print('; %s(%s)  %s' % (es.name(i), ', '.join(es.params(i)), es.desc(i)))
        print(es.source(i))
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
