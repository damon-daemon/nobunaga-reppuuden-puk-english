"""MDATA1/MESSAGE.NB8: blocks of message entries, the block table in the program, and the
entry grammar (notes/engine.md "Message interpreter").

File = blocks, each starting on a 2 KB boundary (zero padding). The program holds the byte
offset of every block (u32) plus the file size at TABLE. Block = u16 n, n u16 offsets (from
the block start), then the entries. Message id = block * 1000 + index; block 0 stays
resident, other blocks are loaded on demand right after it (0x8010ED44 .. 0x80116568).

Entry = bytecode read by the interpreter at 0x8008a184. Bytes >= 0x20 are text (Shift-JIS,
half-width kana, and ASCII); control bytes:
  01 'C'/'J' u16      call / jump to another message     01 'S'/'R' expr   select / return
  01 'M'              (no operand)
  02 ...              expression (value printed)          05 05 xx          keyword (04 = if:
  08 09 0A            output as text (0A newline)                           a condition follows)
  1A/1B x             mode switch (K H k W C)
Expression = term (03 op term)*; condition = expression ((04|06) op expression)*.
Term = 02 b [field] | digits | '%' digits. split() cuts an entry into ('code', bytes) and
('text', bytes) parts; b''.join of the parts is the entry again.
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))

NAME = 'MDATA1/MESSAGE.NB8'
TABLE = 0x800DBF10                 # va of the u32 block table in SLPM_864.83
EXE_BASE = 0x80010000 - 0x800      # va - file offset
NBLOCKS = 64
ALIGN = 2048
BUF_SIZE = 0x80116568 - 0x8010ED44 # current block (block 0 lives in free RAM: uipatch.static_block0)
BUF = 0x8010ED44
B0_MAX = 0x7ffc
OUT_MAX = 0x1FE                    # expansion buffer of one message (0x80089cbc)


def table(exe):
    return list(struct.unpack_from('<%dI' % (NBLOCKS + 1), exe, TABLE - EXE_BASE))


def set_table(exe, offsets):
    assert len(offsets) == NBLOCKS + 1 and all(o % ALIGN == 0 for o in offsets[:-1])
    struct.pack_into('<%dI' % (NBLOCKS + 1), exe, TABLE - EXE_BASE, *offsets)


def parse_block(data):
    """Block bytes (with padding) -> list of entry bytes (the last one ends at the padding)."""
    n = struct.unpack_from('<H', data, 0)[0]
    offs = struct.unpack_from('<%dH' % n, data, 2) + (len(data.rstrip(b'\0')),)
    return [data[offs[i]:offs[i + 1]] for i in range(n)]


def build_block(ents):
    """Entries -> block bytes without padding (header layout as on the disc)."""
    out = bytearray(struct.pack('<H', len(ents)) + bytes(2 * len(ents)))
    for i, e in enumerate(ents):
        if len(out) > 0xFFFF:
            raise ValueError('block offset %d > 64 KB' % len(out))
        struct.pack_into('<H', out, 2 + 2 * i, len(out))
        out += e
    return bytes(out)


def parse(data, exe):
    t = table(exe)
    assert t[-1] == len(data), 'table does not match %s' % NAME
    return [parse_block(data[t[b]:t[b + 1]]) for b in range(NBLOCKS)]


def build(blocks, clean=None):
    """-> (file bytes, offset table). clean: the clean file + exe table, so an unchanged
    block keeps its original padding bytes (byte-identical rebuild)."""
    out, offs = bytearray(), []
    for b, ents in enumerate(blocks):
        offs.append(len(out))
        blk = build_block(ents)
        if clean:
            data, t = clean
            old = data[t[b]:t[b + 1]]
            if build_block(parse_block(old)) == blk:
                blk = old
        out += blk
        out += bytes(-len(out) % ALIGN)
    offs.append(len(out))
    return bytes(out), offs


# ---- entry grammar ----

def _cat(b):
    """0x80094cbc: operand class of `02 b` (0-6 objects with an optional field byte,
    7/8 string tables, 9 functions), None if invalid."""
    for c, lo in enumerate((0x01, 0x0B, 0x15, 0x1F, 0x29, 0x33, 0x3D, 0x65, 0x6F)):
        if lo <= b < lo + 9:
            return c
    return 9 if b >= 0xC9 else None


# functions `02 C9..DA` (table 0x800B3F14): argument kinds, o = object `02 b`, e = expression
FUNC_ARGS = {0xC9: 'e', 0xCA: 'e', 0xCB: 'e', 0xCC: 'e', 0xCD: 'oo', 0xCE: 'oo', 0xCF: 'oo',
             0xD0: 'oo', 0xD1: 'oo', 0xD2: 'o', 0xD6: 'oo', 0xD9: 'o'}


class GrammarError(ValueError):
    pass


def _digits(e, i):
    j = i
    while j < len(e) and j - i < 10 and 0x30 <= e[j] <= 0x39:
        j += 1
    return j


def _term(e, i):
    if i >= len(e):
        raise GrammarError('term at end')
    c = e[i]
    if c == 2:
        cat = _cat(e[i + 1])
        if cat is None:
            raise GrammarError('bad operand %02x at %d' % (e[i + 1], i))
        i += 2
        if cat <= 6 and i < len(e) and e[i] >= 0x20:
            i += 1                                   # field selector
        elif cat == 9:
            for a in FUNC_ARGS.get(e[i - 1], ''):
                if a == 'o':                         # object reader 0x80096504: `02 b`, no field
                    if e[i] != 2:
                        raise GrammarError('object argument %02x at %d' % (e[i], i))
                    i += 2
                else:
                    i = _expr(e, i)
        return i
    if c == 0x25:
        return _digits(e, i + 1)
    if 0x30 <= c <= 0x39:
        return _digits(e, i)
    raise GrammarError('bad term byte %02x at %d' % (c, i))


def _expr(e, i):
    i = _term(e, i)
    while i < len(e) and e[i] == 3:
        i = _term(e, i + 2)
    return i


def _cond(e, i):
    i = _expr(e, i)
    while i < len(e) and e[i] in (4, 6):
        i = _expr(e, i + 2)
    return i


def tokens(e):
    """Entry bytes -> [(kind, bytes)], kind = 'text' (a run of text bytes), 'call'
    (`01 43 u16`), 'print' (`02` expression, value printed), 'mode' (`1A`/`1B x`) or
    'struct' (jumps, select/return, keywords with their condition)."""
    out, i, text = [], 0, bytearray()

    def code(j, kind):
        if text:
            out.append(('text', bytes(text)))
            text.clear()
        out.append((kind, e[i:j]))
        return j
    while i < len(e):
        c = e[i]
        if c >= 0x20 or c in (8, 9, 0x0A):
            n = 2 if (0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC) else 1
            text += e[i:i + n]
            i += n
        elif c == 1:
            k = e[i + 1]
            if k == 0x43:
                i = code(i + 4, 'call')
            elif k == 0x4A:
                i = code(i + 4, 'struct')
            elif k in b'SR':
                i = code(_expr(e, i + 2), 'struct')
            elif k == 0x4D:
                i = code(i + 2, 'struct')
            else:
                raise GrammarError('01 %02x at %d' % (k, i))
        elif c == 2:
            i = code(_expr(e, i), 'print')
        elif c == 5:
            if e[i + 1:i + 2] != b'\x05' or not 4 <= e[i + 2] <= 9:
                raise GrammarError('keyword %s at %d' % (e[i:i + 3].hex(), i))
            i = code(_cond(e, i + 3) if e[i + 2] == 4 else i + 3, 'struct')
        elif c in (0x1A, 0x1B):
            i = code(i + 2, 'mode')
        else:
            raise GrammarError('control %02x at %d' % (c, i))
    if text:
        out.append(('text', bytes(text)))
    assert b''.join(v for _, v in out) == e
    return out


def split(e):
    """Entry bytes -> [('code'|'text', bytes)] (consecutive codes merged)."""
    parts = []
    for kind, v in tokens(e):
        kind = 'text' if kind == 'text' else 'code'
        if parts and parts[-1][0] == kind == 'code':
            parts[-1] = ('code', parts[-1][1] + v)
        else:
            parts.append((kind, v))
    return parts


def texts(e):
    return [v for k, v in split(e) if k == 'text']


def segments(entry):
    """-> list of ('code', bytes) and ('seg', [(kind, bytes)]): a segment is a maximal run of
    text, calls, prints and mode switches with at least one text token; it is what one
    English string replaces. Everything else (jumps, keywords, conditions) is kept."""
    out, run = [], []

    def flush():
        if any(k == 'text' for k, _ in run):
            out.append(('seg', list(run)))
        elif run:
            out.append(('code', b''.join(v for _, v in run)))
        run.clear()
    for kind, v in tokens(entry):
        if kind == 'struct':
            flush()
            out.append(('code', v))
        else:
            run.append((kind, v))
    flush()
    return out


def seg_bytes(pieces):
    return b''.join(v for _, v in pieces)


def join_segments(items):
    return b''.join(v if kind == 'code' else seg_bytes(v) for kind, v in items)


def check_sizes(offs):
    """Errors if a block does not fit its RAM. Block 0 is a static copy in free RAM
    (uipatch.static_block0): its message offsets must stay below B0_MAX (signed 16-bit
    immediates); every other block is read whole (padding included) into the buffer."""
    b0 = offs[1] - offs[0]
    big = max(range(1, NBLOCKS), key=lambda b: offs[b + 1] - offs[b])
    out = []
    if b0 + 3 & ~3 > B0_MAX:
        out.append('message block 0: %d bytes > %d' % (b0, B0_MAX))
    if offs[big + 1] - offs[big] > BUF_SIZE:
        out.append('message buffer: block %d (%d) > %d bytes' % (big, offs[big + 1] - offs[big], BUF_SIZE))
    return out


if __name__ == '__main__':                        # self-test on the extracted disc
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data = open(os.path.join(ROOT, 'extract/jp', NAME), 'rb').read()
    exe = open(os.path.join(ROOT, 'extract/jp/SLPM_864.83'), 'rb').read()
    blocks = parse(data, exe)
    bad, ascii_text, n = 0, 0, 0
    for b, ents in enumerate(blocks):
        for i, e in enumerate(ents):
            try:
                for t in texts(e):
                    n += 1
                    s = t.decode('cp932', 'replace')
                    if any(ch.isascii() and ch not in '\n' for ch in s):
                        ascii_text += 1
                        if ascii_text <= 15:
                            print('ascii in text', b * 1000 + i, repr(s[:50]), e.hex(' ')[:120])
            except (GrammarError, IndexError) as x:
                bad += 1
                if bad <= 15:
                    print('error', b * 1000 + i, x, e.hex(' ')[:160])
    print('entries %d, text parts %d, grammar errors %d, text parts with ASCII %d'
          % (sum(map(len, blocks)), n, bad, ascii_text))
    out, offs = build(blocks, (data, table(exe)))
    print('rebuild identical:', out == data, check_sizes(offs) or 'sizes ok')
    assert all(join_segments(segments(e)) == e for es in blocks for e in es)
    print('segments:', sum(k == 'seg' for es in blocks for e in es for k, _ in segments(e)))
