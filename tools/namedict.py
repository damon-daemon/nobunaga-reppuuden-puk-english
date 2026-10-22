"""Japanese -> English name dictionary in free RAM + the print loop that uses it.

(Port of the Sangokushi 6 PUK tool; the same Koei text library.) print_string 0x80046b48
draws a NUL-terminated string byte by byte (putc). Its loop is replaced by print_all (in the
heap):

  1. expand the string into BUF: at every 2-byte character, look up the longest
     dictionary key; a hit is replaced by its English. When the hit is a family name (S)
     and a given name (G) follows directly, that is looked up too and joined with a
     space ("織田信長" = 織田 S + 信長 G -> "Oda Nobunaga"), so full names are not stored.
     No hit -> the character is copied (drawn in Japanese).
  2. draw BUF through the word-wrap wrapper (tools/vwf.py) and putc, and stop when a
     line after the first would start below the pixel buffer: the buffer is
     wrap width x height (0x800449a4: gp-0x7712 / gp-0x7710) and the blitter never
     checks the height, so English longer than the Japanese box would overwrite the
     memory after it. (Not covered: a single word wider than the line wraps inside putc.)
  Columns: the Japanese lays tables out in padded strings at 8 px a byte (list rows
  like "  織田信長  23   99", sprintf "%-9s" / "%3d"). With proportional English the
  source column (bytes since the line start) is kept where the padding says so:
     - a run of >= 2 spaces before digits: the number is right-aligned on the
       Japanese end column of its digits (digits are tabular, tools/pixfont.py);
     - a run of >= 2 spaces before anything else: left-aligned on the column after
       the run;
     - right after a name hit, one space is enough (before digits or Japanese);
     - spaces at the start of a line before a name (Japanese centring of short
       names in a column) become a one-column (8 px) indent;
     - a single space before digits (not after a name) is digit-wide at draw time.
  Lines with ESC (colour codes) keep plain spaces. The tab in the expanded text:
  TAB 0x1F, 0x20 + (px >> 6), 0x20 + (px & 63) (never a control or lead byte);
  print_all sets x = max(x + space, line start + px); measure() does the same.

Dictionary (built from {japanese: english} pairs, tools/names.py):
  IDX:  u32 n, then n x (u16 first character (lead << 8 | trail), u16 offset into BLOB),
        sorted by character
  BLOB: per first character, entries longest key first: u8 header = 0x20 | S 0x80 |
        G 0x40 | length of the rest of the key (< 32), the rest of the key, the English,
        NUL; a 0 byte ends the group.
build() checks every pair with expand() (a model of the MIPS code) and stores a pair
literally (no S/G flags) when the composition would give another English.
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm, pixfont, text, vwf
from asm import hi, lo

PRINT_CALL = 0x80046c0c                  # b 0x80046c24 / lbu v0, 0(s0): enters print_string's loop
PRINT_EXIT = 0x80046c30                  # addiu v0, zero, 1 after the loop
GP_CELL_H, GP_BUF_H = -0x7f86, -0x7710
CAP = 1024                               # expanded string (messages <= 510 bytes)
F_ANY, F_G, F_S = 0x20, 0x40, 0x80
F_ALL = F_ANY | F_G | F_S
TAB, TAB_BASE = 0x1F, 0x20
CELL = 0x1E                              # table cell marker (tools/cells.py)
CELL_REF = 0x1D                          # cell whose string is elsewhere: + 3 bytes 0x80 | 7 bits of
RAM = 0x80000000                         # the address (21 bits, RAM offset)
# LAYOUT block (tools/cells.py writes, expand reads): u8 column count, 3 pad, 16 x (u16 px,
# u8 width px, u8 flags), then expand's cell index (word) and open cell (word: its tab in BUF)
MAX_COLS, L_COLS, L_CI, L_P = 16, 4, 68, 72
LAYOUT_SIZE = 76
WRAPM_SLACK = 12                         # wrapm: room for a newline + 2-byte char / tab / ESC run + NUL
WIDTHS = bytes(pixfont.glyphs()[chr(c)][1] for c in range(0x20, 0x7F))
DIGIT_ADV = pixfont.DIGIT_W + pixfont.SPACING
SPACE_W = pixfont.SPACE_ADV
FIG_EXTRA = DIGIT_ADV - SPACE_W


def is_lead(c):
    return 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC


def cell_px(buf, widths=None):
    """Pixel width of an expanded table cell (no tabs, no newlines)."""
    widths = widths or WIDTHS
    x, i = 0, 0
    while i < len(buf):
        c = buf[i]
        i += 1
        if c == 0x20:
            j = i
            while j < len(buf) and buf[j] == 0x20:
                j += 1
            x += SPACE_W + (FIG_EXTRA if j < len(buf) and 0x30 <= buf[j] <= 0x39 else 0)
        elif c < 0x20:
            pass
        elif c < 0x7F:
            x += widths[c - 0x20]
        elif 0xA0 <= c < 0xE0:
            x += vwf.DEFAULT_W
        else:
            x += 16
            i += 1
    return x


class Dict:
    """entries: {key bytes: (english bytes, flags)}; layout: table columns [(px, width, flags)]
    as tools/cells.py records them (model of the LAYOUT block)"""
    def __init__(self):
        self.ent = {}
        self.layout = []
        self.deref = lambda a: b''               # CELL_REF: address -> string (model)

    def lookup(self, s, i, mask):
        best = None
        for n in range(len(s) - i, 1, -1):
            e = self.ent.get(s[i:i + n])
            if e and e[1] & mask:
                return e[0], n, e[1]
        return best

    def expand(self, s):
        out, i, start, esc = bytearray(), 0, 0, False
        ci, cell = 0, [None]

        def cell_end():
            p = cell[0]
            if p is None:
                return
            cell[0] = None
            col, w, flags = self.layout[ci - 1]
            px = cell_px(out[p + 3:])
            free = w - px
            off = [0, free, free >> 1, free - ((flags >> 4) & 3) * 8][flags & 3]
            col += max(off, 0)
            out[p + 1], out[p + 2] = TAB_BASE + (col >> 6), TAB_BASE + (col & 63)

        def tab(px):
            out.extend([TAB, TAB_BASE + (px >> 6), TAB_BASE + (px & 63)])

        def spaces(i, after_name):
            """at a space: (new i, True) when a column tab was emitted"""
            if esc or len(out) > CAP - 4 - 1:
                return i, False
            j = i
            while j < len(s) and s[j] == 0x20:
                j += 1
            if i == start and j + 1 < len(s) and is_lead(s[j]) and self.lookup(s, j, F_ANY):
                tab(8)                                  # centring before a name: one column
                return j, True
            n, k = j - i, j
            while k < len(s) and 0x30 <= s[k] <= 0x39:
                k += 1
            if k > j:                                   # digits: right-align
                if n >= 2 or after_name:
                    tab((k - start) * 8 - (k - j) * DIGIT_ADV)
                    return j, True
            elif n >= 2 or (after_name and j < len(s) and s[j] >= 0x80):
                tab((j - start) * 8)
                return j, True
            return i, False

        while i < len(s):
            c = s[i]
            if not is_lead(c) or i + 1 >= len(s):
                if c == 0x0A:
                    cell_end()
                    start, esc, ci = i + 1, False, 0
                elif c == 0x1B:
                    esc = True
                elif c in (CELL, CELL_REF):             # table cell (tools/cells.py)
                    cell_end()
                    if ci < len(self.layout):
                        cell[0] = len(out)
                        out += bytes([TAB, TAB_BASE, TAB_BASE])
                        ci += 1
                        esc = True                      # plain spaces inside cells
                    i += 1
                    if c == CELL_REF:
                        a = RAM | (s[i] & 0x7F) << 14 | (s[i + 1] & 0x7F) << 7 | s[i + 2] & 0x7F
                        out += self.deref(a)[:CAP - 4 - len(out)]
                        i += 3
                    continue
                elif c == 0x20:
                    i, done = spaces(i, False)
                    if done:
                        continue
                elif (0x41 <= c <= 0x5A and len(out) >= 2 and 0x61 <= out[-1] <= 0x7A
                      and out[-2] != 0x1B):
                    out.append(0x20)                    # "BbbbBbbb": Latin surname + given name
                out.append(c)
                i += 1
                continue
            hit = self.lookup(s, i, F_ANY)
            if not hit and i == start and (i + 2 == len(s) or s[i + 2] == 0x0A):
                hit = self.lookup(s, i, F_ALL)          # a whole line: any name (忍 castle)
            if not hit:
                out += s[i:i + 2]
                i += 2
                continue
            out += hit[0]
            i += hit[1]
            if hit[2] & F_S:
                g = self.lookup(s, i, F_G)
                if g:
                    out += b' ' + g[0]
                    i += g[1]
            if i < len(s) and s[i] == 0x20:
                i, _ = spaces(i, True)
        cell_end()
        return bytes(out)


def fig_space(buf, i):
    """The space at buf[i] is drawn digit-wide: digits follow (after more spaces) and it is
    not prose (the byte before is no letter and none of , - .; read even at i = 0)."""
    p = (buf[i - 1] if i else 0) | 0x20
    if 0x61 <= p < 0x7B or 0x2C <= p < 0x2F:
        return False
    j = i + 1
    while j < len(buf) and buf[j] == 0x20:
        j += 1
    return j < len(buf) and 0x30 <= buf[j] <= 0x39


def measure_px(buf, widths):
    """Pixel width of the widest line of an expanded string, as measure() counts it."""
    x, i, widest = 0, 0, 0
    while i < len(buf):
        c = buf[i]
        i += 1
        if c == 0x0A:
            widest, x = max(widest, x), 0
        elif c == 0x1B:
            while i < len(buf) and buf[i] != 0x43:
                i += 1
            i += 1
        elif c == TAB:
            x = max(x + SPACE_W, (buf[i] - TAB_BASE) << 6 | (buf[i + 1] - TAB_BASE))
            i += 2
        elif c == 0x20:
            x += SPACE_W + (FIG_EXTRA if fig_space(buf, i - 1) else 0)
        elif c < 0x20:
            pass
        elif c < 0x7F:
            x += widths[c - 0x20]
        elif 0xA0 <= c < 0xE0:
            x += vwf.DEFAULT_W
        else:
            x += 16
            i += 1
    return max(widest, x)


def build(pairs, surnames, givens, not_alone=(), alone_ok=()):
    """pairs {jp str: en str}; surnames/givens: sets of jp keys that compose; not_alone:
    name parts that are also common words (政治 politics): matched only in a full name;
    alone_ok: one-kanji names that match anywhere (never a word in the script: 泪 treasure)."""
    d = Dict()
    enc = {text.encode(j): e.encode('ascii') for j, e in pairs.items()}
    # a one-kanji name (family 張 林 関, given 飛 備, castle 内) never matches on its own: alone it is far
    # more often part of a Japanese word (尾張 -> "尾Zhang"); full names with such a part are
    # stored whole (the check below), a one-kanji given name still composes after a family name
    after_only = {k for k in enc if (len(text.decode(k)) == 1 and text.decode(k) not in alone_ok)
                  or text.decode(k) in not_alone}
    for k, e in enc.items():
        j = text.decode(k)
        f = (0 if k in after_only else F_ANY) | (F_S if j in surnames else 0) | (F_G if j in givens else 0)
        f = f or F_G                            # a one-kanji place name: a whole line only
        if f != F_ANY:
            d.ent[k] = (e, f)
    enc = {k: e for k, e in enc.items() if k not in after_only}
    for _ in range(3):                    # literal entries for what does not compose
        bad = [k for k, e in enc.items() if d.expand(k) != e]
        for k in bad:
            d.ent[k] = (enc[k], d.ent[k][1] if k in d.ent else F_ANY)
        if not bad:
            break
    bad = [text.decode(k) for k, e in enc.items() if d.expand(k) != e]
    if bad:
        raise ValueError('names do not expand to their English: %s' % bad[:10])
    return d


def pack(d, base):
    """(idx bytes, blob bytes); idx at base, blob right after it."""
    groups = {}
    for k, (e, f) in d.ent.items():
        if len(k) - 2 >= 32:
            raise ValueError('name key too long: %s' % text.decode(k))
        groups.setdefault(k[:2], []).append((k, e, f))
    blob, idx = bytearray(), []
    for first in sorted(groups):
        idx.append((first[0] << 8 | first[1], len(blob)))
        for k, e, f in sorted(groups[first], key=lambda x: -len(x[0])):
            blob += bytes([f | (len(k) - 2)]) + k[2:] + e + b'\0'
        blob.append(0)
    if len(blob) > 0xFFFF:
        raise ValueError('name dictionary too large')
    head = struct.pack('<I', len(idx)) + b''.join(struct.pack('<HH', c, o) for c, o in idx)
    return head, bytes(blob)


def lines(idx, blob, buf, wrapper, layout):
    """print_all(a0 = string); measure(a0 = string) -> v0 = width of the widest line in
    8-px units (replaces strlen / the longest-line count where boxes are sized by bytes x 8); expand(a0) -> v0 = BUF;
    lookup(a0 = text, a1 = flag mask) -> v0 English, v1 key length, t7 header."""
    return [
        'print_all:',
        ('addiu', 'sp', 'sp', -48),
        ('sw', 'ra', 16, 'sp'),
        ('sw', 's1', 24, 'sp'),
        ('sw', 's2', 28, 'sp'),
        ('lh', 't0', vwf.GP_WRAP, 'gp'),      # word-wrap width = the buffer width
        ('lui', 'at', asm.hi(vwf.wrap_override(wrapper))),
        ('lh', 't1', asm.lo(vwf.wrap_override(wrapper)), 'at'),
        ('nop',),
        ('blez', 't1', 'wo'),                 # or the override, when set and narrower
        ('slt', 'at', 't1', 't0'),
        ('bnez', 'at', 'wo'),
        ('addu', 't0', 't1', 'zero'),
        ('lh', 't0', vwf.GP_WRAP, 'gp'),
        'wo:',
        ('lui', 'at', asm.hi(vwf.wrap_limit(wrapper))),
        ('sh', 't0', asm.lo(vwf.wrap_limit(wrapper)), 'at'),
        ('sw', 'zero', 36, 'sp'),             # one-line buffer (a text cell) and the text wider:
        ('lh', 't1', GP_BUF_H, 'gp'),         # no word wrap (the word would go to a line never
        ('lh', 't2', vwf.GP_ADV_Y, 'gp'),     # drawn), every second letter 1 px closer for the
        ('lh', 't3', GP_CELL_H, 'gp'),        # overflow (sp+36 px left, sp+40 toggle); the rest
        ('nop',),                             # is cut at the edge by putc's own wrap
        ('addu', 't2', 't2', 't3'),
        ('slt', 'at', 't1', 't2'),
        ('beqz', 'at', 'ps'),
        ('sw', 't0', 40, 'sp'),
        ('jal', 'measure'),
        ('sw', 'a0', 44, 'sp'),
        ('lw', 't0', 40, 'sp'),               # measure leaves the widest line's px in t4
        ('lw', 'a0', 44, 'sp'),
        ('subu', 't1', 't4', 't0'),
        ('blez', 't1', 'ps'),
        ('sw', 't1', 36, 'sp'),
        ('addiu', 't2', 'zero', -1),
        ('lui', 'at', asm.hi(vwf.wrap_limit(wrapper))),
        ('sh', 't2', asm.lo(vwf.wrap_limit(wrapper)), 'at'),
        'ps:',
        ('addiu', 'at', 'zero', 1),
        ('sw', 'at', 40, 'sp'),
        ('lh', 's1', vwf.GP_X, 'gp'),         # where the first line starts (tabs)
        ('lh', 's2', vwf.GP_Y, 'gp'),
        ('jal', 'expand'),
        ('sw', 's0', 20, 'sp'),
        ('addu', 's0', 'v0', 'zero'),
        'pl:',
        ('lbu', 'a0', 0, 's0'),
        ('lh', 't0', vwf.GP_Y, 'gp'),
        ('beqz', 'a0', 'done'),
        ('lh', 't1', GP_CELL_H, 'gp'),
        ('lh', 't2', GP_BUF_H, 'gp'),
        ('blez', 't0', 'go'),
        ('addu', 't0', 't0', 't1'),
        ('slt', 'at', 't2', 't0'),
        ('bnez', 'at', 'done'),
        ('nop',),
        'go:',
        ('addiu', 'at', 'zero', TAB),
        ('beq', 'a0', 'at', 'tab'),
        ('addiu', 'at', 'zero', 0x20),
        ('beq', 'a0', 'at', 'space'),
        ('nop',),
        'put:',
        ('jal', wrapper),
        ('nop',),
        ('lw', 't0', 36, 'sp'),               # squeeze (see above): letters only
        ('lbu', 't1', 0, 's0'),
        ('blez', 't0', 'next'),
        ('sltiu', 'at', 't1', 0x21),
        ('bnez', 'at', 'next'),
        ('lw', 't2', 40, 'sp'),
        ('lh', 't3', vwf.GP_X, 'gp'),
        ('xori', 't2', 't2', 1),
        ('beqz', 't2', 'next'),
        ('sw', 't2', 40, 'sp'),
        ('blez', 't3', 'next'),               # wrapped (cut): x back at the line start
        ('addiu', 't0', 't0', -1),
        ('sw', 't0', 36, 'sp'),
        ('addiu', 't3', 't3', -1),
        ('sh', 't3', vwf.GP_X, 'gp'),
        'next:',
        ('b', 'pl'),
        ('addiu', 's0', 's0', 1),
        'tab:',                               # x = max(x + space, line start + px)
        ('lbu', 't0', 1, 's0'),
        ('lbu', 't3', 2, 's0'),
        ('lh', 't1', vwf.GP_X, 'gp'),
        ('lh', 't2', vwf.GP_Y, 'gp'),
        ('addiu', 't0', 't0', -TAB_BASE),
        ('sll', 't0', 't0', 6),
        ('addiu', 't3', 't3', -TAB_BASE),
        ('or', 't0', 't0', 't3'),
        ('bne', 't2', 's2', 'tab0'),
        ('addiu', 's0', 's0', 2),
        ('addu', 't0', 't0', 's1'),
        'tab0:',
        ('addiu', 't1', 't1', SPACE_W),
        ('slt', 'at', 't1', 't0'),
        ('beqz', 'at', 'tabset'),
        ('nop',),
        ('addu', 't1', 't0', 'zero'),
        'tabset:',
        ('b', 'next'),
        ('sh', 't1', vwf.GP_X, 'gp'),
        'space:',                             # spaces before a digit: digit width
        ('lbu', 't1', -1, 's0'),              # (not after a letter or , - . : prose)
        ('nop',),
        ('ori', 't1', 't1', 0x20),
        ('addiu', 't0', 't1', -0x61),
        ('sltiu', 't0', 't0', 26),
        ('bnez', 't0', 'put'),
        ('addiu', 't1', 't1', -0x2c),
        ('sltiu', 't1', 't1', 3),
        ('bnez', 't1', 'put'),
        ('addu', 't0', 's0', 'zero'),
        'sp1:',
        ('addiu', 't0', 't0', 1),
        ('lbu', 't1', 0, 't0'),
        ('nop',),
        ('beq', 't1', 'at', 'sp1'),
        ('addiu', 't1', 't1', -0x30),
        ('sltiu', 't1', 't1', 10),
        ('beqz', 't1', 'put'),
        ('lh', 't3', vwf.GP_Y, 'gp'),
        ('nop',),
        ('sw', 't3', 32, 'sp'),
        ('jal', wrapper),
        ('nop',),
        ('lh', 't0', vwf.GP_Y, 'gp'),
        ('lw', 't1', 32, 'sp'),
        ('lh', 't2', vwf.GP_X, 'gp'),
        ('bne', 't0', 't1', 'next'),          # wrapped: no extra width at the line start
        ('addiu', 't2', 't2', FIG_EXTRA),
        ('b', 'next'),
        ('sh', 't2', vwf.GP_X, 'gp'),
        'done:',
        ('lw', 'ra', 16, 'sp'),
        ('lw', 's0', 20, 'sp'),
        ('lw', 's1', 24, 'sp'),
        ('lw', 's2', 28, 'sp'),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 48),

        'measure:',
        ('addiu', 'sp', 'sp', -24),
        ('sw', 'ra', 16, 'sp'),
        ('jal', 'expand'),
        ('nop',),
        ('lui', 't5', hi(vwf.WIDTHS)),
        ('addiu', 't5', 't5', lo(vwf.WIDTHS)),
        ('addu', 't4', 'zero', 'zero'),       # pixels of this line
        ('addu', 't6', 'zero', 'zero'),       # widest line so far
        'mloop:',
        ('lbu', 't0', 0, 'v0'),
        ('addiu', 'v0', 'v0', 1),
        ('beqz', 't0', 'mdone'),
        ('addiu', 'at', 'zero', 0x0A),
        ('beq', 't0', 'at', 'mnl'),
        ('addiu', 'at', 'zero', 0x1B),
        ('beq', 't0', 'at', 'mesc'),
        ('addiu', 'at', 'zero', TAB),
        ('beq', 't0', 'at', 'mtab'),
        ('addiu', 'at', 'zero', 0x20),
        ('beq', 't0', 'at', 'mspace'),
        ('sltiu', 'at', 't0', 0x20),
        ('bnez', 'at', 'mloop'),
        ('sltiu', 'at', 't0', 0x7F),
        ('beqz', 'at', 'mhigh'),
        ('addu', 't1', 't5', 't0'),
        ('lbu', 't1', -0x20, 't1'),
        ('b', 'mloop'),
        ('addu', 't4', 't4', 't1'),
        'mhigh:',
        ('addiu', 't1', 't0', -0xA0),
        ('sltiu', 'at', 't1', 0x40),
        ('bnez', 'at', 'mloop'),
        ('addiu', 't4', 't4', vwf.DEFAULT_W),
        ('addiu', 'v0', 'v0', 1),             # 2-byte character
        ('b', 'mloop'),
        ('addiu', 't4', 't4', 16 - vwf.DEFAULT_W),
        'mtab:',
        ('lbu', 't0', 0, 'v0'),
        ('lbu', 't1', 1, 'v0'),
        ('addiu', 'v0', 'v0', 2),
        ('addiu', 't4', 't4', SPACE_W),
        ('addiu', 't0', 't0', -TAB_BASE),
        ('sll', 't0', 't0', 6),
        ('addiu', 't1', 't1', -TAB_BASE),
        ('or', 't0', 't0', 't1'),
        ('slt', 'at', 't4', 't0'),
        ('beqz', 'at', 'mloop'),
        ('nop',),
        ('b', 'mloop'),
        ('addu', 't4', 't0', 'zero'),
        'mspace:',                            # (at was reused by the delay slot)
        ('lbu', 't0', -2, 'v0'),              # after a letter or , - . : plain (prose)
        ('addiu', 't4', 't4', SPACE_W),
        ('ori', 't0', 't0', 0x20),
        ('addiu', 't1', 't0', -0x61),
        ('sltiu', 't1', 't1', 26),
        ('bnez', 't1', 'mloop'),
        ('addiu', 't0', 't0', -0x2c),
        ('sltiu', 't0', 't0', 3),
        ('bnez', 't0', 'mloop'),
        ('addu', 't1', 'v0', 'zero'),
        ('addiu', 't2', 'zero', 0x20),
        'msp1:',
        ('lbu', 't0', 0, 't1'),
        ('addiu', 't1', 't1', 1),
        ('beq', 't0', 't2', 'msp1'),
        ('addiu', 't0', 't0', -0x30),
        ('sltiu', 't0', 't0', 10),
        ('beqz', 't0', 'mloop'),
        ('nop',),
        ('b', 'mloop'),
        ('addiu', 't4', 't4', FIG_EXTRA),
        'mesc:',
        ('lbu', 't0', 0, 'v0'),
        ('addiu', 'v0', 'v0', 1),
        ('beqz', 't0', 'mdone'),
        ('addiu', 'at', 'zero', 0x43),
        ('bne', 't0', 'at', 'mesc'),
        ('nop',),
        ('b', 'mloop'),
        ('nop',),
        'mnl:',
        ('slt', 'at', 't6', 't4'),
        ('beqz', 'at', 'mnl2'),
        ('nop',),
        ('addu', 't6', 't4', 'zero'),
        'mnl2:',
        ('b', 'mloop'),
        ('addu', 't4', 'zero', 'zero'),
        'mdone:',
        ('slt', 'at', 't4', 't6'),
        ('beqz', 'at', 'mdone2'),
        ('lw', 'ra', 16, 'sp'),
        ('addu', 't4', 't6', 'zero'),
        'mdone2:',
        ('addiu', 'v0', 't4', 7),
        ('srl', 'v0', 'v0', 3),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 24),

        # wrapm(a0 = string, a1 = dst, a2 = width px, a3 = dst size): dst = the expanded string
        # with a newline where print_all + the word-wrap wrapper + putc will break it at that
        # width (replaces the byte line-breaker LINEBRK that sizes boxes / pre-wraps text)
        'wrapm8:',                            # the line-breaker's arguments: a2 = characters (8 px)
        ('sll', 'a2', 'a2', 3),
        ('addiu', 'a3', 'zero', 256),         # its output never passed 256 characters
        'wrapm:',
        ('addiu', 'sp', 'sp', -32),
        ('sw', 'ra', 16, 'sp'),
        ('sw', 'a1', 20, 'sp'),
        ('sw', 'a2', 24, 'sp'),
        ('jal', 'expand'),
        ('sw', 'a3', 28, 'sp'),
        ('lw', 'a1', 20, 'sp'),
        ('lw', 't3', 24, 'sp'),
        ('lw', 't9', 28, 'sp'),
        ('lui', 't5', hi(vwf.WIDTHS)),
        ('addiu', 't5', 't5', lo(vwf.WIDTHS)),
        ('addu', 't9', 't9', 'a1'),
        ('addiu', 't9', 't9', -WRAPM_SLACK),  # room for a newline + 2-byte char / tab / ESC run + NUL
        ('addu', 't4', 'zero', 'zero'),       # x
        'wl:',
        ('lbu', 't0', 0, 'v0'),
        ('sltu', 'at', 'a1', 't9'),
        ('beqz', 't0', 'wdone'),
        ('nop',),
        ('beqz', 'at', 'wdone'),
        ('addiu', 'v0', 'v0', 1),
        ('addiu', 'at', 'zero', 0x0A),
        ('beq', 't0', 'at', 'wnl'),
        ('addiu', 'at', 'zero', 0x1B),
        ('beq', 't0', 'at', 'wesc'),
        ('addiu', 'at', 'zero', TAB),
        ('beq', 't0', 'at', 'wtab'),
        ('addiu', 'at', 'zero', 0x20),
        ('beq', 't0', 'at', 'wsp'),
        ('sltiu', 'at', 't0', 0x20),
        ('bnez', 'at', 'wput'),               # other control bytes: no width
        ('addu', 't1', 'zero', 'zero'),
        ('sltiu', 'at', 't0', 0x7F),
        ('beqz', 'at', 'whigh'),
        ('addu', 't1', 't5', 't0'),
        ('lbu', 't1', -0x20, 't1'),
        ('b', 'wchar'),
        ('nop',),
        'whigh:',
        ('addiu', 't1', 't0', -0xA0),
        ('sltiu', 'at', 't1', 0x40),
        ('bnez', 'at', 'wchar'),
        ('addiu', 't1', 'zero', vwf.DEFAULT_W),
        ('addiu', 't1', 'zero', 16),          # 2-byte character
        ('addu', 't2', 't4', 't1'),
        ('slt', 'at', 't3', 't2'),
        ('beqz', 'at', 'w2'),
        ('addiu', 't2', 'zero', 0x0A),
        ('sb', 't2', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('addu', 't4', 'zero', 'zero'),
        'w2:',
        ('lbu', 't2', 0, 'v0'),
        ('addiu', 'v0', 'v0', 1),
        ('sb', 't0', 0, 'a1'),
        ('sb', 't2', 1, 'a1'),
        ('addiu', 'a1', 'a1', 2),
        ('b', 'wl'),
        ('addu', 't4', 't4', 't1'),
        'wchar:',                             # putc wraps when x + width > wrap
        ('addu', 't2', 't4', 't1'),
        ('slt', 'at', 't3', 't2'),
        ('beqz', 'at', 'wput'),
        ('addiu', 't2', 'zero', 0x0A),
        ('sb', 't2', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('addu', 't4', 'zero', 'zero'),
        'wput:',
        ('sb', 't0', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('b', 'wl'),
        ('addu', 't4', 't4', 't1'),
        'wnl:',
        ('sb', 't0', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('b', 'wl'),
        ('addu', 't4', 'zero', 'zero'),
        'wesc:',                              # ESC ... 'C' copied, no width
        ('sb', 't0', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        'we1:',
        ('lbu', 't0', 0, 'v0'),
        ('sltu', 'at', 'a1', 't9'),
        ('beqz', 't0', 'wl'),
        ('nop',),
        ('beqz', 'at', 'wdone'),
        ('addiu', 'v0', 'v0', 1),
        ('sb', 't0', 0, 'a1'),
        ('addiu', 'at', 'zero', 0x43),
        ('bne', 't0', 'at', 'we1'),
        ('addiu', 'a1', 'a1', 1),
        ('b', 'wl'),
        ('nop',),
        'wtab:',                              # x = max(x + space, px) (the box starts at x 0)
        ('lbu', 't1', 0, 'v0'),
        ('lbu', 't2', 1, 'v0'),
        ('addiu', 'v0', 'v0', 2),
        ('sb', 't0', 0, 'a1'),
        ('sb', 't1', 1, 'a1'),
        ('sb', 't2', 2, 'a1'),
        ('addiu', 'a1', 'a1', 3),
        ('addiu', 't1', 't1', -TAB_BASE),
        ('sll', 't1', 't1', 6),
        ('addiu', 't2', 't2', -TAB_BASE),
        ('or', 't1', 't1', 't2'),
        ('addiu', 't4', 't4', SPACE_W),
        ('slt', 'at', 't4', 't1'),
        ('beqz', 'at', 'wl'),
        ('nop',),
        ('b', 'wl'),
        ('addu', 't4', 't1', 'zero'),
        'wsp:',                               # the wrapper: x + space + next word > wrap -> newline
        ('addu', 't6', 'v0', 'zero'),
        ('addu', 't7', 'zero', 'zero'),
        'ws1:',
        ('lbu', 't2', 0, 't6'),
        ('addiu', 't6', 't6', 1),
        ('addiu', 'at', 'zero', 0x1B),
        ('beq', 't2', 'at', 'wsesc'),
        ('sltiu', 'at', 't2', 0x21),
        ('bnez', 'at', 'wsend'),
        ('sltiu', 'at', 't2', 0x7F),
        ('beqz', 'at', 'wshigh'),
        ('addu', 't1', 't5', 't2'),
        ('lbu', 't1', -0x20, 't1'),
        ('b', 'ws1'),
        ('addu', 't7', 't7', 't1'),
        'wshigh:',
        ('addiu', 't1', 't2', -0xA0),
        ('sltiu', 'at', 't1', 0x40),
        ('beqz', 'at', 'wslead'),
        ('nop',),
        ('b', 'ws1'),
        ('addiu', 't7', 't7', vwf.DEFAULT_W),
        'wslead:',
        ('addiu', 't6', 't6', 1),
        ('b', 'ws1'),
        ('addiu', 't7', 't7', 16),
        'wsesc:',
        ('lbu', 't2', 0, 't6'),
        ('addiu', 't6', 't6', 1),
        ('beqz', 't2', 'wsend'),
        ('addiu', 'at', 'zero', 0x43),
        ('bne', 't2', 'at', 'wsesc'),
        ('nop',),
        ('b', 'ws1'),
        ('nop',),
        'wsend:',
        ('lbu', 't1', 0, 't5'),               # width of ' '
        ('nop',),
        ('addu', 't2', 't4', 't1'),
        ('addu', 't2', 't2', 't7'),
        ('slt', 'at', 't3', 't2'),
        ('beqz', 'at', 'wsp2'),
        ('addiu', 't2', 'zero', 0x0A),
        ('sb', 't2', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('b', 'wl'),
        ('addu', 't4', 'zero', 'zero'),
        'wsp2:',                              # print_all: a space before a digit is digit-wide
        ('sb', 't0', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('addu', 't4', 't4', 't1'),
        ('lbu', 't2', -2, 'v0'),              # after a letter or , - . : plain (prose)
        ('nop',),
        ('ori', 't2', 't2', 0x20),
        ('addiu', 't7', 't2', -0x61),
        ('sltiu', 't7', 't7', 26),
        ('bnez', 't7', 'wl'),
        ('addiu', 't2', 't2', -0x2c),
        ('sltiu', 't2', 't2', 3),
        ('bnez', 't2', 'wl'),
        ('nop',),
        ('addu', 't6', 'v0', 'zero'),
        ('addiu', 't7', 'zero', 0x20),
        'wsp3:',
        ('lbu', 't2', 0, 't6'),
        ('addiu', 't6', 't6', 1),
        ('beq', 't2', 't7', 'wsp3'),
        ('addiu', 't2', 't2', -0x30),
        ('sltiu', 't2', 't2', 10),
        ('beqz', 't2', 'wl'),
        ('nop',),
        ('b', 'wl'),
        ('addiu', 't4', 't4', FIG_EXTRA),
        'wdone:',
        ('lw', 'ra', 16, 'sp'),
        ('sb', 'zero', 0, 'a1'),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 32),

        'expand:',
        ('addiu', 'sp', 'sp', -48),
        ('sw', 'ra', 16, 'sp'),
        ('sw', 's0', 20, 'sp'),
        ('sw', 's1', 24, 'sp'),
        ('sw', 's2', 28, 'sp'),
        ('sw', 's3', 32, 'sp'),
        ('sw', 's4', 36, 'sp'),
        ('addu', 's0', 'a0', 'zero'),
        ('addu', 's3', 'a0', 'zero'),         # start of the source line
        ('addu', 's4', 'zero', 'zero'),       # 1: ESC on this source line (no columns)
        ('lui', 't9', hi(layout)),
        ('sw', 'zero', lo(layout) + L_CI, 't9'),
        ('sw', 'zero', lo(layout) + L_P, 't9'),
        ('lui', 's1', hi(buf)),
        ('addiu', 's1', 's1', lo(buf)),
        ('addiu', 's2', 's1', CAP - 4),      # room for a tab (3 bytes) + NUL
        'exp:',
        ('lbu', 't0', 0, 's0'),
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 't0', 'expdone'),
        ('nop',),
        ('beqz', 'at', 'expdone'),
        ('addiu', 't1', 't0', -0x81),
        ('sltiu', 'at', 't1', 0x1F),
        ('bnez', 'at', 'lead'),
        ('addiu', 't1', 't0', -0xE0),
        ('sltiu', 'at', 't1', 0x1D),
        ('bnez', 'at', 'lead'),
        ('addiu', 'at', 'zero', 0x0A),
        ('bne', 't0', 'at', 'notnl'),
        ('addiu', 'at', 'zero', 0x1B),
        ('jal', 'cellend'),
        ('nop',),
        ('lui', 't9', hi(layout)),
        ('sw', 'zero', lo(layout) + L_CI, 't9'),
        ('addiu', 't0', 'zero', 0x0A),
        ('addiu', 's3', 's0', 1),
        ('b', 'single'),
        ('addu', 's4', 'zero', 'zero'),
        'notnl:',
        ('bne', 't0', 'at', 'notesc'),
        ('addiu', 'at', 'zero', 0x20),
        ('b', 'single'),
        ('addiu', 's4', 'zero', 1),
        'notesc:',
        ('beq', 't0', 'at', 'isspace'),
        ('addiu', 'at', 'zero', CELL),
        ('beq', 't0', 'at', 'iscell'),
        ('addiu', 'at', 'zero', CELL_REF),
        ('bne', 't0', 'at', 'plain'),
        'iscell:',
        ('sw', 't0', 40, 'sp'),               # the marker
        ('jal', 'cellend'),                   # table cell (tools/cells.py)
        ('nop',),
        ('lui', 't9', hi(layout)),
        ('addiu', 't9', 't9', lo(layout)),
        ('lbu', 't1', 0, 't9'),
        ('lw', 't2', L_CI, 't9'),
        ('nop',),
        ('sltu', 'at', 't2', 't1'),
        ('beqz', 'at', 'mskip'),
        ('addiu', 't2', 't2', 1),
        ('sw', 't2', L_CI, 't9'),
        ('sw', 's1', L_P, 't9'),
        ('addiu', 't0', 'zero', TAB),
        ('sb', 't0', 0, 's1'),
        ('addiu', 't0', 'zero', TAB_BASE),    # filled in by cellend
        ('sb', 't0', 1, 's1'),
        ('sb', 't0', 2, 's1'),
        ('addiu', 's1', 's1', 3),
        ('addiu', 's4', 'zero', 1),           # plain spaces inside cells
        'mskip:',
        ('lw', 't0', 40, 'sp'),
        ('addiu', 'at', 'zero', CELL_REF),
        ('bne', 't0', 'at', 'exp'),
        ('addiu', 's0', 's0', 1),
        ('lbu', 't1', 0, 's0'),               # CELL_REF: copy the string at the address
        ('lbu', 't2', 1, 's0'),
        ('lbu', 't3', 2, 's0'),
        ('andi', 't1', 't1', 0x7F),
        ('andi', 't2', 't2', 0x7F),
        ('andi', 't3', 't3', 0x7F),
        ('sll', 't1', 't1', 14),
        ('sll', 't2', 't2', 7),
        ('or', 't1', 't1', 't2'),
        ('or', 't1', 't1', 't3'),
        ('lui', 't2', 0x8000),
        ('or', 't1', 't1', 't2'),
        ('addiu', 's0', 's0', 3),
        'rcopy:',
        ('lbu', 't0', 0, 't1'),
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 't0', 'exp'),
        ('addiu', 't1', 't1', 1),
        ('beqz', 'at', 'exp'),
        ('nop',),
        ('sb', 't0', 0, 's1'),
        ('b', 'rcopy'),
        ('addiu', 's1', 's1', 1),
        'isspace:',
        ('b', 'spaces'),
        ('addu', 't8', 'zero', 'zero'),       # not after a name
        'plain:',                             # upper case after lower case: a space ("BbbbBbbb",
        ('addiu', 't1', 't0', -0x41),         # the game joins a custom officer's names)
        ('sltiu', 'at', 't1', 26),
        ('beqz', 'at', 'single'),
        ('lui', 't9', hi(buf + 2)),
        ('addiu', 't9', 't9', lo(buf + 2)),
        ('sltu', 'at', 's1', 't9'),
        ('bnez', 'at', 'single'),
        ('nop',),
        ('lbu', 't1', -1, 's1'),
        ('lbu', 't2', -2, 's1'),
        ('addiu', 't1', 't1', -0x61),
        ('sltiu', 'at', 't1', 26),
        ('beqz', 'at', 'single'),
        ('addiu', 'at', 'zero', 0x1B),
        ('beq', 't2', 'at', 'single'),
        ('addiu', 't1', 'zero', 0x20),
        ('sb', 't1', 0, 's1'),
        ('b', 'single'),
        ('addiu', 's1', 's1', 1),
        'single1:',
        ('addiu', 't0', 'zero', 0x20),
        'single:',
        ('sb', 't0', 0, 's1'),
        ('addiu', 's0', 's0', 1),
        ('b', 'exp'),
        ('addiu', 's1', 's1', 1),
        'lead:',
        ('addu', 'a0', 's0', 'zero'),
        ('jal', 'lookup'),
        ('addiu', 'a1', 'zero', F_ANY),
        ('bnez', 'v0', 'hit'),
        ('nop',),
        ('bne', 's0', 's3', 'raw2'),          # a whole line (one-kanji names): any flag
        ('lbu', 't0', 2, 's0'),
        ('addiu', 'at', 'zero', 0x0A),
        ('beqz', 't0', 'whole'),
        ('nop',),
        ('bne', 't0', 'at', 'raw2'),
        ('nop',),
        'whole:',
        ('addu', 'a0', 's0', 'zero'),
        ('jal', 'lookup'),
        ('addiu', 'a1', 'zero', F_ALL),
        ('beqz', 'v0', 'raw2'),
        ('nop',),
        'hit:',
        ('addu', 's0', 's0', 'v1'),
        ('addu', 'a3', 't7', 'zero'),        # header of the hit
        'copy:',
        ('lbu', 't0', 0, 'v0'),
        ('addiu', 'v0', 'v0', 1),
        ('beqz', 't0', 'copied'),
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 'at', 'expdone'),
        ('nop',),
        ('sb', 't0', 0, 's1'),
        ('b', 'copy'),
        ('addiu', 's1', 's1', 1),
        'copied:',
        ('andi', 'at', 'a3', F_S),
        ('beqz', 'at', 'after'),
        ('addu', 'a0', 's0', 'zero'),
        ('jal', 'lookup'),
        ('addiu', 'a1', 'zero', F_G),
        ('beqz', 'v0', 'after'),
        ('addu', 'a3', 'zero', 'zero'),
        ('addu', 's0', 's0', 'v1'),
        ('addiu', 't0', 'zero', 0x20),
        ('sb', 't0', 0, 's1'),
        ('b', 'copy'),
        ('addiu', 's1', 's1', 1),
        'after:',                             # a name ends here
        ('lbu', 't0', 0, 's0'),
        ('addiu', 'at', 'zero', 0x20),
        ('bne', 't0', 'at', 'exp'),
        ('nop',),
        ('addiu', 't8', 'zero', 1),
        'spaces:',                            # s0 at a space (t8 = 1: right after a name)
        ('bnez', 's4', 'single1'),            # ESC on this line: columns unknown
        ('addu', 't1', 's0', 'zero'),
        'sp:',
        ('addiu', 't1', 't1', 1),
        ('lbu', 't0', 0, 't1'),
        ('addiu', 'at', 'zero', 0x20),
        ('beq', 't0', 'at', 'sp'),
        ('subu', 't2', 't1', 's0'),           # spaces in the run
        ('bne', 's0', 's3', 'notlead'),       # at the line start, before a name: one column
        ('addiu', 'at', 't0', -0x81),
        ('sltiu', 'at', 'at', 0x1F),
        ('bnez', 'at', 'lsp'),
        ('addiu', 'at', 't0', -0xE0),
        ('sltiu', 'at', 'at', 0x1D),
        ('beqz', 'at', 'notlead'),
        ('nop',),
        'lsp:',
        ('addu', 'a3', 't1', 'zero'),
        ('addu', 'a0', 't1', 'zero'),
        ('jal', 'lookup'),
        ('addiu', 'a1', 'zero', F_ANY),
        ('addu', 't1', 'a3', 'zero'),
        ('beqz', 'v0', 'spre'),
        ('addu', 't8', 'zero', 'zero'),
        ('b', 'emit'),
        ('addiu', 't6', 'zero', 8),
        'spre:',                              # no name: back to the run
        ('lbu', 't0', 0, 't1'),
        ('subu', 't2', 't1', 's0'),
        'notlead:',
        ('addiu', 't3', 't0', -0x30),
        ('sltiu', 't3', 't3', 10),
        ('beqz', 't3', 'word'),
        ('addu', 't4', 't1', 'zero'),
        'dg:',                                # digits follow: t4 = their end, t5 = count
        ('addiu', 't4', 't4', 1),
        ('lbu', 't0', 0, 't4'),
        ('nop',),
        ('addiu', 't0', 't0', -0x30),
        ('sltiu', 't0', 't0', 10),
        ('bnez', 't0', 'dg'),
        ('subu', 't5', 't4', 't1'),
        ('sltiu', 'at', 't2', 2),
        ('beqz', 'at', 'rtab'),
        ('nop',),
        ('beqz', 't8', 'single1'),            # one space: digit-wide at draw time
        ('nop',),
        'rtab:',                              # right-align the number on its Japanese end column
        ('subu', 't6', 't4', 's3'),
        ('sll', 't6', 't6', 3),
        ('sll', 't7', 't5', 1),
        ('subu', 't6', 't6', 't7'),
        ('sll', 't7', 't5', 2),
        ('b', 'emit'),
        ('subu', 't6', 't6', 't7'),
        'word:',
        ('sltiu', 'at', 't2', 2),
        ('beqz', 'at', 'ltab'),
        ('nop',),
        ('beqz', 't8', 'single1'),
        ('sltiu', 'at', 't0', 0x80),          # name + one space: a column only before Japanese
        ('bnez', 'at', 'single1'),
        ('nop',),
        'ltab:',                              # left-align on the Japanese column after the spaces
        ('subu', 't6', 't1', 's3'),
        ('sll', 't6', 't6', 3),
        'emit:',                              # TAB, 0x20 + px >> 6, 0x20 + (px & 63)
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 'at', 'single1'),
        ('srl', 't7', 't6', 6),
        ('addiu', 't7', 't7', TAB_BASE),
        ('andi', 't6', 't6', 63),
        ('addiu', 't6', 't6', TAB_BASE),
        ('addiu', 't0', 'zero', TAB),
        ('sb', 't0', 0, 's1'),
        ('sb', 't7', 1, 's1'),
        ('sb', 't6', 2, 's1'),
        ('addiu', 's1', 's1', 3),
        ('b', 'exp'),
        ('addu', 's0', 't1', 'zero'),
        'raw2:',
        ('lbu', 't0', 0, 's0'),
        ('lbu', 't1', 1, 's0'),
        ('sb', 't0', 0, 's1'),
        ('beqz', 't1', 'expdone'),
        ('addiu', 's1', 's1', 1),
        ('sb', 't1', 0, 's1'),
        ('addiu', 's1', 's1', 1),
        ('b', 'exp'),
        ('addiu', 's0', 's0', 2),
        'expdone:',
        ('jal', 'cellend'),
        ('nop',),
        ('sb', 'zero', 0, 's1'),
        ('lui', 'v0', hi(buf)),
        ('addiu', 'v0', 'v0', lo(buf)),
        ('lw', 'ra', 16, 'sp'),
        ('lw', 's0', 20, 'sp'),
        ('lw', 's1', 24, 'sp'),
        ('lw', 's2', 28, 'sp'),
        ('lw', 's3', 32, 'sp'),
        ('lw', 's4', 36, 'sp'),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 48),

        'cellend:',                           # align the open cell: fill its TAB
        ('lui', 't9', hi(layout)),
        ('addiu', 't9', 't9', lo(layout)),
        ('lw', 't0', L_P, 't9'),
        ('lw', 't1', L_CI, 't9'),
        ('nop',),
        ('beqz', 't0', 'ceret'),
        ('addiu', 't1', 't1', -1),
        ('sw', 'zero', L_P, 't9'),
        ('sll', 't1', 't1', 2),
        ('addu', 't1', 't9', 't1'),
        ('lhu', 't2', L_COLS, 't1'),          # column px
        ('lbu', 't3', L_COLS + 2, 't1'),      # width
        ('lbu', 't4', L_COLS + 3, 't1'),      # flags
        ('addiu', 't5', 't0', 3),
        ('addu', 't6', 'zero', 'zero'),       # px of the cell
        ('lui', 't7', hi(vwf.WIDTHS)),
        ('addiu', 't7', 't7', lo(vwf.WIDTHS)),
        'cm:',
        ('sltu', 'at', 't5', 's1'),
        ('beqz', 'at', 'cmdone'),
        ('nop',),
        ('lbu', 'v1', 0, 't5'),
        ('addiu', 't5', 't5', 1),
        ('addiu', 'at', 'zero', 0x20),
        ('bne', 'v1', 'at', 'cm1'),
        ('addu', 'a0', 't5', 'zero'),
        'cms:',                               # a space: digit-wide before digits
        ('sltu', 'at', 'a0', 's1'),
        ('beqz', 'at', 'cmsp'),
        ('nop',),
        ('lbu', 'a1', 0, 'a0'),
        ('addiu', 'a0', 'a0', 1),
        ('addiu', 'a2', 'zero', 0x20),
        ('beq', 'a1', 'a2', 'cms'),
        ('addiu', 'a1', 'a1', -0x30),
        ('sltiu', 'a1', 'a1', 10),
        ('beqz', 'a1', 'cmsp'),
        ('nop',),
        ('addiu', 't6', 't6', FIG_EXTRA),
        'cmsp:',
        ('b', 'cm'),
        ('addiu', 't6', 't6', SPACE_W),
        'cm1:',
        ('sltiu', 'at', 'v1', 0x20),
        ('bnez', 'at', 'cm'),
        ('sltiu', 'at', 'v1', 0x7F),
        ('beqz', 'at', 'cm2'),
        ('addu', 'a0', 't7', 'v1'),
        ('lbu', 'a0', -0x20, 'a0'),
        ('b', 'cm'),
        ('addu', 't6', 't6', 'a0'),
        'cm2:',
        ('addiu', 'a0', 'v1', -0xA0),
        ('sltiu', 'a0', 'a0', 0x40),
        ('beqz', 'a0', 'cm3'),
        ('nop',),
        ('b', 'cm'),
        ('addiu', 't6', 't6', vwf.DEFAULT_W),
        'cm3:',
        ('addiu', 't5', 't5', 1),
        ('b', 'cm'),
        ('addiu', 't6', 't6', 16),
        'cmdone:',
        ('andi', 'v1', 't4', 3),
        ('subu', 'a0', 't3', 't6'),           # free px
        ('addiu', 'at', 'zero', 1),
        ('bne', 'v1', 'at', 'c2'),
        ('addu', 'a1', 'zero', 'zero'),
        ('b', 'coff'),
        ('addu', 'a1', 'a0', 'zero'),
        'c2:',
        ('addiu', 'at', 'zero', 2),
        ('bne', 'v1', 'at', 'c3'),
        ('nop',),
        ('b', 'coff'),
        ('sra', 'a1', 'a0', 1),
        'c3:',
        ('addiu', 'at', 'zero', 3),
        ('bne', 'v1', 'at', 'coff'),
        ('andi', 'a1', 't4', 0x30),
        ('srl', 'a1', 'a1', 1),               # trailing spaces x 8
        ('subu', 'a1', 'a0', 'a1'),
        'coff:',
        ('slti', 'at', 'a1', 0),
        ('bnez', 'at', 'cpos'),
        ('nop',),
        ('addu', 't2', 't2', 'a1'),
        'cpos:',
        ('srl', 'a2', 't2', 6),
        ('addiu', 'a2', 'a2', TAB_BASE),
        ('sb', 'a2', 1, 't0'),
        ('andi', 'a2', 't2', 63),
        ('addiu', 'a2', 'a2', TAB_BASE),
        ('sb', 'a2', 2, 't0'),
        'ceret:',
        ('jr', 'ra'),
        ('nop',),

        'lookup:',
        ('lbu', 't0', 0, 'a0'),
        ('lbu', 't1', 1, 'a0'),
        ('sll', 't0', 't0', 8),
        ('or', 't0', 't0', 't1'),
        ('lui', 't2', hi(idx)),
        ('addiu', 't2', 't2', lo(idx)),
        ('lw', 't3', 0, 't2'),                # hi = n
        ('addiu', 't2', 't2', 4),
        ('addu', 't4', 'zero', 'zero'),       # lo
        'bs:',
        ('sltu', 'at', 't4', 't3'),
        ('beqz', 'at', 'none'),
        ('addu', 't5', 't4', 't3'),
        ('srl', 't5', 't5', 1),
        ('sll', 't6', 't5', 2),
        ('addu', 't6', 't2', 't6'),
        ('lhu', 't7', 0, 't6'),
        ('nop',),
        ('beq', 't7', 't0', 'found'),
        ('sltu', 'at', 't7', 't0'),
        ('beqz', 'at', 'lower'),
        ('nop',),
        ('b', 'bs'),
        ('addiu', 't4', 't5', 1),
        'lower:',
        ('b', 'bs'),
        ('addu', 't3', 't5', 'zero'),
        'found:',
        ('lhu', 't6', 2, 't6'),
        ('lui', 't7', hi(blob)),
        ('addiu', 't7', 't7', lo(blob)),
        ('addu', 't6', 't7', 't6'),
        'ent:',
        ('lbu', 't7', 0, 't6'),
        ('nop',),
        ('beqz', 't7', 'none'),
        ('addiu', 't6', 't6', 1),
        ('andi', 't8', 't7', 0x1F),
        ('addu', 't9', 'zero', 'zero'),
        'cmp:',
        ('beq', 't9', 't8', 'match'),
        ('addu', 'at', 'a0', 't9'),
        ('lbu', 'v0', 2, 'at'),
        ('addu', 'at', 't6', 't9'),
        ('lbu', 'v1', 0, 'at'),
        ('addiu', 't9', 't9', 1),
        ('bne', 'v0', 'v1', 'skip'),
        ('nop',),
        ('b', 'cmp'),
        ('nop',),
        'match:',
        ('and', 'at', 't7', 'a1'),
        ('beqz', 'at', 'skip'),
        ('nop',),
        ('addu', 'v0', 't6', 't8'),
        ('jr', 'ra'),
        ('addiu', 'v1', 't8', 2),
        'skip:',
        ('addu', 't6', 't6', 't8'),
        'sk2:',
        ('lbu', 'at', 0, 't6'),
        ('addiu', 't6', 't6', 1),
        ('bnez', 'at', 'sk2'),
        ('nop',),
        ('b', 'ent'),
        ('nop',),
        'none:',
        ('jr', 'ra'),
        ('addu', 'v0', 'zero', 'zero'),
    ]



def apply(exe, heap, d, wrapper, layout):
    """Put the dictionary + print_all into the heap and hook print_string's loop.
    layout: the LAYOUT block (LAYOUT_SIZE bytes, shared with tools/cells.py).
    Returns the labels of the code (measure, wrapm, expand for later hooks)."""
    assert vwf.word(exe, PRINT_CALL) == 0x10000005, 'print_string loop entry'
    assert vwf.word(exe, PRINT_EXIT) == 0x24020001, 'print_string loop exit'
    head, blob = pack(d, 0)
    idx = heap.put(head)
    blob_at = heap.put(blob)
    buf = heap.alloc(CAP)
    lst = lines(idx, blob_at, buf, wrapper, layout)
    at = heap.alloc(len(asm.assemble(0x80000000, lst)))
    vwf.put(exe, at, asm.assemble(at, lst))
    vwf.put(exe, PRINT_CALL, asm.assemble(PRINT_CALL, [('jal', at), ('addu', 'a0', 's0', 'zero'),
                                                       ('b', PRINT_EXIT), ('nop',)]))
    return asm.labels(at, lst)


STRLEN = 0x80017a24
MAXLINE = 0x80036f14                     # longest line in bytes (newlines)
LINEBRK = 0x80039374                     # byte line-breaker (src, dst, characters per line)
# Its one caller, SALO 0x8017da48 (save/load message box, S6's dialogue window sizer 0x800bcda4):
# line-breaks the text at width/8 characters into sp+0x3c (to the end of the 0x240 frame), then sizes
# the box from it (longest line x 8: MEASURE_SITES). -> wrapm at the pixel width, names expanded.
LINEBRK_SITE = ('SALO', 0x8017daa0, [0x04c10003, 0x0006c8c3, 0x24c20007, 0x0002c8c3,
                                     0x00e02021, 0x27a5003c, 0x0c00e4dd, 0x03203021])
LINEBRK_BUF = 0x240 - 0x3c
# jal strlen / longest-line whose result x 8 sizes or places a box: -> measure (8-px units of the
# widest English line, names expanded)
MEASURE_SITES = [
    ('PROG', 0x80036dec),    # text object aligner: x = (width - maxline * 8) / 1 or 2 (right / centred)
    ('PROG', 0x8008bab4),    # window width = maxline * 8 (+ lines * 16 high)
    ('RT1', 0x80159f38),     # centred in 0x68 px: (0x68 - len * 8) / 2
    # RT2 0x8016ceb8 (event narration caption: box width = maxline * 8, centred on the screen) -> hook_caption
    ('SALO', 0x8017dac0),    # box width = maxline * 8 of the line-broken text
    ('SALO', 0x8017daf0),    # width = len * 8
    ('TL', 0x8017a920),      # centred in 0x68 px, as RT1
    ('TL', 0x80185cf0),      # window width = maxline * 8, as PROG 0x8008bab4
]
# not hooked: PROG 0x80034d60 / TL 0x8018ccf8 (strlen vs width / 8: padding in characters, check what
# it pads with), PROG 0x8008b69c / TL 0x8018e2ec (result unused), CMD 0x801bb138 (== 4 test),
# PROG 0x80081784 (len < 9), CMD2 name entry (byte counts)


def hook_linebrk(files, wrapm):
    """Replace the byte line-breaker call (LINEBRK_SITE) by wrapm(text, sp+0x3c, width px, LINEBRK_BUF)."""
    import progstr
    tag, va, words = LINEBRK_SITE
    img = files[tag]
    off = va - progstr.ovl_base(img)
    assert list(struct.unpack_from('<8I', img, off)) == words, 'line-breaker call at %s:%08x' % (tag, va)
    img[off:off + 32] = asm.assemble(va, [('addu', 'a0', 'a3', 'zero'), ('addiu', 'a1', 'sp', 0x3c),
                                          ('jal', wrapm), ('addiu', 'a3', 'zero', LINEBRK_BUF),
                                          ('nop',), ('nop',), ('nop',), ('nop',)])


# Message window sizer PROG 0x8008ba94 (obj = s2, text pointer obj+0x298): width = longest line
# x 8 (a MEASURE_SITE), height = line count (0x80036f78), centred on the screen. The Japanese
# breaks its lines by hand; English with no newline gave a box as wide as the whole sentence,
# off both screen edges, while the text wrapped at 256 px inside it. The sizer's first call goes
# through a stub: wrapm the text at NOTICE_W into one of two alternating buffers (two windows
# may be open at once), point obj+0x298 at it, then measure it: box and line count fit the text.
NOTICE_SITE = 0x8008bab0             # lw a0, 0x298(s2) / jal longest-line (-> measure)
NOTICE_W, NOTICE_BUF = 256, 512


def hook_notice(exe, heap, measure, wrapm):
    """Wrap message-window text before sizing; returns its RAM size."""
    off = NOTICE_SITE - vwf.EXE_BASE
    assert exe[off:off + 8] == asm.assemble(NOTICE_SITE, [('lw', 'a0', 0x298, 's2'), ('jal', measure)]), \
        'message window sizer'
    bufs = heap.alloc(2 * NOTICE_BUF + 4)
    flip = bufs + 2 * NOTICE_BUF
    code = lambda at: asm.assemble(at, [
        ('addiu', 'sp', 'sp', -24),
        ('sw', 'ra', 16, 'sp'),
        ('lui', 't0', asm.hi(flip)),
        ('lw', 't1', asm.lo(flip), 't0'),
        ('lw', 'a0', 0x298, 's2'),
        ('xori', 't1', 't1', NOTICE_BUF),
        ('sw', 't1', asm.lo(flip), 't0'),
        ('lui', 'a1', asm.hi(bufs)),
        ('addiu', 'a1', 'a1', asm.lo(bufs)),
        ('addu', 'a1', 'a1', 't1'),
        ('sw', 'a1', 0x298, 's2'),
        ('addiu', 'a2', 'zero', NOTICE_W),
        ('jal', wrapm),
        ('addiu', 'a3', 'zero', NOTICE_BUF),
        ('jal', measure),
        ('lw', 'a0', 0x298, 's2'),
        ('lw', 'ra', 16, 'sp'),
        ('nop',),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 24)])
    n = len(code(0x80000000))
    at = heap.alloc(n)
    vwf.put(exe, at, code(at))
    vwf.put(exe, NOTICE_SITE, asm.assemble(NOTICE_SITE, [('jal', at), ('nop',)]))
    return n + 2 * NOTICE_BUF + 4


# Event narration caption (RT2 0x8016ce30: a1 = text; box = longest line x 8 by lines x 16,
# centred on the screen, dark band). The Japanese breaks its 2-3 lines by hand; an unbroken
# English sentence measured 500 px and the box started off the left screen edge. Stub at the
# measure call: wrapm the text at CAPTION_W into CAPTION buffer, s2 = buffer (the function
# only measures, counts lines and prints s2; the caller owns the original), then measure.
CAPTION_SITE = ('RT2', 0x8016ceb8)   # jal longest-line / move a0, s2
CAPTION_W, CAPTION_BUF = 256, 512   # the caption text re-wraps at 256 px when drawn (as message windows)


def hook_caption(files, heap, measure, wrapm):
    """Wrap event narration captions before sizing; returns the RAM used."""
    import progstr
    tag, site = CAPTION_SITE
    img = files[tag]
    off = site - progstr.ovl_base(img)
    assert img[off:off + 8] == asm.assemble(site, [('jal', MAXLINE), ('addu', 'a0', 's2', 'zero')]), \
        'caption sizer at %s:%08x' % (tag, site)
    buf = heap.alloc(CAPTION_BUF)
    code = lambda at: asm.assemble(at, [
        ('addiu', 'sp', 'sp', -24),
        ('sw', 'ra', 16, 'sp'),
        ('lui', 'a1', asm.hi(buf)),
        ('addiu', 'a1', 'a1', asm.lo(buf)),
        ('addiu', 'a2', 'zero', CAPTION_W),
        ('jal', wrapm),
        ('addiu', 'a3', 'zero', CAPTION_BUF),
        ('lui', 's2', asm.hi(buf)),
        ('addiu', 's2', 's2', asm.lo(buf)),
        ('jal', measure),
        ('addu', 'a0', 's2', 'zero'),
        ('lw', 'ra', 16, 'sp'),
        ('addiu', 'v0', 'v0', 1),            # + 8 px: a line as wide as the box re-wraps its last word
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 24)])
    n = len(code(0x80000000))
    at = heap.alloc(n)
    vwf.put(files['PROG'], at, code(at))
    img[off:off + 4] = asm.assemble(site, [('jal', at)])
    return n + CAPTION_BUF


def hook_measure(files, measure):
    """Point the MEASURE_SITES at measure; files = {'PROG': exe, overlay tag: file}."""
    import progstr
    for tag, site in MEASURE_SITES:
        img = files[tag]
        off = site - vwf.EXE_BASE if tag == 'PROG' else site - progstr.ovl_base(img)
        assert img[off:off + 4] in [struct.pack('<I', 0x0c000000 | (f >> 2) & 0x3FFFFFF) for f in (STRLEN, MAXLINE)], \
            'strlen / longest-line call at %s:%08x' % (tag, site)
        img[off:off + 4] = asm.assemble(site, [('jal', measure)])
