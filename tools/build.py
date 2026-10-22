"""Rebuild track 1 of the disc with the translation and produce a patch.

Output: build/nobunaga_reppuuden_puk_en_track1.bin, a copy of track 2 and a .cue next to
them (boots in an emulator), and build/nobunaga_reppuuden_puk_en.xdelta (clean track 1 ->
English track 1; track 2 is not changed).

usage: .venv/bin/python tools/build.py [clean_track1.bin] [out_track1.bin]
"""
import os, shutil, subprocess, sys

sys.path.insert(0, os.path.dirname(__file__))
import cells, gfx, iso, kanji, msgfile, namedict, names, progstr, setup, tl, uipatch, vwf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'build', 'nobunaga_reppuuden_puk_en_track1.bin')
FIXED = (iso.EXE_NAME,)                  # must keep their place


def build_files(disc, recs):
    """{iso path: bytes} of every rebuilt file."""
    exe = bytearray(disc.file(iso.EXE_NAME, recs))
    msg = disc.file(msgfile.NAME, recs)
    blocks = msgfile.parse(msg, exe)
    heap = kanji.apply(exe)
    wrapper = vwf.apply(exe, heap)
    try:
        pairs = names.pairs(disc, recs)
    except ValueError as e:
        sys.exit('tl/names:\n%s' % e)
    d = namedict.build(pairs, *names.compose_sets(disc, recs), names.NOT_ALONE, names.ALONE_OK)
    layout = heap.put(bytes(namedict.LAYOUT_SIZE))       # table columns (cells -> namedict)
    labels = namedict.apply(exe, heap, d, wrapper, layout)
    print('names: %d pairs, %d dictionary entries' % (len(pairs), len(d.ent)))
    print('table cells: pixel columns, %d bytes of RAM' % cells.apply(exe, heap, layout))
    clean = progstr.disc_files(disc, recs)
    ovl = {t: bytearray(b) for t, b in clean.items() if t != 'PROG'}
    print('ui patches: %d instructions' % uipatch.apply(dict(ovl, PROG=exe)))
    try:
        print('command bar: %d words' % gfx.cmdbar_layout(ovl))
    except ValueError as e:
        sys.exit(str(e))
    print('bio box: %d bytes of RAM' % uipatch.bio_box(exe, heap, wrapper))
    print('princess names: %d bytes of RAM' % uipatch.princess_names(exe, heap))
    print('printf %%M (month names): %d bytes of RAM' % uipatch.month_conv(exe, heap))
    namedict.hook_measure(dict(ovl, PROG=exe), labels['measure'])
    namedict.hook_linebrk(ovl, labels['wrapm'])
    print('narration captions: wrap before sizing, %d bytes of RAM' % namedict.hook_caption(dict(ovl, PROG=exe), heap, labels['measure'], labels['wrapm']))
    print('message windows: wrap before sizing, %d bytes of RAM' % namedict.hook_notice(exe, heap, labels['measure'], labels['wrapm']))
    try:
        n, moved = progstr.apply(dict(ovl, PROG=exe), clean, heap)
    except ValueError as e:
        sys.exit('tl/progstr:\n%s' % e)
    print('progstr: %d strings (%d moved to free RAM)' % (n, moved))
    done, errs = tl.english_entries(blocks)
    if errs:
        sys.exit('tl/msg:\n' + '\n'.join(errs))
    print('messages: %d segments in English' % done)
    test = os.environ.get('NB_TEST_MSG')          # debug: 'id=text' replaces the first segment of one message
    if test:
        mid, t = test.split('=', 1)
        b, i = divmod(int(mid), 1000)
        items = msgfile.segments(blocks[b][i])
        k = next(n for n, (kind, _) in enumerate(items) if kind == 'seg')
        items[k] = ('seg', tl.english_pieces(t, items[k][1]))
        blocks[b][i] = msgfile.join_segments(items)
    data, offs = msgfile.build(blocks, (msg, msgfile.table(exe)))
    bad = msgfile.check_sizes(offs)
    if bad:
        sys.exit('%s:\n%s' % (msgfile.NAME, '\n'.join(bad)))
    msgfile.set_table(exe, offs)
    print('message block 0: %d bytes of RAM' % uipatch.static_block0(exe, data, offs, heap))
    print('free RAM used: %d / %d bytes' % heap.used())
    out = {iso.EXE_NAME: bytes(exe), msgfile.NAME: data}
    out.update({progstr.ovl_name(t): bytes(b) for t, b in ovl.items() if b != clean[t]})
    try:
        imgs = gfx.apply(lambda path: disc.file(path, recs))
    except ValueError as e:
        sys.exit('tl/gfx: %s' % e)
    print('images: %d files redrawn' % len(imgs))
    out.update(imgs)
    return out


def write(clean, out, files):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    moved = iso.write_image(clean, out, files)
    print('rebuilt %s%s' % (out, ' (moved: %s)' % ', '.join(moved) if moved else ''))
    if set(moved) & set(FIXED):
        sys.exit('%s moved on the disc' % ', '.join(set(moved) & set(FIXED)))
    iso.verify(out, files)
    track2 = out.replace('_track1.bin', '_track2.bin')
    if not os.path.exists(track2) or os.path.getsize(track2) != os.path.getsize(setup.CLEAN_TRACK2):
        shutil.copyfile(setup.CLEAN_TRACK2, track2)
    iso.write_cue(out.replace('_track1.bin', '.cue'), out, track2)
    patch = os.path.join(os.path.dirname(out), 'nobunaga_reppuuden_puk_en.xdelta')
    try:
        subprocess.run(['xdelta3', '-e', '-f', '-9', '-S', 'djw', '-B', str(1 << 28), '-s', clean, out, patch], check=True)
        print('patch %s (%d bytes)' % (patch, os.path.getsize(patch)))
    except FileNotFoundError:
        print('xdelta3 not installed; skipped patch')
    return moved


def main():
    clean = sys.argv[1] if len(sys.argv) > 1 else setup.CLEAN
    out = sys.argv[2] if len(sys.argv) > 2 else OUT
    disc = iso.Disc(clean)
    files = build_files(disc, disc.records())
    write(clean, out, files)


if __name__ == '__main__':
    main()
