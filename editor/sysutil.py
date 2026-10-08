"""
d6edit - small platform helpers (Linux, Windows, macOS).
"""
import os
import shlex
import sys

WINDOWS = os.name == 'nt'


def split_command(cmd):
    """Split a command line like the platform's shell: on Windows backslashes
    in paths are kept and double quotes group words."""
    if not WINDOWS:
        return shlex.split(cmd)
    out = []
    for tok in shlex.split(cmd, posix=False):
        if len(tok) >= 2 and tok[0] == tok[-1] == '"':
            tok = tok[1:-1]
        out.append(tok)
    return out


def default_game_command():
    """The game on this platform: the Linux port, or the original deep6.exe."""
    if WINDOWS:
        return '{game}' + os.sep + 'deep6.exe'
    return 'opendwwandw --game-dir {game}'


def default_play_command():
    if WINDOWS:
        return ''        # needs OpenDWWandWExpanded (Linux only for now)
    return 'opendwwandwexpanded --game-dir {game} --load-slot {slot} --start-at {start}'


def default_trenchbroom_command():
    if WINDOWS:
        return 'TrenchBroom.exe {map}'
    if sys.platform == 'darwin':
        return 'open -a TrenchBroom {map}'
    exe = find_trenchbroom()
    return '%s {map}' % (shlex.quote(exe) if exe else 'trenchbroom')


def find_trenchbroom():
    """Linux: TrenchBroom on the PATH, else the release AppImage in the usual
    download folders (the official Linux build is TrenchBroom.AppImage)."""
    import glob
    import shutil
    for n in ('trenchbroom', 'TrenchBroom', 'TrenchBroom.AppImage'):
        p = shutil.which(n)
        if p:
            return p
    home = os.path.expanduser('~')
    for d in ('Applications', 'Downloads', 'bin', '.local/bin', 'TrenchBroom', 'Desktop', ''):
        hits = sorted(glob.glob(os.path.join(home, d, '**', 'TrenchBroom*.AppImage'), recursive=True)
                      if d in ('Downloads', 'TrenchBroom') else
                      glob.glob(os.path.join(home, d, 'TrenchBroom*.AppImage')))
        for h in reversed(hits):
            if os.access(h, os.X_OK) or os.path.isfile(h):
                return h
    for h in sorted(glob.glob('/opt/*/TrenchBroom*.AppImage') + glob.glob('/opt/TrenchBroom*.AppImage')):
        return h
    return None
