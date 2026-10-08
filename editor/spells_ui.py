"""
d6edit - spell table (deep6.exe 0x5D7D80, docs/formats/effects.md section 1).

School, level, slot, recovery, mana, targeting, flags, AI category and cast
animation of the 105 spells; Save patches deep6.exe (backed up first). The
names shown in the game are D6STRING.DAT strings 9000 + id - 1 (the game
copies them over the exe names at start), so the Name column edits those and
File > Save writes D6STRING.DAT. Damage and effects are hard-coded in the exe.
"""
import os
import shutil
import sys
import time

from qtcompat import QtCore, QtWidgets

HERE = os.path.dirname(os.path.abspath(__file__))
FORMATS = os.path.join(os.path.dirname(HERE), 'formats')
if FORMATS not in sys.path:
    sys.path.insert(0, FORMATS)
import d6efx  # noqa: E402

# (field, header, min, max, tooltip)
SCHOOLS = {-1: 'none', 0: 'Spirit', 1: 'Sun', 2: 'Moon', 3: 'Vine', 4: 'Stone', 5: 'Fiend'}
NAME_BASE = 9000         # D6STRING id of spell id 1

COLS = [('name', 'Name', 0, 0, 'D6STRING.DAT string 9000 + id - 1 (31 characters); File > Save writes it'),
        ('id', 'Id', None, None, 'external id (items, tomes, CAST events); read only'),
        ('school', 'School', -1, 5, '0 Spirit, 1 Sun, 2 Moon, 3 Vine, 4 Stone, 5 Fiend, -1 none'),
        ('level', 'Level', 1, 7, 'spellbook level 1..7'),
        ('slot', 'Slot', 1, 32, 'position within the level (school, level, slot must be unique)'),
        ('recover_ms', 'Recover ms', 0, 32767, 'recovery time after casting'),
        ('mana', 'Mana', 0, 32767, 'mana cost'),
        ('target', 'Target', 0, 2, '0 self/party, 1 needs a target, 2 Spirit Eye only (meaning unknown)'),
        ('flags', 'Flags', 0, 255, 'bit 0 line of sight; underwater: bit 1 fizzles, bit 2 hits the caster'),
        ('category', 'Category', 0, 9, '0/1 hostile, 2 heal, 3 cure, 4 buff, 5 resurrect, 6 mind, '
                                        '7 utility, 8 artifact, 9 trap/lock (ally AI)'),
        ('animseq', 'Cast anim', 100, 103, 'cast animation id 100..103')]


class SpellEditor(QtWidgets.QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.root = win.game.root
        self.setWindowTitle('Spells (deep6.exe)')
        self.resize(1000, 700)
        lay = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel('Damage, effects and sounds are hard-coded per spell id in the exe; '
                                'this table sets names, schools, levels, costs and targeting.')
        note.setWordWrap(True)
        lay.addWidget(note)
        self.table = QtWidgets.QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels([c[1] for c in COLS])
        for i, c in enumerate(COLS):
            self.table.horizontalHeaderItem(i).setToolTip(c[4])
        lay.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout()
        for text, slot in (('Save to deep6.exe', self.save), ('Reload', self.load)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.msg = QtWidgets.QLabel()
        self.msg.setStyleSheet('color: #e5c07b')
        lay.addWidget(self.msg)
        self.load()

    def exe_path(self):
        return os.path.join(self.root, 'deep6.exe')

    def strings(self):
        return self.win.game.text_file('D6STRING.DAT')

    def spell_name(self, s):
        t = self.strings()
        v = t.strings.get(NAME_BASE + s.id - 1) if t is not None else None
        return s.label if v is None else v

    def load(self):
        self.st = d6efx.SpellTable.from_exe(open(self.exe_path(), 'rb').read())
        t = self.table
        t.setRowCount(len(self.st.spells))
        for r, s in enumerate(self.st.spells):
            for c, (f, _, lo, hi, tip) in enumerate(COLS):
                v = self.spell_name(s) if f == 'name' else getattr(s, f)
                it = QtWidgets.QTableWidgetItem(str(v))
                it.setToolTip(tip)
                if lo is None:
                    it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable)
                t.setItem(r, c, it)
        t.resizeColumnsToContents()
        self.msg.setText('%d spells' % len(self.st.spells))

    def collect(self):
        """Table -> self.st (exe fields); returns {row: name}."""
        seen = {}
        names = {}
        for r, s in enumerate(self.st.spells):
            for c, (f, h, lo, hi, _) in enumerate(COLS):
                txt = self.table.item(r, c).text()
                if f == 'name':
                    try:
                        raw = txt.encode('latin1')
                    except UnicodeEncodeError:
                        raise ValueError('row %d: only Latin-1 characters' % (r + 1))
                    if len(raw) > 31:
                        raise ValueError('row %d: name longer than 31 characters' % (r + 1))
                    names[r] = txt
                elif lo is not None:
                    try:
                        v = int(txt)
                    except ValueError:
                        raise ValueError('row %d %s: not a number' % (r + 1, h))
                    if not lo <= v <= hi:
                        raise ValueError('row %d %s: %d outside %d..%d' % (r + 1, h, v, lo, hi))
                    setattr(s, f, v)
            if s.school >= 0:
                k = (s.school, s.level, s.slot)
                if k in seen:
                    raise ValueError('rows %d and %d share school %d level %d slot %d'
                                     % (seen[k] + 1, r + 1, *k))
                seen[k] = r
        return names

    def save(self):
        try:
            names = self.collect()
        except ValueError as e:
            self.msg.setText('Error: %s' % e)
            return
        renamed = 0
        t = self.strings()
        for r, txt in names.items():
            s = self.st.spells[r]
            if t is not None and txt != self.spell_name(s):
                t.set(NAME_BASE + s.id - 1, txt)
                renamed += 1
        if renamed:
            t.dirty = True
            self.win.game.db_dirty.add('D6STRING.DAT')
            if hasattr(self.win, '_title'):
                self.win._title()
        p = self.exe_path()
        exe = open(p, 'rb').read()
        new = self.st.patch_exe(exe)
        note = (' %d name(s) changed: File > Save writes D6STRING.DAT.' % renamed) if renamed else ''
        if new == exe:
            self.msg.setText(('No table changes.' + note).strip())
            return
        bd = os.path.join(self.root, 'd6edit_backup', time.strftime('%Y%m%d-%H%M%S'))
        os.makedirs(bd, exist_ok=True)
        shutil.copy2(p, os.path.join(bd, 'deep6.exe'))
        with open(p + '.tmp', 'wb') as f:
            f.write(new)
        os.replace(p + '.tmp', p)
        self.msg.setText('Saved deep6.exe (backup in %s).%s' % (bd, note))
