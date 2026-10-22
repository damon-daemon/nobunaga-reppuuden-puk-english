"""Names: the records keep their Japanese, the text renderer shows English.

The officer records of the scenario files (SDATA1/SCES00-10.NB8: 900 records of 0x50,
family name [9] at +0, given name [9] at +9, SJIS, NUL padded) are slot-stable: a slot is
the same person in every scenario, but 115 slots change name by era (木下/羽柴/豊臣 秀吉,
長尾景虎/上杉謙信...). English does not fit 9 bytes (40% of given names are longer than
8 letters), so nothing is written there: tools/namedict.py packs a Japanese -> English
dictionary into free RAM and the print loop replaces names while drawing.

tl/names/officers.json: one row per line, {"id": slot, "en": [family, given]} for the name
the slot has in most scenarios, plus "sN": [family, given] for scenario N (0-10) where the
Japanese differs from that one; "full": English of the whole name when it is not
"family given" (then the parts are not composed, unless the row has "parts": true: then the
family and given names alone still get their English, e.g. a clan short form "Yamanouchi" for 山内上杉 with
the person's full name "Uesugi Norimasa").

Readings: the officer bios (message blocks 16-32, 61: slot k = block 16 + k / 50, entry
2 * (k % 50); blocks 61-63 continue with slots 850+ and the PUK past heroes) start with
"NAME {1B}kREADING{1B}H years".

Other name tables (TABLES; tl/names/<table>.json rows {"id", "en", "sN"?}; "full" = the long name where
"en" is a short form for narrow list columns (treasures ~108 px, court titles ~84 px), "short" = Soutenroku's, reference only): castles (SCES
0x139A0, 160 slots of 0x5E, name [13], by scenario), branch-castle sites (program table
0x800C16F8: 240 x {u32 map xy, char *name}), and SCEF00 (the same in every SCEF file):
provinces, treasures, court titles, shogunate offices. Rows without English yet are left
out of the dictionary (drawn in Japanese) and listed by `check`.

  names.py export        script/names/officers.json (Japanese per scenario, reading, English)
  names.py init          write tl/names/officers.json proposals (Soutenroku names, else the
                         romanised reading) for the slots it does not have yet
  names.py check         validate tl/names against the disc
  names.py pairs         print the Japanese -> English pairs
  names.py find TEXT...  pairs whose Japanese or English contains TEXT
"""
import collections, json, os, re, sys, unicodedata

sys.path.insert(0, os.path.dirname(__file__))
import iso, msgfile, setup, text

SCES = ['SDATA1/SCES%02d.NB8' % i for i in range(11)]
OFFICERS, OFFICER_SIZE = 900, 0x50
TEMPLATES, TEMPLATE_SIZE = 0x11940, 0x54          # 18 generic records (姫武将①.., 一向一揆①.., だみい)
TL_NAMES = os.path.join(setup.TL, 'names')
BIO_RE = re.compile(r'(\S+) \{1B\}k(.+?)\{1B\}H')
SOUTENROKU = os.path.expanduser('~/Projects/nobunaga_soutenroku_puk_translation')


def field(b):
    return text.decode(b.split(b'\0')[0])


# table: (file(s), offset, record size, count, name field size); 'SCES' = per scenario,
# 'EXE' = pointer table in the program
TABLES = {
    'castles': ('SCES', 0x139A0, 0x5E, 160, 13),
    'sites': ('EXE', 0x800C16F8, 8, 240, None),
    'provinces': ('SDATA1/SCEF00.NB8', 0x3C28, 0x0F, 40, 9),
    'treasures': ('SDATA1/SCEF00.NB8', 0x1518, 0x14, 350, 13),
    'courttitles': ('SDATA1/SCEF00.NB8', 0x3070, 0x0C, 250, 11),
    'offices': ('SDATA1/SCEF00.NB8', 0x3E80, 0x0E, 40, 13),
}
EXE_BASE = 0x80010000 - 0x800


