import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from direct_fixtures import direct_fixture
from test_browser import wait
from ni_to_sp.converter_ui import App


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kit = direct_fixture(self.root, missing=(14,))
        self.env = patch.dict(os.environ, {'NI_TO_SP_DATA_DIR': str(self.root / 'data'), 'NI_TO_SP_DISABLE_AUDIO': '1'})
        self.env.start()
        with patch.object(App, 'scan', lambda _: None):
            self.app = App()
        self.app.geometry('1120x800+10000+10000')
        self.app.update()

    def tearDown(self):
        self.app.close()
        self.env.stop()
        self.temp.cleanup()

    def test_starts_empty_and_has_no_recording_tab(self):
        self.assertEqual(self.app.group['available'], 0)
        self.assertEqual(self.app.tabs._name_list, ['Play & export', 'Batch export'])
        self.assertEqual(self.app.mpc_button.cget('state'), 'disabled')
        self.assertTrue(self.app.drop_available, getattr(self.app, 'drop_error', ''))
        self.assertEqual(self.app.group['unresolved'], [])

    def test_drop_undo_clear_and_autosave_preserve_pad_position(self):
        from ni_to_sp.direct import inspect_group
        source = inspect_group(self.kit)['pads'][0]['source']
        self.assertEqual(self.app.drop_sample(7, '{' + source + '}'), 'copy')
        self.assertEqual(self.app.group['pads'][7]['sp_pad'], 12)
        self.assertEqual(self.app.group['available'], 1)
        self.assertEqual(self.app.mpc_button.cget('state'), 'normal')
        self.assertTrue((self.app.data_dir / 'last-edited-kit.json').is_file())
        self.app.clear_pad(7)
        self.assertEqual(self.app.group['available'], 0)
        self.app.undo_edit()
        self.assertEqual(self.app.group['available'], 1)
        self.assertEqual(self.app.drop_sample(0, '{' + source + '} {' + source + '}'), 'refuse_drop')
        self.assertEqual(self.app.group['available'], 1)

    def test_missing_kit_pad_replacement_refreshes_export_and_preview(self):
        self.app.kits = [self.kit]
        self.app.filter_kits()
        self.app.kit_list.selection_set(0)
        self.app.select_kit()
        wait(self.app)
        self.assertEqual(self.app.group['unresolved'], [15])
        self.assertEqual(self.app.mpc_button.cget('state'), 'disabled')
        self.app.set_sample(14, self.app.group['pads'][0]['source'])
        self.assertEqual(self.app.group['unresolved'], [])
        self.assertIsNotNone(self.app.session.pads[14].sample_id)
        self.assertEqual(self.app.mpc_button.cget('state'), 'normal')
        self.assertEqual(self.app.send_open_button.cget('state'), 'normal')

    def test_random_replacement_and_mpc_export_use_same_edited_kit(self):
        from ni_to_sp.direct import inspect_group
        source = inspect_group(self.kit)['pads'][0]['source']
        self.app.kits = [self.kit]
        self.app.selected_pad = 6
        with patch.object(self.app.pool, 'choose', return_value=(source, {'method': 'test'})):
            self.app.random_pad(1)
            deadline = time.monotonic() + 5
            while self.app._candidate_loading and time.monotonic() < deadline:
                self.app.update()
                time.sleep(.01)
        self.assertIsNotNone(self.app._candidate)
        self.assertEqual(self.app.group['available'], 0)
        self.app.keep_candidate()
        self.assertEqual(self.app.group['available'], 1)
        with patch('ni_to_sp.converter_ui.filedialog.askdirectory', return_value=str(self.root / 'mpc')):
            self.app.export_mpc()
            wait(self.app)
        manifest = json.loads(next((self.root / 'mpc').glob('*/MANIFEST.json')).read_text())
        self.assertTrue(manifest['pads'][6]['file'])
        self.assertEqual(sum(bool(r['file']) for r in manifest['pads']), 1)

    def test_editor_fits_minimum_window(self):
        for size in ('1120x800', '1260x900'):
            self.app.geometry(size + '+10000+10000')
            self.app.update()
            for button in (self.app.mpc_button, self.app.send_open_button, self.app.export_button):
                self.assertLessEqual(button.winfo_rootx() + button.winfo_width(),
                                     self.app.winfo_rootx() + self.app.winfo_width())
                self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                     self.app.winfo_rooty() + self.app.winfo_height())
            for button in self.app.pad_buttons.values():
                self.assertGreaterEqual(button.winfo_height(), 44)

    def test_preview_toggle_keeps_custom_replacements_and_empty_kit(self):
        self.app.kits = [self.kit]
        self.app.filter_kits()
        self.app.kit_list.selection_set(0)
        self.app.select_kit()
        wait(self.app)
        source = self.app.group['pads'][0]['source']
        self.app.set_sample(14, source)
        self.app.clear_pad(3)
        self.app.use_previews_var.set(True)
        self.app.toggle_preview_sources()
        wait(self.app)
        self.assertEqual(self.app.group['pads'][14]['source'], source)
        self.assertEqual(self.app.group['pads'][3]['status'], 'empty')
        self.app.new_empty()
        self.app.toggle_preview_sources()
        self.assertEqual(self.app.group['available'], 0)


if __name__ == '__main__':
    unittest.main()
