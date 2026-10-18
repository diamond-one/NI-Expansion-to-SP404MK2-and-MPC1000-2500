# Build from this source folder with PyInstaller 6.22.2.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
datas = [(str(root / 'licenses'), 'licenses'), (str(root / 'ni_to_sp/assets'), 'ni_to_sp/assets')]
binaries = [(str(root / 'tools/sp404.exe'), 'tools')]
hiddenimports = ['mido.backends.rtmidi', 'rtmidi']
for module in ('customtkinter', 'sounddevice', 'tkinterdnd2'):
    data, binary, imports = collect_all(module)
    datas += data
    binaries += binary
    hiddenimports += imports

a = Analysis([str(root / 'run.py')], pathex=[str(root)], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['matplotlib', 'pandas', 'librosa', 'torch', 'IPython', 'pytest'],
    noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='NI-to-SP-0.15', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=True, upx_exclude=[],
    runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None)

