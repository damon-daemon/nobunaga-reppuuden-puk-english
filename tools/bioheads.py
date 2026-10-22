"""English for the bio name lines (even entries of the bio blocks: "NAME {1B}kREADING{1B}H years")
from the name dictionary (tools/names.py pairs), so they always match the names drawn in game.

Writes "Family Given 1534-1582" records into tl/msg/BB.txt (merged like `tl.py apply`; an
existing record that differs from the generated one is replaced only with --force). Lines
whose name the dictionary does not know are listed: translate those by hand (tl.py apply).
Re-run after changing tl/names/officers.json.

usage: .venv/bin/python tools/bioheads.py [--force]
"""
import os, re, sys

sys.path.insert(0, os.path.dirname(__file__))
import iso, names, setup, tl

HEAD = re.compile(r'(.*?) *\{1B\}k.*?\{1B\}H *(\S+)～(\S+)$')


def years(a, b):
    q = lambda y: '?' if set(y) == {'?'} else y
    return '%s-%s' % (q(a), q(b))


def english(name, p):
    if name in p:
        return p[name]
    for k in range(1, len(name)):
        if name[:k] in p and name[k:] in p:
            return p[name[:k]] + ' ' + p[name[k:]]
    return None


def heads(blocks, p):
    """[(key, English or None, Japanese)] for every bio name line."""
    out = []
    for b in tl.BIO_BLOCKS:
        for (mid, n), (_, s) in sorted(tl.jp_block(blocks, b).items()):
            if n or not tl.is_bio(mid + 1) or mid % 2:
                continue
            m = HEAD.match(s)
            if not m:
                continue
            en = english(m.group(1), p)
            out.append(((mid, n), en and '%s %s' % (en, years(m.group(2), m.group(3))), s))
    return out


def main():
    force = '--force' in sys.argv
    disc = iso.Disc(setup.CLEAN)
    p = names.pairs(disc, disc.records())
    blocks = tl.load_blocks()
    by_block, unknown, kept = {}, [], 0
    for key, en, jp in heads(blocks, p):
        if en is None:
            unknown.append('@%d %s' % (key[0], jp))
        else:
            by_block.setdefault(key[0] // 1000, {})[key] = en
    for b, new in sorted(by_block.items()):
        recs = tl.parse_tl(tl.tl_path(b))
        for k, en in new.items():
            if k in recs and recs[k] != en and not force:
                kept += 1
                continue
            recs[k] = en
        tl.write_tl(b, recs)
    print('%d name lines written, %d hand-edited kept, %d unknown:' % (sum(map(len, by_block.values())) - kept, kept, len(unknown)))
    print('\n'.join(unknown))


if __name__ == '__main__':
    main()
