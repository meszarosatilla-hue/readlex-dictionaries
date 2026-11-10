"""Builds the downloadable dictionary packs of ReadLex.

    python3 tools/build.py <work dir> <output dir>       download the sources and build every pack
    python3 tools/build.py --test <output dir>           build from the small files in tests/

Every pack is one ".rld" file (format below, read by core/Rld.java in the app); index.json lists them.

Pack types
    def    word → definitions            payload: entries RS, fields US: word, pos, ipa, senses (GS)
    tr     word → translations           payload: entries RS, fields US: pos, groups (GS); group = [gloss FS] words ", "
    forms  word form → base form(s)      payload: "lemma|lemma"
A gloss "~" marks equivalents collected in the reverse direction (from the target language's entries).
"""
import gzip
import hashlib
import io
import json
import os
import re
import struct
import sys
import tarfile
import time
import traceback
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib

RS, US, GS, FS = '\x1e', '\x1f', '\x1d', '\x1c'
REVERSE = '~'
FORMAT_VERSION = 1

SKIP_POS = {'name', 'character', 'symbol', 'punct', 'prefix', 'suffix', 'infix', 'interfix', 'affix',
            'circumfix', 'romanization', 'proverb'}
OLD_TAGS = {'obsolete', 'archaic', 'dated', 'historical'}
MAX_SENSES = 5
MAX_SENSE_LEN = 220
MAX_WORDS_IN_HEAD = 4

# Languages of the packs: ISO code → name on kaikki.org, FreeDict code
LANGS = {
    'hu': ('Hungarian', 'hun'), 'de': ('German', 'deu'), 'fr': ('French', 'fra'), 'es': ('Spanish', 'spa'),
    'it': ('Italian', 'ita'), 'pt': ('Portuguese', 'por'), 'nl': ('Dutch', 'nld'), 'pl': ('Polish', 'pol'),
    'cs': ('Czech', 'ces'), 'sk': ('Slovak', 'slk'), 'ro': ('Romanian', 'ron'), 'la': ('Latin', 'lat'),
}
FD = {v[1]: k for k, v in LANGS.items()}
FD['eng'] = 'en'

UA = 'ReadLex dictionary builder (+https://github.com/meszarosatilla-hue/readlex-dictionaries)'
HERE = os.path.dirname(os.path.abspath(__file__))


def note(msg):
    """Shown as an annotation on the GitHub Actions run (readable without the log)."""
    print(msg, flush=True)
    if os.environ.get('GITHUB_ACTIONS'):
        print('::notice::' + msg.replace('\n', ' '), flush=True)


def warn(msg):
    print(msg, flush=True)
    if os.environ.get('GITHUB_ACTIONS'):
        print('::warning::' + msg.replace('\n', ' '), flush=True)


# ---------------------------------------------------------------------- shared with the app (core/Lemma.java)

def key(word, lang='en'):
    """Same as Lemma.key (Java). Latin: without macrons and breves (books rarely print them)."""
    s = word.strip().lower()
    for a, b in (('\u2019', "'"), ('\u2018', "'"), ('\u02bc', "'"), ('\u2010', '-'), ('\u2011', '-')):
        s = s.replace(a, b)
    if lang == 'la':
        s = ''.join(c for c in unicodedata.normalize('NFD', s) if c not in '\u0304\u0306')
        s = unicodedata.normalize('NFC', s)
    return s


