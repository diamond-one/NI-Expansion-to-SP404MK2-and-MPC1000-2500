"""Sound-type aware automatic filling and swapping of pads.

Adapted from CrateBuilder's explainable role-relative matching: a pad's sound
type selects candidates from the same expansion, and the candidates are ranked by
brightness, decay and noisiness *relative to other sounds of that type*.

A pad's type is a drum kind (kick, snare, ...) or, for everything else, the
library family of the sound it holds ('oneshots:bass', 'loops:brass', ...). Drum
pads are matched to the character of the kept pads; other pads to the character of
their own current sound, keeping tempo and key when the file names carry them.

Pure logic: no Tk. Nothing is written to installed expansion folders.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3

import numpy as np
import soundfile as sf

from .direct import usable

FEATURE_VERSION = 1
CANDIDATES_PER_KIND = 400   # plenty to choose from; bounds first-run analysis on huge libraries
MAX_SECONDS = 15            # a drum hit longer than this is not a hit
MAX_FAMILY_SECONDS = 120    # loops and instrument phrases may be long; only the start is analysed
ANALYSIS_SECONDS = 1.5
EXTENSIONS = frozenset({'.wav', '.wave', '.aif', '.aiff', '.flac'})
DRUM_KINDS = frozenset({'kick', 'snare', 'clap', 'rim', 'hat', 'open_hat', 'cymbal', 'perc'})
# Pads that were emptied on purpose carry no empty_reason and are never refilled.
FILLABLE_STATUSES = frozenset({'needs_render', 'layered', 'missing', 'offline', 'unsafe_reference'})

_FOLDER_KINDS = {
    'kick': 'kick', 'kicks': 'kick', 'snare': 'snare', 'snares': 'snare',
    'clap': 'clap', 'claps': 'clap', 'rim': 'rim', 'rimshot': 'rim',
    'hihat': 'hat', 'hihats': 'hat', 'hat': 'hat', 'hats': 'hat', 'hh': 'hat',
    'hihatclosed': 'hat', 'closedhihat': 'hat', 'closedhh': 'hat',
    'hihatopen': 'open_hat', 'openhihat': 'open_hat', 'openhh': 'open_hat',
    'cymbal': 'cymbal', 'cymbals': 'cymbal', 'crash': 'cymbal', 'ride': 'cymbal',
    'percussion': 'perc', 'perc': 'perc', 'tom': 'perc', 'toms': 'perc',
    'shaker': 'perc', 'handdrum': 'perc', 'malletdrum': 'perc', 'wooden': 'perc',
    'metallic': 'perc', 'tambourine': 'perc', 'combo': 'perc',
}
_OPEN_HAT = ('openhh', 'openhat', 'openhihat', 'hhopen', 'hihatopen')
_TOKEN_KINDS = {
    'crash': 'cymbal', 'ride': 'cymbal', 'cymbal': 'cymbal', 'cymbals': 'cymbal', 'splash': 'cymbal', 'china': 'cymbal',
    'hh': 'hat', 'hihat': 'hat', 'hat': 'hat', 'hats': 'hat',
    'kick': 'kick', 'kicks': 'kick', 'bd': 'kick', 'bassdrum': 'kick',
    'snare': 'snare', 'snares': 'snare', 'sd': 'snare',
    'clap': 'clap', 'claps': 'clap', 'snap': 'clap',
    'rim': 'rim', 'rimshot': 'rim', 'sidestick': 'rim',
    'perc': 'perc', 'percussion': 'perc', 'tom': 'perc', 'toms': 'perc', 'shaker': 'perc', 'conga': 'perc',
    'cowbell': 'perc', 'clave': 'perc', 'claves': 'perc', 'bongo': 'perc', 'tambourine': 'perc', 'tamb': 'perc',
}
_SKIP_PREFIXES = ('drumbreak', 'drumloop', 'breakbeat', 'drumfill', 'snareroll', 'snarefill', 'snareflam')


def _norm(path):
    """Comparable form of a path without touching the file system."""
    return os.path.normcase(os.path.abspath(str(path)))


def _fold(text):
    return re.sub(r'[^a-z0-9]', '', str(text).casefold())


def _tokens(name):
    """Words of a name, splitting CamelCase and acronyms: 'MidTom CyberCobra' -> mid tom cyber cobra."""
    return [t.casefold() for t in re.findall(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+', str(name))]


def kind_from_name(name):
    """Drum kind named by a pad or file name, or None. The earliest instrument word wins."""
    tokens = _tokens(name)
    joined = ''.join(tokens)
    if joined.startswith(_OPEN_HAT) or any(a + b in _OPEN_HAT for a, b in zip(tokens, tokens[1:])):
        return 'open_hat'
    for token in tokens:
        if token in _TOKEN_KINDS:
            return _TOKEN_KINDS[token]
    return None


def classify_sample(path):
    """Return a drum kind for a path under an NI Samples folder, else None.

    NI folder labels win over filenames (as in CrateBuilder); loops, fills and
    instrument material are never offered for drum pads.
    """
    parts = PurePosixPath(str(path).replace('\\', '/')).parts
    raw = PurePosixPath(parts[-1]).stem if parts else ''
    stem = _fold(raw)
    if stem.startswith(_SKIP_PREFIXES):
        return None
    for index in range(len(parts) - 2, -1, -1):
        if parts[index].casefold() == 'samples':
            relative = parts[index + 1:-1]
            if relative and _fold(relative[0]) in ('loops', 'instruments'):
                return None
            if relative and _fold(relative[0]) == 'drums' and len(relative) > 1:
                kind = _FOLDER_KINDS.get(_fold(relative[1]))
                if kind:
                    return _refine(kind, raw)
            break
    return kind_from_name(raw)


def _refine(kind, stem):
    """NI files rims, claps, shakers and open hats inside the snare/hat folders; the name says which."""
    named = kind_from_name(stem)
    if kind in ('snare', 'hat') and named in ('rim', 'clap', 'perc', 'open_hat'):
        return named
    return kind


def family_of_path(path):
    """Drum kind, or library family, of a sound; else None.

    Families: 'oneshots:bass', 'loops:brass', 'instruments:bass', 'drums:fills', and
    'previews:<category>' for NI preset audition clips (Sounds/<category>/.previews).
    Loops and one-shots filed straight into their category folder are grouped by the
    instrument word that starts their name ('Guitar[140] C Foo' -> 'loops:guitar').
    """
    kind = classify_sample(path)
    if kind:
        return kind
    parts = PurePosixPath(str(path).replace('\\', '/')).parts
    stem = _fold(PurePosixPath(parts[-1]).stem) if parts else ''
    for index in range(len(parts) - 2, -1, -1):
        folder = parts[index].casefold()
        if folder == '.previews':
            for back in range(index - 1, -1, -1):
                if parts[back].casefold() == 'sounds':
                    return 'previews:' + _fold('/'.join(parts[back + 1:index]))
            return None
        if folder != 'samples':
            continue
        relative = parts[index + 1:-1]
        if not relative:
            return None
        category = _fold(relative[0])
        sub_folder = _fold(relative[1]) if len(relative) > 1 else ''
        if category == 'drums':
            if stem.startswith(('drumfill', 'snareroll', 'snarefill', 'snareflam')):
                return 'drums:fills'
            if stem.startswith(('drumbreak', 'drumloop', 'breakbeat')):
                return 'drums:breaks'
            return None
        if category in ('oneshots', 'loops', 'instruments'):
            words = _tokens(PurePosixPath(parts[-1]).stem)
            if not sub_folder:
                sub_folder = words[0] if words else ''
            if not sub_folder:
                return None
            if category in ('oneshots', 'loops') and words:
                # NI's 'Synth Note' folder holds pads, basses and leads: the name's first word says which.
                return f'{category}:{sub_folder}/{words[0]}'
            return f'{category}:{sub_folder}'
        return None
    return None


def preview_categories(root):
    """Folded names of the preset-preview categories of an expansion (Sounds/<category>/.previews)."""
    sounds = Path(root) / 'Sounds'
    found = set()
    if not sounds.is_dir():
        return frozenset()
    try:
        for folder, directories, _ in os.walk(sounds, followlinks=False):
            if '.previews' in directories:
                found.add(_fold(os.path.relpath(folder, sounds).replace('\\', '/')))
            directories[:] = [d for d in directories if d != '.previews']
    except OSError:
        pass
    found.discard('')
    return frozenset(found)


def name_family(name, categories):
    """'Massive Valid' / 'Keys Lesson' -> 'previews:massivesounds' / 'previews:pianokeys', else None."""
    for token in _tokens(name):
        if len(token) < 3 or token in ('sound', 'sounds', 'kit', 'the'):
            continue
        exact = [c for c in categories if c == token]
        if exact:
            return 'previews:' + exact[0]
        partial = sorted((c for c in categories if token in c), key=lambda c: (len(c), c))
        if partial:
            return 'previews:' + partial[0]
    return None


def coarse_kind(kind):
    """'oneshots:synthnote/pad' -> 'oneshots:synthnote'; drum kinds and plain families unchanged."""
    return kind.split('/')[0]


@lru_cache(maxsize=65536)
def _labels(path):
    """(bpm, key) written in a file's name; cached because planning asks per candidate."""
    return musical_labels(PurePosixPath(str(path).replace(chr(92), '/')).stem)


