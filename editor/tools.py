"""
d6edit - viewport tools: terrain brush and click-to-place.

A tool gets the viewport's left-button events while it is active
(Viewport.tool); the right button still looks around and the wheel still
moves the camera (Ctrl + wheel changes the brush size).
"""
import numpy as np

from qtcompat import QtCore, QtGui, QtWidgets

import d6model


# ---------------------------------------------------------------------------
# undo for terrain strokes
# ---------------------------------------------------------------------------
class TerrainCmd(QtGui.QUndoCommand):
    """Tile rect + BLT table + corner types before/after a stroke."""

    def __init__(self, win, text, rect, before, after):
        super().__init__(text)
        self.win, self.rect, self.before, self.after = win, rect, before, after
        self.first = True

    def _apply(self, st):
        ted = self.win.spoke.ted
        x0, z0, x1, z1 = self.rect
        ted.t[z0:z1 + 1, x0:x1 + 1] = st['tiles']
        ted.corner_types[z0:z1 + 1, x0:x1 + 1] = st['types']
        ted.map.blt[:] = st['blt']
        self.win.terrain_edited(self.rect, water=True)

    def redo(self):
        if self.first:
            self.first = False
            self.win.terrain_edited(self.rect, water=True, quiet=True)
            return
        self._apply(self.after)

    def undo(self):
        self._apply(self.before)


def terrain_state(ted, rect):
    x0, z0, x1, z1 = rect
    return dict(tiles=ted.t[z0:z1 + 1, x0:x1 + 1].copy(),
                types=ted.corner_types[z0:z1 + 1, x0:x1 + 1].copy(),
                blt=list(ted.map.blt))