def candidates(w):
    """Exact copy of Lemma.candidates (Java): rule-based English base forms."""
    out = []

    def add(c):
        if len(c) >= 2 and c != w and c not in out:
            out.append(c)

    n = len(w)
    if w.endswith("'s"):
        add(w[:n - 2])
    if w.endswith("s'"):
        add(w[:n - 1])
    if w.endswith('ies') and n > 4:
        add(w[:n - 3] + 'y')
    if w.endswith('ves') and n > 4:
        add(w[:n - 3] + 'f')
        add(w[:n - 3] + 'fe')
    if w.endswith('es') and n > 3:
        add(w[:n - 2])
    if w.endswith('s') and not w.endswith('ss') and n > 3:
        add(w[:n - 1])
    if w.endswith('ied') and n > 4:
        add(w[:n - 3] + 'y')
    if w.endswith('ed') and n > 3:
        add(w[:n - 2])
        add(w[:n - 1])
        if n > 4 and w[n - 3] == w[n - 4]:
            add(w[:n - 3])
    if w.endswith('ing') and n > 4:
        s = w[:n - 3]
        add(s)
        add(s + 'e')
        m = len(s)
        if m > 2 and s[m - 1] == s[m - 2]:
            add(s[:m - 1])
        if s.endswith('y'):
            add(s[:m - 1] + 'ie')
    if w.endswith('ier') and n > 4:
        add(w[:n - 3] + 'y')
    if w.endswith('iest') and n > 5:
        add(w[:n - 4] + 'y')
    if w.endswith('er') and n > 3:
        add(w[:n - 2])
        add(w[:n - 1])
        if n > 4 and w[n - 3] == w[n - 4]:
            add(w[:n - 3])
    if w.endswith('est') and n > 4:
        add(w[:n - 3])
        add(w[:n - 2])
        if n > 5 and w[n - 4] == w[n - 5]:
            add(w[:n - 4])
    if w.endswith('ily') and n > 4:
        add(w[:n - 3] + 'y')
    if w.endswith('ly') and n > 4:
        add(w[:n - 2])
    return out


# ---------------------------------------------------------------------- downloading

def fetch(url, dest, headers=None):
    if os.path.exists(dest):
        return dest
    print('download', url, flush=True)
    h = {'User-Agent': UA}
    h.update(headers or {})
    t = time.time()
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=180) as r, open(dest + '.part', 'wb') as f:
                n = 0
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
                    n += len(b)
            os.replace(dest + '.part', dest)
            print('  %.1f MB in %.0f s' % (n / 1e6, time.time() - t), flush=True)
            return dest
        except Exception as e:
            last = e
            print('  failed:', e, flush=True)
            if '404' in str(e) or '403' in str(e):
                break
            time.sleep(15 * (attempt + 1))
    raise IOError('%s: %s' % (url, last))


def text_of(url, headers=None):
    h = {'User-Agent': UA}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
        return r.read().decode('utf-8', 'replace')


def kaikki(lang, work):
    """kaikki.org's extract of one language: all its entries in the English Wiktionary."""
    dest = os.path.join(work, lang + '.jsonl')
    for d in (dest, dest + '.gz'):
        if os.path.exists(d):
            return d
    base = 'https://kaikki.org/dictionary/%s/' % lang
    urls = []
    try:
        html = text_of(base + 'index.html')
        for href in re.findall(r'href="([^"]+\.jsonl(?:\.gz)?)"', html):
            u = urllib.parse.urljoin(base, href)
            if 'senses' not in u.lower() and u not in urls:
                urls.append(u)
    except Exception as e:  # the page layout may change: fall back to the known names
        print('  index page:', e, flush=True)
    for u in (base + 'kaikki.org-dictionary-%s.jsonl' % lang, base + 'kaikki.org-dictionary-%s.jsonl.gz' % lang):
        if u not in urls:
            urls.append(u)
    for u in urls:
        try:
            return fetch(u, dest + ('.gz' if u.endswith('.gz') else ''))
        except IOError:
            continue
    raise IOError('no %s data on kaikki.org (tried %s)' % (lang, ', '.join(urls)))


def jsonl(path):
    with open(path, 'rb') as f:
        gz = f.read(2) == b'\x1f\x8b'
    f = gzip.open(path, 'rt', encoding='utf-8') if gz else open(path, encoding='utf-8')
    with f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    pass


# ---------------------------------------------------------------------- helpers

def clean(s):
    s = re.sub(r'\s+', ' ', s or '').strip()
    for c in (RS, US, GS, FS, '\t', '\n', '\r'):
        s = s.replace(c, ' ')
    return s


def short(s, n):
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(' ', 1)[0].rstrip(',;:')
    return cut + '…'


def head_ok(word):
    if not word or len(word) > 60 or len(word.split()) > MAX_WORDS_IN_HEAD:
        return False
    if len(key(word).encode('utf-8')) > 250:
        return False
    return any(c.isalpha() for c in word)


def dedupe(seq):
    seen = set()
    return [x for x in seq if not (x in seen or seen.add(x))]


def fix_hu(s):
    """Old 8-bit Hungarian texts: õ/û instead of ő/ű."""
    return s.replace('õ', 'ő').replace('û', 'ű').replace('Õ', 'Ő').replace('Û', 'Ű')


