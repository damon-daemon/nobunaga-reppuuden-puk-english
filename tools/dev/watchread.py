"""watchread.py ADDR LEN [secs] [kind]: read (kind 3, default) / access (4) / write (2) watchpoint at ADDR in the
headless DuckStation; print pc/ra and return-address-like stack words once per distinct pc."""
import sys, time
sys.path.insert(0, __file__.rsplit('/', 2)[0] + '/emu')
import gdbmem
addr, n = int(sys.argv[1], 0), int(sys.argv[2], 0)
secs = float(sys.argv[3]) if len(sys.argv) > 3 else 60; kind = sys.argv[4] if len(sys.argv) > 4 else '3'
g = gdbmem.G(); print(g.cmd('Z%s,%x,%x' % (kind, addr, n))); g.s.settimeout(secs)
t0 = time.time(); seen = set()
while time.time() - t0 < secs:
    g.s.sendall(b'$c#63')
    try: g._recv()
    except Exception: break
    r = gdbmem.regs(g)
    if r['pc'] in seen: continue
    seen.add(r['pc'])
    sp = g.read(r['r29'], 0x200)
    ras = ['+%x:%08x' % (i, int.from_bytes(sp[i:i+4], 'little')) for i in range(0, 0x200, 4)
           if 0x80010000 <= int.from_bytes(sp[i:i+4], 'little') < 0x801d0000]
    print('pc %08x ra %08x a0 %08x a1 %08x | %s' % (r['pc'], r['r31'], r['r4'], r['r5'], ' '.join(ras[:24])), flush=True)
g.cmd('z%s,%x,%x' % (kind, addr, n)); g.s.sendall(b'$c#63')