def table(disc, name, recs=None):
    """[(default Japanese, {scenario: Japanese} where it differs)] per row"""
    recs = recs or disc.records()
    src, off, size, n, fl = TABLES[name]
    if src == 'EXE':
        exe = disc.file(iso.EXE_NAME, recs)
        out = []
        for i in range(n):
            p = int.from_bytes(exe[off - EXE_BASE + size * i + 4:][:4], 'little') - EXE_BASE
            out.append((field(exe[p:exe.index(b'\0', p)]), {}))
        return out
    files = SCES if src == 'SCES' else [src]
    data = [disc.file(f, recs) for f in files]
    out = []
    for i in range(n):
        names = [field(d[off + size * i:off + size * i + fl]) for d in data]
        top = collections.Counter(names).most_common(1)[0][0]
        out.append((top, {k: x for k, x in enumerate(names) if x != top}))
    return out


def table_pairs(disc, recs=None):
    """([(japanese, english, tier 0)], [missing]) of the TABLES"""
    out, missing = [], []
    for name in TABLES:
        jp = table(disc, name, recs)
        rows = {r['id']: r for r in load(name)}
        for i, (top, diff) in enumerate(jp):
            r = rows.get(i, {})
            for k, j in [(None, top)] + sorted(diff.items()):
                e = r.get('en') if k is None else r.get('s%d' % k)
                if not j:
                    continue
                if e:
                    out.append((j, e, 0))
                else:
                    missing.append('%s %d%s %s' % (name, i, '' if k is None else ' s%d' % k, j))
    return out, missing


def scenario_officers(disc, recs=None):
    """[scenario][slot] = (family, given)"""
    recs = recs or disc.records()
    out = []
    for name in SCES:
        d = disc.file(name, recs)
        out.append([(field(d[o:o + 9]), field(d[o + 9:o + 18]))
                    for o in range(0, OFFICERS * OFFICER_SIZE, OFFICER_SIZE)])
    return out


def officers(disc, recs=None):
    """[(default (family, given), {scenario: (family, given)} where it differs)] per slot"""
    scen = scenario_officers(disc, recs)
    out = []
    for i in range(OFFICERS):
        names = [s[i] for s in scen]
        common = collections.Counter(names).most_common()
        top = common[0][0]
        out.append((top, {k: n for k, n in enumerate(names) if n != top}))
    return out


def bio_readings(blocks):
    """{full Japanese name: half-width katakana reading} from the bio headers"""
    out = {}
    for b in list(range(16, 33)) + [61, 62, 63]:
        for e in blocks[b]:
            m = BIO_RE.match(text.decode(e)) if e else None
            if m:
                out.setdefault(m.group(1), m.group(2))
    return out


def load(name):
    path = os.path.join(TL_NAMES, name + '.json')
    if not os.path.exists(path):
        return []
    return json.load(open(path, encoding='utf-8'))