class Tr:
    """Translations of headwords: key → {pos: [(gloss, [words])]}."""

    def __init__(self, lang=''):
        self.d = {}
        self.lang = lang

    def add(self, k, pos, gloss, word, max_groups=8, max_words=8):
        if self.lang == 'hu':
            word = fix_hu(word)
        groups = self.d.setdefault(k, {}).setdefault(pos, [])
        for g in groups:
            if g[0] == gloss:
                if word not in g[1] and len(g[1]) < max_words:
                    g[1].append(word)
                return
        if len(groups) < max_groups:
            groups.append((gloss, [word]))

    def payloads(self):
        out = {}
        for k, by_pos in self.d.items():
            parts = []
            for pos, groups in by_pos.items():
                gs = [(g[0] + FS if g[0] else '') + ', '.join(g[1]) for g in groups if g[1]]
                if gs:
                    parts.append(clean(pos) + US + GS.join(gs))
            if parts:
                out[k] = RS.join(parts)
        return out


# ---------------------------------------------------------------------- Wiktionary (kaikki.org)

def sense_text(sn):
    raw = sn.get('raw_glosses') or sn.get('glosses') or []
    return clean(raw[-1]) if raw else ''


def ipa_of(e):
    best = ''
    for s in e.get('sounds') or []:
        ipa = s.get('ipa')
        if not ipa:
            continue
        if set(s.get('tags') or []) & {'US', 'General-American'}:
            return clean(ipa)
        if not best:
            best = clean(ipa)
    return best


def is_lang(t, code, name):
    return t.get('lang_code') == code or t.get('code') == code or t.get('lang') == name


def english_pass(en_path, targets):
    """English entries: definitions, the form table, and translations into every target language."""
    defs = {}
    trs = {t: Tr(t) for t in targets}
    rel = {}
    n_en = 0
    for e in jsonl(en_path):
        if not is_lang(e, 'en', 'English'):
            continue
        word = e.get('word') or ''
        pos = clean(e.get('pos') or '')
        if pos in SKIP_POS or not head_ok(word):
            continue
        n_en += 1
        k = key(word)
        senses, old = [], []
        for sn in e.get('senses') or []:
            tags = set(sn.get('tags') or [])
            for b in (sn.get('form_of') or []) + (sn.get('alt_of') or []):
                bw = b.get('word')
                if bw and head_ok(bw) and key(bw) != k:
                    lst = rel.setdefault(k, [])
                    if key(bw) not in lst:
                        lst.append(key(bw))
            if sn.get('form_of') or 'form-of' in tags:
                continue
            t = sense_text(sn)
            if t:
                (old if tags & OLD_TAGS else senses).append(short(t, MAX_SENSE_LEN))
        senses = dedupe(senses or old[:1])[:MAX_SENSES]
        if senses:
            defs.setdefault(k, []).append(US.join([clean(word), pos, ipa_of(e), GS.join(senses)]))
        for f in e.get('forms') or []:
            fw = f.get('form') or ''
            tags = set(f.get('tags') or [])
            if not fw or ' ' in fw or tags & {'table-tags', 'inflection-template', 'class', 'romanization'}:
                continue
            fk = key(fw)
            if fk != k and head_ok(fw):
                lst = rel.setdefault(fk, [])
                if k not in lst:
                    lst.append(k)
        tl = list(e.get('translations') or [])
        for sn in e.get('senses') or []:
            for t in sn.get('translations') or []:
                t = dict(t)
                t.setdefault('sense', (sn.get('glosses') or [''])[-1])
                tl.append(t)
        for t in tl:
            code = t.get('lang_code') or t.get('code')
            if code not in trs:
                continue
            w = clean(t.get('word') or '')
            if w and len(w) <= 60:
                trs[code].add(k, pos, short(clean(t.get('sense') or ''), 90), w)
    en = {k: RS.join(v) for k, v in defs.items()}
    out = {c: t.payloads() for c, t in trs.items()}
    note('Wiktionary English: %d entries, %d with definitions; translations: %s'
         % (n_en, len(en), ', '.join('%s %d' % (c, len(v)) for c, v in out.items())))
    return en, out, rel


