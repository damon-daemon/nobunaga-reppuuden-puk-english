"""Show or change the disc a DuckStation save state loads.

DuckStation loads a state's own media (path in its header), not the disc given on the command
line: a state taken on the clean disc keeps reading the Japanese image even after inject.py put
the English program into its RAM (overlays and message blocks then come from the clean disc).

Header (little-endian u32): +0xa8 media path length, +0xac its offset, +0xc4 screenshot offset,
+0xd4 data offset; the path is followed by the screenshot and the data.

usage: statedisc.py f.sav               print the media path
       statedisc.py f.sav image.cue     point the state at image.cue (rewrites f.sav)
"""
import os, struct, sys


def media(st):
    n, off = struct.unpack_from('<2I', st, 0xa8)
    return st[off:off + n].decode()


def retarget(st, path):
    n, off = struct.unpack_from('<2I', st, 0xa8)
    shot, data = struct.unpack_from('<I', st, 0xc4)[0], struct.unpack_from('<I', st, 0xd4)[0]
    assert off + n == min(shot, data), 'unexpected header layout'
    new = path.encode()
    d = len(new) - n
    out = bytearray(st[:off] + new + st[off + n:])
    struct.pack_into('<I', out, 0xa8, len(new))
    struct.pack_into('<I', out, 0xc4, shot + d)
    struct.pack_into('<I', out, 0xd4, data + d)
    return bytes(out)


def main():
    st = open(sys.argv[1], 'rb').read()
    if len(sys.argv) > 2:
        st = retarget(st, os.path.realpath(sys.argv[2]))
        open(sys.argv[1], 'wb').write(st)
    print(media(st))


if __name__ == '__main__':
    main()
