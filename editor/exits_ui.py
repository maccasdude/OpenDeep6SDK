"""
d6edit - exits: every way out of the spoke (docs/formats/exits.md), drawn in
the view, with "go to destination" and a wizard for new walk-in exits.
"""
import numpy as np

from qtcompat import QtCore, QtGui, QtWidgets

import d6data
import d6exits
import d6level

SDK_EVENT = '@SDKSEGMENTEXIT'
SDK_PARAMS = ['!BOUNDNUM', 'SEGMENT', 'BSPNUM', 'SEGSWITCH', 'SEGSTATUS', 'DELAY']
SDK_CODE = """; walk-in exit (OpenDeep6SDK): when the whole party stands in the box,
; load spoke SEGMENT and place the party on BSP slot BSPNUM
    IFALLINBOUND !BOUNDNUM -> go
    END
go:
    Q:DELAY DELAY
    LOADSEGMENT SEGMENT, BSPNUM, SEGSWITCH, SEGSTATUS, !BOUNDNUM
    END
"""


class TablesCmd(QtGui.QUndoCommand):
    """Raw records of spoke tables before/after (records may be added)."""

    def __init__(self, win, text, keys, before, after):
        super().__init__(text)
        self.win, self.keys, self.before, self.after = win, keys, before, after
        self.first = True

    @staticmethod
    def state(spoke, keys):
        return dict((k, [bytes(r.raw) for r in spoke.tables[k][1].records]) for k in keys if k in spoke.tables)

    def _apply(self, st):
        sp = self.win.spoke
        for k, raws in st.items():
            t = sp.tables[k][1]
            del t.records[len(raws):]
            for i, raw in enumerate(raws):
                if i < len(t.records):
                    t.records[i].raw[:] = raw
                else:
                    t.new_record().raw[:] = raw
            sp.mark_dirty(('TABLE', k))
        self.win.tables_changed()

    def redo(self):
        if self.first:
            self.first = False
            return
        self._apply(self.after)

    def undo(self):
        self._apply(self.before)


class ExitsPanel(QtWidgets.QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.exits = []
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        row = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton('Refresh')
        b.clicked.connect(self.refresh)
        row.addWidget(b)
        b = QtWidgets.QPushButton('New exit...')
        b.clicked.connect(self.new_exit)
        row.addWidget(b)
        lay.addLayout(row)
        self.list = QtWidgets.QListWidget()
        self.list.setWordWrap(True)
        self.list.currentRowChanged.connect(self._sel)
        self.list.itemDoubleClicked.connect(lambda it: self.frame())
        lay.addWidget(self.list, 1)
        self.detail = QtWidgets.QLabel()
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet('color: #9ab')
        self.detail.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.detail)
        row = QtWidgets.QHBoxLayout()
        for t, fn in (('Show', self.frame), ('Go to destination', self.goto_dest), ('Open trigger', self.open_trigger)):
            b = QtWidgets.QPushButton(t)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)

    def refresh(self):
        sp = self.win.spoke
        self.list.clear()
        self.detail.clear()
        self.exits = []
        if sp is None:
            return
        root = self.win.game.root
        d6exits.clear_cache()
        d6exits.set_live(root, sp.number, dict((k, v[1]) for k, v in sp.tables.items()),
                         self.win.game.events.build_dcl(), self.win.game.events.build_cod())
        try:
            self.exits = d6exits.list_exits(root, sp.number)
        except Exception as e:
            self.detail.setText('Could not list exits: %s' % e)
            self.exits = []
        for e in self.exits:
            it = QtWidgets.QListWidgetItem('T%s  %s' % (e.get('trigger'), e['description']))
            self.list.addItem(it)
        self.win.view.exit_boxes = [(np.array(e['box_min']), np.array(e['box_max']), self._short(e))
                                    for e in self.exits if e.get('box_min') is not None and e.get('box_max') is not None]
        self.win.view.rebuild_markers()

    @staticmethod
    def _short(e):
        if e.get('dest_spoke') is not None and e.get('action') == 'segment':
            return 'EXIT -> spoke %d' % e['dest_spoke']
        if e.get('action') == 'town':
            return 'EXIT -> town'
        if e.get('action') == 'bsp':
            return 'to BSP %s' % e.get('dest_bsp')
        return e.get('action', 'exit')

    def current(self):
        r = self.list.currentRow()
        return self.exits[r] if 0 <= r < len(self.exits) else None

    def _sel(self, row):
        e = self.current()
        if e is None:
            return
        lines = [e['description'], 'event %s (trigger %s)' % (e.get('event'), e.get('trigger'))]
        if e.get('dest_pos') is not None:
            lines.append('arrival (world, destination spoke): %s' % (tuple(int(x) for x in e['dest_pos']),))
        if e.get('params'):
            lines.append('params: ' + ', '.join('%s=%s' % kv for kv in e['params'].items()))
        if e.get('conditions'):
            lines.append('only when:')
            lines += ['  ' + c for c in e['conditions']]
        self.detail.setText('\n'.join(lines))
        self.win.view.exit_sel = row
        self.win.view.rebuild_markers()

    def frame(self):
        e = self.current()
        if e is None:
            return
        p = e.get('source_pos')
        if p is None and e.get('box_min') is not None:
            p = (np.array(e['box_min']) + np.array(e['box_max'])) / 2
        if p is not None:
            v = self.win.view
            v.eye = np.array(p, float) - v.forward() * 6000 + (0, 2500, 0)
            v.update()

    def goto_dest(self):
        e = self.current()
        if e is None or e.get('dest_pos') is None:
            self.win.statusBar().showMessage('No destination position for this exit.', 4000)
            return
        dest = e.get('dest_spoke')
        if dest is not None and dest != self.win.spoke.number:
            self.win.load_spoke(dest)
            if self.win.spoke is None or self.win.spoke.number != dest:
                return
        v = self.win.view
        p = np.array(e['dest_pos'], float)
        v.eye = p - v.forward() * 5000 + (0, 2000, 0)
        v.set_brush(p, 400)
        v.update()
        self.win.statusBar().showMessage('Arrival point of %s (yellow ring)' % e['description'], 8000)

    def open_trigger(self):
        e = self.current()
        if e is None or not e.get('trigger'):
            return
        r = self.win.record('trig', e['trigger'])
        if r is not None:
            self.win.select(r)

    def new_exit(self):
        if self.win.spoke is None:
            return
        dlg = NewExitDialog(self.win)
        if dlg.exec():
            self.refresh()


