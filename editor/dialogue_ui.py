"""
d6edit - NPC and guildmaster dialogue (NPCDATA.PAK / GMDATA.PAK).

Each script is shown as assembly (formats/d6npc.py): a .strings section
(what the NPC says, reply options), a .keywords section (words the player can
type or click) and the .code. Apply assembles it; File > Save writes the PAK
(with a backup), like the databases.
"""
import os
import sys

from qtcompat import QtCore, QtGui, QtWidgets

HERE = os.path.dirname(os.path.abspath(__file__))
FORMATS = os.path.join(os.path.dirname(HERE), 'formats')
if FORMATS not in sys.path:
    sys.path.insert(0, FORMATS)
import d6npc  # noqa: E402


def reference(gm):
    ops = d6npc.GMOPS if gm else d6npc.OPS
    ex = d6npc.GMEXPR if gm else d6npc.EXPR
    lines = ['Instructions (operands):']
    for op in sorted(ops):
        mn, spec = ops[op]
        if mn.startswith('NOP') or mn.startswith('NYI'):
            continue
        lines.append('  %-15s %s' % (mn, ', '.join(spec)))
    lines += ['', 'Expressions (IF, val):']
    for k in sorted(ex):
        n, spec = ex[k]
        lines.append('  %s(%s)' % (n, spec))
    lines += ['', 'val = number or expression; who: -1 PC talking, -2 this NPC,',
              '  -3 its target, 0-5 party slot.  IF/IFKEY: when true the next',
              '  line (a JUMP) is skipped: IF c / JUMP then / JUMP else.',
              'SAY, REPLY, WAIT, BYE, LEAVE wait for the game. REPLY options',
              '  are consecutive strings; ONREPLY [label per option] follows.',
              'ONACT slots: ' + ', '.join('%d %s' % kv for kv in sorted(
                  (d6npc.GM_ACT_SLOTS if gm else d6npc.ACT_SLOTS).items())),
              'MODPC kinds: ' + ', '.join(sorted(d6npc.GM_MODPC_NAMES if gm else d6npc.MODPC_NAMES)),
              'Strings: $N = PC name, @ = new message, ~word~ = keyword.']
    if gm:
        lines += ['', 'Guildmasters:',
                  '  A script runs from the start on entry (init: set ONACT 6),',
                  '  on each greeting (a PC steps up) and on each button press',
                  '  (ONACT 6, ACTBUTTON() = the button, ACTITEM() = the item).',
                  '  GMFLAGs reset on every visit; keep state in QFLAG, NPCREG,',
                  '  WSTATE, UBIT. EXIT leaves the building. SAY "" never returns.',
                  '  ROLE(-1) is broken in the game: use ROLE(0..5).',
                  'Main buttons (ACTBUTTON):']
        lines += ['  %s: %s' % kv for kv in d6npc.GM_BUTTONS.items()]
        lines += ['Results: ' + d6npc.GM_RESULTS]
    return '\n'.join(lines)


