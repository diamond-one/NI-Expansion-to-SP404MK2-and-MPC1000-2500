"""Isolated Windows VST2 host for an already prepared Maschine capture project.

Maschine and its instruments remain installed/licensed on the user's computer.
No NI engine or preset data is distributed with this application.
"""
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import time

import numpy as np
import soundfile as sf

I, P, V = C.c_int32, C.c_void_p, C.c_ssize_t
HOST = C.CFUNCTYPE(V, P, I, I, V, P, C.c_float)
DISPATCH = C.CFUNCTYPE(V, P, I, I, V, P, C.c_float)
PROCESS = C.CFUNCTYPE(None, P, C.POINTER(C.POINTER(C.c_float)),
                    C.POINTER(C.POINTER(C.c_float)), I)
_native_keepalive = []


class Effect(C.Structure):
    _fields_ = [('magic', I), ('dispatcher', P), ('process', P), ('setParameter', P), ('getParameter', P),
                ('numPrograms', I), ('numParams', I), ('numInputs', I), ('numOutputs', I), ('flags', I),
                ('reserved1', V), ('reserved2', V), ('initialDelay', I), ('realQualities', I),
                ('offQualities', I), ('ioRatio', C.c_float), ('object', P), ('user', P), ('uniqueID', I),
                ('version', I), ('processReplacing', P), ('processDoubleReplacing', P), ('future', C.c_char * 56)]


class TimeInfo(C.Structure):
    _fields_ = [(n, C.c_double) for n in
                ('samplePos', 'sampleRate', 'nanoSeconds', 'ppqPos', 'tempo',
                 'barStartPos', 'cycleStartPos', 'cycleEndPos')] + [(n, I) for n in
                ('timeSigNumerator', 'timeSigDenominator', 'smpteOffset', 'smpteFrameRate',
                 'samplesToNextClock', 'flags')]


def plugin_path():
    path = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Native Instruments/VSTPlugins 64 bit/Maschine 2.dll'
    if not path.is_file():
        raise ValueError('Install the 64-bit Maschine 2 VST plug-in with Native Access before rendering originals.')
    return path