def dump_rows(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('[\n' + ',\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n]\n')


def row_names(jp, row):
    """[(japanese (family, given), english [family, given], full or None)] of one tl row"""
    top, diff = jp
    out = [(top, row['en'], row.get('full'))]
    for k, n in sorted(diff.items()):
        e = row.get('s%d' % k)
        if e:
            out.append((n, e[:2], e[2] if len(e) > 2 else None))
    return out


def officer_pairs(jp, rows):
    """[(japanese, english, tier)]: full name (tier 0: must agree), family and given name
    alone (tier 1: on a conflict the most common English wins; the others keep their full
    name as a whole entry, tools/namedict.py stores what does not compose)"""
    out = []
    for r in rows:
        for (fam, giv), (efam, egiv), full in row_names(jp[r['id']], r):
            out.append((fam + giv, full or ('%s %s' % (efam, egiv)).strip(), 0))
            if not full or r.get('parts'):
                out.append((fam, efam, 1))
                out.append((giv, egiv, 1))
    return [(j, e, t) for j, e, t in out if j and e]


PLACE_SUFFIXES = (' Castle', ' Fort', ' Hall')       # English castle suffixes 城 砦 館 (tables.txt)


def pairs(disc, recs=None, quiet=True):
    """{japanese text: english}; raises ValueError on conflicts and missing rows."""
    jp = officers(disc, recs)
    rows = load('officers')
    errs, soft = [], []
    have = {r['id']: r for r in rows}
    for i, (top, diff) in enumerate(jp):
        if i not in have:
            errs.append('officers: no English for %d %s%s' % (i, *top))
            continue
        for k in diff:
            if 's%d' % k not in have[i]:
                errs.append('officers: no English for %d s%d %s%s' % (i, k, *diff[k]))
    cand = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    tabs, missing = table_pairs(disc, recs)
    for j, e, t in officer_pairs(jp, rows) + tabs:
        if not e.isascii() or not e.strip():
            errs.append('officers: bad English for %s: %r' % (j, e))
        cand[j][t][e] += 1
    out = {}
    for j, tiers in cand.items():
        t = min(tiers)
        c = tiers[t].most_common()
        if len(c) > 1:
            if t == 0:
                errs.append('conflict: %s = %s' % (j, ' / '.join(e for e, _ in c)))
            else:
                soft.append('%s: %s kept, %s whole names' % (j, c[0][0], ', '.join(e for e, _ in c[1:])))
        out[j] = c[0][0]
    # one-kanji castle/site names (内 林 萩 忍 ...) match only as a whole line (namedict.build), but lists
    # draw name + English suffix (castle suffix table, tl/progstr/tables.txt): literal entries for those
    for name in ('castles', 'sites'):
        for top, diff in table(disc, name, recs):
            for j in [top] + list(diff.values()):
                if j and len(j) == 1 and j in out:
                    for suf in PLACE_SUFFIXES:
                        out[j + suf] = out[j] + suf
    if errs:
        raise ValueError('\n'.join(errs))
    if not quiet and soft:
        print('name parts with several readings:\n  ' + '\n  '.join(soft))
    if not quiet and missing:
        print('%d table names without English (drawn in Japanese):\n  %s' % (len(missing), '\n  '.join(missing)))
    return out


# name parts that are also common words in the game's text: they match only inside a full name
NOT_ALONE = {'政治', '忠義', '本城', '留守', '田原'}
# one-kanji names that never occur as a word in the script: matched anywhere (list rows are not whole lines)
ALONE_OK = {'泪'}


def compose_sets(disc, recs=None):
    """(family names, given names) that tools/namedict.py may join as "Family Given"."""
    jp = officers(disc, recs)
    fam, giv = set(), set()
    for r in load('officers'):
        for (f, g), _, full in row_names(jp[r['id']], r):
            if not full:
                fam.add(f)
                giv.add(g)
    return fam, giv


# ---------------------------------------------------------------- proposals (dev only)

KANA = {
    'きゃ': 'kya', 'きゅ': 'kyu', 'きょ': 'kyo', 'しゃ': 'sha', 'しゅ': 'shu', 'しょ': 'sho',
    'ちゃ': 'cha', 'ちゅ': 'chu', 'ちょ': 'cho', 'にゃ': 'nya', 'にゅ': 'nyu', 'にょ': 'nyo',
    'ひゃ': 'hya', 'ひゅ': 'hyu', 'ひょ': 'hyo', 'みゃ': 'mya', 'みゅ': 'myu', 'みょ': 'myo',
    'りゃ': 'rya', 'りゅ': 'ryu', 'りょ': 'ryo', 'ぎゃ': 'gya', 'ぎゅ': 'gyu', 'ぎょ': 'gyo',
    'じゃ': 'ja', 'じゅ': 'ju', 'じょ': 'jo', 'ぢゃ': 'ja', 'ぢゅ': 'ju', 'ぢょ': 'jo',
    'びゃ': 'bya', 'びゅ': 'byu', 'びょ': 'byo', 'ぴゃ': 'pya', 'ぴゅ': 'pyu', 'ぴょ': 'pyo',
    'てぃ': 'ti', 'でぃ': 'di', 'ふぁ': 'fa', 'ふぃ': 'fi', 'ふぇ': 'fe', 'ふぉ': 'fo',
    'うぃ': 'wi', 'うぇ': 'we', 'うぉ': 'wo', 'しぇ': 'she', 'じぇ': 'je', 'ちぇ': 'che',
    'あ': 'a', 'い': 'i', 'う': 'u', 'え': 'e', 'お': 'o',
    'か': 'ka', 'き': 'ki', 'く': 'ku', 'け': 'ke', 'こ': 'ko',
    'さ': 'sa', 'し': 'shi', 'す': 'su', 'せ': 'se', 'そ': 'so',
    'た': 'ta', 'ち': 'chi', 'つ': 'tsu', 'て': 'te', 'と': 'to',
    'な': 'na', 'に': 'ni', 'ぬ': 'nu', 'ね': 'ne', 'の': 'no',
    'は': 'ha', 'ひ': 'hi', 'ふ': 'fu', 'へ': 'he', 'ほ': 'ho',
    'ま': 'ma', 'み': 'mi', 'む': 'mu', 'め': 'me', 'も': 'mo',
    'や': 'ya', 'ゆ': 'yu', 'よ': 'yo', 'ら': 'ra', 'り': 'ri', 'る': 'ru', 'れ': 're', 'ろ': 'ro',
    'わ': 'wa', 'ゐ': 'i', 'ゑ': 'e', 'を': 'o', 'ん': 'n', 'ゔ': 'vu',
    'が': 'ga', 'ぎ': 'gi', 'ぐ': 'gu', 'げ': 'ge', 'ご': 'go',
    'ざ': 'za', 'じ': 'ji', 'ず': 'zu', 'ぜ': 'ze', 'ぞ': 'zo',
    'だ': 'da', 'ぢ': 'ji', 'づ': 'zu', 'で': 'de', 'ど': 'do',
    'ば': 'ba', 'び': 'bi', 'ぶ': 'bu', 'べ': 'be', 'ぼ': 'bo',
    'ぱ': 'pa', 'ぴ': 'pi', 'ぷ': 'pu', 'ぺ': 'pe', 'ぽ': 'po',
    'ぁ': 'a', 'ぃ': 'i', 'ぅ': 'u', 'ぇ': 'e', 'ぉ': 'o',
}


def hiragana(s):
    s = unicodedata.normalize('NFKC', s)
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in s)


