"""Generated preset identities exercise renamed synths without NI source assets."""
import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
import soundfile as sf

from direct_fixtures import direct_fixture, record
from ni_to_sp.direct import inspect_group, sound_previews, prepare_direct, preview_session
from ni_to_sp.kit_editor import assign, clear, open_kit, save_kit
from ni_to_sp.mpc1000 import export_program
from ni_to_sp.mpc_banks import ensure_banks


def identity(name, product='Massive'):
    return b''.join(struct.pack('<I', len(value.encode('utf-16le')) // 2) + value.encode('utf-16le')
                    for value in (name, 'Fixture author', 'Fixture vendor', product))


def insert_pad(kit, index, data):
    path = Path(kit['path'])
    raw = path.read_bytes()
    marker = record(('Kick' if index == 0 else 'ClosedHH' if index == 2 else 'OpenHH' if index == 3 else 'Sound') + f' Test {index + 1}', 1000 + index * 30)
    assert raw.count(marker) == 1
    path.write_bytes(raw.replace(marker, marker + data, 1))


def synth_preview(kit, name, category='Massive Sounds', metadata=None):
    folder = Path(kit['root']) / 'Sounds' / category
    folder.mkdir(parents=True, exist_ok=True)
    preset = folder / (name + '.nmsv')
    preset.write_bytes(b'Generated preset\0' + (metadata if metadata is not None else identity(name)))
    audio = folder / '.previews' / (preset.name + '.ogg')
    audio.parent.mkdir(exist_ok=True)
    t = np.arange(8820) / 44100
    sf.write(audio, .1 * np.sin(2 * np.pi * 220 * t), 44100, format='OGG', subtype='VORBIS')
    return audio.resolve()


class SynthPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        sound_previews.cache_clear()

    def tearDown(self):
        sound_previews.cache_clear()
        self.temp.cleanup()

    def test_older_native_metadata_record_has_separate_product_tags(self):
        kit = direct_fixture(self.root, missing=(14,))
        fields = identity('Older lead')
        product = 'Massive'.encode('utf-16le')
        metadata = b'DSIN' + struct.pack('<7I', 1, 1, 1, 1, 2, 1, 0)
        metadata += fields[:-(4+len(product))] + bytes(8) + b'\xff' * 4 + bytes(32) + product
        insert_pad(kit, 14, b'Native Instruments:VST:INSTR:1234\0' + metadata)
        synth_preview(kit, 'Older lead', metadata=metadata)
        row = inspect_group(kit)['pads'][14]
        self.assertEqual(row['status'], 'preview')
        self.assertEqual(row['preview_preset'], 'Older lead')

    def test_renamed_preset_identity_overrides_a_misleading_pad_name(self):
        kit = direct_fixture(self.root, missing=(14, 15))
        for i, name in ((14, 'Actual lead'), (15, 'Actual bass')):
            insert_pad(kit, i, b'Native Instruments:VST:INSTR:1234\0' + identity(name))
            source = synth_preview(kit, name)
            synth_preview(kit, f'Sound Test {i + 1}')  # Matching label is a different preset.
        group = inspect_group(kit)
        self.assertEqual(group['unresolved'], [])
        self.assertEqual(group['preview_count'], 2)
        for i, name in ((14, 'Actual lead'), (15, 'Actual bass')):
            row = group['pads'][i]
            self.assertEqual(row['preview_preset'], name)
            self.assertEqual(row['preview_match'], 'preset_identity')
            self.assertEqual(row['status'], 'preview')
            self.assertEqual(row['sp_pad'], i - 11)

    def test_ambiguous_embedded_identity_is_never_guessed_or_exported_as_silence(self):
        kit = direct_fixture(self.root, missing=(14,))
        insert_pad(kit, 14, b'Native Instruments:VST:INSTR:1234\0' + identity('Shared lead'))
        synth_preview(kit, 'Shared lead', 'First')
        synth_preview(kit, 'Shared lead', 'Second')
        group = inspect_group(kit)
        self.assertEqual(group['unresolved'], [15])
        self.assertEqual(len(group['pads'][14]['preview_candidates']), 2)
        for export in (prepare_direct, export_program):
            with self.assertRaisesRegex(ValueError, 'synth pads'):
                export(group, self.root / 'exports', allow_unresolved=True)

    def test_macro_labels_and_other_expansions_are_not_preset_identities(self):
        kit = direct_fixture(self.root, missing=(14,))
        synth_preview(kit, 'Lead label')
        other = direct_fixture(self.root / 'other')
        synth_preview(other, 'Other lead')
        insert_pad(kit, 14, b'Native Instruments:VST:INSTR:1234\0' +
                   'Lead label'.encode('utf-16le') + b'\0' * 20 + identity('Other lead'))
        group = inspect_group(kit)
        self.assertEqual(group['unresolved'], [15])
        self.assertEqual(group['pads'][14]['preview_candidates'], [])

    def test_confirmed_drumsynths_are_empty_in_preview_sp_and_mpc(self):
        positions = (4, 6, 8, 9)
        kit = direct_fixture(self.root, missing=positions)
        for i, engine in zip(positions, ('Kick', 'Perc', 'Hihat', 'Snare')):
            insert_pad(kit, i, ('NI::MASCHINE::DATA::' + engine).encode() + b'\0')
            synth_preview(kit, f'Sound Test {i + 1}')  # DrumSynth remains intentionally empty.
        group = inspect_group(kit)
        self.assertEqual(group['unresolved'], [])
        self.assertEqual(group['available'], 12)
        session = preview_session(group)
        for i in positions:
            self.assertIsNone(session.pads[i].sample_id)
            self.assertEqual(group['pads'][i]['empty_reason'], 'drumsynth')
        folder, _ = prepare_direct(group, self.root / 'sp')
        recipe = json.loads((folder / 'KIT_RECIPE.json').read_text())
        for i in positions:
            row = recipe['pads'][i]
            self.assertEqual(row['empty_reason'], 'drumsynth')
            self.assertTrue(row['intentional_empty'])
            self.assertTrue(np.all(sf.read(folder / row['file'])[0] == 0))
        folder, check = export_program(group, self.root / 'mpc')
        self.assertEqual(check['assigned_pads'], 12)
        rows = json.loads((folder / 'MANIFEST.json').read_text())['pads']
        self.assertTrue(all(rows[i]['file'] == '' for i in positions))

    def test_plugin_wav_asset_is_not_mistaken_for_a_rendered_synth_note(self):
        kit = direct_fixture(self.root)
        insert_pad(kit, 14, b'Native Instruments:VST:INSTR:1234\0' + identity('Rendered lead'))
        source = synth_preview(kit, 'Rendered lead')
        row = inspect_group(kit)['pads'][14]
        self.assertEqual(row['status'], 'preview')
        self.assertEqual(row['source'], str(source))

    def test_old_saved_banks_upgrade_without_replacing_user_samples_or_empty_pads(self):
        kit = direct_fixture(self.root, missing=(14, 15))
        for i, name in ((14, 'Stored lead'), (15, 'Stored bass')):
            insert_pad(kit, i, b'Native Instruments:VST:INSTR:1234\0' + identity(name))
            synth_preview(kit, name)
        group = inspect_group(kit, use_previews=False)
        assign(group, 0, group['pads'][1]['source'])
        clear(group, 3)
        program = ensure_banks(group)
        program['banks'][3] = copy.deepcopy(program['banks'][0])
        program['version'] = 3
        for bank in program['banks']:
            bank['version'] = 3
        path = self.root / 'saved.json'
        save_kit(program, path)
        before, source_before = path.read_bytes(), Path(kit['path']).read_bytes()
        loaded = open_kit(path)
        for bank in (loaded['banks'][0], loaded['banks'][3]):
            self.assertEqual(bank['unresolved'], [])
            self.assertEqual(bank['pads'][0]['replacement']['method'], 'chosen_file')
            self.assertEqual(bank['pads'][3]['status'], 'empty')
            self.assertEqual(bank['pads'][14]['preview_preset'], 'Stored lead')
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(source_before, Path(kit['path']).read_bytes())

    def test_rescan_refreshes_cached_preset_identities(self):
        kit = direct_fixture(self.root, missing=(14,))
        insert_pad(kit, 14, b'Native Instruments:VST:INSTR:1234\0' + identity('Added later'))
        self.assertEqual(inspect_group(kit)['unresolved'], [15])
        synth_preview(kit, 'Added later')
        sound_previews.cache_clear()
        self.assertEqual(inspect_group(kit)['unresolved'], [])
