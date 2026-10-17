import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ni_to_sp.live_controls import LivePreviewMixin
from ni_to_sp.model import Session, Assignment


class Widget:
    def __init__(self):
        self.owner = threading.get_ident()
        self.values = {}

    def configure(self, **kwargs):
        if threading.get_ident() != self.owner:
            raise AssertionError("A worker touched the UI")
        self.values.update(kwargs)


class Harness(LivePreviewMixin):
    def __init__(self):
        self.session = Session([], [Assignment() for _ in range(64)], kit_size=16, target="sp404")
        self.bank = 0
        self.busy, self.closing, self._closed = False, False, False
        self.rank_serial = 42
        self.connection_settings = {"midiInputName": "", "audioDeviceName": "", "hostApiName": "", "mapping": {}}
        self.events = queue.Queue()
        self.live_button, self.preview_route_label = Widget(), Widget()
        self.statuses, self.saved, self.flashes, self.midi_events = [], [], [], []
        self.scheduled = {}
        self.player = SimpleNamespace(set_output_device=lambda device: setattr(self, "output_device", device))

    def set_status(self, status):
        if threading.get_ident() != self.live_button.owner:
            raise AssertionError("A worker changed UI status")
        self.statuses.append(status)

    def save_connections(self):
        self.saved.append(dict(self.connection_settings))

    def flash_pad(self, local):
        self.flashes.append(local)

    def handle_midi_event(self, event):
        self.midi_events.append(event)

    def after(self, delay, callback):
        token = object()
        self.scheduled[token] = callback
        return token

    def after_cancel(self, token):
        self.scheduled.pop(token, None)

    def run_scheduled(self):
        callbacks, self.scheduled = list(self.scheduled.values()), {}
        for callback in callbacks:
            callback()

    def audio_device(self, settings, outputs):
        for output in outputs:
            if output["name"] == settings["audioDeviceName"] and output["hostapi_name"] == settings["hostApiName"]:
                return output["index"]
        if settings.get("audioDeviceName"):
            raise ValueError("Saved audio output unavailable")
        return None

    def dispatch_until(self, predicate):
        for _ in range(30):
            if predicate():
                return
            event, payload = self.events.get(timeout=3)
            self.handle_live_result(event, payload)
        raise AssertionError("Expected preview state was not reached")

    def drain(self):
        while not self.events.empty():
            event, payload = self.events.get_nowait()
            self.handle_live_result(event, payload)


class Engine:
    def __init__(self, device, on_error):
        self.device, self.on_error = device, on_error
        self.running = False
        self.loaded_bank = None
        self.prepared = []
        self.triggers = []
        self.start_count = self.close_count = 0
        self.closed = threading.Event()
        self.prepare_entered = threading.Event()
        self.gates = {}
        self.fail_on = set()

    def prepare(self, session, bank):
        self.prepared.append(bank)
        call = len(self.prepared)
        self.prepare_entered.set()
        if call in self.gates:
            self.gates[call].wait(timeout=3)
        if call in self.fail_on:
            raise ValueError("Sample folder unavailable")
        self.loaded_bank = bank
        return {"loaded": 16, "notes": []}

    def start(self):
        self.running = True
        self.start_count += 1

    def trigger(self, local, velocity):
        self.triggers.append((local, velocity))

    def close(self):
        self.running = False
        self.close_count += 1
        self.closed.set()


class Controller:
    def __init__(self, name, on_pad, **kwargs):
        self.name, self.on_pad, self.options = name, on_pad, kwargs
        self.connected = False
        self.fail = False
        self.close_count = 0

    def connect(self):
        if self.fail:
            raise ValueError("MIDI port busy")
        self.connected = True

    def disconnect(self):
        self.close_count += 1
        self.connected = False