class NewExitDialog(QtWidgets.QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        sp = win.spoke
        self.setWindowTitle('New exit')
        lay = QtWidgets.QFormLayout(self)
        intro = QtWidgets.QLabel(
            'Creates a walk-in exit: a box that, once the whole party stands in it, loads another spoke '
            '(LOADSEGMENT). The party keeps its position relative to the source BSP and is placed on the '
            'destination BSP slot. Make the return exit in the destination spoke away from the arrival point.')
        intro.setWordWrap(True)
        lay.addRow(intro)
        p = win.view.selected.world_pos() if win.view.selected is not None else win.view.center_point()
        self.p = np.array(p, float)
        lay.addRow('Box centre', QtWidgets.QLabel('%d, %d, %d (selection or view centre)' % tuple(self.p)))
        self.size = [QtWidgets.QSpinBox() for _ in range(3)]
        row = QtWidgets.QHBoxLayout()
        for s, v in zip(self.size, (2048, 3072, 2048)):
            s.setRange(256, 65536)
            s.setSingleStep(256)
            s.setValue(v)
            s.valueChanged.connect(self._update)
            row.addWidget(s)
        w = QtWidgets.QWidget()
        w.setLayout(row)
        lay.addRow('Box size x, y, z', w)
        self.src = QtWidgets.QComboBox()
        self.src.addItem('-1  none (terrain, world coordinates)', -1)
        guess = -1
        for b in sp.bsps:
            self.src.addItem('%d  %s' % (b.slot, b.name.upper()), b.slot)
            if b.mesh:
                lo, hi = b.mesh['pos'].min(0), b.mesh['pos'].max(0)
                if np.all(self.p >= lo - 64) and np.all(self.p <= hi + 64):
                    guess = b.slot
        self.src.setCurrentIndex(max(0, self.src.findData(guess)))
        self.src.currentIndexChanged.connect(self._update)
        lay.addRow('Source BSP slot', self.src)
        self.dspoke = QtWidgets.QComboBox()
        for n in range(13):
            self.dspoke.addItem(d6data.SPOKE_NAMES.get(n, str(n)), n)
        self.dspoke.currentIndexChanged.connect(self._fill_slots)
        lay.addRow('Destination spoke', self.dspoke)
        self.dslot = QtWidgets.QComboBox()
        self.dslot.currentIndexChanged.connect(self._update)
        lay.addRow('Destination BSP slot', self.dslot)
        self.delay = QtWidgets.QSpinBox()
        self.delay.setRange(0, 10000)
        self.delay.setValue(500)
        self.delay.setSuffix(' ms')
        lay.addRow('Delay', self.delay)
        self.arrival = QtWidgets.QLabel()
        self.arrival.setWordWrap(True)
        lay.addRow('Arrival', self.arrival)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.create)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)
        self.dspoke.setCurrentIndex(1 if sp.number == 0 else 0)
        self._fill_slots()

    def _fill_slots(self):
        n = self.dspoke.currentData()
        self.dslot.blockSignals(True)
        self.dslot.clear()
        self.dslot.addItem('-1  none (keep world coordinates)', -1)
        self.dest = {}
        for pl in d6level.spoke_bsps(n, self.win.game.root):
            self.dslot.addItem('%d  %s' % (pl['slot'], pl['name'].upper()), pl['slot'])
            self.dest[pl['slot']] = np.array(pl['origin'], float)
        self.dslot.blockSignals(False)
        if self.dslot.count() > 1:
            self.dslot.setCurrentIndex(1)
        self._update()

    def _origin_src(self):
        a = self.src.currentData()
        b = self.win.spoke.bsp_by_slot(a) if a is not None and a >= 0 else None
        return b.origin if b is not None else None

    def _update(self):
        a, b = self.src.currentData(), self.dslot.currentData()
        if a is None or b is None:
            return
        if b == -1:
            arr = self.p
            note = 'same world coordinates in the destination spoke'
        else:
            o = self._origin_src()
            local = self.p - (o if o is not None else 0)
            arr = local + self.dest.get(b, 0)
            note = 'position relative to the source BSP, placed on destination slot %d' % b
            if o is None:
                note += ' (source is terrain: the game uses the BSP the PC stands in; pick a source BSP)'
        self.arr = arr
        self.arrival.setText('%d, %d, %d  -  %s' % (arr[0], arr[1], arr[2], note))

    def create(self):
        win, sp = self.win, self.win.spoke
        for k in ('trig', 'boun'):
            if k not in sp.tables:
                QtWidgets.QMessageBox.warning(self, 'd6edit', 'This spoke has no %s table file.' % k)
                return
        es = win.game.events
        if es.dcl.index(SDK_EVENT) < 0:
            es.set_event(es.n, SDK_EVENT, SDK_PARAMS, 'Walk-in exit: load another spoke when the whole party is in a box',
                         SDK_CODE)
            win.events_changed()
        keys = ['trig', 'boun']
        before = TablesCmd.state(sp, keys)
        a = self.src.currentData()
        o = self._origin_src()
        c = self.p - (o if o is not None else 0)
        h = np.array([s.value() for s in self.size], float) / 2
        btab = sp.tables['boun'][1]
        ttab = sp.tables['trig'][1]
        box = btab.new_record()
        bi = len(btab.records)
        ti = len(ttab.records) + 1
        box.set('state', 0)
        box.set('bsp', a if a is not None and a >= 0 else -1)
        box.set('trigger', ti)
        box.set('enabled', 1)
        box.set('oneshot', 0)
        box.set('who', 0)
        box.set('min', [float(c[0] - h[0]), float(c[1] - 200), float(c[2] - h[2])])
        box.set('max', [float(c[0] + h[0]), float(c[1] - 200 + 2 * h[1]), float(c[2] + h[2])])
        tr = ttab.new_record()
        tr.set('event', SDK_EVENT)
        tr.set('nparams', len(SDK_PARAMS))
        tr.set('enabled', 1)
        tr.set('state', 0)
        tr.set('mode', 0)
        tr.set('oneshot', 0)
        params = [0] * 30
        params[:6] = [bi, self.dspoke.currentData(), self.dslot.currentData(), -1, 0, self.delay.value()]
        tr.set('params', params)
        sp.mark_dirty(('TABLE', 'boun'))
        sp.mark_dirty(('TABLE', 'trig'))
        win.undo.push(TablesCmd(win, 'New exit', keys, before, TablesCmd.state(sp, keys)))
        win.tables_changed()
        win.statusBar().showMessage('Exit created: box %d fires trigger %d (%s). Save, then test with a new game.' % (
            bi, ti, SDK_EVENT), 10000)
        self.accept()
