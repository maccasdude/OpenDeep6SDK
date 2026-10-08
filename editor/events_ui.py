"""
d6edit - event script editor (EVENTS.DCL / EVENTS.COD).
"""
import re

from qtcompat import QtCore, QtGui, QtWidgets

import d6data
import d6events


class AsmHighlighter(QtGui.QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        f = lambda c, bold=False: self._fmt(c, bold)
        self.rules = [
            (re.compile(r';.*$'), f('#7f8c8d')),
            (re.compile(r'^\s*[A-Za-z_]\w*:(?!\S)'), f('#e5c07b', True)),
            (re.compile(r'\bQ:'), f('#c678dd', True)),
            (re.compile(r'^\s*(?:Q:)?(' + '|'.join(sorted(d6events.MNEMONIC, key=len, reverse=True)) +
                        r'|OP_[0-9A-Fa-f]+)\b'), f('#61afef', True)),
            (re.compile(r'->\s*\w+'), f('#98c379')),
        ]

    @staticmethod
    def _fmt(color, bold):
        t = QtGui.QTextCharFormat()
        t.setForeground(QtGui.QColor(color))
        if bold:
            t.setFontWeight(QtGui.QFont.Bold)
        return t

    def highlightBlock(self, text):
        for rx, fm in self.rules:
            for m in rx.finditer(text):
                g = 1 if m.groups() else 0
                self.setFormat(m.start(g), m.end(g) - m.start(g), fm)


class EventEditor(QtWidgets.QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.es = win.game.events
        self.setWindowTitle('Event scripts (EVENTS.DCL / EVENTS.COD)')
        self.resize(1200, 760)
        self.cur = None
        lay = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()
        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText('filter events')
        self.filter.textChanged.connect(self._filter)
        left.addWidget(self.filter)
        self.list = QtWidgets.QListWidget()
        self.list.currentRowChanged.connect(self._select)
        left.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton('New')
        b.clicked.connect(self.new_event)
        row.addWidget(b)
        b = QtWidgets.QPushButton('Duplicate')
        b.clicked.connect(self.duplicate)
        row.addWidget(b)
        left.addLayout(row)
        lw = QtWidgets.QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(330)
        lay.addWidget(lw)

        mid = QtWidgets.QVBoxLayout()
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit()
        form.addRow('Name', self.name)
        self.desc = QtWidgets.QLineEdit()
        form.addRow('Description', self.desc)
        self.params = QtWidgets.QLineEdit()
        self.params.setToolTip('Comma separated. A leading ! marks a number that indexes another table '
                               '(world state, switch, box, trigger). Triggers supply the values in this order.')
        form.addRow('Parameters', self.params)
        self.users = QtWidgets.QLabel()
        self.users.setWordWrap(True)
        self.users.setStyleSheet('color: #9ab')
        form.addRow('Used by', self.users)
        mid.addLayout(form)
        self.code = QtWidgets.QPlainTextEdit()
        self.code.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.code.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.hl = AsmHighlighter(self.code.document())
        mid.addWidget(self.code, 1)
        self.msg = QtWidgets.QLabel()
        self.msg.setWordWrap(True)
        mid.addWidget(self.msg)
        row = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton('Check')
        b.clicked.connect(self.check)
        row.addWidget(b)
        self.apply_btn = QtWidgets.QPushButton('Apply')
        self.apply_btn.clicked.connect(self.apply)
        row.addWidget(self.apply_btn)
        row.addStretch(1)
        b = QtWidgets.QPushButton('Close')
        b.clicked.connect(self.close)
        row.addWidget(b)
        mid.addLayout(row)
        mw = QtWidgets.QWidget()
        mw.setLayout(mid)
        lay.addWidget(mw, 1)

        ref = QtWidgets.QVBoxLayout()
        ref.addWidget(QtWidgets.QLabel('<b>Instructions</b> (double click inserts)'))
        self.ops = QtWidgets.QTreeWidget()
        self.ops.setHeaderLabels(['Op', 'Operands', 'What it does'])
        self.ops.setRootIsDecorated(False)
        for op, (mn, tgt, note) in sorted(d6data.EV_OPS.items(), key=lambda kv: kv[1][0]):
            n = d6events.SEEN_COUNTS.get(op)
            q = 'Q:' if note.startswith('queued') else ''
            it = QtWidgets.QTreeWidgetItem([q + mn, '' if n is None else str(n), note + (' (needs -> label)' if tgt and not q else '')])
            it.setToolTip(2, note)
            self.ops.addTopLevelItem(it)
        self.ops.resizeColumnToContents(0)
        self.ops.itemDoubleClicked.connect(self._insert_op)
        ref.addWidget(self.ops, 1)
        rw = QtWidgets.QWidget()
        rw.setLayout(ref)
        rw.setMinimumWidth(380)
        lay.addWidget(rw)
        self.fill()

    # ------------------------------------------------------------------ list
    def fill(self, select=None):
        self.list.blockSignals(True)
        self.list.clear()
        for i in range(self.es.n):
            it = QtWidgets.QListWidgetItem(self.es.name(i))
            it.setToolTip(self.es.desc(i))
            it.setData(QtCore.Qt.UserRole, i)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self._filter(self.filter.text())
        if select is not None:
            self.list.setCurrentRow(select)
        elif self.list.count():
            self.list.setCurrentRow(0)

    def _filter(self, t):
        t = t.lower()
        for k in range(self.list.count()):
            it = self.list.item(k)
            i = it.data(QtCore.Qt.UserRole)
            hay = (self.es.name(i) + ' ' + self.es.desc(i)).lower()
            it.setHidden(bool(t) and t not in hay)

    def _select(self, row):
        if row < 0:
            return
        i = self.list.item(row).data(QtCore.Qt.UserRole)
        self.show_event(i)

    def show_event(self, i, name=None, params=None, desc=None, text=None):
        self.cur = i
        new = i >= self.es.n
        self.name.setText(name if name is not None else self.es.name(i))
        self.params.setText(', '.join(params if params is not None else self.es.params(i)))
        self.desc.setText(desc if desc is not None else self.es.desc(i))
        self.code.setPlainText(text if text is not None else self.es.source(i))
        self.msg.setText('New event (not saved yet)' if new else '')
        if new:
            self.users.setText('-')
        else:
            u = self.win.event_users(self.es.name(i))
            self.users.setText('%d trigger(s)%s' % (len(u), (': ' + ', '.join('spoke %d T%d' % x for x in u[:12]) +
                                                              (' ...' if len(u) > 12 else '')) if u else ''))

    def _insert_op(self, it, col):
        self.code.insertPlainText('    %s \n' % it.text(0))

    # ------------------------------------------------------------------ actions
    def _fields(self):
        params = [p.strip().upper() for p in self.params.text().split(',') if p.strip()]
        return self.name.text().strip().upper(), params, self.desc.text().strip(), self.code.toPlainText()

    def check(self):
        name, params, desc, text = self._fields()
        try:
            blob, warn = d6events.assemble(text, params, len(self.es.code))
        except d6events.AsmError as e:
            self._error(str(e), e.line)
            return False
        self.msg.setStyleSheet('color: #98c379')
        self.msg.setText('OK, %d bytes.%s' % (len(blob), ('\n' + '\n'.join(warn)) if warn else ''))
        return True

    def _error(self, text, line=None):
        self.msg.setStyleSheet('color: #e06c75')
        self.msg.setText(text)
        if line:
            c = self.code.textCursor()
            c.movePosition(QtGui.QTextCursor.Start)
            c.movePosition(QtGui.QTextCursor.Down, QtGui.QTextCursor.MoveAnchor, line - 1)
            c.select(QtGui.QTextCursor.LineUnderCursor)
            self.code.setTextCursor(c)

    def apply(self):
        if self.cur is None:
            return
        name, params, desc, text = self._fields()
        i = self.cur
        if i < self.es.n:
            u = self.win.event_users(self.es.name(i))
            old = self.es.params(i)
            if u and [p.upper() for p in old] != params:
                if QtWidgets.QMessageBox.question(
                        self, 'd6edit', '%d trigger(s) use this event and give their values in the old parameter '
                        'order (%s).\nChange the parameters anyway?' % (len(u), ', '.join(old))) != QtWidgets.QMessageBox.Yes:
                    return
            if u and name != self.es.name(i).upper():
                QtWidgets.QMessageBox.information(self, 'd6edit', 'Triggers refer to events by name: '
                                                  'renaming would cut them off. Use Duplicate for a new event.')
                return
        try:
            warn = self.es.set_event(i, name, params, desc, text)
        except d6events.AsmError as e:
            self._error(str(e), e.line)
            return
        except ValueError as e:
            self._error(str(e))
            return
        self.win.events_changed()
        self.fill(select=i)
        self.msg.setStyleSheet('color: #98c379')
        self.msg.setText('Applied (saved with File > Save).%s' % (('\n' + '\n'.join(warn)) if warn else ''))

    def new_event(self):
        i = self.es.n
        self.list.clearSelection()
        self.show_event(i, name='@NEWEVENT', params=['!STATE'], desc='',
                        text='; example: set a world state to 1\n    SETSTATE !STATE, p1\n    END\n')
        self.params.setText('!STATE, VALUE')
        self.code.setPlainText('    SETSTATE !STATE, VALUE\n    END\n')

    def duplicate(self):
        if self.cur is None or self.cur >= self.es.n:
            return
        i = self.cur
        self.show_event(self.es.n, name=self.es.name(i) + '2', params=self.es.params(i), desc=self.es.desc(i),
                        text=self.es.source(i))