_NOTES = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def parse_key(key):
    """'F#m' -> (6, True); 'Ebmaj' -> (3, False); None when it is not a key."""
    match = re.match(r'^([A-G])(#|b)?(.*)$', key or '')
    if not match:
        return None
    pitch = (_NOTES[match.group(1)] + (1 if match.group(2) == '#' else -1 if match.group(2) == 'b' else 0)) % 12
    return pitch, match.group(3).lower() in ('m', 'min', 'minor')


def key_tier(want, have):
    """How well a candidate's key suits the key a pad has: 0 same, 1 same root in the other mode
    (NI often leaves the 'm' off), 2 relative major/minor, 3 unknown, 4 unrelated."""
    wanted = parse_key(want)
    if wanted is None:
        return 0
    found = parse_key(have)
    if found is None:
        return 3
    if found == wanted:
        return 0
    if found[0] == wanted[0]:
        return 1
    if found[1] != wanted[1]:
        minor, major = (wanted, found) if wanted[1] else (found, wanted)
        if (minor[0] + 3) % 12 == major[0]:
            return 2
    return 4


def kind_label(kind):
    """Human wording for a kind or family key."""
    if ':' in kind:
        category, family = kind.split(':', 1)
        family = family.split('/')[-1]
        return family + (' loops' if category == 'loops' else ' sounds' if category == 'drums' else '')
    return kind.replace('_', ' ')


