"""MPC1000 PGM 1.00 export, adapted from the user's CrateBuilder exporter.

The unmodified template is by Stephen Norum; see licenses/pympc1000.txt.
Exports a 16-pad kit or one 64-pad program containing banks A-D.
"""
import csv
import hashlib
import json
from pathlib import Path
import re
import struct
import unicodedata
import uuid

import numpy as np
import soundfile as sf

from .capture import file_hash
from .direct import _load_pad_audio, _check_cancel, usable
from .sp_transfer import _write_json

FILE_SIZE, PAD_BASE, PAD_SIZE = 0x2A04, 0x18, 0xA4
TEMPLATE = Path(__file__).parent / 'assets/EMPTY.PGM'


def safe_name(name):
    text = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().upper()
    return re.sub('[^A-Z0-9_]+', '_', text).strip('_')[:16] or 'MY_KIT'


def pad_notes(bank=0):
    data = TEMPLATE.read_bytes()[0x2918:0x2958]
    return list(data if bank is None else data[bank * 16:bank * 16 + 16])


def read_pad_notes(path):
    """Read an MPC1000 program's pad routing without loading its sample files."""
    with Path(path).open('rb') as stream:
        data = stream.read(FILE_SIZE + 1)
    if len(data) != FILE_SIZE or data[4:20] != b'MPC1000 PGM 1.00':
        raise ValueError('Choose an MPC1000 PGM 1.00 program, or use MIDI Learn for another format.')
    notes = list(data[0x2918:0x2958])
    if len(set(notes)) != 64 or any(note > 127 or data[0x2958 + note] != i for i, note in enumerate(notes)):
        raise ValueError('This program has an invalid pad-note table. Your existing mapping was kept.')
    return notes


def program_bytes(names):
    if len(names) not in (16, 64):
        raise ValueError('An MPC1000 program must contain 16 or 64 pad positions.')
    data = bytearray(TEMPLATE.read_bytes())
    if len(data) != FILE_SIZE or data[4:20].rstrip(b'\0') != b'MPC1000 PGM 1.00':
        raise ValueError('Invalid MPC1000 template.')
    for i in range(64):
        base = PAD_BASE + i * PAD_SIZE
        for layer in range(4):
            data[base + layer * 24:base + layer * 24 + 16] = bytes(16)
        data[base + 0x63] = 0  # No guessed choke groups.
        if i < len(names) and names[i]:
            name = names[i].encode('ascii')
            if not re.fullmatch(rb'[A-Z0-9_]{1,16}', name):
                raise ValueError('Invalid MPC sample name.')
            data[base:base + 16] = name.ljust(16, b'\0')
            data[base + 17:base + 20] = bytes([70, 0, 127])
            data[base + 22] = 0
    return bytes(data)


def validate_program(path):
    path = Path(path)
    data = path.read_bytes()
    if len(data) != FILE_SIZE or struct.unpack_from('<H', data)[0] != FILE_SIZE or data[4:20] != b'MPC1000 PGM 1.00':
        raise ValueError('MPC1000 program header/length failed verification.')
    notes = list(data[0x2918:0x2958])
    if len(set(notes)) != 64 or any(data[0x2958 + note] != i for i, note in enumerate(notes)):
        raise ValueError('Invalid MPC MIDI mapping.')
    assigned, memory = 0, 0
    for i in range(64):
        base = PAD_BASE + i * PAD_SIZE
        name = data[base:base + 16].rstrip(b'\0').decode('ascii')
        if any(data[base + layer * 24:base + layer * 24 + 16] != bytes(16) for layer in (1, 2, 3)):
            raise ValueError('Unexpected MPC sample layer.')
        if not name:
            continue
        if not re.fullmatch('[A-Z0-9_]{1,16}', name):
            raise ValueError('Unexpected sample assignment.')
        if data[base + 18:base + 20] != bytes([0, 127]) or data[base + 22] != 0:
            raise ValueError('MPC velocity/play mode mismatch.')
        info = sf.info(path.parent / (name + '.WAV'))
        if info.samplerate != 44100 or info.subtype != 'PCM_16' or info.channels not in (1, 2):
            raise ValueError('MPC WAV format mismatch.')
        memory += info.frames * info.channels * 2
        assigned += 1
    return {'format': 'MPC1000 PGM 1.00', 'assigned_pads': assigned,
            'sample_memory_mb': round(memory / 1024**2, 2), 'hardware_tested': False}


