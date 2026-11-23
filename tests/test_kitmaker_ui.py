"""The Make kits tab, driven the way you use it: keys, keep / skip / undo, swap, export, persistence."""
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from test_kitmaker import FULL, make_kit
from ni_to_sp import kitmaker as km
from ni_to_sp import pad_look as L
from ni_to_sp import kitmaker_ui as kmui
from ni_to_sp import desk_theme as T


def wait(app, condition, limit=30):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        app.update()
        if condition():
            return True
        time.sleep(.01)
    return False


class Base(unittest.TestCase):
    SPARSE = ['Kick Sp', 'Snare Sp', 'ClosedHH Sp', 'Clap Sp'] + [None] * 12      # its own files: not a copy of Alpha

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        make_kit(self.root, 'Alpha', FULL, pack='Aaa Library')
        make_kit(self.root, 'Beta', ['Kick Beta', 'Snare Beta', 'ClosedHH Beta', 'OpenHH Beta', 'Clap Beta', 'Rim Beta', 'Tom Beta', 'Crash Beta'] + [None] * 8,
                 pack='Bbb Library')
        make_kit(self.root, 'Gamma', ['Kick Gamma', 'Snare Gamma', 'ClosedHH Gamma', 'OpenHH Gamma', 'Clap Gamma', 'Rim Gamma', 'Tom Gamma', 'Crash Gamma'] + [None] * 8,
                 pack='Ccc Library')
        self.libdir = self.root / 'kits'
        self.apps = []
        self.app = self.make_app()

    def make_app(self):
        from ni_to_sp.converter_ui import App
        env = {'NI_TO_SP_DATA_DIR': str(self.root / 'data'), 'NI_TO_SP_DISABLE_AUDIO': '1', 'NI_TO_SP_KITMAKER_AUTOSTART': '0',
               'NI_KITMAKER_DIR': str(self.libdir)}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        with patch.object(App, 'scan', lambda _: None):
            app = App()
        app.geometry('1380x920+10000+10000')
        app.root_var.set(str(self.root / 'installed'))
        app.update()
        self.apps.append(app)
        app.km_start(force=True)
        self.assertTrue(wait(app, lambda: app.km.pool_ready and app.km.cand is not None and not app.km.filling), 'pool not ready')
        return app

    def tearDown(self):
        for app in self.apps:
            try:
                app.close()
            except tk.TclError:
                pass
        self.temp.cleanup()

    def press(self, key, app=None):
        app = app or self.app
        app.focus_force()
        app.update()
        app.event_generate('<KeyPress-%s>' % key)
        app.update()

    def kept(self):
        return [m['id'] for m in self.app.km.library.kits()]

    def settle(self):
        self.assertTrue(wait(self.app, lambda: not self.app.km.filling and not self.app.km.swapping))


class TabTests(Base):
    def test_make_kits_is_the_first_tab_and_the_default(self):
        self.assertEqual(self.app.tabs._name_list, ['Make kits', 'Play & export', 'Batch export'])
        self.assertEqual(self.app.tabs.get(), 'Make kits')

    def test_the_editors_rail_and_file_keys_step_aside_on_this_tab_and_return_on_the_others(self):
        self.app.update()
        self.assertFalse(self.app.library_frame.winfo_ismapped())
        self.assertFalse(self.app.head_files.winfo_ismapped())
        self.app.tabs.set('Play & export')
        self.app.update()
        self.assertTrue(self.app.library_frame.winfo_ismapped())
        self.assertTrue(self.app.head_files.winfo_ismapped())
        self.app.tabs.set('Make kits')
        self.app.update()
        self.assertFalse(self.app.library_frame.winfo_ismapped())

    def test_the_first_kit_is_shown_on_eight_lit_pads_in_the_mpc_layout(self):
        km_ = self.app.km
        cand = km_.cand
        self.assertEqual(self.app.km_ui.title.cget('text'), cand['kit'])
        self.assertIn(cand['pack'], self.app.km_ui.detail.cget('text'))
        self.assertIn('8/8 hits', self.app.km_ui.detail.cget('text'))
        pads = self.app.km_ui.pads
        self.assertEqual(sorted(pads), list(range(8)))
        for index, pad in pads.items():
            slot = cand['slots'][index]
            self.assertEqual(pad._state['tag'], km.HOUSE_LAYOUT[index][0])
            self.assertEqual(pad._state['glow'], L.pad_colour(slot['kind']))
            self.assertFalse(pad._state['unlit'])
            self.assertIn(str(index + 1), pad.cget('text').split('\n')[0])
        # pad 1 sits bottom-left, pad 5 top-left, like the MPC1000
        self.app.update()
        self.assertGreater(pads[0].winfo_rooty(), pads[4].winfo_rooty())
        self.assertLess(pads[0].winfo_rootx(), pads[1].winfo_rootx())
        self.assertEqual(pads[0].winfo_rootx(), pads[4].winfo_rootx())

    def test_the_selected_pad_has_a_white_rim_and_the_side_panel_names_it(self):
        self.press('3')
        self.assertEqual(self.app.km.selected, 2)
        self.assertEqual(self.app.km_ui.pads[2]._state['border_color'], '#FFFFFF')
        self.assertIsNone(self.app.km_ui.pads[0]._state['border_color'])
        self.assertIn('CL HAT', self.app.km_ui.pad_title.cget('text'))
        self.assertEqual(self.app.km_ui.pad_name.cget('text'), self.app.km.cand['slots'][2]['name'])


