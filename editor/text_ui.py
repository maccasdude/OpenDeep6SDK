"""
d6edit - game text: TEXTPAK.000 messages (TEXTMSG events, READ texts of
items) and D6STRING.DAT strings (names of guilds, spells, places ...).

In messages ']' is a line break and '@' a paragraph break (shown here as
real line breaks: one newline = ']', blank line = '@').
"""
from qtcompat import QtCore, QtWidgets



def pak_to_edit(t):
    return (t or '').replace('@', '\n\n').replace(']', '\n')


def edit_to_pak(t):
    return t.replace('\r', '').replace('\n\n', '@').replace('\n', ']')


class TextEditor(QtWidgets.QDialog):
    FILES = [('TEXTPAK.000', 'Messages (TEXTPAK)'), ('D6STRING.DAT', 'Strings (D6STRING)')]

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle('Game text')
        self.resize(1000, 650)
        lay = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabBar()
        for f, n in self.FILES:
            self.tabs.addTab(n)
        self.tabs.currentChanged.connect(lambda _: self.fill())
        lay.addWidget(self.tabs)
        body = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('search text or id')
        self.search.textChanged.connect(self._filter)
        left.addWidget(self.search)
        self.list = QtWidgets.QListWidget()
        self.list.currentRowChanged.connect(self._sel)
        left.addWidget(self.list, 1)
        b = QtWidgets.QPushButton('New entry')
        b.clicked.connect(self.new_entry)
        left.addWidget(b)
        lw = QtWidgets.QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(420)
        body.addWidget(lw)
        right = QtWidgets.QVBoxLayout()
        self.idlabel = QtWidgets.QLabel()
        right.addWidget(self.idlabel)
        self.edit = QtWidgets.QPlainTextEdit()
        right.addWidget(self.edit, 1)
        self.used = QtWidgets.QLabel()
        self.used.setWordWrap(True)
        self.used.setStyleSheet('color: #9ab')
        right.addWidget(self.used)
        b = QtWidgets.QPushButton('Apply')
        b.clicked.connect(self.apply)
        right.addWidget(b)
        hint = QtWidgets.QLabel('Messages: a single line break is a new line in the game, an empty line a new '
                                'paragraph. TEXTMSG triggers show a message by its id (parameter MSG); items '
                                'show one with READ (database field readtext).')
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #8a94a0')
        right.addWidget(hint)
        body.addLayout(right, 1)
        lay.addLayout(body, 1)
        self.msg = QtWidgets.QLabel()
        self.msg.setStyleSheet('color: #e5c07b')
        lay.addWidget(self.msg)
        self.cur = None
        self.fill()

    def fname(self):
        return self.FILES[self.tabs.currentIndex()][0]

    def data(self):
        return self.win.game.text_file(self.fname())

    def items(self):
        t = self.data()
        if t is None:
            return []
        if self.fname() == 'TEXTPAK.000':
            return [(e[0], t.text(e[0]) or '') for e in t.dir]
        return [(e[0], t.strings.get(e[0], '')) for e in t.entries]

    def fill(self, select=None):
        self.list.blockSignals(True)
        self.list.clear()
        for i, txt in self.items():
            it = QtWidgets.QListWidgetItem('%5d  %s' % (i, txt.replace(']', ' ').replace('@', ' ')[:70]))
            it.setData(QtCore.Qt.UserRole, i)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self._filter(self.search.text())
        if select is not None:
            for r in range(self.list.count()):
                if self.list.item(r).data(QtCore.Qt.UserRole) == select:
                    self.list.setCurrentRow(r)
                    break
        elif self.list.count():
            self.list.setCurrentRow(0)

    def _filter(self, t):
        t = t.lower()
        for r in range(self.list.count()):
            it = self.list.item(r)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _sel(self, row):
        if row < 0:
            return
        i = self.list.item(row).data(QtCore.Qt.UserRole)
        self.cur = i
        d = dict(self.items())
        self.idlabel.setText('<b>%s id %d</b>' % (self.fname(), i))
        self.edit.setPlainText(pak_to_edit(d[i]) if self.fname() == 'TEXTPAK.000' else d[i])
        if self.fname() == 'TEXTPAK.000':
            self.used.setText(self.win.text_users(i))
        else:
            self.used.setText('')

    def apply(self):
        if self.cur is None:
            return
        t = self.data()
        txt = self.edit.toPlainText()
        try:
            if self.fname() == 'TEXTPAK.000':
                b = edit_to_pak(txt).encode('latin1') + b'\0'
                t.texts[self.cur] = b
            else:
                txt.encode('latin1')
                t.set(self.cur, txt)
        except UnicodeEncodeError:
            self.msg.setText('Only Latin-1 characters can be used.')
            return
        t.dirty = True
        self.win.game.db_dirty.add(self.fname())
        self.win._title()
        self.fill(select=self.cur)
        self.msg.setText('Changed %s id %d (File > Save writes it)' % (self.fname(), self.cur))

    def new_entry(self):
        t = self.data()
        if t is None:
            return
        if self.fname() == 'TEXTPAK.000':
            nid = max([e[0] for e in t.dir] + [0]) + 10
            t.dir.append([nid, 0, 0])
            t.dir.sort()
            t.texts[nid] = b'New message\0'
        else:
            nid = max([e[0] for e in t.entries] + [0]) + 1
            t.set(nid, 'New string')
        t.dirty = True
        self.win.game.db_dirty.add(self.fname())
        self.win._title()
        self.fill(select=nid)
        self.msg.setText('Added id %d' % nid)
