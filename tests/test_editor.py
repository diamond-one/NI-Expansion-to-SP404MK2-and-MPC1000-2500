import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
import mido

from direct_fixtures import direct_fixture
from ni_to_sp import kit_editor as ed
from ni_to_sp.direct import inspect_group, prepare_direct
from ni_to_sp.capture import load_prepared
from ni_to_sp import mpc1000 as mpc
from ni_to_sp.live_midi import MIDIController, MPC_A_NOTES


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.k1 = direct_fixture(self.root / 'one', missing=(14,))
        self.k2 = direct_fixture(self.root / 'two')
        self.k2.update(id='other-kit', pack=self.k1['pack'])
        self.k3 = direct_fixture(self.root / 'three')
        self.k3.update(id='third-kit', pack='Another expansion')
        self.kits = [self.k1, self.k2, self.k3]
        self.group = inspect_group(self.k1, use_previews=False)
        self.pool = ed.ExpansionPool(self.root / 'pool.sqlite')

    def tearDown(self):
        self.temp.cleanup()

    def test_four_scopes_respect_expansion_position_and_other_kit(self):
        same = self.pool.candidates(self.kits, self.group, 14, 0)
        all_ = self.pool.candidates(self.kits, self.group, 14, 1)
        pad_same = self.pool.candidates(self.kits, self.group, 14, 2)
        pad_all = self.pool.candidates(self.kits, self.group, 14, 3)
        self.assertGreater(len(all_), len(same))
        self.assertEqual(len(pad_same), 1)
        self.assertEqual(len(pad_all), 2)
        for row in pad_all.values():
            self.assertEqual(row['ni_pad'], 15)
            self.assertNotEqual(row['kit_id'], self.k1['id'])
        path, origin = self.pool.choose(self.kits, self.group, 14, 2)
        ed.assign(self.group, 14, path, origin)
        self.assertEqual(self.group['pads'][14]['sp_pad'], 3)
        self.assertEqual(self.group['unresolved'], [])
        self.assertEqual(self.group['pads'][14]['replacement']['kit_id'], 'other-kit')
        # Changed files are not blindly accepted from the index.
        Path(path).unlink()
        with self.assertRaisesRegex(ValueError, 'No other usable'):
            self.pool.choose(self.kits, self.group, 14, 2)

    def test_blank_kit_partial_sp_export_roundtrip_and_save(self):
        group = ed.empty_kit('My own kit')
        self.assertEqual(group['available'], 0)
        self.assertEqual(group['unresolved'], [])
        ed.assign(group, 5, self.group['pads'][0]['source'])
        folder, _ = prepare_direct(group, self.root / 'sp')
        _, recipe = load_prepared(folder)
        self.assertFalse(recipe['pads'][5]['intentional_empty'])
        self.assertEqual(sum(r['intentional_empty'] for r in recipe['pads']), 15)
        reopened = ed.from_prepared(folder)
        self.assertEqual(reopened['available'], 1)
        self.assertEqual(reopened['pads'][5]['sp_pad'], 10)
        ed.save_kit(group, self.root / 'kit.json')
        self.assertEqual(ed.open_kit(self.root / 'kit.json')['available'], 1)
        Path(group['pads'][5]['source']).unlink()
        self.assertEqual(ed.open_kit(self.root / 'kit.json')['unresolved'], [6])

    def test_bad_replacement_does_not_change_existing_pad(self):
        original = copy.deepcopy(self.group)
        path = self.root / 'bad.wav'
        path.write_text('invalid')
        with self.assertRaises((ValueError, RuntimeError)):
            ed.assign(self.group, 0, path)
        self.assertEqual(original, self.group)
        ed.clear(self.group, 14)
        self.assertEqual(self.group['unresolved'], [])

    def test_mpc_program_has_correct_bank_a_names_notes_and_pcm(self):
        group = ed.empty_kit('Mý nice kit')
        for index in (0, 7, 15):
            ed.assign(group, index, self.group['pads'][index]['source'])
        folder, check = mpc.export_program(group, self.root / 'mpc')
        self.assertEqual(check['assigned_pads'], 3)
        data = next(folder.glob('*.PGM')).read_bytes()
        self.assertEqual(len(data), 10756)
        self.assertEqual(tuple(data[0x2918:0x2928]), MPC_A_NOTES)
        manifest = json.loads((folder / 'MANIFEST.json').read_text())
        for i, row in enumerate(manifest['pads']):
            name = data[24 + i * 164:40 + i * 164].rstrip(b'\0').decode()
            self.assertEqual(name + '.WAV' if name else '', row['file'])
            if name:
                info = sf.info(folder / row['file'])
                self.assertEqual((info.samplerate, info.subtype, info.channels), (44100, 'PCM_16', 1))
        for i in range(16, 64):
            self.assertEqual(data[24 + i * 164:40 + i * 164], bytes(16))
        self.assertFalse((folder / 'INCOMPLETE.txt').exists())
        # Independent original-format parser, rather than only the writer's validator.
        import reference_mpc1k
        parsed = reference_mpc1k.Program(data)
        self.assertEqual(parsed.pad_midi_notes[:16], list(MPC_A_NOTES))
        self.assertEqual(parsed.pads[7].samples[0].sample_name.rstrip(b'\0').decode(), manifest['pads'][7]['file'][:-4])

    def test_mpc_rejects_unresolved_and_keeps_source_unchanged(self):
        source = Path(self.k1['path']).read_bytes()
        with self.assertRaisesRegex(ValueError, 'missing'):
            mpc.export_program(self.group, self.root / 'mpc')
        folder, check = mpc.export_program(self.group, self.root / 'mpc', True)
        self.assertEqual(check['assigned_pads'], 15)
        self.assertEqual(Path(self.k1['path']).read_bytes(), source)
        next(folder.glob('*.WAV')).unlink()
        with self.assertRaises((RuntimeError, OSError)):
            mpc.validate_program(next(folder.glob('*.PGM')))

    def test_mpc_profile_routes_all_sixteen_notes_and_any_channel(self):
        hits = []
        controller = MIDIController('test', lambda i, v: hits.append((i, v)), profile='mpc1000')
        controller._active = True
        for note in MPC_A_NOTES:
            controller._receive(mido.Message('note_on', channel=15, note=note, velocity=91), controller._generation)
        self.assertEqual(hits, [(i, 91) for i in range(16)])
        controller._receive(mido.Message('note_on', channel=15, note=127, velocity=91), controller._generation)
        self.assertEqual(len(hits), 16)


if __name__ == '__main__':
    unittest.main()
