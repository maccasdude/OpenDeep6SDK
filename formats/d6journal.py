#!/usr/bin/env python3
"""JOURNAL.nnn reader/writer (Wizards & Warriors, jentry.c).

Layout:
  0x000 i32 wpos            next free offset in the entry stream
  0x004 (i32 first, i32 last)[192]   per key; -1/-1 = no entries.
        key 0..159 = NPC id (D6NPC / NPCDATA.PAK slot), 160..191 = 160 + GM id
  0x604 entry stream, 1 KB blocks; stream offset o is at file 0x604 + o.
        entry: i32 next (stream offset of the key's next entry, 0 = end),
               i16 msg (string index in the NPC/GM pak, string block type 2),
               char text[] NUL-terminated (cached prompt, '|' prefixed, or "")
        An entry never crosses a 1 KB block boundary (writer skips to the next block).
  File size = 0x604 + (wpos // 1024 + 1) * 1024. Stale bytes after wpos are kept.

parse(data) -> Journal; Journal.to_bytes() round-trips byte-identically.
"""
import struct
import sys

HDR = 0x604
BLOCK = 0x400
NKEYS = 192


class Entry(object):
    __slots__ = ('offset', 'next', 'msg', 'text')

    def __init__(self, offset, nxt, msg, text):
        self.offset, self.next, self.msg, self.text = offset, nxt, msg, text

    def __repr__(self):
        return 'Entry(@%d msg=%d next=%d text=%r)' % (self.offset, self.msg, self.next, self.text)


class Journal(object):
    def __init__(self):
        self.wpos = 0
        self.keys = [(-1, -1)] * NKEYS
        self.stream = b''

    def chain(self, key):
        """Entries of one key in order (follows the next links)."""
        first, _last = self.keys[key]
        out = []
        off = first
        while off != -1 and len(out) < 256:
            e = self.entry_at(off)
            out.append(e)
            if e.next == 0:
                break
            off = e.next
        return out

    def entry_at(self, off):
        nxt, msg = struct.unpack_from('<ih', self.stream, off)
        end = self.stream.index(b'\0', off + 6)
        return Entry(off, nxt, msg, self.stream[off + 6:end].decode('latin-1'))

    def used_keys(self):
        return [k for k, p in enumerate(self.keys) if p[0] != -1]

    @staticmethod
    def key_name(k):
        return 'NPC %d' % k if k < 160 else 'GM %d' % (k - 160)

    def add(self, key, msg, text=''):
        """Append an entry as PCJournalEntry_ does (no buffer garbage emulated)."""
        raw = text.encode('latin-1')
        off = self.wpos
        if raw and not raw.startswith(b'|'):
            raw = b'|' + raw
        if len(raw) + 7 + off % BLOCK >= BLOCK:
            off = (off // BLOCK + 1) * BLOCK
        need = (off // BLOCK + 1) * BLOCK
        if len(self.stream) < need:
            self.stream += b'\0' * (need - len(self.stream))
        first, last = self.keys[key]
        if last != -1:
            s = bytearray(self.stream)
            struct.pack_into('<i', s, last, off)
            self.stream = bytes(s)
        rec = struct.pack('<ih', 0, msg) + raw + b'\0'
        self.stream = self.stream[:off] + rec + self.stream[off + len(rec):]
        self.keys[key] = (off if first == -1 else first, off)
        self.wpos = off + len(rec)

    def to_bytes(self):
        h = struct.pack('<i', self.wpos) + b''.join(struct.pack('<ii', *p) for p in self.keys)
        return h + self.stream


def parse(data):
    j = Journal()
    j.wpos = struct.unpack_from('<i', data, 0)[0]
    j.keys = [struct.unpack_from('<ii', data, 4 + 8 * i) for i in range(NKEYS)]
    j.stream = bytes(data[HDR:])
    return j


def main(argv):
    for fn in argv[1:]:
        data = open(fn, 'rb').read()
        j = parse(data)
        ok = j.to_bytes() == data
        print('%s: %d bytes, wpos %d, roundtrip %s' % (fn, len(data), j.wpos, 'OK' if ok else 'FAIL'))
        for k in j.used_keys():
            print('  %s (key %d):' % (j.key_name(k), k))
            for e in j.chain(k):
                print('    msg %4d %s' % (e.msg, e.text.replace('\n', '\\n')))


if __name__ == '__main__':
    main(sys.argv)
