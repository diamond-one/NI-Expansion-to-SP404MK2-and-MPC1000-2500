"""Read observed Maschine sound records conservatively; export referenced audio.

This is an interoperability reader, not a Maschine DSP engine. Sound positions
are accepted only when 16 sound-name records precede a matching group record.
Unrecognized layouts fail closed; missing samples never shift later pads.
"""
import csv
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import uuid
import zlib

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .capture import file_hash, safe_name, load_prepared
from .model import Sample, Assignment, Session, sp_pad_number
from .sp_transfer import _write_json

VERSION = 3
MAX_GROUP_BYTES = 32 * 1024 ** 2
MAX_AUDIO_SECONDS = 120
AUDIO_STATUSES = frozenset({'sample', 'preview'})


def usable(pad):
    return pad.get('status') in AUDIO_STATUSES


def _preview_name(name):
    return ' '.join(name.casefold().split())


@lru_cache(maxsize=64)
def sound_previews(root):
    """Index sound-preset previews within one expansion, excluding kit demos."""
    root = Path(root).resolve()
    sounds = root / 'Sounds'
    result = {}
    if not sounds.is_dir() or sounds.is_symlink() or sounds.is_junction():
        return result
    for folder, directories, files in os.walk(sounds, followlinks=False):
        current = Path(folder)
        directories[:] = [name for name in directories if not (current / name).is_symlink()
                           and not (current / name).is_junction() and (current / name).resolve().is_relative_to(root)]
        if current.name != '.previews':
            continue
        for name in files:
            path = current / name
            preset = Path(name[:-4]) if name.casefold().endswith('.ogg') else None
            if preset is None or preset.suffix.casefold() not in {'.mxsnd', '.nksf', '.nksn', '.nmsv', '.nabs', '.nfm8', '.nrkt'}:
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                continue
            # Keep every candidate: ambiguous names must not select a random synth.
            result.setdefault(_preview_name(preset.stem), []).append(str(path.resolve()))
    return result


def _check_cancel(cancel):
    if cancel and cancel.is_set():
        raise InterruptedError('Conversion cancelled. Completed kits are kept.')


def _read_group(path):
    path = Path(path)
    if path.stat().st_size > MAX_GROUP_BYTES:
        raise ValueError('Group exceeds the supported metadata size.')
    data = path.read_bytes()
    if len(data) > MAX_GROUP_BYTES:
        raise ValueError('Group exceeds the supported metadata size.')
    # Some user/older groups are zlib wrapped; never allow unbounded expansion.
    if len(data) > 2 and data[0] == 0x78 and (data[0] * 256 + data[1]) % 31 == 0:
        stream = zlib.decompressobj()
        data = stream.decompress(data, MAX_GROUP_BYTES + 1)
        if len(data) > MAX_GROUP_BYTES or not stream.eof:
            raise ValueError('Compressed group is invalid or too large.')
    return data


def _record_names(data, references=4):
    """Length-prefixed names followed by consecutive object references.

    Observed portable archive references encode their byte width before their
    little-endian value. The newer observed layout uses four references; an
    older layout uses three and omits one prefix byte. Pattern names follow
    the group boundary and are not treated as sound slots.
    """
    result = []
    pattern = rb'\x00\x01([\x00-\x7f])' if references == 4 else rb'\x01([\x00-\x7f])'
    for marker in re.finditer(pattern, data):
        length = marker.group(1)[0]
        start = marker.end()
        end = start + length
        raw = data[start:end]
        if len(raw) != length or any(byte < 32 for byte in raw):
            continue
        try:
            name = raw.decode('utf-8')
        except UnicodeDecodeError:
            continue
        cursor, refs = end, []
        for _ in range(references):
            if cursor >= len(data) or data[cursor] not in (1, 2, 3, 4):
                break
            width = data[cursor]
            refs.append(int.from_bytes(data[cursor + 1:cursor + 1 + width], 'little'))
            cursor += width + 1
        if len(refs) != references or refs != list(range(refs[0], refs[0] + references)):
            continue
        preceding = []
        offsets = (0, 2) if references == 3 and data[marker.start() - 2:marker.start()] == b'\0\0' else (0,)
        for offset in offsets:
            for width in (1, 2, 3, 4):
                end_before = marker.start() - offset
                position = end_before - width - 1
                if position >= 0 and data[position] == width:
                    preceding.append(int.from_bytes(data[position + 1:end_before], 'little'))
        if refs[0] - 1 not in preceding:
            continue
        if references == 3 and data[cursor:cursor + 1] != b'\0':
            continue
        result.append((marker.start(), name.rstrip('\0')))
    return result


def _normal(text):
    return ''.join(c for c in text.casefold() if c.isalnum())


