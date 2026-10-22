"""Single-instruction UI patches (checked against the clean program/overlay): byte pokes into
the Japanese layout and code. build.py applies them before the strings.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm, msgfile, progstr, vwf

PATCHES = [
    # (tag, va, clean instruction, new instruction, why)
    # 36-byte line breakers (notes/overflow_audit.md): a byte copy inserting '\n' every 36 bytes
    # (officer-info bio box, PROG obj+0x1c4; CMD obj+0x3b0, message 33051) breaks English mid-word
    # and 509 bytes + 14 newlines overrun the 512-byte buffer. The counter never reaches 0x7fff:
    # a plain copy (<= 510 bytes + NUL); the VWF word-wraps the box.
    ('PROG', 0x80067d98, ('addiu', 'at', 'zero', 0x24), ('addiu', 'at', 'zero', 0x7fff)),
    ('CMD', 0x801b0558, ('addiu', 'at', 'zero', 0x24), ('addiu', 'at', 'zero', 0x7fff)),
    # Overview list footer plate (PROG 0x80081740..): the "switch" label (大名切替 …, strings 0x800daf94..)
    # is passed to the plate's text object only when strlen < 9 (4 kanji); longer labels are dropped
    # (s0 = 0) and the 64x16 plate shows stale VRAM (noise). English labels are longer: always pass it.
    ('PROG', 0x80081798, ('addu', 's0', 'zero', 'zero'), ('nop',)),
    # Name entry (CMD2 0x801b3dc0 get_font): the "Kanji" page lists the font's level-1 kanji
    # (0x889F-0x9872) grouped by the reading of the kana under the cursor (filter 0x801b287c:
    # ctx, kana group, code -> 0/1; group-end table 0x801bff04). The English font has no level-1
    # kanji (kanji.py: BIOS ROM), so every list was empty; the filter now also passes the
    # full-width Latin set (codes < 0x829B) for every group -> the Kanji button is an "ABC" page.
    # The upper bound (code < 0x9873) is dropped: higher codes fall out of the group loop with 0.
    ('CMD2', 0x801b287c, 0x3401889f, ('ori', 'at', 'zero', 0x829b)),
    ('CMD2', 0x801b2884, 0x14200005, ('bnez', 'at', 0x801b28f8)),      # Latin: return 1
    ('CMD2', 0x801b2888, 0x00000000, ('addiu', 'v0', 'zero', 1)),
    ('CMD2', 0x801b288c, 0x34019873, ('ori', 'at', 'zero', 0x889f)),
    ('CMD2', 0x801b2894, 0x14200003, ('bnez', 'at', 0x801b28f8)),      # below level 1: return 0
    ('CMD2', 0x801b2898, 0x00001821, ('addu', 'v0', 'zero', 'zero')),
    ('CMD2', 0x801b289c, 0x10000016, ('addu', 'v1', 'zero', 'zero')),
    ('CMD2', 0x801b28a0, 0x00001021, ('b', 0x801b28e8)),               # group loop
    ('CMD2', 0x801b28a4, 0x10000010, ('addu', 'a0', 'zero', 'zero')),
    ('CMD2', 0x801b28a8, 0x00002021, ('nop',)),
    # Goal description castle lists (scenario goal picker STUP, system menu Goal MONRT): castle names
    # are appended as "%-12s" (3 per line, 96-px columns at 8 px a byte; namedict turns the padding
    # into column tabs). English "Utsunomiya Castle" is wider than a column, so the names ran into
    # each other ("Kururi CastleMito Castle"). Now "%-18s" (144-px columns) and 2 names per line
    # (the `slti $at, $sN, 3` per-line count in each of the 8 list loops of both overlays).
    ('STUP', 0x801c6378, 0x32312d25, 0x38312d25),          # "%-12" -> "%-18"
    ('MONRT', 0x801cf208, 0x32312d25, 0x38312d25),
] + [(tag, va, ('slti', 'at', reg, 3), ('slti', 'at', reg, 2)) for tag, va, reg in [
    ('STUP', 0x801b528c, 's1'), ('STUP', 0x801b5448, 's0'), ('STUP', 0x801b5784, 's2'), ('STUP', 0x801b590c, 's0'),
    ('STUP', 0x801b5b3c, 's0'), ('STUP', 0x801b5d00, 's0'), ('STUP', 0x801b6988, 's1'), ('STUP', 0x801b6b00, 's0'),
    ('MONRT', 0x801c6254, 's1'), ('MONRT', 0x801c6410, 's0'), ('MONRT', 0x801c674c, 's2'), ('MONRT', 0x801c68d4, 's0'),
    ('MONRT', 0x801c6b04, 's0'), ('MONRT', 0x801c6cc8, 's0'), ('MONRT', 0x801c7950, 's1'), ('MONRT', 0x801c7ac8, 's0'),
]]


# Officer-info bio box (列伝; text object 23 of the window at obj+0x994, resource 0x4e36 =
# a 320 x 64 bitmap, 4 lines): the frame shows 288 px (18 kanji, the Japanese bios break by hand
# there), so English wrapped at 320 was cut at the right edge. Its draws (text object set-text
# 0x8003715c, drawn at once) go through a stub that sets the text object's width (+4: clip) and
# the VWF's wrap override to 288 for the call.
TEXT_SET = 0x8003715c
BIO_DRAWS = [0x800667d0, 0x80066cdc, 0x8006780c, 0x800678e8, 0x8006799c, 0x80067a5c, 0x80067a7c, 0x80067a9c]
BIO_W = 288


def bio_box(exe, heap, wrapper):
    """Route the bio box's draws through the width stub; returns its RAM size."""
    ovr = vwf.wrap_override(wrapper)
    code = lambda at: asm.assemble(at, [
        ('addiu', 'sp', 'sp', -24),
        ('sw', 'ra', 16, 'sp'),
        ('addiu', 't0', 'zero', BIO_W),
        ('sh', 't0', 4, 'a0'),
        ('lui', 'at', asm.hi(ovr)),
        ('jal', TEXT_SET),
        ('sh', 't0', asm.lo(ovr), 'at'),
        ('lui', 'at', asm.hi(ovr)),
        ('sh', 'zero', asm.lo(ovr), 'at'),
        ('lw', 'ra', 16, 'sp'),
        ('nop',),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 24)])
    at = heap.alloc(len(code(0x80000000)))
    vwf.put(exe, at, code(at))
    for va in BIO_DRAWS:
        o = va - vwf.EXE_BASE
        assert exe[o:o + 4] == asm.assemble(va, [('jal', TEXT_SET)]), hex(va)
        exe[o:o + 4] = asm.assemble(va, [('jal', at)])
    return len(code(at))


