#!/usr/bin/env python3
"""
d6mod.py - mod projects: keep a mod as the set of files it changes, install
it into a game folder and take it out again.

Workflow
  1. d6mod.py workcopy PRISTINE WORK        a working copy of the game to edit
                                            (--link: hard links for files the
                                            game never writes; saves are copied)
  2. d6mod.py new PROJECT PRISTINE --name N a mod project; it records the
                                            hashes of the pristine game (base)
  3. edit WORK with d6edit / the tools, test with the game on WORK
  4. d6mod.py capture PROJECT WORK          store what differs from the base
     (d6mod.py status PROJECT WORK          lists it without storing)
  5. d6mod.py install PROJECT GAMEDIR       apply to any game folder (backups
     d6mod.py uninstall NAME GAMEDIR        in GAMEDIR/_mods/NAME), restore it
     d6mod.py list GAMEDIR
  6. d6mod.py pack PROJECT OUT.zip          for distribution

Project layout
  mod.json        name, version, author, description, files: {path: entry}
  base.json       {path: [size, sha1]} of the pristine game (lower-case paths)
  files/PATH      whole files (new files and larger changes)
  patches/PATH.json  byte patches of files whose size did not change and
                  that changed in small ranges (deep6.exe tables, records):
                  {"base": sha1, "result": sha1, "ranges": [[off, old_hex, new_hex]]}.
                  A patch applies when the old bytes match, so two mods that
                  patch different ranges of one file can both be installed.

Entries in mod.json: {"kind": "file"|"patch"|"delete", "base": sha1 or null,
"sha1": result sha1}. Paths use '/' and the case of the working copy; on
install they are matched without case.

Runtime files the game or the editor writes (save/, JOURNAL.*, PCSNAP.*,
D6SEGnn.GAM, maps/*.fog / *.mrk, backups) are never captured.
"""
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile

FORMAT = 1
# files the game (or the tools) write while running; never captured, always
# copied by workcopy --link (traced: the game truncates gameopt.dat, default.kbd,
# D6ARCHIV.DAT, ROSTER.DAT, debug.log in place)
RUNTIME = ['save/*', 'journal.*', 'pcsnap.*', 'd6seg*.gam', 'maps/*.fog', 'maps/*.mrk',
           'd6archiv.dat', 'd6world.dat', 'roster.dat', 'restarch.$$$', 'debug.log',
           'gameopt.dat', 'default.kbd',
           'd6edit_backup/*', '_mods/*', '*.bak', '*.tmp', '*.new', '*.d6tmp', 'config/*', 'savegfx/s_*']
# data files the game also rewrites while playing (shop stock): captured, but
# playing in the working copy changes them too
GAME_WRITES = ['d6smit*.dat']
PATCH_MAX_FRACTION = 0.10    # same-size files with at most this share changed -> byte patch
PATCH_GAP = 16               # merge changed ranges closer than this


class ModError(Exception):
    pass


# ---------------------------------------------------------------- helpers
def sha1_file(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for blk in iter(lambda: f.read(1 << 20), b''):
            h.update(blk)
    return h.hexdigest()


def is_runtime(rel):
    r = rel.lower()
    return any(fnmatch.fnmatch(r, p) for p in RUNTIME)


def walk(root):
    """Relative paths ('/' separated) of all files under root."""
    out = []
    for dp, ds, fs in os.walk(root):
        ds.sort()
        for f in sorted(fs):
            out.append(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, '/'))
    return out


def resolve(root, rel):
    """Path of rel under root, matching each part without case; for a missing
    file the existing directories keep their case and the rest is rel's."""
    cur = root
    parts = rel.split('/')
    for i, p in enumerate(parts):
        nxt = os.path.join(cur, p)
        if not os.path.exists(nxt) and os.path.isdir(cur):
            low = p.lower()
            for e in os.listdir(cur):
                if e.lower() == low:
                    nxt = os.path.join(cur, e)
                    break
        cur = nxt
    return cur


