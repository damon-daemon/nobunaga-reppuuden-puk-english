"""Contact sheet of every TIM in the given files (survey helper).
usage: timsheet.py out.png FILE.NB8... [--scale N]
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')); import tim
from PIL import Image, ImageDraw
a = sys.argv[1:]
sc = 1
if '--scale' in a:
    k = a.index('--scale'); sc = int(a[k + 1]); del a[k:k + 2]
out, files = a[0], a[1:]
ims = []
for f in files:
    d = open(f, 'rb').read()
    for k, o in enumerate(tim.chain(d)):
        im = tim.Texture(d, o).image()
        bg = Image.new('RGB', im.size, (60, 60, 90)); bg.paste(im, mask=im.split()[3])
        if sc > 1: bg = bg.resize((bg.width * sc, bg.height * sc), Image.NEAREST)
        ims.append(('%s#%d' % (os.path.basename(f)[:-4], k), bg))
W = 1024; x = y = rowh = 0; pos = []
for name, im in ims:
    w, h = im.size
    if x + w > W: x, y, rowh = 0, y + rowh + 12, 0
    pos.append((x, y, name, im)); x += w + 6; rowh = max(rowh, h)
sheet = Image.new('RGB', (W, y + rowh + 12), (20, 20, 20)); dr = ImageDraw.Draw(sheet)
for x, y, name, im in pos:
    sheet.paste(im, (x, y + 10)); dr.text((x, y), name, fill=(255, 255, 0))
sheet.save(out); print(sheet.size)