def musical_labels(name):
    """(bpm, key) written in a file name such as 'Bass[138] Cm Foo' or 'Chord Combo A# Foo', else None."""
    name = str(name)
    bpm_match = re.search(r'(?<!\d)(\d{2,3})\s*[-_]?\s*bpm\b', name, re.I)
    bpm = float(bpm_match.group(1)) if bpm_match else None
    if bpm is not None and not 30 <= bpm <= 300:
        bpm = None
    bracket = re.match(r'^[^\[\]]+\[(\d{2,3}(?:\.\d+)?)\]', name)
    bracket_bpm = float(bracket.group(1)) if bracket else None
    if bracket_bpm is not None and not 30 <= bracket_bpm <= 300:
        bracket_bpm = None
    if bpm is None:
        bpm = bracket_bpm
    key_match = None
    if bracket_bpm is not None:
        tail = name[bracket.end():].lstrip(' _-')
        key_match = re.match(r'([A-G](?:#|b)?(?:(?:maj(?:or)?|min(?:or)?|m))?)(?=$|[\s_()\[\]-])', tail, re.I)
    if key_match is None:
        key_match = re.search(r'(?:^|[\s_()\[\]-])([A-G](?:#|b)?(?:maj(?:or)?|min(?:or)?|m))(?=$|[\s_()\[\]-])', name, re.I)
    if key_match is None:
        key_match = re.search(r'(?:^|[\s_()\[\]-])([A-G](?:#|b)?(?:Maj|Min))(?=[A-Z]|$|[\s_()\[\]-])', name)
    if key_match is None:
        # NI writes a lone note letter right after the instrument word: 'Bass A Follower',
        # 'Pad C# Genesis', 'Chord Combo A# Low Lights'. Only trusted there, and only when a
        # CamelCase word follows, so the article in 'Vocal A cappella' is not read as a key.
        key_match = re.match(r'^[A-Za-z]+(?:\s+Combo)?\s+([A-G](?:#|b)?m?)(?=\s+[A-Z0-9]|\s*$)', name)
    key = key_match.group(1) if key_match else None
    if key:
        key = key[0].upper() + key[1:].replace('Maj', 'maj').replace('Min', 'min')
    return bpm, key


