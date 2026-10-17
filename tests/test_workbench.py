from pathlib import Path
from unittest.mock import patch
import time
import unittest
import customtkinter as ctk

import test_converter as fixtures
from test_browser import wait
from ni_to_sp import desk_theme as T
from ni_to_sp.kit_editor import MODES


class WorkbenchTests(unittest.TestCase):
    setUp = fixtures.ConverterTests.setUp
    tearDown = fixtures.ConverterTests.tearDown
    def test_target_switch_preserves_assignments_and_has_one_primary_action(self):
        from ni_to_sp.direct import inspect_group
        source = inspect_group(self.kit)['pads'][0]['source']
        self.app.set_sample(7, source)
        before = self.app.group['pads'][7].copy()
        self.app.target_var.set('MPC1000')
        self.app.change_target()
        self.app.update()
        self.assertFalse(self.app.send_open_button.winfo_manager())
        self.assertEqual(self.app.mpc_button.winfo_manager(), 'grid')
        self.assertFalse(self.app.export_button.winfo_manager())
        self.assertEqual(self.app.group['pads'][7], before)
        self.app.target_var.set('SP-404MKII')
        self.app.change_target()
        self.app.update()
        self.assertEqual(self.app.send_open_button.winfo_manager(), 'grid')
        self.assertFalse(self.app.mpc_button.winfo_manager())
        self.assertEqual(self.app.export_button.winfo_manager(), 'pack')

    def test_four_scope_combinations_and_keyboard_physical_navigation(self):
        for same_pad in (False, True):
            for all_expansions in (False, True):
                self.app.random_matching.set('Same pad' if same_pad else 'Any sample')
                self.app.random_expansion.set('All expansions' if all_expansions else 'This expansion')
                self.app.update_random_scope()
                self.assertEqual(self.app.random_mode.get(), MODES[2 * same_pad + all_expansions])
        self.app.focus_pad(0)
        self.app.move_pad(0, 4)
        self.assertEqual(self.app.selected_pad, 4)
        self.app.move_pad(4, 1)
        self.assertEqual(self.app.selected_pad, 5)
        self.app.move_pad(3, 1)
        self.assertEqual(self.app.selected_pad, 5)  # Never wrap to another physical row.
        with patch.object(self.app, 'preview_pad') as preview:
            self.app.keyboard_preview(5)
            preview.assert_called_once_with(5)

    def test_missing_pad_navigation_and_full_source_information(self):
        self.app.kits = [self.kit]
        self.app.filter_kits()
        self.app.kit_list.selection_set(0)
        self.app.select_kit()
        wait(self.app)
        self.app.next_missing()
        self.assertEqual(self.app.selected_pad, 14)
        self.assertIn('Audio is missing', self.app.pad_state_label.cget('text'))
        self.app.set_sample(14, self.app.group['pads'][0]['source'])
        self.assertEqual(self.app.next_missing_button.cget('state'), 'disabled')
        self.assertEqual(self.app.sound_name_label.cget('text'), self.app.group['pads'][14]['name'])
        self.assertEqual(self.app.source_label.cget('text'), Path(self.app.group['pads'][14]['source']).name)
        deadline = time.monotonic() + 3
        while not self.app.waveform.metadata and time.monotonic() < deadline:
            self.app.update()
            time.sleep(.01)
        self.assertIn('kHz', self.app.waveform.metadata)

    def test_actions_fit_and_text_colours_have_readable_contrast(self):
        def luminance(hex_colour):
            values = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            values = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
            return sum(a * b for a, b in zip(values, (.2126, .7152, .0722)))
        for foreground, background in ((T.INK, T.WORKSPACE), (T.MUTED, T.PAPER),
                (T.ON_DARK, T.ACTION), (T.DARK_MUTED, T.LIBRARY), (T.MISSING_INK, T.MISSING)):
            a, b = sorted((luminance(foreground), luminance(background)))
            self.assertGreaterEqual((b + .05) / (a + .05), 4.5)
        for geometry in ('1120x800', '1320x900'):
            self.app.geometry(geometry + '+10000+10000')
            self.app.update()
            for target in ('SP-404MKII', 'MPC1000'):
                self.app.target_var.set(target)
                self.app.change_target()
                self.app.update()
                action = self.app.send_open_button if target == 'SP-404MKII' else self.app.mpc_button
                self.assertLessEqual(action.winfo_rootx() + action.winfo_width(),
                    self.app.winfo_rootx() + self.app.winfo_width())
                self.assertLessEqual(action.winfo_rooty() + action.winfo_height(),
                    self.app.winfo_rooty() + self.app.winfo_height())
                self.assertGreaterEqual(action.winfo_width(), 150)

    def test_connections_dialog_retains_the_mpc_mapping_and_channel_choices(self):
        with patch('ni_to_sp.converter_ui.simple_audio_outputs', return_value=[]), \
             patch('ni_to_sp.converter_ui.list_input_ports', return_value=['MPC interface']):
            self.app.audio_settings()
            wait(self.app)
        window = next(w for w in self.app.winfo_children() if isinstance(w, ctk.CTkToplevel))
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        choices = [w for w in descendants(window) if isinstance(w, ctk.CTkOptionMenu)]
        self.assertEqual(len(choices), 4)
        choices[1].set('MPC interface')
        choices[2].set('MPC1000 / JJOS3 · program pads')
        choices[3].set('16')
        save = next(w for w in descendants(window) if isinstance(w, ctk.CTkButton) and w.cget('text') == 'Save connections')
        self.app.update()
        self.assertLessEqual(save.winfo_rooty() + save.winfo_height(), window.winfo_rooty() + window.winfo_height())
        save.invoke()
        self.assertEqual(self.app.connection_settings['profile'], 'mpc1000')
        self.assertEqual(self.app.connection_settings['channel'], 16)
        self.assertEqual(self.app.connection_settings['midiInputName'], 'MPC interface')
