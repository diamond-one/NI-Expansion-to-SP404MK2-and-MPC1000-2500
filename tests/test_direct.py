import json
from pathlib import Path
import tempfile
import threading
import unittest
import zlib

import numpy as np
import soundfile as sf
from direct_fixtures import direct_fixture, record
from fixtures import probe
from ni_to_sp.direct import inspect_group, preview_session, prepare_direct, prepare_batch
from ni_to_sp.capture import load_prepared
from ni_to_sp import sp_transfer


class DirectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_both_archive_layouts_keep_the_gap_before_later_samples(self):
        for old in (False, True):
            kit = direct_fixture(self.root / str(old), missing=(3, 14), old=old)
            group = inspect_group(kit)
            self.assertEqual(group['available'], 14)
            self.assertEqual(group['unresolved'], [4, 15])
            self.assertEqual(group['pads'][4]['name'], 'Sound Test 5')
            session = preview_session(group)
            self.assertIsNone(session.pads[3].sample_id)
            self.assertIn('Hit 5-0.wav', session.by_id[session.pads[4].sample_id].path)
            self.assertEqual(group['pads'][14]['sp_pad'], 3)

    def test_unrecognized_record_count_or_group_identity_cannot_guess_positions(self):
        kit = direct_fixture(self.root)
        source = Path(kit['path'])
        raw = source.read_bytes()
        source.write_bytes(record('Extra sound', 3000) + raw)
        with self.assertRaisesRegex(ValueError, 'structure'):
            inspect_group(kit)
        source.write_bytes(raw)
        with self.assertRaises(ValueError):
            inspect_group({**kit, 'name': 'Different kit'})

    def test_compressed_archive_and_layered_sound_are_explicit(self):
        kit = direct_fixture(self.root, layered=(5,))
        path = Path(kit['path'])
        path.write_bytes(zlib.compress(path.read_bytes()))
        group = inspect_group(kit)
        self.assertEqual(group['pads'][5]['status'], 'layered')
        self.assertEqual(len(group['pads'][5]['references']), 2)
        with self.assertRaisesRegex(ValueError, 'silence'):
            prepare_direct(group, self.root / 'output')

    def test_prepare_preserves_samples_names_amplitudes_and_usb_mapping(self):
        kit = direct_fixture(self.root)
        group = inspect_group(kit)
        folder, session = prepare_direct(group, self.root / 'output')
        loaded, recipe = load_prepared(folder)
        self.assertEqual(len(loaded.pads), 16)
        self.assertIn('13_Kick Test 1.wav', [p.name for p in folder.glob('*.wav')])
        source, _ = sf.read(group['pads'][0]['source'], always_2d=True)
        result, rate = sf.read(folder / recipe['pads'][0]['file'], always_2d=True)
        self.assertEqual(rate, 48000)
        np.testing.assert_allclose(result, source, atol=1 / 32768)
        plan = sp_transfer.make_transfer_plan(session, 0, 'B', probe())
        _, files = sp_transfer._export_files(plan, folder)
        self.assertEqual(len(files), 16)
        self.assertEqual(plan.entries[0].target_pad, 'B13')
        self.assertIn('Kick Test 1', plan.entries[0].name)
        cached, _ = prepare_direct(group, self.root / 'output')
        self.assertEqual(cached, folder)

    def test_allowed_missing_pads_become_silence_without_overwriting_other_slots(self):
        group = inspect_group(direct_fixture(self.root, missing=(3,)))
        with self.assertRaises(ValueError):
            prepare_direct(group, self.root / 'output')
        folder, _ = prepare_direct(group, self.root / 'output', allow_unresolved=True)
        _, recipe = load_prepared(folder)
        silent, rate = sf.read(folder / recipe['pads'][3]['file'])
        self.assertTrue(np.all(silent == 0))
        self.assertEqual(len(silent), 4800)
        self.assertEqual(recipe['pads'][4]['ni_pad'], 5)

    def test_resampling_overs_use_common_headroom_without_changing_the_kit_balance(self):
        kit = direct_fixture(self.root)
        original = inspect_group(kit)
        loud = Path(original['pads'][0]['source'])
        sf.write(loud, np.tile([.99, .99, -.99, -.99], 1200), 44100, subtype='PCM_24')
        group = inspect_group(kit)
        output, _ = prepare_direct(group, self.root / 'output')
        _, recipe = load_prepared(output)
        gain = recipe['processing']['common_gain']
        self.assertLess(gain, 1)
        self.assertGreater(gain, .5)
        quiet_before, _ = sf.read(group['pads'][1]['source'], always_2d=True)
        quiet_after, _ = sf.read(output / recipe['pads'][1]['file'], always_2d=True)
        np.testing.assert_allclose(quiet_after, quiet_before * gain, atol=1 / 32768)
        converted, _ = sf.read(output / recipe['pads'][0]['file'])
        self.assertLessEqual(float(np.max(np.abs(converted))), 1)

    def test_modified_sources_cancel_and_installed_output_are_rejected(self):
        kit = direct_fixture(self.root)
        group = inspect_group(kit)
        with self.assertRaisesRegex(ValueError, 'outside'):
            prepare_direct(group, Path(kit['root']) / 'export')
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(InterruptedError):
            prepare_direct(group, self.root / 'cancelled', cancel=cancelled)
        with Path(group['pads'][0]['source']).open('ab') as handle:
            handle.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            prepare_direct(group, self.root / 'changed')

    def test_batch_converts_ready_kits_skips_incomplete_and_reuses_verified_exports(self):
        kits = [direct_fixture(self.root / 'a', name='Ready Kit'), direct_fixture(self.root / 'b', missing=(14,), name='Synth Kit')]
        report_path, report = prepare_batch(kits, self.root / 'output')
        self.assertEqual(len(report['converted']), 1)
        self.assertEqual(len(report['skipped']), 1)
        self.assertTrue(report_path.is_file())
        _, repeated = prepare_batch(kits, self.root / 'output')
        self.assertEqual(report['converted'], repeated['converted'])
        cancelled = threading.Event()
        cancelled.set()
        _, report = prepare_batch(kits, self.root / 'output', cancel=cancelled)
        self.assertTrue(report['cancelled'])


if __name__ == '__main__':
    unittest.main()
