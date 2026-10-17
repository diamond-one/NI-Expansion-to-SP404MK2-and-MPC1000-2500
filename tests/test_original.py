import json
from pathlib import Path
import tempfile
import unittest

import mido
import numpy as np
import soundfile as sf

from direct_fixtures import direct_fixture
from ni_to_sp.capture import load_prepared
from ni_to_sp.maschine_desktop import DesktopAttention
from ni_to_sp.original import guarded_capture, project_names, prepare_original, create_queue, run_queue, source_dependencies, engine_inventory


def project_fixture(kit, path):
    path = Path(path)
    path.write_bytes(b'2SAMJORP\0NI::MASCHINE::DATA::NoteEvent\0' + Path(kit['path']).read_bytes())


def audio_fixture(job, path, peak=.2, silent=None, tail=False):
    with sf.SoundFile(path, 'w', samplerate=48000, channels=2, subtype='FLOAT') as audio:
        audio.write(np.zeros((96000, 2), np.float32))
        for i in range(16):
            t = np.arange(round(job['spacing'] * 48000)) / 48000
            wave = peak * np.sin(2 * np.pi * (100 + i * 23) * t) * np.exp(-t * (0 if tail else 20))
            if i == silent:
                wave[:] = 0
            audio.write(np.repeat(wave[:, None], 2, axis=1))
    return {'ok': True, 'engine_version': 1}


class OriginalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kit = direct_fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def capture(self):
        folder, job = guarded_capture(self.kit, self.root / 'jobs')
        project, render = folder / 'state.mxprj', folder / 'master.wav'
        project_fixture(self.kit, project)
        return folder, job, project, render

    def test_guard_keeps_capture_pattern_longer_than_the_render_without_changing_hits(self):
        folder, job, project, _ = self.capture()
        midi = mido.MidiFile(folder / 'CAPTURE.mid')
        events, seconds = [], 0
        for event in midi:
            seconds += event.time
            if event.type == 'note_on' and event.velocity:
                events.append((round(seconds, 6), event.note, event.velocity))
        self.assertEqual(events[:16], [(2 + 8 * i, 36 + i, 100) for i in range(16)])
        self.assertGreater(events[-1][0], job['duration'])
        self.assertEqual(len(project_names(project, self.kit)), 16)
        with self.assertRaisesRegex(ValueError, 'does not verify'):
            project_names(project, {**self.kit, 'name': 'Wrong Kit'})

    def test_original_export_has_names_layout_checked_hashes_and_retains_relative_levels(self):
        _, job, project, render = self.capture()
        engine = audio_fixture(job, render, peak=1.5)
        output, session = prepare_original(job, project, render, self.root / 'exports', engine)
        _, recipe = load_prepared(output)
        self.assertEqual(recipe['mode'], 'original')
        self.assertEqual(len(session.pads), 16)
        self.assertEqual(recipe['pads'][0]['pad'], 13)
        self.assertIn('Kick Test 1', recipe['pads'][0]['name'])
        self.assertLess(recipe['processing']['common_gain'], 1)
        self.assertTrue(all(.1 <= row['duration'] < 2 for row in recipe['pads']))
        self.assertTrue(all(row['source_status'] == 'captured' for row in recipe['pads']))
        second, _ = prepare_original(job, project, render, self.root / 'exports', engine)
        self.assertEqual(second, output)

    def test_silence_and_truncated_tails_are_not_completed_or_replaced_with_raw_samples(self):
        _, job, project, render = self.capture()
        for options, message in [({'silent': 14}, 'Silent pad'), ({'tail': True}, 'tail reaches')]:
            engine = audio_fixture(job, render, **options)
            with self.assertRaisesRegex(ValueError, message):
                prepare_original(job, project, render, self.root / 'exports', engine)
            self.assertFalse(any((self.root / 'exports').rglob('COMPLETE.json')))

    def test_attention_keeps_pending_kit_and_can_resume_to_completed(self):
        path = create_queue([self.kit], self.root / 'exports', self.root / 'jobs')
        class NeedsAttention:
            def __init__(self, cancel):
                raise DesktopAttention('A dialog needs attention')
        result = run_queue(path, desktop_factory=NeedsAttention)
        self.assertEqual(result['state'], 'needs_attention')
        self.assertEqual(len(result['pending']), 1)
        self.assertEqual(result['failed'], [])
        class Desktop:
            def __init__(self, cancel):
                pass
            def capture_state(self, kit, midi, project):
                project_fixture(kit, project)
        def engine(project, render, job, folder, cancel):
            return audio_fixture(job, render)
        result = run_queue(path, desktop_factory=Desktop, engine_runner=engine)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(len(result['completed']), 1)
        self.assertEqual(result['pending'], [])
        self.assertEqual(result['failed'], [])
        self.assertEqual(json.loads(path.read_text())['state'], 'complete')

    def test_output_inside_installed_pack_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            create_queue([self.kit], Path(self.kit['root']) / 'Original', self.root / 'jobs')

    def test_reuses_verified_originals_but_renders_again_when_referenced_samples_change(self):
        _, job, project, render = self.capture()
        job['engine_inventory'] = engine_inventory()
        engine = audio_fixture(job, render)
        prepare_original(job, project, render, self.root / 'exports', engine)
        class Desktop:
            def __init__(self, cancel):
                raise DesktopAttention('Fresh render requested')
        path = create_queue([self.kit], self.root / 'exports', self.root / 'jobs')
        result = run_queue(path, desktop_factory=Desktop)
        self.assertEqual(result['state'], 'complete')
        self.assertTrue(result['completed'][0]['reused'])
        source = Path(source_dependencies(self.kit)[0]['path'])
        source.write_bytes(source.read_bytes() + b'changed')
        path = create_queue([self.kit], self.root / 'exports', self.root / 'jobs')
        result = run_queue(path, desktop_factory=Desktop)
        self.assertEqual(result['state'], 'needs_attention')
        self.assertEqual(len(result['pending']), 1)

    def test_sample_changes_during_capture_do_not_produce_completed_kit(self):
        _, job, project, render = self.capture()
        job['source_dependencies'] = source_dependencies(self.kit)
        engine = audio_fixture(job, render)
        source = Path(job['source_dependencies'][0]['path'])
        source.write_bytes(source.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'samples changed'):
            prepare_original(job, project, render, self.root / 'exports', engine)


if __name__ == '__main__':
    unittest.main()
