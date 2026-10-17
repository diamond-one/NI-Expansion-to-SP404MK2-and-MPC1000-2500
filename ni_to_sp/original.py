"""Resumable, checked Maschine-engine renders; never substitute raw samples."""
import copy
import ctypes as C
from ctypes import wintypes as W
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

import mido
import numpy as np
import soundfile as sf

from .capture import create_capture, file_hash, inspect_render, load_prepared, safe_name
from .direct import _record_names, _read_group
from .model import sp_pad_number
from .sp_transfer import _write_json

VERSION = 2


def engine_inventory():
    base = Path(os.environ.get('ProgramFiles', 'C:/Program Files'))
    roots = [base / 'Native Instruments/VSTPlugins 64 bit']
    common = base / 'Common Files/VST3'
    if common.is_dir():
        roots += [p for p in common.iterdir() if p.name.startswith(
            ('Maschine', 'Kontakt', 'Reaktor', 'Massive', 'FM8', 'Absynth', 'Komplete'))]
    rows = []
    for root in roots:
        for path in ([root] if root.is_file() else root.rglob('*') if root.is_dir() else []):
            if path.is_file() and path.suffix.casefold() in {'.dll', '.vst3'}:
                stat = path.stat()
                rows.append((str(path), stat.st_size, stat.st_mtime_ns))
    return hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest()


def source_dependencies(kit):
    root = Path(kit['root']).resolve()
    raw = _read_group(kit['path'])
    paths = set()
    for match in re.finditer(rb'Samples[\\/][^\x00-\x1f]{1,1024}?\.(?:wav|wave|aif|aiff|flac)(?=[^a-zA-Z]|$)', raw, re.I):
        relative = match.group().decode('utf-8').replace('\\', '/')
        path = root.joinpath(*relative.split('/')).resolve()
        if not path.is_relative_to(root):
            raise ValueError('A sample reference escapes its installed expansion.')
        paths.add(path)
    return [{'path': str(path), 'sha256': file_hash(path) if path.is_file() else None}
            for path in sorted(paths)]


