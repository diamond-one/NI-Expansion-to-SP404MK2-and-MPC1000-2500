"""Thread-local Windows COM lifetime for WASAPI audio services."""
from contextlib import contextmanager
import os


@contextmanager
def audio_apartment():
    if os.name != 'nt':
        yield
        return
    import ctypes
    api = ctypes.OleDLL('ole32')
    # This dedicated service thread owns and releases its own audio objects.
    result = api.CoInitializeEx(None, 0)
    try:
        yield
    finally:
        if result in (0, 1):
            api.CoUninitialize()
