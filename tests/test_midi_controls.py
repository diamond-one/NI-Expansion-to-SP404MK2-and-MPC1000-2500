"""MIDI learn for app actions: the controller (no hardware) and the Make kits tab driven through it."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_kitmaker_ui import Base, FakeEngine, wait
from test_live_midi import FakeBackend, message
from ni_to_sp import kitmaker_ui as kmui
from ni_to_sp.live_midi import MIDIController, load_settings, normalise_controls, save_settings


def cc(number, value=127, channel=0):
    return SimpleNamespace(type='control_change', channel=channel, control=number, value=value)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        patcher = patch('ni_to_sp.live_midi._load_backend', return_value=self.backend)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.hits, self.events = [], []

    def make(self, **kwargs):
        controller = MIDIController('Keyboard', lambda pad, velocity: self.hits.append((pad, velocity)),
                                    self.events.append, lambda text: self.events.append(('error', text)), **kwargs)
        self.addCleanup(controller.disconnect)
        controller.connect()
        return controller, self.backend.inputs[-1]

    def test_the_next_pad_hit_is_bound_to_the_action_and_does_not_play(self):
        controller, port = self.make()
        controller.learn_control('keep')
        self.assertEqual(controller.learning_control, 'keep')
        port.emit(message(note=49, channel=9))
        self.assertEqual(self.hits, [])
        self.assertIsNone(controller.learning_control)
        self.assertEqual(controller.controls, {'keep': 'note:*:49'})
        self.assertEqual(self.events[-1]['type'], 'control_learned')
        self.assertEqual(self.events[-1]['controls'], {'keep': 'note:*:49'})

    def test_a_learned_note_fires_the_action_instead_of_a_pad_every_time(self):
        controller, port = self.make(profile='mpc1000', controls={'keep': 'note:*:49'})
        port.emit(message(note=49, channel=0))
        port.emit(message(note=49, channel=3))                      # any channel, like pads across banks
        self.assertEqual(self.hits, [])
        self.assertEqual([e['action'] for e in self.events if e.get('type') == 'control'], ['keep', 'keep'])
        port.emit(message(note=37))                                  # an unlearned pad still plays
        self.assertEqual(len(self.hits), 1)

    def test_one_action_has_one_input_and_one_input_has_one_action(self):
        controller, port = self.make()
        controller.learn_control('keep')
        port.emit(message(note=40))
        controller.learn_control('keep')                             # relearn: the old note is released
        port.emit(message(note=41))
        self.assertEqual(controller.controls, {'keep': 'note:*:41'})
        controller.learn_control('skip')
        port.emit(message(note=41))                                  # steal the note: keep loses it
        self.assertEqual(controller.controls, {'skip': 'note:*:41'})

    def test_cc_buttons_work_once_per_press_and_ignore_release_and_held_knobs(self):
        controller, port = self.make(controls={'hear': 'cc:*:20'})
        port.emit(cc(20, 127))
        port.emit(cc(20, 127))                                       # still held / knob jitter
        port.emit(cc(20, 0))                                         # release re-arms
        port.emit(cc(20, 100))
        port.emit(cc(21, 127))                                       # some other control: nothing
        self.assertEqual(len([e for e in self.events if e.get('type') == 'control']), 2)

    def test_a_cc_can_be_learned_and_stays_channel_specific_when_a_channel_is_chosen(self):
        controller, port = self.make(channel=3)
        controller.learn_control('undo')
        port.emit(cc(7, 127, channel=2))
        self.assertEqual(controller.controls, {'undo': 'cc:3:7'})
        port.emit(cc(7, 0, channel=2))
        port.emit(cc(7, 127, channel=5))                             # another channel is filtered out
        self.assertEqual(len([e for e in self.events if e.get('type') == 'control']), 0)

    def test_pad_learn_and_control_learn_do_not_run_together(self):
        controller, port = self.make(controls={'keep': 'note:*:49'})
        controller.learn_control('skip')
        controller.learn(3)
        self.assertIsNone(controller.learning_control)
        port.emit(message(note=49))                                  # pad learning wins: no control fires
        self.assertEqual(controller.mapping, {'*:49': 3})
        self.assertFalse([e for e in self.events if e.get('type') == 'control'])

    def test_cancel_and_clear(self):
        controller, port = self.make()
        controller.learn_control('keep')
        controller.cancel_learn_control()
        self.assertIsNone(controller.learning_control)
        self.assertEqual(self.events[-1]['type'], 'learn_control_cancelled')
        controller.set_controls({'keep': 'note:*:49'})
        controller.set_controls({})
        port.emit(message(note=49, channel=0))
        self.assertFalse([e for e in self.events if e.get('type') == 'control'])

    def test_settings_validate_and_roundtrip_the_controls(self):
        self.assertEqual(normalise_controls({'keep': 'note:*:49', 'hear': 'cc:16:1'}), {'keep': 'note:*:49', 'hear': 'cc:16:1'})
        for bad in ({'keep': 'note:17:49'}, {'keep': 'note:*:200'}, {'keep': 'pad:*:1'}, {'': 'note:*:1'},
                    {'a': 'note:*:1', 'b': 'note:*:1'}, 'keep'):
            with self.assertRaises(ValueError, msg=bad):
                normalise_controls(bad)
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'c.json'
            save_settings(path, {'controls': {'keep': 'note:*:49'}})
            self.assertEqual(load_settings(path)['controls'], {'keep': 'note:*:49'})
        self.assertEqual(load_settings(Path('missing.json'))['controls'], {})


class TabTests(Base):
    """The Make kits tab with a real controller on a fake MIDI port."""

    def setUp(self):
        super().setUp()
        self.backend = FakeBackend()
        for target, value in (('ni_to_sp.live_midi._load_backend', lambda: self.backend),
                              ('ni_to_sp.live_controls.LiveSampler', FakeEngine),
                              ('ni_to_sp.live_controls.list_audio_outputs', lambda: [])):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        app = self.app
        app.connection_settings.update(midiInputName='Keyboard', profile='mpc1000')
        app.audio_device = lambda settings, outputs: None
        app.player.set_output_device = lambda device: None

    def connect(self):
        self.app.toggle_live_preview()
        self.assertTrue(wait(self.app, lambda: self.app.midi_controller is not None and not self.app._live_connecting))
        return self.backend.inputs[-1]

    def test_the_button_opens_a_dialog_that_asks_for_midi_preview_first(self):
        self.app.km_ui.midi.invoke()
        self.app.update()
        learn = self.app._km_midi_rows['keep'][1]
        self.assertEqual(learn.cget('state'), 'disabled')
        self.assertIn('off', self.app._km_midi_note.cget('text'))
        self.app._km_midi_on.invoke()
        self.assertTrue(wait(self.app, lambda: self.app.km_midi_ready()))
        self.assertTrue(wait(self.app, lambda: learn.cget('state') == 'normal'))
        self.assertIn('Click Learn', self.app._km_midi_note.cget('text'))

    def test_learning_a_pad_for_keep_then_hitting_it_keeps_the_kit(self):
        port = self.connect()
        self.app.km_ui.midi.invoke()
        self.app.update()
        self.app._km_midi_rows['keep'][1].invoke()
        self.assertEqual(self.app.midi_controller.learning_control, 'keep')
        self.assertIn('Waiting', self.app._km_midi_rows['keep'][0].cget('text'))
        port.emit(message(note=60, channel=0))
        self.assertTrue(wait(self.app, lambda: 'keep' in self.app.connection_settings.get('controls', {})))
        self.assertIn('Note 60', self.app._km_midi_rows['keep'][0].cget('text'))
        saved = json.loads((self.app.data_dir / 'connections.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['controls'], {'keep': 'note:*:60'})
        before = len(self.app.km.library.kits())
        port.emit(message(note=60, channel=0))
        self.assertTrue(wait(self.app, lambda: len(self.app.km.library.kits()) == before + 1))
        self.assertTrue(self.app.km_ui.saved.winfo_ismapped() or self.app._km_badge)

    def test_each_action_runs_its_own_key(self):
        port = self.connect()
        self.app.connection_settings['controls'] = controls = {
            'skip': 'note:*:61', 'pad_next': 'note:*:62', 'pad_prev': 'note:*:63', 'hear': 'note:*:64'}
        self.app.midi_controller.set_controls(controls)
        port.emit(message(note=62))
        self.assertTrue(wait(self.app, lambda: self.app.km.selected == 1))
        port.emit(message(note=63))
        port.emit(message(note=63))
        self.assertTrue(wait(self.app, lambda: self.app.km.selected == 7))
        port.emit(message(note=64))
        self.assertTrue(wait(self.app, lambda: self.app.km.last_played is not None))
        first = self.app.km.cand['id']
        port.emit(message(note=61))
        self.assertTrue(wait(self.app, lambda: self.app.km.cand['id'] != first))
        self.assertEqual(self.app.km.library.kits(), [])             # skipped, not kept

    def test_hitting_a_hardware_pad_selects_that_pad_so_swap_needs_no_keyboard(self):
        from ni_to_sp.live_midi import MPC_A_NOTES
        port = self.connect()
        app = self.app
        app.connection_settings['controls'] = controls = {'swap': 'note:*:90'}
        app.midi_controller.set_controls(controls)
        self.assertEqual(app.km.selected, 0)
        port.emit(message(note=MPC_A_NOTES[5]))                      # hardware pad 6
        self.assertTrue(wait(app, lambda: app.km.selected == 5))
        self.assertEqual(app.km_ui.pads[5]._state['border_color'], '#FFFFFF')
        before = app.km.cand['slots'][5]['source']
        port.emit(message(note=90))                                  # the pad taught as Swap
        self.assertTrue(wait(app, lambda: app.km.cand['slots'][5]['source'] != before))
        self.assertEqual(app.km.selected, 5)                         # swapping did not move the selection
        port.emit(message(note=MPC_A_NOTES[12]))                     # pads 9-16 are not on this screen
        wait(app, lambda: False, .3)
        self.assertEqual(app.km.selected, 5)

    def test_a_hardware_pad_does_not_change_the_selection_on_other_tabs(self):
        from ni_to_sp.live_midi import MPC_A_NOTES
        port = self.connect()
        self.app.tabs.set('Play & export')
        self.app.update()
        port.emit(message(note=MPC_A_NOTES[3]))
        wait(self.app, lambda: False, .4)
        self.assertEqual(self.app.km.selected, 0)

    def test_controls_are_ignored_on_the_other_tabs(self):
        port = self.connect()
        self.app.connection_settings['controls'] = {'keep': 'note:*:60'}
        self.app.midi_controller.set_controls({'keep': 'note:*:60'})
        self.app.tabs.set('Play & export')
        self.app.update()
        port.emit(message(note=60))
        wait(self.app, lambda: False, .5)
        self.assertEqual(self.app.km.library.kits(), [])

    def test_clear_removes_the_binding_from_the_controller_and_the_file(self):
        port = self.connect()
        self.app.km_set_controls({'keep': 'note:*:60'})
        self.app.km_set_controls({})
        saved = json.loads((self.app.data_dir / 'connections.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['controls'], {})
        port.emit(message(note=60))
        wait(self.app, lambda: False, .5)
        self.assertEqual(self.app.km.library.kits(), [])

    def test_the_saved_controls_come_back_with_the_next_connection(self):
        self.app.connection_settings['controls'] = {'keep': 'note:*:60'}
        port = self.connect()
        self.assertEqual(self.app.midi_controller.controls, {'keep': 'note:*:60'})

    def test_describe(self):
        self.assertEqual(kmui.describe_control('note:*:49'), 'Note 49')
        self.assertEqual(kmui.describe_control('cc:3:20'), 'CC 20 · channel 3')


if __name__ == '__main__':
    unittest.main()