def syllables(kana):
    out, i = [], 0
    while i < len(kana):
        if kana[i] == 'っ' and i + 1 < len(kana):
            nxt = KANA.get(kana[i + 1:i + 3]) or KANA.get(kana[i + 1], '')
            out.append('t' if nxt.startswith('ch') else nxt[:1])
            i += 1
        elif kana[i:i + 2] in KANA:
            out.append(KANA[kana[i:i + 2]])
            i += 2
        elif kana[i] == 'ー':
            out.append('')
            i += 1
        else:
            out.append(KANA.get(kana[i], kana[i]))
            i += 1
    return out


def romaji(kana, given=False):
    """Hepburn, long vowels short (Soutenroku tools/names.py romaji, same rules)."""
    syl = syllables(hiragana(kana))
    out, long_ = '', False
    for j, s in enumerate(syl):
        nxt = syl[j + 1] if j + 1 < len(syl) else ''
        prev = out[-1:] if out else ''
        rest = ''.join(syl[j + 1:])
        if not long_ and s == 'u' and prev in ('o', 'u'):
            keep = (prev == 'o' and nxt[:1] == 'e' and not rest.startswith('emon')
                    or given and prev == 'u' and rest == 'ji' and 'y' not in out[-3:]
                    and out[-3:] not in ('shu', 'chu') and out[-2:] != 'ju')
            if not keep:
                long_ = True
                continue
        if not long_ and s == 'o' and prev == 'o':
            long_ = True
            continue
        long_ = False
        out += s
    return out.capitalize()


