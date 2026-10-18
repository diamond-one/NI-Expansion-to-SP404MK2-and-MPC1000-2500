"""Editable 16-slot kits and incremental, local expansion sample lookup."""
import copy
from contextlib import closing
import json
import os
from pathlib import Path
import random
import sqlite3
import uuid

import soundfile as sf

from .direct import inspect_group, usable, _kind, _check_cancel, VERSION
from .model import sp_pad_number
from .sp_transfer import _write_json

MODES = (
    'Any sample · same expansion',
    'Any sample · all expansions',
    'Same pad in another kit · same expansion',
    'Same pad in another kit · all expansions',
)
EXTENSIONS = {'.wav', '.wave', '.aif', '.aiff', '.flac', '.ogg'}


def refresh(group):
    group['available'] = sum(usable(p) for p in group['pads'])
    group['unresolved'] = [p['ni_pad'] for p in group['pads'] if not usable(p) and p['status'] != 'empty']
    group['preview_count'] = sum(p['status'] == 'preview' for p in group['pads'])
    return group


def empty_kit(name='My kit'):
    return refresh({'mode': 'direct', 'version': VERSION, 'kit_sha256': None,
        'kit': {'id': uuid.uuid4().hex, 'name': name, 'pack': 'My kits', 'path': None, 'root': None},
        'pads': [{'ni_pad': i + 1, 'sp_pad': sp_pad_number(i), 'name': 'Empty',
                  'status': 'empty', 'source': None, 'references': [], 'kind': 'one_shot'} for i in range(16)],
        'fidelity': 'User-built sample kit. No Maschine effects or instrument rendering.'})