class KeepSkipUndoTests(Base):
    def test_y_keeps_the_kit_as_k001_and_moves_on(self):
        first = self.app.km.cand['id']
        self.press('y')
        self.assertEqual(self.kept(), ['K001'])
        self.assertNotEqual(self.app.km.cand['id'], first)
        self.assertEqual(self.app.km.pos, 1)
        self.assertIn('Kept K001', self.app.km_ui.status.cget('text'))
        self.assertIn('1/8', self.app.km_ui.status.cget('text'))
        self.assertTrue((self.libdir / 'kits/K001/KICK.WAV').is_file())

    def test_n_skips_without_keeping_and_a_skipped_kit_does_not_come_back(self):
        first = self.app.km.cand['id']
        self.press('n')
        self.assertEqual(self.kept(), [])
        self.assertIn(first, self.app.km.seen)
        self.assertNotIn(first, [c['id'] for c in self.app.km.order[self.app.km.pos:]])

    def test_u_undoes_a_keep_and_a_skip_in_reverse_order(self):
        first = self.app.km.cand['id']
        self.press('y')
        second = self.app.km.cand['id']
        self.press('n')
        self.press('u')                                                  # undo the skip
        self.assertEqual(self.app.km.cand['id'], second)
        self.assertEqual(self.kept(), ['K001'])
        self.press('u')                                                  # undo the keep
        self.assertEqual(self.app.km.cand['id'], first)
        self.assertEqual(self.kept(), [])
        self.assertNotIn(first, self.app.km.seen)
        self.press('u')
        self.assertIn('Nothing to undo', self.app.km_ui.status.cget('text'))
        self.press('y')
        self.assertEqual(self.kept(), ['K001'])                           # the id is reused: no gap

    def test_the_program_counter_follows_the_keeps(self):
        self.press('y')
        self.assertIn('KITS_001_008', self.app.km_ui.program.cget('text'))
        self.assertIn('1/8', self.app.km_ui.program.cget('text'))
        self.assertIn('K001', self.app.km_ui.recent.cget('text'))
        self.assertIn('1 kept', self.app.km_ui.counter.cget('text'))

    def test_the_end_of_the_queue_is_handled_and_keys_do_nothing_then(self):
        for _ in range(len(self.app.km.order)):
            self.press('n')
        self.assertIsNone(self.app.km.cand)
        self.assertEqual(self.app.km_ui.title.cget('text'), 'All done')
        for key in ('y', 'n', 'r', 'space'):
            self.press(key)                                              # nothing to act on; nothing breaks
        self.assertEqual(self.app.km_ui.keep.cget('state'), 'disabled')
        self.assertEqual(self.app.km_ui.hear.cget('state'), 'disabled')

    def test_buttons_follow_what_is_possible(self):
        self.assertEqual(self.app.km_ui.keep.cget('state'), 'normal')
        self.assertEqual(self.app.km_ui.undo.cget('state'), 'disabled')
        self.assertEqual(self.app.km_ui.export.cget('state'), 'disabled')
        self.press('y')
        self.assertEqual(self.app.km_ui.undo.cget('state'), 'normal')
        self.assertEqual(self.app.km_ui.export.cget('state'), 'normal')

    def test_what_you_reviewed_is_remembered_across_sessions(self):
        first, second = self.app.km.cand['id'], None
        self.press('y')
        second = self.app.km.cand['id']
        self.press('n')
        again = self.make_app()
        ids = [c['id'] for c in again.km.order]
        self.assertNotIn(first, ids)
        self.assertNotIn(second, ids)
        self.assertEqual(again.km.library.kits()[0]['id'], 'K001')