def gloss_words(g, pos):
    """Short English equivalents in a gloss ("to love, to be fond of (someone)" → love, be fond of)."""
    g = re.sub(r'\([^)]*\)', '', g)
    out = []
    for part in re.split(r'[;,]', g):
        p = part.strip().strip('.').strip()
        if pos == 'verb' and p.startswith('to '):
            p = p[3:]
        if p and len(p.split()) <= 3 and re.fullmatch(r"[A-Za-z][A-Za-z' -]*", p):
            out.append(p)
    return out


def lang_pass(code, path):
    """Entries of one language: its words explained in English, its word forms, and English words
    with equivalents in this language (the reverse direction)."""
    name = LANGS[code][0]
    defs = {}
    rel = {}
    found = {}
    n = 0
    for e in jsonl(path):
        if not is_lang(e, code, name):
            continue
        word = clean(e.get('word') or '')
        pos = clean(e.get('pos') or '')
        if pos in SKIP_POS or not head_ok(word):
            continue
        n += 1
        k = key(word, code)
        senses, old = [], []
        for rank, sn in enumerate(e.get('senses') or []):
            tags = set(sn.get('tags') or [])
            for b in (sn.get('form_of') or []) + (sn.get('alt_of') or []):
                bw = b.get('word')
                if bw and head_ok(bw) and key(bw, code) != k:
                    lst = rel.setdefault(k, [])
                    if key(bw, code) not in lst:
                        lst.append(key(bw, code))
            if sn.get('form_of') or 'form-of' in tags:
                continue
            t = sense_text(sn)
            if not t:
                continue
            (old if tags & OLD_TAGS else senses).append(short(t, MAX_SENSE_LEN))
            if not (tags & OLD_TAGS) and not sn.get('alt_of') and len(word.split()) <= 3:
                for p in gloss_words((sn.get('glosses') or [''])[-1], pos):
                    found.setdefault(key(p), []).append((rank, word, pos))
        senses = dedupe(senses or old[:1])[:MAX_SENSES]
        if senses:
            defs.setdefault(k, []).append(US.join([word, pos, ipa_of(e), GS.join(senses)]))
        for f in e.get('forms') or []:
            fw = f.get('form') or ''
            tags = set(f.get('tags') or [])
            if not fw or ' ' in fw or tags & {'table-tags', 'inflection-template', 'class', 'romanization'}:
                continue
            fk = key(fw, code)
            if fk != k and head_ok(fw):
                lst = rel.setdefault(fk, [])
                if k not in lst and len(lst) < 6:
                    lst.append(k)
    rev = Tr(code)
    for k, lst in found.items():
        lst.sort(key=lambda x: x[0])
        for _, w, pos in lst:
            rev.add(k, pos, REVERSE, w, max_words=10)
    d = {k: RS.join(v) for k, v in defs.items()}
    rv = rev.payloads()
    note('Wiktionary %s: %d entries, %d explained in English, %d English words with equivalents'
         % (name, n, len(d), len(rv)))
    return d, rel, rv


def forms_table(rel, exists, common=(), rules=True):
    """Forms only where the app's rules (Lemma.candidates) would not find the right base form.
    Of several base forms, rare ones ("ran" → "rin") are left out when a common one ("run") is there;
    common = words with a translation or in WordNet."""
    out = {}
    for fk, lemmas in rel.items():
        lemmas = [l for l in lemmas if l in exists and l != fk]
        if not lemmas:
            continue
        if len(lemmas) > 1 and common:
            known = [l for l in lemmas if l in common]
            if known:
                lemmas = known
        if rules and fk not in exists:
            first = next((c for c in candidates(fk) if c in exists), None)
            if first is not None and lemmas == [first]:
                continue
        out[fk] = '|'.join(lemmas[:4])
    return out


# ---------------------------------------------------------------------- Open English WordNet

WN_POS = {'n': 'noun', 'v': 'verb', 'a': 'adj', 's': 'adj', 'r': 'adv'}


def wordnet(work):
    rel = json.loads(text_of('https://api.github.com/repos/globalwordnet/english-wordnet/releases/latest',
                             gh_headers()))
    asset = None
    for a in rel.get('assets', []):
        n = a['name']
        if n.endswith('.xml.gz') and 'english-wordnet' in n:
            asset = a
            break
    if asset is None:
        raise IOError('no WN-LMF XML in the latest Open English WordNet release (%s)'
                      % ', '.join(a['name'] for a in rel.get('assets', [])))
    path = fetch(asset['browser_download_url'], os.path.join(work, asset['name']))
    version = rel.get('tag_name', '')
    return wordnet_from(path), version


