import copy
from unittest.mock import patch
import unittest

import test_converter as fixtures
from ni_to_sp.direct import inspect_group
from ni_to_sp.kit_editor import sample_row


class WorkspaceModeTests(unittest.TestCase):
    setUp = fixtures.ConverterTests.setUp
    tearDown = fixtures.ConverterTests.tearDown

    def test_mode_changes_keep_all_banks_undo_and_uncommitted_candidate(self):
        source = inspect_group(self.kit)['pads'][0]['source']
        self.app.set_sample(0, source)
        self.app.select_mpc_bank('D')
        self.app.set_sample(7, source)
        self.app.stage_candidate(sample_row(source))
        before = copy.deepcopy(self.app.group)
        history = copy.deepcopy(self.app.undo_history)
        candidate = copy.deepcopy(self.app._candidate)
        for mode in ('Batch export', 'Play & export'):
            self.app.tabs.mode_buttons[mode].invoke()
            self.app.update()
            self.assertEqual(self.app.tabs.get(), mode)
            self.assertTrue(self.app.tabs.tab(mode).winfo_ismapped())
            other = 'Play & export' if mode == 'Batch export' else 'Batch export'
            self.assertFalse(self.app.tabs.tab(other).winfo_ismapped())
            self.assertEqual(self.app.group, before)
            self.assertEqual(self.app.undo_history, history)
            self.assertEqual(self.app._candidate, candidate)
        self.assertEqual(self.app.mpc_bank_var.get(), 'D')
        self.assertEqual(self.app.mpc_button.cget('state'), 'disabled')

    def test_batch_controls_dispatch_scope_and_share_missing_policy(self):
        self.app.kits = [self.kit]
        self.app.filter_kits()
        self.app.kit_list.selection_set(0)
        self.app.select_kit()
        from test_browser import wait
        wait(self.app)
        self.app.tabs.set('Batch export')
        self.assertFalse(self.app.silence_var.get())
        self.app.batch_policy_checks['Export missing as empty'].toggle()
        self.assertTrue(self.app.silence_var.get())
        with patch.object(self.app, 'export_batch') as export:
            self.app.batch_button.invoke()
            export.assert_called_once_with()
            export.reset_mock()
            self.app.all_batch_button.invoke()
            export.assert_called_once_with(all_expansions=True)
        self.app.tabs.set('Play & export')
        self.assertEqual(self.app.mpc_button.cget('state'), 'normal')
        self.app.tabs.set('Batch export')
        self.app.batch_policy_checks['Export missing as empty'].toggle()
        self.assertFalse(self.app.silence_var.get())
        self.assertEqual(self.app.mpc_button.cget('state'), 'disabled')

    def test_batch_actions_fit_the_minimum_window(self):
        self.app.tabs.set('Batch export')
        self.app.update()
        for control in (self.app.batch_button, self.app.all_batch_button,
                        *self.app.tabs.mode_buttons.values()):
            self.assertTrue(control.winfo_ismapped())
            self.assertGreaterEqual(control.winfo_rootx(), self.app.winfo_rootx())
            self.assertLessEqual(control.winfo_rootx() + control.winfo_width(),
                                 self.app.winfo_rootx() + self.app.winfo_width())
            self.assertLessEqual(control.winfo_rooty() + control.winfo_height(),
                                 self.app.winfo_rooty() + self.app.winfo_height())

