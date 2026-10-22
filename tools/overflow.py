"""Fixed buffers the English message text reaches (audit: notes/overflow_audit.md).

Messages: msg_get 0x8008aedc expands an entry into the shared buffer gp-0x7698: out()
0x80089cbc stores at most 510 bytes, then sets the error flag and msg_get replaces the text
by "ERRORCODE <id>" (no memory overrun, but the message is lost) -> MSG_LIMIT. Callers reach
it through the message family (fmtlimit.MSG_FUNCS) and mostly hand the result to the String
class (heap) or draw it at once; fmtlimit.msg_sites() finds the copies into fixed buffers
(sprintf with the message as format or argument, strcpy, strcat) and their sizes in the clean
program/overlays; limits() = {message id: largest expansion in bytes} = the smallest target - 1,
plus the hand-traced STATIC ones. tl.py checks every changed entry (and the entries inserting a
changed block-0 entry): its longest English expansion must fit, or at least not exceed the
Japanese's (the estimate is conservative).

Expansion model (measure): text bytes (half-width kana count 2: the interpreter turns them into
2-byte hiragana) with sprintf conversions expanded as in fmtlimit.expand (the expanded message
is often a printf format later); `01 43` inserts count the longest expansion of the inserted
block-0 entry; `02` prints count fmtlimit.NAME (names stay Japanese in the buffer: namedict
translates them at draw time); variants: see measure().
"""
import collections, os, sys

sys.path.insert(0, os.path.dirname(__file__))
import fmtlimit

MSG_LIMIT = 0x1FE - 1                 # out() stores 510 bytes; the NUL needs one
# hand-traced fixed buffers {message id: byte limit} (receivers table: notes/overflow_audit.md)
STATIC = {3170: 255,                  # CLOW 0x8018ab64 sprintf("%s") into a 256-byte pool slot (0x8009466c)
          63097: 255 - fmtlimit.NAME}  # PROG 0x80067d5c / CMD2 0x801b16c4 sprintf("%s%s", name, msg), 256-byte slot
# SALO 0x8017cc50: officer dictionary title (even id of each pair) strcpy'd into char[128]; the body
# (odd id) goes into char[512] (> MSG_LIMIT). Ids: SALO 0x8017cd10.
for _base, _n in [(16000 + 1000 * _b, 50) for _b in range(17)] + [(61000, 50), (33014, 16), (62000, 45), (63000, 45)]:
    for _i in range(_n):
        STATIC[_base + 2 * _i] = 127
_LIMITS = None


def limits():
    """{message id: byte limit of a fixed buffer it is copied into} (computed once from the clean disc)."""
    global _LIMITS
    if _LIMITS is None:
        import progstr
        imgs = progstr.images({t: bytearray(b) for t, b in progstr.clean_files().items() if t not in progstr.NO_STRINGS})
        out = dict(STATIC)
        for site, mid, fn, size in fmtlimit.msg_sites(imgs):
            if mid is not None and size is not None:
                out[mid] = min(out.get(mid, MSG_LIMIT), size - 1)
        _LIMITS = out
    return _LIMITS


def measure(entry, get, memo=None, depth=0):
    """Longest expansion (bytes) of an entry; get(id) -> entry bytes or None.
    Variants end in `05 05 05` (return: the message ends, or goes back to the inserting
    entry); a skipped variant (its `if` false) has still output the text before its first
    keyword. Longest = max over k of (the prefixes of variants 0..k-1 + all of variant k)."""
    import msgfile
    memo = {} if memo is None else memo
    best, pre, cur, open_ = 0, 0, 0, True        # open_: still before the variant's first keyword
    for kind, v in msgfile.tokens(entry):
        if kind == 'struct':
            if v == b'\x05\x05\x05':
                best = max(best, cur)
                cur, open_ = pre, True
            else:
                open_ = False
            continue
        n = _size(kind, v, get, memo, depth)
        cur += n
        if open_:
            pre += n
    return max(best, cur)


def _size(kind, v, get, memo, depth):
    if kind == 'text':
        return fmtlimit.expand(v) + sum(0xA6 <= c <= 0xDF for c in _sjis_single(v))
    if kind == 'print':
        return fmtlimit.NAME
    if kind == 'call':
        cid = v[2] | v[3] << 8
        if cid not in memo:
            memo[cid] = 0
            e = get(cid) if depth < 8 else None
            memo[cid] = measure(e, get, memo, depth + 1) if e else 0
        return memo[cid]
    return 0


def _sjis_single(b):
    """Single-byte characters of Shift-JIS text (lead/trail pairs skipped)."""
    i = 0
    while i < len(b):
        c = b[i]
        if 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC:
            i += 2
            continue
        yield c
        i += 1


if __name__ == '__main__':
    import msgfile, setup, iso
    disc = iso.Disc(setup.CLEAN)
    recs = disc.records()
    exe = disc.file(iso.EXE_NAME, recs)
    blocks = msgfile.parse(disc.file(msgfile.NAME, recs), exe)
    get = lambda m: blocks[m // 1000][m % 1000] if m // 1000 < len(blocks) and m % 1000 < len(blocks[m // 1000]) else None
    memo, sizes = {}, []
    for b, ents in enumerate(blocks):
        for i, e in enumerate(ents):
            sizes.append((measure(e, get, memo), b * 1000 + i))
    sizes.sort(reverse=True)
    print('Japanese: %d entries over %d bytes (model); largest %s' % (sum(n > MSG_LIMIT for n, _ in sizes), MSG_LIMIT, sizes[:8]))
    lim = limits()
    print('fixed-buffer limits: %d ids (%s)' % (len(lim), ', '.join('%d x %d B' % (n, v) for v, n in
                                                                   sorted(collections.Counter(lim.values()).items()))))
    jp = {m: n for n, m in sizes}
    print('Japanese over a fixed-buffer limit:', [(m, jp[m], v) for m, v in sorted(lim.items()) if jp.get(m, 0) > v])
