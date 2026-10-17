import os
from pathlib import Path
import tempfile
import time
import sys
import traceback
import unittest
from unittest.mock import patch

import customtkinter as ctk
from direct_fixtures import direct_fixture
from ni_to_sp.browser_ui import App
from ni_to_sp.direct import inspect_group, prepare_direct


def wait(app):
    deadline = time.monotonic() + 15
    while app.busy:
        if time.monotonic() > deadline:
            stacks = '\n'.join(''.join(traceback.format_stack(frame)) for frame in sys._current_frames().values())
            raise AssertionError(f'Browser job did not finish; queued={app.events.qsize()}; status={app.status.cget("text")}\n{stacks}')
        app.update()
        time.sleep(.01)
    app.update()


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kit = direct_fixture(self.root, missing=(14,))
        self.env = patch.dict(os.environ, {'NI_TO_SP_DATA_DIR': str(self.root / 'appdata')})
        self.env.start()
        with patch.object(App, 'scan', lambda _: None):
            self.app = App()
        self.app.geometry('1120x800+10000+10000')
        self.app.update()

    def tearDown(self):
        self.app.close()
        self.env.stop()
        self.temp.cleanup()

    def select(self):
        self.app.kits = [self.kit]
        self.app.filter_kits()
        self.app.kit_list.selection_set(0)
        self.app.select_kit()
        wait(self.app)

    def test_select_immediately_loads_named_preview_without_copying_or_rendering(self):
        self.select()
        self.assertEqual(self.app.group['available'], 15)
        self.assertIsNone(self.app.prepared)
        self.assertFalse((self.app.data_dir / 'prepared').exists())
        self.assertIn('Kick Test 1', self.app.pad_buttons[0].cget('text'))
        self.assertIn('Needs WAV', self.app.pad_buttons[14].cget('text'))
        self.assertEqual(self.app.tabs.get(), 'Play & export')
        self.assertEqual(self.app.send_open_button.cget('state'), 'disabled')
        self.app.silence_var.set(True)
        self.app.sync()
        self.assertEqual(self.app.send_open_button.cget('state'), 'normal')

    def test_send_prepares_all_positions_then_opens_destination_review_without_writing(self):
        self.select()
        self.app.silence_var.set(True)
        with patch.object(self.app, 'transfer_dialog') as review, patch('ni_to_sp.sp_transfer.send') as write:
            self.app.send_current()
            wait(self.app)
            self.assertTrue(self.app.prepared.is_dir())
            review.assert_called_once()
            write.assert_not_called()
            self.app.silence_var.set(False)
            self.app.sync()
            self.assertEqual(self.app.send_open_button.cget('state'), 'disabled')
            self.app.send_current()
            self.assertEqual(review.call_count, 1)

    def test_batch_uses_every_matching_kit_beyond_the_visible_300(self):
        self.app.kits = [{**self.kit, 'name': f'Kit {i}', 'id': str(i)} for i in range(350)]
        self.app.filter_kits()
        self.assertEqual(self.app.kit_list.size(), 300)
        with patch('ni_to_sp.browser_ui.filedialog.askdirectory', return_value=str(self.root / 'exports')), \
             patch('ni_to_sp.browser_ui.prepare_batch', return_value=(self.root / 'report.json', {'converted': [], 'skipped': [], 'cancelled': False})) as batch, \
             patch('ni_to_sp.browser_ui.messagebox.showinfo'):
            self.app.export_batch()
            wait(self.app)
            self.assertEqual(len(batch.call_args.args[0]), 350)
            self.assertTrue(batch.call_args.args[5])

    def test_all_expansions_ignores_search_and_includes_nonvisible_kits(self):
        self.app.kits = [{**self.kit, 'name': f'Kit {i}', 'id': str(i),
                          'pack': 'Other expansion' if i else self.kit['pack']} for i in range(350)]
        self.app.pack_var.set(self.kit['pack'])
        self.app.filter_kits()
        self.assertEqual(len(self.app.filtered), 1)
        with patch('ni_to_sp.browser_ui.filedialog.askdirectory', return_value=str(self.root / 'exports')) as dialog, \
             patch('ni_to_sp.browser_ui.prepare_batch', return_value=(self.root / 'report.json', {'converted': [], 'skipped': [], 'cancelled': False})) as batch, \
             patch('ni_to_sp.browser_ui.messagebox.showinfo'):
            self.app.all_batch_button.invoke()
            wait(self.app)
            self.assertEqual(len(batch.call_args.args[0]), 350)
            self.assertTrue(batch.call_args.args[5])
            self.assertIn('350 all installed kits', dialog.call_args.kwargs['title'])
            self.assertEqual(len(self.app.filtered), 1)
            self.app.pack_var.set('No matches')
            self.app.filter_kits()
            self.assertEqual(self.app.batch_button.cget('state'), 'disabled')
            self.assertEqual(self.app.all_batch_button.cget('state'), 'normal')
            self.app.use_previews_var.set(False)
            self.app.export_batch(all_expansions=True)
            wait(self.app)
            self.assertFalse(batch.call_args.args[5])

    def test_replace_missing_pad_and_reopen_export_keeps_its_position(self):
        self.select()
        source = self.app.group['pads'][0]['source']
        with patch('ni_to_sp.browser_ui.filedialog.askopenfilename', return_value=source):
            self.app.replace_pad(14)
        self.assertEqual(self.app.group['unresolved'], [])
        self.assertEqual(self.app.group['pads'][14]['ni_pad'], 15)
        prepared, _ = prepare_direct(self.app.group, self.root / 'export')
        with patch('ni_to_sp.browser_ui.filedialog.askdirectory', return_value=str(prepared)):
            self.app.open_completed()
            wait(self.app)
        self.assertEqual(self.app.prepared, prepared)
        self.assertIsNone(self.app.group)
        self.assertEqual(self.app.send_open_button.cget('state'), 'normal')
        self.app.show_check_report()
        self.app.update()
        self.assertTrue(any(isinstance(w, ctk.CTkToplevel) for w in self.app.winfo_children()))

    def test_sound_preview_is_labelled_and_can_be_switched_off(self):
        from test_sound_previews import preview
        from ni_to_sp.direct import sound_previews
        preview(self.kit, 'Sound Test 15')
        sound_previews.cache_clear()
        self.select()
        self.assertEqual(self.app.group['available'], 16)
        self.assertIn('NI preview', self.app.pad_buttons[14].cget('text'))
        self.assertEqual(self.app.send_open_button.cget('state'), 'normal')
        self.app.use_previews_var.set(False)
        self.app.select_kit(force=True)
        wait(self.app)
        self.assertEqual(self.app.group['unresolved'], [15])
        self.assertEqual(self.app.send_open_button.cget('state'), 'disabled')

    def test_original_queue_uses_all_installed_kits_and_keeps_preview_separate(self):
        import json
        self.app.kits = [{**self.kit, 'name': f'Kit {i}', 'id': str(i),
                          'pack': 'Other' if i else self.kit['pack']} for i in range(350)]
        self.app.pack_var.set(self.kit['pack'])
        self.app.filter_kits()
        self.app.original_destination.set(str(self.root / 'original-exports'))
        with patch('ni_to_sp.original_ui.subprocess.Popen') as spawn:
            spawn.return_value.poll.return_value = None
            self.app.start_original()
            report = json.loads(self.app.original_queue_path.read_text())
            self.assertEqual(report['total'], 350)
            self.assertEqual(report['mode'], 'original')
            self.assertIsNone(self.app.prepared)
            self.assertIn('--original-queue', spawn.call_args.args[0])
            self.assertEqual(self.app.original_start.cget('state'), 'disabled')
            self.app.pause_original()
            self.assertTrue(self.app.original_queue_path.with_suffix('.pause').is_file())
            spawn.return_value.poll.return_value = 0
            report['completed'] = [{'folder': str(self.root / 'rendered-kit')}]
            report['pending'].pop(0)
            self.app.original_queue_path.write_text(json.dumps(report))
            self.app.poll_original_queue()
            with patch.object(self.app, 'open_completed') as open_kit:
                self.app.open_latest_original()
                open_kit.assert_called_once_with(str(self.root / 'rendered-kit'))
            self.assertEqual(self.app.tabs.get(), 'Play & export')


if __name__ == '__main__':
    unittest.main()
