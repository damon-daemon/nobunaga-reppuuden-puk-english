"""Where the expanded message goes after the message family (audit helper, notes/overflow_audit.md).

For every family call (fmtlimit.MSG_FUNCS) the first call taking the result is its receiver
(fmtlimit.follow). This follows the text argument into each receiver (linear taint scan of the
function body, branches ignored; stack arguments sp+0x10.. followed too) and its callees up to
DEPTH levels, and reports what happens to it: copied by a copier (fmtlimit.COPIERS, with the
target size when fmtlimit.dst_size finds it), stored into memory (pointer kept: fine while the
shared buffer is not reused), or passed on.

  .venv/bin/python tools/dev/msgrecv.py          -> receivers by call count, then their copies
"""
import collections, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import fmtlimit, progstr
from fmtlimit import s16, writes

DEPTH = 4
STOP = {0x8004f108: 'String ctor (heap)', 0x8003715c: 'draw now', 0x80017a24: 'strlen',
        0x8016c900: 'RT2 dialog (draws via 0x8003715c)'}


def body(img, start):
    """Instruction addresses of the function at start: up to the next prologue."""
    a = start + 4
    while a < img.code_end and a - start < 0x2000:
        w = img.w(a)
        if w >> 16 == 0x27bd and w & 0x8000 and img.w(a - 8) == 0x03e00008:
            break
        a += 4
    return range(start, a, 4)


def trace(img, start, regs, imgs, calls, depth, seen, out, path):
    """regs: tainted registers at entry (100+k = stack argument k >= 4)."""
    key = (img.tag, start, tuple(sorted(regs)))
    if key in seen:
        return
    seen.add(key)
    f = fmtlimit.Func(img, start)
    taint = {r for r in regs if r < 100}
    slots = {f.frame + 4 * (r - 100) for r in regs if r >= 100}     # callee's view of sp+0x10..
    for a in body(img, start):
        w = img.w(a)
        op, rs, rt = w >> 26, (w >> 21) & 31, (w >> 16) & 31
        if op == 0x23 and rs == 29 and s16(w & 0xFFFF) in slots:           # lw rt, slot(sp)
            taint.add(rt)
            continue
        if op == 0x2B and rt in taint:                                     # sw tainted
            off = s16(w & 0xFFFF)
            if rs == 29:
                slots.add(off)
            else:
                out['stored'].add('%s:%08x %s+0x%x (%s)' % (img.tag, a, 'r%d' % rs, off & 0xFFFF, path))
            continue
        if op == 0 and w & 0x3F == 0x21 and rt == 0:                       # move
            rd = (w >> 11) & 31
            (taint.add if rs in taint else taint.discard)(rd)
            continue
        if op in (2, 3):
            t = (w & 0x3FFFFFF) << 2 | 0x80000000
            ds = img.w(a + 4)
            tt = set(taint)
            if ds >> 26 == 0 and ds & 0x3F == 0x21 and (ds >> 16) & 31 == 0:
                (tt.add if (ds >> 21) & 31 in tt else tt.discard)((ds >> 11) & 31)
            elif writes(ds) in tt:
                tt.discard(writes(ds))
            args = [r for r in (4, 5, 6, 7) if r in tt]
            args += [100 + (o - 0x10) // 4 + 4 for o in slots if 0x10 <= o < 0x30 and (o - 0x10) % 4 == 0]
            if args:
                hit(img, a, t, args, imgs, calls, depth, seen, out, path)
            if op == 2:
                break
            taint = {r for r in taint if 16 <= r <= 23 or r == 30}
            continue
        if op == 0 and w & 0x3F == 8 and rs == 31:
            continue
        r = writes(w)
        if r in taint:
            taint.discard(r)


def hit(img, a, t, args, imgs, calls, depth, seen, out, path):
    p = '%s > %08x' % (path, t)
    if t in fmtlimit.COPIERS:
        name = fmtlimit.COPIERS[t]
        src = {'strcpy': [5], 'strcat': [5]}.get(name, [5, 6, 7, 104, 105])
        if any(r in src for r in args) or name == 'sprintf':
            size = fmtlimit.dst_size(img, a, calls, imgs)
            out['copied'].add('%s:%08x %s args %s size %s (%s)' % (img.tag, a, name, args, size, p))
        return
    if t in STOP:
        out['end'][STOP[t]] += 1
        return
    if depth == 0:
        out['deep'].add('%s:%08x -> %08x %s (%s)' % (img.tag, a, t, args, p))
        return
    timg = img if img.base <= t < img.code_end else next((i for i in imgs if i.tag == 'PROG'), None)
    if timg is None or not timg.base <= t < timg.code_end:
        return
    trace(timg, t, args, imgs, calls, depth - 1, seen, out, p)


def main():
    imgs = progstr.images({t: bytearray(b) for t, b in progstr.clean_files().items() if t not in progstr.NO_STRINGS})
    calls = {i.tag: fmtlimit.jals(i) for i in imgs}
    recv = collections.defaultdict(list)
    for img in imgs:
        for t, sites in calls[img.tag].items():
            if t in fmtlimit.MSG_FUNCS:
                for c in sites:
                    f = fmtlimit.follow(img, c)
                    if f:
                        recv[(f[0], tuple(f[1]))].append((img, c))
                    else:
                        recv[None].append((img, c))
    for k, sites in sorted(recv.items(), key=lambda kv: -len(kv[1])):
        if k is None:
            print('%5d  (result not passed to a call right away)' % len(sites))
            continue
        t, args = k
        out = {'copied': set(), 'stored': set(), 'deep': set(), 'end': collections.Counter()}
        seen = set()
        for img, c in sites[:1] if t in STOP else sites:
            use = next(x for x in range(c + 8, c + 320, 4) if img.w(x) >> 26 in (2, 3))
            hit(img, use, t, list(args), imgs, calls, DEPTH, seen, out, '%s:%08x' % (img.tag, c))
        print('%5d  %08x %s  end %s' % (len(sites), t, list(args), dict(out['end'])))
        for kind in ('copied', 'stored', 'deep'):
            for x in sorted(out[kind])[:12]:
                print('         %-6s %s' % (kind, x))


if __name__ == '__main__':
    main()
