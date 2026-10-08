#!/usr/bin/env python3
"""
d6edit - map editor for Wizards & Warriors (Deep6 engine) game data.

usage: d6edit.py [GAMEDIR] [--spoke N]

Opens one spoke (area) at a time: terrain, BSP levels, placed objects, nav
points, boundary boxes and the spoke's scripting tables (triggers, switches,
specials, traps, links). Edits are written back into the original files; the
previous versions are copied to GAMEDIR/d6edit_backup/<time>/ first.
"""
import math
import os
import re
import shutil
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))


def _check_packages():
    """Started directly (not through d6edit.sh / d6edit.bat): make sure every
    package is there. Pillow is only imported while a spoke loads, so without
    this check a missing Pillow gives a half-loaded, empty map."""
    import importlib.util as iu
    need = {'numpy': 'numpy', 'PIL': 'Pillow', 'OpenGL': 'PyOpenGL'}
    missing = [pip for mod, pip in need.items() if iu.find_spec(mod) is None]
    if iu.find_spec('PySide6') is None and iu.find_spec('PyQt6') is None:
        missing.append('PySide6')
    if not missing:
        return
    root = os.path.dirname(HERE)
    venv = os.path.join(root, '.venv', 'Scripts' if os.name == 'nt' else 'bin',
                        'python.exe' if os.name == 'nt' else 'python')
    if os.path.exists(venv) and os.path.abspath(sys.executable) != os.path.abspath(venv) \
            and not os.environ.get('D6EDIT_NO_VENV'):
        os.environ['D6EDIT_NO_VENV'] = '1'          # no loop if the venv lacks them too
        os.execv(venv, [venv, os.path.abspath(__file__)] + sys.argv[1:])
    launcher = 'd6edit.bat' if os.name == 'nt' else 'd6edit.sh'
    msg = ('d6edit needs these Python packages, missing for %s:\n  %s\n\n'
           'Start the editor with %s in the SDK folder (it sets them up in .venv),\n'
           'or install them: pip install %s'
           % (sys.executable, ', '.join(missing), launcher, ' '.join(missing)))
    sys.stderr.write(msg + '\n')
    try:
        from qtcompat import QtWidgets as _QW
        _app = _QW.QApplication(sys.argv)
        _QW.QMessageBox.critical(None, 'd6edit', msg)
    except Exception:
        pass
    sys.exit(1)


sys.path.insert(0, HERE)
if __name__ == '__main__':
    _check_packages()
sys.path.insert(0, os.path.join(HERE, '..', 'formats'))

import numpy as np                                    # noqa: E402
from qtcompat import QtCore, QtGui, QtWidgets         # noqa: E402

import d6data                                         # noqa: E402
import world                                          # noqa: E402
import glview                                         # noqa: E402
import models                                         # noqa: E402
import tools                                          # noqa: E402
import navedit                                        # noqa: E402
import events_ui                                      # noqa: E402
import validate                                       # noqa: E402
import db_ui                                          # noqa: E402
import models_ui                                      # noqa: E402
import exits_ui                                       # noqa: E402
import textures_ui                                    # noqa: E402
import text_ui                                        # noqa: E402
import geometry_ui                                    # noqa: E402
import dialogue_ui                                    # noqa: E402
import spells_ui                                      # noqa: E402
import mod_ui                                         # noqa: E402
import d6model                                        # noqa: E402
import sysutil                                        # noqa: E402
import modes                                          # noqa: E402
import subprocess                                     # noqa: E402

KINDS = [('M', 'Monster'), ('I', 'Item'), ('P', 'Prop'), ('F', 'Foliage'),
         ('B', 'BSP placement'), ('N', 'Nav point'), ('L', 'Light (.LIT)'), ('0', 'Empty')]
KIND_NAME = dict(KINDS)
MON_FLAGS = [(1, 'ready'), (2, 'dead'), (4, 'fade in'), (8, 'no ground snap'),
             (0x10, 'hold / ambush'), (0x20, 'flag 0x20'), (0x40, 'flag 0x40')]
TABLE_TITLES = {'trig': 'Triggers', 'swit': 'Switches', 'spec': 'Specials',
                'trap': 'Traps', 'link': 'Nav links', 'boun': 'Boxes'}


# ---------------------------------------------------------------------------
# non spatial table record (trigger, switch, ...)
# ---------------------------------------------------------------------------
class EdRecord(object):
    cat = 'record'

    def __init__(self, spoke, table, index, rec):
        self.spoke, self.table, self.index, self.rec = spoke, table, index, rec

    def label(self):
        r, t, i = self.rec, self.table, self.index
        try:
            if t == 'trig':
                return 'T%d %s%s' % (i, r.get('event') or '(none)', '' if r.get('enabled') else '  [off]')
            if t == 'swit':
                return 'S%d %s %d -> %s' % (i, 'entity' if r.get('isentity') else 'obj', r.get('objid'),
                                            ('T%d' % -r.get('target')) if r.get('target') < 0 else 'state %d' % r.get('target'))
            if t == 'spec':
                ty = {1: 'clock', 2: 'all dead', 3: 'has item'}.get(r.get('type'), 'type %d' % r.get('type'))
                return 'X%d %s -> %s' % (i, ty, ('T%d' % r.get('trigger')) if r.get('trigger') > 0 else 'state %d' % r.get('state'))
            if t == 'trap':
                return 'Trap %d obj %d' % (i, r.get('objid'))
            if t == 'link':
                return 'Link %d: %d/%d <-> %d/%d' % (i, r.get('bspA'), r.get('navA'), r.get('bspB'), r.get('navB'))
        except Exception:
            pass
        return '%s %d' % (t, i)

    def key(self):
        return ('rec', self.table, self.index)


def act(menu, text, slot, shortcut=None):
    """menu.addAction(text, slot, shortcut) for both PySide6 and PyQt6."""
    a = menu.addAction(text)
    a.triggered.connect(lambda checked=False: slot())
    if shortcut is not None:
        a.setShortcut(QtGui.QKeySequence(shortcut))
    return a


def dirty_key(o):
    if o.cat == 'object':
        return tuple(o.listkey)
    if o.cat == 'nav':
        return ('NVS', o.bsp.slot)
    if o.cat == 'bound':
        return ('TABLE', 'boun')
    return ('TABLE', o.table)


def object_id(o):
    """The id scripts use for a placed object (see d6data.split_objid)."""
    if o.cat != 'object' or o.listkey[0] not in ('TOL', 'BOL'):
        return None
    if o.listkey[0] == 'TOL':
        return o.index
    slot = o.listkey[1] or 16
    return slot * 100000 + o.index


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------
class EditCmd(QtGui.QUndoCommand):
    """Before/after snapshots of one record. The edit is already applied when
    the command is pushed, so the first redo does nothing."""

    def __init__(self, win, obj, before, after, text, merge_tag=None):
        super().__init__(text)
        self.win, self.obj, self.before, self.after = win, obj, before, after
        self.merge_tag = merge_tag
        self.first = True

    def id(self):
        return 1 if self.merge_tag else -1

    def mergeWith(self, other):
        if other.merge_tag != self.merge_tag or other.obj is not self.obj:
            return False
        self.after = other.after
        return True

    def redo(self):
        if self.first:
            self.first = False
        else:
            glview.restore(self.obj, self.after)
        self.win.edited(self.obj, from_undo=True)

    def undo(self):
        glview.restore(self.obj, self.before)
        self.win.edited(self.obj, from_undo=True)


# ---------------------------------------------------------------------------
# value parsing for generic record fields
# ---------------------------------------------------------------------------
FMT_RE = re.compile(r'^(\d*)([a-zA-Z])$')


def fmt_parts(fmt):
    if fmt.startswith('str'):
        return 1, 'str'
    m = FMT_RE.match(fmt)
    return int(m.group(1) or 1), m.group(2)


def fmt_text(fmt, v):
    n, c = fmt_parts(fmt)
    if c == 'str':
        return v
    if c == 's':
        return v.hex(' ')
    vals = v if isinstance(v, list) else [v]
    if c in 'fd':
        return ', '.join('%g' % x for x in vals)
    return ', '.join(str(x) for x in vals)


def fmt_parse(fmt, text):
    n, c = fmt_parts(fmt)
    if c == 'str':
        return text
    if c == 's':
        b = bytes.fromhex(text.replace(',', ' '))
        if len(b) != n:
            raise ValueError('need %d bytes' % n)
        return b
    parts = [p for p in re.split(r'[,\s]+', text.strip()) if p]
    if len(parts) != n:
        raise ValueError('need %d value%s' % (n, 's' if n > 1 else ''))
    if c in 'fd':
        vals = [float(p) for p in parts]
    else:
        vals = [int(p, 0) for p in parts]
    return vals if n > 1 else vals[0]


