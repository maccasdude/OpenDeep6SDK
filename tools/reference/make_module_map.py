#!/usr/bin/env python3
"""Writes docs/exe_modules.md: the game modules of deep6.exe (from the Watcom
debug info, as exported to symbols.tsv by the OpenDWWandW RE kit), with the
number of named functions and globals and the SDK documents that cite them.

usage: make_module_map.py SYMBOLS.TSV [OUT.md]"""
import collections, csv, os, re, sys

ROLE = {
    'deep6.c': 'start-up, globals, data loading, town/spoke switch (SetSpokeEntry_)',
    'd6spoke.c': 'spoke loading (LoadSpokeSegment_), entry points, spoke change',
    'scenload.c': 'object lists (.TOL/.BOL/.FOL), object ids',
    'events.c': 'triggers, bound areas, switches, specials, event VM (EVENTS.COD)',
    'npc.c': 'NPC dialogue VM (NPCDATA.PAK)', 'guild.c': 'guildmaster VM (GMDATA.PAK)',
    'terrain.c': 'terrain loading and textures', 'tnew.c': 'terrain tile drawing, walls, canopy',
    'terbsp.c': 'terrain BSP leaf tree (InBSPArea_)', 'bspfile.c': '.bsp/.lf/.ls loading',
    'bsp.c': 'BSP drawing', 'texlist.c': '.twd texture wads', 'cardlite.c': 'coloured lights (.lgt/.rgb)',
    'nav_sys.c': 'nav graph (.nvs/.l2n/.NAV)', 'pathai.c': 'path finding, D6LINK',
    'model.c': '.mdl loading and drawing', 'mdldata.c': 'model name tables',
    'pcmodel.c': 'PC models, PC snapshots (PCSNAP.nnn)', 'particle.c': 'particle emitters (emitters.dat)',
    'efx.c': 'visual effects (EFX)', 'spelleff.c': 'spell effects',
    'combat.c': 'combat, recall/portal effects', 'monster.c': 'monsters (D6MONS)',
    'segwrite.c': 'saves: D6SEGnn.GAM, D6ARCHIV.DAT', 'traps.c': 'D6TRAP locks and traps',
    'textmsg.c': 'TEXTPAK messages', 'd6string.c': 'D6STRING.DAT', 'journal.c': 'journal (JOURNAL.nnn)',
    'jhpmap.c': 'automap (.lm/.fog/.mrk)', 'automap.c': 'automap drawing',
    'playsam.c': 'music', 'soundefx.c': 'sound effects, speech, PC talk', 'audioc.c': 'audio device',
    'sfxcache.c': 'sound cache', 'townhub.c': 'town hub screen, gates, Gareth',
    'townavi.c': 'town hub videos', 'townhall.c': 'town hall', 'townsmit.c': 'smithy shop',
    'townmage.c': 'mage guild', 'towntmpl.c': 'temple', 'towntvrn.c': 'tavern', 'towndojo.c': 'dojo',
    'townpawn.c': 'pawn shop', 'townyard.c': 'ship yard', 'pccreate.c': 'character creation',
    'pcinvent.c': 'inventory screen', 'menu.c': 'title and in-game menus', 'config.c': 'options book',
    'font.c': 'fonts (.FNT/.p16)', 'lmouse.c': 'mouse pointers (.ptr)', 'network.c': 'multiplayer messages',
    'collide.c': 'collision', 'camera.c': 'camera', 'frustum.c': 'view frustum',
    'cardctrl.c': 'Direct3D renderer control', 'screen.c': 'DirectDraw screen',
    'mapobj.c': 'debug level viewer (FetchObjects_)', 'treasure.c': 'treasure (D6TREAS/D6TRLIST)',
}

def main(argv):
    if len(argv) < 2:
        print(__doc__); return 1
    out = argv[2] if len(argv) > 2 else os.path.join(os.path.dirname(__file__), '..', '..', 'docs', 'exe_modules.md')
    fn = collections.Counter(); gl = collections.Counter()
    for row in csv.reader(open(argv[1]), delimiter='\t'):
        if len(row) < 4:
            continue
        kind, mod = row[1], row[3]
        if not re.search(r'\.(c|cpp|asm)$', mod, re.I):
            continue                               # Watcom run-time library
        (fn if kind in ('4', '5') else gl)[mod] += kind in ('2', '3', '4', '5')
    docdir = os.path.join(os.path.dirname(out))
    texts = {}
    for root, _, files in os.walk(docdir):
        for f in files:
            if f.endswith('.md') and f not in ('exe_modules.md', 'REFERENCE.md'):
                texts[os.path.relpath(os.path.join(root, f), docdir)] = open(os.path.join(root, f)).read()
    mods = sorted(set(fn) | set(gl), key=str.lower)
    L = ['# Appendix: deep6.exe modules', '',
         'The game modules of deep6.exe (Watcom debug info: %d source files; the' % len(mods),
         'Watcom run-time library is left out). *Functions* / *globals* are the named',
         'symbols of each module. *Cited in* lists the SDK documents that mention the',
         'module, i.e. where its behaviour is described. Roles are only given where',
         'the SDK has looked at the code.', '',
         '| module | functions | globals | role | cited in |', '|---|---|---|---|---|']
    for m in mods:
        cites = sorted(k for k, t in texts.items() if re.search(r'(?<![\w.])' + re.escape(m) + r'\b', t))
        L.append('| %s | %d | %d | %s | %s |' % (m, fn[m], gl[m], ROLE.get(m, ''), ', '.join(cites)))
    open(out, 'w').write('\n'.join(L) + '\n')
    print('wrote', out, len(mods), 'modules')
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
