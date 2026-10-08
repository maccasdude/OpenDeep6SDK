"""
d6edit - game database editor: monsters, items, props, NPCs, treasure,
treasure lists, monster sounds (docs/formats/databases.md).

Edits go straight into the parsed tables (shared with the map editor) and are
written by File > Save. New records are appended (record numbers are ids:
existing ones never move).
"""
import struct

import numpy as np

from qtcompat import QtCore, QtGui, QtWidgets

import d6data
import d6model
import d6icons

TABS = [('D6MONS.DAT', 'Monsters', 'M'), ('D6ITEM.DAT', 'Items', 'I'), ('D6PROP.DAT', 'Props', 'P'),
        ('D6NPC.DAT', 'NPCs', None), ('D6TREAS.DAT', 'Treasure', None), ('D6TRLIST.DAT', 'Treasure lists', None),
        ('D6MONSND.DAT', 'Monster sounds', None)]
LIMITS = {'D6MONS.DAT': 999, 'D6ITEM.DAT': 999, 'D6PROP.DAT': 255, 'D6NPC.DAT': 159, 'D6TRLIST.DAT': 127}
MODEL_FIELD = {'D6MONS.DAT': ('gfx', 'mon', 'M'), 'D6ITEM.DAT': ('model', 'item', 'I'), 'D6PROP.DAT': ('model', 'prop', 'P')}
ENUM_FIELDS = {
    ('D6ITEM.DAT', 'type'): dict(enumerate(d6data.ITEM_TYPES)),
    ('D6ITEM.DAT', 'skill'): dict(enumerate(d6data.SKILLS)),
    ('D6MONS.DAT', 'mclass'): d6data.MON_CLASSES,
    ('D6MONS.DAT', 'clan'): dict(enumerate(d6data.CLANS)),
    ('D6MONS.DAT', 'role'): dict(enumerate(d6data.ROLES)),
    ('D6MONS.DAT', 'gender'): {0: 'male', 1: 'female'},
}
REC_REFS = {'treasureA': 'D6TREAS.DAT', 'treasureB': 'D6TREAS.DAT', 'companionA': 'D6MONS.DAT',
            'companionB': 'D6MONS.DAT', 'soundrec': 'D6MONSND.DAT'}


def fmt_n(fmt):
    import re
    if fmt.startswith('str'):
        return 1, 'str'
    m = re.match(r'^(\d*)([a-zA-Z])$', fmt)
    return int(m.group(1) or 1), m.group(2)


def rec_label(fname, i, r):
    try:
        if fname == 'D6TREAS.DAT':
            ents = [e for e in r.entries_list() if e['type']]
            return '%3d  %d entries' % (i, len(ents))
        if fname == 'D6MONSND.DAT':
            return '%3d  %s' % (i, r.get('label'))
        return '%3d  %s' % (i, r.get('name'))
    except Exception:
        return '%3d' % i


