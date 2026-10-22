"""Translation files for MESSAGE.NB8: show, apply, check, stats; used by build.py.

The unit is a segment (msgfile.segments): a run of Japanese text, inline calls, printed
values and kana mode switches between structural codes (keywords, conditions, jumps). The
English replaces the whole segment; the structure around it is kept byte for byte.

tl/msg/BB.txt holds the English (no Japanese) of block BB, one record per segment:

    # comment (a line starting with #; English text never does)
    @<id>            segment 0 of message <id> (= block * 1000 + index)
    @<id>.<n>        segment n
    English text, possibly over several physical lines (joined with spaces).

  \\n          forced line break (JP line breaks are dropped; the game will word-wrap)
  \\s          a space (for a space at the start/end of a segment; plain edge spaces are
              stripped)
  =           (whole record) keep the Japanese of this segment
  {C21}       call message 21 (block 0: names, titles, grammar words): may move or be
              dropped (Japanese word endings), not added
  {v0234}     print a value (`02 34`, hex after the 02: object 02, field '4' = a name):
              may move or be dropped, not added
  %s %d       printf fields: same ones in the same order; no other '%'

Kana mode switches (`1B K/H/k`) inside a segment are dropped from the English; the last
one is kept at the end so the state after the segment stays the same. Text right after a
value the interpreter would read as part of it (any byte >= 0x20 after an object printed
without a field byte, a digit after a number) gets a `1B H` in between.

Translation memory: a segment without English takes the English of a segment with the
same Japanese (explicit English always wins).

usage:
    tl.py show  BB [from] [to] [--todo]   # Japanese segments (ids are local indexes)
    tl.py apply BB batch.txt              # validate a batch, merge it into tl/msg/BB.txt
    tl.py check [BB]                      # validate everything in tl/
    tl.py stats
"""
import os, re, sys, unicodedata

sys.path.insert(0, os.path.dirname(__file__))
import msgfile, overflow, pixfont, setup, text

TL_MSG = os.path.join(setup.TL, 'msg')
SEG_MAX = 400                      # bytes per segment (expansion buffer of a message: 510)
_TOK = re.compile(r'\{C(\d+)\}|\{v([0-9a-f]{2,})\}')
_FIELD = re.compile(r'%[-0-9]*[sdu]')
_REC = re.compile(r'@(\d+)(?:\.(\d+))?\s*$')
# Line estimate (warning only): dialogue windows = 4 lines x 256 px (map dialogue, seen in the emulator);
# the limit is max(MIN_LINES, Japanese lines) at max(WRAP_PX, widest Japanese line)
WRAP_PX, MIN_LINES = 256, 3
VAR_PX, CALL_PX, FIELD_PX = 64, 40, 24     # guesses for {v..}, {C..}, %s/%d
_W = None
# Officer bios (列伝 box, tools/uipatch.py bio_box): 4 lines of 288 px, word-wrapped (no '\n')
BIO_LINES, BIO_PX = 4, 288
OVER_SLACK = 32                    # bytes, see overflow_errors
BIO_BLOCKS = {b: 99 for b in range(16, 33)}
BIO_BLOCKS.update({33: 45, 61: 99, 62: 89, 63: 89})
# Scenario intros: two messages (35000+2n, +1) drawn into two stacked bitmaps of 7 lines x 272 px (STUP
# 0x801bc818, resources 0x..81; the box scrolls). The translator splits a pair anywhere; the build joins the
# two and splits again where the word wrap ends line 7 (intro_resplit), so the text flows on unbroken.
INTRO_FIRST, INTRO_PAIRS, INTRO_LINES, INTRO_PX = 35000, 11, 7, 272


# ---------------------------------------------------------------- Japanese side
def tok_str(kind, b):
    if kind == 'call':
        return '{C%d}' % (b[2] | b[3] << 8)
    if kind == 'print':
        return '{v%s}' % b[1:].hex()
    return text.decode(b)                     # mode switch: {1B}K etc.


def readable(s):
    """Display form of a segment string: half-width kana are drawn as hiragana, as katakana
    between {1B}K or {1B}k and {1B}H; mode switches removed."""
    out, kata = [], False
    for part in re.split(r'(\{1B\}.)', s):
        if part.startswith('{1B}'):
            kata = part[-1] in 'Kk'
            continue
        def conv(m):
            t = unicodedata.normalize('NFKC', m.group())
            return ''.join(chr(ord(c) - 0x60) if not kata and 'ァ' <= c <= 'ヶ' else c for c in t)
        out.append(re.sub('[ｦ-ﾟ]+', conv, part))
    return ''.join(out)