def completed_index(destination):
    destination = Path(destination).resolve()
    index = {}
    for path in sorted(destination.glob('*/*/KIT_RECIPE.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True):
        if not path.resolve().is_relative_to(destination) or path.stat().st_size > 2 * 1024**2:
            continue
        try:
            recipe = json.loads(path.read_text(encoding='utf-8'))
            job = recipe.get('capture_job', {})
            if recipe.get('mode') == 'original' and job.get('original_version') == VERSION:
                index.setdefault(job['kit']['id'], []).append((path.parent, recipe))
        except (OSError, ValueError, KeyError):
            continue
    return index


def reusable_original(kit, candidates, inventory):
    for folder, recipe in candidates:
        job = recipe['capture_job']
        try:
            if job.get('kit_sha256') != file_hash(kit['path']) or job.get('engine_inventory') != inventory:
                continue
            dependencies = source_dependencies(kit)
            if any(row['sha256'] is None for row in dependencies) or job.get('source_dependencies') != dependencies:
                continue
            if (job.get('hold'), job.get('velocity'), job.get('bpm')) != (.5, 100, 120):
                continue
            load_prepared(folder)
            return folder
        except (OSError, ValueError, KeyError):
            continue
    return None


def worker_identity(pid=None):
    """PID plus creation time prevents mistaking a reused PID for our worker."""
    if os.name != 'nt':
        return None
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = W.HANDLE
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.GetProcessTimes.argtypes = [W.HANDLE, C.POINTER(W.FILETIME), C.POINTER(W.FILETIME), C.POINTER(W.FILETIME), C.POINTER(W.FILETIME)]
    kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    handle = kernel.OpenProcess(0x101000, False, pid or os.getpid())
    if not handle:
        return None
    try:
        values = [W.FILETIME() for _ in range(4)]
        if kernel.WaitForSingleObject(handle, 0) != 258 or not kernel.GetProcessTimes(handle, *(C.byref(v) for v in values)):
            return None
        return values[0].dwLowDateTime | values[0].dwHighDateTime << 32
    finally:
        kernel.CloseHandle(handle)


class WorkerLock:
    def __enter__(self):
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
        self.kernel.CreateMutexW.restype = W.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        self.kernel.ReleaseMutex.argtypes = [W.HANDLE]
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.handle = self.kernel.CreateMutexW(None, False, 'Local\\NIToSP-OriginalMaschineRenderer')
        if not self.handle:
            raise RuntimeError('Could not create the renderer lock.')
        if self.kernel.WaitForSingleObject(self.handle, 0) not in (0, 128):
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            raise RuntimeError('Another original-render queue is running. Pause it before starting another.')
        return self

    def __exit__(self, *_):
        if self.handle:
            self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)


def guarded_capture(kit, destination, spacing=8):
    folder, job = create_capture(kit, destination, spacing=spacing)
    midi = mido.MidiFile(folder / 'CAPTURE.mid')
    track = midi.tracks[0]
    track.pop()  # Imported patterns drop an empty end-of-track extension.
    last_tick = sum(event.time for event in track)
    guard_tick = round((job['duration'] + 4) * job['bpm'] * midi.ticks_per_beat / 60)
    track.append(mido.Message('note_on', note=36, velocity=1, time=guard_tick - last_tick))
    track.append(mido.Message('note_off', note=36, velocity=0, time=midi.ticks_per_beat))
    track.append(mido.MetaMessage('end_of_track', time=0))
    midi.save(folder / 'CAPTURE.mid')
    job.update(mode='original', original_version=VERSION,
               guard_note_seconds=guard_tick * 60 / (job['bpm'] * midi.ticks_per_beat))
    _write_json(folder / 'CAPTURE.json', job)
    return folder, job


def project_names(project, kit):
    raw = Path(project).read_bytes()
    if len(raw) > 256 * 1024**2 or b'2SAMJORP' not in raw[:4096]:
        raise ValueError('Maschine saved an unsupported capture project.')
    if b'NI::MASCHINE::DATA::NoteEvent' not in raw:
        raise ValueError('The saved project has no capture sequence.')
    for layout in (4, 3):
        records = _record_names(raw, references=layout)
        groups = [i for i, (_, name) in enumerate(records) if name == kit['name'] and i >= 16]
        if len(groups) == 1:
            return [name for _, name in records[groups[0] - 16:groups[0]]]
    raise ValueError('The saved project does not verify this kit and its original 16 sound slots.')


def prepare_original(job, project, render, destination, engine, cancel=None):
    """Validate the capture, retain processing/relative gain, trim end silence."""
    if file_hash(job['kit']['path']) != job['kit_sha256']:
        raise ValueError('The installed kit changed during capture.')
    dependencies = source_dependencies(job['kit'])
    if 'source_dependencies' in job and dependencies != job['source_dependencies']:
        raise ValueError('Referenced samples changed during capture. Render this kit again.')
    job['source_dependencies'] = dependencies
    names = project_names(project, job['kit'])
    master = Path(render)
    peak = 0.0
    with sf.SoundFile(master) as audio:
        if audio.samplerate != 48000 or audio.channels != 2:
            raise ValueError('The engine must render stereo 48 kHz audio.')
        for block in audio.blocks(blocksize=48000, dtype='float32', always_2d=True):
            if not np.isfinite(block).all():
                raise ValueError('Invalid audio from Maschine.')
            peak = max(peak, float(np.max(np.abs(block))))
    gain = (32767 / 32768) / peak if peak > 1 else 1.0
    checked = master
    if gain < 1:
        checked = master.with_name('master-headroom.wav')
        with sf.SoundFile(master) as source, sf.SoundFile(checked, 'w', samplerate=48000, channels=2, subtype='FLOAT') as target:
            for block in source.blocks(blocksize=48000, dtype='float32', always_2d=True):
                target.write(block * gain)
    report = inspect_render(job, checked, confirmed=True, cancel=cancel)
    if not report['ok']:
        raise ValueError('; '.join(report['problems']))
    report['kit_identity'] = 'Requested group path loaded in a fresh private project; saved group and all 16 sound names verified'
    destination = Path(destination).resolve()
    if destination.is_relative_to(Path(job['kit']['root']).resolve()):
        raise ValueError('Original renders must stay outside installed expansions.')
    signature = hashlib.sha256(json.dumps({'version': VERSION, 'kit': job['kit_sha256'],
        'bpm': job['bpm'], 'spacing': job['spacing'], 'hold': job['hold'], 'velocity': job['velocity'],
        'engine': engine.get('engine_version'), 'engine_inventory': job.get('engine_inventory'),
        'source_dependencies': dependencies,
        'forced_capture': file_hash(project) if job.get('force') or any(row['sha256'] is None for row in dependencies) else None}, sort_keys=True).encode()).hexdigest()
    pack = destination / safe_name(job['kit']['pack'])
    output = pack / (safe_name(job['kit']['name']) + '-' + signature[:8])
    if output.exists():
        try:
            session, recipe = load_prepared(output)
            if recipe.get('original_signature') == signature:
                return output, session
        except (OSError, ValueError, KeyError):
            pass
        output = output.with_name(output.name + '-' + uuid.uuid4().hex[:6])
    output.mkdir(parents=True)
    marker = output / 'INCOMPLETE.txt'
    marker.write_text('Incomplete original capture. Do not import.\n', encoding='utf-8')
    rows = []
    try:
        with sf.SoundFile(checked) as source:
            source.seek(0)
            floor = float(np.sqrt(np.mean(source.read(48000, dtype='float64', always_2d=True)**2)))
            for i, slot in enumerate(job['slots']):
                if cancel and cancel.is_set():
                    raise InterruptedError('Original rendering paused.')
                source.seek(round(slot['start'] * 48000))
                data = source.read(round(job['spacing'] * 48000), dtype='float32', always_2d=True)
                threshold = max(float(np.max(np.abs(data))) * 1e-5, floor * 2, 1e-7)
                audible = np.flatnonzero(np.max(np.abs(data), axis=1) > threshold)
                # Keep the full attack/pre-delay and 50 ms after the last
                # audible frame. The render check already rejects cut tails.
                end = min(len(data), max(4800, int(audible[-1]) + 2401))
                data = data[:end]
                name = f'{slot["sp_pad"]:02d}_' + safe_name(names[i])[:55] + '.wav'
                target = output / name
                sf.write(target, data, 48000, subtype='PCM_16')
                info = sf.info(target)
                if (info.samplerate, info.subtype, info.channels, info.frames) != (48000, 'PCM_16', 2, end):
                    raise ValueError('A prepared original WAV failed validation.')
                digest = file_hash(target)
                identity = hashlib.sha256(f'{digest}:{i}'.encode()).hexdigest()[:20]
                rows.append({'logical_slot': i + 1, 'ni_pad': i + 1, 'pad': sp_pad_number(i), 'name': names[i],
                    'file': name, 'sample_id': identity, 'source_sha256': digest, 'output_sha256': digest,
                    'source_status': 'captured', 'intentional_empty': False, 'duration': end / 48000})
                report['pads'][i].update(name=names[i], status='captured', source=str(project))
        recipe = {'schema': 1, 'kit': 1, 'kit_size': 16, 'target': 'sp404', 'seed': 1000, 'mode': 'original',
            'pads': rows, 'capture_job': job, 'capture_report': report, 'engine': engine,
            'original_signature': signature, 'project_sha256': file_hash(project),
            'processing': {'common_gain': gain, 'headroom_db': 20 * math.log10(gain), 'trim_trailing_silence': True},
            'fidelity': 'One C3 note at velocity 100 per pad through the loaded sound/group/master chain. Shared effects react to isolated hits.'}
        _write_json(output / 'KIT_RECIPE.json', recipe)
        with (output / 'PAD_MAP.csv').open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.writer(handle)
            writer.writerow(['Maschine pad', 'SP pad', 'Sound', 'File'])
            writer.writerows([row['ni_pad'], row['pad'], row['name'], row['file']] for row in rows)
        _write_json(output / 'COMPLETE.json', {'complete': True, 'validated': True, 'files': 16})
        marker.unlink()
        return output, load_prepared(output)[0]
    except Exception as exc:
        marker.write_text('Incomplete original capture. Do not import.\n' + str(exc), encoding='utf-8')
        raise


def create_queue(kits, destination, workdir, force=False):
    if not kits:
        raise ValueError('No kits in this selection.')
    destination = Path(destination).resolve()
    if any(destination.is_relative_to(Path(kit['root']).resolve()) for kit in kits):
        raise ValueError('Choose an output folder outside installed expansions.')
    destination.mkdir(parents=True, exist_ok=True)
    workdir = Path(workdir).resolve() / ('original-' + uuid.uuid4().hex[:8])
    workdir.mkdir(parents=True)
    queue = {'schema': 1, 'mode': 'original', 'state': 'ready', 'destination': str(destination),
        'workdir': str(workdir), 'total': len(kits), 'completed': [], 'failed': [],
        'pending': copy.deepcopy(kits), 'current': None, 'message': 'Ready to render',
        'force': bool(force), 'updated': time.time()}
    path = destination / ('ORIGINAL-BATCH-' + uuid.uuid4().hex[:8] + '.json')
    _write_json(path, queue)
    return path


def command(flag, request):
    if getattr(sys, 'frozen', False):
        return [sys.executable, flag, str(request)]
    return [sys.executable, str(Path(__file__).parent.parent / 'run.py'), flag, str(request)]


def run_engine(project, render, job, folder, cancel=None):
    request = Path(folder) / 'ENGINE_REQUEST.json'
    report_path = Path(folder) / 'ENGINE_RESULT.json'
    _write_json(request, {'project': str(project), 'target': str(render),
        'duration': job['duration'], 'bpm': job['bpm'], 'expected_kit': job['kit']['name'],
        'report': str(report_path)})
    with (Path(folder) / 'ENGINE.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command('--original-render', request), stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 900
        while process.poll() is None:
            if cancel and cancel.is_set():
                process.terminate()
                process.wait(timeout=15)
                raise InterruptedError('Original rendering paused.')
            if time.monotonic() > deadline:
                process.kill()
                process.wait(timeout=15)
                raise ValueError('Maschine engine timed out. See the private ENGINE.log.')
            time.sleep(.15)
    if not report_path.is_file():
        raise ValueError('Maschine engine failed. See ' + str(Path(folder) / 'ENGINE.log'))
    result = json.loads(report_path.read_text(encoding='utf-8'))
    if not result.get('ok'):
        raise ValueError(result.get('error', 'Maschine rendering failed.'))
    # NI can fail during DLL process detach after a fully written render.
    # Accept it only when the success receipt and complete WAV hash agree;
    # the parent still inspects every pad before creating a completed kit.
    if result.get('render_sha256') != file_hash(render):
        raise ValueError('The engine render does not match its completion receipt.')
    info = sf.info(render)
    if (info.frames, info.samplerate, info.channels) != (round(job['duration'] * 48000), 48000, 2) or result.get('project_sha256') != file_hash(project):
        raise ValueError('The engine completion receipt does not verify the complete capture state and audio.')
    result['shutdown_exit_code'] = process.returncode
    return result


def render_request(path):
    from .maschine_engine import render_project
    request = json.loads(Path(path).read_text(encoding='utf-8'))
    report_path = Path(request.pop('report'))
    try:
        result = render_project(**request)
        result['render_sha256'] = file_hash(request['target'])
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)}
    _write_json(report_path, result)
    return bool(result['ok'])


