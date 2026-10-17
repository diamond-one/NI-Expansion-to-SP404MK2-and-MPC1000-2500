from pathlib import Path
import numpy as np
import soundfile as sf
from ni_to_sp.capture import create_capture


def fixture(root, spacing=4, channels=2):
    root = Path(root)
    pack = root / "installed/Test Library"
    source = pack / "Groups/Kits/Test Kit.mxgrp"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"synthetic kit reference, not NI proprietary content")
    kit = {"path": str(source), "name": "Test Kit", "pack": "Test", "root": str(pack), "id": "test"}
    folder, job = create_capture(kit, root / "captures", spacing=spacing)
    path = root / "master.wav"
    rate = 48000
    with sf.SoundFile(path, "w", samplerate=rate, channels=channels, subtype="FLOAT") as audio:
        audio.write(np.zeros((rate * 2, channels), dtype=np.float32))
        for i in range(16):
            t = np.arange(spacing * rate) / rate
            wave = .2 * np.sin(2 * np.pi * (100 + i * 23) * t) * np.exp(-t * 20)
            audio.write(np.repeat(wave[:, None], channels, axis=1))
    return kit, folder, job, path


def probe():
    return {"project": 1, "project_name": "Test", "port": "COM4", "device_serial": "fixture",
            "occupancy_known": True, "occupied_pads": {},
            "banks": {bank: {"protected": False, "tempo": 120, "volume": 100} for bank in "ABCDEFGHIJ"}}
