"""
d6edit - Qt binding selection.

Uses PySide6 when installed (pip), else PyQt6 (Ubuntu/Debian: apt install
python3-pyqt6). Set D6EDIT_QT=pyqt6 or pyside6 to force one.

PyQt6 only accepts fully scoped enum names (Qt.Key.Key_T); the editor uses the
short names PySide6 also accepts (Qt.Key_T), so for PyQt6 the enum members
are copied onto their parent classes.
"""
import enum
import os

_want = os.environ.get('D6EDIT_QT', '').lower()
API = None

if _want != 'pyqt6':
    try:
        from PySide6 import QtCore, QtGui, QtWidgets                 # noqa: F401
        from PySide6.QtOpenGLWidgets import QOpenGLWidget            # noqa: F401
        Signal = QtCore.Signal
        API = 'pyside6'
    except ImportError:
        if _want == 'pyside6':
            raise

if API is None:
    from PyQt6 import QtCore, QtGui, QtWidgets                       # noqa: F401
    from PyQt6.QtOpenGLWidgets import QOpenGLWidget                  # noqa: F401
    Signal = QtCore.pyqtSignal
    API = 'pyqt6'

    def _alias(cls, seen):
        if id(cls) in seen:
            return
        seen.add(id(cls))
        for name in list(vars(cls)):
            v = getattr(cls, name, None)
            if isinstance(v, type) and issubclass(v, enum.Enum):
                for mname, m in v.__members__.items():     # (iterating a Flag skips composite values)
                    if not hasattr(cls, mname):
                        try:
                            setattr(cls, mname, m)
                        except (AttributeError, TypeError):
                            pass
            elif isinstance(v, type) and v.__module__.startswith('PyQt6') and name[0] != '_':
                _alias(v, seen)

    _seen = set()
    for _mod in (QtCore, QtGui, QtWidgets):
        for _n in dir(_mod):
            _c = getattr(_mod, _n)
            if isinstance(_c, type):
                _alias(_c, _seen)
