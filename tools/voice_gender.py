"""Measures whether each voice of the "voices" release is a male or a female voice and writes it
into voices.json ("gender": "m" / "f", "f0": median pitch in Hz). Run in GitHub Actions:
every voice reads a sentence (sherpa-onnx), and the median fundamental frequency decides
(male voices ~85-155 Hz, female ~165-255 Hz)."""
import json
import os
import shutil
import sys
import urllib.request
import zipfile

import numpy as np
import sherpa_onnx

BASE = 'https://github.com/meszarosatilla-hue/readlex-dictionaries/releases/download/voices/'
SAMPLE = {
    'hu': 'Egyszer volt, hol nem volt, élt egyszer egy öreg király, akinek három fia volt.',
    'en': 'Once upon a time there lived an old king who had three sons and a beautiful garden.',
    'es': 'Había una vez un viejo rey que tenía tres hijos y un hermoso jardín.',
    'de': 'Es war einmal ein alter König, der hatte drei Söhne und einen schönen Garten.',
    'fr': 'Il était une fois un vieux roi qui avait trois fils et un très beau jardin.',
    'it': 'C’era una volta un vecchio re che aveva tre figli e un bellissimo giardino.',
    'pt': 'Era uma vez um velho rei que tinha três filhos e um belo jardim.',
    'nl': 'Er was eens een oude koning die drie zonen had en een prachtige tuin.',
    'pl': 'Dawno, dawno temu żył stary król, który miał trzech synów i piękny ogród.',
    'cs': 'Byl jednou jeden starý král, který měl tři syny a krásnou zahradu.',
    'sk': 'Kde bolo, tam bolo, žil raz jeden starý kráľ, ktorý mal troch synov a krásnu záhradu.',
    'ro': 'A fost odată un rege bătrân care avea trei fii și o grădină frumoasă.',
}


def fetch(url, dest):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'ReadLex'}), timeout=300) as r, \
            open(dest, 'wb') as f:
        shutil.copyfileobj(r, f, 1 << 20)


def f0(samples, rate):
    """Median pitch of the voiced frames (autocorrelation, 60–400 Hz)."""
    x = np.asarray(samples, dtype=np.float64)
    n = int(rate * 0.04)
    hop = n // 2
    lo, hi = int(rate / 400), int(rate / 60)
    energy = [np.sqrt(np.mean(x[i:i + n] ** 2)) for i in range(0, len(x) - n, hop)]
    thr = 0.3 * np.percentile(energy, 90) if energy else 0
    out = []
    for k, i in enumerate(range(0, len(x) - n, hop)):
        if energy[k] < thr:
            continue
        fr = x[i:i + n] - np.mean(x[i:i + n])
        ac = np.correlate(fr, fr, 'full')[n - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:hi]
        lag = lo + int(np.argmax(seg))
        if seg.max() / ac[0] > 0.45:
            out.append(rate / lag)
    return float(np.median(out)) if out else 0.0


def main():
    work = 'gender-work'
    os.makedirs(work, exist_ok=True)
    fetch(BASE + 'voices.json', 'voices.json')
    d = json.load(open('voices.json'))
    fetch(BASE + d['espeak']['file'], os.path.join(work, 'espeak.zip'))
    zipfile.ZipFile(os.path.join(work, 'espeak.zip')).extractall(work)
    espeak = os.path.join(work, 'espeak-ng-data')
    for v in d['voices']:
        vd = os.path.join(work, v['id'])
        fetch(BASE + v['file'], vd + '.zip')
        zipfile.ZipFile(vd + '.zip').extractall(vd)
        os.remove(vd + '.zip')
        cfg = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=os.path.join(vd, 'model.onnx'), tokens=os.path.join(vd, 'tokens.txt'), data_dir=espeak),
                num_threads=2),
        )
        tts = sherpa_onnx.OfflineTts(cfg)
        a = tts.generate(SAMPLE.get(v['lang'], SAMPLE['en']), sid=0, speed=1.0)
        p = f0(a.samples, a.sample_rate)
        v['f0'] = round(p)
        v['gender'] = 'm' if 0 < p < 160 else ('f' if p >= 160 else '')
        msg = '%s: %d Hz → %s' % (v['id'], p, v['gender'] or '?')
        print(msg, flush=True)
        if os.environ.get('GITHUB_ACTIONS'):
            print('::notice::' + msg, flush=True)
        shutil.rmtree(vd)
    os.makedirs('voices-out', exist_ok=True)
    with open('voices-out/voices.json', 'w') as f:
        json.dump(d, f, indent=1)


if __name__ == '__main__':
    main()