class SavedFeedbackTests(Base):
    def test_keeping_shows_a_saved_badge_naming_the_kit_and_the_program_slot(self):
        ui = self.app.km_ui
        self.assertFalse(ui.saved.winfo_ismapped())
        self.press('y')
        self.assertTrue(ui.saved.winfo_ismapped())
        text = ui.saved.cget('text')
        self.assertIn('SAVED', text)
        self.assertIn('K001', text)
        self.assertIn('1/8 in KITS_001_008', text)
        self.assertEqual(ui.keep.cget('text'), 'Kept ✓')

    def test_the_program_meter_fills_one_cell_per_kept_kit_and_the_newest_stands_out(self):
        ui = self.app.km_ui
        self.assertTrue(all(cell.cget('fg_color') == T.LINE for cell in ui.meter))
        self.press('y')
        self.settle()
        self.assertEqual(ui.meter[0].cget('fg_color'), kmui.SAVED_NEW)
        self.assertEqual(ui.meter[1].cget('fg_color'), T.LINE)
        self.press('y')
        self.settle()
        self.assertEqual(ui.meter[0].cget('fg_color'), kmui.SAVED)
        self.assertEqual(ui.meter[1].cget('fg_color'), kmui.SAVED_NEW)

    def test_the_badge_goes_away_by_itself_and_the_key_returns_to_normal(self):
        with patch.object(kmui, 'SAVED_SECONDS', .05):
            self.press('y')
            self.assertTrue(wait(self.app, lambda: not self.app.km_ui.saved.winfo_ismapped(), 5))
        self.assertEqual(self.app.km_ui.keep.cget('text'), 'Keep (Y)')
        self.assertEqual(self.app.km_ui.meter[0].cget('fg_color'), kmui.SAVED)       # the cell stays filled

    def test_skip_shows_no_badge_and_undo_takes_it_away(self):
        self.press('n')
        self.assertFalse(self.app.km_ui.saved.winfo_ismapped())
        self.press('u')
        self.press('y')
        self.assertTrue(self.app.km_ui.saved.winfo_ismapped())
        self.press('u')
        self.assertFalse(self.app.km_ui.saved.winfo_ismapped())
        self.assertTrue(all(cell.cget('fg_color') == T.LINE for cell in self.app.km_ui.meter))

    def test_white_text_on_the_badge_is_legible(self):
        def lum(h):
            c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4 for x in c]
            return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]
        ratio = (lum(kmui.SAVED_INK[0]) + .05) / (lum(kmui.SAVED[0]) + .05)
        self.assertGreaterEqual(ratio, 4.5)