class LiveControlsTests(unittest.TestCase):
    def setUp(self):
        self.app = Harness()
        self.engines, self.controllers = [], []
        self.created = threading.Event()
        self.configure_engine = lambda engine: None
        self.configure_controller = lambda controller: None
        self.outputs = [
            {"index": 4, "name": "SP-404MKII", "hostapi_name": "MME"},
            {"index": 20, "name": "SP-404MKII", "hostapi_name": "Windows WASAPI"},
            {"index": 2, "name": "Computer speakers", "hostapi_name": "Windows WASAPI"},
        ]

        def create_engine(**kwargs):
            engine = Engine(**kwargs)
            self.configure_engine(engine)
            self.engines.append(engine)
            self.created.set()
            return engine

        def create_controller(*args, **kwargs):
            controller = Controller(*args, **kwargs)
            self.configure_controller(controller)
            self.controllers.append(controller)
            return controller

        patches = [patch("ni_to_sp.live_controls.LiveSampler", side_effect=create_engine),
                   patch("ni_to_sp.live_controls.MIDIController", side_effect=create_controller),
                   patch("ni_to_sp.live_controls.list_input_ports", return_value=["SP-404MKII-G0"]),
                   patch("ni_to_sp.live_controls.list_audio_outputs", return_value=self.outputs)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.close_preview)

    def close_preview(self):
        self.app.disable_live_preview(quiet=True)
        cleanup = self.app._live_cleanup_thread
        if cleanup:
            cleanup.join(timeout=3)

    def connect(self):
        self.app.enable_live_preview()
        self.app.dispatch_until(lambda: self.app.live_engine is not None)

    def test_first_on_selects_sp_wasapi_and_does_not_take_global_job_lock(self):
        self.app.busy = True
        self.connect()
        self.assertTrue(self.app.busy)
        self.assertEqual(self.app.rank_serial, 42)
        self.assertEqual(self.app.output_device, 20)
        self.assertEqual(self.app.connection_settings["midiInputName"], "SP-404MKII-G0")
        self.assertEqual(self.app.connection_settings["hostApiName"], "Windows WASAPI")
        self.assertEqual(self.app.live_button.values["text"], "MIDI preview: ON")
        self.assertEqual(self.app.live_button.values["state"], "normal")
        self.assertIn("SP-404MKII", self.app.preview_route_label.values["text"])

    def test_off_during_pending_audio_load_never_opens_midi_or_restarts_audio(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.configure_engine = lambda engine: engine.gates.update({1: gate})
        self.app.enable_live_preview()
        self.assertEqual(self.app.live_button.values["text"], "MIDI preview: starting…")
        self.assertTrue(self.created.wait(timeout=3))
        engine = self.engines[0]
        self.assertTrue(engine.prepare_entered.wait(timeout=3))
        self.app.busy = True
        self.app.toggle_live_preview()
        self.assertEqual(self.app.live_button.values["text"], "MIDI preview: OFF")
        self.assertFalse(self.app._live_requested)
        gate.set()
        self.assertTrue(engine.closed.wait(timeout=3))
        self.assertEqual(engine.start_count, 0)
        self.assertFalse(self.controllers)

    def test_off_closes_ready_resources_even_before_ready_event_is_dispatched(self):
        self.app.enable_live_preview()
        event, payload = self.app.events.get(timeout=3)
        self.assertEqual(event, "live_connected")
        engine = payload["resources"].engine
        self.assertTrue(engine.running)
        self.app.disable_live_preview()
        self.app.handle_live_result(event, payload)
        self.assertTrue(engine.closed.wait(timeout=3))
        self.assertIsNone(self.app.live_engine)
        self.assertEqual(self.app.live_button.values["text"], "MIDI preview: OFF")

    def test_missing_configured_midi_input_fails_off_without_fallback(self):
        self.app.connection_settings["midiInputName"] = "My disconnected controller"
        self.app.enable_live_preview()
        self.app.dispatch_until(lambda: not self.app._live_requested)
        self.assertFalse(self.engines)
        self.assertIn("saved MIDI input is unavailable", self.app.statuses[-1])
        self.assertEqual(self.app.live_button.values["text"], "MIDI preview: OFF")

    def test_missing_configured_audio_output_fails_off_without_fallback(self):
        self.app.connection_settings.update(midiInputName="SP-404MKII-G0", audioDeviceName="Disconnected output")
        self.app.enable_live_preview()
        self.app.dispatch_until(lambda: not self.app._live_requested)
        self.assertFalse(self.engines)
        self.assertIn("audio output unavailable", self.app.statuses[-1])

    def test_first_on_does_not_silently_choose_an_unrelated_midi_controller(self):
        with patch("ni_to_sp.live_controls.list_input_ports", return_value=["Other keyboard"]):
            self.app.enable_live_preview()
            self.app.dispatch_until(lambda: not self.app._live_requested)
        self.assertFalse(self.engines)
        self.assertIn("Choose your MIDI input in Connections", self.app.statuses[-1])

    def test_restart_closes_old_devices_and_ignores_a_late_failure(self):
        self.connect()
        old_engine, old_controller = self.app.live_engine, self.app.midi_controller
        old_token = self.app._live_generation
        self.app.connection_settings.update(audioDeviceName="Computer speakers", hostApiName="Windows WASAPI")
        self.app.enable_live_preview()
        self.app.dispatch_until(lambda: self.app.live_engine is not None)
        current = self.app.live_engine
        self.assertIsNot(current, old_engine)
        self.assertTrue(old_engine.closed.is_set())
        self.assertFalse(old_controller.connected)
        self.assertEqual(current.device, 2)
        self.app.handle_live_result("live_connect_failed", {"token": old_token, "error": "Late old failure"})
        self.assertIs(self.app.live_engine, current)
        self.assertTrue(current.running)
        self.assertEqual(self.app.live_button.values["text"], "MIDI preview: ON")

    def test_midi_connect_failure_closes_the_started_audio_engine(self):
        self.configure_controller = lambda controller: setattr(controller, "fail", True)
        self.app.enable_live_preview()
        self.app.dispatch_until(lambda: not self.app._live_requested)
        self.assertTrue(self.engines[0].closed.wait(timeout=3))
        self.assertFalse(self.engines[0].running)
        self.assertGreater(self.controllers[0].close_count, 0)
        self.assertIn("MIDI port busy", self.app.statuses[-1])

    def test_midi_hits_are_immediate_and_late_callbacks_after_off_are_ignored(self):
        self.connect()
        engine, controller = self.app.live_engine, self.app.midi_controller
        controller.on_pad(4, 103)
        self.assertEqual(engine.triggers, [(4, 103)])
        self.assertFalse(self.app.flashes)
        self.app.drain()
        self.assertEqual(self.app.flashes, [4])
        self.app.disable_live_preview()
        controller.on_pad(4, 90)
        self.assertEqual(engine.triggers, [(4, 103)])

    def test_bank_reload_while_another_job_is_busy_coalesces_to_latest_bank(self):
        self.connect()
        engine = self.app.live_engine
        gate = threading.Event()
        self.addCleanup(gate.set)
        engine.gates[2] = gate
        engine.prepare_entered.clear()
        self.app.busy = True
        self.app.bank = 2
        self.app.schedule_live_reload()
        self.app.run_scheduled()
        self.assertTrue(engine.prepare_entered.wait(timeout=3))
        self.assertTrue(self.app.busy)
        self.assertEqual(self.app.rank_serial, 42)
        self.app.bank = 3
        self.app.schedule_live_reload()
        self.assertFalse(self.app.scheduled)
        gate.set()
        self.app.dispatch_until(lambda: self.app._live_reload_inflight is None)
        self.assertTrue(self.app.scheduled)
        self.app.run_scheduled()
        self.app.dispatch_until(lambda: self.app._live_reload_inflight is None)
        self.assertEqual(engine.prepared, [0, 2, 3])
        self.assertEqual(engine.loaded_bank, 3)
        self.assertEqual(self.app.live_button.values["text"], "MIDI preview: ON")

    def test_midi_remains_playable_during_background_job(self):
        self.connect()
        self.app.busy = True
        self.app.midi_controller.on_pad(4, 110)
        self.assertEqual(self.app.live_engine.triggers, [(4, 110)])

    def test_audio_only_sampler_does_not_require_any_midi_input(self):
        with patch('ni_to_sp.live_controls.list_input_ports', return_value=[]):
            self.app.enable_live_preview(audio_only=True)
            self.app.dispatch_until(lambda: self.app.live_engine is not None)
        self.assertFalse(self.controllers)
        self.assertTrue(self.app.live_engine.running)
        self.assertEqual(self.app.live_button.values['text'], 'MIDI preview: OFF')

    def test_reload_failure_turns_off_and_does_not_create_retry_loop(self):
        self.connect()
        engine = self.app.live_engine
        engine.fail_on.add(2)
        self.app.bank = 1
        self.app.schedule_live_reload()
        self.app.run_scheduled()
        self.app.dispatch_until(lambda: not self.app._live_requested)
        self.assertTrue(engine.closed.wait(timeout=3))
        self.assertFalse(self.app.scheduled)
        self.app.schedule_live_reload()
        self.assertFalse(self.app.scheduled)
        self.assertIn("Sample folder unavailable", self.app.statuses[-1])

    def test_disconnected_event_stops_preview_and_late_learn_event_is_ignored(self):
        self.connect()
        token = self.app._live_generation
        self.app.handle_live_result("live_midi_event", {"token": token, "event": {"type": "disconnected"}})
        self.app.handle_live_result("live_midi_event", {"token": token, "event": {"type": "learned", "mapping": {"*:40": 4}}})
        self.assertFalse(self.app._live_requested)
        self.assertFalse(self.app.midi_events)
        self.assertEqual(self.app.live_button.values["text"], "MIDI preview: OFF")


if __name__ == "__main__":
    unittest.main()