def _kind(name, source):
    name = _normal(name)
    for starts, kind in [(('openhh', 'openhat', 'openhihat'), 'open_hat'),
                         (('closedhh', 'closedhat', 'hihat'), 'hat'),
                         (('kick',), 'kick'), (('snare',), 'snare'),
                         (('clap',), 'clap'), (('rim',), 'rim'),
                         (('perc', 'tom', 'shaker'), 'perc')]:
        if name.startswith(starts):
            return kind
    return 'one_shot'


def inspect_group(kit, cancel=None, use_previews=True):
    _check_cancel(cancel)
    path = Path(kit['path']).resolve(strict=True)
    root = Path(kit['root']).resolve(strict=True)
    before = path.stat()
    data = _read_group(path)
    names = []
    for references in (4, 3):
        candidates = _record_names(data, references)
        boundaries = [i for i, (_, name) in enumerate(candidates) if _normal(name) == _normal(kit['name'])]
        if len(boundaries) == 1 and boundaries[0] == 16:
            names = candidates[:17]
            break
    # No heuristic consecutive-WAV fallback: it would erase empty synth slots.
    if len(names) != 17 or _normal(names[-1][1]) != _normal(kit['name']):
        raise ValueError('This group structure is not supported for direct conversion. Start an empty kit and add its samples manually.')
    pads = []
    for index, (start, name) in enumerate(names[:16]):
        _check_cancel(cancel)
        segment = data[start:names[index + 1][0]]
        refs = []
        for match in re.finditer(rb'Samples[\\/][^\x00-\x1f]{1,1024}?\.(?:wav|wave|aif|aiff|flac)(?=[^a-zA-Z]|$)', segment, re.I):
            try:
                relative = match.group().decode('utf-8').replace('\\', '/')
            except UnicodeDecodeError:
                continue
            if relative.casefold() not in {value.casefold() for value in refs}:
                refs.append(relative)
        pad = {'ni_pad': index + 1, 'sp_pad': sp_pad_number(index), 'name': name.strip(),
               'status': 'needs_render', 'source': None, 'references': refs,
               'kind': _kind(name, refs[0] if refs else '')}
        if len(refs) > 1:
            pad['status'] = 'layered'
        elif len(refs) == 1:
            relative = PurePosixPath(refs[0])
            candidate = root.joinpath(*relative.parts).resolve()
            if '..' in relative.parts or not candidate.is_relative_to(root):
                pad['status'] = 'unsafe_reference'
            elif candidate.is_file():
                stat = candidate.stat()
                if getattr(stat, 'st_file_attributes', 0) & (0x1000 | 0x400000):
                    pad['status'] = 'offline'
                else:
                    pad.update(status='sample', source=str(candidate), size=stat.st_size, mtime_ns=stat.st_mtime_ns)
            else:
                pad['status'] = 'missing'
        if pad['status'] in {'needs_render', 'layered', 'missing'}:
            matches = sound_previews(str(root)).get(_preview_name(pad['name']), [])
            pad['preview_candidates'] = matches
            if use_previews and len(matches) == 1:
                preview = Path(matches[0])
                try:
                    stat = preview.stat()
                    if getattr(stat, 'st_file_attributes', 0) & (0x1000 | 0x400000):
                        raise ValueError('Preview is online-only.')
                    info = sf.info(preview)
                    if info.channels not in (1, 2) or not 8000 <= info.samplerate <= 192000 or not 0 < info.duration <= MAX_AUDIO_SECONDS:
                        raise ValueError('Preview audio format or duration is unsupported.')
                    pad.update(original_status=pad['status'], status='preview', source=str(preview),
                               size=stat.st_size, mtime_ns=stat.st_mtime_ns, preview_duration=info.duration,
                               preview_note='NI preset audition clip; note, phrase and processing may differ from the kit.')
                except (OSError, ValueError, RuntimeError) as exc:
                    pad['preview_error'] = str(exc)
        pads.append(pad)
    if (before.st_size, before.st_mtime_ns) != (path.stat().st_size, path.stat().st_mtime_ns):
        raise ValueError('Kit changed while being read. Select it again.')
    return {'mode': 'direct', 'version': VERSION, 'kit': dict(kit), 'kit_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'pads': pads, 'available': sum(usable(p) for p in pads),
            'preview_count': sum(p['status'] == 'preview' for p in pads), 'use_previews': use_previews,
            'unresolved': [p['ni_pad'] for p in pads if not usable(p)],
            'fidelity': 'Original referenced samples and observed slot positions. Maschine tuning, envelopes, layers and effects are not rendered.'}


