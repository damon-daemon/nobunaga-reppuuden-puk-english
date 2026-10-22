"""sheet.py out.png in1.png in2.png ... : contact sheet (2 columns) of 320x240 screenshots, dedup identical."""
import sys
from PIL import Image
out, ins = sys.argv[1], sys.argv[2:]
ims, prev = [], None
for p in ins:
    try: im = Image.open(p).convert('RGB')
    except Exception: continue
    b = im.tobytes()
    if b == prev: continue
    prev = b; ims.append(im)
cols = 2; rows = (len(ims) + 1) // 2
sheet = Image.new('RGB', (320 * cols, 240 * rows), 'black')
for i, im in enumerate(ims):
    sheet.paste(im, ((i % cols) * 320, (i // cols) * 240))
sheet.save(out); print(len(ims), 'frames')
