#!/bin/sh
# d6edit on Linux / macOS. Uses the system Python packages when present
# (Debian/Ubuntu: apt install python3-pyqt6 python3-opengl python3-numpy python3-pil),
# else a virtual environment in .venv with the pip packages of requirements.txt.
HERE=$(cd "$(dirname "$0")" && pwd)
if python3 -c "import numpy, PIL, OpenGL; import PySide6" 2>/dev/null || \
   python3 -c "import numpy, PIL, OpenGL; import PyQt6" 2>/dev/null; then
    exec python3 "$HERE/editor/d6edit.py" "$@"
fi
if [ ! -x "$HERE/.venv/bin/python" ]; then
    python3 -m venv "$HERE/.venv" && "$HERE/.venv/bin/pip" install -r "$HERE/requirements.txt" || exit 1
fi
exec "$HERE/.venv/bin/python" "$HERE/editor/d6edit.py" "$@"