def pad_target_kind(pad, categories=None):
    """Sound type a pad should receive, or None when it cannot be told.

    The pad's own name is the most specific evidence ('Crash' on a DrumSynth Hihat
    engine is a cymbal; 'Clap' on a Snare engine is a clap), then the engine type,
    then the first sample layer's folder, then the family of the sound it holds.
    """
    if pad.get('target_kind'):
        return pad['target_kind']
    kind = kind_from_name(pad.get('name', ''))
    if kind:
        return kind
    if pad.get('engine_kind') in DRUM_KINDS:
        return pad['engine_kind']
    if pad.get('kind') in DRUM_KINDS:
        return pad['kind']
    for reference in pad.get('references') or ():
        kind = classify_sample(reference)
        if kind:
            return kind
    if usable(pad) and pad.get('source'):
        # Not a drum: the folder of the sound it holds says what family it belongs to.
        return family_of_path(pad['source'])
    # No sound to look at (an unrendered synth pad): its name may name a preset category.
    return name_family(pad.get('name', ''), categories) if categories else None


def fillable_pads(group):
    """Indexes of pads that have no audio but a known place to look for some."""
    result = []
    for index, pad in enumerate(group['pads']):
        if usable(pad):
            continue
        if pad['status'] in FILLABLE_STATUSES or (pad['status'] == 'empty' and pad.get('empty_reason') == 'drumsynth'):
            result.append(index)
    return result


# --- audio features -------------------------------------------------------

def analyse(path, max_seconds=MAX_SECONDS):
    """Brightness, decay and noisiness of the first moments of a sample."""
    with sf.SoundFile(path) as audio:
        if audio.channels not in (1, 2) or not 8000 <= audio.samplerate <= 192000 or len(audio) <= 0:
            raise ValueError('Unsupported audio format.')
        if len(audio) > max_seconds * audio.samplerate:
            raise ValueError('Too long.')
        rate = audio.samplerate
        data = audio.read(int(ANALYSIS_SECONDS * rate), dtype='float32', always_2d=True)
    mono = data.mean(axis=1)
    if not np.isfinite(mono).all():
        raise ValueError('Invalid audio.')
    peak = float(np.max(np.abs(mono)))
    if peak < 1e-5:
        raise ValueError('Silent sample.')
    onset = int(np.argmax(np.abs(mono) >= peak * .1))
    body = mono[onset:onset + int(.25 * rate)]
    window = np.hanning(len(body)) if len(body) > 1 else np.ones(1)
    spectrum = np.abs(np.fft.rfft(body * window)) ** 2 + 1e-18
    freqs = np.fft.rfftfreq(len(body), 1 / rate)
    centroid = float(np.sum(freqs * spectrum) / np.sum(spectrum))
    flatness = float(np.exp(np.mean(np.log(spectrum))) / np.mean(spectrum))
    hop = max(1, int(.005 * rate))
    tail = mono[onset:]
    usable_frames = len(tail) // hop
    if usable_frames:
        frames = np.sqrt(np.mean(tail[:usable_frames * hop].reshape(usable_frames, hop) ** 2, axis=1))
    else:
        frames = np.array([peak])
    top = int(np.argmax(frames))
    below = np.nonzero(frames[top:] < frames[top] * 10 ** (-30 / 20))[0]
    decay = float((below[0] if len(below) else len(frames) - top) * hop / rate)
    return {'centroid': centroid, 'decay': decay, 'flatness': flatness,
            'clipped': float(np.mean(np.abs(mono) >= .9999)), 'leading': onset / rate}