def export_program(group, destination, allow_unresolved=False, progress=None, cancel=None):
    from .mpc_banks import all_banks
    banks = all_banks(group)
    pads = [row for bank in banks for row in bank['pads']]
    if len(group.get('pads', [])) != 16 or not any(bank['available'] for bank in banks):
        raise ValueError('Add at least one sample before exporting.')
    if any(bank['unresolved'] for bank in banks) and not allow_unresolved:
        raise ValueError('Replace missing pads or explicitly allow empty positions.')
    kit = group['kit']
    def check_sources():
        for bank in banks:
            source = bank['kit']
            if source.get('path') and file_hash(source['path']) != bank['kit_sha256']:
                raise ValueError('A source kit changed. Reopen it before exporting.')
    check_sources()
    destination = Path(destination).expanduser().resolve()
    for bank in banks:
        if bank['kit'].get('root') and destination.is_relative_to(Path(bank['kit']['root']).resolve()):
            raise ValueError('Export outside the installed NI library.')
    destination.mkdir(parents=True, exist_ok=True)
    name = safe_name(kit['name'])
    folder = destination / (name + '_' + uuid.uuid4().hex[:6].upper())
    folder.mkdir()
    marker = folder / 'INCOMPLETE.txt'
    marker.write_text('Incomplete export. Do not load.', encoding='utf-8')
    try:
        hashes, peak = {}, 0.0
        for i, row in enumerate(pads):
            _check_cancel(cancel)
            if usable(row):
                hashes[i] = file_hash(row['source'])
                audio = _load_pad_audio(row, 44100)
                peak = max(peak, float(np.max(np.abs(audio))))
        gain = (32767 / 32768) / peak if peak > 1 else 1.0
        names, rows = [], []
        for i, row in enumerate(pads):
            _check_cancel(cancel)
            if progress:
                progress(f'Exporting MPC pad {"ABCD"[i // 16]}{i % 16 + 1:02d}', i / len(pads))
            wav_name = ''
            if usable(row):
                if file_hash(row['source']) != hashes[i]:
                    raise ValueError('A sample changed during export.')
                audio = _load_pad_audio(row, 44100) * gain
                if not np.isfinite(audio).all() or float(np.max(np.abs(audio))) > 1:
                    raise ValueError('Audio changed after headroom checking.')
                wav_name = f'P{i + 1:02d}_' + hashes[i][:10].upper()
                target = folder / (wav_name + '.WAV')
                sf.write(target, audio, 44100, subtype='PCM_16')
                if file_hash(row['source']) != hashes[i]:
                    raise ValueError('A sample changed during export.')
                digest = file_hash(target)
            else:
                digest = None
            names.append(wav_name)
            rows.append({'pad': f'{"ABCD"[i // 16]}{i % 16 + 1:02d}', 'ni_pad': i % 16 + 1, 'sp_pad': row['sp_pad'],
                'name': row['name'], 'file': wav_name + '.WAV' if wav_name else '',
                'midi_note': pad_notes(None)[i], 'source': row.get('source'),
                'original_sha256': hashes.get(i), 'sha256': digest,
                'status': row['status'], 'replacement': row.get('replacement')})
        check_sources()
        pgm = folder / (name + '.PGM')
        pgm.write_bytes(program_bytes(names))
        validation = validate_program(pgm)
        _write_json(folder / 'MANIFEST.json', {'kit': kit, 'pads': rows, 'validation': validation,
            'banks': [bank['kit'] for bank in banks], 'common_gain': gain, 'processing': '44.1 kHz / 16-bit PCM; no Maschine effects or synth rendering.'})
        with (folder / 'PAD_MAP.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        (folder / 'LOAD_ME.txt').write_text(
            f'Copy this whole folder to the MPC card or disk. Load {name}.PGM with samples.\n' +
            ('Banks A-D are included. Pad 1 is bottom-left in each bank.\n' if len(banks) == 4 else 'One kit in bank A. Banks B-D are empty. Pad 1 is bottom-left.\n') +
            'WAVs are 44.1 kHz / 16-bit. No effects, envelopes, tuning or synths are rendered.\n'
            'MPC1000 MIDI OUT -> computer MIDI interface IN. Select that input in Connections.\n'
            'Use the MPC1000 standard profile, or MIDI Learn for changed pad notes.\n'
            'MIDI preview plays computer audio; route your audio interface outputs to your monitoring.\n'
            f"Sample memory: about {validation['sample_memory_mb']} MB plus device overhead.\n"
            'Program structure and files verified in software; physical JJOS loading still needs checking.\n',
            encoding='utf-8')
        marker.unlink()
        return folder, validation
    except Exception as exc:
        marker.write_text('Incomplete export. Do not load.\n' + str(exc), encoding='utf-8')
        raise
