"""Mod project workflow end to end on a small game folder made of real game
files: two mods patching different spell records of deep6.exe (stacked), a
string change and a new file, conflicts, uninstall order, pack / install zip.
Usage: python3 tests/test_mod.py GAMEDIR"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'formats'))
import d6data  # noqa: E402
import d6efx   # noqa: E402
import d6mod   # noqa: E402

game = sys.argv[1] if len(sys.argv) > 1 else '.'
tmp = tempfile.mkdtemp()
quiet = lambda *a: None  # noqa: E731


def mk_pristine():
    p = os.path.join(tmp, 'pristine')
    os.makedirs(os.path.join(p, 'save'))
    for f in ('deep6.exe', 'D6STRING.DAT'):
        shutil.copy2(d6data.find_file(game, f), os.path.join(p, f))
    open(os.path.join(p, 'save', 'game00.sav'), 'wb').write(b'save')
    return p


def tree_sha(d):
    return dict((r.lower(), d6mod.sha1_file(os.path.join(d, r))) for r in d6mod.walk(d)
                if not r.startswith('_mods/'))


def set_mana(work, spell_index, mana):
    p = os.path.join(work, 'deep6.exe')
    exe = open(p, 'rb').read()
    st = d6efx.SpellTable.from_exe(exe)
    st.spells[spell_index].mana = mana
    d6mod.write_new(p, st.patch_exe(exe))


pristine = mk_pristine()
before = tree_sha(pristine)

# mod A: mana of spell 0, a renamed spell, a new file, and a save change (ignored)
wa = os.path.join(tmp, 'workA')
d6mod.workcopy(pristine, wa, link=True, log=quiet)
set_mana(wa, 0, 77)
t = d6data.StringTable.parse(open(os.path.join(wa, 'D6STRING.DAT'), 'rb').read())
t.set(9019, 'Fire Spark')
d6mod.write_new(os.path.join(wa, 'D6STRING.DAT'), t.build())
os.makedirs(os.path.join(wa, 'sounds'))
open(os.path.join(wa, 'sounds', 'new.wav'), 'wb').write(b'RIFF')
d6mod.write_new(os.path.join(wa, 'save', 'game00.sav'), b'changed')
assert tree_sha(pristine) == before, 'work copy edits leaked into the pristine game (hard links)'
A = d6mod.Project.create(os.path.join(tmp, 'modA'), pristine, 'modA', log=quiet)
ch = dict((r.lower(), w) for r, w in A.changes(wa))
assert ch == {'deep6.exe': 'changed', 'd6string.dat': 'changed', 'sounds/new.wav': 'new'}, ch
files = A.capture(wa, pristine, log=quiet)
kinds = dict((r.lower(), e['kind']) for r, e in files.items())
assert kinds['deep6.exe'] == 'patch' and kinds['sounds/new.wav'] == 'file', kinds
assert os.path.getsize(os.path.join(A.root, 'patches', 'deep6.exe.json')) < 2000

# mod B: mana of spell 5 (other bytes of deep6.exe); mod C: same bytes as A
wb = os.path.join(tmp, 'workB')
d6mod.workcopy(pristine, wb, log=quiet)
set_mana(wb, 5, 55)
B = d6mod.Project.create(os.path.join(tmp, 'modB'), pristine, 'modB', log=quiet)
B.capture(wb, pristine, log=quiet)
wc = os.path.join(tmp, 'workC')
d6mod.workcopy(pristine, wc, log=quiet)
set_mana(wc, 0, 99)
C = d6mod.Project.create(os.path.join(tmp, 'modC'), pristine, 'modC', log=quiet)
C.capture(wc, pristine, log=quiet)

# install A and B into a fresh game
g = os.path.join(tmp, 'game')
shutil.copytree(pristine, g)
d6mod.install(A, g, log=quiet)
d6mod.install(B, g, log=quiet)
st = d6efx.SpellTable.from_exe(open(os.path.join(g, 'deep6.exe'), 'rb').read())
assert st.spells[0].mana == 77 and st.spells[5].mana == 55, 'stacked patches'
assert os.path.exists(os.path.join(g, 'sounds', 'new.wav'))
try:
    d6mod.install(C, g, log=quiet)
    raise AssertionError('conflicting patch installed')
except d6mod.ModError:
    pass
try:
    d6mod.uninstall('modA', g, log=quiet)
    raise AssertionError('uninstalled under a later mod')
except d6mod.ModError:
    pass
d6mod.uninstall('modB', g, log=quiet)
d6mod.uninstall('modA', g, log=quiet)
assert tree_sha(g) == before, 'uninstall restores the game'
assert not os.path.exists(os.path.join(g, 'sounds', 'new.wav'))

# pack and install from the zip
z = os.path.join(tmp, 'modA.zip')
d6mod.pack(A, z, log=quiet)
P = d6mod.Project(d6mod.unpack(z, os.path.join(tmp, 'unz')))
d6mod.install(P, g, log=quiet)
assert d6mod.sha1_file(os.path.join(g, 'D6STRING.DAT')) == d6mod.sha1_file(os.path.join(wa, 'D6STRING.DAT'))
d6mod.uninstall('modA', g, log=quiet)
assert tree_sha(g) == before
shutil.rmtree(tmp)
print('mod workflow OK')