def wordnet_from(path):
    entries = []   # (word, pos, [synset ids], ipa)
    synsets = {}   # id → definition
    with (gzip.open(path, 'rb') if path.endswith('.gz') else open(path, 'rb')) as f:
        word = pos = None
        sids, ipa = [], ''
        for ev, el in ET.iterparse(f, events=('start', 'end')):
            tag = el.tag.rsplit('}', 1)[-1]
            if ev == 'start':
                if tag == 'LexicalEntry':
                    word, pos, sids, ipa = None, None, [], ''
                continue
            if tag == 'Lemma':
                word = el.get('writtenForm')
                pos = WN_POS.get(el.get('partOfSpeech'), el.get('partOfSpeech'))
            elif tag == 'Pronunciation':
                if not ipa or el.get('variety') in ('US', 'GA'):
                    ipa = '/' + (el.text or '').strip() + '/' if el.text else ipa
            elif tag == 'Sense':
                if el.get('synset'):
                    sids.append(el.get('synset'))
            elif tag == 'LexicalEntry':
                if word and head_ok(word):
                    entries.append((word, pos, sids, ipa))
                el.clear()
            elif tag == 'Definition':
                pass
            elif tag == 'Synset':
                d = el.find('{*}Definition')
                if d is None:
                    d = el.find('Definition')
                if d is not None and d.text:
                    synsets[el.get('id')] = clean(d.text)
                el.clear()
    by_key = {}
    for word, pos, sids, ipa in entries:
        senses = dedupe([synsets[s] for s in sids if s in synsets])[:6]
        if not senses:
            continue
        senses = [short(s[:1].upper() + s[1:], MAX_SENSE_LEN) for s in senses]
        by_key.setdefault(key(word), []).append(US.join([clean(word), pos or '', ipa, GS.join(senses)]))
    out = {k: RS.join(v) for k, v in by_key.items()}
    note('Open English WordNet: %d words' % len(out))
    return out


def gh_headers():
    t = os.environ.get('GITHUB_TOKEN')
    h = {'Accept': 'application/vnd.github+json'}
    if t:
        h['Authorization'] = 'Bearer ' + t
    return h


# ---------------------------------------------------------------------- FreeDict (TEI)

def freedict(name, work, db=None):
    if db is None:
        db = json.loads(text_of('https://freedict.org/freedict-database.json'))
    item = next((d for d in db if d.get('name') == name), None)
    if item is None:
        raise IOError('FreeDict has no %s dictionary' % name)
    rel = None
    for r in item.get('releases', []):
        if r.get('platform') == 'src':
            rel = r
    if rel is None:
        raise IOError('FreeDict %s: no source release' % name)
    path = fetch(rel['URL'], os.path.join(work, os.path.basename(rel['URL'])))
    tei = None
    with tarfile.open(path) as tar:
        for m in tar.getmembers():
            if m.name.endswith('.tei'):
                tei = tar.extractfile(m).read()
                break
    if tei is None:
        raise IOError('FreeDict %s: no .tei file in %s' % (name, path))
    return freedict_from(tei), rel.get('version', item.get('edition', ''))


def freedict_from(tei):
    tr = Tr()
    for ev, el in ET.iterparse(io.BytesIO(tei), events=('end',)):
        if el.tag.rsplit('}', 1)[-1] != 'entry':
            continue
        orth = [clean(o.text) for o in el.iter() if o.tag.rsplit('}', 1)[-1] == 'orth' and o.text]
        pos = next((clean(p.text) for p in el.iter() if p.tag.rsplit('}', 1)[-1] == 'pos' and p.text), '')
        pos = {'n': 'noun', 'v': 'verb', 'adj': 'adj', 'adv': 'adv', 'vt': 'verb', 'vi': 'verb'}.get(pos, pos)
        for o in orth[:1]:
            if not head_ok(o):
                continue
            for sense in [s for s in el.iter() if s.tag.rsplit('}', 1)[-1] == 'sense'] or [el]:
                gloss = ''
                for x in sense.iter():
                    t = x.tag.rsplit('}', 1)[-1]
                    if t in ('def', 'note') and x.text and not gloss:
                        gloss = short(clean(x.text), 90)
                for cit in sense.iter():
                    if cit.tag.rsplit('}', 1)[-1] != 'cit' or cit.get('type') not in ('trans', 'translation'):
                        continue
                    for q in cit.iter():
                        if q.tag.rsplit('}', 1)[-1] == 'quote' and q.text and len(q.text) <= 60:
                            tr.add(key(o), pos, gloss, clean(q.text))
        el.clear()
    out = tr.payloads()
    note('FreeDict: %d headwords' % len(out))
    return out


