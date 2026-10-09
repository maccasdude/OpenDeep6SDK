#!/usr/bin/env python3
"""Compiles the SDK documentation into one reference document:
docs/REFERENCE.md (and, with --pdf, a PDF next to it, via pandoc and
wkhtmltopdf).

The chapters stay separate files (docs/engine.md, docs/formats/*.md, ...);
this script only joins them in reading order, numbers the chapters and adds
a table of contents. Run it after editing any chapter.

usage: build_reference.py [--pdf] [--version V]"""
import datetime
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.normpath(os.path.join(HERE, '..', '..', 'docs'))

CHAPTERS = [
    'engine.md',
    'formats/levels.md',
    'formats/terrain.md',
    'formats/walls_textures.md',
    'formats/data.md',
    'formats/databases.md',
    'formats/models.md',
    'formats/effects.md',
    'formats/audio.md',
    'formats/npc.md',
    'formats/exits.md',
    'formats/saves.md',
    'formats/ui.md',
    'geometry.md',
]
APPENDICES = ['exe_modules.md', 'verification.md']

CSS = """body{font-family:'DejaVu Sans',sans-serif;font-size:10pt;line-height:1.35;margin:0 1.2cm}
h1{font-size:18pt;border-bottom:2px solid #444;margin-top:1.5em;page-break-before:always}
h1:first-of-type{page-break-before:avoid}
h2{font-size:13pt;margin-top:1.2em}h3{font-size:11pt}
code,pre{font-family:'DejaVu Sans Mono',monospace;font-size:8.5pt}
pre{background:#f4f4f4;padding:6px;white-space:pre-wrap}
table{border-collapse:collapse;font-size:8pt;margin:6px 0}
td,th{border:1px solid #bbb;padding:2px 4px;vertical-align:top;word-wrap:break-word}
td:first-child,th{white-space:nowrap}
th{background:#e8e8e8}"""


def slug(title):
    """GitHub's heading anchor."""
    s = title.strip().lower()
    s = re.sub(r'[^\w\- ]', '', s)
    return s.replace(' ', '-')


def build(version):
    parts, toc = [], []
    entries = [(c, False) for c in CHAPTERS] + [(a, True) for a in APPENDICES]
    n = 0
    for i, (rel, appendix) in enumerate(entries):
        text = open(os.path.join(DOCS, rel), encoding='utf-8').read().strip('\n')
        lines = text.split('\n')
        if not lines[0].startswith('# '):
            raise SystemExit('%s: first line must be a "# " title' % rel)
        title = re.sub(r'^Appendix:\s*', '', lines[0][2:].strip())
        if appendix:
            label = 'Appendix %s' % chr(ord('A') + i - len(CHAPTERS))
        else:
            n += 1
            label = 'Chapter %d' % n
        head = '%s. %s' % (label, title)
        toc.append('* [%s](#%s) (`docs/%s`)' % (head, slug(head), rel))
        parts.append('# %s\n\n*Source: `docs/%s`*\n\n%s' % (head, rel, '\n'.join(lines[1:]).strip('\n')))
    today = datetime.date.today().isoformat()
    front = ('# Deep6 Engine and File Format Reference\n\n'
             '**Wizards & Warriors (Heuristic Park, 2000) - deep6.exe**\n\n'
             'OpenDeep6SDK %s, compiled %s from the SDK documentation.\n\n'
             'Everything here was found by reverse engineering the GOG release of the game '
             '(build 1266995554): reading the executable with its Watcom symbols, running it '
             'and round-tripping every data file. It is meant to be enough to write your own '
             'tools for the game; the Python modules in `formats/` are working references for '
             'every format. No game data is included.\n\n'
             '## Contents\n\n%s\n' % (version, today, '\n'.join(toc)))
    out = front + '\n\n' + '\n\n'.join(parts) + '\n'
    path = os.path.join(DOCS, 'REFERENCE.md')
    open(path, 'w', encoding='utf-8').write(out)
    return path


def pdf(md, version):
    css = os.path.join(os.path.dirname(md), '.reference.css')
    open(css, 'w').write(CSS)
    out = os.path.splitext(md)[0] + '.pdf'
    try:
        subprocess.check_call(['pandoc', md, '-f', 'gfm', '-o', out, '--pdf-engine=wkhtmltopdf',
                               '--css', css, '--metadata', 'pagetitle=Deep6 Engine and File Format Reference',
                               '-V', 'margin-top=15mm', '-V', 'margin-bottom=15mm',
                               '-V', 'margin-left=10mm', '-V', 'margin-right=10mm'])
    finally:
        os.remove(css)
    return out


def main(argv):
    version = 'v1.1.0'
    if '--version' in argv:
        version = argv[argv.index('--version') + 1]
    md = build(version)
    print('wrote', md)
    if '--pdf' in argv:
        print('wrote', pdf(md, version))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