def seg_str(pieces):
    return ''.join(text.decode(v) if k == 'text' else tok_str(k, v) for k, v in pieces)


def jp_block(blocks, b):
    """{(id, n): (pieces, Japanese string)} of block b."""
    out = {}
    for i, e in enumerate(blocks[b]):
        n = 0
        for kind, v in msgfile.segments(e):
            if kind == 'seg':
                out[(b * 1000 + i, n)] = (v, seg_str(v))
                n += 1
    return out


def load_blocks():
    ex = os.path.join(setup.ROOT, 'extract', 'jp')
    if not os.path.exists(os.path.join(ex, msgfile.NAME)):
        import iso
        disc = iso.Disc(setup.CLEAN)
        recs = disc.records()
        return msgfile.parse(disc.file(msgfile.NAME, recs), disc.file(iso.EXE_NAME, recs))
    return msgfile.parse(open(os.path.join(ex, msgfile.NAME), 'rb').read(),
                         open(os.path.join(ex, 'SLPM_864.83'), 'rb').read())


# ---------------------------------------------------------------- tl files
def tl_path(b):
    return os.path.join(TL_MSG, '%02d.txt' % b)


def parse_tl(path):
    """-> {(id, n): English or '='}"""
    out, key, buf = {}, None, []

    def flush():
        if key is not None:
            out[key] = ' '.join(x.strip() for x in buf if x.strip())
    if not os.path.exists(path):
        return out
    for ln in open(path, encoding='utf-8'):
        ln = ln.rstrip('\n')
        if ln.startswith('#'):
            continue
        m = _REC.match(ln)
        if m:
            flush()
            key, buf = (int(m.group(1)), int(m.group(2) or 0)), []
            if key in out:
                raise ValueError('%s: @%d.%d twice' % (path, *key))
        elif key is not None:
            buf.append(ln)
        elif ln.strip():
            raise ValueError('%s: text before the first @ line: %r' % (path, ln))
    flush()
    return out


def write_tl(b, recs):
    os.makedirs(TL_MSG, exist_ok=True)
    with open(tl_path(b), 'w', encoding='utf-8') as f:
        for (mid, n) in sorted(recs):
            f.write('@%d%s\n%s\n\n' % (mid, '.%d' % n if n else '', recs[(mid, n)]))


# ---------------------------------------------------------------- English side
def encode(s):
    return s.replace('\\n', '\n').replace('\\s', ' ').encode('ascii')


def english_pieces(en, jp_pieces=()):
    """English string -> segment pieces; the last mode switch of the Japanese segment is
    appended."""
    out, pos = [], 0
    for m in _TOK.finditer(en):
        if m.start() > pos:
            out.append(('text', encode(en[pos:m.start()])))
        if m.group(1):
            v = int(m.group(1))
            out.append(('call', bytes([0x01, 0x43, v & 0xFF, v >> 8])))
        else:
            out.append(('print', b'\x02' + bytes.fromhex(m.group(2))))
        pos = m.end()
    if pos < len(en) or not out:
        out.append(('text', encode(en[pos:])))
    for j in range(len(out) - 1, 0, -1):
        if out[j][0] == 'text' and out[j - 1][0] == 'print' and _absorbs(out[j - 1][1], out[j][1]):
            out.insert(j, SEPARATOR)
    modes = [p for p in jp_pieces if p[0] == 'mode']
    return out + modes[-1:]


SEPARATOR = ('mode', b'\x1bH')    # (hiragana mode: no effect on ASCII)


def _absorbs(value, txt):
    """True if the interpreter would read the start of txt as part of the printed value
    (a field byte after an object without one, a digit after a number)."""
    try:
        return msgfile.tokens(value + txt)[0] != ('print', value)
    except (msgfile.GrammarError, IndexError):
        return True