# ---------------------------------------------------------------------- file format

def write_rld(path, items, meta, block_size=16384):
    keys = sorted(items, key=lambda k: k.encode('utf-8'))
    blocks, cur, size, first = [], [], 0, None
    for k in keys:
        if not k or '\t' in k or '\n' in k:
            continue
        line = (k + '\t' + items[k] + '\n').encode('utf-8')
        if first is None:
            first = k
        cur.append(line)
        size += len(line)
        if size >= block_size:
            blocks.append((first, b''.join(cur)))
            cur, size, first = [], 0, None
    if cur:
        blocks.append((first, b''.join(cur)))
    meta_b = ''.join('%s=%s\n' % (k, clean(str(v))) for k, v in meta.items()).encode('utf-8')
    comp = [(f, zlib.compress(raw, 9), len(raw)) for f, raw in blocks]
    off = 16 + len(meta_b) + sum(2 + len(f.encode('utf-8')) + 16 for f, _, _ in comp)
    with open(path, 'wb') as fo:
        fo.write(b'RLD1' + struct.pack('>iii', FORMAT_VERSION, len(comp), len(meta_b)) + meta_b)
        for f, c, rawlen in comp:
            fb = f.encode('utf-8')
            fo.write(struct.pack('>H', len(fb)) + fb + struct.pack('>qii', off, len(c), rawlen))
            off += len(c)
        for _, c, _ in comp:
            fo.write(c)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


class Packs:
    def __init__(self, out):
        self.out = out
        self.index = []
        os.makedirs(out, exist_ok=True)

    def add(self, pid, items, **meta):
        if not items:
            warn('%s: empty, not published' % pid)
            return
        meta = dict(meta, id=pid, date=time.strftime('%Y-%m-%d'), entries=len(items))
        path = os.path.join(self.out, pid + '.rld')
        write_rld(path, items, meta)
        size = os.path.getsize(path)
        e = dict(meta, file=pid + '.rld', size=size, sha256=sha256(path))
        self.index.append(e)
        note('%s: %d entries, %.1f MB' % (pid, len(items), size / 1e6))

    def finish(self):
        with open(os.path.join(self.out, 'index.json'), 'w', encoding='utf-8') as f:
            json.dump({'format': FORMAT_VERSION, 'built': time.strftime('%Y-%m-%d'), 'packs': self.index},
                      f, ensure_ascii=False, indent=1)


WIKT = dict(source='Wiktionary (via kaikki.org / wiktextract)', license='CC BY-SA 4.0',
            url='https://en.wiktionary.org')


def build_all(packs, en_path, lang_paths, wn_keys=()):
    """lang_paths: code → path of the kaikki extract, or a function returning it (downloads lazily,
    so only one big file is on the disk at a time)."""
    targets = list(LANGS)
    en, trs, rel = english_pass(en_path, targets)
    packs.add('en-wiktionary', en, name='Wiktionary', type='def', **{'from': 'en', 'to': 'en'}, rank=10, **WIKT)
    common = set(wn_keys)
    for c in targets:
        packs.add('en-%s-wiktionary' % c, trs.get(c), name='Wiktionary', type='tr', **{'from': 'en', 'to': c},
                  rank=10, **WIKT)
        common |= set(trs.get(c) or ())
    for c in targets:
        src = lang_paths.get(c)
        if src is None:
            continue
        try:
            path = src() if callable(src) else src
            d, lrel, rv = lang_pass(c, path)
        except Exception as e:
            traceback.print_exc()
            warn('%s skipped: %s' % (LANGS[c][0], e))
            continue
        if callable(src):
            try:
                os.remove(path)
            except OSError:
                pass
        packs.add('%s-wiktionary' % c, d, name='Wiktionary', type='def', **{'from': c, 'to': 'en'}, rank=10, **WIKT)
        packs.add('en-%s-wiktionary-%s' % (c, c), rv, name='Wiktionary (%s)' % LANGS[c][0], type='tr',
                  **{'from': 'en', 'to': c}, rank=20, **WIKT)
        common |= set(rv)
        packs.add('%s-forms' % c, forms_table(lrel, set(d), rules=False), name='%s word forms' % LANGS[c][0],
                  type='forms', **{'from': c, 'to': c}, rank=0, **WIKT)
    exists = set(en) | common
    packs.add('en-forms', forms_table(rel, exists, common), name='English word forms', type='forms',
              **{'from': 'en', 'to': 'en'}, rank=0, **WIKT)


