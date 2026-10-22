"""List likely UI strings (NUL-terminated Shift-JIS) in the program and overlays, survey helper.
usage: ustr.py [-v] [file...]  (default: program + BIN/*.BIN)
"""
import glob, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
R = re.compile(rb'(?:[\x81-\x84\x88-\x9f\xe0-\xea][\x40-\x7e\x80-\xfc]|[\xa6-\xdf]|[0-9A-Za-z %.\-/:\n]|%[0-9.\-]*[sdx]|\x1b[KHk])+(?=\0)')


def strings(d):
    for m in R.finditer(d):
        if m.start() and d[m.start() - 1] not in (0,):
            continue
        try:
            s = m.group().decode('cp932')
        except UnicodeDecodeError:
            continue
        if sum(1 for c in s if ord(c) > 0x3000) >= 2:
            yield m.start(), s


if __name__ == '__main__':
    v = '-v' in sys.argv
    files = [f for f in sys.argv[1:] if f != '-v'] or [os.path.join(ROOT, 'extract/jp/SLPM_864.83')] + sorted(glob.glob(os.path.join(ROOT, 'extract/jp/BIN/*.BIN')))
    tot = 0
    for f in files:
        r = list(strings(open(f, 'rb').read()))
        tot += sum(len(s) for _, s in r)
        print('## %-14s strings %5d chars %6d' % (os.path.basename(f), len(r), sum(len(s) for _, s in r)))
        if v:
            for o, s in r:
                print('%06x %s' % (o, s.replace('\n', '\\n').replace('\x1b', '\\e')))
    print('total chars', tot)
