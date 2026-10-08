"""
d6edit - level geometry: edit the BSP levels in TrenchBroom.

  Set up       copies the Deep6 game configuration into TrenchBroom and
               exports every level texture as PNG into the project folder
  Decompile    level -> <project>/maps/<level>.map (formats/d6bspdc.py)
  Edit         opens the map in TrenchBroom
  Compile      <map> -> level files (formats/d6bspc.py) and installs them
               into the game folder (old files go to d6edit_backup)
  Place        adds a new level to a terrain spoke (SPOKEnn.TOL 'B' record)

Compiling and decompiling run as separate processes; their output appears
in the log.
"""
import os
import shlex

import sysutil
import sys

from qtcompat import QtCore, QtWidgets

HERE = os.path.dirname(os.path.abspath(__file__))
FORMATS = os.path.join(os.path.dirname(HERE), 'formats')
if FORMATS not in sys.path:
    sys.path.insert(0, FORMATS)
import d6level  # noqa: E402

TERRAIN_SPOKES = (0, 3, 11)


class GeometryDialog(QtWidgets.QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle('Level geometry (TrenchBroom)')
        self.resize(900, 620)
        st = win.settings
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.project = QtWidgets.QLineEdit(st.value('tb_project', os.path.expanduser('~/Deep6Maps')))
        b = QtWidgets.QPushButton('...')
        b.clicked.connect(self._pick_project)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.project, 1)
        row.addWidget(b)
        form.addRow('Project folder', row)
        self.tbcmd = QtWidgets.QLineEdit(st.value('tb_command', sysutil.default_trenchbroom_command()))
        form.addRow('TrenchBroom command', self.tbcmd)
        lay.addLayout(form)

        body = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel('Levels of this spoke'))
        self.list = QtWidgets.QListWidget()
        left.addWidget(self.list, 1)
        body.addLayout(left, 1)
        btns = QtWidgets.QVBoxLayout()
        for text, slot, tip in (
                ('Set up TrenchBroom project', self.setup, 'Game configuration + all textures as PNG'),
                ('Decompile to .map', self.decompile, 'Write <project>/maps/<level>.map'),
                ('Open in TrenchBroom', self.open_tb, 'Run the TrenchBroom command on the map'),
                ('Compile and install', self.compile, 'Compile the map and put the level into the game'),
                ('Place new level in this spoke...', self.place, 'Terrain spokes: add a level from a map')):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            btns.addWidget(b)
        self.fastvis = QtWidgets.QCheckBox('Fast vis')
        self.newnav = QtWidgets.QCheckBox('New nav graph (otherwise keep the level\'s)')
        btns.addWidget(self.fastvis)
        btns.addWidget(self.newnav)
        btns.addStretch(1)
        body.addLayout(btns)
        lay.addLayout(body, 1)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        lay.addWidget(self.log, 1)
        hint = QtWidgets.QLabel(
            'Set up TrenchBroom project also sets TrenchBroom\'s game path (close TrenchBroom first). '
            'In TrenchBroom, Ctrl+A then Ctrl+U brings the level into view; the outer hull is a hidden layer. '
            'Map units are BSP units (z up); texture scale 0.5 matches the shipped levels. '
            'Brush flags: water / lava / clip contents, nodraw / terrain surface flags.')
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #8a94a0')
        lay.addWidget(hint)
        self.proc = None
        self.after = None
        self.refresh()

    # ------------------------------------------------------------------ helpers
    def refresh(self):
        self.list.clear()
        sp = self.win.spoke
        if sp is None:
            return
        for inst in sp.bsps:
            it = QtWidgets.QListWidgetItem('%-12s slot %d' % (inst.name, inst.slot))
            it.setData(QtCore.Qt.UserRole, inst.name)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _pick_project(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, 'TrenchBroom project folder', self.project.text())
        if d:
            self.project.setText(d)

    def _save_settings(self):
        self.win.settings.setValue('tb_project', self.project.text())
        self.win.settings.setValue('tb_command', self.tbcmd.text())

    def level(self):
        it = self.list.currentItem()
        return it.data(QtCore.Qt.UserRole) if it else None

    def map_path(self, name):
        return os.path.join(self.project.text(), 'maps', name.lower() + '.map')

    def say(self, t):
        self.log.appendPlainText(t)

    def run(self, args, after=None):
        if self.proc is not None:
            self.say('busy: wait for the running job')
            return
        self._save_settings()
        self.say('$ ' + ' '.join(shlex.quote(a) for a in args))
        p = QtCore.QProcess(self)
        p.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        p.readyReadStandardOutput.connect(
            lambda: self.say(bytes(p.readAllStandardOutput()).decode('utf-8', 'replace').rstrip()))
        p.finished.connect(lambda code, status: self._done(code))
        self.proc = p
        self.after = after
        p.start(sys.executable, args)

    def _done(self, code):
        self.proc = None
        self.say('-- finished (exit %d)' % code)
        if code == 0 and self.after:
            f, self.after = self.after, None
            f()

    # ------------------------------------------------------------------ actions
    def setup(self):
        proj = self.project.text()
        os.makedirs(proj, exist_ok=True)
        self.run([os.path.join(FORMATS, 'd6bspc.py'), 'setup-trenchbroom', proj, self.win.game.root])

    def decompile(self):
        name = self.level()
        if not name:
            return
        mp = self.map_path(name)
        if os.path.exists(mp) and QtWidgets.QMessageBox.question(
                self, 'Decompile', '%s exists. Overwrite it?' % mp) != QtWidgets.QMessageBox.Yes:
            return
        os.makedirs(os.path.dirname(mp), exist_ok=True)
        self.run([os.path.join(FORMATS, 'd6bspdc.py'), self.win.game.root, name, mp,
                  '--textures', os.path.join(self.project.text(), 'textures')])

    def open_tb(self):
        name = self.level()
        if not name:
            return
        mp = self.map_path(name)
        if not os.path.exists(mp):
            self.say('no map yet: decompile first (%s)' % mp)
            return
        self._save_settings()
        argv = [a.replace('{map}', mp) for a in sysutil.split_command(self.tbcmd.text())]
        import shutil
        if argv and not shutil.which(argv[0]) and not os.path.isfile(argv[0]) and not sysutil.WINDOWS:
            found = sysutil.find_trenchbroom()          # e.g. the saved default 'trenchbroom' is not installed
            if found:
                self.tbcmd.setText(sysutil.default_trenchbroom_command())
                self._save_settings()
                self.say('using %s' % found)
                argv[0] = found
        if argv and argv[0].endswith('.AppImage') and os.path.isfile(argv[0]) and not os.access(argv[0], os.X_OK):
            try:
                os.chmod(argv[0], os.stat(argv[0]).st_mode | 0o111)   # downloads arrive without +x
                self.say('made %s executable' % argv[0])
            except OSError as e:
                self.say('%s is not executable: %s' % (argv[0], e))
        if not QtCore.QProcess.startDetached(argv[0], argv[1:]):
            self.say('cannot start %s (set the TrenchBroom command)' % argv[0])

    def _compile_args(self, mp, name):
        out = os.path.join(self.project.text(), 'build')
        a = [os.path.join(FORMATS, 'd6bspc.py'), 'compile', mp, '--name', name, '--out', out,
             '--game', self.win.game.root, '--textures', os.path.join(self.project.text(), 'textures'),
             '--install']
        if self.fastvis.isChecked():
            a.append('--fastvis')
        if self.newnav.isChecked():
            a.append('--newnav')
        return a

    def compile(self):
        name = self.level()
        if not name:
            return
        mp = self.map_path(name)
        if not os.path.exists(mp):
            mp, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Map to compile into %s' % name,
                                                          self.project.text(), 'Maps (*.map)')
            if not mp:
                return
        if not self.win.maybe_save():
            return
        self.run(self._compile_args(mp, name), after=self._reload)

    def _reload(self):
        if self.win.spoke is not None:
            self.win.spoke.dirty.clear()
            self.win.load_spoke(self.win.spoke.number)
        self.refresh()

    def place(self):
        sp = self.win.spoke
        if sp is None or sp.number not in TERRAIN_SPOKES:
            QtWidgets.QMessageBox.information(
                self, 'Place level', 'New levels can be placed in the terrain spokes (0, 3, 11). '
                'The dungeon spokes have a fixed level list in the game; compile over one of '
                'their levels instead.')
            return
        mp, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Map of the new level', self.project.text(),
                                                      'Maps (*.map)')
        if not mp:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, 'Place level', 'Level name (max 15 characters, new files):',
                                                  QtWidgets.QLineEdit.Normal,
                                                  os.path.splitext(os.path.basename(mp))[0][:15])
        if not ok or not name.strip():
            return
        name = name.strip().lower()
        if d6level.find_file(self.win.game.root, name + '.bsp') and QtWidgets.QMessageBox.question(
                self, 'Place level', '%s.bsp exists in the game. Replace it?' % name) != QtWidgets.QMessageBox.Yes:
            return
        try:
            v = self.win.view
            pt = v.eye + v.forward() * 6000.0          # a point in front of the camera
            tx, tz = max(0, int(pt[0] // 1024)), max(0, int(pt[2] // 1024))
        except Exception:
            tx, tz = 0, 0
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle('Placement')
        f = QtWidgets.QFormLayout(dlg)
        sx = QtWidgets.QSpinBox()
        sx.setRange(0, 1023)
        sx.setValue(int(tx))
        sz = QtWidgets.QSpinBox()
        sz.setRange(0, 1023)
        sz.setValue(int(tz))
        sy = QtWidgets.QSpinBox()
        sy.setRange(-127, 127)
        f.addRow('Tile x of the map origin', sx)
        f.addRow('Tile z of the map origin', sz)
        f.addRow('Height (steps of 64 map units)', sy)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if not dlg.exec():
            return
        if not self.win.maybe_save():
            return
        place = (sx.value(), sz.value(), sy.value())

        def placed():
            try:
                game = self.win.game.root
                b = d6level.BSPFile.load(d6level.find_file(game, name + '.bsp'))
                m = b.models[0]
                tp = d6level.find_file(game, 'SPOKE%02d.TOL' % sp.number)
                tol = d6level.ObjectList.load(tp)
                slot = tol.add_bsp_placement(name, place[:2], place[2], (m.minx, m.minz, m.maxx, m.maxz))
                import shutil
                import time
                bdir = os.path.join(game, 'd6edit_backup', time.strftime('%Y%m%d-%H%M%S') + '-tol')
                os.makedirs(bdir, exist_ok=True)
                shutil.copy2(tp, bdir)
                data = tol.to_bytes()
                with open(tp + '.tmp', 'wb') as fh:
                    fh.write(data)
                os.replace(tp + '.tmp', tp)
                self.say('placed %s in slot %d of SPOKE%02d.TOL' % (name, slot, sp.number))
            except Exception as e:
                self.say('placing failed: %s' % e)
            self._reload()
        self.run(self._compile_args(mp, name), after=placed)