class StopFile:
    def __init__(self, path):
        self.path = Path(path)
    def is_set(self):
        return self.path.exists()


def _run_queue_locked(path, desktop_factory=None, engine_runner=run_engine):
    from .maschine_desktop import MaschineDesktop, DesktopAttention
    path = Path(path)
    queue = json.loads(path.read_text(encoding='utf-8'))
    if queue.get('schema') != 1 or queue.get('mode') != 'original' or not isinstance(queue.get('pending'), list):
        raise ValueError('This is not a supported original-render queue.')
    queue.update(worker_pid=os.getpid(), worker_created=worker_identity())
    cancel = StopFile(path.with_suffix('.pause'))
    def save(message, state='running'):
        queue.update(message=message, state=state, updated=time.time())
        _write_json(path, queue)
    try:
        desktop = None
        index, inventory = completed_index(queue['destination']), engine_inventory()
        while queue['pending']:
            if cancel.is_set():
                raise InterruptedError('Original rendering paused.')
            kit = queue['pending'][0]
            queue['current'] = {'kit': kit['name'], 'pack': kit['pack']}
            save(f'Loading {kit["pack"]} / {kit["name"]}')
            try:
                reused = None if queue.get('force') else reusable_original(kit, index.get(kit['id'], []), inventory)
                if reused:
                    queue['completed'].append({'kit': kit['name'], 'pack': kit['pack'], 'folder': str(reused), 'reused': True})
                    queue['pending'].pop(0)
                    save(f'Reused verified original: {kit["name"]}')
                    continue
                if desktop is None:
                    desktop = desktop_factory(cancel) if desktop_factory else MaschineDesktop(cancel, lambda message: save(message, 'waiting_for_maschine'))
                # Each retry gets a fresh, guarded pattern with a larger gap.
                for spacing in (8, 16, 32):
                    folder, job = guarded_capture(kit, queue['workdir'], spacing)
                    job.update(source_dependencies=source_dependencies(kit), engine_inventory=inventory, force=queue.get('force', False))
                    _write_json(folder / 'CAPTURE.json', job)
                    project, render = folder / 'NI-to-SP render session.mxprj', folder / 'master.wav'
                    required = round(job['duration'] * 48000 * 8) + 500 * 1024**2
                    if any(shutil.disk_usage(place).free < required for place in (folder, queue['destination'])):
                        raise DesktopAttention('Not enough free disk space for the next original render. Free space, then resume.')
                    desktop.capture_state(kit, folder / 'CAPTURE.mid', project)
                    project_names(project, kit)
                    save(f'Rendering {kit["name"]} · {spacing}s between pads')
                    try:
                        engine = engine_runner(project, render, job, folder, cancel)
                        try:
                            output, _ = prepare_original(job, project, render, queue['destination'], engine, cancel)
                            break
                        except ValueError as exc:
                            if 'tail reaches the next capture boundary' not in str(exc) or spacing == 32:
                                raise
                    finally:
                        # Only these scratch WAVs in this freshly created job
                        # folder are removed. NI sources and final kits remain.
                        for scratch in (render, folder / 'master-headroom.wav'):
                            if scratch.is_file():
                                scratch.unlink()
                queue['completed'].append({'kit': kit['name'], 'pack': kit['pack'], 'folder': str(output),
                    'kit_sha256': job['kit_sha256'], 'spacing': spacing})
            except (DesktopAttention, InterruptedError):
                raise
            except Exception as exc:
                queue['failed'].append({'kit': kit['name'], 'pack': kit['pack'], 'reason': str(exc)})
            queue['pending'].pop(0)
            save(f'{len(queue["completed"])} rendered · {len(queue["failed"])} failed · {len(queue["pending"])} remaining')
        queue['current'] = None
        save('Collection rendering finished. Review the completed and failed lists.', 'complete')
    except DesktopAttention as exc:
        save(str(exc), 'needs_attention')
    except InterruptedError as exc:
        save(str(exc), 'paused')
    except Exception as exc:
        save(str(exc), 'needs_attention')
    finally:
        queue.update(worker_pid=None, worker_created=None)
        _write_json(path, queue)
    return queue


def run_queue(path, desktop_factory=None, engine_runner=run_engine):
    try:
        with WorkerLock():
            return _run_queue_locked(path, desktop_factory, engine_runner)
    except RuntimeError as exc:
        path = Path(path)
        queue = json.loads(path.read_text(encoding='utf-8'))
        if not queue.get('worker_pid') or worker_identity(queue['worker_pid']) != queue.get('worker_created'):
            queue.update(state='needs_attention', message=str(exc), worker_pid=None, worker_created=None, updated=time.time())
            _write_json(path, queue)
        return queue