def _previews_listing(root):
    """NI preset audition clips: Sounds/<category>/.previews/<preset>.ogg."""
    sounds = root / 'Sounds'
    found = []
    if not sounds.is_dir() or sounds.is_symlink():
        return found
    for folder, directories, names in os.walk(sounds, followlinks=False):
        if Path(folder).name != '.previews':
            directories[:] = [d for d in directories if not (Path(folder) / d).is_symlink()]
            continue
        directories[:] = []
        for name in names:
            path = Path(folder) / name
            if path.suffix.casefold() == '.ogg' and not path.is_symlink():
                kind = family_of_path(path)
                if kind:
                    found.append((str(path), kind))
    return found


@lru_cache(maxsize=96)
def _listing(root):
    """Every sound of an expansion with its drum kind or family, listed once per session."""
    root = Path(root)
    found = _previews_listing(root)
    samples = root / 'Samples'
    for folder, directories, names in os.walk(samples, followlinks=False) if samples.is_dir() else ():
        directories[:] = [d for d in directories if not (Path(folder) / d).is_symlink()
                          and not (Path(folder) / d).is_junction()]
        for name in names:
            path = Path(folder) / name
            if path.suffix.casefold() in EXTENSIONS and not path.is_symlink():
                kind = family_of_path(path)
                if kind:
                    found.append((str(path), kind))
    return tuple(found)


class SoundIndex:
    """Feature cache keyed on size and mtime, like the kit-position index."""

    def __init__(self, database):
        self.database = Path(database)

    def rows(self, expansion_root, cancel=None, progress=None, kinds=None, cap=None):
        """Analysed sounds of an expansion, each tagged with its drum kind or library family.

        `kinds` limits the sound types, and `cap` keeps at most that many per type (a stable
        pick, so the cache is reused), so a huge library is not analysed end to end.
        """
        found = [(Path(path), kind) for path, kind in _listing(str(Path(expansion_root).resolve()))
                 if kinds is None or kind in kinds or coarse_kind(kind) in kinds]
        if cap:
            kept = []
            for kind in {k for _, k in found}:
                same = sorted((f for f in found if f[1] == kind),
                              key=lambda f: hashlib.sha1(_norm(f[0]).encode()).digest())
                kept.extend(same[:cap])
            found = kept
        self.database.parent.mkdir(parents=True, exist_ok=True)
        result, misses = [], []
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS features (path TEXT PRIMARY KEY, size INTEGER, '
                       'modified INTEGER, version INTEGER, data TEXT)')
            for path, kind in found:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError('Auto-fill cancelled')
                try:
                    stat = path.stat()
                    if getattr(stat, 'st_file_attributes', 0) & (0x1000 | 0x400000):
                        continue  # online-only
                    cached = db.execute('SELECT size, modified, version, data FROM features WHERE path=?',
                                        (str(path),)).fetchone()
                except OSError:
                    continue
                if cached and cached[:3] == (stat.st_size, stat.st_mtime_ns, FEATURE_VERSION):
                    result.append(self._row(path, kind, stat, json.loads(cached[3])))
                else:
                    misses.append((path, kind, stat))
            # File reads and numpy release the GIL, so a few threads speed up a cold first run.
            done = 0
            with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 2))) as pool:
                futures = [(pool.submit(analyse, path, MAX_SECONDS if kind in DRUM_KINDS else MAX_FAMILY_SECONDS), path, kind, stat)
                           for path, kind, stat in misses]
                try:
                    for future, path, kind, stat in futures:
                        if cancel is not None and cancel.is_set():
                            raise InterruptedError('Auto-fill cancelled')
                        done += 1
                        if progress and done % 25 == 0:
                            progress(f'Analysing sounds (first time only) · {done}/{len(misses)}', done / max(1, len(misses)))
                        try:
                            features = future.result()
                        except (OSError, RuntimeError, ValueError, sf.LibsndfileError):
                            continue
                        db.execute('INSERT OR REPLACE INTO features VALUES (?,?,?,?,?)',
                                   (str(path), stat.st_size, stat.st_mtime_ns, FEATURE_VERSION, json.dumps(features)))
                        result.append(self._row(path, kind, stat, features))
                except InterruptedError:
                    for future, *_ in futures:
                        future.cancel()
                    raise
        return result

    @staticmethod
    def _row(path, kind, stat, features):
        return {'path': str(path), 'kind': kind, 'size': stat.st_size,
                'mtime_ns': stat.st_mtime_ns, 'features': features}


