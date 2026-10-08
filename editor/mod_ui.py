"""
d6edit - mod projects (formats/d6mod.py).

The editor works on a game folder; for a mod that folder is a working copy of
a pristine game. The Mod dialog shows what the working copy changes against
the project's base, stores it in the project (Capture), packs it as a zip,
and installs / uninstalls mods in other game folders.
"""
import os
import sys

from qtcompat import QtCore, QtWidgets

HERE = os.path.dirname(os.path.abspath(__file__))
FORMATS = os.path.join(os.path.dirname(HERE), 'formats')
if FORMATS not in sys.path:
    sys.path.insert(0, FORMATS)
import d6mod  # noqa: E402


class ModDialog(QtWidgets.QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle('Mod project')
        self.resize(820, 620)
        lay = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        for text, slot in (('New project...', self.new_project), ('Open project...', self.open_project),
                           ('Make working copy...', self.make_workcopy)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.info)
        form = QtWidgets.QFormLayout()
        self.ed_name = QtWidgets.QLineEdit()
        self.ed_version = QtWidgets.QLineEdit()
        self.ed_author = QtWidgets.QLineEdit()
        self.ed_desc = QtWidgets.QLineEdit()
        for lbl, w in (('Name', self.ed_name), ('Version', self.ed_version), ('Author', self.ed_author),
                       ('Description', self.ed_desc)):
            form.addRow(lbl, w)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel('Changes of the open game folder against the project base:'))
        self.list = QtWidgets.QListWidget()
        lay.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        for text, slot in (('Refresh', self.refresh), ('Capture into project', self.capture),
                           ('Pack zip...', self.pack), ('Install into a game...', self.install),
                           ('Uninstall from a game...', self.uninstall)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.msg = QtWidgets.QLabel()
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet('color: #e5c07b')
        lay.addWidget(self.msg)
        self.project = None
        p = self.win.settings.value('modproject', '')
        if p and os.path.exists(os.path.join(p, 'mod.json')):
            self._load(p)
        self.refresh()

    # ----------------------------------------------------------- helpers
    def s(self, key, default=''):
        return self.win.settings.value(key, default)

    def _load(self, path):
        try:
            self.project = d6mod.Project(path)
        except (d6mod.ModError, OSError, ValueError) as e:
            self.msg.setText('Cannot open %s: %s' % (path, e))
            return
        self.win.settings.setValue('modproject', path)
        m = self.project.meta
        self.ed_name.setText(m.get('name', ''))
        self.ed_version.setText(m.get('version', ''))
        self.ed_author.setText(m.get('author', ''))
        self.ed_desc.setText(m.get('description', ''))

    def _store_meta(self):
        if self.project is None:
            return
        m = self.project.meta
        m['version'] = self.ed_version.text().strip() or m.get('version', '0.1')
        m['author'] = self.ed_author.text().strip()
        m['description'] = self.ed_desc.text().strip()
        name = self.ed_name.text().strip()
        if name and name != m['name']:
            m['name'] = name
        self.project.save()

    def _gamedir(self):
        return self.win.game.root if self.win.game is not None else None

    def _ask_dir(self, title, key):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, title, self.s(key, os.path.expanduser('~')))
        if d:
            self.win.settings.setValue(key, d)
        return d

    # ----------------------------------------------------------- actions
    def refresh(self):
        self.list.clear()
        g = self._gamedir()
        if self.project is None:
            self.info.setText('No mod project open. A project records a pristine game (its base) '
                              'and stores what your working copy changes.')
            return
        m = self.project.meta
        self.info.setText('Project: %s\nBase: %d files. Captured: %d entries (%s), for the %s game.\n'
                          'Working copy: %s' % (
                              self.project.root, len(self.project.base), len(m.get('files', {})),
                              m.get('captured', 'never'), 'EX' if m.get('target') == 'ex' else 'Standard',
                              g or '(no game folder open)'))
        if g is None:
            return
        if self.win.unsaved():
            self.msg.setText('The editor has unsaved changes: File > Save before Capture.')
        try:
            ch = self.project.changes(g)
        except OSError as e:
            self.msg.setText(str(e))
            return
        for rel, what in ch:
            self.list.addItem('%-8s %s' % (what, rel))
        if not ch:
            self.list.addItem('(no changes)')

    def new_project(self):
        d = self._ask_dir('Folder for the new mod project (empty)', 'modparent')
        if not d:
            return
        pr = self._ask_dir('Pristine game folder (unmodified install: the base)', 'pristine')
        if not pr:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, 'Mod project', 'Mod name:',
                                                  QtWidgets.QLineEdit.Normal, os.path.basename(d))
        if not ok or not name.strip():
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            d6mod.Project.create(d, pr, name.strip(), log=self.msg.setText)
        except (d6mod.ModError, OSError) as e:
            self.msg.setText('Error: %s' % e)
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._load(d)
        self.refresh()
        self.msg.setText('Created %s. Edit a working copy of the game (Make working copy), then Capture.' % d)

    def open_project(self):
        d = self._ask_dir('Mod project folder (with mod.json)', 'modproject')
        if d:
            self._load(d)
            self.refresh()

    def make_workcopy(self):
        pr = self._ask_dir('Pristine game folder to copy', 'pristine')
        if not pr:
            return
        dst = self._ask_dir('New, empty folder for the working copy', 'workparent')
        if not dst:
            return
        link = QtWidgets.QMessageBox.question(
            self, 'Working copy', 'Use hard links for files the game does not write (fast, little disk space)?\n'
            'The SDK tools replace files instead of writing into them, so the pristine copy stays intact.',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            d6mod.workcopy(pr, dst, link=link, log=self.msg.setText)
        except (d6mod.ModError, OSError) as e:
            self.msg.setText('Error: %s' % e)
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.win.settings.setValue('pristine', pr)
        self.win.open_game(dst)
        self.refresh()

    def capture(self):
        if self.project is None or self._gamedir() is None:
            self.msg.setText('Open a project and a working copy first.')
            return
        if self.win.unsaved():
            self.msg.setText('Save the editor changes first (File > Save).')
            return
        self._store_meta()
        self.project.meta['target'] = getattr(self.win, 'mode', 'standard')
        self.project.save()
        pr = self.s('pristine', '')
        pr = pr if pr and os.path.isdir(pr) else None
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.project.capture(self._gamedir(), pr, log=self.msg.setText)
        except (d6mod.ModError, OSError) as e:
            self.msg.setText('Error: %s' % e)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if pr is None:
            self.msg.setText(self.msg.text() + ' (no pristine folder known: changed files stored whole)')
        self.refresh()

    def pack(self):
        if self.project is None:
            return
        self._store_meta()
        out, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Pack mod', os.path.join(self.s('modparent', ''), self.project.name + '.zip'), 'Zip (*.zip)')
        if out:
            try:
                d6mod.pack(self.project, out, log=self.msg.setText)
            except (d6mod.ModError, OSError) as e:
                self.msg.setText('Error: %s' % e)

    def install(self):
        src = self.project
        if src is None:
            zp, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Mod zip', '', 'Zip (*.zip)')
            if not zp:
                return
            import tempfile
            src = d6mod.Project(d6mod.unpack(zp, tempfile.mkdtemp()))
        g = self._ask_dir('Game folder to install %s into' % src.name, 'installdir')
        if not g:
            return
        if self._gamedir() and os.path.samefile(g, self._gamedir()):
            self.msg.setText('That is the working copy: install into another game folder.')
            return
        try:
            d6mod.install(src, g, log=self.msg.setText)
        except d6mod.ModError as e:
            r = QtWidgets.QMessageBox.question(self, 'Install', '%s\n\nInstall anyway (replace those files)?' % e,
                                               QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if r == QtWidgets.QMessageBox.Yes:
                try:
                    d6mod.install(src, g, force=True, log=self.msg.setText)
                except (d6mod.ModError, OSError) as e2:
                    self.msg.setText('Error: %s' % e2)
        except OSError as e:
            self.msg.setText('Error: %s' % e)

    def uninstall(self):
        g = self._ask_dir('Game folder to uninstall a mod from', 'installdir')
        if not g:
            return
        mods = [m['name'] for m in d6mod.installed(g)]
        if not mods:
            self.msg.setText('No mods installed in %s.' % g)
            return
        name, ok = QtWidgets.QInputDialog.getItem(self, 'Uninstall', 'Mod (last installed first):',
                                                  mods[::-1], 0, False)
        if not ok:
            return
        try:
            d6mod.uninstall(name, g, log=self.msg.setText)
        except (d6mod.ModError, OSError) as e:
            self.msg.setText('Error: %s' % e)