def sources():
    """({(family, given): [family, given]}, {family: English}, {given: English}) from the earlier
    projects, Soutenroku first, then Taiko 5, then Nobunaga DS 2"""
    full = {}
    fam, giv = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)

    def add(names, en, weight):
        if not all(en):
            return
        if not weight:                                  # S6: whole names only
            full.setdefault(tuple(names), list(en))
            return
        full.setdefault(tuple(names), list(en))
        fam[names[0]][en[0]] += weight
        giv[names[1]][en[1]] += weight

    def read(path):
        return json.load(open(os.path.join(os.path.dirname(SOUTENROKU), path), encoding='utf-8'))

    en = {r['id']: r for r in read('nobunaga_soutenroku_puk_translation/tl/names/persons.json')}
    for r in read('nobunaga_soutenroku_puk_translation/script/names/persons.json'):
        e = en.get(r['id'])
        if e:
            add(r['jp'], e['en'], 100)
            for k, n in r.get('jp_scen', {}).items():
                add(n, e.get(k, e['en']), 100)
    en = {r['id']: r['en'] for r in read('taiko_risshiden_5_translation/tl/names_persons.json')}
    for r in read('taiko_risshiden_5_translation/script/names_persons.json'):
        for n in {tuple(v) for v in r['jp'].values()}:
            if r['id'] in en:
                add(n, en[r['id']], 10)
    en = {r['id']: r['en'] for r in read('nobunaga_ds_2_translation/tl/names_officers.json')}
    for r in read('nobunaga_ds_2_translation/script/names_officers.json'):
        if r['id'] in en:
            add(r['jp'], en[r['id']], 1)
    for r in read('sangokushi_6_puk_translation/script/names/officers.json'):   # PUK heroes of China
        if r.get('en') and r['jp'][0] and r['jp'][1]:
            add(r['jp'][:2], r['en'][:2], 0)
    best = lambda m: {k: c.most_common(1)[0][0] for k, c in m.items()}
    return full, best(fam), best(giv)


def squash(s):
    """letters only, long vowels short (Yamanouchi = Yamanochi, Kazuuji = Kazuji)"""
    s = re.sub(r'[^a-z]', '', s.lower())
    return s.replace('ou', 'o').replace('oo', 'o').replace('uu', 'u')


def split_kana(kana, efam, egiv=None):
    """(family kana, given kana) where the family part romanises to efam (the longest such
    part, or the one whose given part romanises to egiv), else None"""
    k = hiragana(kana)
    cands = [(k[:n], k[n:]) for n in range(len(k) - 1, 0, -1) if squash(romaji(k[:n])) == squash(efam)]
    for c in cands:
        if egiv and squash(romaji(c[1], True)) == squash(egiv):
            return c
    return cands[0] if cands else None


def propose(name, reading, src):
    """([family, given], note) for one Japanese (family, given). The game's own reading
    wins over the earlier projects (glossary rule); their spelling is kept where it agrees."""
    full, sfam, sgiv = src
    fam, giv = name
    e = full.get(name)
    efam = (e or [None])[0] or sfam.get(fam)
    if reading and efam:
        sp = split_kana(reading, efam, (e or [None, sgiv.get(giv)])[1])
        if not sp and e:
            for n in range(1, len(hiragana(reading))):      # the game reads the family differently
                g = romaji(hiragana(reading)[n:], True)
                if squash(g) == squash(e[1]):
                    return [romaji(hiragana(reading)[:n]), e[1]], 'family read differently: %s' % e[0]
        if sp:
            g = romaji(sp[1], True)
            if e and squash(g) == squash(e[1]):
                return e, ''
            return [efam, g], ('given read differently: %s' % e[1]) if e else 'romaji'
    if e:
        return e, ('reading %s does not match' % reading) if reading and fam in sfam else ''
    if not reading:
        return [efam or '?', sgiv.get(giv, '?')], 'no reading'
    return [romaji(reading), ''], 'romaji, split by hand'


ST_TABLE = {'castles': 'castles', 'sites': 'castles', 'provinces': 'provinces', 'treasures': 'treasures',
            'courttitles': 'courttitles', 'offices': 'offices'}


