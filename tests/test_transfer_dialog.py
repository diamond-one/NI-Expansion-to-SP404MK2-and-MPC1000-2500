import copy
import unittest
from unittest.mock import patch

import test_converter as fixtures
from test_browser import wait
from fixtures import probe
from ni_to_sp import sp_transfer
from ni_to_sp.direct import inspect_group, prepare_direct
from ni_to_sp.kit_editor import empty_kit, assign


class SelectionTests(unittest.TestCase):
    def test_real_status_shape_returns_project_bank_and_pad(self):
        raw = '< ch05 long  dst=57 7e 00 00 01 00 0c 00 00 ff ff ff ff 00 03 01 00 00 0b 33'
        self.assertEqual(sp_transfer.parse_selection(raw), {'project': 1, 'bank': 'B', 'pad': 13})

    def test_unknown_invalid_and_ambiguous_selection_never_guess_bank_a(self):
        for raw in ('', 'unsupported output', '< ch06 short dst=57 7e 00 00 01 00 00',
                    '< ch05 short dst=57 7e 00', '< ch05 short dst=57 7e 00 00 0a 00 00',
                    '< ch05 short dst=57 7e 10 00 01 00 00',
                    '< ch05 short dst=57 7e 00 00 01 00 10',
                    '< ch05 short dst=57 7e 00 00 01 00 00\n< ch05 short dst=57 7e 00 00 02 00 00'):
            self.assertIsNone(sp_transfer.parse_selection(raw), raw)

    def test_probe_correlates_selected_bank_with_checked_project(self):
        state = probe()
        state['banks'] = copy.deepcopy(state['banks'])
        raw = '< ch05 long  dst=57 7e 00 00 01 00 0c'
        def command(cli, arguments, *args, **kwargs):
            return raw if arguments[0] == 'raw' else 'status' if arguments[0] == 'status' else ''
        with patch.object(sp_transfer, 'list_ports', return_value=[{'port': 'COM4', 'serial': 'test'}]), \
             patch.object(sp_transfer, '_run', side_effect=command), \
             patch.object(sp_transfer, 'parse_status', return_value=state):
            self.assertEqual(sp_transfer.probe('fixture.exe')['current_bank'], 'B')
            state['project'] = 2
            with self.assertRaisesRegex(sp_transfer.TransferError, 'project changed'):
                sp_transfer.probe('fixture.exe')


