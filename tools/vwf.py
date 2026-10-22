"""Proportional Latin font in the kanji terminal (notes/engine.md "Text renderer").

The terminal draws single-byte characters from a 128-entry half-width set (u16 codes
HALF_CODES, sorted for a binary search; 8x16 1bpp glyphs at HALF_GLYPHS) and advances
by half a cell (8 px). This patch
  - replaces the set with ' '..'~' from tools/pixfont.py (the Japanese set has no lower
    case) plus the first KEEP_KANA half-width kana codes (their own glyphs), 112 entries:
    the count passed to the font init (FONT_INIT_COUNT) becomes 112;
  - stores the advance widths in the 16 glyph slots left over (WIDTHS);
  - rewrites the single-byte path of putc (PUTC_SINGLE .. PUTC_END): auto-wrap test and
    advance use the width of the character instead of 8;
  - fixes the 4bpp blit for odd x (fix_blit): it picked the nibble from the glyph column
    alone, so glyphs at odd x (never used by the Japanese, every advance was even) broke up;
  - word wrap (wrap_hook): every string is drawn by print_string (PRINT_LOOP calls putc
    for each byte, s0 = the byte's address). Its putc call goes through a wrapper placed in
    free RAM: at a space it measures the next word (up to a byte <= 0x20; ESC..'C' colour
    codes skipped; 2-byte Shift-JIS = cell advance, other single bytes = their width) and
    sends a newline instead of the space when the word would cross the wrap width (the
    wrapper's own limit, else the terminal's GP_WRAP).
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm, pixfont

EXE_BASE = 0x80010000 - 0x800
HALF_CODES = 0x800c2aec
HALF_GLYPHS = 0x800c22ec
HALF_N = 128
FONT_INIT_COUNT = 0x800aeab0          # addiu v0, zero, 0x80 (half-width count, init stack arg)
ASCII = list(range(0x20, 0x7F))       # 95 codes
KEEP_KANA = 112 - len(ASCII)          # first 17 of 0xA0..0xDF
NEW_N = 112
WIDTHS = HALF_GLYPHS + 16 * NEW_N     # u8 width of 0x20..0x7E, then the saved width
SAVED = WIDTHS + len(ASCII)
DEFAULT_W = 8                         # other single-byte codes (kana)
PUTC_SINGLE, PUTC_END = 0x80044c68, 0x80044d08   # rewritten range; PUTC_END = common exit
DRAW_CHAR = 0x800445bc
GP_WRAP, GP_X, GP_Y, GP_ADV_Y, GP_STATE = -0x7712, -0x7716, -0x7714, -0x7f82, -0x7718
GP_ADV_X = -0x7f84
PUTC = 0x80044a6c
PRINT_LOOP = 0x80046c14                 # jal 0x80046c7c (j putc) in print_string's loop


def word(exe, va):
    return struct.unpack_from('<I', exe, va - EXE_BASE)[0]


def put(exe, va, data):
    exe[va - EXE_BASE:va - EXE_BASE + len(data)] = data


def hook():
    hi, lo = asm.hi, asm.lo
    return asm.assemble(PUTC_SINGLE, [
        ('addiu', 't0', 'a2', -0x20),
        ('sltiu', 'at', 't0', len(ASCII)),
        ('beqz', 'at', 'store'),
        ('addiu', 't1', 'zero', DEFAULT_W),
        ('lui', 't1', hi(WIDTHS)),
        ('addu', 't1', 't1', 't0'),
        ('lbu', 't1', lo(WIDTHS), 't1'),
        ('nop',),
        'store:',
        ('lui', 'at', hi(SAVED)),
        ('sb', 't1', lo(SAVED), 'at'),
        ('lh', 'v1', GP_WRAP, 'gp'),
        ('lh', 'v0', GP_X, 'gp'),
        ('bltz', 'v1', 'draw'),
        ('addu', 'v0', 'v0', 't1'),           # x + width (load delay: v0 ready here)
        ('slt', 'at', 'v1', 'v0'),
        ('beqz', 'at', 'draw'),
        ('nop',),
        ('lh', 'v1', GP_Y, 'gp'),
        ('lh', 'v0', GP_ADV_Y, 'gp'),
        ('sh', 'zero', GP_X, 'gp'),
        ('addu', 'v0', 'v1', 'v0'),
        ('sh', 'v0', GP_Y, 'gp'),
        'draw:',
        ('lh', 'a0', GP_X, 'gp'),
        ('lh', 'a1', GP_Y, 'gp'),
        ('jal', DRAW_CHAR),
        ('nop',),
        ('bnez', 'v0', 'adv'),
        ('nop',),
        ('b', PUTC_END),
        ('sb', 'zero', GP_STATE, 'gp'),
        'adv:',
        ('lui', 'at', hi(SAVED)),
        ('lbu', 'v1', lo(SAVED), 'at'),
        ('lh', 'v0', GP_X, 'gp'),
        ('nop',),
        ('addu', 'v0', 'v0', 'v1'),
        ('sh', 'v0', GP_X, 'gp'),
    ])


# 4bpp blit 0x80043e40, two pixel loops (shadow pass at x+1 in s2, colour gp-0x770c; main
# pass at x in t8, colour gp-0x7f80). Per set pixel (column a3, byte at v0):
#   P:   lh a2, colour; andi t0, a3, 1; bnez t0, ODD; andi a2, a2, 0xff
#        lbu t0, (v0) ... (even: low nibble)        ODD: 6 instructions (high nibble)
#   Q:   andi a2, a3, 1; addiu at, zero, 1; bne a2, at, +2; nop  (then v0 += 1)
# Both tests use (column + x) & 1 after the patch. The even path's `lbu t0, (v0)` moves into
# the branch delay slot, so ODD is rewritten not to write t0 first (load delay).
BLIT_LOOPS = [(0x80043f50, 's2', -0x770c), (0x800441a4, 't8', -0x7f80)]
ODD, Q = 0x28, 0x40                   # offsets from P


def blit_orig(gp):
    return [0x87860000 | gp & 0xFFFF, 0x30e80001, 0x15000007, 0x30c600ff, 0x90480000]


BLIT_ODD = [0x00064100, 0x90460000, 0x310800f0, 0x30c6000f, 0x01063025, 0xa0460000]
BLIT_Q = [0x30e60001, 0x24010001, 0x14c10002, 0x00000000, 0x24420001]


def fix_blit(exe):
    for p, xr, gp in BLIT_LOOPS:
        assert [word(exe, p + 4 * i) for i in range(5)] == blit_orig(gp), hex(p)
        assert [word(exe, p + ODD + 4 * i) for i in range(6)] == BLIT_ODD, hex(p + ODD)
        assert [word(exe, p + Q + 4 * i) for i in range(5)] == BLIT_Q, hex(p + Q)
        put(exe, p, asm.assemble(p, [('lh', 'a2', gp, 'gp'), ('addu', 't0', 'a3', xr), ('andi', 't0', 't0', 1),
                                     ('bnez', 't0', p + ODD)]))       # delay slot: lbu t0, (v0)
        put(exe, p + ODD, asm.assemble(p + ODD, [('sll', 'a2', 'a2', 4), ('andi', 'a2', 'a2', 0xf0),
                                                 ('andi', 't0', 't0', 0xf), ('or', 'a2', 'a2', 't0'),
                                                 ('sb', 'a2', 0, 'v0'), ('nop',)]))
        put(exe, p + Q, asm.assemble(p + Q, [('addu', 'a2', 'a3', xr), ('andi', 'a2', 'a2', 1),
                                             ('beqz', 'a2', p + Q + 0x14), ('nop',)]))


def wrap_limit(wrapper_at):
    """Address of the wrapper's line width (s16, < 0 = no wrap), just before its code: the
    caller may set it (narrower than the buffer for some text); while it is < 0 the
    terminal's own wrap width GP_WRAP applies (also < 0 = no wrap)."""
    return wrapper_at - 4


