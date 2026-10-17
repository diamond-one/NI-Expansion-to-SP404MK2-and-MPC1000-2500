"""Small generated archives exercise interoperability without distributing NI kits."""
from pathlib import Path
import numpy as np
import soundfile as sf


def reference(number):
    return b'\x02' + int(number).to_bytes(2, 'little')


def record(name, number, old=False, first=False):
    raw = name.encode('utf-8')
    return reference(number - 1) + (b'\0\0' if old and first else b'') + (b'\x01' if old else b'\0\x01') + bytes([len(raw)]) + raw + b''.join(
        reference(number + i) for i in range(3 if old else 4)) + b'\0' * 8


def direct_fixture(root, missing=(), layered=(), old=False, name='Direct Kit'):
    root = Path(root)
    pack = root / 'installed/Test Library'
    source = pack / 'Groups/Kits' / (name + '.mxgrp')
    source.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(b'Generated test archive\0')
    for index in range(16):
        title = ('Kick' if index == 0 else 'ClosedHH' if index == 2 else 'OpenHH' if index == 3 else 'Sound') + f' Test {index + 1}'
        data += record(title, 1000 + index * 30, old, index == 0)
        if index in missing:
            data += b'NI::MASCHINE::DATA::BassSynth\0'
            continue
        for layer in range(2 if index in layered else 1):
            relative = f'Samples/Drums/Percussion/Hit {index + 1}-{layer}.wav'
            path = pack / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            t = np.arange(4800) / 48000
            wave = (.1 + .01 * index) * np.cos(t * (300 + index * 40)) * np.exp(-t * 40)
            sf.write(path, wave, 48000, subtype='PCM_24')
            data += relative.encode() + b'\0'
    data += record(name, 2000, old)
    # Pattern records after the group must never be counted as sound slots.
    data += record('Pattern A', 2100, old)
    source.write_bytes(data)
    return {'id': name, 'name': name, 'pack': 'Test', 'root': str(pack), 'path': str(source), 'preview': None}