def render_project(project, target, duration, bpm=120, expected_kit=None, progress=None):
    """Call only in a separate process; native plug-ins may hang or crash."""
    if os.name != 'nt' or struct.calcsize('P') != 8:
        raise ValueError('Original Maschine rendering requires 64-bit Windows.')
    C.WinDLL('kernel32').SetErrorMode(0x8003)
    duration, bpm = float(duration), float(bpm)
    if not math.isfinite(duration) or not 1 <= duration <= 1100 or not 40 <= bpm <= 240:
        raise ValueError('Invalid capture duration or tempo.')
    project, target = Path(project).resolve(strict=True), Path(target).resolve()
    if project.suffix.casefold() != '.mxprj' or project.stat().st_size > 256 * 1024**2:
        raise ValueError('Choose a bounded Maschine capture project.')
    raw = project.read_bytes()
    if b'2SAMJORP' not in raw[:4096] or b'NI::MASCHINE::DATA::NoteEvent' not in raw:
        raise ValueError('The capture project has no internal pad sequence.')
    if expected_kit and expected_kit.encode('utf-8') not in raw:
        raise ValueError('Saved capture project does not identify the requested kit.')
    source_hash = hashlib.sha256(raw).hexdigest()
    if project == target or target.suffix.casefold() != '.wav':
        raise ValueError('Render to a separate WAV file.')
    target.parent.mkdir(parents=True, exist_ok=True)
    transport = TimeInfo(0, 48000, 0, 0, bpm, 0, 0, 0, 4, 4, 0, 0, 0, 2 | 512 | 1024 | 2048 | 8192)
    requests = {'file_selector': False}

    @HOST
    def host(effect, opcode, index, value, ptr, opt):
        if opcode == 1:
            return 2400
        if opcode == 7:
            return C.addressof(transport)
        if opcode == 16:
            return 48000
        if opcode == 17:
            return 512
        if opcode == 23:
            return 4  # Offline processing.
        if opcode in (13, 14):
            return 1
        if opcode in (32, 33) and ptr:
            text = b'NI to SP\x00'
            C.memmove(ptr, text, len(text))
            return 1
        if opcode == 34:
            return 1000
        if opcode == 37 and ptr:
            capability = C.string_at(ptr)
            return int(capability in {b'sendVstTimeInfo', b'acceptIOChanges', b'offline', b'startStopProcess'})
        if opcode == 45:
            requests['file_selector'] = True
        return 0

    library = C.CDLL(str(plugin_path()))
    main = library.VSTPluginMain
    main.argtypes, main.restype = [HOST], C.POINTER(Effect)
    effect = main(host)
    _native_keepalive.append((library, host, effect, transport))
    if not effect or effect.contents.magic != 1450406992:
        raise ValueError('The installed plug-in did not initialize as VST2.')
    dispatcher = DISPATCH(effect.contents.dispatcher)
    dispatcher(effect, 0, 0, 0, None, 0)
    dispatcher(effect, 10, 0, 0, None, 48000)
    dispatcher(effect, 11, 0, 512, None, 0)
    state = C.create_string_buffer(raw)
    dispatcher(effect, 24, 0, len(raw), state, 0)
    # Sample-loading threads must finish before advancing the capture clock.
    time.sleep(2)
    current = P()
    size = dispatcher(effect, 23, 0, 0, C.byref(current), 0)
    if not 0 < size <= 256 * 1024**2:
        raise ValueError('Maschine did not return a valid loaded project.')
    loaded = C.string_at(current, size)
    if expected_kit and expected_kit.encode('utf-8') not in loaded:
        raise ValueError('Maschine did not retain the requested kit.')
    e = effect.contents
    if not 0 <= e.numInputs <= 64 or not 2 <= e.numOutputs <= 64 or not e.processReplacing:
        raise ValueError('Unsupported Maschine audio channel configuration.')
    if not 0 <= e.initialDelay <= 48000 * 10:
        raise ValueError('Unsupported plug-in latency.')
    inputs = np.zeros((e.numInputs, 512), np.float32)
    outputs = np.zeros((e.numOutputs, 512), np.float32)
    ins = (C.POINTER(C.c_float) * e.numInputs)(*[row.ctypes.data_as(C.POINTER(C.c_float)) for row in inputs])
    outs = (C.POINTER(C.c_float) * e.numOutputs)(*[row.ctypes.data_as(C.POINTER(C.c_float)) for row in outputs])
    process = PROCESS(e.processReplacing)
    frames, latency = round(duration * 48000), e.initialDelay
    dispatcher(effect, 12, 0, 1, None, 0)
    dispatcher(effect, 71, 0, 0, None, 0)
    peak, written = 0.0, 0
    started = time.monotonic()
    with sf.SoundFile(target, 'w', samplerate=48000, channels=2, subtype='FLOAT') as audio:
        for position in range(0, frames + latency, 512):
            transport.samplePos = position
            transport.ppqPos = position * bpm / (48000 * 60)
            transport.barStartPos = int(transport.ppqPos // 4) * 4
            transport.flags = 2 | 512 | 1024 | 2048 | 8192 | (1 if position == 0 else 0)
            outputs.fill(0)
            process(effect, ins, outs, 512)
            if requests['file_selector']:
                raise ValueError('A loaded instrument requested a missing file. Resolve it in Maschine.')
            lo, hi = max(0, latency - position), min(512, frames + latency - position)
            data = outputs[:2, lo:hi].T
            if not np.isfinite(data).all():
                raise ValueError('Maschine produced non-finite audio.')
            if len(data):
                peak = max(peak, float(np.max(np.abs(data))))
                audio.write(data)
                written += len(data)
            if progress and position % (512 * 96) == 0:
                progress(position / (frames + latency))
    dispatcher(effect, 72, 0, 0, None, 0)
    dispatcher(effect, 12, 0, 0, None, 0)
    if written != frames or hashlib.sha256(project.read_bytes()).hexdigest() != source_hash:
        raise ValueError('The capture state or rendered length changed.')
    result = {'ok': True, 'frames': written, 'rate': 48000, 'channels': 2, 'peak': peak,
              'latency_frames': latency, 'project_sha256': source_hash, 'seconds': time.monotonic() - started,
              'engine': str(plugin_path()), 'engine_version': e.version,
              'engine_size': plugin_path().stat().st_size, 'engine_mtime_ns': plugin_path().stat().st_mtime_ns}
    # The isolated worker exits after recording its report. Some instruments
    # hang during normal plug-in teardown; no host state needs to be retained.
    return result
