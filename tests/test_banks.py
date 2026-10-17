import copy
import json
import unittest
from unittest.mock import patch
import mido

import test_converter as fixtures
from test_browser import wait
from ni_to_sp.direct import inspect_group
from ni_to_sp.kit_editor import save_kit, open_kit
from ni_to_sp.mpc_banks import all_banks
from ni_to_sp.mpc1000 import export_program, pad_notes
from ni_to_sp.live_midi import MIDIController


class BankTests(unittest.TestCase):
    setUp = fixtures.ConverterTests.setUp
    tearDown = fixtures.ConverterTests.tearDown

    def source(self, slot=0):
        return inspect_group(self.kit)['pads'][slot]['source']

    def populate(self):
        for b, letter in enumerate('ABCD'):
            self.app.select_mpc_bank(letter)
            self.app.set_sample(b, self.source(b))

    def test_four_banks_preserve_samples_through_switch_save_reopen_and_undo(self):
        self.populate()
        for b, letter in enumerate('ABCD'):
            self.app.select_mpc_bank(letter)
            self.assertEqual(self.app.group['pads'][b]['source'], self.source(b))
            self.assertIn(f'{letter}{b + 1:02d}', self.app.pad_buttons[b].cget('text'))
        save_kit(self.app.group, self.root / 'program.json')
        restored = open_kit(self.root / 'program.json')
        self.assertEqual([bank['available'] for bank in all_banks(restored)], [1, 1, 1, 1])
        self.app.clear_pad(3)
        self.app.undo_edit()
        self.assertEqual([bank['available'] for bank in all_banks(self.app.group)], [1, 1, 1, 1])

    def test_mpc_export_has_assignments_and_midi_notes_in_all_four_banks(self):
        self.populate()
        folder, report = export_program(self.app.group, self.root / 'mpc')
        self.assertEqual(report['assigned_pads'], 4)
        rows = json.loads((folder / 'MANIFEST.json').read_text())['pads']
        self.assertEqual(len(rows), 64)
        import reference_mpc1k
        pgm = reference_mpc1k.Program(next(folder.glob('*.PGM')).read_bytes())
        for b, letter in enumerate('ABCD'):
            i = b * 16 + b
            self.assertEqual(rows[i]['pad'], f'{letter}{b + 1:02d}')
            self.assertEqual(pgm.pads[i].samples[0].sample_name.rstrip(b'\0').decode(), rows[i]['file'][:-4])
            self.assertEqual(rows[i]['midi_note'], pad_notes(b)[b])

    def test_export_remains_available_from_an_empty_bank(self):
        self.app.set_sample(0, self.source())
        self.app.select_mpc_bank('D')
        self.assertEqual(self.app.group['available'], 0)
        self.assertEqual(self.app.mpc_button.cget('state'), 'normal')
        self.assertIn('1/64', self.app.quality.cget('text'))

    def test_browsing_loads_only_selected_bank_and_demo_respects_toggle(self):
        self.app.set_sample(0, self.source())
        self.app.select_mpc_bank('C')
        self.app.kits = [self.kit]
        self.app.filter_kits()
        self.app.kit_list.selection_set(0)
        self.app.auto_ni_demo.set(True)
        with patch.object(self.app, 'preview_demo') as demo:
            self.app.select_kit()
            wait(self.app)
            demo.assert_called_once()
        banks = all_banks(self.app.group)
        self.assertEqual(banks[0]['available'], 1)
        self.assertEqual(banks[2]['available'], 15)
        self.assertEqual(self.app.group['active_bank'], 2)
        save_kit(self.app.group, self.root / 'combined.json')  # Must be non-recursive.
        self.app.auto_ni_demo.set(False)
        with patch.object(self.app, 'preview_demo') as demo:
            self.app.select_kit(force=True)
            wait(self.app)
            demo.assert_not_called()

    def test_hidden_missing_pad_blocks_export_and_next_missing_finds_it(self):
        self.app._install_group(inspect_group(self.kit))
        self.app.select_mpc_bank('B')
        self.app.set_sample(0, self.source())
        self.assertEqual(self.app.mpc_button.cget('state'), 'disabled')
        self.app.silence_var.set(True)
        self.app.sync()
        self.assertEqual(self.app.mpc_button.cget('state'), 'normal')
        self.assertIn('missing will export empty', self.app.quality.cget('text'))
        self.app.next_missing()
        self.assertEqual(self.app.group['active_bank'], 0)
        self.assertEqual(self.app.selected_pad, 14)

    def test_favorite_button_updates_immediately_and_can_remove(self):
        self.app.toggle_favorite()
        self.assertEqual(self.app.favorite_button.cget('text'), 'Remove from Favorites')
        self.app.toggle_favorite()
        self.assertEqual(self.app.favorite_button.cget('text'), 'Add to Favorites')
        self.assertNotIn(self.app.kit['id'], self.app._favorites)

    def test_hardware_mpc_notes_follow_selected_software_bank(self):
        hits = []
        controller = MIDIController('fixture', lambda i, v: hits.append(i), profile='mpc1000')
        controller._active = True
        for bank in range(4):
            controller.set_mpc_bank(bank)
            hits.clear()
            for note in pad_notes(bank):
                controller._receive(mido.Message('note_on', note=note, velocity=100), controller._generation)
            self.assertEqual(hits, list(range(16)))


if __name__ == '__main__':
    unittest.main()