# ---------------------------------------------------------------------------
class TerrainTool(object):
    MODES = [('raise', 'Raise'), ('lower', 'Lower'), ('smooth', 'Smooth'), ('flatten', 'Flatten'),
             ('noise', 'Roughen'), ('paint', 'Paint type'), ('water', 'Water'), ('pick', 'Pick'),
             ('forest', 'Tree wall'), ('castle', 'Castle wall'), ('clearwall', 'Clear wall')]

    def __init__(self, win):
        self.win = win
        self.mode = 'raise'
        self.radius = 3000.0
        self.strength = 120.0
        self.letter = None
        self.water = 1
        self.active = False
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self._repeat)
        self.last = None
        self.target = None
        self.failed = 0

    # ---------------------------------------------------------------- helpers
    def _hit(self, view, e):
        o, d = view.ray(e.position().x(), e.position().y())
        return view.terrain_hit(o, d)

    def hover(self, view, e):
        h = self._hit(view, e)
        self.last = h
        view.set_brush(h, self.radius if self.mode not in ('pick',) else 300)
        view.overlay.hud = self.win.tile_info(h)
        view.overlay.update()

    def wheel(self, view, d):
        self.radius = float(np.clip(self.radius * (1.15 if d > 0 else 1 / 1.15), 300, 40000))
        self.win.terrain_panel.sync()
        if self.last is not None:
            view.set_brush(self.last, self.radius)

    # ---------------------------------------------------------------- stroke
    def press(self, view, e):
        ted = self.win.spoke.ted if self.win.spoke else None
        if ted is None:
            return
        h = self._hit(view, e)
        if h is None:
            return
        if self.mode == 'pick':
            x, z = int(h[0] // 1024), int(h[2] // 1024)
            if 0 <= x < ted.W - 1 and 0 <= z < ted.D - 1:
                n = ted.tile_name(x, z)
                c = ted.corner_types[int(round(h[2] / 1024)), int(round(h[0] / 1024))]
                if c != '?':
                    self.letter = c
                self.target = float(ted.t['height'][int(round(h[2] / 1024)), int(round(h[0] / 1024))])
                self.win.terrain_panel.sync()
                self.win.statusBar().showMessage('Picked type %s (tile %s), height %d' % (c, n, self.target), 5000)
            return
        self.active = True
        self.before = ted.t.copy()
        self.before_types = ted.corner_types.copy()
        self.before_blt = list(ted.map.blt)
        self.rect = None
        self.failed = 0
        if self.mode == 'flatten':
            self.target = ted.t['height'][int(round(h[2] / 1024)), int(round(h[0] / 1024))]
        self.last = h
        self._apply(view, h)
        if self.mode in ('raise', 'lower', 'smooth', 'flatten', 'noise'):
            self.timer.start(40)
        self.view = view

    def drag(self, view, e):
        h = self._hit(view, e)
        if h is None:
            return
        moved = self.last is None or np.linalg.norm(h - self.last) > self.radius * 0.2
        self.last = h
        view.set_brush(h, self.radius)
        view.overlay.hud = self.win.tile_info(h)
        if self.active and (moved or self.mode in ('paint', 'water', 'forest', 'castle', 'clearwall')):
            self._apply(view, h)

    def _repeat(self):
        if self.active and self.last is not None:
            self._apply(self.view, self.last)

    def _apply(self, view, h):
        ted = self.win.spoke.ted
        if self.mode in ('raise', 'lower', 'smooth', 'flatten', 'noise'):
            r = ted.sculpt(self.mode, h[0], h[2], self.radius, self.strength, target=self.target)
            water = False
        elif self.mode == 'paint':
            if not self.letter:
                self.win.statusBar().showMessage('Pick a terrain type in the Terrain panel first.', 4000)
                return
            r, nf = ted.paint_types(h[0], h[2], self.radius, self.letter)
            self.failed += nf
            water = False
        elif self.mode == 'water':
            r = ted.set_field('water', h[0], h[2], self.radius, self.water)
            water = True
        elif self.mode in ('forest', 'castle', 'clearwall'):
            r = ted.paint_walls(h[0], h[2], self.radius, {'forest': 1, 'castle': 2, 'clearwall': 0}[self.mode])
            water = False
        else:
            return
        self.rect = r if self.rect is None else (min(self.rect[0], r[0]), min(self.rect[1], r[1]),
                                                 max(self.rect[2], r[2]), max(self.rect[3], r[3]))
        view.terrain_changed(r, water=water)
        if self.mode != 'paint' and self.mode != 'water':
            view.set_brush(h, self.radius)

    def release(self, view, e):
        self.timer.stop()
        if not self.active:
            return
        self.active = False
        if self.rect is None:
            return
        ted = self.win.spoke.ted
        x0, z0, x1, z1 = self.rect
        x0, z0 = max(0, x0 - 1), max(0, z0 - 1)
        x1, z1 = min(ted.W - 1, x1 + 1), min(ted.D - 1, z1 + 1)
        rect = (x0, z0, x1, z1)
        before = dict(tiles=self.before[z0:z1 + 1, x0:x1 + 1].copy(),
                      types=self.before_types[z0:z1 + 1, x0:x1 + 1].copy(), blt=self.before_blt)
        after = terrain_state(ted, rect)
        if np.array_equal(before['tiles'], after['tiles']) and before['blt'] == after['blt']:
            return
        name = dict(self.MODES)[self.mode]
        self.win.undo.push(TerrainCmd(self.win, 'Terrain: %s' % name, rect, before, after))
        if self.failed:
            self.win.statusBar().showMessage(
                '%d tiles kept their old texture: the game has no tile for that corner combination '
                '(paint next to types that have transitions).' % self.failed, 8000)


class TerrainPanel(QtWidgets.QWidget):
    """Options of the terrain tool (dock)."""

    def __init__(self, win, tool):
        super().__init__()
        self.win, self.tool = win, tool
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.enable = QtWidgets.QPushButton('Terrain brush: off')
        self.enable.setCheckable(True)
        self.enable.toggled.connect(self._toggle)
        lay.addWidget(self.enable)
        grid = QtWidgets.QGridLayout()
        self.mode_btns = {}
        for i, (m, name) in enumerate(TerrainTool.MODES):
            b = QtWidgets.QToolButton()
            b.setText(name)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, m=m: self._mode(m))
            grid.addWidget(b, i // 4, i % 4)
            self.mode_btns[m] = b
        lay.addLayout(grid)
        form = QtWidgets.QFormLayout()
        self.radius = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.radius.setRange(3, 400)
        self.radius.valueChanged.connect(lambda v: setattr(tool, 'radius', v * 100.0) or self._label())
        form.addRow('Size', self.radius)
        self.strength = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.strength.setRange(10, 1000)
        self.strength.valueChanged.connect(lambda v: setattr(tool, 'strength', float(v)) or self._label())
        form.addRow('Strength', self.strength)
        self.info = QtWidgets.QLabel()
        form.addRow(self.info)
        self.water = QtWidgets.QComboBox()
        for v, n in ((0, '0  none'), (1, '1  water, level 0'), (2, '2  water, level 0 (b)'),
                     (3, '3  water, level 7936'), (4, '4  level 7936 (b)'), (5, '5  water, level 5120'),
                     (6, '6  level 5120 (b)')):
            self.water.addItem(n, v)
        self.water.currentIndexChanged.connect(lambda i: setattr(tool, 'water', self.water.itemData(i)))
        form.addRow('Water', self.water)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel('Terrain types (Paint type):'))
        self.types = QtWidgets.QListWidget()
        self.types.setViewMode(QtWidgets.QListView.IconMode)
        self.types.setIconSize(QtCore.QSize(56, 56))
        self.types.setGridSize(QtCore.QSize(70, 80))
        self.types.setResizeMode(QtWidgets.QListView.Adjust)
        self.types.setMovement(QtWidgets.QListView.Static)
        self.types.currentItemChanged.connect(self._type)
        lay.addWidget(self.types, 1)
        self.relight_btn = QtWidgets.QPushButton('Relight whole map from lights')
        self.relight_btn.clicked.connect(win.relight_all)
        lay.addWidget(self.relight_btn)
        hint = QtWidgets.QLabel('Left drag: apply   Ctrl+wheel: size\n'
                                'Lights (View > Lights, .LIT) bake the terrain light;\n'
                                'moving one relights the ground around it.')
        hint.setStyleSheet('color: #9ab')
        lay.addWidget(hint)
        self.sync()

    def _label(self):
        self.info.setText('radius %d units (%.1f tiles), strength %d' %
                          (self.tool.radius, self.tool.radius / 1024, self.tool.strength))

    def _toggle(self, on):
        self.enable.setText('Terrain brush: on' if on else 'Terrain brush: off')
        self.win.set_tool(self.tool if on else None)

    def _mode(self, m):
        self.tool.mode = m
        if not self.enable.isChecked():
            self.enable.setChecked(True)

    def _type(self, cur, prev):
        if cur is not None:
            self.tool.letter = cur.data(QtCore.Qt.UserRole)
            if self.tool.mode != 'paint':
                self.mode_btns['paint'].setChecked(True)
                self._mode('paint')

    def sync(self):
        t = self.tool
        for w, v in ((self.radius, int(t.radius / 100)), (self.strength, int(t.strength))):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self.mode_btns[t.mode].setChecked(True)
        for i in range(self.types.count()):
            it = self.types.item(i)
            if it.data(QtCore.Qt.UserRole) == t.letter:
                self.types.blockSignals(True)
                self.types.setCurrentItem(it)
                self.types.blockSignals(False)
        self._label()

    def load_spoke(self, spoke):
        self.types.clear()
        has = spoke is not None and spoke.ted is not None
        self.setEnabled(has)
        if not has:
            self.enable.setChecked(False)
            return
        ted = spoke.ted
        used = set(ted.used_types())
        for c, name in sorted(ted.fill_type_info().items(), key=lambda kv: (kv[0] not in used, kv[0])):
            img = ted.tileset.image(name)
            if img is None:
                continue
            qi = QtGui.QImage(np.ascontiguousarray(img).data, 128, 128, 128 * 3, QtGui.QImage.Format_RGB888).copy()
            it = QtWidgets.QListWidgetItem(QtGui.QIcon(QtGui.QPixmap.fromImage(qi)),
                                           c + ('' if c in used else ' *'))
            it.setData(QtCore.Qt.UserRole, c)
            it.setToolTip('Type %s%s' % (c, '' if c in used else ' (not used on this map yet)'))
            self.types.addItem(it)
        self.sync()