class RecordForm(QtWidgets.QScrollArea):
    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        self.setWidgetResizable(True)

    def show_record(self, fname, idx, rec):
        old = self.takeWidget()
        if old is not None:
            old.hide()
            old.deleteLater()
        w = QtWidgets.QWidget()
        lay = QtWidgets.QFormLayout(w)
        lay.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.setWidget(w)
        self.fname, self.idx, self.rec = fname, idx, rec
        if rec is None:
            return
        title = QtWidgets.QLabel('<b>%s record %d</b>' % (fname, idx))
        lay.addRow(title)
        mf = MODEL_FIELD.get(fname)
        if mf:
            self.preview = QtWidgets.QLabel()
            self.preview.setFixedHeight(180)
            self.preview.setAlignment(QtCore.Qt.AlignCenter)
            self.preview.setStyleSheet('background: #303338')
            lay.addRow(self.preview)
            self._update_preview()
        if fname == 'D6TREAS.DAT':
            self._treasure(lay, rec)
            return
        if fname == 'D6TRLIST.DAT':
            self._text(lay, rec, rec.field('name'))
            self._trlist(lay, rec)
            return
        if fname == 'D6MONSND.DAT':
            self._text(lay, rec, rec.field('modelno'))
            self._text(lay, rec, rec.field('label'))
            self._monsnd(lay, rec)
            return
        for f in rec.FIELDS:
            if f.name.startswith('rt_'):
                continue
            if mf and f.name == mf[0]:
                self._model_combo(lay, rec, f, mf)
            elif fname == 'D6ITEM.DAT' and f.name == 'icon':
                self._text(lay, rec, f)
                self._icon(lay, rec)
            elif (fname, f.name) in ENUM_FIELDS:
                self._enum(lay, rec, f, ENUM_FIELDS[(fname, f.name)])
            else:
                self._text(lay, rec, f)
        if fname == 'D6ITEM.DAT':
            self._bits(lay, rec, 'restrict', [c for c in d6data.CLANS[:10]] + list(d6data.ROLES[:15]) +
                       ['male', 'female', 'Evil', 'Neutral', 'Good'], 'Not usable by')

    # ---------------------------------------------------------------- widgets
    def _changed(self):
        self.dlg.changed(self.fname, self.idx)

    def _doc(self, lay, f):
        if f.doc:
            d = QtWidgets.QLabel(f.doc)
            d.setWordWrap(True)
            d.setStyleSheet('color: #8a94a0; font-size: 9pt')
            lay.addRow('', d)

    def _text(self, lay, rec, f):
        n, c = fmt_n(f.fmt)
        v = rec.get(f.name)
        if c == 'str':
            txt = v
        elif c == 's':
            txt = v.hex(' ')
        else:
            vals = v if isinstance(v, list) else [v]
            txt = ', '.join(('%g' % x) if c in 'fd' else str(x) for x in vals)
        e = QtWidgets.QLineEdit(txt)
        e.setToolTip('%s\n[%s at 0x%x, confidence %s]' % (f.doc, f.fmt, f.off, f.conf))
        if f.conf == 'low':
            e.setStyleSheet('color: #a0a0a0')

        def done(e=e, f=f, c=c, n=n):
            t = e.text()
            try:
                if c == 'str':
                    if len(t.encode('latin1')) >= int(f.fmt[3:]):
                        raise ValueError('too long (max %d)' % (int(f.fmt[3:]) - 1))
                    val = t
                elif c == 's':
                    val = bytes.fromhex(t)
                    if len(val) != n:
                        raise ValueError('need %d bytes' % n)
                else:
                    parts = [p for p in t.replace(',', ' ').split() if p]
                    if len(parts) != n:
                        raise ValueError('need %d value(s)' % n)
                    vals = [float(p) if c in 'fd' else int(p, 0) for p in parts]
                    val = vals if n > 1 else vals[0]
                old = bytes(rec.raw)
                rec.set(f.name, val)
                if bytes(rec.raw) != old:
                    self._changed()
                    if f.name == 'name':
                        self.dlg.relabel()
            except Exception as ex:
                self.dlg.msg('%s: %s' % (f.name, ex))
                return
        e.editingFinished.connect(done)
        label = f.name
        ref = REC_REFS.get(f.name)
        if ref and isinstance(v, int) and v > 0:
            t = self.dlg.win.game.table(ref)
            if t is not None and v <= len(t.records):
                label = '%s (%s)' % (f.name, rec_label(ref, v, t.records[v - 1]).split(None, 1)[-1].strip())
        lay.addRow(label, e)
        self._doc(lay, f)

    def _icon(self, lay, rec):
        root = self.dlg.win.game.root
        row = QtWidgets.QHBoxLayout()
        lab = QtWidgets.QLabel()
        lab.setFixedSize(96, 86)
        lab.setStyleSheet('background: #00ff00')
        row.addWidget(lab)
        col = QtWidgets.QVBoxLayout()
        b1 = QtWidgets.QPushButton('Replace icon image...')
        b2 = QtWidgets.QPushButton('Render icon from the model')
        col.addWidget(b1)
        col.addWidget(b2)
        row.addLayout(col)
        w = QtWidgets.QWidget()
        w.setLayout(row)
        lay.addRow('icon image', w)

        def show():
            p = d6icons.icon_path(root, rec.get('icon'))
            if p and __import__('os').path.exists(p):
                a = d6icons.load_icon(p)[..., :3]
                a = np.ascontiguousarray(a)
                qi = QtGui.QImage(a.data, a.shape[1], a.shape[0], a.shape[1] * 3, QtGui.QImage.Format_RGB888).copy()
                lab.setPixmap(QtGui.QPixmap.fromImage(qi).scaled(96, 86))
            else:
                lab.setText('none')

        def write(rgba):
            import os
            import shutil
            import time
            p = d6icons.icon_path(root, rec.get('icon'))
            if not p:
                self.dlg.msg('icon index %d has no file name' % rec.get('icon'))
                return
            users = [i for i, r in enumerate(self.dlg.win.game.items.records, 1) if r.get('icon') == rec.get('icon')]
            if len(users) > 1 and QtWidgets.QMessageBox.question(
                    self, 'd6edit', 'Icon %d is shared by %d items. Change it for all of them?' % (
                        rec.get('icon'), len(users))) != QtWidgets.QMessageBox.Yes:
                return
            if os.path.exists(p):
                bdir = os.path.join(root, 'd6edit_backup', time.strftime('%Y%m%d-%H%M%S'), 'itemicon')
                os.makedirs(bdir, exist_ok=True)
                shutil.copy2(p, bdir)
            d6icons.save_icon(p, rgba, size=d6icons.ICON_SIZE)
            show()
            self.dlg.msg('Wrote %s (old file backed up)' % p)

        def replace():
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Icon image', '', 'Images (*.png *.bmp *.jpg *.tga)')
            if p:
                from PIL import Image
                write(np.array(Image.open(p).convert('RGBA')))

        def render():
            try:
                m = d6model.load_model(root, d6model.model_for(root, 'I', self.idx))
            except Exception as e:
                self.dlg.msg('no model: %s' % e)
                return
            write(d6icons.render_icon(m))
        b1.clicked.connect(replace)
        b2.clicked.connect(render)
        show()

    def _enum(self, lay, rec, f, mapping):
        cb = QtWidgets.QComboBox()
        cur = rec.get(f.name)
        keys = sorted(mapping)
        if cur not in mapping:
            keys.append(cur)
        for k in keys:
            cb.addItem('%d  %s' % (k, mapping.get(k, '?')), k)
        cb.setCurrentIndex(keys.index(cur))

        def ch(i):
            rec.set(f.name, cb.itemData(i))
            self._changed()
        cb.currentIndexChanged.connect(ch)
        lay.addRow(f.name, cb)
        self._doc(lay, f)

    def _bits(self, lay, rec, field, names, label):
        box = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(box)
        g.setContentsMargins(0, 0, 0, 0)
        v = rec.get(field)
        for b, n in enumerate(names):
            c = QtWidgets.QCheckBox(n)
            c.setChecked(bool(v & (1 << b)))

            def tg(on, b=b):
                x = rec.get(field)
                rec.set(field, (x | (1 << b)) if on else (x & ~(1 << b)))
                self._changed()
                self.dlg.refresh_current()
            c.toggled.connect(tg)
            g.addWidget(c, b // 3, b % 3)
        lay.addRow(QtWidgets.QLabel('<b>%s</b>' % label))
        lay.addRow(box)

    def _model_combo(self, lay, rec, f, mf):
        field, kind, _ = mf
        table = d6data.exe_model_table(self.dlg.win.game.root, kind)
        cb = QtWidgets.QComboBox()
        cb.setEditable(True)
        cb.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        for i, name, ext, param in table:
            cb.addItem('%3d  %s%s' % (i, ('pc/' if kind == 'mon' and i < 17 else ''), name or '-'), i)
        cb.completer().setFilterMode(QtCore.Qt.MatchContains)
        cb.completer().setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        cur = rec.get(field)
        if 0 <= cur < cb.count():
            cb.setCurrentIndex(cur)

        def ch(i):
            v = cb.itemData(i)
            if v is None:
                return
            rec.set(field, v)
            self._changed()
            self._update_preview()
        cb.currentIndexChanged.connect(ch)
        lay.addRow('model (exe table)', cb)
        self._doc(lay, f)

    def _update_preview(self):
        mf = MODEL_FIELD.get(self.fname)
        if not mf:
            return
        game = self.dlg.win.game
        try:
            path = d6model.model_for(game.root, mf[2], self.idx)
        except Exception:
            path = None
        pm = QtGui.QPixmap()
        if path:
            try:
                m = d6model.load_model(game.root, path)
                img = np.ascontiguousarray(d6model.render(m, size=176, bg=(48, 51, 56))[..., :3], np.uint8)
                pm = QtGui.QPixmap.fromImage(QtGui.QImage(img.data, img.shape[1], img.shape[0], img.shape[1] * 3,
                                                          QtGui.QImage.Format_RGB888).copy())
            except Exception as ex:
                self.dlg.msg('preview: %s' % ex)
        self.preview.setPixmap(pm)
        self.preview.setToolTip(path or '')

    def _grid(self, lay, rows, cols, getter, setter, colnames, combos=None):
        tw = QtWidgets.QTableWidget(rows, cols)
        tw.setHorizontalHeaderLabels(colnames)
        tw.verticalHeader().setDefaultSectionSize(22)
        for r in range(rows):
            vals = getter(r)
            for c in range(cols):
                it = QtWidgets.QTableWidgetItem(str(vals[c]))
                tw.setItem(r, c, it)
        tw.setMinimumHeight(24 * rows + 30)

        def ch(it):
            r, c = it.row(), it.column()
            try:
                v = int(it.text(), 0)
            except ValueError:
                self.dlg.msg('number expected')
                return
            if setter(r, c, v):
                self._changed()
        tw.itemChanged.connect(ch)
        lay.addRow(tw)
        return tw

    def _treasure(self, lay, rec):
        tl = QtWidgets.QLabel('type: ' + '; '.join('%d %s' % kv for kv in sorted(d6data.TREASURE_TYPES.items())))
        tl.setWordWrap(True)
        lay.addRow(tl)
        F = '<hhiiihh'

        def get(r):
            return struct.unpack_from(F, rec.raw, r * 20)

        def put(r, c, v):
            vals = list(get(r))
            vals[c] = v
            struct.pack_into(F, rec.raw, r * 20, *vals)
            return True
        self._grid(lay, 10, 7, get, put, ['type', 'chance%', 'a', 'b', 'c', 'pA', 'pB'])
        d = QtWidgets.QLabel('Types 1/2: item = DiceRoll(a,b,c); 3: item from treasure lists a/b/c (chances pA/pB); '
                             '4: experience. See docs/formats/databases.md.')
        d.setWordWrap(True)
        d.setStyleSheet('color: #8a94a0')
        lay.addRow(d)

    def _trlist(self, lay, rec):
        items = self.dlg.win.game.items

        def nm(i):
            return items[i].get('name') if 1 <= i <= len(items) else ''

        def get(r):
            a, b = struct.unpack_from('<hh', rec.raw, 0x14 + 4 * r)
            return (a, b, nm(a), nm(b))

        def put(r, c, v):
            if c > 1:
                return False
            a, b = struct.unpack_from('<hh', rec.raw, 0x14 + 4 * r)
            a, b = (v, b) if c == 0 else (a, v)
            struct.pack_into('<hh', rec.raw, 0x14 + 4 * r, a, b)
            return True
        self._grid(lay, 10, 4, get, put, ['first item', 'last item', 'first name', 'last name'])

    def _monsnd(self, lay, rec):
        names = ['move/alert', 'idle', 'wound', 'death', 'attack'] + ['slot %d' % i for i in range(5, 16)]
        F = '<BBhhhh'

        def get(r):
            return struct.unpack_from(F, rec.raw, 0x18 + r * 10)

        def put(r, c, v):
            vals = list(get(r))
            vals[c] = v
            struct.pack_into(F, rec.raw, 0x18 + r * 10, *vals)
            return True
        tw = self._grid(lay, 16, 6, get, put, ['count', 'extra', 'timer', 'sfx1', 'sfx2', 'sfx3'])
        tw.setVerticalHeaderLabels(names)


class DatabaseEditor(QtWidgets.QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle('Game databases')
        self.resize(1150, 800)
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.tabs = QtWidgets.QTabBar()
        for f, n, k in TABS:
            self.tabs.addTab(n)
        self.tabs.currentChanged.connect(lambda i: self.fill())
        top.addWidget(self.tabs, 1)
        lay.addLayout(top)
        body = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('search')
        self.search.textChanged.connect(self._filter)
        left.addWidget(self.search)
        self.list = QtWidgets.QListWidget()
        self.list.currentRowChanged.connect(self._sel)
        left.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        for text, fn in (('Duplicate', self.duplicate), ('New blank', self.new_blank), ('Revert', self.revert)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        left.addLayout(row)
        lw = QtWidgets.QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(330)
        body.addWidget(lw)
        self.form = RecordForm(self)
        body.addWidget(self.form, 1)
        lay.addLayout(body, 1)
        self.status = QtWidgets.QLabel()
        self.status.setStyleSheet('color: #e5c07b')
        lay.addWidget(self.status)
        self.fill()

    def fname(self):
        return TABS[self.tabs.currentIndex()][0]

    def table(self):
        return self.win.game.table(self.fname())

    def msg(self, t):
        self.status.setText(t)

    def fill(self, select=None):
        t = self.table()
        self.list.blockSignals(True)
        self.list.clear()
        if t is not None:
            for i, r in enumerate(t.records, 1):
                it = QtWidgets.QListWidgetItem(rec_label(self.fname(), i, r))
                it.setData(QtCore.Qt.UserRole, i)
                self.list.addItem(it)
        self.list.blockSignals(False)
        self._filter(self.search.text())
        if t is None:
            self.form.show_record(self.fname(), 0, None)
            self.msg('%s not found in the game folder' % self.fname())
            return
        self.list.setCurrentRow((select - 1) if select else 0)

    def _filter(self, t):
        t = t.lower()
        for k in range(self.list.count()):
            it = self.list.item(k)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _sel(self, row):
        if row < 0:
            return
        i = self.list.item(row).data(QtCore.Qt.UserRole)
        self.form.show_record(self.fname(), i, self.table().records[i - 1])

    def current(self):
        it = self.list.currentItem()
        return it.data(QtCore.Qt.UserRole) if it else None

    def relabel(self):
        it = self.list.currentItem()
        if it is not None:
            i = it.data(QtCore.Qt.UserRole)
            it.setText(rec_label(self.fname(), i, self.table().records[i - 1]))

    def refresh_current(self):
        pass

    def changed(self, fname, idx):
        g = self.win.game
        g.db_dirty.add(fname)
        if self.win.models is not None:
            kind = {'D6MONS.DAT': 'M', 'D6ITEM.DAT': 'I', 'D6PROP.DAT': 'P'}.get(fname)
            if kind:
                self.win.models.by_rec.pop((kind, idx), None)
        self.win.db_changed()
        self.msg('%s record %d changed (File > Save writes it)' % (fname, idx))

    def _append(self, raw):
        t = self.table()
        lim = LIMITS.get(self.fname())
        if lim and len(t.records) >= lim:
            self.msg('%s can hold at most %d records' % (self.fname(), lim))
            return
        r = t.new_record()
        r.raw[:] = raw
        i = len(t.records)
        self.changed(self.fname(), i)
        self.fill(select=i)
        self.msg('Added record %d to %s' % (i, self.fname()))

    def duplicate(self):
        i = self.current()
        if i is None:
            return
        self._append(bytes(self.table().records[i - 1].raw))

    def new_blank(self):
        t = self.table()
        self._append(bytes(t.REC.SIZE))

    def revert(self):
        i = self.current()
        if i is None:
            return
        orig = self.win.game.db_orig.get(self.fname(), [])
        if i <= len(orig):
            self.table().records[i - 1].raw[:] = orig[i - 1]
            self.changed(self.fname(), i)
            self.fill(select=i)
            self.msg('Record %d reverted to the loaded version' % i)
        else:
            self.msg('Record %d is new (not in the loaded file)' % i)