def check_seg(jp, en, jp_pieces=()):
    """Errors of one English segment against its Japanese string."""
    errs = []
    plain = _TOK.sub('', en)
    bad = sorted(set(c for c in plain.replace('\\n', '').replace('\\s', '') if not (' ' <= c <= '~') or c in '{}\\'))
    if bad:
        errs.append('characters not allowed: %s' % ''.join(bad))
    if _FIELD.findall(plain) != _FIELD.findall(_TOK.sub('', jp)):
        errs.append('printf fields %s, Japanese has %s' % (_FIELD.findall(plain), _FIELD.findall(_TOK.sub('', jp))))
    if '%' in _FIELD.sub('', plain):
        errs.append("bare '%' (write 'percent')")
    jt = [m.group() for m in _TOK.finditer(jp)]
    for t in set(m.group() for m in _TOK.finditer(en)):
        if en.count(t) > jt.count(t):
            errs.append('%s used %d times, Japanese has %d' % (t, en.count(t), jt.count(t)))
    if errs:
        return errs
    pieces = english_pieces(en, jp_pieces)
    b = msgfile.seg_bytes(pieces)
    if len(b) > SEG_MAX:
        errs.append('%d bytes > %d' % (len(b), SEG_MAX))
    # the interpreter must read the bytes back as the same tokens
    try:
        back = [p for p in msgfile.tokens(b) if p[0] != 'text']
    except (msgfile.GrammarError, IndexError) as x:
        back = [('error', str(x))]
    if back != [p for p in pieces if p[0] != 'text']:
        errs.append('bytes not read back as written: %s' % b.hex(' '))
    return errs


def est_lines(en, width=WRAP_PX):
    """Lines the word wrap gives (tools/pixfont.py widths), inserts at guessed widths."""
    global _W
    if _W is None:
        _W = {c: w for c, (_, w) in pixfont.glyphs().items()}
    s = _TOK.sub(lambda m: '\x03' if m.group(1) else '\x01', _FIELD.sub('\x02', en)).replace('\\n', '\n')
    size = lambda w: sum({'\x01': VAR_PX, '\x02': FIELD_PX, '\x03': CALL_PX}.get(c) or _W.get(c, 8) for c in w)
    lines = 0
    for para in s.split('\n'):
        lines += 1
        x = 0
        for i, w in enumerate(para.split(' ')):
            add = size(w) + (_W[' '] if i else 0)
            if i and x + add > width:
                lines, x = lines + 1, size(w)
            else:
                x += add
    return lines


def is_bio(mid):
    """Biography body: odd entry of the officer/princess/guest bio blocks (even = name line)."""
    b, i = divmod(mid, 1000)
    return mid % 2 == 1 and i <= BIO_BLOCKS.get(b, -1) and (b != 33 or i >= 15)


def fit_seg(mid, en):
    """Hard limits of the box an entry is drawn in."""
    if is_bio(mid):
        n = est_lines(en, BIO_PX)
        if '\\n' in en or n > BIO_LINES:
            return ['bio: %d lines at %d px (box: %d, no \\n)' % (n, BIO_PX, BIO_LINES)]
    return []


def wrap_lines(en, width):
    """Word-wrapped lines of plain text (no tokens, no \\n), as the VWF wraps them."""
    est_lines('')
    size = lambda w: sum(_W.get(c, 8) for c in w)
    lines, x = [[]], 0
    for w in en.split(' '):
        add = size(w) + (_W[' '] if lines[-1] else 0)
        if lines[-1] and x + add > width:
            lines.append([w]); x = size(w)
        else:
            lines[-1].append(w); x += add
    return [' '.join(ws) for ws in lines]


def intro_resplit(recs):
    """Scenario intro pairs split again at the end of the first bitmap's last line. Returns (recs, errors)."""
    recs, errs = dict(recs), []
    for n in range(INTRO_FIRST, INTRO_FIRST + 2 * INTRO_PAIRS, 2):
        a, b = recs.get((n, 0)), recs.get((n + 1, 0))
        if not a or not b or '=' in (a, b):
            continue
        if _TOK.search(a + b) or _FIELD.search(a + b) or '\\' in a + b:
            errs.append('@%d: scenario intro with tokens or \\n' % n)
            continue
        lines = wrap_lines(a.strip() + ' ' + b.strip(), INTRO_PX)
        if len(lines) > 2 * INTRO_LINES:
            errs.append('@%d: scenario intro %d lines at %d px (box: 2 x %d)' % (n, len(lines), INTRO_PX, INTRO_LINES))
            continue
        recs[(n, 0)] = ' '.join(lines[:INTRO_LINES])
        recs[(n + 1, 0)] = ' '.join(lines[INTRO_LINES:]) or '\\s'
    return recs, errs


