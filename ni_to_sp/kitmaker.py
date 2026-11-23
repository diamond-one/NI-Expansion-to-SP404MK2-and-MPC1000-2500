"""Kit Maker: every Maschine kit's drum half becomes a ready 8-pad drum kit in one house layout.

The pool is built once (and cached): for each kit, find the half of the 16 pads that holds the drums,
place its hits into the house layout by role, and queue the kits for quick review. Any role a kit
lacks, and any hit you dislike, is swapped for a similar sound of the same role from the same expansion.
No Tk in here.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import random
import sqlite3

from . import autofill as af
from .direct import inspect_group, usable

POOL_VERSION = 4
MIN_NATIVE = 4             # a half needs this many of its own hits to count as a drum half
# slot -> (label on the pad, short file name, accepted sound kinds in order of preference)
HOUSE_LAYOUT = (
    ('KICK', 'KICK', ('kick',)),
    ('SNARE', 'SNARE', ('snare',)),
    ('CL HAT', 'CLHAT', ('hat',)),
    ('OP HAT', 'OPHAT', ('open_hat',)),
    ('CLAP', 'CLAP', ('clap', 'rim')),
    ('RIM', 'RIM', ('rim', 'perc')),
    ('TOM', 'TOM', ('perc',)),
    ('CYMBAL', 'CYMBAL', ('cymbal',)),
)
SLOTS = len(HOUSE_LAYOUT)


def want_kind(index):
    """The kind of sound a slot is meant to hold (what a swap or fill looks for)."""
    return HOUSE_LAYOUT[index][2][0]


def _empty_slot(index):
    label, short, kinds = HOUSE_LAYOUT[index]
    return {'label': label, 'short': short, 'kind': kinds[0], 'name': '', 'source': None, 'origin': 'empty'}


def _drum_pads(group, indexes):
    by_kind = {}
    for i in indexes:
        pad = group['pads'][i]
        if pad.get('status') != 'sample' or not usable(pad):
            continue                      # drums must be real samples (not synth previews or silence)
        kind = af.pad_target_kind(pad)
        if kind in af.DRUM_KINDS:
            by_kind.setdefault(kind, []).append(i)
    return by_kind


def _identity(slots):
    """Two candidates with the same kick, snare and hat are the same kit."""
    parts = [os.path.normcase(slots[0]['source'] or ''), os.path.normcase(slots[1]['source'] or ''),
             os.path.normcase(slots[2]['source'] or slots[3]['source'] or '')]
    return hashlib.sha1('|'.join(parts).encode()).hexdigest()[:16]


def extract_candidates(kit, group):
    """Candidate kits (one per drum half that has kick + snare + a hat) from an inspected NI kit.

    A role the main half lacks is first taken from the kit's other half (toms and cymbals are often
    there) before anything is borrowed from the rest of the expansion.
    """
    halves = {'bottom': range(0, 8), 'top': range(8, 16)}
    found = []
    for half, indexes in halves.items():
        other = halves['top' if half == 'bottom' else 'bottom']
        main, extra = _drum_pads(group, indexes), _drum_pads(group, other)
        used, files, slots = set(), set(), []
        # Some NI kits point two pads at one file; a sound must not sit on two pads of the same kit.
        free = lambda i: i not in used and os.path.normcase(group['pads'][i]['source']) not in files
        for index, (label, short, kinds) in enumerate(HOUSE_LAYOUT):
            pick = next((i for kind in kinds for i in main.get(kind, []) if free(i)), None)
            origin = 'kit'
            if pick is None:
                pick = next((i for kind in kinds for i in extra.get(kind, []) if free(i)), None)
                origin = 'kit (other half)'
            if pick is None:
                slots.append(_empty_slot(index))
                continue
            used.add(pick)
            pad = group['pads'][pick]
            files.add(os.path.normcase(pad['source']))
            slots.append({'label': label, 'short': short, 'kind': af.pad_target_kind(pad), 'name': pad['name'],
                          'source': pad['source'], 'origin': origin, 'pad': pick + 1})
        native = sum(slot['origin'] == 'kit' for slot in slots)
        if native < MIN_NATIVE or not (slots[0]['source'] and slots[1]['source'] and (slots[2]['source'] or slots[3]['source'])):
            continue                  # the half must really hold drums, or it is only a mirror of the other half
        found.append({'id': hashlib.sha1((kit['id'] + half).encode()).hexdigest()[:12], 'pack': kit['pack'],
                      'kit': kit['name'], 'root': kit['root'], 'path': kit['path'], 'half': half,
                      'filled': sum(bool(s['source']) for s in slots), 'native': native, 'key': _identity(slots),
                      'slots': slots})
    return found


def read_kit(kit):
    """Candidates of one kit; [] when it has none or cannot be read."""
    try:
        group = inspect_group(kit, use_previews=False)
    except Exception:
        return []
    return extract_candidates(kit, group)


def build_pool(kits, cache_path, progress=None, cancel=None, workers=4):
    """All candidates of all kits. Results are cached per kit (path, size, mtime), so a rebuild is quick."""
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    pool, misses = [], []
    with closing(sqlite3.connect(cache_path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS pool (path TEXT PRIMARY KEY, size INTEGER, modified INTEGER, '
                   'version INTEGER, data TEXT)')
        for kit in kits:
            row = db.execute('SELECT size, modified, version, data FROM pool WHERE path=?', (kit['path'],)).fetchone()
            if row and row[:3] == (kit['size'], kit['mtime_ns'], POOL_VERSION):
                pool.extend(json.loads(row[3]))
            else:
                misses.append(kit)
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [(executor.submit(read_kit, kit), kit) for kit in misses]
            try:
                for future, kit in futures:
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError('Kit pool cancelled')
                    candidates = future.result()
                    done += 1
                    if progress and done % 20 == 0:
                        progress(f'Reading kits (first time only) · {done}/{len(misses)}', done / max(1, len(misses)))
                    db.execute('INSERT OR REPLACE INTO pool VALUES (?,?,?,?,?)',
                               (kit['path'], kit['size'], kit['mtime_ns'], POOL_VERSION, json.dumps(candidates)))
                    pool.extend(candidates)
            except InterruptedError:
                for future, _ in futures:
                    future.cancel()
                raise
    return pool


def order_pool(candidates, seen=(), seed=0):
    """Review order: no repeats, nothing already seen, complete kits first, expansions taken in turns."""
    seen = set(seen)
    # A copy of a kit you already kept or skipped (same kick, snare and hat in another expansion) is seen too.
    seen_keys = {c['key'] for c in candidates if c['id'] in seen}
    unique, keys = [], set(seen_keys)
    for cand in sorted(candidates, key=lambda c: (-c['filled'], c['id'])):
        if cand['id'] in seen or cand['key'] in keys:
            continue
        keys.add(cand['key'])
        unique.append(cand)
    by_pack = {}
    for cand in unique:
        by_pack.setdefault(cand['pack'], []).append(cand)
    rng = random.Random(seed)
    for group in by_pack.values():
        rng.shuffle(group)
        group.sort(key=lambda c: -c['filled'])        # stable: complete kits first, shuffled within
    packs = sorted(by_pack)
    rng.shuffle(packs)
    ordered = []
    while any(by_pack.values()):
        for pack in packs:
            if by_pack[pack]:
                ordered.append(by_pack[pack].pop(0))
    return ordered


# --- swapping and filling hits ------------------------------------------------------------------

def _group_for(cand):
    pads = []
    for index, slot in enumerate(cand['slots']):
        pads.append({'name': slot['name'] or slot['label'], 'status': 'sample' if slot['source'] else 'empty',
                     'source': slot['source'], 'target_kind': want_kind(index), 'kind': want_kind(index),
                     'references': []})
    return {'pads': pads, 'kit': {'root': cand['root']}}


def _rows_for(root, kind, sounds, in_use, cancel=None, progress=None):
    rows = sounds.rows(root, cancel, progress, kinds={kind}, cap=af.CANDIDATES_PER_KIND)
    used = {af._norm(p) for p in in_use}
    if sum(af._norm(r['path']) not in used for r in rows) < 12:           # a thin expansion borrows from its neighbours
        for sibling in af.sibling_roots(root)[:8]:
            rows += [dict(r, foreign=True) for r in sounds.rows(sibling, cancel, progress, kinds={kind}, cap=30)]
    return rows


def suggest(cand, index, sounds, seed=0, avoid=(), surprise=.6, cancel=None, progress=None):
    """Another sound for one slot: same role, same expansion first, similar in character to the current hit
    (or to a typical one when the slot is empty), never one already in the kit. (path, name) or None."""
    group = _group_for(cand)
    in_use = [s['source'] for s in cand['slots'] if s['source']]
    rows = _rows_for(cand['root'], want_kind(index), sounds, in_use, cancel, progress)
    current = bool(cand['slots'][index]['source'])
    plan, _ = af.plan_autofill(group, rows, seed=seed, surprise=surprise, pads=[index],
                               anchor_pads=[index] if current else [], avoid=avoid,
                               method='swap', categories=frozenset())
    if not plan:
        return None
    return plan[0]['path'], Path(plan[0]['path']).stem


def apply_hit(cand, index, path, name, origin):
    cand['slots'][index].update(source=path, name=name, origin=origin, kind=want_kind(index))
    cand['filled'] = sum(bool(s['source']) for s in cand['slots'])
    cand['key'] = _identity(cand['slots'])
    return cand


def fill_empty(cand, sounds, seed=0, cancel=None, progress=None):
    """Fill every empty slot from the same expansion. Returns the number still empty."""
    for index, slot in enumerate(cand['slots']):
        if slot['source']:
            continue
        found = suggest(cand, index, sounds, seed + index, cancel=cancel, progress=progress)
        if found:
            apply_hit(cand, index, found[0], found[1], 'filled')
    return sum(not s['source'] for s in cand['slots'])
