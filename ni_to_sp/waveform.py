"""Small asynchronous source waveform; never opens an audio output."""
from pathlib import Path
import queue
import threading
import tkinter as tk

import numpy as np
import soundfile as sf

from . import desk_theme as T


class Waveform(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=72, background=T.resolve(T.WORKSPACE),
                         highlightthickness=0, **kwargs)
        self._key, self._serial, self._points = None, 0, None
        self._message, self.metadata = 'Drop or choose a sample', ''
        self._results = queue.Queue()
        self.bind('<Configure>', lambda _: self._draw())
        self._poll_id = self.after(80, self._poll)

    def set_source(self, row):
        key = (row.get('source'), row.get('size'), row.get('mtime_ns'))
        if self._key == key:
            return
        self._key = key
        self._serial += 1
        serial = self._serial
        self._points, self.metadata = None, ''
        self._message = 'Reading sample…' if key[0] else 'Drop or choose a sample'
        self._draw()
        if not key[0] or row.get('status') not in ('sample', 'preview'):
            self._message = 'No audio assigned'
            self._draw()
            return

        def read():
            try:
                path = Path(key[0])
                stat = path.stat()
                if (stat.st_size, stat.st_mtime_ns) != key[1:]:
                    raise ValueError('Source changed')
                with sf.SoundFile(path) as audio:
                    if audio.channels not in (1, 2) or not 0 < len(audio) <= audio.samplerate * 120:
                        raise ValueError('Unsupported preview')
                    duration = len(audio) / audio.samplerate
                    metadata = f"{duration:.2f} s   {audio.samplerate / 1000:g} kHz   " + ('Mono' if audio.channels == 1 else 'Stereo')
                    # Read sparse short windows for a bounded, quick overview.
                    points = []
                    for start in np.linspace(0, max(0, len(audio) - 512), 180).astype(int):
                        audio.seek(int(start))
                        block = audio.read(min(512, len(audio) - int(start)), dtype='float32', always_2d=True)
                        if not np.isfinite(block).all():
                            raise ValueError('Invalid audio')
                        points.append(float(np.max(np.abs(block))))
                self._results.put((serial, points, metadata))
            except (OSError, RuntimeError, ValueError):
                self._results.put((serial, None, 'Source unavailable'))
        threading.Thread(target=read, daemon=True).start()

    def _poll(self):
        try:
            while True:
                serial, points, metadata = self._results.get_nowait()
                if serial == self._serial:
                    self._points, self.metadata = points, metadata
                    self._message = metadata
                    self._draw()
        except queue.Empty:
            pass
        self._poll_id = self.after(80, self._poll)

    def _draw(self):
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        mid = (h - 16) / 2 if self._points else h / 2
        self.create_line(10, mid, max(10, w - 10), mid, fill=T.resolve(T.LINE))
        if self._points:
            peak = max(max(self._points), 1e-6)
            for i, value in enumerate(self._points):
                x = 10 + i / max(1, len(self._points) - 1) * max(1, w - 20)
                height = max(1, value / peak * (mid - 6))
                self.create_line(x, mid - height, x, mid + height, fill=T.resolve(T.ACTION))
            self.create_text(10, h - 7, text=self.metadata, anchor='w', fill=T.resolve(T.MUTED), font=('Segoe UI', 10))
        else:
            self.create_text(w / 2, mid, text=self._message, fill=T.resolve(T.MUTED), font=('Segoe UI', 11))

    def destroy(self):
        self._serial += 1
        if self._poll_id:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        super().destroy()