# --- matching -------------------------------------------------------------

_AXES = (('centroid', 1.2), ('decay', 1.1), ('flatness', .5))


def _scales(pool_by_kind):
    """Sorted feature values per kind and axis, built once per plan."""
    return {kind: {axis: np.sort([r['features'][axis] for r in rows]) for axis, _ in _AXES}
            for kind, rows in pool_by_kind.items() if rows}


def _vector(scales, kind, features):
    """Where a sound sits (midrank 0..1) among sounds of its own kind."""
    scale = scales.get(kind)
    if scale is None:
        return None
    result = []
    for axis, _ in _AXES:
        values = scale[axis]
        below = np.searchsorted(values, features[axis], side='left')
        equal = np.searchsorted(values, features[axis], side='right') - below
        result.append((below + equal / 2) / len(values))
    return np.array(result, dtype=float)


def _quality(features):
    return -min(features.get('clipped', 0) * 8, .7) - min(features.get('leading', 0), .4)


def _jitter(seed, pad, path):
    digest = hashlib.sha256(f'{seed}:{pad}:{path}'.encode()).digest()
    return int.from_bytes(digest[:2], 'little') / 65535


def _kit_root(group):
    return (group.get('origin_kit') or group['kit']).get('root')


def swappable_pads(group, categories=None):
    """Unlocked pads that can be given another sound of their own type."""
    if categories is None and _kit_root(group):
        categories = preview_categories(_kit_root(group))
    fillable = set(fillable_pads(group))
    return [i for i, pad in enumerate(group['pads'])
            if not pad.get('locked') and (usable(pad) or i in fillable) and pad_target_kind(pad, categories)]