def write_new(path, data):
    """Write as a new file (new inode: never through a hard link)."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.d6mod.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def diff_ranges(a, b):
    """[(off, old, new)] covering the bytes where a and b (same length) differ."""
    import numpy as np
    x = np.frombuffer(a, np.uint8)
    y = np.frombuffer(b, np.uint8)
    idx = np.nonzero(x != y)[0]
    out = []
    if not len(idx):
        return out
    s = e = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - e > PATCH_GAP:
            out.append((s, a[s:e + 1], b[s:e + 1]))
            s = i
        e = i
    out.append((s, a[s:e + 1], b[s:e + 1]))
    return out


# ---------------------------------------------------------------- base index
def snapshot(gamedir, log=print):
    idx = {}
    for rel in walk(gamedir):
        if is_runtime(rel):
            continue
        p = os.path.join(gamedir, rel)
        idx[rel.lower()] = [os.path.getsize(p), sha1_file(p)]
    log('indexed %d files' % len(idx))
    return idx


def workcopy(pristine, work, link=False, log=print):
    """Copy the game to work. With link, files are hard-linked except the ones
    the game writes in place (runtime files are always copied)."""
    if os.path.exists(work) and os.listdir(work):
        raise ModError('%s is not empty' % work)
    n = nl = 0
    for rel in walk(pristine):
        src = os.path.join(pristine, rel)
        dst = os.path.join(work, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if link and not is_runtime(rel):
            try:
                os.link(src, dst)
                nl += 1
            except OSError:          # file system without hard links (FAT, network shares): copy
                shutil.copy2(src, dst)
        else:
            shutil.copy2(src, dst)
        n += 1
    log('%d files (%d hard links) in %s' % (n, nl, work))
    if link:
        log('note: tools must replace files (new inode), not write into them; '
            'the SDK tools and d6edit do')


# ---------------------------------------------------------------- project
class Project(object):
    def __init__(self, root):
        self.root = root
        mp = os.path.join(root, 'mod.json')
        if not os.path.exists(mp):
            raise ModError('%s has no mod.json' % root)
        self.meta = json.load(open(mp))
        self.base = json.load(open(os.path.join(root, 'base.json')))

    @classmethod
    def create(cls, root, pristine, name, author='', description='', log=print):
        if os.path.exists(os.path.join(root, 'mod.json')):
            raise ModError('%s already has a mod.json' % root)
        os.makedirs(root, exist_ok=True)
        base = snapshot(pristine, log)
        json.dump(base, open(os.path.join(root, 'base.json'), 'w'), indent=0, sort_keys=True)
        meta = dict(format=FORMAT, name=name, version='0.1', author=author, description=description,
                    created=time.strftime('%Y-%m-%d'), files={})
        json.dump(meta, open(os.path.join(root, 'mod.json'), 'w'), indent=2, sort_keys=True)
        return cls(root)

    @property
    def name(self):
        return self.meta['name']

    def save(self):
        json.dump(self.meta, open(os.path.join(self.root, 'mod.json'), 'w'), indent=2, sort_keys=True)

    # ------------------------------------------------------------ capture
    def changes(self, work):
        """[(rel, what)] what = 'new' | 'changed' | 'deleted', against the base;
        'changed*' = a file the game also rewrites while playing (shop stock)."""
        out = []
        seen = set()
        for rel in walk(work):
            if is_runtime(rel):
                continue
            k = rel.lower()
            seen.add(k)
            p = os.path.join(work, rel)
            b = self.base.get(k)
            if b is None:
                out.append((rel, 'new'))
            elif os.path.getsize(p) != b[0] or sha1_file(p) != b[1]:
                played = any(fnmatch.fnmatch(k, g) for g in GAME_WRITES)
                out.append((rel, 'changed*' if played else 'changed'))
        for k in sorted(set(self.base) - seen):
            out.append((k, 'deleted'))
        return out

    def capture(self, work, pristine=None, log=print):
        """Store the working copy's changes in the project (replaces what was
        captured before). pristine (optional) is needed for byte patches of
        changed files; without it changed files are stored whole."""
        for d in ('files', 'patches'):
            p = os.path.join(self.root, d)
            if os.path.isdir(p):
                shutil.rmtree(p)
        files = {}
        for rel, what in self.changes(work):
            k = rel.lower()
            if what == 'deleted':
                files[rel] = dict(kind='delete', base=self.base[k][1], sha1=None)
                continue
            p = os.path.join(work, rel)
            data = open(p, 'rb').read()
            sha = hashlib.sha1(data).hexdigest()
            base = self.base.get(k)
            entry = dict(kind='file', base=base[1] if base else None, sha1=sha)
            if base and pristine and base[0] == len(data):
                op = resolve(pristine, rel)
                old = open(op, 'rb').read() if os.path.exists(op) else None
                if old is not None and hashlib.sha1(old).hexdigest() == base[1]:
                    rng = diff_ranges(old, data)
                    if sum(len(n) for _, _, n in rng) <= PATCH_MAX_FRACTION * len(data):
                        pp = os.path.join(self.root, 'patches', rel + '.json')
                        os.makedirs(os.path.dirname(pp), exist_ok=True)
                        json.dump(dict(base=base[1], result=sha,
                                       ranges=[[o, a.hex(), b.hex()] for o, a, b in rng]),
                                  open(pp, 'w'), indent=0)
                        entry['kind'] = 'patch'
                        files[rel] = entry
                        continue
            fp = os.path.join(self.root, 'files', rel)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            shutil.copy2(p, fp)
            files[rel] = entry
        self.meta['files'] = files
        self.meta['captured'] = time.strftime('%Y-%m-%d %H:%M')
        self.save()
        n = {}
        for e in files.values():
            n[e['kind']] = n.get(e['kind'], 0) + 1
        log('captured %d entries (%s)' % (len(files), ', '.join('%d %s' % (v, k) for k, v in sorted(n.items()))))
        return files

    # ------------------------------------------------------------ install
    def result_bytes(self, rel, entry, current):
        """New contents for rel given the current target bytes (or None)."""
        if entry['kind'] == 'file':
            return open(os.path.join(self.root, 'files', rel), 'rb').read()
        p = json.load(open(os.path.join(self.root, 'patches', rel + '.json')))
        if current is None:
            raise ModError('%s: the file to patch is missing' % rel)
        buf = bytearray(current)
        for off, old, new in p['ranges']:
            old, new = bytes.fromhex(old), bytes.fromhex(new)
            have = bytes(buf[off:off + len(old)])
            if have == new:
                continue              # already applied (same change from another mod)
            if have != old:
                raise ModError('%s: bytes at 0x%x differ from what the patch expects '
                               '(another mod or version changed them)' % (rel, off))
            buf[off:off + len(new)] = new
        return bytes(buf)


def _mods_dir(gamedir, name=None):
    p = os.path.join(gamedir, '_mods')
    return os.path.join(p, name) if name else p


def installed(gamedir):
    d = _mods_dir(gamedir)
    out = []
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            mp = os.path.join(d, n, 'installed.json')
            if os.path.exists(mp):
                out.append(json.load(open(mp)))
    return sorted(out, key=lambda m: m.get('seq', 0))      # install order


def install(project, gamedir, force=False, log=print):
    """Apply the project to gamedir. Whole-file entries need the target to be
    the base version (or absent for new files) unless force; patches need
    their old bytes."""
    if not project.meta.get('files'):
        raise ModError('the project has no captured files (run capture)')
    for m in installed(gamedir):
        if m['name'] == project.name:
            raise ModError('%s is already installed in %s (uninstall it first)' % (project.name, gamedir))
    owners = {}
    for m in installed(gamedir):
        for rel, e in m['files'].items():
            owners.setdefault(rel.lower(), []).append((m['name'], e['kind']))
    plan = []
    problems = []
    for rel, e in sorted(project.meta['files'].items()):
        tgt = resolve(gamedir, rel)
        cur = open(tgt, 'rb').read() if os.path.exists(tgt) else None
        cur_sha = hashlib.sha1(cur).hexdigest() if cur is not None else None
        try:
            if e['kind'] == 'delete':
                new = None
            else:
                new = project.result_bytes(rel, e, cur)
        except ModError as ex:
            problems.append(str(ex))
            continue
        if e['kind'] in ('file', 'delete') and cur_sha != e['base'] and cur_sha != e['sha1']:
            who = ', '.join(n for n, _ in owners.get(rel.lower(), [])) or 'another version or mod'
            problems.append('%s: the installed file is not the one this mod was made from (changed by %s)'
                            % (rel, who))
            if not force:
                continue
        plan.append((rel, tgt, cur, new))
    if problems and not force:
        raise ModError('cannot install %s:\n  ' % project.name + '\n  '.join(problems))
    md = _mods_dir(gamedir, project.name)
    bdir = os.path.join(md, 'backup')
    os.makedirs(bdir, exist_ok=True)
    seq = 1 + max([m.get('seq', 0) for m in installed(gamedir)] or [0])
    if project.meta.get('target') == 'ex':
        log('note: %s was made for OpenDWWandWExpanded (EX); the original game may not run all of it' % project.name)
    rec = dict(name=project.name, version=project.meta.get('version'), installed=time.strftime('%Y-%m-%d %H:%M'), seq=seq,
               target=project.meta.get('target', 'standard'),
               source=os.path.abspath(project.root), files={})
    for rel, tgt, cur, new in plan:
        r = dict(kind=project.meta['files'][rel]['kind'], existed=cur is not None,
                 before=hashlib.sha1(cur).hexdigest() if cur is not None else None,
                 after=hashlib.sha1(new).hexdigest() if new is not None else None,
                 path=os.path.relpath(tgt, gamedir).replace(os.sep, '/'))
        if cur is not None:
            write_new(os.path.join(bdir, rel), cur)
        if new is None:
            if os.path.exists(tgt):
                os.remove(tgt)
        else:
            write_new(tgt, new)
        rec['files'][rel] = r
    json.dump(rec, open(os.path.join(md, 'installed.json'), 'w'), indent=2, sort_keys=True)
    log('installed %s: %d files%s' % (project.name, len(plan), (' (%d problems forced)' % len(problems)) if problems else ''))
    return rec


def uninstall(name, gamedir, force=False, log=print):
    md = _mods_dir(gamedir, name)
    mp = os.path.join(md, 'installed.json')
    if not os.path.exists(mp):
        raise ModError('%s is not installed in %s' % (name, gamedir))
    rec = json.load(open(mp))
    # mods installed later that touch the same files must go first
    later = [m for m in installed(gamedir) if m['name'] != name and m.get('seq', 0) > rec.get('seq', 0)
             and set(k.lower() for k in m['files']) & set(k.lower() for k in rec['files'])]
    problems = []
    for rel, r in rec['files'].items():
        tgt = os.path.join(gamedir, r['path'])
        cur = sha1_file(tgt) if os.path.exists(tgt) else None
        if cur != r['after']:
            problems.append('%s was changed after the install' % rel)
    if later and not force:
        raise ModError('uninstall %s first (installed later, same files)' % ', '.join(m['name'] for m in later))
    if problems and not force:
        raise ModError('cannot uninstall %s safely:\n  %s\n(force restores the backups anyway)'
                       % (name, '\n  '.join(problems)))
    bdir = os.path.join(md, 'backup')
    for rel, r in rec['files'].items():
        tgt = os.path.join(gamedir, r['path'])
        if r['existed']:
            if r['kind'] == 'patch' and later:
                log('warning: %s restored from the backup, later patches to it are lost' % rel)
            write_new(tgt, open(os.path.join(bdir, rel), 'rb').read())
        elif os.path.exists(tgt):
            os.remove(tgt)
    shutil.rmtree(md)
    log('uninstalled %s (%d files restored)' % (name, len(rec['files'])))


def pack(project, out, log=print):
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for rel in walk(project.root):
            if rel.startswith('.') or '/.' in rel:
                continue
            z.write(os.path.join(project.root, rel), '%s/%s' % (project.name, rel))
    log('wrote %s' % out)


def unpack(zpath, dest):
    """Extract a packed mod; returns the project folder."""
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        for n in names:
            if n.startswith('/') or '..' in n.split('/'):
                raise ModError('unsafe path in %s: %s' % (zpath, n))
        z.extractall(dest)
    top = names[0].split('/')[0]
    return os.path.join(dest, top)


# ---------------------------------------------------------------- CLI
def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog='d6mod.py', description='Mod projects for Wizards & Warriors')
    sub = ap.add_subparsers(dest='cmd')
    s = sub.add_parser('workcopy', help='make a working copy of the game')
    s.add_argument('pristine'); s.add_argument('work'); s.add_argument('--link', action='store_true')
    s = sub.add_parser('new', help='new mod project with the base of a pristine game')
    s.add_argument('project'); s.add_argument('pristine'); s.add_argument('--name', required=True)
    s.add_argument('--author', default=''); s.add_argument('--description', default='')
    s = sub.add_parser('status', help='list the changes of a working copy')
    s.add_argument('project'); s.add_argument('work')
    s = sub.add_parser('capture', help='store the changes of a working copy in the project')
    s.add_argument('project'); s.add_argument('work')
    s.add_argument('--pristine', help='pristine game (enables byte patches)')
    s = sub.add_parser('install'); s.add_argument('project'); s.add_argument('gamedir')
    s.add_argument('--force', action='store_true')
    s = sub.add_parser('uninstall'); s.add_argument('name'); s.add_argument('gamedir')
    s.add_argument('--force', action='store_true')
    s = sub.add_parser('list'); s.add_argument('gamedir')
    s = sub.add_parser('pack'); s.add_argument('project'); s.add_argument('out')
    a = ap.parse_args(argv[1:])
    try:
        if a.cmd == 'workcopy':
            workcopy(a.pristine, a.work, a.link)
        elif a.cmd == 'new':
            Project.create(a.project, a.pristine, a.name, a.author, a.description)
        elif a.cmd == 'status':
            for rel, what in Project(a.project).changes(a.work):
                print('%-8s %s' % (what, rel))
        elif a.cmd == 'capture':
            Project(a.project).capture(a.work, a.pristine)
        elif a.cmd == 'install':
            p = a.project
            if p.lower().endswith('.zip'):
                import tempfile
                p = unpack(p, tempfile.mkdtemp())
            install(Project(p), a.gamedir, a.force)
        elif a.cmd == 'uninstall':
            uninstall(a.name, a.gamedir, a.force)
        elif a.cmd == 'list':
            for m in installed(a.gamedir):
                print('%s %s (%d files, %s%s)' % (m['name'], m.get('version'), len(m['files']), m['installed'],
                                                  ', needs OpenDWWandWExpanded' if m.get('target') == 'ex' else ''))
        elif a.cmd == 'pack':
            pack(Project(a.project), a.out)
        else:
            ap.print_help()
            return 1
    except ModError as e:
        print('error:', e, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