def jp_width(jp):
    """Widest line of a Japanese segment in px (16 per full-width character, 8 half-width)."""
    s = _TOK.sub('\x01', readable(jp))
    return max(sum(VAR_PX if c == '\x01' else 8 if ' ' <= c <= '~' or 'ｦ' <= c <= 'ﾟ' else 16 for c in ln)
               for ln in s.split('\n'))


def warn_seg(jp, en):
    w = max(WRAP_PX, jp_width(jp))
    n, jl = est_lines(en, w), jp.count('\n') + 1
    if n > max(MIN_LINES, jl):
        return ['~%d lines at %d px (Japanese %d)' % (n, w, jl)]
    return []


def memory(jps, tls):
    """{Japanese string: English} from every explicit record."""
    tm = {}
    for b, recs in tls.items():
        for k, en in recs.items():
            if en != '=' and k in jps.get(b, {}):
                tm.setdefault(jps[b][k][1], en)
    return tm


def english_entries(blocks, tls=None):
    """Replace the translated segments in blocks (list of entry lists, in place).
    Returns (translated segment count, errors)."""
    tls = tls if tls is not None else {b: parse_tl(tl_path(b)) for b in range(msgfile.NBLOCKS)}
    jps = {b: jp_block(blocks, b) for b in tls if tls[b]}
    tm = memory(jps, tls)
    errs, done = [], 0
    if tls.get(INTRO_FIRST // 1000):
        tls = dict(tls)
        tls[INTRO_FIRST // 1000], errs = intro_resplit(tls[INTRO_FIRST // 1000])
    orig, touched = [list(es) for es in blocks], []
    for b in range(msgfile.NBLOCKS):
        recs = tls.get(b, {})
        for k in recs:
            if k not in jps.get(b, {}):
                errs.append('@%d.%d: no such segment' % k)
        if not recs and not tm:
            continue
        for i, e in enumerate(blocks[b]):
            items = msgfile.segments(e)
            n, changed = 0, False
            for j, (kind, v) in enumerate(items):
                if kind != 'seg':
                    continue
                key = (b * 1000 + i, n)
                n += 1
                jp = seg_str(v)
                en = recs.get(key)
                if en is None:
                    en = tm.get(jp)
                if en is None or en == '=':
                    continue
                es = check_seg(jp, en, v) + fit_seg(key[0], en)
                if es:
                    errs += ['@%d.%d: %s' % (*key, x) for x in es]
                    continue
                items[j] = ('seg', english_pieces(en, v))
                changed = True
                done += 1
            if changed:
                blocks[b][i] = msgfile.join_segments(items)
                touched.append(b * 1000 + i)
    errs += overflow_errors(blocks, orig, touched)
    return done, errs


def overflow_errors(blocks, orig, ids):
    """Entries (and entries inserting them) whose English expansion can overflow the message
    buffer or a fixed buffer it is copied into (tools/overflow.py)."""
    lims = overflow.limits()
    get = lambda bs: (lambda m: bs[m // 1000][m % 1000] if m // 1000 < len(bs) and m % 1000 < len(bs[m // 1000]) else None)
    errs, memo_en, memo_jp = [], {}, {}
    check = set(ids)
    if any(i < 1000 for i in ids):                     # a block-0 entry changed: every entry
        check = {b * 1000 + i for b, es in enumerate(blocks) for i in range(len(es))}
    for mid in sorted(check):
        e = get(blocks)(mid)
        if e is None:
            continue
        lim = lims.get(mid, overflow.MSG_LIMIT)
        n = overflow.measure(e, get(blocks), memo_en)
        if n <= lim:
            continue
        j = overflow.measure(get(orig)(mid), get(orig), memo_jp)
        # the model overestimates the 7 Japanese entries it puts over the limit (skipped variants are
        # summed): those may grow a little (longer block-0 address forms), not by a whole sentence
        if n > j + (OVER_SLACK if j > lim else 0):
            errs.append('%d: up to %d bytes into a buffer of %d (Japanese %d; tools/overflow.py)' % (mid, n, lim + 1, j))
    return errs


# ---------------------------------------------------------------- commands
def show(b, a=0, z=None, todo=False):
    blocks = load_blocks()
    jp = jp_block(blocks, b)
    recs = parse_tl(tl_path(b))
    tls = {c: parse_tl(tl_path(c)) for c in range(msgfile.NBLOCKS)}
    tm = memory({c: jp if c == b else jp_block(blocks, c) for c in tls if tls[c]}, tls)
    calls = set()
    for (mid, n), (pieces, s) in sorted(jp.items()):
        i = mid % 1000
        if i < a or (z is not None and i > z):
            continue
        if todo and ((mid, n) in recs or s in tm):
            continue
        print('@%d%s\n%s\n' % (mid, '.%d' % n if n else '', readable(s).replace('\n', '\\n')))
        calls.update(int(m.group(1)) for m in _TOK.finditer(s) if m.group(1))
    if calls:
        print('# calls:')
        for c in sorted(calls):
            cb, ci = divmod(c, 1000)
            segs = [s for (mid, n), (_, s) in sorted(jp_block(blocks, cb).items()) if mid == c] \
                if cb < msgfile.NBLOCKS and ci < len(blocks[cb]) else []
            print('#   {C%d} = %s' % (c, ' | '.join(readable(s).replace('\n', '\\n') for s in segs)[:200]))


def apply(b, batch):
    blocks = load_blocks()
    jp = jp_block(blocks, b)
    new = parse_tl(batch)
    errs = []
    for k, en in new.items():
        if k not in jp:
            errs.append('@%d.%d: no such segment in block %d' % (*k, b))
        elif en != '=':
            errs += ['@%d.%d: %s' % (*k, x) for x in check_seg(jp[k][1], en, jp[k][0]) + fit_seg(k[0], en)]
    if errs:
        sys.exit('\n'.join(errs) + '\n%d errors, nothing written' % len(errs))
    for k, en in new.items():
        if en != '=':
            for w in warn_seg(jp[k][1], en):
                print('warning @%d.%d: %s' % (*k, w))
    recs = parse_tl(tl_path(b))
    recs.update(new)
    write_tl(b, recs)
    print('block %d: %d records merged (%d in file)' % (b, len(new), len(recs)))


def check(only=None):
    blocks = load_blocks()
    tls = {b: parse_tl(tl_path(b)) for b in range(msgfile.NBLOCKS) if only is None or b in (0, only)}   # block 0: its English sizes the calls
    done, errs = english_entries(blocks, tls)
    jps = {b: jp_block(load_blocks(), b) for b in tls if tls[b]}
    for b, recs in tls.items():
        for k, en in sorted(recs.items()):
            if en != '=' and k in jps.get(b, {}):
                for w in warn_seg(jps[b][k][1], en):
                    print('warning @%d.%d: %s' % (*k, w))
    data, offs = msgfile.build(blocks)
    for w in msgfile.check_sizes(offs):       # build.py fails on it; not the translator's to fix (phase 3)
        print('warning (main session):', w)
    print('\n'.join(errs))
    print('%d segments translated, %d errors' % (done, len(errs)))
    return not errs


def stats():
    blocks = load_blocks()
    tls = {b: parse_tl(tl_path(b)) for b in range(msgfile.NBLOCKS)}
    jps = {b: jp_block(blocks, b) for b in range(msgfile.NBLOCKS)}
    tm = memory(jps, tls)
    tot = done = 0
    for b in range(msgfile.NBLOCKS):
        n = len(jps[b])
        d = sum(1 for k, (_, s) in jps[b].items() if k in tls[b] or s in tm)
        tot, done = tot + n, done + d
        if n:
            print('block %02d: %5d / %5d' % (b, d, n))
    print('total   : %5d / %5d (%.1f%%)' % (done, tot, 100.0 * done / max(tot, 1)))


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == 'show':
        nums = [int(x) for x in a[2:] if not x.startswith('--')]
        show(int(a[1]), *(nums + [0, None])[:2], todo='--todo' in a)
    elif a[0] == 'apply':
        apply(int(a[1]), a[2])
    elif a[0] == 'check':
        sys.exit(0 if check(int(a[1]) if len(a) > 1 else None) else 1)
    elif a[0] == 'stats':
        stats()
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
