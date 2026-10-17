from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ni_to_sp.live_midi import MIDIController, MPC_PAD_NOTES, load_settings, save_settings
from ni_to_sp.mpc1000 import TEMPLATE, pad_notes, read_pad_notes
from test_live_midi import message


class MPCInputTests(unittest.TestCase):
    def controller(self, **kwargs):
        self.hits = []
        controller = MIDIController('fixture', lambda i, v: self.hits.append((i, v)),
                                    profile='mpc1000', **kwargs)
        controller._active = True
        return controller

    def test_every_physical_bank_plays_visible_pad_positions_for_any_editor_bank(self):
        self.assertEqual(tuple(pad_notes(None)), MPC_PAD_NOTES)
        controller = self.controller()
        for visible_bank in range(4):
            controller.set_mpc_bank(visible_bank)
            for physical_bank in range(4):
                self.hits.clear()
                for i, note in enumerate(pad_notes(physical_bank)):
                    controller._receive(message(note=note, velocity=i+60, channel=15), controller._generation)
                self.assertEqual(self.hits, [(i, i+60) for i in range(16)])

    def test_jj_remote_button_numbers_are_not_mistaken_for_outgoing_bank_commands(self):
        controller = self.controller()
        for note in range(89, 93):
            controller._receive(message(note=note), controller._generation)
        self.assertEqual(self.hits, [(i, 100) for i in range(6, 10)])  # D07–D10.

    def test_imported_mapping_and_learned_overrides_keep_velocity_and_channel_filter(self):
        notes = list(range(16, 80))
        controller = self.controller(mpc_notes=notes, mapping={'2:64': 3}, channel=2)
        controller._receive(message(note=64, channel=0), controller._generation)
        controller._receive(message(note=64, channel=1, velocity=41), controller._generation)
        controller._receive(message(note=79, channel=1, velocity=117), controller._generation)
        self.assertEqual(self.hits, [(3, 41), (15, 117)])

    def test_program_mapping_reads_metadata_without_any_sample_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'MY_PROGRAM.PGM'
            data = bytearray(TEMPLATE.read_bytes())
            notes = list(range(16, 80))
            data[0x2918:0x2958] = bytes(notes)
            data[0x2958:0x29D8] = bytes([255])*128
            for i, note in enumerate(notes):
                data[0x2958+note] = i
            path.write_bytes(data)
            with patch('soundfile.SoundFile', side_effect=AssertionError('Must not load audio')):
                self.assertEqual(read_pad_notes(path), notes)
            data[0x2919] = data[0x2918]
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'invalid pad-note'):
                read_pad_notes(path)
            path.write_bytes(bytes(0x2A05))
            with self.assertRaisesRegex(ValueError, 'PGM 1.00'):
                read_pad_notes(path)

    def test_mapping_preferences_roundtrip_and_invalid_replacement_is_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'connections.json'
            save_settings(path, {'profile': 'mpc1000', 'mpcPadNotes': list(range(16, 80))})
            self.assertEqual(load_settings(path)['mpcPadNotes'], list(range(16, 80)))
            before = path.read_bytes()
            for notes in ([36]*64, [36]*16, list(range(1, 64))+[128]):
                with self.assertRaises(ValueError):
                    save_settings(path, {'mpcPadNotes': notes})
                self.assertEqual(path.read_bytes(), before)