# ---------------------------------------------------------------------------
# inspector
# ---------------------------------------------------------------------------
class Inspector(QtWidgets.QScrollArea):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.obj = None
        self.setWidgetResizable(True)
        self.setMinimumWidth(380)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.show_obj(None)

    def _form(self):
        # the old form may be the sender of the signal being handled right now,
        # so it is deleted later instead of by setWidget (crashes under PyQt6)
        old = self.takeWidget()
        if old is not None:
            old.hide()
            old.deleteLater()
        w = QtWidgets.QWidget()
        lay = QtWidgets.QFormLayout(w)
        lay.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.setWidget(w)
        return lay

    def commit(self, obj, apply, text, tag=None):
        """Apply a change through the undo stack."""
        before = glview.snapshot(obj)
        try:
            apply()
        except Exception as e:
            glview.restore(obj, before)
            self.win.statusBar().showMessage('Not changed: %s' % e, 5000)
            return False
        after = glview.snapshot(obj)
        if after != before:
            self.win.undo.push(EditCmd(self.win, obj, before, after, text, tag))
        return True

    # ------------------------------------------------------------------ builders
    def show_obj(self, obj):
        self.obj = obj
        self.face_spin = None
        self.rot_spins = None
        self.pos_spins = None
        lay = self._form()
        if obj is None:
            lay.addRow(QtWidgets.QLabel('Nothing selected.\n\nLeft click an object in the 3D view\n'
                                        'or pick one in the outliner.'))
            return
        title = QtWidgets.QLabel('<b>%s</b>' % obj.label())
        title.setWordWrap(True)
        lay.addRow(title)
        if obj.cat == 'object':
            self._object(lay, obj)
        elif obj.cat == 'nav':
            self._nav(lay, obj)
        else:
            self._record(lay, obj)
        refs = self.win.references(obj)
        if refs:
            lay.addRow(QtWidgets.QLabel('<b>Used by</b>'))
            for r in refs:
                b = QtWidgets.QPushButton(r.label())
                b.clicked.connect(lambda _=False, r=r: self.win.select(r))
                lay.addRow(b)

    def _float_row(self, lay, label, values, setter, tag, tip=''):
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        spins = []
        for v in values:
            s = QtWidgets.QDoubleSpinBox()
            s.setRange(-1e8, 1e8)
            s.setDecimals(2)
            s.setSingleStep(64)
            s.setKeyboardTracking(False)
            s.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            s.setMinimumWidth(60)
            s.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
            s.setValue(float(v))
            s.setToolTip(tip)
            h.addWidget(s)
            spins.append(s)

        def changed(_=None):
            vals = [s.value() for s in spins]
            self.commit(self.obj, lambda: setter(vals), 'Change %s' % label, tag)
        for s in spins:
            s.valueChanged.connect(changed)
        lay.addRow(label, row)
        return spins

    def _object(self, lay, o):
        rec = o.rec
        info = {'TOL': 'terrain object list (.TOL)', 'NAV': 'terrain nav list (.NAV)',
                'FOL': 'foliage list (.FOL)', 'LIT': 'light list (.LIT, editor only: bakes terrain light)'
                }.get(o.listkey[0])
        if o.kind in 'MIP' and o.recno:
            pv = QtWidgets.QLabel()
            pv.setAlignment(QtCore.Qt.AlignCenter)
            pv.setStyleSheet('background: #303338')
            pv.setPixmap(self.win.palette.pixmap(o.kind, o.recno, 160))
            pv.setFixedHeight(164)
            lay.addRow(pv)
        if info is None:
            info = 'object list of %s (.BOL), positions relative to the BSP' % o.bsp.name.upper()
        oid = object_id(o)
        lay.addRow('Record', QtWidgets.QLabel('#%d in %s' % (o.index, info)))
        if oid is not None:
            lay.addRow('Object id', QtWidgets.QLabel('%d  (used by switches, traps, scripts)' % oid))

        kind = QtWidgets.QComboBox()
        for k, n in KINDS:
            kind.addItem('%s  %s' % (k, n), k)
        kind.setCurrentIndex(max(0, [k for k, _ in KINDS].index(o.kind) if o.kind in KIND_NAME else 0))

        def set_kind(i):
            k = kind.itemData(i)

            def ap():
                if k == '0' and o.listkey[0] == 'BOL':
                    rec.raw[0] = 0
                else:
                    rec.raw[0] = ord(k)
                if not bytes(rec.raw[1:4]).isdigit():
                    rec.raw[1:4] = b'001'
            if self.commit(o, ap, 'Change type'):
                self.show_obj(o)
        kind.currentIndexChanged.connect(set_kind)
        lay.addRow('Type', kind)

        if o.kind in 'MIP':
            cb = QtWidgets.QComboBox()
            cb.setEditable(True)
            cb.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
            names = self.win.game.names(o.kind)
            for n, name in names:
                cb.addItem('%03d  %s' % (n, name), n)
            cb.completer().setFilterMode(QtCore.Qt.MatchContains)
            cb.completer().setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
            cur = o.recno or 0
            if 1 <= cur <= len(names):
                cb.setCurrentIndex(cur - 1)

            def set_rec(i):
                n = cb.itemData(i)
                if n is None:
                    return
                self.commit(o, lambda: rec.raw.__setitem__(slice(1, 4), b'%03d' % n), 'Change record')
            cb.currentIndexChanged.connect(set_rec)
            lay.addRow({'M': 'Monster', 'I': 'Item', 'P': 'Prop'}[o.kind], cb)
        elif o.kind in 'FBN':
            sp = QtWidgets.QSpinBox()
            sp.setRange(0, 999)
            sp.setValue(o.recno or 0)
            sp.setKeyboardTracking(False)
            sp.valueChanged.connect(lambda v: self.commit(o, lambda: rec.raw.__setitem__(slice(1, 4), b'%03d' % v),
                                                          'Change number'))
            lay.addRow('Number', sp)

        p = o.local_pos()
        tip = 'y = 0 on terrain means "stand on the ground"' if o.bsp is None else 'world units relative to the BSP origin'
        self.pos_spins = self._float_row(lay, 'Position', p, lambda v: o.set_local_pos(v), 'pos', tip)
        if o.kind == 'B':
            lay.addRow(QtWidgets.QLabel('<i>BSP footprint follows the record after reloading the spoke.</i>'))
        self.rot_spins = self._float_row(lay, 'Rotation' if o.kind != 'B' else 'Size', rec.get('rot'),
                        lambda v: rec.set('rot', v), 'rot',
                        'y = facing (values like 256, 768 seen: 1024 = full turn)' if o.kind != 'B' else '')

        if o.kind in 'MIP':
            face = QtWidgets.QSpinBox()
            face.setRange(-1, 360)
            face.setWrapping(True)
            face.setSuffix(' deg')
            face.setSingleStep(15)
            face.setKeyboardTracking(False)
            face.setValue(int(round(rec.get('rot')[1] * 360.0 / 1024)) % 360)
            face.setToolTip('Facing (rotation y, 1024 = full turn); Ctrl+wheel in the view turns it too')

            def set_face(v):
                r = list(rec.get('rot'))
                r[1] = float(round((v % 360) * 1024.0 / 360))
                self.commit(o, lambda: rec.set('rot', r), 'Change facing', 'face')
                self.refresh_positions()
            face.valueChanged.connect(set_face)
            lay.addRow('Facing', face)
            self.face_spin = face

        b20 = rec.get('b20')
        if o.kind == 'M':
            box = QtWidgets.QWidget()
            g = QtWidgets.QGridLayout(box)
            g.setContentsMargins(0, 0, 0, 0)
            for j, (bit, name) in enumerate(MON_FLAGS):
                c = QtWidgets.QCheckBox(name)
                c.setChecked(bool(b20 & bit))
                c.toggled.connect(lambda on, bit=bit: self.commit(
                    o, lambda: rec.set('b20', (rec.get('b20') | bit) if on else (rec.get('b20') & ~bit)), 'Change flags'))
                g.addWidget(c, j // 2, j % 2)
            lay.addRow('Flags', box)
        else:
            self._text_field(lay, o, rec, d6data.ObjListRec.field('b20'),
                             'BSP number' if o.kind == 'B' else 'Byte 0x20')
        self._text_field(lay, o, rec, d6data.ObjListRec.field('b21'),
                         'Tile offsets' if o.kind == 'B' else 'Bytes 0x21-23')
        if o.kind == 'B':
            self._text_field(lay, o, rec, d6data.ObjListRec.field('bspname'), 'BSP name')
        raw = QtWidgets.QLabel(bytes(rec.raw[0x24:0x30]).hex(' '))
        raw.setToolTip('unknown bytes 0x24-0x2f, kept as they are')
        lay.addRow('Other', raw)

    def _nav(self, lay, o):
        pt = o.pt
        lay.addRow('BSP', QtWidgets.QLabel('%s point %d' % (o.bsp.name.upper(), o.index)))
        lay.addRow(QtWidgets.QLabel('<i>Position in BSP units (x16 = world units)</i>'))
        nvs = o.bsp.nvs

        def setp(vals):
            nvs.points[o.index] = o.pt._replace(x=vals[0], y=vals[1], z=vals[2])
        self.pos_spins = self._float_row(lay, 'Position', (pt.x, pt.y, pt.z), setp, 'pos')
        for f in pt._fields:
            if f in ('x', 'y', 'z'):
                continue
            v = getattr(pt, f)
            e = QtWidgets.QLineEdit(('%g' % v) if isinstance(v, float) else str(v))

            def done(f=f, e=e, isf=isinstance(v, float)):
                def ap():
                    val = float(e.text()) if isf else int(e.text(), 0)
                    nvs.points[o.index] = o.pt._replace(**{f: val})
                self.commit(o, ap, 'Change nav %s' % f)
            e.editingFinished.connect(done)
            lay.addRow(f, e)

    def _text_field(self, lay, o, rec, f, label=None):
        v = rec.get(f.name)
        e = QtWidgets.QLineEdit(fmt_text(f.fmt, v))
        e.setToolTip('%s\n[%s, offset 0x%x, confidence %s]' % (f.doc, f.fmt, f.off, f.conf))
        if f.name.startswith('rt') or f.conf == 'low':
            e.setStyleSheet('color: gray')

        def done():
            try:
                val = fmt_parse(f.fmt, e.text())
            except Exception as ex:
                self.win.statusBar().showMessage('%s: %s' % (f.name, ex), 5000)
                e.setText(fmt_text(f.fmt, rec.get(f.name)))
                return
            self.commit(o, lambda: rec.set(f.name, val), 'Change %s' % f.name)
        e.editingFinished.connect(done)
        ref = {'keyitem': 'I', 'monrec': 'M'}.get(f.name)
        if ref and isinstance(v, int) and v > 0 and self.win.game.name(ref, v):
            label = '%s (%s)' % (label or f.name, self.win.game.name(ref, v))
        lay.addRow(label or f.name, e)
        return e

    def _record(self, lay, o):
        rec = o.rec
        table = 'boun' if o.cat == 'bound' else o.table
        lay.addRow('Table', QtWidgets.QLabel('%s record %d' % (TABLE_TITLES.get(table, table), o.index)))
        if table == 'trig':
            self._trigger(lay, o)
            return
        for f in rec.FIELDS:
            self._text_field(lay, o, rec, f)
        # jump buttons for references
        targets = []
        if table in ('boun', 'spec') and rec.get('trigger') > 0:
            targets.append(('trig', rec.get('trigger')))
        if table == 'swit' and rec.get('target') < 0:
            targets.append(('trig', -rec.get('target')))
        if table in ('swit', 'trap') and not (table == 'swit' and rec.get('isentity')):
            t = self.win.object_by_id(rec.get('objid'))
            if t is not None:
                b = QtWidgets.QPushButton('Go to object: %s' % t.label())
                b.clicked.connect(lambda _=False, t=t: self.win.select(t, frame=True))
                lay.addRow(b)
        if table == 'boun' and rec.get('who') == 4:
            t = self.win.object_by_id(rec.get('objid'))
            if t is not None:
                b = QtWidgets.QPushButton('Go to object: %s' % t.label())
                b.clicked.connect(lambda _=False, t=t: self.win.select(t, frame=True))
                lay.addRow(b)
        for tab, idx in targets:
            r = self.win.record(tab, idx)
            if r is not None:
                b = QtWidgets.QPushButton('Open %s' % r.label())
                b.clicked.connect(lambda _=False, r=r: self.win.select(r))
                lay.addRow(b)

    def _trigger(self, lay, o):
        rec = o.rec
        dcl = self.win.game.dcl
        ev = QtWidgets.QComboBox()
        ev.setEditable(True)
        ev.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        names = sorted(set(dcl.name(i) for i in range(dcl.n) if dcl.name(i)))
        ev.addItems(names)
        ev.completer().setFilterMode(QtCore.Qt.MatchContains)
        ev.completer().setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        ev.setCurrentText(rec.get('event'))
        idx = dcl.index(rec.get('event')) if rec.get('event') else -1

        def set_event():
            name = ev.currentText().strip()
            if name == rec.get('event'):
                return
            i = dcl.index(name)
            if i < 0:
                self.win.statusBar().showMessage('Unknown event %s (not in EVENTS.DCL)' % name, 5000)
                return

            def ap():
                rec.set('event', name)
                rec.set('nparams', dcl.nparams[i])
            if self.commit(o, ap, 'Change event'):
                self.show_obj(o)
        ev.activated.connect(lambda _: set_event())
        ev.lineEdit().editingFinished.connect(set_event)
        lay.addRow('Event', ev)
        if idx >= 0 and dcl.desc(idx):
            d = QtWidgets.QLabel(dcl.desc(idx))
            d.setWordWrap(True)
            d.setStyleSheet('color: #9ab')
            lay.addRow(d)
        for name in ('enabled', 'oneshot', 'mode', 'state', 'laststate'):
            self._text_field(lay, o, rec, rec.field(name))
        pnames = dcl.params_of(idx) if idx >= 0 else []
        params = rec.get('params')
        n = max(len(pnames), rec.get('nparams') if idx < 0 else 0)
        for k in range(min(n, 30)):
            e = QtWidgets.QLineEdit(str(params[k]))

            def done(k=k, e=e):
                def ap():
                    p = rec.get('params')
                    p[k] = int(e.text(), 0)
                    rec.set('params', p)
                self.commit(o, ap, 'Change parameter')
            e.editingFinished.connect(done)
            lay.addRow(pnames[k] if k < len(pnames) else 'P%d' % k, e)
            if k < len(pnames) and pnames[k].upper() in ('MSG', 'TEXTID', 'TEXT'):
                txt = self.win.game.message(params[k])
                b = QtWidgets.QPushButton((txt or '(no such message)').replace(']', ' ').replace('@', ' ')[:90] +
                                          ('...' if txt and len(txt) > 90 else ''))
                b.setToolTip('Edit this message (Game text)')
                b.clicked.connect(lambda _=False, v=params[k]: self.win.edit_text(select=v))
                lay.addRow('', b)
        code = self.win.disasm(idx)
        if code:
            t = QtWidgets.QPlainTextEdit(code)
            t.setReadOnly(True)
            t.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
            t.setMinimumHeight(160)
            lay.addRow(QtWidgets.QLabel('<b>Event code</b> (EVENTS.COD)'))
            lay.addRow(t)

    def refresh_positions(self):
        """Update the position spin boxes after a drag in the viewport."""
        o = self.obj
        spins = getattr(self, 'pos_spins', None)
        if o is None or not spins or o.cat == 'bound' or o.cat == 'record':
            return
        p = o.local_pos() if o.cat == 'object' else (o.pt.x, o.pt.y, o.pt.z)
        for s, v in zip(spins, p):
            s.blockSignals(True)
            s.setValue(float(v))
            s.blockSignals(False)
        if o.cat == 'object':
            for s, v in zip(getattr(self, 'rot_spins', []) or [], o.rec.get('rot')):
                s.blockSignals(True)
                s.setValue(float(v))
                s.blockSignals(False)
            f = getattr(self, 'face_spin', None)
            if f is not None:
                try:
                    f.blockSignals(True)
                    f.setValue(int(round(o.rec.get('rot')[1] * 360.0 / 1024)) % 360)
                    f.blockSignals(False)
                except RuntimeError:
                    self.face_spin = None


# ---------------------------------------------------------------------------
# main window
# ---------------------------------------------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, gamedir=None, spoke=None):
        super().__init__()
        self.resize(1500, 920)
        self.game = None
        self.spoke = None
        self.items = {}
        self._code = None
        self.undo = QtGui.QUndoStack(self)
        self.undo.cleanChanged.connect(lambda _: self._title())
        self.settings = QtCore.QSettings('OpenDeep6SDK', 'd6edit')

        self.view = glview.Viewport()
        self.setCentralWidget(self.view)
        self.view.selectionChanged.connect(self._viewport_selected)
        self.view.objectMoved.connect(self._moved)
        self.view.groupMoved.connect(self._group_moved)
        self.view.statusText.connect(lambda t: self.statusBar().showMessage(t, 4000))

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemSelectionChanged.connect(self._tree_selected)
        self.tree.itemDoubleClicked.connect(lambda *_: self.view.frame_selection())
        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText('Filter (name, type, number)')
        self.filter.textChanged.connect(self._apply_filter)
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(2, 2, 2, 2)
        lv.addWidget(self.filter)
        lv.addWidget(self.tree)
        d = QtWidgets.QDockWidget('Outliner')
        d.setObjectName('outliner')
        d.setWidget(left)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, d)
        self.outliner_dock = d

        self.inspector = Inspector(self)
        d = QtWidgets.QDockWidget('Inspector')
        d.setObjectName('inspector')
        d.setWidget(self.inspector)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, d)
        self.inspector_dock = d

        self.models = None
        self.palette = tools.PalettePanel(self)
        d = QtWidgets.QDockWidget('Objects')
        d.setObjectName('palette')
        d.setWidget(self.palette)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, d)
        self.tabifyDockWidget(self.outliner_dock, d)
        self.outliner_dock.raise_()
        self.palette_dock = d

        self.terrain_tool = tools.TerrainTool(self)
        self.terrain_panel = tools.TerrainPanel(self, self.terrain_tool)
        d = QtWidgets.QDockWidget('Terrain')
        d.setObjectName('terrain')
        d.setWidget(self.terrain_panel)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, d)
        self.tabifyDockWidget(self.inspector_dock, d)
        self.inspector_dock.raise_()
        self.terrain_dock = d

        self.nav_tool = tools.NavTool(self)
        self.nav_panel = tools.NavPanel(self, self.nav_tool)
        d = QtWidgets.QDockWidget('Nav')
        d.setObjectName('nav')
        d.setWidget(self.nav_panel)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, d)
        self.tabifyDockWidget(self.terrain_dock, d)
        self.inspector_dock.raise_()
        self.nav_dock = d

        self.exits_panel = exits_ui.ExitsPanel(self)
        d = QtWidgets.QDockWidget('Exits')
        d.setObjectName('exits')
        d.setWidget(self.exits_panel)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, d)
        self.tabifyDockWidget(self.nav_dock, d)
        self.inspector_dock.raise_()
        self.exits_dock = d
        self.light_pos = {}
        esc = QtGui.QShortcut(QtGui.QKeySequence('Escape'), self)
        esc.activated.connect(lambda: self.set_tool(None))

        self._menus()
        self.statusBar()
        self._title()

        gamedir = gamedir or self.settings.value('gamedir')
        if gamedir and os.path.isdir(gamedir):
            self.open_game(gamedir, spoke if spoke is not None else int(self.settings.value('spoke', 0)))
        else:
            QtCore.QTimer.singleShot(0, self.choose_game)

    # ----------------------------------------------------------------- menus
    def _menus(self):
        mb = self.menuBar()
        m = mb.addMenu('&File')
        act(m, '&Open game folder...', self.choose_game, QtGui.QKeySequence.Open)
        self.save_act = act(m, '&Save', self.save, QtGui.QKeySequence.Save)
        act(m, '&Reload spoke', lambda: self.load_spoke(self.spoke.number) if self.spoke else None, QtGui.QKeySequence('Ctrl+R'))
        m.addSeparator()
        act(m, '&Test in game', self.test_in_game, QtGui.QKeySequence('F5'))
        act(m, 'Game &command...', self.set_game_command)
        self.act_play = act(m, '&Play from the camera (EX)', self.play_from_camera, QtGui.QKeySequence('Shift+F5'))
        self.act_play_set = act(m, 'Play from the camera &settings...', self.set_play_command)
        m.addSeparator()
        act(m, 'E&xport tables as text...', self.export_text)
        act(m, '&Import tables from text...', self.import_text)
        m.addSeparator()
        act(m, '&Quit', self.close, QtGui.QKeySequence.Quit)

        m = mb.addMenu('&Edit')
        a = self.undo.createUndoAction(self, '&Undo')
        a.setShortcut(QtGui.QKeySequence.Undo)
        m.addAction(a)
        a = self.undo.createRedoAction(self, '&Redo')
        a.setShortcut(QtGui.QKeySequence.Redo)
        m.addAction(a)
        m.addSeparator()
        act(m, 'Add &monster', lambda: self.add_object('M'), QtGui.QKeySequence('Ctrl+1'))
        act(m, 'Add &item', lambda: self.add_object('I'), QtGui.QKeySequence('Ctrl+2'))
        act(m, 'Add &prop', lambda: self.add_object('P'), QtGui.QKeySequence('Ctrl+3'))
        act(m, 'Add &light (terrain)', lambda: self.add_object('L'))
        act(m, 'Add &box', self.add_box)
        act(m, 'Add &trigger', lambda: self.add_record('trig'))
        act(m, 'Add s&witch', lambda: self.add_record('swit'))
        m.addSeparator()
        act(m, '&Copy', self.copy_selection, QtGui.QKeySequence.Copy)
        act(m, '&Paste (in front of the camera)', self.paste, QtGui.QKeySequence.Paste)
        act(m, '&Duplicate', self.duplicate, QtGui.QKeySequence('Ctrl+D'))
        act(m, 'De&lete', self.delete, QtGui.QKeySequence.Delete)
        act(m, 'Drop to &ground', self.view.drop_selection, QtGui.QKeySequence('G'))
        m.addSeparator()
        act(m, 'Save selection as pre&fab...', self.save_prefab)
        act(m, 'Insert p&refab...', self.insert_prefab)
        sm = m.addMenu('Grid &snap')
        grp = QtGui.QActionGroup(self)
        for v, name in ((0, 'Off'), (64, '64 (1/16 tile)'), (256, '256 (1/4 tile)'), (512, '512 (1/2 tile)'),
                        (1024, '1024 (tile)')):
            a = sm.addAction(name)
            a.setCheckable(True)
            a.setChecked(v == 0)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, v=v: self.set_snap(v))

        m = mb.addMenu('&View')
        for key, name in (('M', 'Monsters'), ('I', 'Items'), ('P', 'Props'), ('B', 'BSP placements'),
                          ('F', 'Foliage'), ('N', 'Terrain nav points'), ('nav', 'BSP nav points'),
                          ('bound', 'Boxes'), ('L', 'Lights (.LIT, terrain light baking)'),
                          ('models', '3D models'), ('water', 'Water'), ('labels', 'Labels'),
                          ('cull', 'See into rooms (hide walls facing away)'),
                          ('walls', 'Forest / castle walls'), ('canopy', 'Tree canopy (tops of walls)')):
            a = m.addAction(name)
            a.setCheckable(True)
            a.setChecked(self.view.show.get(key, False))
            a.toggled.connect(lambda on, key=key: self._toggle(key, on))
        bm = m.addMenu('&Brightness')
        grp = QtGui.QActionGroup(self)
        for v in (1.0, 1.5, 2.0, 3.0):
            a = bm.addAction('%gx' % v)
            a.setCheckable(True)
            a.setChecked(v == 1.0)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, v=v: (setattr(self.view, 'brightness', v), self.view.update()))
        m.addSeparator()
        act(m, '&Top view', lambda: self._key(QtCore.Qt.Key_T), QtGui.QKeySequence('T'))
        act(m, '&Frame selection', self.view.frame_selection, QtGui.QKeySequence('F'))
        m.addSeparator()
        m.addAction(self.outliner_dock.toggleViewAction())
        m.addAction(self.inspector_dock.toggleViewAction())
        m.addAction(self.palette_dock.toggleViewAction())
        m.addAction(self.terrain_dock.toggleViewAction())
        m.addAction(self.nav_dock.toggleViewAction())
        m.addAction(self.exits_dock.toggleViewAction())

        m = mb.addMenu('&Tools')
        act(m, '&Terrain brush', self.toggle_terrain, QtGui.QKeySequence('Ctrl+T'))
        act(m, '&Place objects', lambda: (self.palette_dock.raise_(), self.palette.place()),
            QtGui.QKeySequence('Ctrl+P'))
        act(m, '&Event scripts...', self.edit_events, QtGui.QKeySequence('Ctrl+E'))
        act(m, '&Check spoke', self.check_spoke, QtGui.QKeySequence('Ctrl+K'))
        act(m, 'Game &databases (monsters, items, props...)', self.edit_db, QtGui.QKeySequence('Ctrl+B'))
        act(m, '&Models (preview, import / export)...', self.edit_models, QtGui.QKeySequence('Ctrl+M'))
        act(m, 'Te&xtures (level textures, terrain types)...', self.edit_textures, QtGui.QKeySequence('Ctrl+Shift+T'))
        act(m, 'Game te&xt (messages, strings)...', self.edit_text, QtGui.QKeySequence('Ctrl+G'))
        act(m, 'NPC &dialogue (scripts, strings, keywords)...', self.edit_dialogue,
            QtGui.QKeySequence('Ctrl+Shift+D'))
        act(m, '&Spells (names, schools, mana, targeting)...', self.edit_spells)
        act(m, 'Level &geometry (TrenchBroom: decompile, compile, place)...', self.edit_geometry,
            QtGui.QKeySequence('Ctrl+Shift+G'))
        act(m, '&Nav graph tool', lambda: (self.nav_dock.raise_(), self.nav_panel.enable.setChecked(True)),
            QtGui.QKeySequence('Ctrl+N'))
        act(m, 'Select / move (Esc)', lambda: self.set_tool(None))
        m.addSeparator()
        act(m, '&Relight whole terrain from lights', self.relight_all)
        act(m, '&Fit light model to the map', self.fit_light)
        act(m, 'Redraw the &automap of this spoke', self.redraw_automap)

        m = mb.addMenu('M&ode')
        self.mode_actions = {}
        grp = QtGui.QActionGroup(self)
        for md, text in ((modes.STANDARD, '&Standard (original game: deep6.exe / OpenDWWandW)'),
                         (modes.EX, '&EX (OpenDWWandWExpanded)')):
            a = m.addAction(text)
            a.setCheckable(True)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, md=md: self.set_mode(md))
            self.mode_actions[md] = a
        m.addSeparator()
        act(m, 'What the modes change...', lambda: QtWidgets.QMessageBox.information(
            self, 'Modes', modes.describe(modes.STANDARD) + '\n\n' + modes.describe(modes.EX)))

        m = mb.addMenu('&Mod')
        act(m, '&Mod project (capture, pack, install)...', self.edit_mod)

        m = mb.addMenu('&Help')
        act(m, '&Controls', self.help)
        self.set_mode(self.settings.value('mode', modes.STANDARD))

        tb = self.addToolBar('Spoke')
        tb.setObjectName('spoketb')
        tb.addWidget(QtWidgets.QLabel(' Spoke '))
        self.spoke_box = QtWidgets.QComboBox()
        for n in range(13):
            self.spoke_box.addItem(d6data.SPOKE_NAMES.get(n, 'Spoke %d' % n), n)
        self.spoke_box.activated.connect(lambda i: self.load_spoke(self.spoke_box.itemData(i)))
        tb.addWidget(self.spoke_box)
        tb.addSeparator()
        act(tb, 'Save', self.save)
        act(tb, 'Add monster', lambda: self.add_object('M'))
        act(tb, 'Add item', lambda: self.add_object('I'))
        act(tb, 'Add prop', lambda: self.add_object('P'))
        act(tb, 'Delete', self.delete)

    def _key(self, k):
        self.view.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, k, QtCore.Qt.NoModifier))

    def _toggle(self, key, on):
        self.view.show[key] = on
        self.view.rebuild_markers()

    def help(self):
        QtWidgets.QMessageBox.information(self, 'd6edit controls', glview.__doc__.split('Controls', 1)[1] +
                                          '\n  Ctrl+Z / Ctrl+Y      undo / redo\n  Del                  delete\n'
                                          '  Ctrl+1/2/3           add monster / item / prop\n'
                                          '  Ctrl+C / Ctrl+V      copy / paste in front of the camera\n'
                                          '  Ctrl+D               duplicate\n  Ctrl+S               save\n'
                                          '  F5 / Shift+F5        test in game / play from the camera\n'
                                          'Grid snap and prefabs: Edit menu.')

    def _title(self):
        if getattr(self, '_closing', False):
            return
        t = 'OpenDeep6SDK Editor [%s]' % modes.NAMES.get(getattr(self, 'mode', modes.STANDARD), '?')
        if self.spoke is not None:
            t += ' - %s' % d6data.SPOKE_NAMES.get(self.spoke.number, self.spoke.number)
            if self.unsaved():
                t += ' *'
        self.setWindowTitle(t)

    # ------------------------------------------------------------------ loading
    def choose_game(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, 'Wizards & Warriors game folder (with D6MONS.DAT)',
                                                       self.settings.value('gamedir', os.path.expanduser('~')))
        if d:
            self.open_game(d, 0)

    def open_game(self, d, spoke=0):
        if not world.find(d, 'D6MONS.DAT'):
            QtWidgets.QMessageBox.warning(self, 'd6edit', '%s does not look like the game folder\n'
                                          '(D6MONS.DAT not found).' % d)
            return
        if not self.maybe_save():
            return
        self.game = world.GameData(d)
        self.models = models.ModelCache(d)
        for k, t in (('M', self.game.mons), ('I', self.game.items), ('P', self.game.props)):
            d6model._table_cache[(os.path.abspath(d), 'db' + k)] = t     # model lookups see live edits
        self.view.models = self.models
        self._code = None
        self.palette.fill()
        self.settings.setValue('gamedir', d)
        self.load_spoke(spoke)

    def _step(self, what, fn, *a):
        """Run one step of a spoke load; an error is reported and the load goes on."""
        try:
            return fn(*a)
        except Exception:
            report_error('Loading spoke: %s failed' % what, traceback.format_exc(), self)
            return None

    def load_spoke(self, n):
        if self.game is None:
            return
        if self.spoke is not None and n != self.spoke.number and not self.maybe_save():
            self.spoke_box.setCurrentIndex(self.spoke.number)
            return
        dlg = QtWidgets.QProgressDialog('Loading spoke %d...' % n, None, 0, 0, self)
        dlg.setWindowModality(QtCore.Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.show()
        QtWidgets.QApplication.processEvents()

        def prog(msg):
            dlg.setLabelText('Loading spoke %d: %s' % (n, msg))
            QtWidgets.QApplication.processEvents()
        try:
            sp = world.Spoke(self.game, n, prog)
        except Exception as e:
            dlg.close()
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, 'd6edit', 'Cannot load spoke %d:\n%s' % (n, e))
            return
        self.set_tool(None)
        if self.models is not None:
            if self.spoke is not None:
                self.models.drop_texlib(self.spoke.texlib)
            need = sorted(set((o.kind, o.recno) for o in sp.objects
                              if o.cat == 'object' and o.kind in 'MIP' and o.recno) |
                          set(('P', o.tree_prop()) for o in sp.objects if o.cat == 'object' and o.kind == 'F'))
            for i, (k, r) in enumerate(need):
                if i % 8 == 0:
                    prog('models %d/%d' % (i, len(need)))
                self.models.get(k, r, sp.texlib)
        dlg.close()
        self.spoke = sp
        self.undo.clear()
        self.light_pos = dict((o.key(), o.local_pos().copy()) for o in sp.objects
                              if o.cat == 'object' and o.listkey == ('LIT',))
        self._step('terrain panel', self.terrain_panel.load_spoke, sp)
        sp.navgraph = None

        def nav():
            sp.navgraph = navedit.NavGraph(sp)
            self.nav_saved = sp.navgraph.state()
        self._step('nav graph', nav)
        self.view.exit_sel = None
        self.settings.setValue('spoke', n)
        self.spoke_box.setCurrentIndex(n)
        self.records = {}
        for key, (path, tab) in sp.tables.items():
            if key == 'boun':
                continue
            self.records[key] = [EdRecord(sp, key, i, r) for i, r in enumerate(tab.records, 1)]
        self._step('3D view', self.view.set_spoke, sp)
        self._step('exits', self.exits_panel.refresh)
        self.inspector.show_obj(None)
        self._step('outliner', self.build_tree)
        self._title()
        saved = sp.saved_state_files()
        msg = '%d objects, %d BSPs' % (sum(1 for o in sp.objects if o.cat == 'object' and o.kind != '0'), len(sp.bsps))
        if saved:
            msg += ' - note: %s holds saved positions for this spoke' % os.path.basename(saved[0])
        self.statusBar().showMessage(msg, 10000)

    # ------------------------------------------------------------------ outliner
    def build_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items = {}
        sp = self.spoke

        def group(parent, text):
            it = QtWidgets.QTreeWidgetItem([text])
            if parent is None:
                self.tree.addTopLevelItem(it)
            else:
                parent.addChild(it)
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
            return it

        def add(parent, o):
            it = QtWidgets.QTreeWidgetItem([o.label()])
            it.setData(0, QtCore.Qt.UserRole, o.key())
            parent.addChild(it)
            self.items[o.key()] = (it, o, getattr(o, 'kind', None))

        by_list = {}
        for o in sp.objects:
            if o.cat == 'object':
                by_list.setdefault(tuple(o.listkey), []).append(o)
        if sp.terrain is not None:
            top = group(None, 'Terrain %s' % os.path.basename(sp.tmr_path))
            objs = by_list.get(('TOL',), [])
            for k, name in KINDS:
                sub = [o for o in objs if o.kind == k]
                if sub:
                    g = group(top, '%s (%d)' % (name if k != '0' else 'Empty slots', len(sub)))
                    for o in sub:
                        add(g, o)
            nav = by_list.get(('NAV',), [])
            if nav:
                g = group(top, 'Nav points (%d)' % len(nav))
                for o in nav:
                    add(g, o)
        for b in sp.bsps:
            top = group(None, 'BSP %s (slot %d)' % (b.name.upper(), b.slot))
            objs = by_list.get(('BOL', b.slot), [])
            for k, name in KINDS:
                sub = [o for o in objs if o.kind == k]
                if sub:
                    g = group(top, '%s (%d)' % (name if k != '0' else 'Empty slots', len(sub)))
                    for o in sub:
                        add(g, o)
            navs = [o for o in sp.objects if o.cat == 'nav' and o.bsp is b]
            if navs:
                g = group(top, 'Nav points (%d)' % len(navs))
                for o in navs:
                    add(g, o)
        bounds = [o for o in sp.objects if o.cat == 'bound']
        scripts = group(None, 'Scripting')
        if bounds:
            g = group(scripts, 'Boxes (%d)' % len(bounds))
            for o in bounds:
                add(g, o)
        for key in ('trig', 'swit', 'spec', 'trap', 'link'):
            recs = self.records.get(key, [])
            g = group(scripts, '%s (%d)' % (TABLE_TITLES[key], len(recs)))
            for o in recs:
                add(g, o)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setExpanded(True)
        self.tree.blockSignals(False)
        self._apply_filter(self.filter.text())

    def _apply_filter(self, text):
        t = text.strip().lower()

        def walk(it):
            if it.childCount() == 0:
                hit = not t or t in it.text(0).lower()
                it.setHidden(not hit)
                return hit
            any_hit = False
            for i in range(it.childCount()):
                any_hit = walk(it.child(i)) or any_hit
            it.setHidden(bool(t) and not any_hit)
            if t and any_hit:
                it.setExpanded(True)
            return any_hit or not t
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def _tree_selected(self):
        its = self.tree.selectedItems()
        if not its:
            return
        k = its[0].data(0, QtCore.Qt.UserRole)
        if k is None or k not in self.items:
            return
        o = self.items[k][1]
        self._select_quiet = True
        try:
            if o.cat == 'record':
                self.view.select(None)
                self.inspector.show_obj(o)
            else:
                self.view.select(o)
        finally:
            self._select_quiet = False

    def _viewport_selected(self, o):
        if o is None and getattr(self, '_select_quiet', False):
            return
        self.inspector.show_obj(o)
        if not getattr(self, '_select_quiet', False):
            self._tree_select(o)

    def _tree_select(self, o):
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        if o is not None and o.key() in self.items:
            it = self.items[o.key()][0]
            if it.isHidden():
                self.filter.clear()
            it.setSelected(True)
            self.tree.scrollToItem(it)
        self.tree.blockSignals(False)

    def select(self, o, frame=False):
        if o is None or o.cat == 'record':
            self.view.select(None)
            self.inspector.show_obj(o)
            self._tree_select(o)
            return
        if o.cat == 'object' and not self.view.visible(o) and o.kind in self.view.show:
            self.view.show[o.kind] = True
            self.view.rebuild_markers()
        self.view.select(o)
        if frame:
            self.view.frame_selection()

    # ------------------------------------------------------------------ lookups
    def object_by_id(self, oid):
        if oid is None or oid <= 0 or self.spoke is None:
            return None
        b, k = d6data.split_objid(oid)
        key = ('obj', 'TOL', k) if b is None else ('obj', 'BOL', b, k)
        for o in self.spoke.objects:
            if o.key() == key:
                return o
        return None

    def record(self, table, idx):
        recs = self.records.get(table, [])
        if 1 <= idx <= len(recs):
            return recs[idx - 1]
        return None

    def references(self, obj):
        """Scripting records that point at obj."""
        out = []
        if obj.cat == 'object':
            oid = object_id(obj)
            if oid is None:
                return out
            for r in self.records.get('swit', []):
                if not r.rec.get('isentity') and r.rec.get('objid') == oid:
                    out.append(r)
            for r in self.records.get('trap', []):
                if r.rec.get('objid') == oid:
                    out.append(r)
            for r in self.records.get('spec', []):
                if r.rec.get('type') == 2 and oid in r.rec.get('args'):
                    out.append(r)
            for o in self.spoke.objects:
                if o.cat == 'bound' and o.rec.get('who') == 4 and o.rec.get('objid') == oid:
                    out.append(o)
        elif obj.cat == 'record' and obj.table == 'trig':
            for o in self.spoke.objects:
                if o.cat == 'bound' and o.rec.get('trigger') == obj.index:
                    out.append(o)
            for r in self.records.get('swit', []):
                if r.rec.get('target') == -obj.index:
                    out.append(r)
            for r in self.records.get('spec', []):
                if r.rec.get('trigger') == obj.index:
                    out.append(r)
        return out

    def disasm(self, idx):
        if idx is None or idx < 0 or self.game is None:
            return ''
        try:
            return self.game.events.source(idx)
        except Exception:
            return ''

    # ------------------------------------------------------------------ editing
    def edited(self, obj, from_undo=False):
        """Called after any change to obj (inspector edit, drag, undo/redo)."""
        if self.spoke is None:
            return
        self.spoke.mark_dirty(dirty_key(obj))
        if obj.cat == 'object' and obj.listkey == ('LIT',) and self.spoke.ted is not None:
            self._light_changed(obj)
        if obj.key() in self.items:
            it, _, kind = self.items[obj.key()]
            if getattr(obj, 'kind', None) != kind:
                self.build_tree()                 # moved to another group (type changed, added, deleted)
                self._tree_select(obj if self.view.selected is obj or self.inspector.obj is obj else None)
            else:
                it.setText(0, obj.label())
        self.view.rebuild_markers()
        if from_undo and self.inspector.obj is obj:
            if obj.cat == 'record' or not self.inspector.isAncestorOf(QtWidgets.QApplication.focusWidget()):
                self.inspector.show_obj(obj)
            else:
                self.inspector.refresh_positions()
        self._title()

    def _moved(self, obj, before, after):
        self.undo.push(EditCmd(self, obj, before, after, 'Move %s' % obj.label()))
        self.inspector.refresh_positions()

    def _target_list(self, p):
        """Object list a new object at world point p goes into: the BSP whose
        bounds contain it (dungeons), else the terrain list."""
        sp = self.spoke
        best = None
        for b in sp.bsps:
            if b.bol is None or not b.mesh:
                continue
            lo, hi = b.mesh['pos'].min(0), b.mesh['pos'].max(0)
            if np.all(p >= lo - 64) and np.all(p <= hi + 64):
                vol = np.prod(hi - lo)
                if best is None or vol < best[0]:
                    best = (vol, b)
        if best is not None:
            return ('BOL', best[1].slot), best[1].bol, best[1]
        if sp.tol is not None:
            return ('TOL',), sp.tol, None
        bs = [b for b in sp.bsps if b.bol is not None]
        if not bs:
            return None, None, None
        b = min(bs, key=lambda b: np.linalg.norm(b.mesh['pos'].mean(0) - p) if b.mesh else 1e18)
        return ('BOL', b.slot), b.bol, b

    def _free_object(self, listkey, olist, bsp):
        """Reuse an empty record (keeps every other object's id), else append."""
        for o in self.spoke.objects:
            if o.cat == 'object' and tuple(o.listkey) == listkey and o.kind == '0':
                return o, False
        rec = olist.new_record()
        rec.raw[0:4] = (b'\x00' if listkey[0] == 'BOL' else b'0') + b'000'
        o = world.EdObject(self.spoke, listkey, len(olist.records), rec, bsp=bsp)
        self.spoke.objects.append(o)
        return o, True

    def add_object(self, kind, src=None, at=None, recno=None, keep_tool=False):
        if self.spoke is None:
            return
        if at is not None:
            p = np.array(at, np.float64)
        else:
            p = self.view.snap_point(self.view.center_point()) if src is None else src.world_pos() + (256, 0, 256)
        if kind == 'L':
            if self.spoke.lit is None:
                self.statusBar().showMessage('Lights exist only on terrain spokes (.LIT).', 5000)
                return
            listkey, olist, bsp = ('LIT',), self.spoke.lit, None
        else:
            listkey, olist, bsp = self._target_list(p)
        if olist is None:
            self.statusBar().showMessage('This spoke has no object list.', 5000)
            return
        o, new = self._free_object(listkey, olist, bsp)
        before = glview.snapshot(o)
        rec = o.rec
        if src is not None:
            rec.raw[:] = src.rec.raw
        else:
            rec.raw[0:0x30] = b'\0' * 0x30
            rec.raw[0:4] = (kind + '%03d' % (recno or (1 if kind != 'L' else 0))).encode()
            if kind == 'M':
                rec.set('b20', 0)
        if kind == 'L':
            p = p.copy()
            p[1] = 8192.0                 # height used by the shipped lights
        elif o.bsp is None and src is None and self.spoke.terrain is not None:
            p = p.copy()
            p[1] = 0                      # stand on the ground
        o.set_world_pos(p)
        self.undo.push(EditCmd(self, o, before, glview.snapshot(o), 'Add %s' % KIND_NAME.get(o.kind, kind)))
        if new or o.key() not in self.items:
            self.build_tree()
        self.edited(o)
        if not keep_tool:
            self.set_tool(None)
        self.select(o)
        self.statusBar().showMessage('Added %s as record %d of %s%s' % (
            o.label(), o.index, listkey[0], '' if not bsp else ' ' + bsp.name.upper()), 6000)

    def set_snap(self, v):
        self.view.snap = float(v)
        self.statusBar().showMessage('Grid snap %s' % (('%d units' % v) if v else 'off'), 4000)

    def _group_moved(self, changes):
        if not changes:
            return
        self.undo.beginMacro('Move %d objects' % len(changes))
        for o, before, after in changes:
            self.undo.push(EditCmd(self, o, before, after, 'Move %s' % o.label()))
        self.undo.endMacro()
        self.inspector.refresh_positions()

    def copyable(self):
        return [o for o in self.view.selection()
                if o.cat == 'object' and o.kind not in ('0', 'B') and o.rec is not None]

    def selection_blob(self):
        """JSON-able description of the selected objects, positions relative to
        their centre (x, z) and to the ground for objects standing on it."""
        objs = self.copyable()
        if not objs:
            return None
        c = np.mean([o.world_pos() for o in objs], axis=0)
        items = []
        for o in objs:
            p = o.world_pos()
            ground = bool(o.bsp is None and o.kind in 'MIPNF' and o.local_pos()[1] == 0)
            items.append(dict(list=o.listkey[0], kind=o.kind, raw=bytes(o.rec.raw).hex(), ground=ground,
                              pos=[float(p[0] - c[0]), float(p[1] - c[1]), float(p[2] - c[2])]))
        return {'d6edit': 'objects', 'version': 1, 'spoke': self.spoke.number, 'items': items}

    def place_blob(self, blob, at, text):
        """Create the objects of a blob around world point at; returns them."""
        if not blob or blob.get('d6edit') != 'objects':
            raise ValueError('not d6edit objects')
        at = self.view.snap_point(np.asarray(at, np.float64))
        made = []
        self.undo.beginMacro(text)
        try:
            for it in blob['items']:
                raw = bytes.fromhex(it['raw'])
                p = at + np.array(it['pos'])
                if it['list'] == 'LIT':
                    if self.spoke.lit is None:
                        continue
                    listkey, olist, bsp = ('LIT',), self.spoke.lit, None
                else:
                    listkey, olist, bsp = self._target_list(p)
                    if olist is None:
                        continue
                o, new = self._free_object(listkey, olist, bsp)
                if len(raw) != len(o.rec.raw):
                    continue
                before = glview.snapshot(o)
                o.rec.raw[:] = raw
                if it.get('ground') and bsp is None and self.spoke.terrain is not None:
                    p[1] = 0
                o.set_world_pos(p)
                self.undo.push(EditCmd(self, o, before, glview.snapshot(o), 'Add %s' % o.label()))
                self.edited(o)
                made.append(o)
        finally:
            self.undo.endMacro()
        self.build_tree()
        if made:
            self.view.select(made[0])
            for o in made[1:]:
                self.view.select(o, add=True)
        return made

    def copy_selection(self):
        b = self.selection_blob()
        if b is None:
            self.statusBar().showMessage('Nothing to copy (select monsters, items, props, trees, lights).', 5000)
            return
        import json
        QtWidgets.QApplication.clipboard().setText(json.dumps(b))
        self.statusBar().showMessage('Copied %d objects.' % len(b['items']), 4000)

    def paste(self):
        if self.spoke is None:
            return
        import json
        try:
            blob = json.loads(QtWidgets.QApplication.clipboard().text())
            made = self.place_blob(blob, self.view.center_point(), 'Paste')
        except (ValueError, KeyError, TypeError) as e:
            self.statusBar().showMessage('Nothing to paste (%s).' % e, 5000)
            return
        self.statusBar().showMessage('Pasted %d objects.' % len(made), 4000)

    def prefab_dir(self):
        d = self.settings.value('prefabdir', '') or os.path.join(os.path.dirname(HERE), 'prefabs')
        os.makedirs(d, exist_ok=True)
        return d

    def save_prefab(self):
        b = self.selection_blob()
        if b is None:
            self.statusBar().showMessage('Select the objects of the prefab first.', 5000)
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Save prefab', self.prefab_dir(), 'Prefab (*.d6prefab)')
        if not path:
            return
        if not path.endswith('.d6prefab'):
            path += '.d6prefab'
        import json
        json.dump(b, open(path, 'w'), indent=1)
        self.settings.setValue('prefabdir', os.path.dirname(path))
        self.statusBar().showMessage('Saved %s (%d objects).' % (path, len(b['items'])), 5000)

    def insert_prefab(self, path=None):
        if self.spoke is None:
            return
        if not path:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Insert prefab', self.prefab_dir(),
                                                            'Prefab (*.d6prefab)')
        if not path:
            return
        import json
        try:
            made = self.place_blob(json.load(open(path)), self.view.center_point(),
                                   'Insert %s' % os.path.basename(path))
        except (ValueError, KeyError, TypeError, OSError) as e:
            self.statusBar().showMessage('Cannot insert %s: %s' % (path, e), 6000)
            return
        self.statusBar().showMessage('Inserted %d objects.' % len(made), 4000)

    def duplicate(self):
        b = self.selection_blob()
        if b is None:
            return
        c = np.mean([o.world_pos() for o in self.copyable()], axis=0)
        self.place_blob(b, c + (256, 0, 256), 'Duplicate')

    def delete(self):
        sel = self.view.selection()
        if len(sel) > 1:
            objs = [o for o in sel if o.cat == 'object' and o.kind != 'B']
            refs = [r for o in objs for r in self.references(o)]
            if refs and QtWidgets.QMessageBox.question(
                    self, 'd6edit', 'Some objects are used by:\n%s\n\nDelete anyway?' % '\n'.join(
                        sorted(set(r.label() for r in refs)))) != QtWidgets.QMessageBox.Yes:
                return
            self.undo.beginMacro('Delete %d objects' % len(objs))
            for o in objs:
                before = glview.snapshot(o)
                o.rec.raw[0] = 0 if o.listkey[0] == 'BOL' else ord('0')
                self.undo.push(EditCmd(self, o, before, glview.snapshot(o), 'Delete'))
                self.edited(o)
            self.undo.endMacro()
            self.view.select(None)
            self.inspector.show_obj(None)
            self.statusBar().showMessage('Deleted %d objects (BSPs, boxes and nav points are kept).' % len(objs), 5000)
            return
        o = self.view.selected or self.inspector.obj
        if o is None:
            return
        if o.cat == 'object':
            if o.kind == 'B':
                QtWidgets.QMessageBox.information(self, 'd6edit', 'BSP placements cannot be deleted here.')
                return
            refs = self.references(o)
            if refs and QtWidgets.QMessageBox.question(
                    self, 'd6edit', '%s is used by:\n%s\n\nDelete anyway?' % (
                        o.label(), '\n'.join(r.label() for r in refs))) != QtWidgets.QMessageBox.Yes:
                return
            before = glview.snapshot(o)
            # blank the record: indices of the other objects are object ids
            o.rec.raw[0] = 0 if o.listkey[0] == 'BOL' else ord('0')
            self.undo.push(EditCmd(self, o, before, glview.snapshot(o), 'Delete'))
            self.edited(o)
            self.view.select(None)
            self.inspector.show_obj(None)
        elif o.cat in ('bound', 'record'):
            before = glview.snapshot(o)
            if 'enabled' in [f.name for f in o.rec.FIELDS]:
                o.rec.set('enabled', 0)
                self.undo.push(EditCmd(self, o, before, glview.snapshot(o), 'Disable'))
                self.edited(o)
                self.inspector.show_obj(o)
                self.statusBar().showMessage('Records are referenced by number, so they are disabled, not removed.', 6000)
        else:
            self.statusBar().showMessage('Nav points cannot be deleted (links refer to them).', 5000)

    def add_box(self):
        if self.spoke is None or 'boun' not in self.spoke.tables:
            return
        p = self.view.center_point()
        tab = self.spoke.tables['boun'][1]
        rec = tab.new_record()
        listkey, olist, bsp = self._target_list(p)
        org = np.zeros(3)
        if bsp is not None and listkey[0] == 'BOL':
            rec.set('bsp', bsp.slot)
            org = bsp.origin
        else:
            rec.set('bsp', -1)
        c = p - org
        rec.set('min', [float(c[0] - 512), float(c[1]), float(c[2] - 512)])
        rec.set('max', [float(c[0] + 512), float(c[1] + 1024), float(c[2] + 512)])
        rec.set('enabled', 0)
        rec.set('trigger', 0)
        o = world.EdBound(self.spoke, len(tab.records), rec)
        self.spoke.objects.append(o)
        before = ('raw', bytes(len(rec.raw)))
        self.undo.push(EditCmd(self, o, before, glview.snapshot(o), 'Add box'))
        self.view.show['bound'] = True
        self.build_tree()
        self.edited(o)
        self.select(o)
        self.statusBar().showMessage('New box %d is disabled until you set its trigger/state and enable it.' % o.index, 8000)

    def add_record(self, table):
        if self.spoke is None:
            return
        if table not in self.spoke.tables:
            self.statusBar().showMessage('This spoke has no %s file.' % TABLE_TITLES[table], 5000)
            return
        tab = self.spoke.tables[table][1]
        rec = tab.new_record()
        o = EdRecord(self.spoke, table, len(tab.records), rec)
        self.records.setdefault(table, []).append(o)
        self.undo.push(EditCmd(self, o, ('raw', bytes(len(rec.raw))), glview.snapshot(o), 'Add record'))
        self.build_tree()
        self.edited(o)
        self.select(o)

    # ------------------------------------------------------------------ tools / terrain
    def set_tool(self, tool):
        if self.view.tool is tool:
            return
        self.view.tool = tool
        self.view.drag = None
        if tool is not self.terrain_tool and self.terrain_panel.enable.isChecked():
            self.terrain_panel.enable.blockSignals(True)
            self.terrain_panel.enable.setChecked(False)
            self.terrain_panel.enable.setText('Terrain brush: off')
            self.terrain_panel.enable.blockSignals(False)
        if tool is not self.nav_tool and self.nav_panel.enable.isChecked():
            self.nav_panel.enable.blockSignals(True)
            self.nav_panel.enable.setChecked(False)
            self.nav_panel.enable.setText('Nav tool: off')
            self.nav_panel.enable.blockSignals(False)
        td = getattr(self, '_tex_dlg', None)
        if td is not None and tool is not td.tool and td.paint.isChecked():
            td.paint.blockSignals(True)
            td.paint.setChecked(False)
            td.paint.setText('Paint on faces: off')
            td.paint.blockSignals(False)
        if tool is not self.palette.tool and self.palette.place_btn.isChecked():
            self.palette.place_btn.blockSignals(True)
            self.palette.place_btn.setChecked(False)
            self.palette.place_btn.setText('Place by clicking in the view')
            self.palette.place_btn.blockSignals(False)
        if tool is None:
            self.view.set_brush(None, 0)
            self.view.overlay.hud = ''
            self.view.setCursor(QtCore.Qt.ArrowCursor)
        else:
            self.view.setCursor(QtCore.Qt.CrossCursor)
        if tool is self.terrain_tool:
            self.terrain_dock.raise_()
        self.view.overlay.update()

    def toggle_terrain(self):
        if self.spoke is None or self.spoke.ted is None:
            self.statusBar().showMessage('This spoke has no terrain.', 4000)
            return
        self.terrain_dock.raise_()
        self.terrain_panel.enable.setChecked(not self.terrain_panel.enable.isChecked())

    def tile_info(self, h):
        ted = self.spoke.ted if self.spoke else None
        if h is None or ted is None:
            return ''
        x, z = int(h[0] // 1024), int(h[2] // 1024)
        if not (0 <= x < ted.W - 1 and 0 <= z < ted.D - 1):
            return ''
        t = ted.t[z, x]
        return ('tile %d,%d  %s  height %d  water %d  walls 0x%02x/%d  light %d   [%s]' % (
            x, z, ted.map.blt_name(int(t['tex'])) or '-', t['height'], t['water'], t['flags'],
            t['walltype'], t['light'][0], dict(tools.TerrainTool.MODES)[self.terrain_tool.mode]))

    def check_spoke(self):
        if self.spoke is None:
            return
        res = validate.check(self)
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle('Check spoke %d' % self.spoke.number)
        dlg.resize(820, 480)
        lay = QtWidgets.QVBoxLayout(dlg)
        n = dict((k, sum(1 for r in res if r[0] == k)) for k in (validate.ERR, validate.WARN, validate.INFO))
        lay.addWidget(QtWidgets.QLabel('%d errors, %d warnings, %d notes. Double click a line to select the thing.' % (
            n[validate.ERR], n[validate.WARN], n[validate.INFO])))
        lst = QtWidgets.QListWidget()
        colors = {validate.ERR: '#e06c75', validate.WARN: '#e5c07b', validate.INFO: '#9ab'}
        for lvl, txt, o in res:
            it = QtWidgets.QListWidgetItem('%-8s %s' % (lvl, txt))
            it.setForeground(QtGui.QColor(colors[lvl]))
            it.setData(QtCore.Qt.UserRole, o.key() if o is not None else None)
            lst.addItem(it)
        if not res:
            lst.addItem('No problems found.')

        def go(it):
            k = it.data(QtCore.Qt.UserRole)
            if k is None:
                return
            for o in list(self.spoke.objects) + [r for v in self.records.values() for r in v]:
                if o.key() == k:
                    self.select(o, frame=o.cat != 'record')
                    break
        lst.itemDoubleClicked.connect(go)
        lay.addWidget(lst)
        b = QtWidgets.QPushButton('Close')
        b.clicked.connect(dlg.close)
        lay.addWidget(b)
        dlg.show()
        self._check_dlg = dlg

    def set_mode(self, md):
        if md not in modes.NAMES:
            md = modes.STANDARD
        self.mode = md
        self.settings.setValue('mode', md)
        for k, a in self.mode_actions.items():
            a.setChecked(k == md)
        for feature, action in (('play_from_camera', self.act_play), ('play_from_camera', self.act_play_set)):
            ok = modes.available(md, feature)
            action.setEnabled(ok)
            action.setToolTip('' if ok else 'EX only: ' + modes.EX_FEATURES[feature])
        self._title()
        self.statusBar().showMessage(modes.describe(md), 8000)

    def game_command(self):
        cmd = self.settings.value('gamecmd_' + self.mode, None)
        if cmd is None and self.mode == modes.STANDARD:
            cmd = self.settings.value('gamecmd', None)        # setting of older versions
        return cmd if cmd is not None else modes.default_game_command(self.mode)

    def set_game_command(self):
        cur = self.game_command()
        t, ok = QtWidgets.QInputDialog.getText(
            self, 'Game command', 'Command that starts the game in %s mode ({game} = game folder):'
            % modes.NAMES[self.mode],
            QtWidgets.QLineEdit.Normal, cur)
        if ok and t.strip():
            self.settings.setValue('gamecmd_' + self.mode, t.strip())

    def export_text(self):
        if self.game is None:
            return
        if self.unsaved():
            self.statusBar().showMessage('Save first: the export reads the files.', 5000)
            return
        d = QtWidgets.QFileDialog.getExistingDirectory(self, 'Folder for the text tables (JSON lines)',
                                                       self.settings.value('textdir', ''))
        if not d:
            return
        import d6textio
        self.settings.setValue('textdir', d)
        d6textio.export_all(self.game.root, d, log=lambda t: self.statusBar().showMessage(t, 6000))

    def import_text(self):
        if self.game is None:
            return
        if self.unsaved() and not self.maybe_save():
            return
        d = QtWidgets.QFileDialog.getExistingDirectory(self, 'Folder with the text tables',
                                                       self.settings.value('textdir', ''))
        if not d:
            return
        import d6textio
        try:
            changed = d6textio.import_all(self.game.root, d, dry=True, log=lambda t: None)
        except (ValueError, KeyError, OSError) as e:
            QtWidgets.QMessageBox.warning(self, 'd6edit', 'Cannot read the text tables:\n%s' % e)
            return
        if not changed:
            self.statusBar().showMessage('The text tables match the game files: nothing to import.', 5000)
            return
        if QtWidgets.QMessageBox.question(self, 'd6edit', 'Write %d files?\n%s' % (
                len(changed), '\n'.join(changed[:30]))) != QtWidgets.QMessageBox.Yes:
            return
        d6textio.import_all(self.game.root, d, log=lambda t: self.statusBar().showMessage(t, 6000))
        self.open_game(self.game.root, self.spoke.number if self.spoke else 0)

    PLAY_CMD = sysutil.default_play_command()

    def set_play_command(self):
        cur = self.settings.value('playcmd', self.PLAY_CMD)
        t, ok = QtWidgets.QInputDialog.getText(
            self, 'Play from the camera',
            'Command ({game} = game folder, {slot} = save slot, {start} = spoke,x,y,z,yaw).\n'
            'Needs OpenDWWandWExpanded (--load-slot, --start-at):',
            QtWidgets.QLineEdit.Normal, cur)
        if not ok or not t.strip():
            return
        self.settings.setValue('playcmd', t.strip())
        slot, ok = QtWidgets.QInputDialog.getInt(
            self, 'Play from the camera', 'Save slot to resume (save/gameNN.sav, needs a party):',
            int(self.settings.value('playslot', 0)), 0, 99)
        if ok:
            self.settings.setValue('playslot', slot)

    def camera_start(self):
        """(spoke, x, y, z, yaw degrees) for the game: the point below the
        camera on a BSP floor or the terrain, facing the camera's direction."""
        v = self.view
        eye = np.asarray(v.eye, np.float64)
        f = v.forward()
        yaw = math.degrees(math.atan2(f[0], f[2])) % 360.0
        t = v.bsp_hit(eye, (0.0, -1.0, 0.0))
        if t is not None:
            y = eye[1] - t
        elif self.spoke.terrain is not None:
            y = self.spoke.ground_height(eye[0], eye[2])
        else:
            return None
        return self.spoke.number, float(eye[0]), float(y) + 64.0, float(eye[2]), yaw

    def play_from_camera(self):
        if self.game is None or self.spoke is None:
            return
        if not modes.available(self.mode, 'play_from_camera'):
            self.statusBar().showMessage('Play from the camera needs EX mode (Mode > EX).', 6000)
            return
        st = self.camera_start()
        if st is None:
            QtWidgets.QMessageBox.information(self, 'd6edit', 'There is no floor below the camera. '
                                              'Move the camera above a level floor or the terrain.')
            return
        if self.unsaved():
            r = QtWidgets.QMessageBox.question(self, 'd6edit', 'Save the changes before starting the game?',
                                               QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Cancel)
            if r != QtWidgets.QMessageBox.Save or not self.save():
                return
        start = '%d,%.0f,%.0f,%.0f,%.0f' % st
        cmd = self.settings.value('playcmd', self.PLAY_CMD)
        if not cmd:
            QtWidgets.QMessageBox.information(self, 'd6edit', 'Play from the camera needs OpenDWWandWExpanded '
                                              '(Linux for now). Set its command in File > Play from the camera settings.')
            return
        slot = str(int(self.settings.value('playslot', 0)))
        argv = [a.replace('{game}', self.game.root).replace('{slot}', slot).replace('{start}', start)
                for a in sysutil.split_command(cmd)]
        try:
            subprocess.Popen(argv, cwd=self.game.root)
            self.statusBar().showMessage('Started: %s' % ' '.join(argv), 8000)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, 'd6edit', 'Cannot start the game:\n%s\n\n'
                                          'Set the command with File > Play from the camera settings.' % e)

    def test_in_game(self):
        if self.game is None:
            return
        if self.unsaved():
            r = QtWidgets.QMessageBox.question(self, 'd6edit', 'Save the changes before starting the game?',
                                               QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Cancel)
            if r != QtWidgets.QMessageBox.Save or not self.save():
                return
        cmd = self.game_command()
        if not cmd:
            QtWidgets.QMessageBox.information(self, 'd6edit', 'No game command for %s mode on this system. '
                                              'Set one with File > Game command.' % modes.NAMES[self.mode])
            return
        argv = [a.replace('{game}', self.game.root) for a in sysutil.split_command(cmd)]
        try:
            subprocess.Popen(argv, cwd=self.game.root)
            self.statusBar().showMessage('Started: %s' % ' '.join(argv), 6000)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, 'd6edit', 'Cannot start the game:\n%s\n\n'
                                          'Set the command with File > Game command.' % e)

    def edit_db(self):
        if self.game is None:
            return
        if getattr(self, '_db_dlg', None) is None:
            self._db_dlg = db_ui.DatabaseEditor(self)
        self._db_dlg.show()
        self._db_dlg.raise_()

    def edit_models(self):
        if self.game is None:
            return
        if getattr(self, '_models_dlg', None) is None:
            self._models_dlg = models_ui.ModelTools(self)
        self._models_dlg.show()
        self._models_dlg.raise_()

    def redraw_automap(self):
        if self.spoke is None:
            return
        if QtWidgets.QMessageBox.question(
                self, 'Automap', 'Redraw the automap pages of this spoke from the terrain and the levels '
                '(saved files)? The old maps/lm0b%d.lm goes to the backup folder.' % self.spoke.number) \
                != QtWidgets.QMessageBox.Yes:
            return
        import d6automap
        import d6terrain
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            lm, prev = d6automap.regenerate(self.game.root, self.spoke.number, log=lambda t: None)
            path = d6terrain.find_ci(self.game.root, 'maps/lm0b%d.lm' % self.spoke.number)
            bdir = os.path.join(self.game.root, 'd6edit_backup', time.strftime('%Y%m%d-%H%M%S'))
            os.makedirs(bdir, exist_ok=True)
            shutil.copy2(path, bdir)
            data = lm.serialize()
            with open(path + '.d6edit-tmp', 'wb') as f:
                f.write(data)
            os.replace(path + '.d6edit-tmp', path)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.statusBar().showMessage('Automap redrawn: %d levels (%s)' % (len(prev), path), 8000)

    def edit_dialogue(self):
        if self.game is None:
            return
        if getattr(self, '_dlg_dlg', None) is None:
            self._dlg_dlg = dialogue_ui.DialogueEditor(self)
        self._dlg_dlg.show()
        self._dlg_dlg.raise_()

    def edit_mod(self):
        if getattr(self, '_mod_dlg', None) is None:
            self._mod_dlg = mod_ui.ModDialog(self)
        else:
            self._mod_dlg.refresh()
        self._mod_dlg.show()
        self._mod_dlg.raise_()

    def edit_spells(self):
        if self.game is None:
            return
        if getattr(self, '_spell_dlg', None) is None:
            self._spell_dlg = spells_ui.SpellEditor(self)
        self._spell_dlg.show()
        self._spell_dlg.raise_()

    def edit_geometry(self):
        if self.game is None:
            return
        if getattr(self, '_geo_dlg', None) is None:
            self._geo_dlg = geometry_ui.GeometryDialog(self)
        else:
            self._geo_dlg.refresh()
        self._geo_dlg.show()
        self._geo_dlg.raise_()

    def edit_textures(self):
        if self.game is None:
            return
        if getattr(self, '_tex_dlg', None) is None:
            self._tex_dlg = textures_ui.TexturesDialog(self)
        else:
            self._tex_dlg.refresh()
        self._tex_dlg.show()
        self._tex_dlg.raise_()

    def edit_text(self, select=None):
        if self.game is None:
            return
        if getattr(self, '_text_dlg', None) is None:
            self._text_dlg = text_ui.TextEditor(self)
        if select is not None:
            self._text_dlg.tabs.setCurrentIndex(0)
            self._text_dlg.fill(select=select)
        self._text_dlg.show()
        self._text_dlg.raise_()

    def text_users(self, tid):
        out = []
        for n in range(13):
            if self.spoke is not None and n == self.spoke.number and 'trig' in self.spoke.tables:
                tab = self.spoke.tables['trig'][1]
            else:
                data = world.read(self.game.root, 'D6Trig%02d.dat' % n)
                tab = d6data.TriggerTable.parse(data) if data else None
            if tab is None:
                continue
            for k, r in enumerate(tab.records, 1):
                ev = r.get('event')
                i = self.game.dcl.index(ev) if ev else -1
                if i < 0:
                    continue
                for pn, v in zip(self.game.dcl.params_of(i), r.get('params')):
                    if pn.upper() in ('MSG', 'TEXTID', 'TEXT') and v == tid:
                        out.append('spoke %d T%d' % (n, k))
        for k, r in enumerate(self.game.items.records, 1):
            if r.get('readtext') == tid:
                out.append('item %d %s (READ)' % (k, r.get('name')))
        return 'Used by: ' + (', '.join(out) if out else 'nothing')

    def tiles_changed(self):
        """New tile files on disk: rescan and refresh the terrain palette."""
        import terrain as terrain_mod
        self.game.tileset = terrain_mod.TileSet(self.game.root)
        if self.spoke is not None and self.spoke.ted is not None:
            self.spoke.ted.tileset = self.game.tileset
            self.terrain_panel.load_spoke(self.spoke)

    def models_changed(self):
        """Model files or slots changed on disk: reload what the view shows."""
        if self.models is not None and self.spoke is not None:
            self.models.drop_texlib(self.spoke.texlib)
            self.models.by_rec.clear()
        self.palette.previews.clear()
        if self.view.gl_ready:
            self.view.makeCurrent()
            for b in self.view.model_bufs.values():
                if b is not None:
                    b.delete()
            self.view.model_bufs = {}
        self.view.rebuild_markers()

    def db_changed(self):
        self._title()
        self.palette.fill()
        self.view.rebuild_markers()

    def edit_events(self):
        if self.game is None:
            return
        if getattr(self, '_events_dlg', None) is None:
            self._events_dlg = events_ui.EventEditor(self)
        self._events_dlg.show()
        self._events_dlg.raise_()

    def event_users(self, name):
        """(spoke, trigger index) of every trigger in the game using an event."""
        out = []
        nm = name.upper()
        for n in range(13):
            if self.spoke is not None and n == self.spoke.number and 'trig' in self.spoke.tables:
                tab = self.spoke.tables['trig'][1]
            else:
                data = world.read(self.game.root, 'D6Trig%02d.dat' % n)
                if not data:
                    continue
                tab = d6data.TriggerTable.parse(data)
            for k, r in enumerate(tab.records, 1):
                if r.get('event').upper() == nm:
                    out.append((n, k))
        return out

    def events_changed(self):
        o = self.inspector.obj
        if o is not None and o.cat == 'record' and o.table == 'trig':
            self.inspector.show_obj(o)
        self._title()

    def tables_changed(self):
        """Scripting table records were added/removed (exit wizard, undo)."""
        sp = self.spoke
        self.records = {}
        for key, (path, tab) in sp.tables.items():
            if key == 'boun':
                continue
            self.records[key] = [EdRecord(sp, key, i, r) for i, r in enumerate(tab.records, 1)]
        sp.build_objects()
        self.view.selected = None
        self.inspector.show_obj(None)
        self.build_tree()
        self.view.rebuild_markers()
        self._title()

    def nav_changed(self, select=None):
        """Nav structure changed (points added/removed/linked): rebuild the
        object list, mark the changed files, reselect."""
        sp = self.spoke
        for k in navedit.changed_keys(self.nav_saved, sp.navgraph.state()):
            sp.mark_dirty(k)
        sel = self.view.selected
        selkey = sel.key() if sel is not None else None
        sp.build_objects()
        self.build_tree()
        target = None
        if select is not None:
            for o in sp.objects:
                if navedit.ref_of(o) == tuple(select):
                    target = o
                    break
        elif selkey is not None:
            target = next((o for o in sp.objects if o.key() == selkey), None)
        self.view.selected = None
        self.view.rebuild_markers()
        if target is not None:
            self.view.select(target)
            self._tree_select(target)
        else:
            self.view.select(None)
        self._title()

    def terrain_edited(self, rect, water=False, quiet=False):
        self.spoke.mark_dirty(('TMR',))
        if not quiet:
            self.view.terrain_changed(rect, water=water)
        self.view.rebuild_markers()          # ground snapped objects follow the heights
        self._title()

    def _light_changed(self, obj):
        ted = self.spoke.ted
        old = self.light_pos.get(obj.key())
        new = obj.local_pos().copy() if obj.kind == 'L' else None
        pts = [p for p in (old, new) if p is not None]
        self.light_pos[obj.key()] = new
        if not pts:
            return
        r = ted.light_r + 1024
        xs = [p[0] for p in pts]
        zs = [p[2] for p in pts]
        rect = (max(0, int((min(xs) - r) // 1024)), max(0, int((min(zs) - r) // 1024)),
                min(ted.W - 1, int((max(xs) + r) // 1024) + 1), min(ted.D - 1, int((max(zs) + r) // 1024) + 1))
        ted.relight(rect)
        self.spoke.mark_dirty(('TMR',))
        self.view.terrain_changed(rect)

    def relight_all(self):
        ted = self.spoke.ted if self.spoke else None
        if ted is None:
            return
        rect = (0, 0, ted.W - 1, ted.D - 1)
        before = tools.terrain_state(ted, rect)
        ted.relight()
        self.undo.push(tools.TerrainCmd(self, 'Relight terrain', rect, before, tools.terrain_state(ted, rect)))
        self.view.terrain_changed(rect)
        self.statusBar().showMessage('Relit with ambient %d, k %.1f, radius %d.' % (
            ted.ambient, ted.light_k, ted.light_r), 6000)

    def fit_light(self):
        ted = self.spoke.ted if self.spoke else None
        if ted is None:
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            r = ted.fit_light()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if r:
            self.statusBar().showMessage('Light model: ambient %d, k %.1f, radius %d (mean error %.2f shades).' % (
                ted.ambient, r[1], r[2], r[0]), 8000)

    # ------------------------------------------------------------------ saving
    def unsaved(self):
        return bool((self.spoke is not None and self.spoke.dirty) or
                    (self.game is not None and (self.game.events.dirty or self.game.db_dirty)))

    def maybe_save(self):
        if not self.unsaved():
            return True
        r = QtWidgets.QMessageBox.question(self, 'd6edit', 'Save changes?',
                                           QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard |
                                           QtWidgets.QMessageBox.Cancel)
        if r == QtWidgets.QMessageBox.Cancel:
            return False
        if r == QtWidgets.QMessageBox.Save:
            return self.save()
        return True

    def save(self):
        sp = self.spoke
        if sp is None:
            return True
        if not self.unsaved():
            self.statusBar().showMessage('Nothing to save.', 3000)
            return True
        ev_written = []
        if self.game.events.dirty:
            try:
                ev_written = self.game.save_events()
            except Exception as e:
                traceback.print_exc()
                QtWidgets.QMessageBox.critical(self, 'd6edit', 'Saving the event scripts failed:\n%s' % e)
                return False
        if self.game.db_dirty:
            try:
                ev_written += self.game.save_db()
            except Exception as e:
                traceback.print_exc()
                QtWidgets.QMessageBox.critical(self, 'd6edit', 'Saving the databases failed:\n%s' % e)
                return False
        saved = sp.saved_state_files() if sp.dirty else []
        if saved:
            mb = QtWidgets.QMessageBox(self)
            mb.setWindowTitle('d6edit')
            mb.setText('%s exists in the game folder.\n\nIt is the saved state of this spoke from playing it '
                       '(object positions, dead monsters, opened doors). While it exists the game uses it '
                       'instead of the edited files.' % os.path.basename(saved[0]))
            mv = mb.addButton('Move it to the backup folder', QtWidgets.QMessageBox.AcceptRole)
            mb.addButton('Keep it', QtWidgets.QMessageBox.RejectRole)
            cancel = mb.addButton(QtWidgets.QMessageBox.Cancel)
            mb.exec()
            if mb.clickedButton() is cancel:
                return False
            if mb.clickedButton() is mv:
                bdir = os.path.join(sp.root, 'd6edit_backup', time.strftime('%Y%m%d-%H%M%S'))
                os.makedirs(bdir, exist_ok=True)
                for p in saved:
                    shutil.move(p, os.path.join(bdir, os.path.basename(p)))
        try:
            written = sp.save()
        except Exception as e:
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, 'd6edit', 'Save failed:\n%s' % e)
            return False
        self.undo.setClean()
        if getattr(sp, 'navgraph', None) is not None:
            self.nav_saved = sp.navgraph.state()
        self._title()
        self.statusBar().showMessage('Saved %s (old versions in d6edit_backup)' %
                                     ', '.join(os.path.basename(p) for p in written + ev_written), 8000)
        return True

    def closeEvent(self, e):
        if self.maybe_save():
            # the undo stack is destroyed after the window and emits cleanChanged
            # while it clears (PyQt6: "wrapped C/C++ object ... deleted")
            self._closing = True
            try:
                self.undo.cleanChanged.disconnect()
            except (TypeError, RuntimeError):
                pass
            e.accept()
        else:
            e.ignore()


def apply_style(app):
    app.setStyle('Fusion')
    pal = QtGui.QPalette()
    for role, c in ((QtGui.QPalette.Window, '#2b2d31'), (QtGui.QPalette.WindowText, '#e0e0e0'),
                    (QtGui.QPalette.Base, '#1e1f22'), (QtGui.QPalette.AlternateBase, '#2b2d31'),
                    (QtGui.QPalette.Text, '#e0e0e0'), (QtGui.QPalette.Button, '#3a3c41'),
                    (QtGui.QPalette.ButtonText, '#e0e0e0'), (QtGui.QPalette.Highlight, '#3d6fb5'),
                    (QtGui.QPalette.HighlightedText, '#ffffff'), (QtGui.QPalette.ToolTipBase, '#3a3c41'),
                    (QtGui.QPalette.ToolTipText, '#e0e0e0')):
        pal.setColor(role, QtGui.QColor(c))
    for role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText):
        pal.setColor(QtGui.QPalette.Disabled, role, QtGui.QColor('#6b6e75'))     # disabled = grey
    app.setPalette(pal)


LOG = os.path.join(os.path.expanduser('~'), '.d6edit.log')
_shown = set()


def report_error(title, text, parent=None):
    """Errors go to the terminal, to ~/.d6edit.log and into a dialog (once per error)."""
    sys.stderr.write('%s\n%s\n' % (title, text))
    try:
        with open(LOG, 'a') as f:
            f.write('%s  %s\n%s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), title, text))
    except OSError:
        pass
    key = text.strip().splitlines()[-1] if text.strip() else title
    if key in _shown or QtWidgets.QApplication.instance() is None:
        return
    _shown.add(key)
    try:
        box = QtWidgets.QMessageBox(parent or QtWidgets.QApplication.activeWindow())
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle('d6edit error')
        box.setText('%s:\n%s\n\nThe full report is in %s (also under "Show Details").'
                    % (title, key, LOG))
        box.setDetailedText(text)
        box.exec()
    except Exception:
        pass


def _excepthook(tp, val, tb):
    report_error('Unexpected error', ''.join(traceback.format_exception(tp, val, tb)))


def main(argv):
    sys.excepthook = _excepthook
    args = [a for a in argv[1:]]
    spoke = None
    if '--spoke' in args:
        i = args.index('--spoke')
        spoke = int(args[i + 1])
        del args[i:i + 2]
    shot = None
    if '--screenshot' in args:          # testing: save a screenshot and quit
        i = args.index('--screenshot')
        shot = args[i + 1]
        del args[i:i + 2]
    fmt = QtGui.QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QtGui.QSurfaceFormat.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QtGui.QSurfaceFormat.setDefaultFormat(fmt)
    app = QtWidgets.QApplication(argv)
    app.setApplicationName('d6edit')
    apply_style(app)
    w = MainWindow(args[0] if args else None, spoke)
    w.show()
    if shot:
        def grab():
            w.grab().save(shot)
            app.quit()
        QtCore.QTimer.singleShot(4000, grab)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main(sys.argv))
