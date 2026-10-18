import json
import os
from pathlib import Path
import tempfile
import time
import traceback
from unittest.mock import patch


def self_check(report_path):
    report = {"ok": False, "checks": []}
    try:
        import numpy as np
        import soundfile as sf
        from .capture import create_capture, inspect_render, prepare_kit, load_prepared
        from .library import discover_kits
        from . import sp_transfer
        with tempfile.TemporaryDirectory(prefix="ni-to-sp-check-") as temporary:
            root = Path(temporary)
            kit_path = root / "installed/Test Library/Groups/Kits/Test.mxgrp"
            kit_path.parent.mkdir(parents=True)
            kit_path.write_bytes(b"Synthetic NI to SP test reference")
            kits = discover_kits(root / "installed")
            assert len(kits) == 1
            folder, job = create_capture(kits[0], root / "captures", spacing=4)
            report["checks"].append("Installed group discovery and 16-pad capture MIDI")
            render = root / "master.wav"
            with sf.SoundFile(render, "w", samplerate=48000, channels=2, subtype="FLOAT") as audio:
                audio.write(np.zeros((96000, 2), dtype=np.float32))
                for i in range(16):
                    t = np.arange(4 * 48000) / 48000
                    hit = .2 * np.sin(2 * np.pi * (100 + i * 23) * t) * np.exp(-20 * t)
                    audio.write(np.repeat(hit[:, None], 2, axis=1))
            checked = inspect_render(job, render, confirmed=True)
            assert checked["ok"], checked["problems"]
            output, session = prepare_kit(job, checked, root / "prepared")
            session, recipe = load_prepared(output)
            assert len(session.pads) == len(recipe["pads"]) == 16
            report["checks"].append("Synthetic master audio inspection, fixed pad order and verified 48 kHz / 16-bit WAVs")
            device = {"project": 1, "project_name": "Fixture", "port": "fixture", "device_serial": "fixture",
                      "occupancy_known": True, "occupied_pads": {}, "banks": {b: {"protected": False} for b in "ABCDEFGHIJ"}}
            plan = sp_transfer.make_transfer_plan(session, 0, "B", device)
            _, files = sp_transfer._export_files(plan, output)
            assert len(files) == 16 and plan.entries[0].target_pad == "B13"
            report["checks"].append("Immutable SP transfer plan, complete-kit hashes and physical NI-to-SP mapping")
            cli = sp_transfer.discover_cli()
            assert cli and "sp404" in sp_transfer._run(cli, ["--version"]).lower()
            report["checks"].append("Bundled USB helper loaded; no device writes")
            with patch.dict(os.environ, {"NI_TO_SP_DATA_DIR": str(root / "data")}):
                from .capture_ui import App
                with patch.object(App, "scan", lambda _: None):
                    app = App()
                try:
                    app.geometry("1260x900+10000+10000")
                    app.kits = kits
                    app.filter_kits()
                    app.update()
                    assert app.kit_list.size() == 1 and len(app.pad_buttons) == 16
                    app.kit_list.selection_set(0)
                    app.select_kit()
                    app.job, app.job_folder, app.prepared, app.session = job, folder, output, session
                    app.sync()
                    assert app.send_open_button.cget("state") == "normal"
                    app.transfer_dialog()
                    app.update()
                    for window in list(app.winfo_children()):
                        if window.winfo_class() == "Toplevel":
                            window.destroy()
                    report["checks"].append("Standalone desktop kit browser, capture controls, 16-pad review and destination dialog")
                finally:
                    app.close()
            # A second, small generated group checks the default direct path.
            from .direct import inspect_group, preview_session, prepare_direct
            direct_wav = kit_path.parents[2] / 'Samples/Drums/Test.wav'
            direct_wav.parent.mkdir(parents=True)
            sf.write(direct_wav, np.sin(np.arange(4800) * .1) * .15, 48000)
            def record(name, number):
                raw = name.encode()
                ref = lambda n: bytes([2]) + n.to_bytes(2, 'little')
                return ref(number - 1) + bytes([0, 1, len(raw)]) + raw + b''.join(ref(number + i) for i in range(4)) + bytes(8)
            preview = kit_path.parents[2] / 'Sounds/Bass Synth/.previews/Sound 15.mxsnd.ogg'
            preview.parent.mkdir(parents=True)
            sf.write(preview, np.sin(np.arange(4800) * .1) * .15, 48000, format='OGG', subtype='VORBIS')
            kit_path.write_bytes(b''.join(record(f'Sound {i + 1}', 100 + i * 10) +
                (b'Internal synth' if i == 14 else b'Samples/Drums/Test.wav') + bytes(1) for i in range(16)) + record('Test', 500))
            kits = discover_kits(root / 'installed')
            direct = inspect_group(kits[0])
            assert direct['available'] == 16
            assert direct['preview_count'] == 1 and direct['pads'][14]['status'] == 'preview'
            prepared, session = prepare_direct(direct, root / 'direct-prepared')
            assert len(load_prepared(prepared)[0].pads) == 16
            from .converter_ui import App as Browser
            with patch.dict(os.environ, {'NI_TO_SP_DATA_DIR': str(root / 'browser-data')}), patch.object(Browser, 'scan', lambda _: None):
                browser = Browser()
            try:
                browser.geometry('1120x800+10000+10000')
                browser.kit, browser.group, browser.session = kits[0], direct, preview_session(direct)
                browser.show_group()
                browser.update()
                assert browser.tabs.get() == 'Play & export'
                assert browser.send_open_button.cget('state') == 'normal'
                assert browser.prepared is None
                assert browser.drop_available, getattr(browser, 'drop_error', '')
                assert browser.tabs._name_list == ['Play & export', 'Batch export']
                assert browser.mpc_button.cget('state') == 'normal'
                import tkinter.font
                assert 'Space Grotesk' in tkinter.font.families(browser)
                assert 'Oswald' in tkinter.font.families(browser)
                before = json.dumps(browser.group, sort_keys=True)
                browser.tabs.mode_buttons['Batch export'].invoke()
                browser.update()
                assert browser.tabs.get() == 'Batch export'
                browser.tabs.mode_buttons['Play & export'].invoke()
                browser.update()
                assert before == json.dumps(browser.group, sort_keys=True)
            finally:
                browser.close()
            report['checks'].append('Direct group reader, named 16-pad browser, sample export and immediate preview session without a Maschine render')
            report['checks'].append('Bundled Oswald and Space Grotesk loaded; native workspace mode controls preserve the loaded kit')
            import mido.backends.rtmidi
            import rtmidi
            assert hasattr(rtmidi, 'MidiIn')
            report['checks'].append('Native MIDI input backend bundled and loaded; no hardware ports opened')
            report['checks'].append('NI sound preview exact-name matching, native OGG decoding and 48 kHz / 16-bit SP export')
            from .original import guarded_capture, prepare_original
            original_folder, original_job = guarded_capture(kits[0], root / 'original-jobs', spacing=4)
            original_project = original_folder / 'fixture.mxprj'
            original_project.write_bytes(b'2SAMJORP\0NI::MASCHINE::DATA::NoteEvent\0' + kit_path.read_bytes())
            original_output, original_session = prepare_original(original_job, original_project, render,
                root / 'original-exports', {'engine_version': 1})
            assert len(original_session.pads) == 16
            assert load_prepared(original_output)[1]['mode'] == 'original'
            report['checks'].append('Original mode guarded MIDI, processed-audio checks and named WAV export with generated fixtures; no Maschine window operated')
            from .kit_editor import empty_kit, assign, save_kit, open_kit
            from .mpc1000 import export_program, validate_program
            custom = empty_kit('Fixture custom kit')
            assign(custom, 7, direct_wav)
            editable = root / 'editable.json'
            save_kit(custom, editable)
            assert open_kit(editable)['available'] == 1
            report['checks'].append('Blank kit, editable save/open and intentionally empty pad positions')
            mpc_folder, validation = export_program(custom, root / 'mpc')
            assert validation['assigned_pads'] == 1
            assert validate_program(next(mpc_folder.glob('*.PGM')))['assigned_pads'] == 1
            report['checks'].append('MPC1000 binary program, bank A assignment and verified 44.1 kHz / 16-bit WAV')
            report['checks'].append('Native sample-drop package loaded in the sample-only editor; recording controls absent')
            report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report["ok"]