def wrap_override(wrapper_at):
    """Address of a word-wrap width (s16, 0 = none) that print_all takes instead of GP_WRAP
    when narrower: set around a draw whose text box is narrower than its bitmap
    (uipatch.bio_box)."""
    return wrapper_at - 2


def wrapper(at):
    hi, lo = asm.hi, asm.lo
    lim = wrap_limit(at)
    return asm.assemble(at, [
        ('addiu', 'at', 'zero', 0x20),
        ('bne', 'a0', 'at', 'out'),
        ('lui', 't2', hi(lim)),
        ('lh', 't2', lo(lim), 't2'),
        ('lui', 't5', hi(WIDTHS)),
        ('bgez', 't2', 'have'),
        ('addiu', 't5', 't5', lo(WIDTHS)),
        ('lh', 't2', GP_WRAP, 'gp'),          # no limit set: the terminal's wrap width
        ('nop',),
        ('bltz', 't2', 'out'),
        ('nop',),
        'have:',
        ('lh', 't4', GP_X, 'gp'),
        ('lbu', 't1', 0, 't5'),              # width of ' '
        ('addiu', 't3', 's0', 1),
        ('addu', 't4', 't4', 't1'),
        'loop:',
        ('lbu', 't0', 0, 't3'),
        ('addiu', 't3', 't3', 1),
        ('addiu', 'at', 'zero', 0x1B),
        ('beq', 't0', 'at', 'esc'),
        ('sltiu', 'at', 't0', 0x21),
        ('bnez', 'at', 'done'),
        ('sltiu', 'at', 't0', 0x7F),
        ('beqz', 'at', 'high'),
        ('addu', 't1', 't5', 't0'),
        ('lbu', 't1', -0x20, 't1'),
        ('b', 'loop'),
        ('addu', 't4', 't4', 't1'),
        'high:',
        ('addiu', 't1', 't0', -0xA0),
        ('sltiu', 'at', 't1', 0x40),
        ('beqz', 'at', 'lead'),
        ('lh', 't1', GP_ADV_X, 'gp'),
        ('b', 'loop'),
        ('addiu', 't4', 't4', DEFAULT_W),
        'lead:',
        ('addiu', 't3', 't3', 1),            # skip the trail byte
        ('b', 'loop'),
        ('addu', 't4', 't4', 't1'),
        'esc:',
        ('lbu', 't0', 0, 't3'),
        ('addiu', 't3', 't3', 1),
        ('beqz', 't0', 'done'),
        ('addiu', 'at', 'zero', 0x43),
        ('bne', 't0', 'at', 'esc'),
        ('nop',),
        ('b', 'loop'),
        ('nop',),
        'done:',
        ('slt', 'at', 't2', 't4'),
        ('beqz', 'at', 'out'),
        ('nop',),
        ('addiu', 'a0', 'zero', 0x0A),
        'out:',
        ('j', PUTC),
        ('nop',),
    ])


