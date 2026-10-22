"""Put a new build into a running (state-loaded) headless DuckStation instead of booting.

A save state holds the RAM of the build it was taken on, program included. This halts
the CPU (GDB connect; reconnected until pc and ra are outside the text renderer and the
heap, so none of our hook code is running), then writes through the GDB server, where RAM
differs from the new build:
  - program code and the freed font area (static at run time: the heap) in full,
  - other program bytes the build changes (strings, tables in .data), whole words,
  - in each overlay in RAM (overlays whose code matches RAM >= 90 % at their load address;
    of overlapping ones the best), the bytes the build changes, whole words,
and forgets the cached message block (gp-0x767c), so the next message outside block 0 is read
from the new disc, and continues. Also restored: words that hold a heap pointer in RAM where
this build keeps the clean word (a string an older build moved and this one leaves in place).
Not refreshed: block 0 (resident; reboot for its changes), other patches an older build made in
an overlay and this one drops.

usage: .venv/bin/python tools/emu/inject.py [built_track1.bin]
"""
import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
import iso, kanji, progstr, setup
import gdbmem, ram as ramlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUILT = os.path.join(ROOT, 'build', 'nobunaga_reppuuden_puk_en_track1.bin')
CODE_END = 0x800b0d70                                       # after the last jr ra
HEAP = (kanji.GLYPHS, kanji.AREA_END)
BUSY = [(0x80043e00, 0x80046d00), HEAP]                      # blit..print_string/putc; heap
MSG_CUR = 0x800de2f0                                         # current message block no. (gp-0x767c)
STATIC = [(0x80010000, CODE_END), HEAP]


def ranges_diff(a, b, base):
    """[(va, va_end)] where a and b differ (merged when < 16 bytes apart)."""
    out, i, n = [], 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and (a[j] != b[j] or any(a[k] != b[k] for k in range(j, min(j + 16, n)))):
                j += 1
            out.append((base + i, base + j))
            i = j
        else:
            i += 1
    return out


def loaded(mem, clean):
    """[(tag, share of its code equal in RAM)] of the overlays in RAM."""
    found = []
    for t in progstr.OVERLAYS:
        c = clean[t]
        base = progstr.ovl_base(c)
        n = c.rfind(b'\x08\x00\xe0\x03') + 8
        at = base & 0x1FFFFF
        same = sum(x == y for x, y in zip(c[:n], mem[at:at + n])) / max(n, 1)
        if same >= 0.90:                          # WMAIN in battle: 0.94 (data in its code range)
            found.append((same, t, base, base + len(c)))
    out = []
    for same, t, lo, hi in sorted(found, reverse=True):
        if not any(lo < h and l < hi for _, l, h, _ in out):
            out.append((t, lo, hi, same))
    return [(t, same) for t, _, _, same in out]


def plan(mem, clean, new):
    """([(va, bytes)] to write, [overlays in RAM]). mem = 2 MB RAM, clean/new = {tag: file}."""
    want = {}
    exe_c, exe_n = clean['PROG'][0x800:], new['PROG'][0x800:]

    def take(img, base, lo, hi):
        for va in range(lo, hi):
            want[va] = img[va - base]

    for lo, hi in STATIC:
        take(exe_n, 0x80010000, lo, hi)
    for lo, hi in ranges_diff(exe_c, exe_n, 0x80010000):
        take(exe_n, 0x80010000, lo & ~3, (hi + 3) & ~3)
    def dropped(c, n, base):
        # words an older build repointed into the heap and this one leaves unchanged
        for k in range(0, min(len(c), len(n)) & ~3, 4):
            if c[k:k + 4] != n[k:k + 4]:
                continue
            at = (base + k) & 0x1FFFFF
            w = int.from_bytes(mem[at:at + 4], 'little')
            if HEAP[0] <= w < HEAP[1] and mem[at:at + 4] != n[k:k + 4]:
                take(n, base, base + k, base + k + 4)

    dropped(exe_c, exe_n, 0x80010000)
    found = loaded(mem, clean)
    ovls = [t for t, _ in found]
    for t, same in found:
        base = progstr.ovl_base(clean[t])
        for lo, hi in ranges_diff(clean[t], new[t], base):
            take(new[t], base, lo & ~3, (hi + 3) & ~3)
        if same >= 0.95:                                # code (static): in full, so repoints
            n = new[t].rfind(b'\x08\x00\xe0\x03') + 8 # an older build made are undone too;
            take(new[t], base, base, base + n)          # not below 95 %: data in the code range
        dropped(clean[t], new[t], base)
    # string slots in full: an older build's longer in-place English leaves bytes after
    # this build's terminator that the clean/new diff does not cover ("Ruse" over "Scheme").
    # Only slots this build writes: other rows may be live variables misread as strings.
    imgs = {'PROG': (exe_c, exe_n, 0x80010000)}
    imgs.update((t, (clean[t], new[t], progstr.ovl_base(clean[t]))) for t in ovls)
    for r in json.load(open(os.path.join(ROOT, 'script', 'progstr.json'), encoding='utf-8')):
        if r['tag'] in imgs and r['kind'] in ('ui', 'array'):
            c, img, base = imgs[r['tag']]
            lo, hi = r['va'] - base, r['va'] - base + r['room']
            if 0 <= lo and hi <= min(len(c), len(img)) and c[lo:hi] != img[lo:hi]:
                take(img, base, r['va'], r['va'] + r['room'])
    out, run = [], None                         # runs where RAM differs, gaps < 32 bytes merged
    for va in sorted(want):
        if mem[va & 0x1FFFFF] == want[va]:
            continue
        if run and va - run[1] < 32 and all(v in want for v in range(run[1], va)):
            run[1] = va + 1
        else:
            run = [va, va + 1]
            out.append(run)
    # whole aligned words (DuckStation's GDB server does not answer unaligned writes)
    return [(lo & ~3, bytes(want.get(v, mem[v & 0x1FFFFF]) for v in range(lo & ~3, (hi + 3) & ~3)))
            for lo, hi in out], ovls


def images(path):
    disc = iso.Disc(path)
    return progstr.disc_files(disc, disc.records())


def main():
    built = sys.argv[1] if len(sys.argv) > 1 else BUILT
    clean, new = images(setup.CLEAN), images(built)
    for tries in range(50):                      # connecting halts the CPU: retry until it is
        g = gdbmem.G()                           # outside our hooks and the text renderer
        g.s.settimeout(5)
        g.cmd('?')
        r = gdbmem.regs(g)
        if not any(lo <= r[k] < hi for k in ('pc', 'r31') for lo, hi in BUSY):
            break
        g.s.sendall(b'$c#63')
        g.s.close()
        time.sleep(0.05)
    else:
        sys.exit('CPU always busy in the text code; try again')
    where = 'pc %08x ra %08x' % (r['pc'], r['r31'])
    writes, ovls = plan(ramlib.shm(), clean, new)
    total = 0
    for va, b in writes:
        for k in range(0, len(b), 0x400):
            part = b[k:k + 0x400]
            r = g.cmd('M%x,%x:%s' % (va + k, len(part), part.hex()))
            if r != 'OK':
                sys.exit('write %08x failed: %r' % (va + k, r))
        total += len(b)
    if g.cmd('M%x,4:ffffffff' % MSG_CUR) != 'OK':
        sys.exit('write %08x failed' % MSG_CUR)
    g.s.sendall(b'$c#63')
    print('halted at %s; wrote %d bytes in %d runs (overlays in RAM: %s)' % (where, total, len(writes), ' '.join(ovls) or '-'))


if __name__ == '__main__':
    main()