# Princess given names (field 34; getter 0x8007bf40, index 0..0x73): 0..15 historical via the pointer
# table 0x800b3200, 16..115 generated from char[3] (one kanji) at 0x800c308c (index x 3). Only this getter
# reads them (no compares): the getter gets English tables (tl/names/princesses.json; the Japanese one-kanji
# names would never match in the name dictionary inside text).
PRINCESS_PTRS = (0x8007bf70, 0x8007bf78)            # lui v0 / addiu v0 of the pointer table
PRINCESS_GEN = (0x8007bf98, 0x8007bf9c)             # lui v0 / addiu v0 of the char[3] table
PRINCESS_IDX = (0x8007bf90, 0x8007bf94)             # sll v0,a0,1 / add v1,v0,a0 (x 3)
PRINCESS_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tl', 'names', 'princesses.json')


def princess_names(exe, heap):
    """English princess-name tables in free RAM; returns their size."""
    d = json.load(open(PRINCESS_JSON))
    hist, gen = d['historical'], d['generated']
    assert len(hist) == 16 and len(gen) == 100 and all(n.isascii() and 0 < len(n) < 16 for n in hist + gen)
    w = lambda va: int.from_bytes(exe[va - vwf.EXE_BASE:va - vwf.EXE_BASE + 4], 'little')
    for va, ins in [(PRINCESS_PTRS[0], 0x3c02800b), (PRINCESS_PTRS[1], 0x24423200), (PRINCESS_GEN[0], 0x3c02800c),
                    (PRINCESS_GEN[1], 0x2442308c), (PRINCESS_IDX[0], 0x00041040), (PRINCESS_IDX[1], 0x00441820)]:
        assert w(va) == ins, 'princess name getter %x' % va
    strs = [heap.put(n.encode() + b'\0', 1) for n in hist]
    ptrs = heap.put(b''.join(a.to_bytes(4, 'little') for a in strs), 4)
    table = heap.put(b''.join(n.encode() + bytes(16 - len(n)) for n in gen), 4)
    for (lui, addiu), at in [(PRINCESS_PTRS, ptrs), (PRINCESS_GEN, table)]:
        vwf.put(exe, lui, asm.assemble(lui, [('lui', 'v0', asm.hi(at))]))
        vwf.put(exe, addiu, asm.assemble(addiu, [('addiu', 'v0', 'v0', asm.lo(at))]))
    vwf.put(exe, PRINCESS_IDX[0], asm.assemble(PRINCESS_IDX[0], [('sll', 'v1', 'a0', 4), ('nop',)]))
    return sum(len(n) + 1 for n in hist) + 64 + 1600


