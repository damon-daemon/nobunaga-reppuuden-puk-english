"""logids.py ADDR [secs]: break at ADDR in the headless DuckStation, print each distinct (a0, ra) once."""
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
    r = gdbmem.regs(g); key = (r['r4'], r['r31'])
    if key not in seen:
        seen.add(key); print('a0 %08x a1 %08x a2 %08x ra %08x' % (r['r4'], r['r5'], r['r6'], r['r31']), flush=True)
g.cmd('z0,%x,4' % addr); g.s.sendall(b'$c#63')
