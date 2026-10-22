"""MESSAGE.NB8 survey decoder (heuristic, phase 1).

File = 2 KB-aligned blocks; the program holds a u32 table of block byte offsets plus the
file size (va 0x800DBF10, 64 blocks). Block = u16 n, n u16 offsets (relative to the block),
entries. Entry = bytecode with raw Shift-JIS text (no text-run prefix, unlike S6):
`01 43 u16` block 0 word insert, `01 xx` other code, `02 01 xx` / `02 xx` variable insert,
`04` condition start, `05` separator/end (`05 05 05` closes an entry), `06` logical op,
`0A` newline, `1B K`/`1B H`/`1B k` kana switches. Half-width katakana stand for hiragana.

usage: msg.py dump [-x] > out.txt     (-x: also print the raw bytes)
       msg.py stats
"""
import os, re, struct, sys, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MSG, EXE, TABLE, NBLOCKS = 'extract/jp/MDATA1/MESSAGE.NB8', 'extract/jp/SLPM_864.83', 0x800DBF10, 64
EXE_BASE = 0x80010000 - 0x800


def load():
    d = open(os.path.join(ROOT, MSG), 'rb').read()
    exe = open(os.path.join(ROOT, EXE), 'rb').read()
    t = struct.unpack_from('<%dI' % (NBLOCKS + 1), exe, TABLE - EXE_BASE)
    assert t[-1] == len(d)
    blocks = []
    for b in range(NBLOCKS):
        blk = d[t[b]:t[b + 1]]
        n = struct.unpack_from('<H', blk, 0)[0]
        o = struct.unpack_from('<%dH' % n, blk, 2) + (len(blk.rstrip(b'\0')),)
        blocks.append([blk[o[i]:o[i + 1]] for i in range(n)])
    return blocks


def hira(s):
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ン' else c for c in s)


def decode(e):
    """-> (display string, number of text characters)."""
    out, nch, i, text = [], 0, 0, bytearray()

    def flush():
        nonlocal nch
        if text:
            s = text.decode('cp932', 'replace')
            nch += len(s)
            s = re.sub('[ｦ-ﾟ]+', lambda m: hira(unicodedata.normalize('NFKC', m.group())), s)
            out.append(s.replace('\n', '\\n'))
            text.clear()
    while i < len(e):
        c = e[i]
        if c == 1:
            flush()
            if e[i + 1] == 0x43:
                out.append('{C%d}' % struct.unpack_from('<H', e, i + 2)[0]); i += 4
            else:
                out.append('{1:%02x}' % e[i + 1]); i += 2
        elif c == 2:
            flush()
            if e[i + 1] == 1:
                out.append('{V%02x}' % e[i + 2]); i += 3
            else:
                out.append('{v%02x}' % e[i + 1]); i += 2
        elif c in (4, 5, 6):
            flush()
            out.append({4: '{?', 5: '}', 6: '{op}'}[c]); i += 1
        elif c == 0x1b:
            flush()
            out.append('\\e' + chr(e[i + 1])); i += 2
        elif (0x81 <= c <= 0x9f or 0xe0 <= c <= 0xfc) and i + 1 < len(e):
            text += e[i:i + 2]; i += 2
        else:
            text.append(c); i += 1
    flush()
    return ''.join(out), nch


if __name__ == '__main__':
    blocks = load()
    if sys.argv[1] == 'stats':
        tot = 0
        for bi, ents in enumerate(blocks):
            n = sum(decode(e)[1] for e in ents)
            tot += n
            first = next((decode(e)[0] for e in ents if decode(e)[1]), '')[:60]
            print('%2d %4d entries %6d chars  %s' % (bi, len(ents), n, first))
        print('total chars', tot, 'entries', sum(map(len, blocks)))
    else:
        raw = '-x' in sys.argv
        for bi, ents in enumerate(blocks):
            for i, e in enumerate(ents):
                print('%d %s' % (bi * 1000 + i, decode(e)[0]))
                if raw:
                    print('    ' + e.hex(' '))
