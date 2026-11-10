"""Prepares the read-aloud parts of ReadLex (run in GitHub Actions, needs the gh CLI):

  * the sherpa-onnx engine for Android (native libraries + its Kotlin API, Tts.kt)
  * Piper voices (neural, offline) for the ReadLex languages, repackaged as plain zip files
    (model.onnx + tokens.txt), the shared espeak-ng-data.zip and voices.json

Everything is uploaded to the "voices" release; Tts.kt and the version also go to vendor/.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

REPO = 'k2-fsa/sherpa-onnx'
LOCALES = ['hu_HU', 'en_US', 'en_GB', 'es_ES', 'es_MX', 'de_DE', 'fr_FR', 'it_IT', 'pt_PT', 'pt_BR', 'nl_NL', 'nl_BE',
           'pl_PL', 'cs_CZ', 'sk_SK', 'ro_RO']
MULTI = ('libritts', 'arctic', 'vctk', 'l2arctic', 'mls')  # many speakers, large – skipped
PER_LOCALE = 3
OUT = 'voices-out'


def note(m):
    print(m, flush=True)
    if os.environ.get('GITHUB_ACTIONS'):
        print('::notice::' + m, flush=True)


def gh(*args):
    return subprocess.run(['gh'] + list(args), check=True, capture_output=True, text=True).stdout


def fetch(url, dest):
    print('download', url, flush=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'ReadLex'}), timeout=300) as r, \
            open(dest, 'wb') as f:
        shutil.copyfileobj(r, f, 1 << 20)
    return dest


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def engine():
    rel = json.loads(gh('api', 'repos/%s/releases/latest' % REPO))
    tag = rel['tag_name']
    names = [a['name'] for a in rel['assets']]
    note('sherpa-onnx %s; android assets: %s' % (tag, ', '.join(n for n in names if 'android' in n.lower() or n.endswith('.aar'))[:900]))
    libs = {}
    cand = [a for a in rel['assets'] if re.search(r'android\.tar\.bz2$', a['name']) and not re.search(r'rknn|qnn|static|ascend', a['name'])]
    if cand:
        p = fetch(cand[0]['browser_download_url'], 'android.tar.bz2')
        with tarfile.open(p) as t:
            for m in t.getmembers():
                mm = re.search(r'(arm64-v8a|armeabi-v7a)/(lib[^/]+\.so)$', m.name)
                if mm and m.isfile():
                    libs['%s/%s' % (mm.group(1), mm.group(2))] = t.extractfile(m).read()
    else:
        aar = [a for a in rel['assets'] if a['name'].endswith('.aar')]
        p = fetch(aar[0]['browser_download_url'], 'sherpa.aar')
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                mm = re.search(r'jni/(arm64-v8a|armeabi-v7a)/(lib[^/]+\.so)$', n)
                if mm:
                    libs['%s/%s' % (mm.group(1), mm.group(2))] = z.read(n)
    if not libs:
        raise SystemExit('no Android libraries found')
    with zipfile.ZipFile(os.path.join(OUT, 'sherpa-jni.zip'), 'w', zipfile.ZIP_DEFLATED) as z:
        for k, v in sorted(libs.items()):
            z.writestr(k, v)
    note('engine libraries: ' + ', '.join('%s %.1f MB' % (k, len(v) / 1e6) for k, v in sorted(libs.items())))
    tree = json.loads(gh('api', 'repos/%s/git/trees/%s?recursive=1' % (REPO, tag)))
    paths = [t['path'] for t in tree['tree'] if t['path'].endswith('kotlin-api/Tts.kt')]
    note('Tts.kt candidates: ' + ', '.join(paths))
    src = fetch('https://raw.githubusercontent.com/%s/%s/%s' % (REPO, tag, paths[0]), os.path.join(OUT, 'Tts.kt'))
    os.makedirs('vendor', exist_ok=True)
    shutil.copy(src, 'vendor/Tts.kt')
    with open('vendor/sherpa-version.txt', 'w') as f:
        f.write(tag + '\n')
    return tag


def voices():
    rel = json.loads(gh('api', 'repos/%s/releases/tags/tts-models' % REPO))
    # complete list through the assets endpoint (the release object holds at most 1000)
    allassets = []
    page = 1
    while True:
        more = json.loads(gh('api', 'repos/%s/releases/%s/assets?per_page=100&page=%d' % (REPO, rel['id'], page)))
        if not more:
            break
        allassets += more
        page += 1
    found = {}
    for a in allassets:
        m = re.fullmatch(r'vits-piper-([a-z]{2}_[A-Z]{2})-([A-Za-z0-9_]+)-(high|medium)\.tar\.bz2', a['name'])
        if not m or m.group(1) not in LOCALES or any(x in m.group(2) for x in MULTI):
            continue
        found.setdefault(m.group(1), []).append((0 if m.group(3) == 'high' else 1, m.group(2), m.group(3), a))
    index = []
    espeak_done = False
    for loc in LOCALES:
        lst = sorted(found.get(loc, []), key=lambda x: (x[0], x[1]))
        names = set()
        chosen = []
        for q, name, quality, a in lst:  # one quality per speaker (the better one)
            if name in names:
                continue
            names.add(name)
            chosen.append((name, quality, a))
        for name, quality, a in chosen[:PER_LOCALE]:
            p = fetch(a['browser_download_url'], 'v.tar.bz2')
            model = tokens = None
            with tarfile.open(p) as t:
                for m in t.getmembers():
                    if not m.isfile():
                        continue
                    if m.name.endswith('.onnx'):
                        model = t.extractfile(m).read()
                    elif m.name.endswith('tokens.txt'):
                        tokens = t.extractfile(m).read()
                    elif '/espeak-ng-data/' in m.name and not espeak_done:
                        pass
                if not espeak_done:
                    with zipfile.ZipFile(os.path.join(OUT, 'espeak-ng-data.zip'), 'w', zipfile.ZIP_DEFLATED) as z:
                        for m in t.getmembers():
                            i = m.name.find('espeak-ng-data/')
                            if m.isfile() and i >= 0:
                                z.writestr(m.name[i:], t.extractfile(m).read())
                    espeak_done = True
            os.remove(p)
            if not model or not tokens:
                print('skip', a['name'])
                continue
            vid = '%s-%s-%s' % (loc, name, quality)
            zp = os.path.join(OUT, 'voice-%s.zip' % vid)
            with zipfile.ZipFile(zp, 'w', zipfile.ZIP_STORED) as z:
                z.writestr('model.onnx', model)
                z.writestr('tokens.txt', tokens)
            index.append(dict(id=vid, lang=loc[:2], locale=loc, name=name.capitalize(), quality=quality,
                              file=os.path.basename(zp), size=os.path.getsize(zp), sha256=sha256(zp),
                              license='see https://huggingface.co/rhasspy/piper-voices'))
            note('voice %s: %.0f MB' % (vid, os.path.getsize(zp) / 1e6))
    ep = os.path.join(OUT, 'espeak-ng-data.zip')
    return index, dict(file='espeak-ng-data.zip', size=os.path.getsize(ep), sha256=sha256(ep))


def main():
    os.makedirs(OUT, exist_ok=True)
    tag = engine()
    vs, espeak = voices()
    jni = os.path.join(OUT, 'sherpa-jni.zip')
    with open(os.path.join(OUT, 'voices.json'), 'w') as f:
        json.dump(dict(engine=tag, jni=dict(file='sherpa-jni.zip', sha256=sha256(jni)), espeak=espeak, voices=vs),
                  f, indent=1)
    note('%d voices' % len(vs))


if __name__ == '__main__':
    main()