def wrap_hook(exe, heap):
    assert word(exe, PRINT_LOOP) == 0x0c011b1f, 'print_string putc call'
    at = heap.put(b'\xff\xff\0\0') + 4        # wrap_limit: -1 until print_all sets it; wrap_override 0
    assert heap.alloc(len(wrapper(at))) == at
    put(exe, at, wrapper(at))
    put(exe, PRINT_LOOP, asm.assemble(PRINT_LOOP, [('jal', at)]))
    return at


def apply(exe, heap):
    """Patch the program (bytearray) in place; returns the word-wrap wrapper's address."""
    assert word(exe, FONT_INIT_COUNT) == 0x24020080, 'font init count'
    codes = struct.unpack_from('<%dH' % HALF_N, exe, HALF_CODES - EXE_BASE)
    assert codes[0] == 0x20 and codes[64] == 0xA0, 'half-width code table'
    old = exe[HALF_GLYPHS - EXE_BASE:HALF_GLYPHS - EXE_BASE + 16 * HALF_N]
    g = pixfont.glyphs()
    new_codes = ASCII + list(codes[64:64 + KEEP_KANA])
    glyphs = b''.join(g[chr(c)][0] for c in ASCII) + b''.join(old[16 * (64 + k):16 * (65 + k)] for k in range(KEEP_KANA))
    assert len(new_codes) == NEW_N and new_codes == sorted(new_codes)
    put(exe, HALF_CODES, struct.pack('<%dH' % HALF_N, *(new_codes + [0xFFFF] * (HALF_N - NEW_N))))
    widths = bytes(g[chr(c)][1] for c in ASCII)
    put(exe, HALF_GLYPHS, glyphs + widths + bytes(16 * (HALF_N - NEW_N) - len(widths)))
    put(exe, FONT_INIT_COUNT, struct.pack('<I', 0x24020000 | NEW_N))
    code = hook()
    assert PUTC_SINGLE + len(code) <= PUTC_END, 'putc hook too long (%d bytes)' % len(code)
    nops = (PUTC_END - PUTC_SINGLE - len(code)) // 4
    put(exe, PUTC_SINGLE, code + b'\0' * 4 * nops)
    fix_blit(exe)
    return wrap_hook(exe, heap)


if __name__ == '__main__':
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32 | capstone.CS_MODE_LITTLE_ENDIAN)
    for i in md.disasm(hook(), PUTC_SINGLE):
        print('%08x  %-8s %s' % (i.address, i.mnemonic, i.op_str))
