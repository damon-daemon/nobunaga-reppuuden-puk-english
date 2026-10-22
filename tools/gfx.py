"""Redraw the Japanese text baked into the TIM images (*.NB8 TIM chains, tools/tim.py).

Ported from the Sangokushi VI PUK gfx.py (itself from Soutenroku/Taiko 5). Lettering comes from the stroke glyphs of
tools/latinfont.py (original drawings), rendered with antialiasing at any size and
mapped to the nearest colour of the image's own CLUT (palettes, sizes and the TIM count
never change), or for tiny 4bpp icons from the pixel glyphs of tools/tinyfont.py.

  plate(): light text with a dark shadow/outline on a textured plate (menu bars, tiles,
    buttons): the old lettering and its shadow are erased by inpainting from the plate
    around them, then the English is drawn in the original's fill and shadow colours.
  clear(): text on transparency (the box is cleared, then redrawn).
  banner(): big outlined lettering on transparency.

  cmdbar: the main-map command bar (MAINWIN sheets regridded, see CMDBAR below).

tl/gfx.txt: "FILE.tim[.spot]<TAB>English" ('|' = line break). FILE is the disc path
without .NB8 (MDATA1/MAINWIN); spot = index into the item's box list in ITEMS (multi-label
images). Items not in ITEMS are a plate over the whole image.
"""
import collections, os, random, struct, sys
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))
import asm, latinfont, pixfont, tim, tinyfont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TL = os.path.join(ROOT, 'tl', 'gfx.txt')
SS = 4

def luma(c):
    return (c[0] * 299 + c[1] * 587 + c[2] * 114) // 1000

class Canvas:
    """Palette helpers around a tim.Texture."""
    def __init__(self, tex):
        self.t = tex
        self.w, self.h, self.pal = tex.w, tex.h, tex.pal
        self.cache = {}

    def get(self, x, y):
        return self.t.get(x, y)

    def idx(self, x, y):
        return self.t.idx(x, y)

    def put(self, x, y, i):
        self.t.put(x, y, i)

    def index(self, rgb, alpha=0x80):
        """Nearest palette entry to an RGB colour (alpha 0 = the transparent colour)."""
        key = tuple(int(round(v)) for v in rgb[:3]) + (int(round(alpha)),)
        if key not in self.cache:
            self.cache[key] = min(range(len(self.pal)), key=lambda i: sum((a - b) ** 2 for a, b in zip(self.pal[i][:3], key[:3]))
                                  + 6 * (self.pal[i][3] - key[3]) ** 2)
        return self.cache[key]

# ---------------------------------------------------------------- lettering

def text_masks(text, cap, widths, condense=1.0, spacing=None, descender=None):
    """Coverage images (PIL 'L', SS times the final size) of text drawn with each
    stroke width in widths (px, final scale): wider strokes give the outline layers.
    Returns (images, (w, h), (left, top) of the cap box) at final scale."""
    stroke = widths[0]
    s = (cap - stroke) / latinfont.C
    spacing = stroke * 0.45 if spacing is None else spacing
    pad = max(widths) / 2 + 1
    xs, x = [], pad
    for c in text:
        w, _ = latinfont.GLYPHS[c]
        xs.append(x)
        x += (w * s * condense + stroke + spacing) if c != ' ' else cap * 0.32
    width = x - spacing + pad
    if descender is None:
        descender = any(c in 'gjpqy,;' for c in text)
    height = cap + stroke + 2 * pad + (3 * s if descender else 0)
    base = pad + cap - stroke / 2
    out = []
    for sw in widths:
        im = Image.new('L', (int(width * SS) + 1, int(height * SS) + 1), 0)
        dr = ImageDraw.Draw(im)
        r = sw * SS / 2
        for c, x0 in zip(text, xs):
            for st in latinfont.GLYPHS[c][1]:
                pts = [((x0 + stroke / 2 + px * s * condense) * SS, (base - py * s) * SS) for px, py in st]
                for a, b in zip(pts, pts[1:]):
                    dr.line([a, b], fill=255, width=max(1, round(sw * SS)))
                for px, py in pts:
                    dr.ellipse((px - r, py - r, px + r, py + r), fill=255)
        out.append(im)
    return out, (width, height), (pad, pad)

def fit(lines, cap, box_w, box_h, widths_of, min_cap=5.0, min_condense=0.7):
    """Shrink (condense first, then cap) until every line fits box_w and all lines box_h.
    -> (cap, condense, [(masks, (w, h), (px, py))])"""
    condense = 1.0
    while True:
        r = [text_masks(l, cap, widths_of(cap), condense, descender=False) for l in lines]
        lh = cap + widths_of(cap)[-1]
        tw = max(m[1][0] for m in r) - 2 * (max(widths_of(cap)) / 2 + 1)
        if (tw <= box_w and lh * len(lines) + (len(lines) - 1) * 1 <= box_h + 1) or cap <= min_cap:
            if tw > box_w + 0.5 or lh * len(lines) > box_h + 3:
                raise ValueError('%r does not fit %dx%d' % ('|'.join(lines), box_w, box_h))
            return cap, condense, r
        if condense > min_condense and lh * len(lines) + (len(lines) - 1) * 1 <= box_h + 1:
            condense *= 0.96            # too wide: condense first
        else:
            cap *= 0.95

# ---------------------------------------------------------------- plates

