"""Create the working folder script/ from your own disc image.

script/ holds the Japanese text extracted from the disc next to the English, so it is
generated on each machine and never distributed. Run this once after cloning (and
again after editing tl/), then tools/build.py.

usage: .venv/bin/python tools/setup.py [track1.bin]
"""
import hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(__file__))
import iso, msgfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'script')
TL = os.path.join(ROOT, 'tl')
CLEAN = os.path.join(ROOT, 'nobunaga_reppuuden_puk_track1.bin')
CLEAN_TRACK2 = os.path.join(ROOT, 'nobunaga_reppuuden_puk_track2.bin')
CLEAN_SHA1 = '221738a8700147a69fd02d0785f1c8e28d027405'
TRACK2_SHA1 = '7c8508996d34d2a39fd75a8fab58e2c9b766b09b'


def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def check_clean(path, want=CLEAN_SHA1):
    digest = sha1(path)
    if digest != want:
        sys.exit('%s: SHA-1 %s does not match the clean dump (%s)' % (path, digest, want))


def msg_json(b):
    return os.path.join(SCRIPT, 'msg', 'block%02d.json' % b)


def export_messages(blocks):
    """script/msg/blockNN.json = [{'id', 'jp': [segment strings]}] (entries with text only)."""
    import tl
    os.makedirs(os.path.join(SCRIPT, 'msg'), exist_ok=True)
    for b in range(msgfile.NBLOCKS):
        rows = {}
        for (mid, n), (_, s) in sorted(tl.jp_block(blocks, b).items()):
            rows.setdefault(mid, []).append(s)
        with open(msg_json(b), 'w', encoding='utf-8') as f:
            json.dump([{'id': k, 'jp': v} for k, v in rows.items()], f, ensure_ascii=False, indent=0)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else CLEAN
    check_clean(path)
    track2 = os.path.join(os.path.dirname(os.path.abspath(path)), os.path.basename(CLEAN_TRACK2))
    if os.path.exists(track2):
        check_clean(track2, TRACK2_SHA1)
    disc = iso.Disc(path)
    recs = disc.records()
    os.makedirs(SCRIPT, exist_ok=True)
    export_messages(msgfile.parse(disc.file(msgfile.NAME, recs), disc.file(iso.EXE_NAME, recs)))
    print('script/ ready; now run tools/build.py')


if __name__ == '__main__':
    main()