def preview_session(group):
    samples, pads = [], []
    for row in group['pads']:
        if not usable(row):
            pads.append(Assignment())
            continue
        identity = hashlib.sha256((row['source'] + str(row['mtime_ns'])).encode()).hexdigest()[:20]
        samples.append(Sample(identity, row['source'], '', row['kind'],
                              {'available': True, 'size': row['size'], 'mtime_ns': row['mtime_ns'], 'name': row['name']}))
        pads.append(Assignment(identity))
    return Session(samples, pads)


def _signature(group):
    return hashlib.sha256(json.dumps(group, sort_keys=True).encode()).hexdigest()


def _load_pad_audio(row, target_rate=48000):
    if not usable(row):
        return np.zeros((4800, 2), dtype=np.float32)
    source = Path(row['source'])
    before = source.stat()
    if (before.st_size, before.st_mtime_ns) != (row['size'], row['mtime_ns']):
        raise ValueError(f'{source.name} changed. Select this kit again.')
    with sf.SoundFile(source) as audio:
        if audio.channels not in (1, 2) or not 8000 <= audio.samplerate <= 192000 or not 0 < len(audio) <= MAX_AUDIO_SECONDS * audio.samplerate:
            raise ValueError(f'{source.name}: use mono/stereo audio up to 120 seconds.')
        data = audio.read(dtype='float32', always_2d=True)
        rate = audio.samplerate
    if not np.isfinite(data).all() or row['status'] != 'preview' and float(np.max(np.abs(data))) > 1:
        raise ValueError(f'{source.name} contains invalid or clipped float audio. Use a rendered capture with headroom.')
    if rate != target_rate:
        divisor = math.gcd(rate, target_rate)
        data = resample_poly(data, target_rate // divisor, rate // divisor, axis=0)
    minimum = round(target_rate * .1)
    if len(data) < minimum:
        data = np.pad(data, ((0, minimum - len(data)), (0, 0)))
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f'{source.name} changed during conversion.')
    return data


