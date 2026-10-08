"""
d6edit - model tools: browse the model slots of the game (the name tables in
deep6.exe), preview, export to OBJ / glTF, import OBJ / glTF into a slot.
"""
import os
import time

import numpy as np

from qtcompat import QtCore, QtGui, QtWidgets

import d6data
import d6model
import d6mdlio

KINDS = [('M', 'Monsters'), ('I', 'Items'), ('P', 'Props')]
FIELD = {'M': ('D6MONS.DAT', 'gfx'), 'I': ('D6ITEM.DAT', 'model'), 'P': ('D6PROP.DAT', 'model')}


class ModelTools(QtWidgets.QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.root = win.game.root
        self.setWindowTitle('Models')
        self.resize(1100, 720)
        lay = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()
        self.kind = QtWidgets.QComboBox()
        for k, n in KINDS:
            self.kind.addItem(n, k)
        self.kind.currentIndexChanged.connect(lambda _: self.fill())
        left.addWidget(self.kind)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('search')
        self.search.textChanged.connect(self._filter)
        left.addWidget(self.search)
        self.list = QtWidgets.QListWidget()
        self.list.currentRowChanged.connect(self._sel)
        left.addWidget(self.list, 1)
        lw = QtWidgets.QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(360)
        lay.addWidget(lw)
        right = QtWidgets.QVBoxLayout()
        self.preview = QtWidgets.QLabel()
        self.preview.setMinimumSize(420, 420)
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setStyleSheet('background: #303338')
        right.addWidget(self.preview, 1)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel('Frame'))
        self.frame = QtWidgets.QSpinBox()
        self.frame.setRange(0, 65535)
        self.frame.valueChanged.connect(lambda _: self._render())
        row.addWidget(self.frame)
        row.addWidget(QtWidgets.QLabel('Turn'))
        self.yaw = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.yaw.setRange(0, 359)
        self.yaw.setValue(35)
        self.yaw.valueChanged.connect(lambda _: self._render())
        row.addWidget(self.yaw, 1)
        right.addLayout(row)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        self.info.setStyleSheet('color: #9ab')
        right.addWidget(self.info)
        btns = QtWidgets.QGridLayout()
        for i, (t, fn) in enumerate((('Export OBJ...', self.export_obj), ('Export glTF (.glb)...', self.export_glb),
                                     ('Import into this slot...', self.import_slot),
                                     ('Point slot to another file...', self.rename_slot))):
            b = QtWidgets.QPushButton(t)
            b.clicked.connect(fn)
            btns.addWidget(b, i // 2, i % 2)
        right.addLayout(btns)
        self.attach = QtWidgets.QCheckBox('Keep the attachment points (weapon hands, helm) of the current model on import')
        self.attach.setChecked(True)
        right.addWidget(self.attach)
        hint = QtWidgets.QLabel(
            'Import accepts .obj (static) and .glb/.gltf (static, or animated with morph-target frames and '
            'anim_<id> animations as written by Export glTF). Units: metres in the file, 1 mm in the game. '
            'Records point at slots (Game databases > model); "Point slot" edits the name table in deep6.exe '
            '(backed up).')
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #8a94a0')
        right.addWidget(hint)
        self.msg = QtWidgets.QLabel()
        self.msg.setStyleSheet('color: #e5c07b')
        self.msg.setWordWrap(True)
        right.addWidget(self.msg)
        rw = QtWidgets.QWidget()
        rw.setLayout(right)
        lay.addWidget(rw, 1)
        self.model = None
        self.fill()

    # ------------------------------------------------------------------ list
    def k(self):
        return self.kind.currentData()

    def fill(self):
        k = self.k()
        names = d6model.model_table(self.root, k)
        fname, field = FIELD[k]
        tab = self.win.game.table(fname)
        users = {}
        for i, r in enumerate(tab.records, 1):
            users.setdefault(r.get(field), []).append(i)
        self.users = users
        self.list.blockSignals(True)
        self.list.clear()
        for i, n in enumerate(names):
            u = users.get(i, [])
            it = QtWidgets.QListWidgetItem('%3d  %s  (%d)' % (i, n or '-', len(u)))
            it.setData(QtCore.Qt.UserRole, i)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self._filter(self.search.text())
        self.list.setCurrentRow(0)

    def _filter(self, t):
        t = t.lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def slot(self):
        it = self.list.currentItem()
        return it.data(QtCore.Qt.UserRole) if it else None

    def _sel(self, row):
        s = self.slot()
        if s is None:
            return
        path = d6mdlio.slot_path(self.root, self.k(), s)
        self.path = path
        try:
            self.model = d6model.load_model(self.root, path)
        except Exception as e:
            self.model = None
            self.preview.setPixmap(QtGui.QPixmap())
            self.info.setText('%s: %s' % (path, e))
            return
        m = self.model
        fname, field = FIELD[self.k()]
        tab = self.win.game.table(fname)
        u = self.users.get(s, [])
        who = ', '.join('%d %s' % (i, tab.records[i - 1].get('name') if hasattr(tab.records[i - 1], 'get') else '')
                        for i in u[:8]) + (' ...' if len(u) > 8 else '')
        nanim = sum(1 for a in m.anims if a != (0, 0, 0))
        self.info.setText('%s  -  version %d, %d frames (%d stored), %d animations, %d parts, %d textures, '
                          '%d attachment slots, height %d\nUsed by: %s' % (
                              path, m.version, m.nframes, len(m.stored_frames()), nanim, len(m.parts),
                              len(m.textures), len(m.attachments), m.height(), who or 'no record'))
        self.frame.blockSignals(True)
        self.frame.setMaximum(max(0, m.nframes - 1))
        self.frame.setValue(m.default_frame)
        self.frame.blockSignals(False)
        self._render()

    def _render(self):
        m = self.model
        if m is None:
            return
        f = self.frame.value()
        if f not in m.stored_frames():
            f = m.default_frame
        try:
            img = d6model.render(m, size=420, frame=f, yaw=float(self.yaw.value()), bg=(48, 51, 56))
        except Exception as e:
            self.msg.setText('render: %s' % e)
            return
        img = np.ascontiguousarray(img[..., :3], np.uint8)
        qi = QtGui.QImage(img.data, img.shape[1], img.shape[0], img.shape[1] * 3, QtGui.QImage.Format_RGB888).copy()
        self.preview.setPixmap(QtGui.QPixmap.fromImage(qi))

    # ------------------------------------------------------------------ actions
    def export_obj(self):
        if self.model is None:
            return
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Export OBJ', os.path.splitext(os.path.basename(self.path))[0] + '.obj',
                                                     'Wavefront OBJ (*.obj)')
        if p:
            d6mdlio.export_obj(self.model, p, frame=self.frame.value() if self.frame.value() in self.model.stored_frames() else None)
            self.msg.setText('Wrote %s (+ .mtl and PNG textures)' % p)

    def export_glb(self):
        if self.model is None:
            return
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Export glTF', os.path.splitext(os.path.basename(self.path))[0] + '.glb',
                                                     'glTF binary (*.glb)')
        if p:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                d6mdlio.export_glb(self.model, p)
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            self.msg.setText('Wrote %s (frames as morph targets, animations anim_<id>)' % p)

    def import_slot(self, src=None):
        s = self.slot()
        if s is None:
            return
        if src is None:
            src, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Import model', '', 'Models (*.obj *.glb *.gltf)')
            if not src:
                return
        try:
            b = d6mdlio.import_file(src)
            if self.attach.isChecked() and self.model is not None and self.model.attachments:
                d6mdlio.copy_attachments(b, self.model)
            data = b.build()
            d6model.Model(data, 'check')          # must decode
        except Exception as e:
            self.msg.setText('Import failed: %s' % e)
            return
        full = d6data.find_file(self.root, 'models/' + self.path) or os.path.join(self.root, 'models', self.path)
        bdir = os.path.join(self.root, 'd6edit_backup', time.strftime('%Y%m%d-%H%M%S'), 'models', os.path.dirname(self.path))
        if os.path.exists(full):
            os.makedirs(bdir, exist_ok=True)
            import shutil
            shutil.copy2(full, os.path.join(bdir, os.path.basename(full)))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(full + '.tmp', full)
        self.win.models_changed()
        self._sel(self.list.currentRow())
        self.msg.setText('Imported %s into %s (%d frames, %d triangles, %d textures; old file backed up).%s' % (
            os.path.basename(src), self.path, len(b.frames), len(b.tris), len(b.textures),
            ('\n' + '\n'.join(b.warnings)) if b.warnings else ''))

    def rename_slot(self):
        s = self.slot()
        if s is None:
            return
        names = d6model.model_table(self.root, self.k())
        sub = 'pc' if self.k() == 'M' and s < d6model.PC_GFX_LIMIT else d6mdlio.SLOT_KINDS[self.k()][0]
        t, ok = QtWidgets.QInputDialog.getText(
            self, 'Model slot %d' % s, 'File name in models/%s/ (the game loads it for every record using '
            'this slot; deep6.exe is patched, a backup is kept):' % sub, QtWidgets.QLineEdit.Normal, names[s])
        if not ok or not t.strip():
            return
        t = t.strip()
        try:
            d6mdlio.set_slot_name(self.root, self.k(), s, t,
                                  backup_dir=os.path.join(self.root, 'd6edit_backup', time.strftime('%Y%m%d-%H%M%S')))
        except Exception as e:
            self.msg.setText('Failed: %s' % e)
            return
        if not d6data.find_file(self.root, 'models/%s/%s' % (sub, t)):
            self.msg.setText('Slot %d now loads models/%s/%s, which does not exist yet: use "Import into this slot".' % (s, sub, t))
        self.win.models_changed()
        row = self.list.currentRow()
        self.fill()
        self.list.setCurrentRow(row)