def plate_style(cv, box, mode='light', grow=2, sens=(24, 0.4), amin=0x40, th=None):
    """-> (text mask set, fill rgb, outline rgb, text alpha) for light text with a dark
    shadow (mode 'light') or dark text on a light plate ('dark'). th = fixed luma threshold
    of the lettering (when it covers too much of the box for the median to be the plate)."""
    x0, y0, x1, y1 = box
    px = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    L = {p: luma(cv.get(*p)) for p in px}
    A = {p: cv.get(*p)[3] for p in px}
    vals = sorted(L[p] for p in px if A[p] > amin)
    med = vals[len(vals) // 2]
    spread = max(6, vals[len(vals) * 2 // 3] - vals[len(vals) // 3])
    if th is not None:
        core = {p for p in px if (L[p] >= th if mode == 'light' else L[p] <= th) and A[p] > amin}
        med = sorted(L[p] for p in px if p not in core)[(len(px) - len(core)) // 2]
    elif mode == 'light':
        hi = vals[int(len(vals) * 0.97)]
        th = med + max(sens[0], (hi - med) * sens[1])
        core = {p for p in px if L[p] >= th and A[p] > amin}
    else:
        lo = vals[int(len(vals) * 0.03)]
        th = med - max(sens[0], (med - lo) * sens[1])
        core = {p for p in px if L[p] <= th and A[p] > amin}
    if not core:
        raise ValueError('no lettering found in %r' % (box,))
    ring = collections.Counter()
    for (x, y) in core:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                q = (x + dx, y + dy)
                if q in L and q not in core and ((L[q] < med) if mode == 'light' else (L[q] > med)):
                    ring[q] += 1
    fillc = sorted((cv.get(*p) for p in core), key=luma)
    fill = fillc[int(len(fillc) * 0.8)] if mode == 'light' else fillc[len(fillc) // 5]
    outc = sorted((cv.get(*p) for p in ring), key=luma)
    outline = (outc[len(outc) // 10] if mode == 'light' else outc[len(outc) * 3 // 4]) if outc else None
    mask = set(core) | set(ring)
    for _ in range(grow):
        mask |= {(x + dx, y + dy) for (x, y) in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                 if (x + dx, y + dy) in L and abs(L[x + dx, y + dy] - med) > spread}
    mask |= {(x + dx, y + dy) for (x, y) in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (x + dx, y + dy) in L}
    return mask, fill[:3], outline[:3] if outline else None, fill[3]

def erase(cv, box, mask, seed=0, sample=False):
    """Inpaint masked pixels from the outside in (onion peel): each pass fills the pixels
    with the most known neighbours by copying a random known pixel within 2 px, so the
    background's local colours and grain continue under the text. sample: copy random
    unmasked pixels from anywhere in the box instead (plain noisy stone under big lettering)."""
    x0, y0, x1, y1 = box
    rnd = random.Random(seed)
    todo = {p for p in mask if x0 <= p[0] <= x1 and y0 <= p[1] <= y1}
    if sample:
        src = [cv.idx(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1) if (x, y) not in todo]
        for p in sorted(todo):
            cv.put(p[0], p[1], rnd.choice(src))
        return
    near = [(dx, dy) for dx in range(-2, 3) for dy in range(-2, 3) if dx or dy]
    def known(x, y):
        return [(x + dx, y + dy) for dx, dy in near
                if x0 <= x + dx <= x1 and y0 <= y + dy <= y1 and (x + dx, y + dy) not in todo]
    while todo:
        cand = {p: known(*p) for p in sorted(todo)}
        best = max(len(v) for v in cand.values())
        if best == 0:
            break
        need = max(1, min(best, 8))
        layer = [p for p, v in cand.items() if len(v) >= need]
        for p in layer:
            cv.put(p[0], p[1], cv.idx(*rnd.choice(cand[p])))
        todo -= set(layer)

def draw(cv, box, text, fill, outline, alpha=0x80, cap=None, align='center', bold=0.14, lw=1.0,
         vshift=0, hshift=0, shadow=None, min_cap=5.0, sharp=0.0):
    """English (lines split on '|') in box, fill + outline colours. shadow=(dx, dy): the
    outline colour is a drop shadow of the letters instead of a ring round them. sharp
    (0-1) pushes antialiased edges towards ink or background (crisper small text)."""
    x0, y0, x1, y1 = box
    lines = text.split('|')
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    ring = outline is not None and not shadow
    widths_of = lambda c: [max(1.0, c * bold)] + ([max(1.0, c * bold) + 2 * lw] if ring else [])
    cap = cap or min(bh / len(lines) * 0.62, 22)
    cap = min(cap, bh / len(lines) * 0.75)
    sx, sy = shadow or (0, 0)
    cap, condense, rs = fit(lines, cap, bw - 2 - sx, bh - sy, widths_of, min_cap)
    lh = cap + widths_of(cap)[-1] + (1 if len(lines) > 1 else 0)
    total = lh * len(lines) - (1 if len(lines) > 1 else 0)
    top = y0 + (bh - sy - total) / 2 + vshift
    def cover(v):
        if sharp:
            v = min(1.0, max(0.0, (v - 0.5) * (1 + 3 * sharp) + 0.5))
        return v
    for k, (masks, (tw, th), (px, py)) in enumerate(rs):
        size = (int(tw) + 1, int(th) + 1)
        layers = [m.resize(size, Image.BOX) for m in masks]
        inkw = tw - 2 * px
        ox = {'center': x0 + (bw - sx - inkw) / 2, 'left': x0 + 1, 'right': x1 - sx - inkw}[align] - px + hshift
        oy = top + k * lh - py + (widths_of(cap)[-1] - widths_of(cap)[0]) / 2
        ox, oy = int(round(ox)), int(round(oy))
        get = lambda l, x, y: cover(l.getpixel((x, y)) / 255) if 0 <= x < size[0] and 0 <= y < size[1] else 0
        for y in range(-1, size[1] + sy + 1):
            for x in range(-1, size[0] + sx + 1):
                tx, ty = ox + x, oy + y
                if not (x0 <= tx <= x1 and y0 <= ty <= y1):
                    continue
                c = get(layers[0], x, y)
                if shadow:
                    e = get(layers[0], x - sx, y - sy) if outline else 0
                else:
                    e = get(layers[1], x, y) if ring else 0
                if c <= 0.04 and e <= 0.08:
                    continue
                base = cv.get(tx, ty)
                col = base[:3]
                if e > 0:
                    col = tuple(p + (q - p) * min(1, e * 1.3) for p, q in zip(col, outline))
                if c > 0:
                    col = tuple(p + (q - p) * min(1, c * 1.15) for p, q in zip(col, fill))
                a = base[3] + (max(alpha, base[3]) - base[3]) * max(c, e)
                cv.put(tx, ty, cv.index(col, a))

def clear(cv, box, text, dark=False, amin=0x60, outline=True, colours=None, **kw):
    """Text on transparency: fill/outline = bright/dark opaque colours of the box (or
    colours=(fill, outline) rgb), which is cleared to the transparent colour and redrawn."""
    x0, y0, x1, y1 = box
    inside = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    if colours:
        fill, line = colours
    else:
        opq = sorted((cv.get(x, y) for x, y in inside if cv.get(x, y)[3] >= amin), key=luma)
        fill, line = opq[len(opq) * 19 // 20], opq[len(opq) // 10]
        if dark:
            fill, line = opq[len(opq) // 20], opq[len(opq) * 9 // 10]
    t = min(range(len(cv.pal)), key=lambda i: cv.pal[i][3])
    if cv.pal[t][3]:
        t = cv.index((0, 0, 0))         # no transparent colour (opaque full-screen image): black
    for x, y in inside:
        cv.put(x, y, t)
    if text:
        draw(cv, box, text, fill[:3], line[:3] if outline else None, 0x80, **kw)

def plate(cv, text, box=None, mode='light', erase_box=None, style=None, colours=None, sample=False, **kw):
    """colours = (fill rgb, outline rgb): override the measured ones (e.g. a tile whose
    lettering is too thin to measure well)."""
    box = box or (1, 1, cv.w - 2, cv.h - 2)
    if mode == 'clear':
        return clear(cv, box, text, colours=colours, **kw)
    boxes = erase_box if isinstance(erase_box, list) else [erase_box or box]
    mask, fill, outline, alpha = plate_style(cv, boxes[0], mode, **(style or {}))
    for b in boxes[1:]:
        mask |= plate_style(cv, b, mode, **(style or {}))[0]
    if colours:
        fill, outline = colours
    for b in boxes:
        erase(cv, b, mask, sample=sample)
    if text and '^' in text:
        # date column: left part in the first col px, the rest left-aligned after it
        col = kw.pop('col', 50)
        left, right = text.split('^')
        kw['align'] = 'left'
        draw(cv, (box[0] + 2, box[1], box[0] + col - 1, box[3]), left, fill, outline, alpha, **kw)
        draw(cv, (box[0] + col + 2, box[1], box[2], box[3]), right, fill, outline, alpha, **kw)
    elif text:
        kw.pop('col', None)
        draw(cv, box, text, fill, outline, alpha, **kw)

# ---------------------------------------------------------------- pixel icons

def pixel(cv, text, box, mode='dark', font=None, ink=None, shadow=None, grow=0, vshift=0, hshift=0, also=(), th=None, grey=False, outline=None, flat=None):
    """Tiny icon lettering (tinyfont): the old glyph = pixels of the ink palette index (the
    darkest colour in the box, or the brightest for mode 'light'; ink= forces it) plus those
    of the shadow index; they are inpainted, then text is drawn in the same index, centred.
    shadow = (palette index, dx, dy) drop shadow. font: 'mid' / 'small' (default: mid when
    it fits). also = more palette indexes to erase; th = erase every pixel darker (mode
    'dark') or brighter than this luma; grey = erase every grey pixel (lettering and its
    outline on a coloured plate); outline = palette index of a 1 px ring round the text;
    flat = fill the whole box with this palette index instead of inpainting."""
    x0, y0, x1, y1 = box
    px = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    if ink is None:
        used = collections.Counter(cv.idx(*p) for p in px)
        key = lambda i: luma(cv.pal[i])
        ink = (min if mode == 'dark' else max)((i for i in used if cv.pal[i][3]), key=key)
    kill = {ink} | ({shadow[0]} if shadow else set()) | set(also)
    mask = {p for p in px if cv.idx(*p) in kill}
    if grey:
        mask |= {p for p in px if max(cv.get(*p)[:3]) - min(cv.get(*p)[:3]) < 24}
    if th is not None:
        mask |= {p for p in px if (luma(cv.get(*p)) <= th if mode == 'dark' else luma(cv.get(*p)) >= th)}
    for _ in range(grow):
        mask |= {(x + dx, y + dy) for (x, y) in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                 if x0 <= x + dx <= x1 and y0 <= y + dy <= y1}
    if isinstance(flat, tuple):
        flat = cv.index(flat[:3], *flat[3:])
    if flat is not None:
        for p in px:
            cv.put(p[0], p[1], flat)
    else:
        erase(cv, box, mask)
    if not text:
        return
    lines = text.split('|')
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    sx, sy = (shadow[1], shadow[2]) if shadow else (0, 0)
    if outline is not None:
        bw, bh, x0, y0 = bw - 2, bh - 2, x0 + 1, y0 + 1
    fonts = {'mid': tinyfont.MID, 'small': tinyfont.SMALL}
    for name in ([font] if font else ['mid', 'small']):
        f = fonts[name]
        rs = [tinyfont.render(l, f) for l in lines]
        # lowercase descenders hang below the cap height (7): count them only when present
        hs = [h for _, h, _ in rs]
        total = sum(hs) + len(lines) - 1
        if max(w for w, _, _ in rs) + sx <= bw and total + sy <= bh + (2 if name == 'mid' else 0):
            break
    else:
        raise ValueError('%r does not fit %dx%d' % (text, bw, bh))
    cap = 7 if f is tinyfont.MID else 5
    lh = cap + 1
    top = y0 + (bh - sy - (lh * len(lines) - 1)) // 2 + vshift
    for k, (w, h, ink_px) in enumerate(rs):
        ox = x0 + (bw - sx - w + 1) // 2 + hshift
        oy = top + k * lh
        if outline is not None:
            ring = {(x + dx, y + dy) for (x, y) in ink_px for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
            for (x, y) in ring:
                tx, ty = ox + x, oy + y
                if box[0] <= tx <= x1 and box[1] <= ty <= y1:
                    cv.put(tx, ty, outline)
        if shadow:
            for (x, y) in ink_px:
                tx, ty = ox + x + sx, oy + y + sy
                if x0 <= tx <= x1 and y0 <= ty <= y1:
                    cv.put(tx, ty, shadow[0])
        for (x, y) in ink_px:
            tx, ty = ox + x, oy + y
            if x0 <= tx <= x1 and y0 <= ty <= y1 + 1:
                cv.put(tx, ty, ink)

def icons(n, w=16, h=16, inset=2, **kw):
    """A strip of n icons of w x h (frame inset px)."""
    return [dict(kw, kind='pixel', box=(k * w + inset, inset, k * w + w - 1 - inset, h - 1 - inset)) for k in range(n)]

# ---------------------------------------------------------------- engraved banners

def engrave(cv, text, cap=20, bold=0.18, bevel=1.6, rim=1.0, edge=1.0, colours=None, min_cap=10):
    """Big lettering on transparency in the HEX/SSLG.S6 style (crisp, no antialiasing): dark
    stroke, light bevel (bright up-left, grey down-right), green rim, black edge. The whole
    image is cleared first. colours = (core, bevel light, bevel dark, rim, edge) rgb."""
    core_c, hi_c, lo_c, rim_c, edge_c = colours or ((8, 8, 8), (230, 230, 230), (164, 172, 156), (82, 115, 82), (8, 8, 8))
    t = min(range(len(cv.pal)), key=lambda i: cv.pal[i][3])
    for y in range(cv.h):
        for x in range(cv.w):
            cv.put(x, y, t)
    condense = 1.0
    while True:
        sw = max(2.0, cap * bold)
        widths = [sw, sw + 2 * bevel, sw + 2 * (bevel + rim), sw + 2 * (bevel + rim + edge)]
        masks, (tw, th), _ = text_masks(text, cap, widths, condense, descender=False)
        if tw <= cv.w - 2 or cap <= min_cap:
            break
        if condense > 0.8:
            condense *= 0.97
        else:
            cap *= 0.96
    if tw > cv.w:
        raise ValueError('%r: %d px wide, image %d' % (text, tw, cv.w))
    size = (int(tw) + 1, int(th) + 1)
    layers = [m.resize(size, Image.BOX) for m in masks]
    on = [[[layers[k].getpixel((x, y)) >= 128 for x in range(size[0])] for y in range(size[1])] for k in range(4)]
    ox, oy = (cv.w - size[0]) // 2, (cv.h - size[1]) // 2
    idx = [cv.index(c) for c in (core_c, hi_c, lo_c, rim_c, edge_c)]
    for y in range(size[1]):
        for x in range(size[0]):
            tx, ty = ox + x, oy + y
            if not (0 <= tx < cv.w and 0 <= ty < cv.h):
                continue
            if on[0][y][x]:
                i = idx[0]
            elif on[1][y][x]:
                # bevel: lit when the stroke lies down-right of it
                lit = any(on[0][y + d][x + d] for d in (1, 2) if y + d < size[1] and x + d < size[0])
                i = idx[1] if lit else idx[2]
            elif on[2][y][x]:
                i = idx[3]
            elif on[3][y][x]:
                i = idx[4]
            else:
                continue
            cv.put(tx, ty, i)

# ---------------------------------------------------------------- items

# (file, tim): [box options per spot]; a spot's options go to plate() ('kind': 'banner' etc.)
MENU = {'shadow': (1, 1), 'cap': 11, 'bold': 0.12, 'sharp': 0.5, 'style': {'grow': 1}}
def menu_bars(rows, cols=2, w=192, h=20):
    """The 384x200 main-menu images: 2 columns x 10 bars of 192x20."""
    return [dict(MENU, box=(c * w + 4, r * h + 3, c * w + w - 6, r * h + h - 4),
                 erase_box=(c * w + 3, r * h + 2, c * w + w - 5, r * h + h - 3)) for r in range(rows) for c in range(cols)]

TILE = {'cap': 9, 'bold': 0.13, 'shadow': (1, 1), 'style': {'grow': 1}}
def tiles(cols, rows, w=48, h=40, **kw):
    """Atlases of 48x40 label tiles (frame 4 px): spot = row*cols + column."""
    return [dict(TILE, box=(c * w + 4, r * h + 5, c * w + w - 5, r * h + h - 7), **kw) for r in range(rows) for c in range(cols)]

def buttons(**kw):
    """160x160: 4 big square buttons of 80x80 (register menus)."""
    return [dict({'cap': 13, 'bold': 0.13, 'style': {'grow': 1}}, box=(c * 80 + 12, r * 80 + 12, c * 80 + 67, r * 80 + 67), **kw)
            for r in range(2) for c in range(2)]

# Main-map command bar (MDATA1/MAINWIN #11 normal, #12 highlighted; notes/graphics.md): each
# command is one cell of a sheet record (RT1 0x7587 normal / 0x7588 highlighted / 0x758f = #12's
# pixels under #13's CLUT; TL 0xc3aa/c3ab/c3b2 = the tutorial's copies), cell n of a grid of
# cols x rows over the record's 128x192 rect (record word 6: CLUT y | cols << 16 | rows << 24).
# Japanese: 8 x 6 cells of 16x32 (two stacked kanji), one row of 7 widgets at x 56..202, y 192.
# English: 3 x 16 cells of 40x12 (one pixfont line), the widgets in two rows of 4 + 3. The
# loader steps through the file by the record rects, so those stay; only the grid changes.
CMD_COLS, CMD_ROWS = 3, 16
# The grid works in VRAM halfwords (4 px): the rect's 32 / 3 -> cells of 10 (40 px) at 0, 10, 21.
CMD_W, CMD_H = 32 // CMD_COLS * 4, 192 // CMD_ROWS
CMD_U = [32 * c // CMD_COLS * 4 for c in range(CMD_COLS)]
CMD_RECORDS = [   # (overlay, va of word 6, clean value)
    ('RT1', 0x8016a6f8, 0x060800f1), ('RT1', 0x8016a714, 0x060800f1), ('RT1', 0x8016a7d8, 0x060800f1),
    ('TL', 0x8019e88c, 0x060801f5), ('TL', 0x8019e8a8, 0x060801f5), ('TL', 0x8019e96c, 0x060801f5)]
CMD_WIDGETS = [('CMD', 0x801cd1f0), ('TL', 0x8019c2a0)]   # 7 x 20 bytes: flags, id 0x96b+i, image, y<<16|x, 0
CMD_X0 = [56, 80, 104, 128, 152, 176, 202]
# The bar's image ids carry the cell as (row + 1) << 24 | (col + 1) << 16 | 0x80000000 | record,
# built by one block per overlay from the menu's linear cell index n (s3): row n >> 3, col n & 7,
# then the normal / highlighted / third record. Rewritten for 3 columns (row n / 3, col n % 3).
CMD_IDS = [('CMD', 0x801b20b8, 0x7587), ('TL', 0x8017e2c0, 0xc3aa)]   # (overlay, va, normal record)

def _ids_clean(rec):
    return [0x001310c2, 0x24420001, 0x00021e00, 0x32620007, 0x24420001, 0x3c018000, 0x00022400,
            0x34210000 | rec, 0x00811021, 0x3c018000, 0x00432821, 0x34210000 | (rec + 1), 0x00811021,
            0x3c018000, 0x00433021, 0x34210000 | (rec + 8), 0x00811021, 0x00433821]

def _ids_new(va, rec):
    code = asm.assemble(va, [
        ('addiu', 'v0', 'zero', CMD_COLS),
        ('divu', 's3', 'v0'),
        ('mflo', 'v1'),
        ('mfhi', 'v0'),
        ('addiu', 'v1', 'v1', 1),
        ('sll', 'v1', 'v1', 24),
        ('addiu', 'v0', 'v0', 1),
        ('sll', 'a0', 'v0', 16),
        ('addu', 'a0', 'a0', 'v1'),
        ('lui', 'at', 0x8000),
        ('ori', 'at', 'at', rec),
        ('addu', 'a1', 'a0', 'at'),          # normal
        ('addiu', 'a2', 'a1', 1),            # highlighted
        ('addiu', 'a3', 'a1', 8),            # third (#12's pixels, #13's CLUT)
    ])
    return code + bytes(18 * 4 - len(code))
CMD_POS = [(48 + 44 * (i % 4), 193 + 14 * (i // 4)) for i in range(7)]
CMD_INK = {11: (9, 8), 12: (6, 2)}     # (ink, shadow) CLUT indexes: #11 white, #12 red (#13's CLUT: gold)

def cmdbar_layout(ovl):
    """Regrid the command sheets and move the bar's widgets (overlay images by tag)."""
    for tag, va, old in CMD_RECORDS:
        o = va - struct.unpack_from('<I', ovl[tag], 8)[0]
        if struct.unpack_from('<I', ovl[tag], o)[0] != old:
            raise ValueError('cmdbar: %s %08x is not the clean grid' % (tag, va))
        struct.pack_into('<I', ovl[tag], o, old & 0xffff | CMD_ROWS << 24 | CMD_COLS << 16)
    for tag, va in CMD_WIDGETS:
        base = struct.unpack_from('<I', ovl[tag], 8)[0]
        for i, (x, y) in enumerate(CMD_POS):
            o = va - base + 20 * i
            if struct.unpack_from('<II', ovl[tag], o + 4)[0] != 0x96b + i or \
                    struct.unpack_from('<I', ovl[tag], o + 12)[0] != (192 << 16 | CMD_X0[i]):
                raise ValueError('cmdbar: %s widget %d at %08x is not the clean one' % (tag, i, va + 20 * i))
            struct.pack_into('<I', ovl[tag], o + 12, y << 16 | x)
    for tag, va, rec in CMD_IDS:
        o = va - struct.unpack_from('<I', ovl[tag], 8)[0]
        if list(struct.unpack_from('<18I', ovl[tag], o)) != _ids_clean(rec):
            raise ValueError('cmdbar: %s %08x is not the clean id code' % (tag, va))
        ovl[tag][o:o + 72] = _ids_new(va, rec)
    return len(CMD_RECORDS) + len(CMD_WIDGETS) * len(CMD_POS) + 18 * len(CMD_IDS)

def _cmd_glyphs(text):
    """-> [(x, {(col, row)})] of the label's glyphs, kerned by one column where needed: a gap
    goes when the facing columns have no ink row in common (they touch at most diagonally)."""
    g = pixfont.glyphs()
    out, x = [], 0
    for ch in text:
        if ch == ' ':
            x += pixfont.SPACE_ADV
            continue
        if ch not in g:
            raise ValueError('cmdbar: no glyph %r' % ch)
        rows, adv = g[ch]
        px = {(c, r) for r in range(12) for c in range(8) if rows[pixfont.TOP + r] & (0x80 >> c)}
        out.append([x, px])
        x += adv
    def width():
        return max(gx + c for gx, px in out for c, _ in px) + 1
    def edge(px, last):
        c = max(c for c, _ in px) if last else min(c for c, _ in px)
        return c, {r for cc, r in px if cc == c}
    for k in range(len(out) - 1, 0, -1):
        if width() <= CMD_W:
            break
        (xa, pa), (xb, pb) = out[k - 1], out[k]
        ca, ra = edge(pa, True)
        cb, rb = edge(pb, False)
        if xb + cb - (xa + ca) == 2 and not ra & rb:
            for item in out[k:]:
                item[0] -= 1
    if width() > CMD_W:
        raise ValueError('cmdbar: %r is %d px wide (cell %d)' % (text, width(), CMD_W))
    return out

def cmd_cell(cv, text, n, ink, shadow):
    """One command label in pixfont (rows TOP..TOP+11 = the 12-px cell), left aligned, with a
    drop shadow to the right and below."""
    x0, y0 = CMD_U[n % CMD_COLS], n // CMD_COLS * CMD_H
    ink_px = {(gx + c, r) for gx, px in _cmd_glyphs(text) for c, r in px}
    for px, py in ink_px:
        for dx, dy in ((1, 0), (0, 1), (1, 1)):
            q = (px + dx, py + dy)
            if q not in ink_px and q[0] < CMD_W and q[1] < CMD_H:     # shadow clipped at the cell edge
                cv.t.put(x0 + q[0], y0 + q[1], shadow)
    for px, py in ink_px:
        cv.t.put(x0 + px, y0 + py, ink)

def grid(cols, rows, w, h, inset=4, pair=1, insety=None, **kw):
    """Button sheets of cols x rows cells of w x h (frame 3 px): spot = linear cell index.
    pair=2: the cells come in dim / lit pairs with the same label -> spot = cell // 2 and
    both cells get the text ('boxes'). insety: a different inset for the y axis (flat bars)."""
    iy = inset if insety is None else insety
    cells = [(c * w + inset, r * h + iy, c * w + w - 1 - inset, r * h + h - 1 - iy)
             for r in range(rows) for c in range(cols)]
    return [dict(MENU, box=cells[i], boxes=cells[i:i + pair], **kw) for i in range(0, len(cells), pair)]

def kanji_tiles(n, th, first=0, **kw):
    """Strips of 16x16 one-kanji label tiles (COMMON): tinyfont SMALL codes in the kanji's darkest
    colour; th = luma below which a pixel is lettering (the plates are lighter)."""
    return [dict(kind='pixel', box=(k * 16 + 1, 1, k * 16 + 14, 14), font='small', mode='dark', th=th, **kw)
            for k in range(first, first + n)]

# EDATA editor strips (notes/gfx_edata.md): one label per TIM on the left of a textured plate (dark
# lettering), transparent value field from col 34 (2-kanji labels) / 66 (4-kanji); 20-px rows: frame
# rows 0-2 / 18-19, separator 2 px before the field.
FIELD = dict(MENU, mode='dark')
def field(label_w=34, cols=1, rows=1, cw=0, ch=20):
    """cols x rows cells of cw x ch (one cell = the whole TIM) with the label in the first
    label_w px of each cell (box after the 2-px frame, before the separator)."""
    return [dict(FIELD, box=(c * cw + 2, r * ch + 3, c * cw + label_w - 3, r * ch + ch - 3))
            for r in range(rows) for c in range(cols)]
BTN = dict(MENU, mode='dark', cap=9)      # 32x16 OK / Cancel / Next / Back buttons: box = the plate

# BGM select: IDATA1/BGMSEL #0 = 16 x 3 tiles of 16x16, tracks 1-24 as a kanji numeral + a "ten"
# tick (tiles 0-23 normal, 24-47 selected: green corner). English: the number in tinyfont MID.
BGM_TRACKS = 24
def bgm_tile(cv, text, n):
    for k in (n, n + BGM_TRACKS):
        x0, y0 = k % 16 * 16, k // 16 * 16
        # the old numeral's shadow spills onto the right / bottom frame line; in the selected
        # state that line is checkered like the plate, so it is cleaned too (solid dark: kept)
        xs = range(1, 16 if any(cv.idx(x0 + 15, y0 + y) != 5 for y in range(16)) else 15)
        ys = range(1, 16 if any(cv.idx(x0 + x, y0 + 15) != 5 for x in range(16)) else 15)
        inner = [(x, y) for y in ys for x in xs]
        # the plate is flat or a 2-colour checkerboard: fill each parity with its commonest
        # colour among the pixels that are not lettering (indexes 1-3, 5)
        bg = [collections.Counter(cv.idx(x0 + x, y0 + y) for x, y in inner
                                  if (x + y) % 2 == par and cv.idx(x0 + x, y0 + y) not in (1, 2, 3, 5)).most_common(1)[0][0]
              for par in (0, 1)]
        for x, y in inner:
            if x + y > 8:                   # keep the corner triangle (state mark)
                cv.put(x0 + x, y0 + y, bg[(x + y) % 2])
        w, h, ink = tinyfont.render(text, tinyfont.MID)
        ox, oy = x0 + 1 + (14 - w) // 2, y0 + 7
        for x, y in ink:
            cv.put(ox + x + 1, oy + y + 1, 5)
        for x, y in ink:
            cv.put(ox + x, oy + y, 1)

ITEMS = {
    ('MDATA1/MAINWIN', 11): 'cmdbar',
    ('IDATA1/BGMSEL', 0): 'bgm',
    # officer-screen label tiles (notes/graphics.md): 略/詳, 未/済, aptitudes 農商建登外 (#29 = dim strip,
    # tiles 4-8), unit types 足騎鉄水, 婚/同, stats 政戦采知
    ('CDATA1/COMMON', 22): [dict(kind='pixel', box=(k * 16 + 1, 1, k * 16 + 14, 14), font='small', mode='light',
                                 ink=4, also=[1, 2, 5, 10, 13], shadow=(10, 1, 1)) for k in range(2)],
    ('CDATA1/COMMON', 26): [dict(kind='pixel', box=(k * 16 + 1, 1, k * 16 + 14, 14), font='small', mode='light',
                                 ink=15, also=[1], shadow=(1, 1, 1)) for k in range(2)],
    ('CDATA1/COMMON', 29): kanji_tiles(5, 123, first=4),
    ('CDATA1/COMMON', 31): kanji_tiles(5, 80, grow=1),
    ('CDATA1/COMMON', 34): kanji_tiles(4, 81, also=[14], grow=1),
    ('CDATA1/COMMON', 41): kanji_tiles(2, 66, grow=1),
    ('CDATA1/COMMON', 42): kanji_tiles(4, 55, grow=1),
    # battle marks (notes/gfx_wdata.md): siege attacker / defender side marks (lit + dim cell), formation
    # stance marks defend / move / attack, mid-map castle marks disaster / done
    ('WDATA1/WARMARK', 34): kanji_tiles(2, 30, also=[11, 7]),
    ('WDATA1/WARMARK', 51): kanji_tiles(2, 30, also=[15, 11], grow=1),
    ('WDATA1/WJINKEI', 0): kanji_tiles(1, 35, also=[14], grow=1),
    ('WDATA1/WJINKEI', 4): kanji_tiles(1, 35, also=[14]),
    ('WDATA1/WJINKEI', 5): kanji_tiles(1, 35, also=[14]),
    ('MDATA1/MIDMAP', 2): kanji_tiles(2, 58, ink=1),
    # new-game settings screen: On/Off, Stereo/Mono, Show/Hide, Beginner/Advanced, Appear/Absent (dim, lit)
    ('MDATA1/KANKYOU', 0): grid(4, 5, 96, 24, pair=2),
    # text speed Fast/Normal/Slow, own battles Watch/Ask/Command/Skip (dim, lit)
    ('MDATA1/KANKYOU', 1): grid(7, 2, 64, 24, pair=2),
    # main menu: 8 bars (lit, dim); scenario list: 11 bars "Scenario N  Mon YYYY^Title" (gold plates);
    # the mode menu over the scenario list: 7 bars (lit, dim); new-daimyo start menu: 3 bars (#1 = dim copy)
    ('IDATA1/NEWGAME', 0): grid(2, 8, 232, 24, pair=2),
    ('IDATA1/SCENARIO', 0): grid(2, 11, 256, 24, pair=2, col=112),
    ('IDATA1/SCENARIO', 1): grid(2, 7, 232, 24, pair=2),
    ('IDATA1/NEWDYOU', 0): grid(1, 3, 224, 24),
}
ALIAS = {('MDATA1/MAINWIN', 12): ('MDATA1/MAINWIN', 11),     # same English, other image
         ('IDATA1/NEWDYOU', 1): ('IDATA1/NEWDYOU', 0),
         ('CDATA1/COMMON', 29): ('CDATA1/COMMON', 31)}       # same English, own boxes (dim strip)


EDATA_PAGES = ('BEPAGE1A', 'BEPAGE1B', 'BEPAGE2', 'BEPAGE3', 'BEPAGE4', 'BEPAGE5')
for _p in EDATA_PAGES:                                   # 32x16 buttons #3-#10 (OK/Cancel x2, Next/Back x2)
    for _k in range(3, 11):
        ITEMS['EDATA/' + _p, _k] = [dict(BTN, box=(2, 2, 29, 13))]
for _p, _r in (('BEPAGE1A', range(11, 23)), ('BEPAGE1B', range(11, 19)), ('BEPAGE2', range(11, 27)),
               ('BEPAGE3', range(11, 29)), ('BEPAGE4', range(11, 35)), ('BEPAGE6', range(3, 7))):
    for _k in _r:
        ITEMS['EDATA/' + _p, _k] = field()
ITEMS['EDATA/BEPAGE1A', 13] = ITEMS['EDATA/BEPAGE1A', 19] = [dict(FIELD, box=(3, 3, 88, 17))]   # face-change button
for _k in (11, 12, 13, 16, 19, 20, 21, 24):
    ITEMS['EDATA/BEPAGE5', _k] = field()
for _k in (14, 15, 17, 18, 22, 23, 25, 26):
    ITEMS['EDATA/BEPAGE5', _k] = field(66)
for _k in (0, 2, 3, 4, 7, 9, 10, 11):
    ITEMS['EDATA/KED', _k] = field()
for _k in (5, 6, 12, 13):
    ITEMS['EDATA/KED', _k] = field(66)
ITEMS['EDATA/KED', 1] = ITEMS['EDATA/KED', 8] = [dict(FIELD, box=(3, 3, 48, 17))]   # CG-change button
ITEMS['EDATA/DED', 0] = field(34, 3, 2, 76)          # fame amity ironclad / court cannon (spot 5 unused)
ITEMS['EDATA/SED', 0] = field(36, 3, 2, 84)          # defence gold rice / troops guns horses
for _key in (('EDATA/DED', 3), ('EDATA/EDCOMMON', 0), ('EDATA/EDCOMMON', 1)):
    ITEMS[_key] = [dict(BTN, box=(c * 32 + 2, 2, c * 32 + 29, 13)) for c in range(2)]   # 64x16 OK + Cancel
ITEMS['EDATA/MDMAIN', 0] = grid(1, 4, 224, 24)       # register menu bars (lit; #1/#3/#5 = dim copies)
ITEMS['EDATA/MDMAIN', 2] = grid(1, 3, 224, 24)
ITEMS['EDATA/MDMAIN', 4] = grid(1, 3, 224, 24)
# same English, own pixels: gold (selected) strips -> grey, dim menu bars -> lit, every page file's buttons
# -> BEPAGE1A (BEPAGE4's OK/Cancel pairs are swapped but carry the same text), 64x16 pairs -> DED #3
for _p, _grey, _gold in (('BEPAGE1A', range(11, 17), range(17, 23)), ('BEPAGE1B', range(11, 15), range(15, 19)),
                         ('BEPAGE2', range(11, 19), range(19, 27)), ('BEPAGE3', range(11, 20), range(20, 29)),
                         ('BEPAGE4', range(11, 23), range(23, 35)), ('BEPAGE5', range(11, 19), range(19, 27)),
                         ('KED', range(0, 7), range(7, 14))):
    for _g, _y in zip(_grey, _gold):
        ALIAS['EDATA/' + _p, _y] = ('EDATA/' + _p, _g)
ALIAS['EDATA/DED', 1] = ('EDATA/DED', 0)
ALIAS['EDATA/SED', 1] = ('EDATA/SED', 0)
for _k in (1, 3, 5):
    ALIAS['EDATA/MDMAIN', _k] = ('EDATA/MDMAIN', _k - 1)
for _p in EDATA_PAGES[1:]:
    for _k in range(3, 11):
        ALIAS['EDATA/' + _p, _k] = ('EDATA/BEPAGE1A', _k)
for _key in (('EDATA/DED', 4), ('EDATA/KED', 15), ('EDATA/KED', 16), ('EDATA/SED', 4), ('EDATA/SED', 5)):
    ALIAS[_key] = ('EDATA/DED', 3)

# TDATA1 tutorial (notes/gfx_tdata.md): menus (lit + dim cells), glossary topic bars (4 columns = lit L, dim L,
# lit R, dim R -> pair=2, spot 2r / 2r+1 = left / right topic of row r; #1 = brown copy), map / battle callouts
# (one opaque plate each, arrows outside the box), 8bpp stills (message box, PC-style command bars, navy
# 2-colour labels on orange).
STILL_LABEL = dict(MENU, box=(0, 0, 70, 15), align='left', colours=((205, 205, 205), (98, 98, 49)))
NAVY = dict(mode='dark', colours=((0, 0, 49), None), bold=0.2, sharp=1.0, style={'th': 60})
ITEMS.update({
    ('TDATA1/TMENU', 0): grid(1, 4, 264, 24, inset=3, pair=2),
    ('TDATA1/TMENU', 1): grid(2, 9, 72, 24, inset=3, pair=2),
    ('TDATA1/TPCMENU', 0): grid(4, 11, 136, 16, inset=3, insety=2, pair=2, cap=9),
    ('TDATA1/TMMARKER', 1): [dict(MENU, box=(109, 10, 144, 27))],
    ('TDATA1/TWMARKER', 0): [dict(MENU, box=(3, 43, 38, 60))],
    ('TDATA1/TWMARKER', 1): [dict(MENU, box=(11, 35, 65, 52))],
    ('TDATA1/TWMARKER', 2): [dict(MENU, box=(3, 32, 57, 71))],
    ('TDATA1/TWMARKER', 3): [dict(MENU, box=(3, 35, 57, 74))],
    ('TDATA1/TWMARKER', 4): [dict(MENU, box=(3, 42, 68, 76))],
    ('TDATA1/TSTILL', 2): [dict(box=(9, 8, 262, 24), align='left', colours=((205, 205, 205), None), cap=12)],
    ('TDATA1/TSTILL', 4): [STILL_LABEL] + [dict(MENU, box=(b * 40 + 4, 24, b * 40 + 35, 39)) for b in range(7)],
    ('TDATA1/TSTILL', 5): [STILL_LABEL] + [dict(MENU, box=(b * 40 + 4, 24, b * 40 + 35, 39)) for b in range(5)],
    ('TDATA1/TSTILL', 6): [dict(NAVY, box=(31, 3, 76, 23), cap=11)],
    ('TDATA1/TSTILL', 7): [dict(NAVY, box=(31, 3, 76, 24), cap=11)],
    ('TDATA1/TSTILL', 8): [dict(NAVY, box=(6, 3, 90, 23), cap=15)],
})
for _k in (2, 3, 4, 5):
    ITEMS['TDATA1/TMMARKER', _k] = [dict(MENU, box=(3, 3, 57, 20))]
for _k in (6, 7, 8, 9, 10, 11):
    ITEMS['TDATA1/TMMARKER', _k] = [dict(MENU, box=(3, 3, 38, 20))]
for _k in (12, 13):
    ITEMS['TDATA1/TMMARKER', _k] = [dict(MENU, box=(3, 3, 108, 20))]
ALIAS['TDATA1/TPCMENU', 1] = ('TDATA1/TPCMENU', 0)


def load_tl():
    """-> {(file, tim): {spot: text}}"""
    out = collections.defaultdict(dict)
    if os.path.exists(TL):
        for line in open(TL, encoding='ascii').read().splitlines():
            if line.strip() and not line.startswith('#'):
                k, _, v = line.partition('\t')
                parts = k.split('.')
                out[parts[0], int(parts[1])][int(parts[2]) if len(parts) > 2 else 0] = v
    for k, src in ALIAS.items():
        if src in out:
            out[k] = out[src]
    return out

def redraw(data, key, spots):
    """-> (edited texture, clean texture) for one image of a file."""
    offs = tim.chain(data)
    tex = tim.Texture(data, offs[key[1]])
    cv = Canvas(tex)
    boxes = ITEMS.get(key) or ITEMS.get(ALIAS.get(key, key), [{}])
    if boxes == 'bgm':
        for spot, text in sorted(spots.items()):
            if spot >= BGM_TRACKS:
                raise ValueError('no spot %d' % spot)
            bgm_tile(cv, text, spot)
        return tex, tim.Texture(data, offs[key[1]])
    if boxes == 'cmdbar':
        for y in range(tex.h):
            for x in range(tex.w):
                tex.put(x, y, 0)
        for spot, text in sorted(spots.items()):
            if spot >= CMD_COLS * CMD_ROWS:
                raise ValueError('no spot %d' % spot)
            cmd_cell(cv, text, spot, *CMD_INK[key[1]])
        return tex, tim.Texture(data, offs[key[1]])
    for spot, text in sorted(spots.items()):
        if spot >= len(boxes):
            raise ValueError('no spot %d' % spot)
        o = dict(boxes[spot])
        kind = o.pop('kind', 'plate')
        for box in o.pop('boxes', [o.get('box')]):     # several cells with the same label
            if box is not None:
                o['box'] = box
            if kind == 'plate':
                plate(cv, text, **o)
            elif kind == 'pixel':
                pixel(cv, text, **o)
            elif kind == 'engrave':
                engrave(cv, text, **o)
            else:
                raise ValueError(kind)
    return tex, tim.Texture(data, offs[key[1]])

def apply(file_fn):
    """-> {disc path: new bytes}; file_fn(path) = clean file bytes (path like 'MDATA1/MAINWIN.NB8')."""
    by_file = collections.defaultdict(dict)
    for (f, t), spots in load_tl().items():
        by_file[f][t] = spots
    out = {}
    for f, images in sorted(by_file.items()):
        path = f + '.NB8'
        clean = file_fn(path)
        buf = bytearray(clean)
        for t, spots in sorted(images.items()):
            try:
                redraw(clean, (f, t), spots)[0].store(buf)
            except ValueError as err:
                raise ValueError('gfx %s.%d: %s' % (f, t, err))
        assert len(buf) == len(clean)
        out[path] = bytes(buf)
    return out

def preview(out, prefixes=None, scale=2, root=os.path.join(ROOT, 'extract', 'jp')):
    """Before/after sheet of the tl/gfx.txt items (dev; Koei data, keep out of git)."""
    cells = []
    for (f, t), spots in sorted(load_tl().items()):
        k = '%s.%d' % (f, t)
        if prefixes and not any(k.startswith(p) for p in prefixes):
            continue
        d = open(os.path.join(root, f + '.NB8'), 'rb').read()
        try:
            b, a = redraw(d, (f, t), spots)
        except ValueError as err:
            print('%s: %s' % (k, err))
            continue
        pair = [x.image().resize((x.w * scale, x.h * scale), Image.NEAREST) for x in (a, b)]
        cells.append((k, pair))
    W = max([1200] + [a.width * 2 + 8 for _, (a, b) in cells])
    x = y = rowh = 0
    pos = []
    for lab, (a, b) in cells:
        cw = a.width + b.width + 8
        if x + cw > W:
            x, y, rowh = 0, y + rowh + 16, 0
        pos.append((x, y))
        x += cw + 10
        rowh = max(rowh, a.height)
    sheet = Image.new('RGBA', (W, y + rowh + 16), (60, 0, 60, 255))
    dr = ImageDraw.Draw(sheet)
    for (lab, (a, b)), (px, py) in zip(cells, pos):
        dr.text((px, py), lab, fill=(255, 255, 0, 255))
        sheet.alpha_composite(a, (px, py + 12))
        sheet.alpha_composite(b, (px + a.width + 8, py + 12))
    sheet.save(out)
    print(out, len(cells))

if __name__ == '__main__':
    # gfx.py preview out.png [key prefixes...] [--scale N]
    a = sys.argv[3:]
    sc = 2
    if '--scale' in a:
        k = a.index('--scale'); sc = int(a[k + 1]); del a[k:k + 2]
    preview(sys.argv[2], a, sc)
