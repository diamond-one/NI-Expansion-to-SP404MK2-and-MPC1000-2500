"""The kit library: every kept kit is a folder of 8 ready WAVs plus a manifest, with a permanent K### id.

This folder is the source of truth. MPC1000 programs (and later SP-404 banks) are built from it.
"""
from datetime import datetime, timezone
import json
from math import gcd
import os
from pathlib import Path
import shutil

import numpy as np
from scipy.signal import resample_poly
import soundfile as sf

from . import autofill as af

RATE = 44100
MAX_SECONDS = 3.5            # a hit longer than this is trimmed (cymbals ring, but not for ever)
TARGET_PEAK = .89            # about -1 dBFS across the loudest hit of the kit
# Cut at the real tertiles / quartiles of a 300-kit sample of an NI library, so tags spread evenly.
TAG_DARK, TAG_BRIGHT = 1800, 3100    # snare brightness in Hz (centroid of its first moments)
TAG_TIGHT, TAG_ROOMY = .09, .17      # snare decay in seconds (time to fall 30 dB)


def load_hit(path, rate=RATE, max_seconds=MAX_SECONDS):
    """A drum hit as float32 (n, 1|2) at `rate`: silence trimmed either side, tail faded, mono when L = R."""
    with sf.SoundFile(path) as audio:
        source_rate = audio.samplerate
        if not 8000 <= source_rate <= 192000 or len(audio) <= 0 or audio.channels < 1:
            raise ValueError('Unsupported audio.')
        data = audio.read(int((max_seconds + .5) * source_rate), dtype='float32', always_2d=True)
    data = data[:, :2]
    if not np.isfinite(data).all():
        raise ValueError('Invalid audio.')
    if source_rate != rate:
        divisor = gcd(rate, source_rate)
        data = resample_poly(data, rate // divisor, source_rate // divisor, axis=0).astype(np.float32)
    peak = float(np.max(np.abs(data)))
    if peak < 1e-5:
        raise ValueError('Silent hit.')
    loud = np.nonzero(np.max(np.abs(data), axis=1) > peak * 10 ** (-60 / 20))[0]
    start = max(0, int(loud[0]) - int(.003 * rate))
    end = min(len(data), int(loud[-1]) + int(.02 * rate), start + int(max_seconds * rate))
    data = data[start:end].copy()
    fade = min(len(data) // 4, int(.008 * rate))
    if fade:
        data[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
    if data.shape[1] == 2:
        left, right = data[:, 0], data[:, 1]
        if np.sqrt(np.mean((left - right) ** 2)) <= 1e-3 * (np.sqrt(np.mean(left ** 2)) + 1e-12):
            data = ((left + right) / 2)[:, None]
    return np.ascontiguousarray(data, dtype=np.float32)


def character_tag(features):
    """'dark tight', 'bright', 'mid roomy' ... from the snare analysis ({'snare': {...}})."""
    snare = features.get('snare')
    if not snare:
        return ''
    centroid, decay = snare['centroid'], snare['decay']
    words = ['bright' if centroid > TAG_BRIGHT else 'dark' if centroid < TAG_DARK else 'mid']
    if decay <= TAG_TIGHT:
        words.append('tight')
    elif decay >= TAG_ROOMY:
        words.append('roomy')
    return ' '.join(words)


def kit_features(cand):
    found = {}
    for slot in cand['slots']:
        if slot['label'] in ('KICK', 'SNARE') and slot['source']:
            try:
                found[slot['label'].lower()] = af.analyse(slot['source'])
            except (OSError, RuntimeError, ValueError, sf.LibsndfileError):
                pass
    return found


def kit_name(number):
    return f'K{number:03d}' if number < 1000 else f'K{number}'


class KitLibrary:
    def __init__(self, root):
        self.root = Path(root)
        self.kits_dir = self.root / 'kits'
        self.kits_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self.root / 'library.json'

    # -- bookkeeping ---------------------------------------------------------------------------
    def _state(self):
        try:
            state = json.loads(self._state_file.read_text(encoding='utf-8'))
            if isinstance(state, dict) and isinstance(state.get('next_id'), int):
                return state
        except (OSError, ValueError):
            pass
        existing = [m['number'] for m in self.kits()]
        return {'next_id': (max(existing) + 1) if existing else 1}

    def _save_state(self, state):
        tmp = self._state_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, indent=2), encoding='utf-8')
        os.replace(tmp, self._state_file)

    def next_number(self):
        return self._state()['next_id']

    # -- kits ----------------------------------------------------------------------------------
    def kits(self):
        found = []
        for folder in sorted(self.kits_dir.glob('K*')):
            try:
                found.append(json.loads((folder / 'manifest.json').read_text(encoding='utf-8')))
            except (OSError, ValueError):
                continue
        return sorted(found, key=lambda m: m['number'])

    def get(self, number):
        return json.loads((self.kits_dir / kit_name(number) / 'manifest.json').read_text(encoding='utf-8'))

    def wav(self, manifest, index):
        slot = manifest['slots'][index]
        return self.kits_dir / manifest['id'] / slot['wav'] if slot.get('wav') else None

    def add_kit(self, cand, tag=None, hit_cache=None):
        """Render the candidate's eight hits into a new kit folder. Returns its manifest.
        `hit_cache` maps source path -> already decoded hit (as load_hit returns), to skip decoding again."""
        cache = hit_cache or {}
        hits = []
        for slot in cand['slots']:
            if not slot['source']:
                hits.append(None)
            else:
                hit = cache.get(slot['source'])
                hits.append(hit if hit is not None else load_hit(slot['source']))
        if not any(h is not None for h in hits):
            raise ValueError('This kit has no sounds.')
        peak = max(float(np.max(np.abs(h))) for h in hits if h is not None)
        gain = min(TARGET_PEAK / peak, 4.0) if peak > 0 else 1.0
        state = self._state()
        number = state['next_id']
        name = kit_name(number)
        final, temp = self.kits_dir / name, self.kits_dir / (name + '.tmp')
        if final.exists():
            raise ValueError(f'{name} already exists; the library state is out of date.')
        shutil.rmtree(temp, ignore_errors=True)
        temp.mkdir()
        try:
            slots, memory = [], 0
            for slot, hit in zip(cand['slots'], hits):
                wav = ''
                if hit is not None:
                    wav = slot['short'] + '.WAV'
                    out = np.clip(hit * gain, -1, 1)
                    sf.write(temp / wav, out, RATE, subtype='PCM_16')
                    memory += out.size * 2
                slots.append({'label': slot['label'], 'kind': slot['kind'], 'name': slot['name'],
                              'source': slot['source'], 'wav': wav, 'origin': slot['origin'],
                              'channels': int(hit.shape[1]) if hit is not None else 0,
                              'seconds': round(len(hit) / RATE, 3) if hit is not None else 0})
            manifest = {'id': name, 'number': number, 'created': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                        'tag': tag if tag is not None else character_tag(kit_features(cand)),
                        'source': {'pack': cand['pack'], 'kit': cand['kit'], 'half': cand['half'],
                                   'kit_path': cand['path'], 'candidate': cand['id']},
                        'slots': slots, 'memory_bytes': memory, 'format': '44.1 kHz / 16-bit PCM',
                        'gain': round(gain, 3)}
            (temp / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            os.replace(temp, final)
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise
        state['next_id'] = number + 1
        self._save_state(state)
        return manifest

    def remove_last(self, number):
        """Undo a keep. Only the newest kit can be removed, so ids never have gaps or get renumbered."""
        state = self._state()
        if number != state['next_id'] - 1:
            raise ValueError('Only the most recently kept kit can be removed.')
        shutil.rmtree(self.kits_dir / kit_name(number), ignore_errors=True)
        state['next_id'] = number
        self._save_state(state)