# ---------------------------------------------------------------------------
# object palette: browse monsters / items / props with a preview, place by click
# ---------------------------------------------------------------------------
class PlaceTool(object):
    def __init__(self, win):
        self.win = win
        self.kind, self.recno = 'M', 1

    def hover(self, view, e):
        o, d = view.ray(e.position().x(), e.position().y())
        h = view.ground_hit(o, d)
        view.set_brush(h, 250)
        view.overlay.hud = 'Click to place %s%03d %s   (Esc / right panel button to stop)' % (
            self.kind, self.recno, self.win.game.name(self.kind, self.recno))
        view.overlay.update()

    def press(self, view, e):
        o, d = view.ray(e.position().x(), e.position().y())
        h = view.ground_hit(o, d)
        if h is not None:
            self.win.add_object(self.kind, at=h, recno=self.recno,
                                keep_tool=bool(e.modifiers() & QtCore.Qt.ShiftModifier))

    def drag(self, view, e):
        pass

    def release(self, view, e):
        pass

    def wheel(self, view, d):
        pass


class PalettePanel(QtWidgets.QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.tool = PlaceTool(win)
        self.previews = {}
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        row = QtWidgets.QHBoxLayout()
        self.kind = QtWidgets.QComboBox()
        for k, n in (('M', 'Monsters'), ('I', 'Items'), ('P', 'Props')):
            self.kind.addItem(n, k)
        self.kind.currentIndexChanged.connect(lambda _: self.fill())
        row.addWidget(self.kind)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('search')
        self.search.textChanged.connect(self._filter)
        row.addWidget(self.search, 1)
        lay.addLayout(row)
        self.list = QtWidgets.QListWidget()
        self.list.currentItemChanged.connect(self._sel)
        self.list.itemDoubleClicked.connect(lambda it: self.place())
        lay.addWidget(self.list, 1)
        self.preview = QtWidgets.QLabel()
        self.preview.setFixedHeight(200)
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setStyleSheet('background: #303338')
        lay.addWidget(self.preview)
        self.model_label = QtWidgets.QLabel()
        self.model_label.setStyleSheet('color: #9ab')
        lay.addWidget(self.model_label)
        self.place_btn = QtWidgets.QPushButton('Place by clicking in the view')
        self.place_btn.setCheckable(True)
        self.place_btn.toggled.connect(self._placing)
        lay.addWidget(self.place_btn)
        hint = QtWidgets.QLabel('Shift+click places several.')
        hint.setStyleSheet('color: #9ab')
        lay.addWidget(hint)

    def fill(self):
        self.list.clear()
        if self.win.game is None:
            return
        k = self.kind.currentData()
        for n, name in self.win.game.names(k):
            if not name:
                continue
            it = QtWidgets.QListWidgetItem('%s%03d  %s' % (k, n, name))
            it.setData(QtCore.Qt.UserRole, n)
            self.list.addItem(it)
        self._filter(self.search.text())

    def _filter(self, t):
        t = t.lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _sel(self, cur, prev):
        if cur is None:
            return
        k, n = self.kind.currentData(), cur.data(QtCore.Qt.UserRole)
        self.tool.kind, self.tool.recno = k, n
        self.preview.setPixmap(self.pixmap(k, n, 200))
        path = self.win.models.path_for(k, n) if self.win.models else None
        self.model_label.setText(path or '(no model)')

    def pixmap(self, k, n, size):
        key = (k, n, size)
        if key not in self.previews:
            pm = QtGui.QPixmap()
            try:
                path = self.win.models.path_for(k, n)
                if path:
                    m = d6model.load_model(self.win.game.root, path)
                    img = d6model.render(m, size=size, bg=(48, 51, 56))
                    img = np.ascontiguousarray(img[..., :3], np.uint8)
                    qi = QtGui.QImage(img.data, img.shape[1], img.shape[0], img.shape[1] * 3,
                                      QtGui.QImage.Format_RGB888).copy()
                    pm = QtGui.QPixmap.fromImage(qi)
            except Exception as ex:
                print('preview %s%d: %s' % (k, n, ex))
            self.previews[key] = pm
        return self.previews[key]

    def place(self):
        self.place_btn.setChecked(True)

    def _placing(self, on):
        self.place_btn.setText('Placing: click in the view' if on else 'Place by clicking in the view')
        self.win.set_tool(self.tool if on else None)

    def show_record(self, k, n):
        idx = self.kind.findData(k)
        if idx >= 0 and self.kind.currentIndex() != idx:
            self.kind.setCurrentIndex(idx)
        for i in range(self.list.count()):
            if self.list.item(i).data(QtCore.Qt.UserRole) == n:
                self.list.setCurrentRow(i)
                break


# ---------------------------------------------------------------------------
# nav graph tool
# ---------------------------------------------------------------------------
class NavCmd(QtGui.QUndoCommand):
    """Whole nav state (all .nvs/.l2n of the spoke, terrain .NAV, D6LINK) before/after."""

    def __init__(self, win, text, before, after):
        super().__init__(text)
        self.win, self.before, self.after = win, before, after
        self.first = True

    def _apply(self, st):
        self.win.spoke.navgraph.restore(st)
        self.win.nav_changed()

    def redo(self):
        if self.first:
            self.first = False
            return
        self._apply(self.after)

    def undo(self):
        self._apply(self.before)


class NavTool(object):
    MODES = [('add', 'Add point'), ('link', 'Link / unlink'), ('delete', 'Delete point')]

    def __init__(self, win):
        self.win = win
        self.mode = 'add'
        self.first = None

    def _pick(self, view, e):
        import navedit
        return view.pick_object(e.position().x(), e.position().y(), filt=lambda o: navedit.ref_of(o) is not None)

    def hover(self, view, e):
        msg = {'add': 'Click on the floor / ground to add a nav point',
               'link': 'Click two nav points to link them (again to unlink)' +
                       ('  -  first: %s' % self.first.label() if self.first is not None else ''),
               'delete': 'Click a nav point to delete it'}[self.mode]
        view.overlay.hud = msg
        if self.mode == 'add':
            o, d = view.ray(e.position().x(), e.position().y())
            view.set_brush(view.ground_hit(o, d), 120)
        view.overlay.update()

    def press(self, view, e):
        import navedit
        win = self.win
        g = win.spoke.navgraph
        before = g.state()
        try:
            if self.mode == 'add':
                o, d = view.ray(e.position().x(), e.position().y())
                h = view.ground_hit(o, d)
                if h is None:
                    return
                ref = g.add(h + (0, 0, 0))
                if ref is None:
                    win.statusBar().showMessage('No nav list here.', 4000)
                    return
                text = 'Add nav point'
                if self.first is not None and e.modifiers() & QtCore.Qt.ShiftModifier:
                    a = navedit.ref_of(self.first)
                    if a is not None:
                        g.toggle_link(a, ref)
                        text = 'Add linked nav point'
                win.undo.push(NavCmd(win, text, before, g.state()))
                win.nav_changed(select=ref)
                self.first = win.view.selected
            elif self.mode == 'link':
                o = self._pick(view, e)
                if o is None:
                    return
                if self.first is None or navedit.ref_of(self.first) is None:
                    self.first = o
                    view.select(o)
                    self.hover(view, e)
                    return
                r = g.toggle_link(navedit.ref_of(self.first), navedit.ref_of(o))
                win.undo.push(NavCmd(win, 'Nav %s' % r, before, g.state()))
                win.nav_changed(select=navedit.ref_of(o))
                win.statusBar().showMessage('%s: %s - %s' % (r, self.first.label(), o.label()), 4000)
                self.first = win.view.selected if e.modifiers() & QtCore.Qt.ShiftModifier else None
            else:
                o = self._pick(view, e)
                if o is None:
                    return
                ref = navedit.ref_of(o)
                refs = g.references(ref, win.records)
                if refs:
                    QtWidgets.QMessageBox.information(
                        win, 'd6edit', '%s is used by:\n%s\n\nRemove those first (its id is referenced).' % (
                            o.label(), '\n'.join(refs)))
                    return
                g.delete(ref)
                win.undo.push(NavCmd(win, 'Delete nav point', before, g.state()))
                win.nav_changed()
        except ValueError as ex:
            g.restore(before)
            win.statusBar().showMessage(str(ex), 5000)

    def drag(self, view, e):
        pass

    def release(self, view, e):
        pass

    def wheel(self, view, d):
        pass


class NavPanel(QtWidgets.QWidget):
    def __init__(self, win, tool):
        super().__init__()
        self.win, self.tool = win, tool
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.enable = QtWidgets.QPushButton('Nav tool: off')
        self.enable.setCheckable(True)
        self.enable.toggled.connect(self._toggle)
        lay.addWidget(self.enable)
        row = QtWidgets.QHBoxLayout()
        self.btns = {}
        for m, n in NavTool.MODES:
            b = QtWidgets.QToolButton()
            b.setText(n)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, m=m: self._mode(m))
            row.addWidget(b)
            self.btns[m] = b
        self.btns['add'].setChecked(True)
        lay.addLayout(row)
        hint = QtWidgets.QLabel(
            'Monsters walk between linked nav points.\n\n'
            'Add: click the floor. Shift+click adds the next point\n'
            '  linked to the previous one (draw a path).\n'
            'Link: click two points; again to unlink. Points in\n'
            '  different BSPs or BSP <-> terrain are joined\n'
            '  through D6LINK (cyan lines).\n'
            'Delete: renumbers the BSP points and fixes links and\n'
            '  the leaf table; refused while the id is used.\n\n'
            'Green: links inside a graph. Max 6 links per point.')
        hint.setStyleSheet('color: #9ab')
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch(1)

    def _toggle(self, on):
        self.enable.setText('Nav tool: on' if on else 'Nav tool: off')
        if on:
            for k in ('nav', 'N'):
                self.win.view.show[k] = True
            self.win.view.rebuild_markers()
        self.tool.first = None
        self.win.set_tool(self.tool if on else None)

    def _mode(self, m):
        self.tool.mode = m
        self.tool.first = None
        if not self.enable.isChecked():
            self.enable.setChecked(True)
