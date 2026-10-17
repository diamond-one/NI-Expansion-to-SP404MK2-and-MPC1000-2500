"""Compact audio-device choices shared with the local CrateBuilder project."""
from collections import OrderedDict


def load_audio(sample, rate=48000):
    from pathlib import Path
    from math import gcd
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from .capture import file_hash
    path = Path(sample.path)
    stat = path.stat()
    if getattr(stat, 'st_file_attributes', 0) & (0x1000 | 0x400000):
        raise ValueError(f'{path.name} is online-only. Make it available offline first.')
    features = sample.features or {}
    if 'size' in features and (stat.st_size, stat.st_mtime_ns) != (features['size'], features['mtime_ns']):
        raise ValueError(f'{path.name} changed. Select the kit again.')
    if sample.fingerprint and file_hash(path) != sample.fingerprint:
        raise ValueError(f'{path.name} changed. Reopen the completed kit.')
    with sf.SoundFile(path) as source:
        if source.channels not in (1, 2) or not 8000 <= source.samplerate <= 192000 or not 0 < len(source) <= source.samplerate * 120:
            raise ValueError(f'{path.name}: preview requires mono/stereo audio up to 120 seconds.')
        data = source.read(dtype='float32', always_2d=True)
        source_rate = source.samplerate
    if not np.isfinite(data).all():
        raise ValueError(f'{path.name} contains invalid audio.')
    if source_rate != rate:
        divisor = gcd(source_rate, rate)
        data = resample_poly(data, rate // divisor, source_rate // divisor, axis=0)
    return data.astype(np.float32)

def list_audio_outputs():
    import sounddevice as sd
    hosts = sd.query_hostapis()
    result = []
    for index, device in enumerate(sd.query_devices()):
        if device["max_output_channels"] >= 2:
            result.append({"index": index, "name": device["name"],
                           "hostapi_name": hosts[device["hostapi"]]["name"],
                           "samplerate": float(device["default_samplerate"])})
    return result

def _audio_host_priority(host):
    host = str(host).casefold()
    for priority, token in enumerate(("wasapi", "asio", "directsound", "mme", "wdm-ks")):
        if token in host:
            return priority
    return 5

def _generic_audio_alias(name):
    name = " ".join(str(name).casefold().replace("–", "-").replace("—", "-").split())
    return name in {
        "microsoft sound mapper", "microsoft sound mapper - output",
        "primary sound driver", "default", "default output", "default output device",
        "default audio device", "default device", "system default", "system default output",
        "system default audio device", "windows default", "windows default output",
    }

def simple_audio_outputs(outputs=None):
    """Return a short device list while preserving each endpoint's actual index.

    Matching names across Windows driver hosts are represented by WASAPI,
    ASIO, DirectSound, MME, then WDM-KS, in that order. Distinct names are never
    merged, and identically named devices within the preferred host remain
    separate: they may be two real devices with the same manufacturer name.
    Generic mapper/default aliases remain available if they are the only outputs.
    Long MME prefixes are hidden only when one better-driver endpoint matches.
    The SP appears first, followed by other outputs in alphabetical order.
    Pass an inventory to make this a pure operation with no device queries.
    """
    inventory = list(list_audio_outputs() if outputs is None else outputs)
    endpoints = [device for device in inventory if not _generic_audio_alias(device.get("name", ""))]
    inventory = endpoints or inventory
    groups = OrderedDict()
    for device in inventory:
        name = " ".join(str(device.get("name", "")).casefold().split())
        groups.setdefault(name, []).append(device)
    result = []
    for variants in groups.values():
        preferred = min(variants, key=lambda device: _audio_host_priority(device.get("hostapi_name", "")))
        host = str(preferred.get("hostapi_name", "")).casefold()
        seen_indices = set()
        for device in variants:
            if str(device.get("hostapi_name", "")).casefold() != host:
                continue
            index = device.get("index")
            if index in seen_indices:
                continue
            seen_indices.add(index)
            result.append(dict(device))
    compact = []
    for device in result:
        name = " ".join(str(device.get("name", "")).casefold().split())
        host = str(device.get("hostapi_name", ""))
        # WinMM's short device labels can end at 31 characters. Match only a
        # unique longer endpoint on a better driver; a shared prefix is not
        # enough to distinguish two physical outputs.
        if "mme" in host.casefold() and len(str(device.get("name", "")).strip()) >= 30:
            matches = [other for other in result
                       if _audio_host_priority(other.get("hostapi_name", "")) < _audio_host_priority(host)
                       and len(other_name := " ".join(str(other.get("name", "")).casefold().split())) > len(name)
                       and other_name.startswith(name)]
            if len(matches) == 1:
                continue
        compact.append(device)

    def display_order(device):
        name = " ".join(str(device.get("name", "")).casefold().split())
        sp = "sp404" in name.replace("-", "").replace(" ", "")
        return (not sp, name)

    return sorted(compact, key=display_order)

def audio_output_label(device, advanced=False):
    """Use the endpoint's familiar name, with driver information only on request."""
    name = str(device.get("name", "Audio output"))
    host = str(device.get("hostapi_name", ""))
    return f"{name} ({host})" if advanced and host else name

def preferred_audio_output(outputs=None):
    outputs = list_audio_outputs() if outputs is None else outputs
    sp = [device for device in outputs if "sp-404" in device["name"].lower()]
    if sp:
        return next((device for device in sp if "WASAPI" in device["hostapi_name"].upper()), sp[0])["index"]
    return None
