"""
d6edit - target game: Standard or EX.

  Standard  the original game: deep6.exe (Windows) or the faithful Linux
            port OpenDWWandW. Only what the shipped engine can load.
  EX        OpenDWWandWExpanded: everything in Standard plus features the
            expanded engine adds (play from the camera, ...).

Features that need EX are listed in EX_FEATURES; the editor disables their
actions in Standard mode. A mod project records the mode it was captured in
(mod.json "target"), so players know which game it needs.
"""
import sysutil

STANDARD = 'standard'
EX = 'ex'
NAMES = {STANDARD: 'Standard', EX: 'EX'}

# feature -> what it needs from OpenDWWandWExpanded (shown as a tooltip)
EX_FEATURES = {
    'play_from_camera': 'starts the game at the camera (--load-slot / --start-at)',
}


def available(mode, feature):
    return mode == EX or feature not in EX_FEATURES


def default_game_command(mode):
    if mode == EX:
        if sysutil.WINDOWS:
            return ''            # OpenDWWandWExpanded runs on Linux only for now
        return 'opendwwandwexpanded --game-dir {game}'
    return sysutil.default_game_command()


def describe(mode):
    if mode == EX:
        return ('EX: OpenDWWandWExpanded. Adds: ' + '; '.join(EX_FEATURES.values()) +
                ('. Not available on Windows yet.' if sysutil.WINDOWS else '.'))
    return 'Standard: the original game (deep6.exe or OpenDWWandW). EX-only features are disabled.'
