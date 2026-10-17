import copy
import json
import threading
import time
from unittest.mock import patch
import unittest

import test_converter as fixtures
from test_browser import wait
from ni_to_sp.direct import inspect_group, preview_session
from ni_to_sp.kit_editor import sample_row


class StudioTests(unittest.TestCase):
    setUp = fixtures.ConverterTests.setUp
    tearDown = fixtures.ConverterTests.tearDown

    def source(self, slot=0):
        return inspect_group(self.kit)['pads'][slot]['source']

    def wait_candidate(self):
        deadline = time.monotonic() + 5
        while self.app._candidate_loading and time.monotonic() < deadline:
            self.app.update()
            time.sleep(.01)
        self.assertFalse(self.app._candidate_loading)

    def test_candidate_is_temporary_compare_discard_keep_and_undo(self):
        original, alternative = self.source(0), self.source(1)
        self.app.set_sample(4, original)
        self.app.stage_candidate(sample_row(alternative))
        self.assertEqual(self.app.group['pads'][4]['source'], original)
        self.assertEqual(self.app.display_row()['source'], alternative)
        self.assertEqual(self.app.mpc_button.cget('state'), 'disabled')
        self.app.audition_original()
        self.assertEqual(self.app.display_row()['source'], original)
        self.app.audition_original()
        self.assertEqual(self.app.display_row()['source'], alternative)
        self.app.discard_candidate()
        self.assertEqual(self.app.group['pads'][4]['source'], original)
        self.app.stage_candidate(sample_row(alternative))
        self.app.keep_candidate()
        self.assertEqual(self.app.group['pads'][4]['source'], alternative)
        self.app.undo_edit()
        self.assertEqual(self.app.group['pads'][4]['source'], original)

    def test_random_search_does_not_take_global_busy_lock_or_accept_stale_result(self):
        source = self.source()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def choose(*args, **kwargs):
            entered.set()
            release.wait(3)
            return source, {'method': 'test'}
        with patch.object(self.app.pool, 'choose', side_effect=choose):
            self.app.random_pad(1)
            self.assertTrue(entered.wait(2))
            self.assertFalse(self.app.busy)
            self.app.focus_pad(5)
            release.set()
            for _ in range(20):
                self.app.update()
                time.sleep(.01)
        self.assertIsNone(self.app._candidate)
        self.assertEqual(self.app.group['available'], 0)

    def test_previous_next_candidate_history_does_not_refetch(self):
        self.app._history_mode = 1
        self.app.random_mode.set('Any sample · all expansions')
        first, second = sample_row(self.source(0)), sample_row(self.source(1))
        self.app.stage_candidate(first)
        self.app.stage_candidate(second)
        self.app.previous_candidate()
        self.assertEqual(self.app.display_row()['source'], first['source'])
        with patch.object(self.app.pool, 'choose', side_effect=AssertionError('history should be reused')):
            self.app.random_pad(1)
        self.assertEqual(self.app.display_row()['source'], second['source'])

    def test_browsing_and_return_preserve_assignments_and_undo(self):
        self.app.set_sample(3, self.source())
        identity, history = self.app.kit['id'], copy.deepcopy(self.app.undo_history)
        self.app.kits = [self.kit]
        self.app.filter_kits()
        self.app.kit_list.selection_set(0)
        self.app.select_kit()
        wait(self.app)
        self.assertNotEqual(identity, self.app.kit['id'])
        self.app.restore_draft(identity)
        self.assertEqual(self.app.kit['id'], identity)
        self.assertEqual(self.app.group['pads'][3]['source'], self.source())
        self.assertEqual(self.app.undo_history, history)
        self.app.undo_edit()
        self.assertEqual(self.app.group['available'], 0)

    def test_multiple_edited_kits_have_separate_recovery_files(self):
        self.app.set_sample(0, self.source())
        first = self.app.kit['id']
        self.app.new_empty()
        self.app.set_sample(2, self.source(1))
        second = self.app.kit['id']
        self.assertNotEqual(first, second)
        self.assertTrue(self.app.draft_path(first).is_file())
        self.assertTrue(self.app.draft_path(second).is_file())
        self.app._drafts.pop(first)
        self.app.restore_draft(first)
        self.assertEqual(self.app.group['available'], 1)
        self.assertTrue(self.app.group['pads'][0]['source'])

    def test_previous_version_draft_is_offered_after_restart(self):
        self.app.set_sample(2, self.source())
        identity = self.app.kit['id']
        self.app.close()
        (self.root / 'data' / 'studio-session.json').unlink()
        with patch.object(fixtures.App, 'scan', lambda _: None):
            self.app = fixtures.App()
        self.assertEqual(self.app._recover_id, identity)
        self.app.restore_draft(identity)
        self.assertEqual(self.app.group['available'], 1)
        self.assertTrue(self.app.group['pads'][2]['source'])

    def test_session_menu_recovers_multiple_kits_after_restart(self):
        self.app.set_sample(0, self.source())
        first = self.app.kit['id']
        self.app.new_empty()
        self.app.set_sample(1, self.source(1))
        second = self.app.kit['id']
        self.app.close()
        with patch.object(fixtures.App, 'scan', lambda _: None):
            self.app = fixtures.App()
        self.assertIn(first, self.app._draft_choices.values())
        self.assertIn(second, self.app._draft_choices.values())
        self.app.restore_draft(first)
        self.assertEqual(self.app.group['available'], 1)
        self.assertTrue(self.app.group['pads'][0]['source'])

    def test_malformed_optional_preferences_do_not_block_startup(self):
        self.app.close()
        (self.root / 'data' / 'studio-session.json').write_text(
            '{"favorites": null, "recents": 7, "target": "MPC999"}')
        with patch.object(fixtures.App, 'scan', lambda _: None):
            self.app = fixtures.App()
        self.assertEqual(self.app.target_var.get(), 'MPC1000')
        self.assertEqual(self.app._favorites, set())
        self.assertEqual(self.app._recents, [])

    def test_every_match_is_reachable_and_favorites_are_persistent(self):
        self.app.kits = [dict(self.kit, id=str(i), name=f'Kit {i:04d}') for i in range(1650)]
        self.app.filter_kits()
        self.assertEqual(self.app.kit_list.size(), 1650)
        self.assertEqual(self.app.visible[-1]['name'], 'Kit 1649')
        self.app.kit = self.app.kits[-1]
        self.app.toggle_favorite()
        self.app.library_view.set('Favorites')
        self.app.filter_kits()
        self.assertEqual([kit['id'] for kit in self.app.visible], ['1649'])
        state = json.loads((self.app.data_dir / 'studio-session.json').read_text())
        self.assertIn('1649', state['favorites'])
        self.app.kit = self.app.group['kit']

    def test_fill_empty_pads_keeps_existing_sounds_and_exports_one_mpc_program(self):
        source, added = self.source(), self.source(1)
        self.app.set_sample(0, source)
        with patch('ni_to_sp.studio_workflow.filedialog.askopenfilenames', return_value=(added, source)):
            self.app.fill_empty_pads()
            wait(self.app)
        self.assertEqual(self.app.group['available'], 3)
        self.assertEqual(self.app.group['pads'][0]['source'], source)
        self.assertEqual(self.app.group['pads'][1]['source'], added)
        with patch('ni_to_sp.converter_ui.filedialog.askdirectory', return_value=str(self.root / 'mpc')):
            self.app.export_mpc()
            wait(self.app)
        manifest = json.loads(next((self.root / 'mpc').glob('*/MANIFEST.json')).read_text())
        self.assertEqual(sum(bool(row['file']) for row in manifest['pads']), 3)
        self.app.undo_edit()
        self.assertEqual(self.app.group['available'], 1)

    def test_waveform_and_keep_action_visible_at_minimum_size(self):
        self.app.stage_candidate(sample_row(self.source()))
        self.app.geometry('1120x800+10000+10000')
        self.app.update()
        for widget in (self.app.waveform, self.app.keep_button, self.app.choose_sample_button):
            self.assertGreaterEqual(widget.winfo_rooty(), self.app.winfo_rooty())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), self.app.winfo_rooty() + 800)
        self.assertIn('MPC1000', self.app.bank_label.cget('text'))
        self.assertIn('A01', self.app.pad_buttons[0].cget('text'))
        self.assertNotIn('SP ', self.app.pad_buttons[0].cget('text'))


if __name__ == '__main__':
    unittest.main()