def prepare_direct(group, destination, allow_unresolved=False, progress=None, cancel=None):
    if group.get('mode') != 'direct' or len(group.get('pads', [])) != 16 or not group['available']:
        raise ValueError('Select a kit containing supported sample pads.')
    if group['unresolved'] and not allow_unresolved:
        raise ValueError('Some pads need rendering or relinking. Review them, or explicitly allow silence for those positions.')
    if group['kit'].get('path') and file_hash(group['kit']['path']) != group['kit_sha256']:
        raise ValueError('Original kit changed. Select it again.')
    destination = Path(destination).expanduser().resolve()
    if group['kit'].get('root') and destination.is_relative_to(Path(group['kit']['root']).resolve()):
        raise ValueError('Choose an export folder outside the installed NI expansion.')
    pack = destination / safe_name(group['kit']['pack'])
    signature = _signature(group)
    output = pack / (safe_name(group['kit']['name']) + '-' + signature[:8])
    if output.is_dir():
        try:
            session, recipe = load_prepared(output)
            if recipe.get('direct_signature') == signature:
                for index, pad in enumerate(group['pads']):
                    if pad['source']:
                        stat = Path(pad['source']).stat()
                        if (stat.st_size, stat.st_mtime_ns) != (pad['size'], pad['mtime_ns']):
                            raise ValueError('Source changed. Select the kit again.')
                        if file_hash(pad['source']) != recipe['pads'][index].get('original_sha256'):
                            raise ValueError('Source contents changed. Select the kit again.')
                _check_cancel(cancel)
                return output, session
        except (OSError, ValueError, KeyError):
            output = pack / (safe_name(group['kit']['name']) + '-' + signature[:8] + '-' + uuid.uuid4().hex[:6])
    output.mkdir(parents=True)
    marker = output / 'INCOMPLETE.txt'
    marker.write_text('Kit preparation is incomplete. Do not import.\n', encoding='utf-8')
    samples, assignments, rows, reports = [], [], [], []
    try:
        # One common attenuation preserves the kit's relative sample levels.
        # It is only used for peaks introduced by sample-rate conversion.
        peak = 0.0
        for index, row in enumerate(group['pads']):
            _check_cancel(cancel)
            if progress:
                progress(f"Checking headroom · pad {index + 1}/16", index / 32)
            data = _load_pad_audio(row)
            peak = max(peak, float(np.max(np.abs(data))))
        gain = (32767 / 32768) / peak if peak > 1 else 1.0
        for index, row in enumerate(group['pads']):
            _check_cancel(cancel)
            if progress:
                progress(f"Preparing {group['kit']['name']} · pad {index + 1}/16", .5 + index / 32)
            original_hash = None
            data = _load_pad_audio(row) * gain
            if not np.isfinite(data).all() or float(np.max(np.abs(data))) > 1:
                raise ValueError('Audio changed after the headroom check. Select this kit again.')
            if usable(row):
                source = Path(row['source'])
                before = source.stat()
                if (before.st_size, before.st_mtime_ns) != (row['size'], row['mtime_ns']):
                    raise ValueError(f"{source.name} changed. Select this kit again.")
                original_hash = file_hash(source)
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError(f'{source.name} changed during conversion.')
            name = f"{row['sp_pad']:02d}_" + safe_name(row['name'])[:55] + '.wav'
            target = output / name
            sf.write(target, data, 48000, subtype='PCM_16')
            info = sf.info(target)
            if (info.samplerate, info.subtype, info.frames) != (48000, 'PCM_16', len(data)):
                raise ValueError('Prepared WAV verification failed.')
            digest = file_hash(target)
            identity = hashlib.sha256(f'{digest}:{index}'.encode()).hexdigest()[:20]
            samples.append(Sample(identity, str(target), digest, row['kind'], {'available': True, 'name': row['name']}))
            assignments.append(Assignment(identity))
            empty = not usable(row)
            rows.append({'logical_slot': index + 1, 'pad': row['sp_pad'], 'ni_pad': index + 1, 'name': row['name'],
                         'sample_id': identity, 'source_sha256': digest, 'output_sha256': digest, 'file': name,
                         'original_source': row['source'], 'original_sha256': original_hash,
                         'source_status': row['status'], 'intentional_empty': empty})
            reports.append({**row, 'issues': [], 'intentional_empty': empty})
        if group['kit'].get('path') and file_hash(group['kit']['path']) != group['kit_sha256']:
            raise ValueError('Original kit changed during conversion.')
        recipe = {'schema': 1, 'kit': 1, 'kit_size': 16, 'target': 'sp404', 'seed': 1000, 'mode': 'direct',
                  'pads': rows, 'direct_signature': signature, 'direct_group': group,
                  'capture_job': {'kit': group['kit']}, 'capture_report': {'ok': True, 'mode': 'direct', 'pads': reports},
                  'processing': {'sample_rate': 48000, 'bit_depth': 16, 'common_gain': gain,
                                 'headroom_db': 20 * math.log10(gain),
                                 'reason': 'Only attenuate resampling/preview decoding overs; all pads share the same gain'},
                  'fidelity': group['fidelity']}
        (output / 'KIT_RECIPE.json').write_text(json.dumps(recipe, indent=2), encoding='utf-8')
        with (output / 'PAD_MAP.csv').open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['Maschine pad', 'SP pad', 'Sound', 'Status', 'File'])
            writer.writerows([r['ni_pad'], r['pad'], r['name'], r['source_status'], r['file']] for r in rows)
        (output / 'COMPLETE.json').write_text(json.dumps({'complete': True, 'validated': True, 'files': 16}), encoding='utf-8')
        marker.unlink()
        if progress:
            progress('Kit export ready', 1)
        return output, Session(samples, assignments)
    except Exception as exc:
        marker.write_text('Incomplete kit. Do not import.\n' + str(exc), encoding='utf-8')
        raise


def prepare_batch(kits, destination, allow_unresolved=False, progress=None, cancel=None, use_previews=True):
    destination = Path(destination).resolve()
    if any(destination.is_relative_to(Path(kit['root']).resolve()) for kit in kits):
        raise ValueError('Choose an output folder outside the installed NI expansions.')
    destination.mkdir(parents=True, exist_ok=True)
    report = {'converted': [], 'skipped': [], 'cancelled': False, 'total': len(kits)}
    sound_previews.cache_clear()
    report_path = destination / ('BATCH-' + uuid.uuid4().hex[:8] + '.json')
    def save():
        _write_json(report_path, report)
    for index, kit in enumerate(kits):
        try:
            _check_cancel(cancel)
            if progress:
                progress(f"Kit {index + 1}/{len(kits)} · {kit['name']}", index / max(1, len(kits)))
            group = inspect_group(kit, cancel, use_previews)
            output, _ = prepare_direct(group, destination, allow_unresolved, cancel=cancel)
            report['converted'].append({'kit': kit['name'], 'pack': kit['pack'], 'folder': str(output),
                                        'silent_pads': group['unresolved'], 'preview_pads': [p['ni_pad'] for p in group['pads'] if p['status'] == 'preview']})
        except InterruptedError:
            report['cancelled'] = True
            break
        except Exception as exc:
            report['skipped'].append({'kit': kit['name'], 'pack': kit['pack'], 'reason': str(exc)})
        save()
    save()
    if progress and not report['cancelled']:
        progress(f"{len(report['converted'])} kits exported", 1)
    return report_path, report