class TransferDialogTests(unittest.TestCase):
    setUp = fixtures.ConverterTests.setUp
    tearDown = fixtures.ConverterTests.tearDown

    def open_dialog(self, device=None, ports=None):
        group = empty_kit('Transfer fixture')
        assign(group, 0, inspect_group(self.kit)['pads'][0]['source'])
        self.app.prepared = prepare_direct(group, self.root / 'sp-export')[0]
        state = copy.deepcopy(device if device is not None else {**probe(), 'current_bank': 'B'})
        ports = ports if ports is not None else [{'port': 'COM4', 'serial': 'fixture'}]
        for target, value in [('discover_cli', 'fixture.exe'), ('list_ports', ports), ('probe', state)]:
            mock = patch.object(sp_transfer, target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        self.app.transfer_dialog()
        self.window = self.app.transfer_window
        self.window.geometry('850x850+10000+10000')
        self.window.check()
        wait(self.app)
        self.app.update()
        return self.window

    def test_current_bank_is_default_single_usb_choice_hidden_and_no_write_on_open(self):
        with patch.object(sp_transfer, 'send') as send:
            window = self.open_dialog()
            self.assertEqual(window.bank.get(), 'B')
            self.assertEqual(window.plan.bank, 'B')
            self.assertFalse(window.device_row.winfo_manager())
            self.assertEqual(window.send_button.cget('state'), 'normal')
            self.assertIn('selected on your SP: B', window.bank_hint.cget('text'))
            self.assertEqual(window.plan.entries[0].target_pad, 'B13')
            send.assert_not_called()

    def test_replacing_is_one_explicit_switch_and_backup_location_is_automatic(self):
        device = {**probe(), 'current_bank': 'B'}
        device['occupied_pads'] = {'B13': {'signature': 'original', 'name': 'My old kick'}}
        window = self.open_dialog(device)
        self.assertEqual(window.send_button.cget('state'), 'disabled')
        with patch.object(sp_transfer, 'send', return_value={
                'completed_pads': list(range(16)), 'journal': 'fixture', 'backup_dir': 'fixture-backup'}) as send:
            window.send()
            send.assert_not_called()
            window.replace_switch.toggle()
            self.assertEqual(window.send_button.cget('state'), 'normal')
            window.send_button.invoke()
            wait(self.app)
            send.assert_called_once()
            self.assertIs(send.call_args.kwargs['allow_overwrite'], True)
            self.assertIs(send.call_args.kwargs['reviewed'], True)
            self.assertEqual(send.call_args.kwargs['backup_dir'], self.app.data_dir / 'SP-backups')
            self.assertEqual(window.send_button.cget('state'), 'disabled')

    def test_bank_change_resets_replacement_permission_and_preserves_manual_choice_on_refresh(self):
        window = self.open_dialog()
        window.replace.set(True)
        window.bank.set('D')
        window.bank_changed()
        self.assertFalse(window.replace.get())
        self.assertEqual(window.plan.bank, 'D')
        window.check()
        wait(self.app)
        self.assertEqual(window.plan.bank, 'D')
        self.assertIn('currently on bank B', window.bank_hint.cget('text'))

    def test_unknown_current_bank_requires_an_explicit_selection(self):
        window = self.open_dialog(probe())
        self.assertEqual(window.bank.get(), '')
        self.assertIsNone(window.plan)
        self.assertEqual(window.send_button.cget('state'), 'disabled')
        window.bank.set('F')
        window.bank_changed()
        self.assertEqual(window.plan.bank, 'F')

    def test_multiple_devices_require_an_explicit_device_before_probing(self):
        with patch.object(sp_transfer, 'probe') as read:
            # open_dialog installs the probe mock itself; check its actual calls.
            window = self.open_dialog(ports=[{'port': 'COM4'}, {'port': 'COM5'}])
            self.assertTrue(window.device_row.winfo_manager())
            self.assertIsNone(window.plan)
            sp_transfer.probe.assert_not_called()
            window.port.set('COM5')
            window.device_changed()
            wait(self.app)
            sp_transfer.probe.assert_called_once_with('fixture.exe', 'COM5')

    def test_protected_bank_blocks_send_and_failed_send_requires_refresh(self):
        device = {**probe(), 'current_bank': 'B'}
        device['banks']['B']['protected'] = True
        window = self.open_dialog(device)
        self.assertIsNone(window.plan)
        self.assertIn('protected', window.message.cget('text'))
        window.bank.set('C')
        window.bank_changed()
        with patch.object(sp_transfer, 'send', side_effect=sp_transfer.TransferError('Disconnected')):
            window.send()
            wait(self.app)
        self.assertIsNone(window.plan)
        self.assertIn('Refresh SP', window.message.cget('text'))
        self.assertEqual(window.send_button.cget('state'), 'disabled')

    def test_primary_action_stays_visible_at_minimum_dialog_size(self):
        window = self.open_dialog()
        window.geometry('760x680+10000+10000')
        self.app.update()
        action = window.send_button
        self.assertLessEqual(action.winfo_rootx() + action.winfo_width(), window.winfo_rootx() + window.winfo_width())
        self.assertLessEqual(action.winfo_rooty() + action.winfo_height(), window.winfo_rooty() + window.winfo_height())

    def test_backup_failure_stops_the_real_transfer_engine_before_any_import(self):
        device = {**probe(), 'current_bank': 'B'}
        device['occupied_pads'] = {'B13': {'signature': 'original', 'name': 'Keep my kick'}}
        window = self.open_dialog(device)
        with patch.object(sp_transfer, '_run', return_value='') as command:
            with self.assertRaisesRegex(sp_transfer.TransferError, 'backup was not saved'):
                sp_transfer.send(window.plan, 'fixture.exe', window.export_folder,
                    reviewed=True, allow_overwrite=True, backup_dir=window.backup_folder)
        self.assertEqual([call.args[1][0] for call in command.call_args_list], ['get'])

    def test_device_project_change_blocks_the_real_transfer_engine_before_backup(self):
        window = self.open_dialog()
        changed = {**probe(), 'project': 2, 'project_name': 'Different project'}
        with patch.object(sp_transfer, 'probe', return_value=changed), \
             patch.object(sp_transfer, '_run') as command:
            with self.assertRaises(sp_transfer.TransferError):
                sp_transfer.send(window.plan, 'fixture.exe', window.export_folder,
                    reviewed=True, backup_dir=window.backup_folder)
        command.assert_not_called()
