"""Input-only live MIDI behavior, tested without hardware or MIDI dependencies."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ni_to_sp.live_midi import (DEFAULT_SETTINGS, MIDIController, list_input_ports,
                                    list_midi_inputs, load_settings, normalise_mapping,
                                    read_settings, save_settings)


def message(note=36, velocity=100, channel=0, kind="note_on"):
    return SimpleNamespace(type=kind, channel=channel, note=note, velocity=velocity)


class FakePort:
    def __init__(self, callback):
        self.callback = callback
        self.closed = False

    def close(self):
        self.closed = True

    def emit(self, value):
        self.callback(value)


class FakeBackend:
    def __init__(self):
        self.inputs = []
        self.get_input_names = Mock(return_value=["SP-404MKII MIDI", "Keyboard"])
        self.open_input = Mock(side_effect=self._open_input)
        self.open_output = Mock(side_effect=AssertionError("Must never open MIDI output"))
        self.get_output_names = Mock(side_effect=AssertionError("Must never enumerate MIDI output"))

    def _open_input(self, name, callback):
        port = FakePort(callback)
        self.inputs.append(port)
        return port


class LiveMIDITests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        patcher = patch("ni_to_sp.live_midi._load_backend", return_value=self.backend)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.hits, self.events, self.errors = [], [], []

    def controller(self, **kwargs):
        controller = MIDIController("SP-404MKII MIDI", lambda pad, velocity: self.hits.append((pad, velocity)),
                                    self.events.append, self.errors.append, **kwargs)
        self.addCleanup(controller.disconnect)
        controller.connect()
        return controller, self.backend.inputs[-1]

    def test_port_inventory_and_connection_only_use_midi_inputs(self):
        self.assertEqual(list_input_ports(), ["SP-404MKII MIDI", "Keyboard"])
        self.assertEqual(list_midi_inputs(), list_input_ports())
        controller, port = self.controller()
        self.assertTrue(controller.connected)
        controller.start()
        self.assertEqual(self.backend.open_input.call_count, 1)
        self.backend.open_input.assert_called_with("SP-404MKII MIDI", callback=port.callback)
        self.backend.open_output.assert_not_called()
        self.backend.get_output_names.assert_not_called()
        controller.stop()
        self.assertTrue(port.closed)
        self.assertFalse(controller.connected)

    def test_mode_a_all_notes_follow_the_actual_bottom_up_sp_pad_layout(self):
        _, port = self.controller()
        for index in range(16):
            port.emit(message(note=36 + index, velocity=20 + index))
        self.assertEqual(self.hits, [(index, 20 + index) for index in range(16)])
        # SP pad 13 is logical 0; SP pad 9 (kick) is logical 4;
        # SP pad 5 is logical 8; SP pad 1 is logical 12.
        self.assertEqual([self.hits[index][0] for index in (0, 4, 8, 12)], [0, 4, 8, 12])
        self.assertEqual(len([event for event in self.events if event["type"] == "note"]), 16)

    def test_default_accepts_all_ten_bank_channels_but_not_chromatic_or_control_channels(self):
        _, port = self.controller()
        for channel in range(16):
            port.emit(message(channel=channel))
        self.assertEqual(self.hits, [(0, 100)] * 10)
        self.assertEqual([event["channel"] for event in self.events if event["type"] == "note"], list(range(1, 11)))

    def test_explicit_channel_is_one_based_and_can_select_channel_sixteen(self):
        _, port = self.controller(channel=16)
        port.emit(message(channel=0))
        port.emit(message(channel=14))
        port.emit(message(channel=15, note=40, velocity=64))
        self.assertEqual(self.hits, [(4, 64)])

    def test_clock_control_note_off_and_zero_velocity_are_ignored_without_event_floods(self):
        _, port = self.controller()
        initial_events = list(self.events)
        for kind in ("clock", "start", "stop", "continue", "active_sensing", "control_change", "note_off", "pitchwheel"):
            for _ in range(20):
                port.emit(message(kind=kind))
        port.emit(message(velocity=0))
        self.assertEqual(self.hits, [])
        self.assertEqual(self.events, initial_events)
        port.emit(message(note=35))  # EXT SOURCE is not one of the 16 pads.
        port.emit(message(note=52))
        self.assertEqual(self.hits, [])
        self.assertEqual([event["type"] for event in self.events[-2:]], ["unmapped", "unmapped"])

    def test_note_offset_selects_another_sixteen_note_window(self):
        _, port = self.controller(note_offset=16)
        port.emit(message(note=36))
        port.emit(message(note=52, velocity=80))
        port.emit(message(note=67, velocity=127))
        port.emit(message(note=68))
        self.assertEqual(self.hits, [(0, 80), (15, 127)])

    def test_custom_mapping_supports_specific_channels_and_wildcards_including_pad_zero(self):
        controller, port = self.controller(mapping={(1, 90): 0, "*:91": 15}, profile="custom")
        port.emit(message(note=90, channel=0))
        port.emit(message(note=90, channel=1))
        port.emit(message(note=91, channel=1))
        port.emit(message(note=36))
        self.assertEqual(self.hits, [(0, 100), (15, 100)])
        snapshot = controller.mapping
        snapshot["*:91"] = 3
        self.assertEqual(controller.mapping["*:91"], 15)
        controller.set_mapping({36: 7})
        port.emit(message(note=36))
        self.assertEqual(self.hits[-1], (7, 100))
        controller.mapping = {}
        port.emit(message(note=36))
        self.assertEqual(len(self.hits), 3)

    def test_channel_specific_mapping_takes_precedence_over_wildcard(self):
        _, port = self.controller(mapping={"*:36": 2, "1:36": 8})
        port.emit(message(channel=0))
        port.emit(message(channel=1))
        self.assertEqual(self.hits, [(8, 100), (2, 100)])

    def test_learning_consumes_hit_and_follows_same_pad_across_bank_channels(self):
        controller, port = self.controller()
        controller.learn(10)
        self.assertEqual(controller.learning, 10)
        port.emit(message(note=90, channel=10))  # Default filter keeps waiting.
        self.assertEqual(controller.learning, 10)
        port.emit(message(note=90, channel=1))
        self.assertEqual(self.hits, [])
        self.assertIsNone(controller.learning)
        self.assertEqual(controller.mapping, {"*:90": 10})
        self.assertEqual(self.events[-1], {"type": "learned", "pad": 10, "channel": 2,
                                          "note": 90, "mapping": {"*:90": 10}})
        port.emit(message(note=90, channel=4, velocity=81))
        self.assertEqual(self.hits, [(10, 81)])

    def test_learning_replaces_pad_binding_can_reassign_note_and_cancel(self):
        controller, port = self.controller(channel=2, mapping={"2:80": 5, "2:81": 6}, profile="custom")
        controller.learn(5)
        port.emit(message(note=81, channel=1))
        self.assertEqual(controller.mapping, {"2:81": 5})
        self.assertEqual(self.hits, [])
        controller.learn(7)
        controller.cancel_learn()
        self.assertIsNone(controller.learning)
        port.emit(message(note=81, channel=1, velocity=45))
        self.assertEqual(self.hits, [(5, 45)])
        self.assertEqual(controller.mapping, {"2:81": 5})

    def test_disconnect_and_reconnect_reject_late_callbacks_from_old_connection(self):
        controller, first_port = self.controller()
        controller.disconnect()
        first_port.emit(message())
        self.assertEqual(self.hits, [])
        controller.connect()
        second_port = self.backend.inputs[-1]
        first_port.emit(message(note=40))
        second_port.emit(message(note=48))
        self.assertEqual(self.hits, [(12, 100)])
        self.assertFalse(first_port is second_port)

    def test_callback_errors_are_reported_without_crashing_input_thread(self):
        controller, port = self.controller()
        controller.on_pad = Mock(side_effect=RuntimeError("Audio temporarily unavailable"))
        port.emit(message())
        self.assertTrue(any("Audio temporarily unavailable" in error for error in self.errors))
        controller.on_event = Mock(side_effect=RuntimeError("UI queue failure"))
        port.emit(message())
        self.assertTrue(any("UI queue failure" in error for error in self.errors))
        controller.on_error = Mock(side_effect=RuntimeError("Error queue failure"))
        port.emit(message())  # Even a broken error handler cannot kill the MIDI thread.

    def test_failed_connect_is_retryable_and_bad_messages_do_not_play(self):
        controller = MIDIController("SP-404MKII MIDI", self.hits.append)
        self.addCleanup(controller.disconnect)
        self.backend.open_input.side_effect = OSError("Port disconnected")
        with self.assertRaisesRegex(RuntimeError, "Port disconnected"):
            controller.connect()
        self.assertFalse(controller.connected)
        self.backend.open_input.side_effect = self.backend._open_input
        controller.on_error = self.errors.append
        controller.connect()
        self.backend.inputs[-1].emit(message(note=300))
        self.assertEqual(self.hits, [])
        self.assertTrue(self.errors)

    def test_constructor_validates_public_mapping_and_channel_ranges(self):
        for kwargs in ({"channel": 0}, {"channel": 17}, {"note_offset": 200},
                       {"mapping": {"17:36": 0}}, {"mapping": {"*:128": 0}},
                       {"mapping": {"*:36": 16}}, {"profile": "unknown"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                MIDIController("test", lambda pad, velocity: None, **kwargs)
        self.assertEqual(normalise_mapping({(None, 48): 12, "49": 13}), {"*:48": 12, "*:49": 13})


class LiveMIDISettingsTests(unittest.TestCase):
    def setUp(self):
        test_parent = Path(__file__).resolve().parents[2] / "test-temp"
        test_parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="midi-", dir=test_parent)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.assertTrue(self.work.is_relative_to(test_parent.resolve()))
        self.path = self.work / "config" / "devices.json"

    def test_settings_roundtrip_preserves_stable_names_and_learned_mapping(self):
        settings = {"audioDeviceName": "SP-404MKII", "hostApiName": "Windows WASAPI",
                    "midiInputName": "SP-404MKII MIDI", "channel": "any", "noteOffset": 0,
                    "mapping": {(1, 48): 12, "*:36": 0}, "cliPath": "C:/Apps/sparky.exe",
                    "port": "COM4", "anotherSetting": [1, "retained"]}
        saved = save_settings(self.path, settings)
        restored = load_settings(self.path)
        self.assertEqual(saved, restored)
        self.assertEqual(restored["mapping"], {"1:48": 12, "*:36": 0})
        self.assertIsNone(restored["channel"])
        self.assertEqual(restored["audioDeviceName"], "SP-404MKII")
        self.assertEqual(read_settings(self.path), restored)

    def test_missing_broken_and_future_settings_fall_back_without_touching_original(self):
        self.assertEqual(load_settings(self.path), DEFAULT_SETTINGS)
        self.path.parent.mkdir()
        for payload in ('{"broken":', '[1,2]', '{"schema":99}', '{"channel":0}'):
            self.path.write_text(payload, encoding="utf-8")
            recovered = load_settings(self.path)
            self.assertEqual(recovered["mapping"], {})
            self.assertIn("settingsError", recovered)
            self.assertEqual(self.path.read_text(encoding="utf-8"), payload)

    def test_atomic_replace_failure_preserves_previous_settings_and_cleans_scratch_file(self):
        save_settings(self.path, {"midiInputName": "Original"})
        before = self.path.read_bytes()
        with patch("ni_to_sp.live_midi.os.replace", side_effect=OSError("Disk unavailable")):
            with self.assertRaisesRegex(OSError, "Disk unavailable"):
                save_settings(self.path, {"midiInputName": "Replacement"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_settings_do_not_replace_previous_file(self):
        save_settings(self.path, {"midiInputName": "Original"})
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            save_settings(self.path, {"mapping": {"*:36": 99}})
        self.assertEqual(self.path.read_bytes(), before)

    def test_settings_and_controller_creation_do_not_import_optional_midi_dependencies(self):
        with patch("ni_to_sp.live_midi.importlib.import_module", side_effect=AssertionError("Eager MIDI import")):
            save_settings(self.path, {"midiInputName": "SP-404MKII"})
            self.assertEqual(load_settings(self.path)["midiInputName"], "SP-404MKII")
            controller = MIDIController("SP-404MKII", lambda pad, velocity: None)
            self.assertFalse(controller.connected)
        with patch("ni_to_sp.live_midi.importlib.import_module", side_effect=ModuleNotFoundError("mido")):
            with self.assertRaisesRegex(RuntimeError, "Live MIDI support"):
                list_input_ports()


if __name__ == "__main__":
    unittest.main()