def plan_autofill(group, rows, seed=0, surprise=.35, exclude=(), pads=None, anchor_pads=None,
                  avoid=(), method='auto_fill', categories=None):
    """Choose a sound for each target pad (default: every fillable pad).

    Returns (plan, skipped). plan items are dicts with index, path, kind, reason;
    skipped items say why a pad was left for manual work. Deterministic for a
    seed, so a different seed re-rolls. `anchor_pads` are the pads whose character
    the new sounds should sit with (default: every pad that is not being replaced);
    `avoid` lists sounds to skip while alternatives exist.
    """
    if categories is None and _kit_root(group):
        categories = preview_categories(_kit_root(group))
    pool_by_kind, pool_by_coarse = {}, {}
    for row in rows:
        bpm, key = musical_labels(PurePosixPath(str(row['path']).replace('\\', '/')).stem)
        row = dict(row, norm=_norm(row['path']), bpm=bpm, key=key)
        pool_by_kind.setdefault(row['kind'], []).append(row)
        pool_by_coarse.setdefault(coarse_kind(row['kind']), []).append(row)
    scales = _scales(pool_by_kind)
    targets = fillable_pads(group) if pads is None else list(pads)
    if anchor_pads is None:
        anchor_pads = [i for i, p in enumerate(group['pads']) if usable(p) and i not in targets]
        anchor_pads = anchor_pads or [i for i, p in enumerate(group['pads']) if usable(p)]
    # Anchors: pads that stay, placed on the same relative scale as the candidates.
    anchors = []
    for i in anchor_pads:
        pad = group['pads'][i]
        if not usable(pad) or not pad.get('source'):
            continue
        kind = pad_target_kind(pad)
        try:
            vector = _vector(scales, kind, analyse(pad['source'])) if kind else None
        except (OSError, RuntimeError, ValueError, sf.LibsndfileError):
            vector = None
        if vector is not None:
            anchors.append(vector)
    target = np.mean(anchors, axis=0) if anchors else np.array([.5] * len(_AXES))
    weights = [w for _, w in _AXES]
    window = .15 + .35 * min(1, max(0, surprise))
    used = {_norm(p['source']) for p in group['pads'] if p.get('source')}
    used |= {_norm(p) for p in exclude}
    avoid = {_norm(p) for p in avoid}
    verb = 'Swapped' if method == 'swap' else 'Auto-filled'
    plan, skipped = [], []
    for index in targets:
        pad = group['pads'][index]
        kind = pad_target_kind(pad, categories)
        if kind is None:
            skipped.append({'index': index, 'reason': 'Sound type unknown'})
            continue
        fresh = [r for r in pool_by_kind.get(kind, []) if r['norm'] not in used]
        if not fresh:   # no unused sound of this exact instrument anywhere: any of the folder's family
            fresh = [r for r in pool_by_coarse.get(coarse_kind(kind), []) if r['norm'] not in used]
        pool = [r for r in fresh if r['norm'] not in avoid] or fresh
        if not pool:
            skipped.append({'index': index, 'reason': f"No other {kind_label(kind)} in this expansion"})
            continue
        # Drum pads sit with the kept pads; other pads stay close to the sound they replace,
        # and keep its tempo and key when the file names say what they are.
        goal, bpm, key = target, None, None
        if kind not in DRUM_KINDS and pad.get('source'):
            bpm, key = musical_labels(PurePosixPath(str(pad['source']).replace('\\', '/')).stem)
            loop = kind.startswith('loops:')
            if loop and coarse_kind(kind) != kind:
                # A loop at the wrong tempo is useless, an arp at the right one is not: look across the
                # whole folder, then narrow by tempo, then key, and only then by instrument.
                wide = [r for r in pool_by_coarse.get(coarse_kind(kind), []) if r['norm'] not in used]
                pool = [r for r in wide if r['norm'] not in avoid] or wide or pool
            try:
                own = _vector(scales, kind, analyse(pad['source'], MAX_FAMILY_SECONDS))
            except (OSError, RuntimeError, ValueError, sf.LibsndfileError):
                own = None
            if own is not None:
                goal = own
            if bpm:   # same tempo, else a sound that does not claim one, else anything
                pool = ([r for r in pool if r['bpm'] == bpm] or [r for r in pool if r['bpm'] is None] or pool)
            if key:   # closest key: same, same root, relative, then one that does not claim a key, then any
                tiers = [key_tier(key, r['key']) for r in pool]
                pool = [r for r, tier in zip(pool, tiers) if tier == min(tiers)]
            if loop:
                pool = [r for r in pool if r['kind'] == kind] or pool
        scored = []
        for row in pool:
            vector = _vector(scales, kind, row['features'])
            score = (-.95 * float(np.average(np.abs(vector - goal), weights=weights))
                     + _quality(row['features']) + .08 * _jitter(seed, index, row['path']))
            if row.get('foreign'):
                score -= .3
            if key and row['key'] == key:
                score += .6
            if bpm and row['bpm'] == bpm:
                score += .3
            scored.append((score, row))
        best = max(score for score, _ in scored)
        near = [item for item in scored if item[0] >= best - window]
        score, row = min(near, key=lambda item: hashlib.sha256(f'{seed}:{index}:{item[1]["path"]}'.encode()).digest())
        used.add(row['norm'])
        plan.append({'index': index, 'path': row['path'], 'kind': kind, 'score': round(score, 3), 'method': method,
                     'reason': f"{verb} {kind_label(kind)}: matched the character of the " +
                               ('kept pads' if kind in DRUM_KINDS else 'sound it replaced')})
    return plan, skipped


def apply_plan(group, plan):
    """Assign planned sounds; raise nothing for a single bad file, report it."""
    from . import kit_editor as ed
    failed = []
    for item in plan:
        try:
            ed.assign(group, item['index'], item['path'],
                      {'method': item.get('method', 'auto_fill'), 'kind': item['kind'], 'reason': item['reason']})
            # Sticky: a pad keeps its role even if the new sound's file name says otherwise.
            group['pads'][item['index']].update(
                kind=item['kind'] if item['kind'] in DRUM_KINDS else 'one_shot', target_kind=item['kind'])
        except (OSError, RuntimeError, ValueError) as exc:
            failed.append({'index': item['index'], 'reason': str(exc)})
    return ed.refresh(group), failed


SIBLING_EXPANSIONS = 30      # at most this many other expansions are asked, one at a time, until the family has enough
PER_SIBLING_CAP = 30
KEY_SIBLINGS = 8            # hunting for a matching key or tempo asks at most this many expansions


