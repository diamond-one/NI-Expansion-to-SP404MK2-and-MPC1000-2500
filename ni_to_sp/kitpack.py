"""Pack kept kits into MPC1000 programs: 4 banks x 2 kits, kit A on pads 1-8 and kit B on pads 9-16."""
import csv
import json
import os
from pathlib import Path
import shutil

from . import mpc1000 as mpc
from .kitlib import kit_name

PER_PROGRAM = 8
HAT_SLOTS = (2, 3)            # CL HAT and OP HAT choke each other, within their own kit


def program_name(first, last):
    return f'KITS_{first:03d}_{last:03d}'


def sample_name(number, short):
    return f'K{number:03d}_{short}'


def pad_for(position, slot):
    """MPC pad index (0-63) for slot 0-7 of the kit at `position` 0-7 within a program."""
    return (position // 2) * 16 + (position % 2) * 8 + slot


def program_data(manifests):
    """(pgm bytes, the 64 sample names) for up to 8 kit manifests."""
    names = [''] * 64
    for position, manifest in enumerate(manifests):
        for slot, row in enumerate(manifest['slots']):
            if row['wav']:
                names[pad_for(position, slot)] = sample_name(manifest['number'], Path(row['wav']).stem)
    data = bytearray(mpc.program_bytes(names))
    for position in range(len(manifests)):
        for slot in HAT_SLOTS:
            base = mpc.PAD_BASE + pad_for(position, slot) * mpc.PAD_SIZE
            if names[pad_for(position, slot)]:
                data[base + 0x63] = position + 1          # one mute group per kit: its hats choke each other only
    return bytes(data), names


def build_program(library, manifests, out_dir):
    """Write one program folder (PGM + its WAVs). Returns {'name', 'folder', 'kits', 'validation'}."""
    if not 1 <= len(manifests) <= PER_PROGRAM:
        raise ValueError(f'A program holds 1 to {PER_PROGRAM} kits.')
    out_dir = Path(out_dir)
    name = program_name(manifests[0]['number'], manifests[-1]['number'])
    final, temp = out_dir / name, out_dir / (name + '.tmp')
    shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir(parents=True)
    try:
        data, names = program_data(manifests)
        for position, manifest in enumerate(manifests):
            for slot, row in enumerate(manifest['slots']):
                if row['wav']:
                    source = library.wav(manifest, slot)
                    if source is None or not source.is_file():
                        raise ValueError(f"{manifest['id']} is missing {row['wav']}.")
                    shutil.copyfile(source, temp / (names[pad_for(position, slot)] + '.WAV'))
        pgm = temp / (name + '.PGM')
        pgm.write_bytes(data)
        validation = mpc.validate_program(pgm)
        info = {'name': name, 'kits': [m['id'] for m in manifests], 'validation': validation,
                'layout': 'Each bank holds two kits: kit A on pads 1-8 (bottom two rows), kit B on pads 9-16.'}
        (temp / 'PROGRAM.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
        (temp / 'LOAD_ME.txt').write_text(
            f'Copy this folder to the MPC card and load {name}.PGM with its samples.\n'
            'Banks A-D each hold two kits: pads 1-8 (bottom two rows) and pads 9-16 (top two rows).\n'
            'Kit layout: 1 KICK 2 SNARE 3 CL HAT 4 OP HAT / 5 CLAP 6 RIM 7 TOM 8 CYMBAL.\n'
            'Closed and open hat share a mute group inside each kit.\n'
            'Kits: ' + ', '.join(info['kits']) + '\n'
            f"Sample memory: about {validation['sample_memory_mb']} MB.\n", encoding='utf-8')
        shutil.rmtree(final, ignore_errors=True)
        os.replace(temp, final)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return {'name': name, 'folder': final, 'kits': info['kits'], 'validation': validation}


def write_catalogue(manifests, programs, out_dir):
    """CATALOGUE.csv: where every kit came from and where it lives on the MPC."""
    where = {}
    for program in programs:
        for position, kit_id in enumerate(program['kits']):
            where[kit_id] = (program['name'], 'ABCD'[position // 2], '1-8' if position % 2 == 0 else '9-16')
    path = Path(out_dir) / 'CATALOGUE.csv'
    labels = ['KICK', 'SNARE', 'CL HAT', 'OP HAT', 'CLAP', 'RIM', 'TOM', 'CYMBAL']
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['kit', 'program', 'bank', 'pads', 'character', 'expansion', 'source kit', 'half'] + labels)
        for manifest in manifests:
            program, bank, pads = where.get(manifest['id'], ('', '', ''))
            source = manifest['source']
            writer.writerow([manifest['id'], program, bank, pads, manifest['tag'], source['pack'], source['kit'],
                             source['half']] + [row['name'] for row in manifest['slots']])
    return path


def build_all(library, out_dir, progress=None):
    """Build every program from the whole library. Returns the list of program results."""
    manifests = library.kits()
    if not manifests:
        raise ValueError('Keep at least one kit first.')
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    programs = []
    chunks = [manifests[i:i + PER_PROGRAM] for i in range(0, len(manifests), PER_PROGRAM)]
    for n, chunk in enumerate(chunks):
        if progress:
            progress(f'Building {program_name(chunk[0]["number"], chunk[-1]["number"])}', n / len(chunks))
        programs.append(build_program(library, chunk, out_dir))
    write_catalogue(manifests, programs, out_dir)
    return programs
