"""
d6edit - textures: BSP level textures (.twd: replace, add, export, paint on
faces) and terrain types (new type / retexture with generated transitions).
"""
import os
import time

import numpy as np

from qtcompat import QtCore, QtGui, QtWidgets

import d6tilegen


def qpix(rgb, size=None):
    img = np.ascontiguousarray(np.asarray(rgb, np.uint8)[..., :3])
    qi = QtGui.QImage(img.data, img.shape[1], img.shape[0], img.shape[1] * 3, QtGui.QImage.Format_RGB888).copy()
    pm = QtGui.QPixmap.fromImage(qi)
    if size:
        pm = pm.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.FastTransformation)
    return pm


def load_rgb(path, size=None):
    from PIL import Image
    im = Image.open(path).convert('RGB')
    if size:
        im = im.resize((size, size), Image.LANCZOS)
    return np.array(im)


class FaceCmd(QtGui.QUndoCommand):
    def __init__(self, win, inst, before, after):
        super().__init__('Retexture face')
        self.win, self.inst, self.before, self.after = win, inst, before, after
        self.first = True

    def _apply(self, st):
        b = self.inst.bsp
        b.texinfo[:] = st[0]
        b.faces[:] = st[1]
        self.win.spoke.mark_dirty(('BSP', self.inst.slot))
        self.win.view.rebuild_bsp(self.inst)
        self.win._title()

    def redo(self):
        if self.first:
            self.first = False
            return
        self._apply(self.after)

    def undo(self):
        self._apply(self.before)


class FacePaintTool(object):
    def __init__(self, panel):
        self.panel = panel

    def hover(self, view, e):
        h = view.pick_bsp_face(e.position().x(), e.position().y())
        if h is None:
            view.overlay.hud = 'Click a BSP face to give it the selected texture (Esc: stop)'
        else:
            inst, fi, _ = h
            ti = inst.bsp.texinfo[inst.bsp.faces[fi].texinfo]
            view.overlay.hud = '%s face %d  texture %d   - click to apply texture %s' % (
                inst.name.upper(), fi, ti.flags & 0xfff if ti.flags >= 0 else -1, self.panel.cur_tex)
        view.overlay.update()

    def press(self, view, e):
        h = view.pick_bsp_face(e.position().x(), e.position().y())
        tex = self.panel.cur_tex
        if h is None or tex is None:
            return
        inst, fi, _ = h
        if inst.slot != self.panel.cur_slot:
            self.panel.win.statusBar().showMessage('That face belongs to %s; pick a texture of that level.' % inst.name.upper(), 5000)
            return
        b = inst.bsp
        before = (list(b.texinfo), list(b.faces))
        f = b.faces[fi]
        ti = b.texinfo[f.texinfo]
        if ti.flags < 0 or ti.flags & 0xa000:
            self.panel.win.statusBar().showMessage('Water / lava / invisible faces keep their texture.', 4000)
            return
        nflags = (ti.flags & ~0xfff) | tex
        if nflags == ti.flags:
            return
        want = ti._replace(flags=nflags)
        idx = next((i for i, t in enumerate(b.texinfo) if t == want), None)
        if idx is None:
            b.texinfo.append(want)
            idx = len(b.texinfo) - 1
        b.faces[fi] = f._replace(texinfo=idx)
        self.panel.win.undo.push(FaceCmd(self.panel.win, inst, before, (list(b.texinfo), list(b.faces))))
        self.panel.win.spoke.mark_dirty(('BSP', inst.slot))
        view.rebuild_bsp(inst)
        self.panel.win._title()

    def drag(self, view, e):
        pass

    def release(self, view, e):
        pass

    def wheel(self, view, d):
        pass