def st_table(name):
    """{japanese: (english, short or None)} of a Soutenroku name table"""
    def read(path):
        return json.load(open(os.path.join(SOUTENROKU, path), encoding='utf-8'))
    en = {r['id']: r for r in read('tl/names/%s.json' % name)}
    out = {}
    for r in read('script/names/%s.json' % name):
        e = en.get(r['id'])
        if not e:
            continue
        one = lambda j: j[0] if isinstance(j, list) else j
        out.setdefault(one(r['jp']), (e['en'], e.get('short')))
        for k, j in r.get('jp_scen', {}).items():
            out.setdefault(one(j), (e.get(k, e['en']), None))
    return out


def init_tables(disc, recs):
    for name in TABLES:
        st = st_table(ST_TABLE[name])
        rows = {r['id']: r for r in load(name)}
        new = 0
        for i, (top, diff) in enumerate(table(disc, name, recs)):
            r = rows.setdefault(i, {'id': i})
            for k, j in [(None, top)] + sorted(diff.items()):
                key = 'en' if k is None else 's%d' % k
                if key not in r and j in st:
                    r[key] = st[j][0]
                    if k is None and st[j][1]:
                        r['short'] = st[j][1]
                    new += 1
        dump_rows(os.path.join(TL_NAMES, name + '.json'), [rows[k] for k in sorted(rows)])
        print('%s: %d names from Soutenroku' % (name, new))


def init(disc, recs=None):
    recs = recs or disc.records()
    init_tables(disc, recs)
    jp = officers(disc, recs)
    blocks = msgfile.parse(disc.file(msgfile.NAME, recs), disc.file(iso.EXE_NAME, recs))
    reads = bio_readings(blocks)
    src = sources()
    rows = {r['id']: r for r in load('officers')}
    notes = []
    for i, (top, diff) in enumerate(jp):
        r = rows.get(i)
        if r is None:
            e, n = propose(top, reads.get(''.join(top)), src)
            r = rows[i] = {'id': i, 'en': e}
            if n:
                notes.append('%d %s%s: %s %s' % (i, *top, e, n))
        for k, nm in sorted(diff.items()):
            if 's%d' % k not in r:
                e, n = propose(nm, reads.get(''.join(nm)), src)
                r['s%d' % k] = e
                if n:
                    notes.append('%d s%d %s%s: %s %s' % (i, k, *nm, e, n))
    dump_rows(os.path.join(TL_NAMES, 'officers.json'), [rows[k] for k in sorted(rows)])
    print('\n'.join(notes))
    print('%d notes' % len(notes))


def export(disc, recs=None):
    recs = recs or disc.records()
    jp = officers(disc, recs)
    blocks = msgfile.parse(disc.file(msgfile.NAME, recs), disc.file(iso.EXE_NAME, recs))
    reads = bio_readings(blocks)
    en = {r['id']: r for r in load('officers')}
    rows = []
    for i, (top, diff) in enumerate(jp):
        row = {'id': i, 'jp': list(top), 'kana': reads.get(''.join(top), '')}
        for k, n in sorted(diff.items()):
            row['s%d' % k] = list(n)
        if i in en:
            row['en'] = en[i]['en']
        rows.append(row)
    dump_rows(os.path.join(setup.SCRIPT, 'names', 'officers.json'), rows)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    disc = iso.Disc(setup.CLEAN)
    recs = disc.records()
    if cmd == 'export':
        export(disc, recs)
    elif cmd == 'init':
        init(disc, recs)
    elif cmd == 'check':
        p = pairs(disc, recs, quiet=False)
        print('%d pairs OK' % len(p))
    elif cmd == 'pairs':
        for j, e in sorted(pairs(disc, recs).items()):
            print('%s\t%s' % (j, e))
    elif cmd == 'find':
        for j, e in sorted(pairs(disc, recs).items()):
            if any(q in j or q.lower() in e.lower() for q in sys.argv[2:]):
                print('%s\t%s' % (j, e))
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