def sample_row(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.casefold() not in EXTENSIONS:
        raise ValueError('Choose a WAV, AIFF, FLAC or OGG sample.')
    stat = path.stat()
    if getattr(stat, 'st_file_attributes', 0) & (0x1000 | 0x400000):
        raise ValueError('Make this sample available offline first.')
    info = sf.info(path)
    if info.channels not in (1, 2) or not 8000 <= info.samplerate <= 192000 or not 0 < info.duration <= 120:
        raise ValueError('Use mono/stereo samples up to 120 seconds.')
    if (stat.st_size, stat.st_mtime_ns) != (path.stat().st_size, path.stat().st_mtime_ns):
        raise ValueError('The sample changed while it was checked.')
    return {'name': path.stem, 'status': 'sample', 'source': str(path),
            'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'kind': _kind(path.stem, str(path))}


def assign(group, index, path, provenance=None):
    if index not in range(16):
        raise ValueError('Pad must be 1–16.')
    replacement = sample_row(path)
    old = group['pads'][index]
    replacement.update(ni_pad=index + 1, sp_pad=sp_pad_number(index),
        original_name=old.get('original_name', old['name']), references=[],
        replacement=provenance or {'method': 'chosen_file'})
    group['pads'][index] = replacement
    return refresh(group)


def clear(group, index):
    group['pads'][index] = {'ni_pad': index + 1, 'sp_pad': sp_pad_number(index),
        'name': 'Empty', 'status': 'empty', 'source': None, 'references': [], 'kind': 'one_shot'}
    return refresh(group)


def from_prepared(folder):
    from .capture import load_prepared
    _, recipe = load_prepared(folder)
    group = empty_kit(recipe['capture_job']['kit']['name'])
    group['kit']['pack'] = recipe['capture_job']['kit']['pack']
    group['origin_kit'] = recipe['capture_job']['kit']
    for row in recipe['pads']:
        i = row['logical_slot'] - 1
        if not row.get('intentional_empty'):
            assign(group, i, Path(folder) / row['file'], {'method': 'opened_export'})
            group['pads'][i]['name'] = row.get('name', group['pads'][i]['name'])
    return group


def save_kit(group, path):
    from .mpc_banks import sync_banks
    sync_banks(group)
    _write_json(path, {'schema': 1, 'kind': 'ni-to-sp-editable-kit', 'group': group})


def open_kit(path):
    path = Path(path)
    if path.stat().st_size > 4 * 1024**2:
        raise ValueError('Kit file is too large.')
    document = json.loads(path.read_text(encoding='utf-8'))
    group = document.get('group', {})
    if document.get('kind') != 'ni-to-sp-editable-kit' or document.get('schema') != 1 or len(group.get('pads', [])) != 16:
        raise ValueError('Choose an editable NI to SP kit file.')
    groups = [group]
    if 'banks' in group:
        if not isinstance(group['banks'], list) or len(group['banks']) != 4 or type(group.get('active_bank')) is not int or group['active_bank'] not in range(4):
            raise ValueError('Invalid MPC banks. Expected A, B, C and D.')
        if not isinstance(group.get('program_id'), str) or not isinstance(group.get('program_name'), str):
            raise ValueError('Invalid MPC program identity.')
        for bank in group['banks']:
            if not isinstance(bank, dict) or len(bank.get('pads', [])) != 16 or 'banks' in bank:
                raise ValueError('Each MPC bank must contain exactly 16 pads.')
        groups += group['banks']
    for bank in groups:
        for i, row in enumerate(bank['pads']):
            if (row.get('ni_pad'), row.get('sp_pad')) != (i + 1, sp_pad_number(i)):
                raise ValueError('Kit pad positions are invalid.')
            if usable(row):
                try:
                    checked = sample_row(row['source'])
                    if (checked['size'], checked['mtime_ns']) != (row['size'], row['mtime_ns']):
                        raise ValueError('Sample changed')
                except (OSError, RuntimeError, ValueError):
                    row.update(status='missing')
        if bank.get('version', 0) < VERSION or bank.get('use_previews') is False:
            source = bank.get('origin_kit') or bank.get('bank_kit') or bank['kit']
            if source.get('path'):
                try:
                    from .capture import file_hash
                    if file_hash(source['path']) == bank.get('kit_sha256'):
                        original = inspect_group(dict(source, name=Path(source['path']).stem))
                        for i, row in enumerate(bank['pads']):
                            if not usable(row) and row['status'] != 'empty' and not row.get('replacement'):
                                bank['pads'][i] = original['pads'][i]
                        bank.update(version=VERSION, use_previews=True)
                except (OSError, ValueError, RuntimeError):
                    pass  # Offline/changed NI installs never erase saved edits.
        refresh(bank)
    return refresh(group)


class ExpansionPool:
    def __init__(self, database):
        self.database = Path(database)

    def candidates(self, kits, group, index, mode, pack=None, cancel=None, progress=None):
        if mode not in range(4):
            raise ValueError('Choose one of the four replacement scopes.')
        current = group.get('origin_kit', group['kit'])
        anchor = pack or current['pack']
        scope = [k for k in kits if mode in (1, 3) or k['pack'] == anchor]
        if not scope:
            raise ValueError('Choose an expansion in the library filter for this scope.')
        results = {}
        if mode < 2:
            roots = sorted({k['root'] for k in scope})
            for root in roots:
                _check_cancel(cancel)
                samples = Path(root).resolve() / 'Samples'
                if not samples.is_dir():
                    continue
                for folder, directories, files in os.walk(samples, followlinks=False):
                    _check_cancel(cancel)
                    directories[:] = [d for d in directories if not (Path(folder) / d).is_symlink()
                        and not (Path(folder) / d).is_junction()]
                    for name in files:
                        p = Path(folder) / name
                        if p.suffix.casefold() in EXTENSIONS and not p.is_symlink() and p.resolve().is_relative_to(samples):
                            results[str(p.resolve())] = {'method': MODES[mode], 'expansion': Path(root).name}
        else:
            self.database.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute('CREATE TABLE IF NOT EXISTS kits (path TEXT PRIMARY KEY, size INTEGER, modified INTEGER, pads TEXT)')
                for n, kit in enumerate(scope):
                    _check_cancel(cancel)
                    if progress and n % 25 == 0:
                        progress(f"Indexing pad positions · {n + 1}/{len(scope)} kits", n / len(scope))
                    if kit['id'] == current['id']:
                        continue
                    p = Path(kit['path'])
                    try:
                        stat = p.stat()
                        cached = db.execute('SELECT size, modified, pads FROM kits WHERE path=?', (str(p),)).fetchone()
                        if cached and cached[:2] == (stat.st_size, stat.st_mtime_ns):
                            pads = json.loads(cached[2])
                        else:
                            pads = inspect_group(kit, cancel, use_previews=False)['pads']
                            db.execute('INSERT OR REPLACE INTO kits VALUES (?,?,?,?)',
                                (str(p), stat.st_size, stat.st_mtime_ns, json.dumps(pads)))
                        row = pads[index]
                        if row['status'] == 'sample':
                            results.setdefault(row['source'], {'method': MODES[mode], 'kit': kit['name'],
                                'kit_id': kit['id'], 'expansion': kit['pack'], 'ni_pad': index + 1})
                    except (OSError, ValueError, RuntimeError):
                        continue
        results.pop(group['pads'][index].get('source'), None)
        return results

    def choose(self, kits, group, index, mode, pack=None, cancel=None, progress=None, excluded=()):
        choices = self.candidates(kits, group, index, mode, pack, cancel, progress)
        for path in excluded:
            choices.pop(path, None)
        # Validate only the shuffled candidates tried, not every audio file.
        paths = list(choices)
        random.SystemRandom().shuffle(paths)
        for path in paths:
            _check_cancel(cancel)
            try:
                sample_row(path)
                return path, choices[path]
            except (OSError, ValueError, RuntimeError):
                continue
        raise ValueError('No other usable sample was found for this scope and pad.')