# Message block 0 (resident: address forms, pronouns, inserts) shared the 30,756-byte buffer
# 0x8010ED44 with the current block, and init passed a hard-coded block-0 size (0x2e19): English
# block 0 + block 50 no longer fit. Block 0 becomes a static copy in free RAM; the buffer holds
# only the current block. Message offsets (gp-0x7678) keep their single space: block 0 at
# [0, A0), the current block at [A0, ...) with A0 = align4(block-0 size), as before.
MSG_INIT_SIZE = 0x8008c4ac          # addiu a1, zero, 0x2e19: block-0 size for init 0x800967cc
MSG_INIT_LOAD = 0x8008af70          # jal load_block(0) in init 0x8008af48
LOAD_DST = 0x80089f40               # load_block: s1 = block-0 size (dst offset)
ENTRY_B0 = [(0x8008a030, 0x8008a038, 'a1'), (0x8008a06c, 0x8008a070, 'v0')]  # msg_entry, id < 1000
ENTRY_CUR = [(0x8008a0e8, 'a1', 'v0', 'v1'), (0x8008a130, 'v1', 'v0', 'v1')]   # base + A0 -> base
GETBYTE = 0x80089c54                # lui/addiu/addu a1 = 0x8010ED44 + offset


def static_block0(exe, data, offs, heap):
    """Block 0 of the built message file into free RAM + the loader patches; returns its size."""
    b0 = data[offs[0]:offs[1]]
    a0 = len(b0) + 3 & ~3
    assert a0 <= msgfile.B0_MAX
    w = lambda va: int.from_bytes(exe[va - vwf.EXE_BASE:va - vwf.EXE_BASE + 4], 'little')
    chk = lambda va, ins: asm.assemble(va, [ins]) == exe[va - vwf.EXE_BASE:va - vwf.EXE_BASE + 4]
    hi, lo = asm.hi, asm.lo
    assert chk(MSG_INIT_SIZE, ('addiu', 'a1', 'zero', 0x2e19)) and chk(MSG_INIT_LOAD, ('jal', 0x80089f18))
    assert chk(LOAD_DST, ('lw', 's1', -0x766c, 'gp'))
    for va, rd, rs, rt in ENTRY_CUR:
        assert chk(va, ('addu', rd, rs, rt)), hex(va)
    for l, a, r in ENTRY_B0:
        assert chk(l, ('lui', r, 0x8011)) and chk(a, ('addiu', r, r, -0x12bc)), hex(l)
    assert [w(GETBYTE + 4 * i) for i in range(3)] == [0x3c028011, 0x2442ed44, 0x00432821]
    at = heap.put(b0)
    stub = heap.alloc(40)
    cur = msgfile.BUF - a0
    vwf.put(exe, stub, asm.assemble(stub, [
        ('sltiu', 'at', 'v1', a0),
        ('bnez', 'at', 'b0'),
        ('lui', 'a1', hi(cur)),
        ('addiu', 'a1', 'a1', lo(cur)),
        ('jr', 'ra'),
        ('addu', 'a1', 'a1', 'v1'),
        'b0:',
        ('lui', 'a1', hi(at)),
        ('addiu', 'a1', 'a1', lo(at)),
        ('jr', 'ra'),
        ('addu', 'a1', 'a1', 'v1')]))
    vwf.put(exe, GETBYTE, asm.assemble(GETBYTE, [('jal', stub), ('nop',), ('nop',)]))
    vwf.put(exe, MSG_INIT_SIZE, asm.assemble(MSG_INIT_SIZE, [('ori', 'a1', 'zero', len(b0))]))
    vwf.put(exe, MSG_INIT_LOAD, asm.assemble(MSG_INIT_LOAD, [('nop',)]))
    vwf.put(exe, LOAD_DST, asm.assemble(LOAD_DST, [('addu', 's1', 'zero', 'zero')]))
    for va, rd, rs, rt in ENTRY_CUR:
        vwf.put(exe, va, asm.assemble(va, [('addu', rd, rs, 'zero')]))
    for l, a, r in ENTRY_B0:
        vwf.put(exe, l, asm.assemble(l, [('lui', r, hi(at))]))
        vwf.put(exe, a, asm.assemble(a, [('addiu', r, r, lo(at))]))
    return len(b0) + 40