class TexturesDialog(QtWidgets.QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle('Textures')
        self.resize(1000, 700)
        self.cur_tex = None
        self.cur_slot = None
        self.tool = FacePaintTool(self)
        lay = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        lay.addWidget(self.tabs)
        self.tabs.addTab(self._level_tab(), 'Level textures (BSP)')
        self.tabs.addTab(self._terrain_tab(), 'Terrain types')
        self.msg = QtWidgets.QLabel()
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet('color: #e5c07b')
        lay.addWidget(self.msg)
        self.refresh()

    # ------------------------------------------------------------------ BSP
    def _level_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        left = QtWidgets.QVBoxLayout()
        self.bsp = QtWidgets.QComboBox()
        self.bsp.currentIndexChanged.connect(lambda _: self.fill_bsp())
        left.addWidget(self.bsp)
        self.texlist = QtWidgets.QListWidget()
        self.texlist.setViewMode(QtWidgets.QListView.IconMode)
        self.texlist.setIconSize(QtCore.QSize(64, 64))
        self.texlist.setGridSize(QtCore.QSize(78, 92))
        self.texlist.setResizeMode(QtWidgets.QListView.Adjust)
        self.texlist.setMovement(QtWidgets.QListView.Static)
        self.texlist.currentRowChanged.connect(self._tex_sel)
        left.addWidget(self.texlist, 1)
        lay.addLayout(left, 2)
        right = QtWidgets.QVBoxLayout()
        self.tex_preview = QtWidgets.QLabel()
        self.tex_preview.setFixedSize(256, 256)
        self.tex_preview.setStyleSheet('background: #303338')
        right.addWidget(self.tex_preview)
        self.tex_info = QtWidgets.QLabel()
        self.tex_info.setWordWrap(True)
        right.addWidget(self.tex_info)
        self.newpal = QtWidgets.QCheckBox('Own palette (better colours; uses a palette slot)')
        right.addWidget(self.newpal)
        for t, fn in (('Replace with image...', self.replace_tex), ('Add new texture from image...', self.add_tex),
                      ('Export PNG...', self.export_tex)):
            b = QtWidgets.QPushButton(t)
            b.clicked.connect(fn)
            right.addWidget(b)
        self.paint = QtWidgets.QPushButton('Paint on faces: off')
        self.paint.setCheckable(True)
        self.paint.toggled.connect(self._paint)
        right.addWidget(self.paint)
        hint = QtWidgets.QLabel('BSP textures are 128x128 (images are scaled). The engine merges textures whose '
                                '16x16 mip is identical. Painting on faces gives the clicked face this texture '
                                '(its texture mapping is kept); Ctrl+Z undoes.')
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #8a94a0')
        right.addWidget(hint)
        right.addStretch(1)
        lay.addLayout(right, 1)
        return w

    def refresh(self):
        sp = self.win.spoke
        self.bsp.blockSignals(True)
        self.bsp.clear()
        if sp is not None:
            for b in sp.bsps:
                if b.twd is not None:
                    self.bsp.addItem('%s (slot %d)' % (b.name.upper(), b.slot), b.slot)
        self.bsp.blockSignals(False)
        self.fill_bsp()
        self.fill_types()

    def inst(self):
        s = self.bsp.currentData()
        return self.win.spoke.bsp_by_slot(s) if s is not None else None

    def fill_bsp(self):
        self.texlist.clear()
        b = self.inst()
        self.cur_slot = b.slot if b else None
        if b is None:
            return
        for i in range(len(b.twd.textures)):
            w, h, rgb = b.twd.texture_rgb(i)
            img = np.frombuffer(rgb, np.uint8).reshape(h, w, 3)
            it = QtWidgets.QListWidgetItem(QtGui.QIcon(qpix(img)), str(i))
            it.setData(QtCore.Qt.UserRole, i)
            self.texlist.addItem(it)

    def _tex_sel(self, row):
        b = self.inst()
        if b is None or row < 0:
            return
        i = self.texlist.item(row).data(QtCore.Qt.UserRole)
        self.cur_tex = i
        w, h, rgb = b.twd.texture_rgb(i)
        self.tex_preview.setPixmap(qpix(np.frombuffer(rgb, np.uint8).reshape(h, w, 3), 256))
        t = b.twd.textures[i]
        used = sum(1 for f in b.bsp.faces if b.bsp.texinfo[f.texinfo].flags >= 0 and
                   (b.bsp.texinfo[f.texinfo].flags & 0xfff) == i and not b.bsp.texinfo[f.texinfo].flags & 0xa000)
        self.tex_info.setText('texture %d "%s", palette %d, %dx%d, used by %d faces' % (
            i, t.name_raw.split(b'\0')[0].decode('latin1'), t.palette, t.width, t.height, used))

    def _after_twd(self, b, i):
        w, h, rgb = b.twd.texture_rgb(i)
        self.win.spoke.texlib.replace(('bsp', b.name, i), np.frombuffer(rgb, np.uint8).reshape(h, w, 3))
        self.win.spoke.mark_dirty(('TWD', b.slot))
        self.win.view.rebuild_bsp(b)
        self.win._title()

    def replace_tex(self):
        b = self.inst()
        if b is None or self.cur_tex is None:
            return
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Image', '', 'Images (*.png *.jpg *.jpeg *.bmp *.tga)')
        if not p:
            return
        try:
            b.twd.replace_texture(self.cur_tex, load_rgb(p, 128), palette=None if self.newpal.isChecked() else 'keep')
        except Exception as e:
            self.msg.setText('Failed: %s' % e)
            return
        self._after_twd(b, self.cur_tex)
        row = self.texlist.currentRow()
        self.fill_bsp()
        self.texlist.setCurrentRow(row)
        self.msg.setText('Texture %d replaced (File > Save writes %s.twd)' % (self.cur_tex, b.name))

    def add_tex(self):
        b = self.inst()
        if b is None:
            return
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Image', '', 'Images (*.png *.jpg *.jpeg *.bmp *.tga)')
        if not p:
            return
        try:
            i = b.twd.add_texture(os.path.splitext(os.path.basename(p))[0][:60], load_rgb(p, 128),
                                  palette=None if self.newpal.isChecked() or not b.twd.palettes else 0)
        except Exception as e:
            self.msg.setText('Failed: %s' % e)
            return
        self._after_twd(b, i)
        self.fill_bsp()
        self.texlist.setCurrentRow(i)
        self.msg.setText('Added texture %d; use "Paint on faces" to put it on walls/floors.' % i)

    def export_tex(self):
        b = self.inst()
        if b is None or self.cur_tex is None:
            return
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Export', '%s_%d.png' % (b.name, self.cur_tex), 'PNG (*.png)')
        if p:
            from PIL import Image
            w, h, rgb = b.twd.texture_rgb(self.cur_tex)
            Image.fromarray(np.frombuffer(rgb, np.uint8).reshape(h, w, 3)).save(p)
            self.msg.setText('Wrote %s' % p)

    def _paint(self, on):
        self.paint.setText('Paint on faces: on' if on else 'Paint on faces: off')
        self.win.set_tool(self.tool if on else None)

    # ------------------------------------------------------------------ terrain
    def _terrain_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        self.types = QtWidgets.QListWidget()
        self.types.setViewMode(QtWidgets.QListView.IconMode)
        self.types.setIconSize(QtCore.QSize(64, 64))
        self.types.setGridSize(QtCore.QSize(78, 92))
        self.types.setResizeMode(QtWidgets.QListView.Adjust)
        self.types.setMovement(QtWidgets.QListView.Static)
        lay.addWidget(self.types, 2)
        right = QtWidgets.QFormLayout()
        self.letter = QtWidgets.QLineEdit()
        self.letter.setMaxLength(1)
        self.letter.setPlaceholderText('e.g. W')
        right.addRow('Type letter', self.letter)
        self.img_path = QtWidgets.QLineEdit()
        b = QtWidgets.QPushButton('Image...')
        b.clicked.connect(self._pick_img)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.img_path, 1)
        row.addWidget(b)
        ww = QtWidgets.QWidget()
        ww.setLayout(row)
        right.addRow('Texture', ww)
        self.partners = QtWidgets.QLineEdit()
        self.partners.setPlaceholderText('types it borders, e.g. G R 1')
        right.addRow('Transitions to', self.partners)
        self.repal = QtWidgets.QCheckBox('Rebuild the palette of tile folder 803 for the new colours\n'
                                         '(its 9 existing tiles are re-quantised slightly)')
        right.addRow(self.repal)
        row = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton('Preview')
        b.clicked.connect(lambda: self.gen(write=False))
        row.addWidget(b)
        b = QtWidgets.QPushButton('Create tiles')
        b.clicked.connect(lambda: self.gen(write=True))
        row.addWidget(b)
        ww = QtWidgets.QWidget()
        ww.setLayout(row)
        right.addRow(ww)
        self.gen_preview = QtWidgets.QLabel()
        self.gen_preview.setMinimumHeight(140)
        self.gen_preview.setWordWrap(True)
        right.addRow(self.gen_preview)
        hint = QtWidgets.QLabel('Writes tiles/80x/<name>.mip for the full tile and every corner mix with each '
                                'listed type (existing files are backed up). Then paint the type with the Terrain '
                                'brush; the game picks the tiles by name. Using an existing letter re-textures it.')
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #8a94a0')
        right.addRow(hint)
        rw = QtWidgets.QWidget()
        rw.setLayout(right)
        lay.addWidget(rw, 2)
        return w

    def fill_types(self):
        self.types.clear()
        g = self.win.game
        if g is None:
            return
        tiles = d6tilegen.existing_tiles(g.root)
        for c in sorted(set(ch for n in tiles for ch in n)):
            if not c.isalnum() or c * 4 not in tiles:
                continue
            img = d6tilegen.tile_rgb(g.root, c * 4, tiles)
            it = QtWidgets.QListWidgetItem(QtGui.QIcon(qpix(img)), '%s (%s)' % (c, tiles[c * 4][0]))
            self.types.addItem(it)

    def _pick_img(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Texture', '', 'Images (*.png *.jpg *.jpeg *.bmp *.tga)')
        if p:
            self.img_path.setText(p)

    def gen(self, write):
        g = self.win.game
        letter = self.letter.text().strip().upper()
        try:
            img = load_rgb(self.img_path.text())
        except Exception as e:
            self.msg.setText('Image: %s' % e)
            return
        partners = [p for p in self.partners.text().replace(',', ' ').upper().split() if p]
        if write:
            # back up tiles that will be overwritten
            tiles = d6tilegen.existing_tiles(g.root)
            bdir = os.path.join(g.root, 'd6edit_backup', time.strftime('%Y%m%d-%H%M%S'), 'tiles')
            import itertools
            import shutil
            names = {d6tilegen.canon(letter * 4)} | {d6tilegen.canon(''.join(c)) for p in partners
                                                     for c in itertools.product((letter, p), repeat=4)}
            for n in names:
                if n in tiles:
                    os.makedirs(os.path.join(bdir, tiles[n][0]), exist_ok=True)
                    shutil.copy2(tiles[n][1], os.path.join(bdir, tiles[n][0]))
        try:
            res = d6tilegen.generate(g.root, letter, img, partners, write=write, repalette=self.repal.isChecked())
        except Exception as e:
            self.msg.setText('Failed: %s' % e)
            return
        sheet = np.concatenate([r[3] for r in res[:12]], 1).astype(np.uint8)
        self.gen_preview.setPixmap(qpix(sheet).scaledToHeight(96))
        if write:
            if hasattr(g, 'tileset'):
                del g.tileset
            self.fill_types()
            self.win.tiles_changed()
            self.msg.setText('Wrote %d tiles to tiles/%s (%s). Paint type %s with the Terrain brush.' % (
                len(res), res[0][1], ', '.join(r[0] for r in res[:8]) + (' ...' if len(res) > 8 else ''), letter))
        else:
            self.msg.setText('Preview of %d tiles (folder %s); nothing written.' % (len(res), res[0][1]))