class AuditionTests(Base):
    def test_space_plays_the_beat_and_a_pad_click_plays_that_hit(self):
        self.press('space')
        shape, rate = self.app.km.last_played
        self.assertEqual((shape[1], rate), (2, 44100))
        self.assertGreater(shape[0] / 44100, 5)                          # two bars at 92 bpm and a tail
        self.app.km_ui.pads[1].invoke()
        self.assertEqual(self.app.km.selected, 1)
        self.assertLess(self.app.km.last_played[0][0], shape[0])         # one hit is shorter than the beat

    def test_space_again_while_the_beat_is_sounding_stops_it_and_a_third_press_plays_again(self):
        app = self.app
        with patch.object(type(app), 'stop_audio') as stop:
            self.press('space')
            self.assertEqual(stop.call_count, 0)
            first = app.km.last_played
            app.km.last_played = None
            self.press('space')                                          # still sounding: this one stops
            self.assertEqual(stop.call_count, 1)
            self.assertIsNone(app.km.last_played)
            self.press('space')                                          # stopped: this one plays
            self.assertEqual(app.km.last_played, first)
            self.assertEqual(stop.call_count, 1)

    def test_space_plays_again_once_the_beat_has_finished(self):
        app = self.app
        with patch.object(type(app), 'stop_audio') as stop:
            self.press('space')
            app.km.sounding_until = 0                                    # the beat ran out
            app.km.last_played = None
            self.press('space')
            self.assertEqual(stop.call_count, 0)
            self.assertIsNotNone(app.km.last_played)

    def test_a_swap_cuts_off_the_beat_and_plays_only_the_new_hit(self):
        app = self.app
        self.press('space')
        beat = app.km.last_played[0][0]
        with patch.object(type(app), 'stop_audio') as stop:
            self.press('r')
            self.settle()
            self.assertEqual(stop.call_count, 1)
        self.assertLess(app.km.last_played[0][0], beat)

    def test_keys_are_ignored_while_typing(self):
        entry = tk.Entry(self.app)
        entry.place(x=0, y=0, width=80)
        self.app.update()
        entry.focus_force()
        self.app.update()
        entry.event_generate('<KeyPress-y>')
        entry.event_generate('<KeyPress-n>')
        self.app.update()
        self.assertEqual(self.kept(), [])
        self.assertEqual(self.app.km.pos, 0)
        entry.destroy()

    def test_on_the_editor_tab_the_digit_and_lock_keys_keep_their_old_jobs(self):
        self.app.tabs.set('Play & export')
        self.app.update()
        calls = []
        with patch.object(type(self.app), 'swap_unlocked', lambda s: calls.append('swap')), \
                patch.object(type(self.app), 'toggle_lock', lambda s, index=None: calls.append('lock')):
            self.press('1')
            self.press('l')
            self.press('y')                                              # not a Make kits key here
        self.assertEqual(calls, ['swap', 'lock'])
        self.assertEqual(self.kept(), [])
        self.assertEqual(self.app.km.selected, 0)


class SwapTests(Base):
    def test_r_swaps_the_selected_hit_for_another_of_its_role_and_plays_just_that_hit(self):
        self.press('2')
        before = self.app.km.cand['slots'][1]['source']
        before_name = self.app.km.cand['slots'][1]['name']
        self.press('r')
        self.settle()
        slot = self.app.km.cand['slots'][1]
        self.assertNotEqual(slot['source'], before)
        self.assertEqual(slot['origin'], 'swapped')
        self.assertTrue(slot['name'].startswith('Snare'))
        self.assertIn('swapped', self.app.km_ui.pads[1].cget('text'))
        shape, _ = self.app.km.last_played
        self.assertLess(shape[0] / 44100, 4)                             # one hit, not the two-bar beat
        status = self.app.km_ui.status.cget('text')
        self.assertIn(slot['name'], status)
        self.assertIn(before_name, status)                               # "was -> now"
        self.assertIn('→', status)

    def test_repeated_swaps_keep_finding_new_sounds(self):
        seen = {self.app.km.cand['slots'][0]['source']}
        for _ in range(4):
            self.press('1')
            self.press('r')
            self.settle()
            seen.add(self.app.km.cand['slots'][0]['source'])
        self.assertEqual(len(seen), 5)

    def test_a_kept_kit_carries_the_swapped_hit(self):
        self.press('1')
        self.press('r')
        self.settle()
        swapped = self.app.km.cand['slots'][0]['source']
        self.press('y')
        manifest = self.app.km.library.kits()[0]
        self.assertEqual(manifest['slots'][0]['source'], swapped)
        self.assertEqual(manifest['slots'][0]['origin'], 'swapped')

    def test_swap_without_a_kit_does_nothing(self):
        for _ in range(len(self.app.km.order)):
            self.press('n')
        self.press('r')
        self.assertFalse(self.app.km.swapping)


class FillTests(Base):
    def test_a_kit_with_empty_roles_is_filled_in_the_background_and_then_kept(self):
        make_kit(self.root, 'Sparse', self.SPARSE, pack='Aaa Library')
        app = self.make_app()
        sparse = next(i for i, c in enumerate(app.km.order) if c['kit'] == 'Sparse')
        for _ in range(sparse):
            self.press('n', app)
        self.assertEqual(app.km.cand['kit'], 'Sparse')
        self.assertTrue(wait(app, lambda: not app.km.filling))
        self.assertEqual(app.km.cand['filled'], 8)
        self.assertIn('8/8 hits', app.km_ui.detail.cget('text'))
        self.assertTrue(any(s['origin'] == 'filled' for s in app.km.cand['slots']))
        self.press('y', app)
        manifest = app.km.library.kits()[-1]
        self.assertEqual(sum(bool(s['wav']) for s in manifest['slots']), 8)


