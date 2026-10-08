#!/usr/bin/env python3
"""
d6textio.py - game tables as text, for diffs and version control.

Every record table (D6MONS, D6ITEM, D6PROP, D6NPC, D6TREAS, D6TRLIST,
D6MONSND, emitters.dat, the per-spoke D6Trig/Boun/Spec/Swit/Link/Trap) and
every object list (*.TOL, *.BOL, *.FOL, *.LIT) becomes one JSON-lines file:

  line 1   {"file": "D6MONS.DAT", "table": "MonsTable", "header": hex, "trailer": hex}
  line 2+  {"i": 1, "name": "Graveyard Skeleton", ..., "rest": hex}

one record per line, fields by name (docs/formats/databases.md), and "rest"
= the bytes no field covers (zero bytes are written as "", so most records
have none). Import rebuilds the files byte for byte; only files whose bytes
change are written (new file + rename).

  d6textio.py export GAMEDIR OUTDIR [--only PATTERN]
  d6textio.py import GAMEDIR INDIR [--dry-run]
  d6textio.py selftest GAMEDIR        export + import of every table, compare
"""
import fnmatch
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import d6data  # noqa: E402

D = d6data


def table_files(gamedir):
    """[(relative path, table class)] present in gamedir."""
    out = []
    for name, cls in D.GLOBAL_FILES + D.spoke_files():
        if isinstance(cls, type) and issubclass(cls, D.RecordTable) and D.find_file(gamedir, name):
            out.append((os.path.relpath(D.find_file(gamedir, name), gamedir).replace(os.sep, '/'), cls))
    for dp, ds, fs in os.walk(gamedir):
        ds[:] = [d for d in ds if d not in ('_mods', 'd6edit_backup', 'save')]
        for f in sorted(fs):
            if f.lower().rsplit('.', 1)[-1] in ('tol', 'bol', 'fol', 'lit'):
                out.append((os.path.relpath(os.path.join(dp, f), gamedir).replace(os.sep, '/'), D.ObjList))
    return out


def _covered(cls):
    m = bytearray(cls.SIZE)
    for f in cls.FIELDS:
        n = int(f.fmt[3:]) if f.fmt.startswith('str') else struct.calcsize('<' + f.fmt)
        for k in range(f.off, min(cls.SIZE, f.off + n)):
            m[k] = 1
    return m


def _jsonable(v):
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, float) and v != v:
        return 'nan'
    return v


def _value(f, v):
    if f.fmt.startswith('str'):
        return v
    if f.fmt.endswith('s'):
        return bytes.fromhex(v)
    if isinstance(v, list):
        return [float(x) if x == 'nan' else x for x in v]
    return float(v) if v == 'nan' else v


def export_table(data, cls, name):
    t = cls.parse(data)
    rc = t.REC
    cov = _covered(rc)
    lines = [json.dumps(dict(file=name, table=cls.__name__, header=bytes(t.header).hex(),
                             trailer=bytes(t.trailer).hex()))]
    for i, r in enumerate(t.records, 1):
        d = {'i': i}
        for f in rc.FIELDS:
            try:
                d[f.name] = _jsonable(r.get(f.name))
            except Exception:
                pass
        c = bytearray(cov)
        for f in rc.FIELDS:          # strings: only the text and its NUL; stale bytes after it go to rest
            if f.fmt.startswith('str'):
                n = int(f.fmt[3:])
                used = bytes(r.raw[f.off:f.off + n]).find(b'\0') + 1 or n
                for k in range(f.off + used, f.off + n):
                    c[k] = 0
        rest = bytes(b if not c[k] else 0 for k, b in enumerate(r.raw[:rc.SIZE]))
        tail = bytes(r.raw[rc.SIZE:])
        d['rest'] = '' if not any(rest) and not tail else (rest + tail).hex()
        lines.append(json.dumps(d, ensure_ascii=False))
    return '\n'.join(lines) + '\n'


def import_table(text, cls):
    lines = [json.loads(l) for l in text.splitlines() if l.strip()]
    head = lines[0]
    t = cls.parse(bytes.fromhex(head['header']) if head['header'] else b'\0\0\0\0')
    t.header = bytearray(bytes.fromhex(head['header']))
    t.trailer = bytes.fromhex(head['trailer'])
    rc = t.REC
    t.records = []
    for d in lines[1:]:
        raw = bytes.fromhex(d.get('rest') or '') or bytes(rc.SIZE)
        if len(raw) < rc.SIZE:
            raw += bytes(rc.SIZE - len(raw))
        r = rc(raw)
        for f in rc.FIELDS:
            if f.name in d:
                r.set(f.name, _value(f, d[f.name]))
        t.records.append(r)
    return t.build()


def export_all(gamedir, outdir, only=None, log=print):
    n = 0
    for rel, cls in table_files(gamedir):
        if only and not fnmatch.fnmatch(rel.lower(), only.lower()):
            continue
        data = open(os.path.join(gamedir, rel), 'rb').read()
        p = os.path.join(outdir, rel + '.jsonl')
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(export_table(data, cls, rel))
        n += 1
    log('exported %d tables to %s' % (n, outdir))


def import_all(gamedir, indir, dry=False, log=print):
    classes = dict((c.__name__, c) for c in (D.ObjList,) + tuple(
        c for _, c in D.GLOBAL_FILES + D.spoke_files() if isinstance(c, type)))
    changed = []
    for dp, ds, fs in os.walk(indir):
        for f in sorted(fs):
            if not f.endswith('.jsonl'):
                continue
            text = open(os.path.join(dp, f), encoding='utf-8').read()
            head = json.loads(text.splitlines()[0])
            cls = classes[head['table']]
            data = import_table(text, cls)
            tgt = D.find_file(gamedir, head['file']) or os.path.join(gamedir, head['file'])
            old = open(tgt, 'rb').read() if os.path.exists(tgt) else None
            if old == data:
                continue
            changed.append(head['file'])
            if not dry:
                tmp = tgt + '.d6tmp'
                with open(tmp, 'wb') as fh:
                    fh.write(data)
                os.replace(tmp, tgt)
    log('%s %d files: %s' % ('would write' if dry else 'wrote', len(changed), ', '.join(changed) or '-'))
    return changed


def selftest(gamedir):
    bad = n = 0
    for rel, cls in table_files(gamedir):
        data = open(os.path.join(gamedir, rel), 'rb').read()
        try:
            again = import_table(export_table(data, cls, rel), cls)
        except Exception as e:
            again = repr(e)
        n += 1
        if again != data:
            bad += 1
            print('MISMATCH', rel)
    print('%d tables, %d mismatches' % (n, bad))
    return bad == 0


def main(argv):
    a = argv[1:]
    if len(a) >= 2 and a[0] == 'selftest':
        return 0 if selftest(a[1]) else 1
    if len(a) >= 3 and a[0] == 'export':
        only = a[a.index('--only') + 1] if '--only' in a else None
        export_all(a[1], a[2], only)
        return 0
    if len(a) >= 3 and a[0] == 'import':
        import_all(a[1], a[2], '--dry-run' in a)
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