class DialogueEditor(QtWidgets.QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle('NPC dialogue')
        self.resize(1300, 820)
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.kind = QtWidgets.QComboBox()
        self.kind.addItems(['NPCs (NPCDATA.PAK)', 'Guildmasters (GMDATA.PAK)'])
        self.kind.currentIndexChanged.connect(lambda _: self.fill())
        top.addWidget(self.kind)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('search names and text')
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search, 1)
        lay.addLayout(top)
        split = QtWidgets.QSplitter()
        self.list = QtWidgets.QListWidget()
        self.list.currentRowChanged.connect(self._sel)
        split.addWidget(self.list)
        mid = QtWidgets.QWidget()
        ml = QtWidgets.QVBoxLayout(mid)
        ml.setContentsMargins(0, 0, 0, 0)
        self.title = QtWidgets.QLabel()
        ml.addWidget(self.title)
        self.edit = QtWidgets.QPlainTextEdit()
        f = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.edit.setFont(f)
        self.edit.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        ml.addWidget(self.edit, 1)
        row = QtWidgets.QHBoxLayout()
        for text, slot in (('Apply', self.apply), ('Revert', self.revert), ('New from template', self.template),
                           ('Check', self.check)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        ml.addLayout(row)
        self.used = QtWidgets.QLabel()
        self.used.setWordWrap(True)
        self.used.setStyleSheet('color: #9ab')
        ml.addWidget(self.used)
        split.addWidget(mid)
        self.ref = QtWidgets.QPlainTextEdit()
        self.ref.setReadOnly(True)
        self.ref.setFont(f)
        split.addWidget(self.ref)
        split.setSizes([260, 760, 300])
        lay.addWidget(split, 1)
        self.msg = QtWidgets.QLabel()
        self.msg.setStyleSheet('color: #e5c07b')
        lay.addWidget(self.msg)
        self.cur = None
        self.fill()

    # ----------------------------------------------------------------- data
    def gm(self):
        return self.kind.currentIndex() == 1

    def fname(self):
        return 'GMDATA.PAK' if self.gm() else 'NPCDATA.PAK'

    def pak(self):
        g = self.win.game
        fn = self.fname()
        if fn not in g.db:
            g.db[fn] = d6npc.DialoguePak.load(g.root, self.gm())
        return g.db[fn]

    @staticmethod
    def gm_label(n):
        info = d6npc.GM_INFO.get(n)
        if info is None:
            return 'Guildmaster %d' % n
        town, building, name = info
        where = building if town is None else '%s, %s' % (building, d6npc.TOWNS[town])
        return '%s (%s)' % (name or building, where)

    def names(self):
        if self.gm():
            return {}
        try:
            return d6npc.npc_names(table=self.win.game.table('D6NPC.DAT'))
        except Exception:
            return {}

    def fill(self, select=None):
        dp = self.pak()
        names = self.names()
        self.list.blockSignals(True)
        self.list.clear()
        for n in range(dp.count()):
            sc = dp.script(n)
            label = '%3d  %s%s' % (n, names.get(n, self.gm_label(n) if self.gm() else ''),
                                   '' if sc.code else '  (empty)')
            it = QtWidgets.QListWidgetItem(label)
            it.setData(QtCore.Qt.UserRole, n)
            it.setData(QtCore.Qt.UserRole + 1, ' '.join(sc.strings + sc.keywords).lower())
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.ref.setPlainText(reference(self.gm()))
        self._filter(self.search.text())
        row = 1 if select is None else select
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))

    def _filter(self, t):
        t = t.lower()
        for r in range(self.list.count()):
            it = self.list.item(r)
            it.setHidden(bool(t) and t not in it.text().lower() and t not in it.data(QtCore.Qt.UserRole + 1))

    def _sel(self, row):
        if row < 0:
            return
        n = self.list.item(row).data(QtCore.Qt.UserRole)
        self.cur = n
        sc = self.pak().script(n)
        self.title.setText('<b>%s %d</b> %s' % ('Guildmaster' if self.gm() else 'NPC', n,
                                               self.gm_label(n) if self.gm() else self.names().get(n, '')))
        try:
            self.edit.setPlainText(sc.disassemble() if sc.code else '; empty: use "New from template"\n')
            self.msg.setText('')
        except d6npc.ScriptError as e:
            self.edit.setPlainText('; cannot decode: %s\n' % e)
        self.used.setText(self.users(n))

    def users(self, n):
        if self.gm():
            info = d6npc.GM_INFO.get(n)
            if info is None:
                return 'Not used by any building.'
            return 'Used by: %s. Buttons: %s' % (self.gm_label(n), d6npc.GM_BUTTONS.get(info[1], 'none (intro)'))
        try:
            mons = self.win.game.mons
            who = ['%d %s' % (i, r.name.strip()) for i, r in enumerate(mons.records, 1)
                   if getattr(r, 'npc', 0) == n]
        except Exception:
            who = []
        return ('Monsters with this NPC id: ' + ', '.join(who)) if who else \
            'No monster record uses this NPC id (set the monster field "npc").'

    # -------------------------------------------------------------- actions
    def _assemble(self):
        try:
            return d6npc.assemble(self.edit.toPlainText(), self.gm())
        except d6npc.ScriptError as e:
            self.msg.setText('Error: %s' % e)
            m = __import__('re').match(r'line (\d+)', str(e))
            if m:
                blk = self.edit.document().findBlockByNumber(int(m.group(1)) - 1)
                cur = self.edit.textCursor()
                cur.setPosition(blk.position())
                self.edit.setTextCursor(cur)
            return None

    def check(self):
        sc = self._assemble()
        if sc is not None:
            try:
                sc.decode()
                self.msg.setText('OK: %d bytes of code, %d strings, %d keywords'
                                 % (len(sc.code), len(sc.strings), len(sc.keywords)))
            except d6npc.ScriptError as e:
                self.msg.setText('Error: %s' % e)

    def apply(self):
        if self.cur is None:
            return
        sc = self._assemble()
        if sc is None:
            return
        self.pak().set_script(self.cur, sc)
        self.win.game.db_dirty.add(self.fname())
        self.win._title()
        self.edit.setPlainText(self.pak().script(self.cur).disassemble())
        self.msg.setText('Applied %s %d (File > Save writes %s)' % (
            'guildmaster' if self.gm() else 'NPC', self.cur, self.fname()))

    def revert(self):
        if self.cur is not None:
            self._sel(self.list.currentRow())

    def template(self):
        t = d6npc.GM_TEMPLATE if self.gm() else d6npc.TEMPLATE
        if self.gm() and self.cur is not None:
            b = d6npc.GM_INFO.get(self.cur, (None, '', ''))[1]
            t = '; buttons of this building (ACTBUTTON): %s\n' % d6npc.GM_BUTTONS.get(b, 'see the reference') + t
        self.edit.setPlainText(t)
        self.msg.setText('Template loaded; Apply to store it in this slot.')