def sibling_roots(root):
    """Other installed expansions next to this one, in a stable pseudo-random order."""
    root = Path(root)
    try:
        others = [d for d in root.parent.iterdir() if d != root and d.name.endswith(' Library')
                  and d.is_dir() and not d.is_symlink()]
    except OSError:
        return []
    return sorted(others, key=lambda d: hashlib.sha1(d.name.encode()).digest())


def auto_fill(group, sounds, expansion_root=None, seed=0, surprise=.35, cancel=None, progress=None,
              exclude=(), pads=None, anchor_pads=None, avoid=(), method='auto_fill'):
    """Plan and apply in one call. `sounds` is a SoundIndex."""
    root = expansion_root or _kit_root(group)
    if not root:
        todo = fillable_pads(group) if pads is None else pads
        return [], [{'index': i, 'reason': 'This kit has no expansion to search'} for i in todo]
    categories = preview_categories(root)
    kinds_of = {i: pad_target_kind(pad, categories) for i, pad in enumerate(group['pads'])}
    wanted_kinds = {k for k in kinds_of.values() if k}
    wanted_kinds |= {coarse_kind(k) for k in wanted_kinds if k.startswith('loops:')}
    rows = sounds.rows(root, cancel, progress, kinds=wanted_kinds, cap=CANDIDATES_PER_KIND)
    targets = fillable_pads(group) if pads is None else list(pads)
    wanted = {}
    for i in targets:
        if kinds_of.get(i):
            wanted[kinds_of[i]] = wanted.get(kinds_of[i], 0) + 1
    in_use = {_norm(p['source']) for p in group['pads'] if p.get('source')}

    # What each pad needs musically: its tempo and key, when its own file name carries them.
    needs = {}
    for i in targets:
        kind, pad = kinds_of.get(i), group['pads'][i]
        if kind and kind not in DRUM_KINDS and pad.get('source'):
            needs.setdefault(coarse_kind(kind) if kind.startswith('loops:') else kind, set()).add(_labels(str(pad['source'])))

    def thin_kinds(musical=True):
        """Kinds that need more sounds: too few to choose from, or (musical) none in a pad's key and tempo."""
        counts, labelled = {}, {}
        for row in rows:
            if _norm(row['path']) in in_use:
                continue
            for key in {row['kind'], coarse_kind(row['kind'])}:
                counts[key] = counts.get(key, 0) + 1
                labelled.setdefault(key, set()).add(_labels(row['path']))
        thin = {kind for kind, count in wanted.items() if counts.get(kind, 0) < max(4, 2 * count)}
        for kind, wanted_labels in (needs.items() if musical else ()):
            have = labelled.get(kind, set())
            for bpm, key in wanted_labels:
                if (bpm or key) and not any((not bpm or b == bpm) and (not key or key_tier(key, k) <= 2) for b, k in have):
                    thin.add(kind)
        return thin

    # 1. the same instrument from neighbouring expansions, one at a time, until there is enough to choose from
    thin = thin_kinds()
    for n, sibling in enumerate(sibling_roots(root)[:SIBLING_EXPANSIONS] if thin else ()):
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Auto-fill cancelled')
        if n >= KEY_SIBLINGS and not thin_kinds(musical=False):
            break     # only a key or tempo is missing: not worth asking every expansion
        if progress:
            progress(f'Looking for more matching sounds in other expansions · {n + 1}', 0)
        rows += [dict(r, foreign=True) for r in sounds.rows(sibling, cancel, progress, kinds=thin, cap=PER_SIBLING_CAP)]
        thin = thin_kinds()
        if not thin:
            break
    # 2. last resort: any instrument of the same folder in this expansion (a pad for a bass is better than silence)
    if thin:
        known = {_norm(r['path']) for r in rows}
        wide = sounds.rows(root, cancel, progress, kinds={coarse_kind(k) for k in thin}, cap=CANDIDATES_PER_KIND)
        rows += [r for r in wide if _norm(r['path']) not in known]
    plan, skipped = plan_autofill(group, rows, seed, surprise, exclude, pads, anchor_pads, avoid, method, categories)
    group, failed = apply_plan(group, plan)
    return plan, skipped + failed
