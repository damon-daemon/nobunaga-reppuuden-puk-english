"""findmeasure.py 'needle' [addr ...]: break at strlen (0x80017a24) and longest-line (0x80036f14)
in the running headless DuckStation; print the caller (ra) each time a0 points at a string
containing the needle. Ctrl-C / timeout ends it."""
import sys, time
sys.path.insert(0, __file__.rsplit('/', 2)[0] + '/emu')
import gdbmem
needle = bytes.fromhex(sys.argv[1][4:]) if sys.argv[1].startswith('hex:') else sys.argv[1].encode()
reg = 'r4'
args = sys.argv[2:]
if args and args[0].startswith('r'): reg = args.pop(0)
addrs = [int(a, 0) for a in args] or [0x80017a24, 0x80036f14]
g = gdbmem.G()
for a in addrs: print(a, g.cmd('Z0,%x,4' % a))
g.s.settimeout(120)
t0 = time.time(); seen = set()
while time.time() - t0 < 110:
    g.s.sendall(b'$c#63')
    try: g._recv()
    except Exception as e: print('timeout', e); break
    r = gdbmem.regs(g)
    s = g.read(r[reg], 48)
    if needle in s:
        key = (r['pc'], r['r31'])
        if key not in seen:
            seen.add(key); sp=g.read(r['r29'],0x400); ras=[(i,int.from_bytes(sp[i:i+4],'little')) for i in range(0,0x400,4)]; ras=['+%x:%08x'%(i,w) for i,w in ras if 0x80010000<=w<0x801d0000 and w&3==0]; print('RAS', ' '.join(ras)); obj=g.read(r['r18'],0x40) if 0x80000000<=r['r18']<0x80200000 else b''; print('pc %08x ra %08x reg %08x a1 %x a2 %x a3 %x s2 %08x obj %s sp %s %r' % (r['pc'], r['r31'], r[reg], r['r5'], r['r6'], r['r7'], r['r18'], obj.hex(), ' '.join('%08x'%int.from_bytes(sp[i:i+4],'little') for i in range(0,96,4)), s.split(b'\0')[0][:40]), flush=True)
for a in addrs: g.cmd('z0,%x,4' % a)
g.s.sendall(b'$c#63')
