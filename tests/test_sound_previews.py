from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import soundfile as sf
from direct_fixtures import direct_fixture
from ni_to_sp.direct import inspect_group, sound_previews, preview_session, prepare_direct, prepare_batch
from ni_to_sp.capture import load_prepared


def preview(kit, name, category='Bass Synth', extension='.mxsnd'):
    folder = Path(kit['root']) / 'Sounds' / category
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name + extension)).write_bytes(b'Generated test preset reference')
    path = folder / '.previews' / (name + extension + '.ogg')
    path.parent.mkdir(parents=True, exist_ok=True)
    time = np.arange(13230) / 44100
    sf.write(path, .15 * np.sin(2 * np.pi * 220 * time), 44100, format='OGG', subtype='VORBIS')
    return path


class SoundPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        sound_previews.cache_clear()

    def tearDown(self):
        sound_previews.cache_clear()
        self.temp.cleanup()

    def test_exact_preview_fills_the_synth_position_and_can_be_disabled(self):
        kit = direct_fixture(self.root, missing=(14,))
        path = preview(kit, 'Sound Test 15')
        group = inspect_group(kit)
        self.assertEqual(group['available'], 16)
        self.assertEqual(group['preview_count'], 1)
        self.assertEqual(group['pads'][14]['status'], 'preview')
        self.assertEqual(group['pads'][14]['source'], str(path.resolve()))
        self.assertAlmostEqual(group['pads'][14]['preview_duration'], .3, places=2)
        session = preview_session(group)
        self.assertEqual(session.by_id[session.pads[14].sample_id].path, str(path.resolve()))
        raw = inspect_group(kit, use_previews=False)
        self.assertEqual(raw['unresolved'], [15])
        self.assertEqual(raw['pads'][14]['status'], 'needs_render')

    def test_ambiguous_same_named_presets_are_not_guessed(self):
        kit = direct_fixture(self.root, missing=(14,))
        preview(kit, 'Sound Test 15', category='Bass Synth')
        preview(kit, 'Sound Test 15', category='Massive')
        group = inspect_group(kit)
        self.assertEqual(group['unresolved'], [15])
        self.assertEqual(len(group['pads'][14]['preview_candidates']), 2)

    def test_kit_demos_and_other_expansion_previews_cannot_fill_a_pad(self):
        kit = direct_fixture(self.root, missing=(14,))
        preview(kit, 'Sound Test 15', category='Battery Kits', extension='.nbkt')
        other = direct_fixture(self.root / 'other', missing=(14,))
        preview(other, 'Sound Test 15')
        self.assertEqual(inspect_group(kit)['unresolved'], [15])

    def test_existing_sample_is_preferred_and_layered_sound_can_use_its_preview(self):
        kit = direct_fixture(self.root, layered=(14,))
        preview(kit, 'Kick Test 1')
        preview(kit, 'Sound Test 15')
        group = inspect_group(kit)
        self.assertEqual(group['pads'][0]['status'], 'sample')
        self.assertEqual(group['pads'][14]['status'], 'preview')
        self.assertEqual(group['pads'][14]['original_status'], 'layered')

    def test_preview_converts_to_verified_sp_wav_and_batch_records_its_origin(self):
        kit = direct_fixture(self.root, missing=(14,))
        preview(kit, 'Sound Test 15')
        group = inspect_group(kit)
        output, _ = prepare_direct(group, self.root / 'exports')
        _, recipe = load_prepared(output)
        row = recipe['pads'][14]
        self.assertFalse(row['intentional_empty'])
        self.assertEqual(row['source_status'], 'preview')
        self.assertEqual(row['pad'], 3)
        self.assertTrue(row['original_source'].endswith('.mxsnd.ogg'))
        info = sf.info(output / row['file'])
        self.assertEqual((info.samplerate, info.subtype), (48000, 'PCM_16'))
        self.assertAlmostEqual(info.duration, .3, places=2)
        _, batch = prepare_batch([kit], self.root / 'batch')
        self.assertEqual(batch['converted'][0]['preview_pads'], [15])
        _, disabled = prepare_batch([kit], self.root / 'disabled', use_previews=False)
        self.assertEqual(len(disabled['skipped']), 1)

    def test_invalid_preview_remains_unresolved_instead_of_becoming_silence(self):
        kit = direct_fixture(self.root, missing=(14,))
        path = preview(kit, 'Sound Test 15')
        path.write_bytes(b'Broken ogg file')
        group = inspect_group(kit)
        self.assertEqual(group['unresolved'], [15])
        self.assertIn('preview_error', group['pads'][14])


if __name__ == '__main__':
    unittest.main()