# %M = month name: vsprintf 0x80046e98 (MSVC-style: char class table 0x800b78c8, class 8 =
# conversion letter, then a compare chain) gets two more conversions. 'M' and 'Y' (class 0)
# become class 8 (the high nibble, a state-table entry, is kept); the 'c'/'C' compare at
# 0x800471bc jumps to a stub that checks them first. %M takes an int argument (1..12) and
# continues in the %s path (0x8004721c: a3 = string, v1 = precision) with the month's English
# name ("?" out of range). %Y = month first: swaps the next two arguments (year, month: the
# Japanese order) in the va_list, then does %M, so "%Y %4d" prints "Mar 1546".
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
FMT_CLASS = 0x800b78c8 - 0x20      # + character
FMT_CHAIN_C = 0x800471bc            # addiu at, zero, 0x63 / beq v1, at, 0x800471d4
FMT_C, FMT_S_PTR, FMT_OTHER = 0x800471d4, 0x8004721c, 0x8004741c


def month_conv(exe, heap):
    """The %M conversion; returns its RAM size."""
    for ch, b in (('M', 0x50), ('Y', 0x60)):
        assert exe[FMT_CLASS + ord(ch) - vwf.EXE_BASE] == b, 'printf class table'
    o = FMT_CHAIN_C - vwf.EXE_BASE
    assert exe[o:o + 8] == asm.assemble(FMT_CHAIN_C, [('addiu', 'at', 'zero', 0x63), ('beq', 'v1', 'at', FMT_C)])
    names = [heap.put(m.encode() + b'\0', 1) for m in MONTHS + ['?']]
    table = heap.put(b''.join(a.to_bytes(4, 'little') for a in names[:12]), 4)
    hi, lo = asm.hi, asm.lo
    code = lambda at: asm.assemble(at, [
        ('addiu', 'at', 'zero', ord('M')),
        ('beq', 'v1', 'at', 'm'),
        ('addiu', 'at', 'zero', ord('Y')),
        ('beq', 'v1', 'at', 'y'),
        ('ori', 't0', 'v1', 0x20),
        ('addiu', 'at', 'zero', ord('c')),
        ('beq', 't0', 'at', 'c'),
        ('nop',),
        ('j', FMT_OTHER),
        ('nop',),
        'c:',
        ('j', FMT_C),
        ('nop',),
        'y:',
        ('lw', 't0', 0, 'a2'),
        ('lw', 'at', 4, 'a2'),
        ('sw', 't0', 4, 'a2'),
        ('sw', 'at', 0, 'a2'),
        'm:',
        ('addiu', 'a2', 'a2', 4),
        ('lw', 't0', -4, 'a2'),
        ('lui', 'a3', hi(names[12])),
        ('addiu', 't0', 't0', -1),
        ('sltiu', 'at', 't0', 12),
        ('beqz', 'at', 'done'),
        ('addiu', 'a3', 'a3', lo(names[12])),
        ('sll', 't0', 't0', 2),
        ('lui', 'at', hi(table)),
        ('addu', 'at', 'at', 't0'),
        ('lw', 'a3', lo(table), 'at'),
        'done:',
        ('j', FMT_S_PTR),
        ('addu', 'v1', 't3', 'zero')])
    n = len(code(0x80000000))
    at = heap.alloc(n)
    vwf.put(exe, at, code(at))
    for ch in 'MY':
        exe[FMT_CLASS + ord(ch) - vwf.EXE_BASE] |= 8
    vwf.put(exe, FMT_CHAIN_C, asm.assemble(FMT_CHAIN_C, [('j', at), ('nop',)]))
    return n + 52 + sum(len(m) + 1 for m in MONTHS) + 2


def _off(img, tag, va):
    return va - vwf.EXE_BASE if tag == 'PROG' else va - progstr.ovl_base(img)


def apply(progs):
    """progs: {tag: bytearray}. Returns the number of patched instructions."""
    for tag, va, old, new in PATCHES:
        img = progs[tag]
        o = _off(img, tag, va)
        old, new = [w.to_bytes(4, 'little') if isinstance(w, int) else asm.assemble(va, [w]) for w in (old, new)]
        assert img[o:o + 4] == old, (tag, hex(va))
        img[o:o + 4] = new
    return len(PATCHES)
