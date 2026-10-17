from collections import deque
from dataclasses import dataclass
from pathlib import Path
import threading
import numpy as np
from .audio import load_audio, list_audio_outputs, preferred_audio_output
from .windows_audio import audio_apartment

@dataclass
class _Voice:
    pad: int
    data: np.ndarray
    position: int
    gain: float
    group: int | None
    release: int | None = None


class LiveSampler:
    """Preloaded, polyphonic pad player. MIDI callbacks never open audio files."""
    def __init__(self, device=None, samplerate=None, blocksize=256, on_error=lambda message: None):
        self.device = device
        if samplerate is None:
            import sounddevice as sd
            info = sd.query_devices(device, "output")
            samplerate = int(info["default_samplerate"])
        self.sample_rate = int(samplerate)
        self.blocksize = int(blocksize)
        self.on_error = on_error
        self.loaded_bank = None
        self._stream = None
        self._pads = {}
        self._groups = {}
        self._voices = []
        self._commands = deque(maxlen=256)
        self._lock = threading.Lock()
        self._last_status = ""
        self.max_voices = 32
        self.output_channels = 2
        self._audio_thread = None
        self._stop_requested = threading.Event()
        self._started = threading.Event()
        self._start_error = None
        self._cache = {}

    @property
    def running(self):
        return self._stream is not None and self._stream.active

    def prepare(self, session, bank=0, preserve=False):
        if not 0 <= bank < len(session.pads) // 16:
            raise ValueError("Choose a valid preview bank.")
        pads, groups, notes, cache = {}, {}, [], {}
        memory = 0
        samples = session.by_id
        for local in range(16):
            assignment = session.pads[bank * 16 + local]
            sample = samples.get(assignment.sample_id)
            if sample is None or sample.kind == "excluded":
                continue
            if not sample.features.get("available", True):
                notes.append(f"Pad {local + 1}: source unavailable")
                continue
            try:
                key = (sample.path, sample.features.get('size'), sample.features.get('mtime_ns'), sample.fingerprint)
                data = self._cache.get(key)
                if data is None:
                    data = load_audio(sample, rate=self.sample_rate)
                elif key[1] is not None:
                    stat = Path(sample.path).stat()
                    if (stat.st_size, stat.st_mtime_ns) != key[1:3]:
                        raise ValueError(f'{Path(sample.path).name} changed. Reopen the kit.')
                if data.shape[1] == 1:
                    data = np.repeat(data, 2, axis=1)
                memory += data.nbytes
                if memory > 128 * 1024 ** 2:
                    raise ValueError('This kit exceeds the 128 MB live preview limit. Use shorter samples.')
                pads[local] = np.ascontiguousarray(data, dtype=np.float32)
                cache[key] = pads[local]
            except (OSError, ValueError, RuntimeError) as exc:
                notes.append(str(exc))
                continue
            if sample.kind in ('hat', 'open_hat'):
                groups[local] = local // session.kit_size
        if not pads:
            if preserve:
                with self._lock:
                    self._pads, self._groups, self._cache = {}, {}, {}
                    self.loaded_bank = bank
                return {"loaded": 0, "notes": notes, "bank": bank, "samplerate": self.sample_rate}
            with self._lock:
                self._pads, self._groups = {}, {}
                self.loaded_bank = bank
                self._commands.clear()
                self._commands.append(("reset", None, None))
            raise ValueError("This bank has no available samples to preview. Generate a kit first.")
        with self._lock:
            self._pads, self._groups = pads, groups
            self._cache = cache
            self.loaded_bank = bank
            if not preserve:
                self._commands.clear()
                self._commands.append(("reset", None, None))
        return {"loaded": len(pads), "notes": notes, "bank": bank, "samplerate": self.sample_rate}

    def update_bank(self, session, bank=0):
        """Swap prepared pads atomically; existing voices retain their audio."""
        return self.prepare(session, bank, preserve=True)

    def start(self):
        if self.running:
            return
        self._stop_requested.clear()
        self._started.clear()
        self._start_error = None
        def service():
            try:
                with audio_apartment():
                    try:
                        self._open_stream()
                        self._started.set()
                        self._stop_requested.wait()
                    finally:
                        self._close_stream()
            except Exception as exc:
                self._start_error = exc
            finally:
                self._started.set()
        self._audio_thread = threading.Thread(target=service, daemon=True, name='NI-to-SP-audio')
        self._audio_thread.start()
        if not self._started.wait(15):
            self._stop_requested.set()
            raise RuntimeError('Audio driver did not start. Choose another output in Connections.')
        if self._start_error is not None:
            raise self._start_error

    def _open_stream(self):
        import sounddevice as sd
        try:
            sd.check_output_settings(device=self.device, channels=2, dtype="float32", samplerate=self.sample_rate)
        except sd.PortAudioError:
            channels = int(sd.query_devices(self.device, "output")["max_output_channels"])
            if channels != 4:
                raise
            sd.check_output_settings(device=self.device, channels=channels, dtype="float32", samplerate=self.sample_rate)
            self.output_channels = channels
        self._stream = sd.OutputStream(device=self.device, samplerate=self.sample_rate, channels=self.output_channels,
                                       dtype="float32", blocksize=self.blocksize, latency="low",
                                       callback=self._callback)
        try:
            self._stream.start()
        except Exception:
            self._stream.close()
            self._stream = None
            raise

    def trigger(self, local_index, velocity=127):
        if not 0 <= local_index < 16 or velocity <= 0:
            return False
        with self._lock:
            data = self._pads.get(local_index)
            if data is None:
                return False
            self._commands.append((local_index, data, min(127, velocity) / 127))
        return True

    def silence(self):
        with self._lock:
            self._commands.clear()
            self._commands.append(("reset", None, None))

    def silence_pad(self, index):
        with self._lock:
            self._commands.append(('stop_pad', index, None))

    def _render(self, frames):
        """Pure block mixer, also exercised without an attached audio device."""
        with self._lock:
            commands = list(self._commands)
            self._commands.clear()
            groups = self._groups
        for pad, data, velocity in commands:
            if pad == "reset":
                self._voices.clear()
                continue
            if pad == 'stop_pad':
                self._voices = [voice for voice in self._voices if voice.pad != data]
                continue
            group = groups.get(pad)
            if group is not None:
                for voice in self._voices:
                    if voice.group == group:
                        voice.release = min(voice.release or 64, 64)
            self._voices.append(_Voice(pad, data, 0, .65 * velocity, group))
            self._voices = self._voices[-self.max_voices:]
        output = np.zeros((frames, 2), dtype=np.float32)
        alive = []
        for voice in self._voices:
            length = min(frames, len(voice.data) - voice.position)
            if voice.release is not None:
                length = min(length, voice.release)
            if length <= 0:
                continue
            clip = voice.data[voice.position:voice.position + length] * voice.gain
            if voice.release is not None:
                clip = clip * (np.arange(voice.release, voice.release - length, -1, dtype=np.float32) / 64)[:, None]
                voice.release -= length
            output[:length] += clip
            voice.position += length
            if voice.position < len(voice.data) and voice.release != 0:
                alive.append(voice)
        self._voices = alive
        np.clip(output, -.95, .95, out=output)
        return output

    def _callback(self, outdata, frames, timing, status):
        try:
            outdata.fill(0)
            outdata[:, :2] = self._render(frames)
            if status and str(status) != self._last_status:
                self._last_status = str(status)
                self.on_error("Audio preview: " + str(status))
        except Exception as exc:
            outdata.fill(0)
            self.on_error(str(exc))

    def stop(self):
        self._stop_requested.set()
        thread = self._audio_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        self._audio_thread = None
        with self._lock:
            self._commands.clear()
            self._voices.clear()

    def _close_stream(self):
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    close = stop