def freedict_all(packs, work):
    """Every FreeDict dictionary between two languages of ReadLex."""
    db = json.loads(text_of('https://freedict.org/freedict-database.json'))
    n = 0
    for item in db:
        name = item.get('name') or ''
        if '-' not in name:
            continue
        a, b = name.split('-', 1)
        if a not in FD or b not in FD:
            continue
        try:
            fd, version = freedict(name, work, db)
        except Exception as e:
            warn('FreeDict %s skipped: %s' % (name, e))
            continue
        frm, to = FD[a], FD[b]
        if frm != 'en':
            fd = {key(k, frm) if frm == 'la' else k: v for k, v in fd.items()}
        packs.add('%s-%s-freedict' % (frm, to), fd, name='FreeDict', type='tr', **{'from': frm, 'to': to}, rank=30,
                  version=version, source='FreeDict %s' % name, license='GPL / free licenses (see freedict.org)',
                  url='https://freedict.org')
        n += 1
    note('FreeDict: %d dictionaries between ReadLex languages' % n)


def main(argv):
    if argv[:1] == ['--test']:
        t = os.path.join(HERE, '..', 'tests')
        packs = Packs(argv[1])
        wn = wordnet_from(os.path.join(t, 'wn.xml'))
        build_all(packs, os.path.join(t, 'en.jsonl'),
                  {'hu': os.path.join(t, 'hu.jsonl'), 'la': os.path.join(t, 'la.jsonl')}, wn.keys())
        packs.add('en-wordnet', wn, name='Open English WordNet', type='def', **{'from': 'en', 'to': 'en'},
                  rank=20, source='Open English WordNet', license='CC BY 4.0')
        with open(os.path.join(t, 'freedict.tei'), 'rb') as f:
            packs.add('en-hu-freedict', freedict_from(f.read()), name='FreeDict', type='tr',
                      **{'from': 'en', 'to': 'hu'}, rank=30, source='FreeDict', license='GPL')
        packs.finish()
        return
    work, out = argv[0], argv[1]
    os.makedirs(work, exist_ok=True)
    packs = Packs(out)
    failed = []

    wn, wn_version = {}, ''
    try:
        wn, wn_version = wordnet(work)
    except Exception as e:
        traceback.print_exc()
        warn('Open English WordNet skipped: %s' % e)
        failed.append('wordnet')

    try:
        en = kaikki('English', work)
        lazy = {c: (lambda n=LANGS[c][0]: kaikki(n, work)) for c in LANGS}
        build_all(packs, en, lazy, wn.keys())
        try:
            os.remove(en)
        except OSError:
            pass
    except Exception as e:
        traceback.print_exc()
        warn('Wiktionary skipped: %s' % e)
        failed.append('wiktionary')

    if wn:
        packs.add('en-wordnet', wn, name='Open English WordNet', type='def', **{'from': 'en', 'to': 'en'},
                  rank=20, version=wn_version, source='Open English WordNet (Princeton WordNet)',
                  license='CC BY 4.0', url='https://en-word.net')

    try:
        freedict_all(packs, work)
    except Exception as e:
        traceback.print_exc()
        warn('FreeDict skipped: %s' % e)
        failed.append('freedict')

    packs.finish()
    if not packs.index:
        raise SystemExit('no pack could be built')
    if failed:
        warn('not built: ' + ', '.join(failed))


if __name__ == '__main__':
    main(sys.argv[1:])