class FakeEngine:
    running, loaded_bank = True, None

    def __init__(self, **_):
        self.loads = []

    def prepare(self, session, bank=0, preserve=False):
        self.loads.append([p.sample_id for p in session.pads[:8]] and [s.path for s in session.samples])
        self.loaded_bank = bank
        return {}

    update_bank = prepare

    def start(self):
        pass

    def close(self):
        self.running = False

    def trigger(self, *_):
        return True

    def silence_pad(self, *_):
        pass


class FakeMidi:
    connected = True

    def __init__(self, *_, **__):
        pass

    def connect(self):
        pass

    def disconnect(self):
        self.connected = False

    def set_mpc_bank(self, *_):
        pass


class MidiPreviewTests(Base):
    """MIDI preview used to say 'Select a kit' on this tab because only the editor's kit counted."""

    def turn_on(self):
        app = self.app
        app.connection_settings.update(midiInputName='USB Midi 0')
        app.audio_device = lambda settings, outputs: None
        app.player.set_output_device = lambda device: None
        for name, value in (('LiveSampler', FakeEngine), ('MIDIController', FakeMidi),
                            ('list_input_ports', lambda: ['USB Midi 0']), ('list_audio_outputs', lambda: [])):
            patcher = patch(f'ni_to_sp.live_controls.{name}', value)
            patcher.start()
            self.addCleanup(patcher.stop)
        app.toggle_live_preview()
        self.assertTrue(wait(app, lambda: app.live_engine is not None and not app._live_connecting), app.status.cget('text')
                        if hasattr(app, 'status') else 'preview did not connect')

    def test_the_current_kit_becomes_a_session_on_hardware_pads_1_to_8_only_on_this_tab(self):
        app, cand = self.app, self.app.km.cand
        session = app.km_live_session()
        self.assertEqual([s.path for s in session.samples], [slot['source'] for slot in cand['slots']])
        self.assertEqual(sum(bool(p.sample_id) for p in session.pads), 8)
        self.assertTrue(all(not p.sample_id for p in session.pads[8:]))
        self.assertIs(app.km_live_session(), session)                  # not rebuilt while the kit is unchanged
        app.tabs.set('Play & export')
        app.update()
        self.assertIsNone(app.km_live_session())

    def test_it_turns_on_from_this_tab_without_an_editor_kit_and_loads_the_kit_showing(self):
        self.assertEqual(self.app.group['available'], 0)               # the editor holds only an empty kit
        self.turn_on()
        engine = self.app.live_engine
        self.assertEqual(engine.loads[-1], [slot['source'] for slot in self.app.km.cand['slots']])
        self.assertIn('MIDI preview: ON', self.app.live_button.cget('text'))

    def test_the_pads_follow_the_kit_when_you_move_on_or_swap_a_hit(self):
        self.turn_on()
        engine = self.app.live_engine
        before = len(engine.loads)
        self.press('n')
        self.settle()
        self.assertTrue(wait(self.app, lambda: len(engine.loads) > before))
        self.assertEqual(engine.loads[-1], [slot['source'] for slot in self.app.km.cand['slots']])
        count = len(engine.loads)
        self.press('r')
        self.settle()
        self.assertTrue(wait(self.app, lambda: len(engine.loads) > count))
        self.assertEqual(engine.loads[-1], [slot['source'] for slot in self.app.km.cand['slots']])


class ExportTests(Base):
    def test_exporting_builds_a_valid_program_folder_and_says_where(self):
        for _ in range(3):
            self.press('y')
        self.app.km_ui.export.invoke()
        self.assertTrue(wait(self.app, lambda: not self.app.km.exporting))
        out = self.libdir / 'programs' / 'KITS_001_003'
        self.assertTrue((out / 'KITS_001_003.PGM').is_file())
        self.assertTrue((self.libdir / 'programs' / 'CATALOGUE.csv').is_file())
        self.assertIn('Built 1 program', self.app.km_ui.status.cget('text'))
        self.assertIn(str(self.libdir / 'programs'), self.app.km_ui.status.cget('text'))
        self.assertEqual(len(list(out.glob('*.WAV'))), 24)

    def test_export_with_nothing_kept_is_not_offered(self):
        self.assertEqual(self.app.km_ui.export.cget('state'), 'disabled')
        self.app.km_export()
        self.assertFalse(self.app.km.exporting)
        self.assertFalse((self.libdir / 'programs').exists())


if __name__ == '__main__':
    unittest.main()
