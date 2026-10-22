"""logload.py ADDR [secs]: break at ADDR; print a0-a3 and 32 bytes at a1 for each stop (distinct a1/a2)."""
import sys, time
sys.path.insert(0, __file__.rsplit('/', 2)[0] + '/emu')
import gdbmem
addr = int(sys.argv[1], 0); secs = float(sys.argv[2]) if len(sys.argv) > 2 else 60
g = gdbmem.G(); print(g.cmd('Z0,%x,4' % addr)); g.s.settimeout(secs)
t0 = time.time(); seen = set()
while time.time() - t0 < secs:
    g.s.sendall(b'$c#63')
    try: g._recv()
    except Exception: break
    r = gdbmem.regs(g); key = (r['r5'], r['r6'])
    if key in seen: continue
    seen.add(key)
    b = g.read(r['r5'], 32).hex() if 0x80000000 <= r['r5'] < 0x80200000 else '-'
    print('a0 %08x a1 %08x a2 %08x a3 %08x ra %08x | %s' % (r['r4'], r['r5'], r['r6'], r['r7'], r['r31'], b), flush=True)
g.cmd('z0,%x,4' % addr); g.s.sendall(b'$c#63')
