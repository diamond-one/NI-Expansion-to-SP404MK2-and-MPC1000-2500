"""Non-blocking desktop controls for the preloaded MIDI preview instrument.

Only public control methods and ``handle_live_result`` touch UI widgets. Audio
loads, device opening and cleanup run in their own workers, independently of
library/generation jobs. Pending resources remain tracked until accepted, so
turning preview off also closes a connection whose ready event is still queued.
"""
import copy
import gc
import threading

from .live_midi import MIDIController, list_input_ports
from .preview import LiveSampler, list_audio_outputs, preferred_audio_output


class _Cancelled(Exception):
    pass


class _Resources:
    def __init__(self):
        self.lock = threading.RLock()
        self.engine = None
        self.controller = None
        self.closed = False

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                if self.controller is not None:
                    self.controller.disconnect()
            except Exception:
                pass
            try:
                if self.engine is not None:
                    self.engine.close()
            except Exception:
                pass


def _snapshot(session):
    result = copy.copy(session)
    result.pads = copy.deepcopy(session.pads)
    # Analysis records are immutable during editing; catalogue updates replace
    # this list, retaining the matcher's prepared statistics for unchanged data.
    result.samples = session.samples
    for name in ("sources", "history", "scan_notes", "locked_banks"):
        if hasattr(session, name):
            setattr(result, name, list(getattr(session, name)))
    return result


def _running(engine):
    try:
        return engine is not None and engine.running
    except Exception:
        return False


class LivePreviewMixin:
    def _init_live_controls(self):
        if hasattr(self, "_live_generation"):
            return
        self._live_generation = 0
        self._live_requested = False
        self._live_connecting = False
        self._live_pending = {}
        self._live_resources = None
        self._live_cleanup_thread = None
        self._live_reload_inflight = None
        self._live_reload_serial = 0
        self._live_route = "SP pads → computer audio"
        self._live_reload_after = getattr(self, "_live_reload_after", None)
        self.live_engine = getattr(self, "live_engine", None)
        self.midi_controller = getattr(self, "midi_controller", None)
        self.live_signature = getattr(self, "live_signature", None)
        self.live_failed_signature = getattr(self, "live_failed_signature", None)

    def _live_current(self, token):
        return (token == self._live_generation and self._live_requested
                and not getattr(self, "closing", False) and not getattr(self, "_closed", False))

    def sync_live_controls(self):
        self._init_live_controls()
        if getattr(self, "_closed", False):
            return
        active = _running(self.live_engine)
        if self._live_requested and not self._live_connecting:
            connected = getattr(self, '_live_audio_only', False) or (self.midi_controller is not None and self.midi_controller.connected)
            if not active or not connected:
                self.disable_live_preview(quiet=True)
                return
        if self._live_requested and self._live_connecting:
            label, route = "MIDI preview: starting…", "Connecting audio and MIDI…"
        elif self._live_requested and active:
            label = "MIDI preview: OFF" if getattr(self, '_live_audio_only', False) else "MIDI preview: ON"
            route = "Loading updated pads…" if self._live_reload_inflight else self._live_route
        else:
            label, route = "MIDI preview: OFF", "Mouse / keyboard audition available · MIDI preview off"
        # Off must remain available while other app jobs are running.
        if getattr(self, "live_button", None) is not None:
            self.live_button.configure(text=label, state="normal")
            colors = getattr(self, "live_colors", None)
            if colors:
                highlighted = self._live_requested and not getattr(self, '_live_audio_only', False) and (active or self._live_connecting)
                color = colors["pending"] if self._live_connecting else colors["on"] if highlighted else colors["off"]
                self.live_button.configure(fg_color=color, text_color=colors["dark"] if highlighted else colors["text"])
        if getattr(self, "preview_route_label", None) is not None:
            self.preview_route_label.configure(text=route)

    def toggle_live_preview(self):
        self._init_live_controls()
        if self._live_requested or self.live_engine is not None:
            self.disable_live_preview()
        else:
            self.enable_live_preview()

    def disable_live_preview(self, quiet=False):
        self._init_live_controls()
        self._live_generation += 1
        self._live_requested = self._live_connecting = False
        self._live_reload_inflight = None
        if self._live_reload_after is not None:
            try:
                self.after_cancel(self._live_reload_after)
            except Exception:
                pass
            self._live_reload_after = None
        bundles = list(self._live_pending.values())
        self._live_pending.clear()
        if self._live_resources is not None:
            bundles.append(self._live_resources)
        elif self.live_engine is not None or self.midi_controller is not None:
            legacy = _Resources()
            legacy.engine, legacy.controller = self.live_engine, self.midi_controller
            bundles.append(legacy)
        previous_cleanup = self._live_cleanup_thread
        self._live_resources = None
        self.live_engine = self.midi_controller = None
        self.live_signature = self.live_failed_signature = None
        if bundles:
            def cleanup():
                if previous_cleanup is not None:
                    previous_cleanup.join()
                for bundle in bundles:
                    bundle.close()
            thread = threading.Thread(target=cleanup, daemon=True)
            self._live_cleanup_thread = thread
            thread.start()
        self.sync_live_controls()
        if not quiet and not getattr(self, "_closed", False):
            self.set_status("MIDI preview off.")

    def enable_live_preview(self, audio_only=False):
        self._init_live_controls()
        if getattr(self, "closing", False) or getattr(self, "_closed", False):
            return
        if not getattr(self, "session", None):
            self.set_status("Select a kit before turning MIDI preview on.")
            self.sync_live_controls()
            return
        gc.collect()
        self.disable_live_preview(quiet=True)
        self._live_audio_only = audio_only
        token = self._live_generation
        self._live_requested = self._live_connecting = True
        snapshot, bank = _snapshot(self.session), self.bank
        signature = self.live_bank_signature()
        settings = copy.deepcopy(self.connection_settings)
        resources = _Resources()
        self._live_pending[token] = resources
        previous_cleanup = self._live_cleanup_thread
        self.sync_live_controls()
        self.set_status("Connecting MIDI preview…")

        def check():
            if not self._live_current(token) or resources.closed:
                raise _Cancelled()

        def worker():
            try:
                if previous_cleanup is not None:
                    previous_cleanup.join()
                check()
                inputs, outputs = ([] if audio_only else list_input_ports()), list_audio_outputs()
                first_connection = not settings.get("midiInputName")
                input_name = settings.get("midiInputName")
                if not audio_only and input_name and input_name not in inputs:
                    raise ValueError("The saved MIDI input is unavailable. Reconnect it or choose another input in Settings.")
                if not audio_only and not input_name:
                    token_hint = "mpc" if settings.get("profile") == "mpc1000" else "404"
                    input_name = next((name for name in inputs if token_hint in name.casefold()), None)
                    if input_name is None:
                        raise ValueError("Choose your MIDI input in Connections. For MPC1000, select the computer's MIDI interface input.")
                    settings["midiInputName"] = input_name
                if first_connection and not settings.get("audioDeviceName"):
                    device = preferred_audio_output(outputs)
                    output = next((item for item in outputs if item["index"] == device), None)
                    if output:
                        settings.update(audioDeviceName=output["name"], hostApiName=output["hostapi_name"])
                else:
                    device = self.audio_device(settings, outputs)
                check()
                with resources.lock:
                    check()
                    engine = LiveSampler(device=device,
                        on_error=lambda message: self.events.put(("live_notice", {"token": token, "message": str(message), "source": "audio"})))
                    resources.engine = engine
                    check()
                report = engine.prepare(snapshot, bank)
                with resources.lock:
                    check()
                    engine.start()
                    check()

                def on_pad(local, velocity):
                    if (self._live_current(token) and not resources.closed
                            and self.loaded_pad_matches(local)):
                        engine.trigger(local, velocity)
                        self.events.put(("live_hit", {"token": token, "engine": engine, "pad": local}))

                if audio_only:
                    route = f"Mouse / keyboard → {settings.get('audioDeviceName') or 'Computer default'}"
                    self.events.put(("live_connected", {"token": token, "resources": resources,
                        "device": device, "settings": settings, "signature": signature, "report": report, "route": route}))
                    return
                with resources.lock:
                    check()
                    controller = MIDIController(input_name, on_pad,
                        on_event=lambda event: self.events.put(("live_midi_event", {"token": token, "event": event})),
                        on_error=lambda message: self.events.put(("live_notice", {"token": token, "message": str(message), "source": "midi"})),
                        channel=settings.get("channel"), note_offset=settings.get("noteOffset", 0),
                        profile=settings.get("profile", "sp404_a"), mapping=settings.get("mapping", {}))
                    resources.controller = controller
                    check()
                    controller.connect()
                    check()
                route = f"{input_name} → {settings.get('audioDeviceName') or 'Computer default'}"
                self.events.put(("live_connected", {"token": token, "resources": resources,
                    "device": device, "settings": settings, "signature": signature, "report": report, "route": route}))
            except _Cancelled:
                resources.close()
            except Exception as exc:
                resources.close()
                self.events.put(("live_connect_failed", {"token": token, "error": str(exc)}))
        threading.Thread(target=worker, daemon=True).start()

    def loaded_pad_matches(self, index):
        """Unchanged pads remain playable while a replacement is prepared."""
        loaded, desired = self.live_signature, self.live_bank_signature()
        if not loaded or not desired or not self.live_engine or self.live_engine.loaded_bank != self.bank:
            return False
        return loaded == desired or (loaded[1] == desired[1] and loaded[3][index] == desired[3][index])

    def live_bank_signature(self):
        if not getattr(self, "session", None):
            return None
        return (id(self.session), self.bank, self.session.library_revision,
                tuple(p.sample_id for p in self.session.pads[self.bank * 16:self.bank * 16 + 16]))

    def schedule_live_reload(self):
        self._init_live_controls()
        if (not self._live_requested or self._live_connecting or not _running(self.live_engine)
                or getattr(self, "closing", False) or getattr(self, "_closed", False)):
            return
        signature = self.live_bank_signature()
        if signature in (self.live_signature, self.live_failed_signature) or self._live_reload_inflight:
            return
        if self._live_reload_after is not None:
            self.after_cancel(self._live_reload_after)
        self._live_reload_after = self.after(80, self.reload_live_bank)

    def reload_live_bank(self):
        self._init_live_controls()
        self._live_reload_after = None
        if (not self._live_requested or not getattr(self, "session", None) or not _running(self.live_engine)
                or self._live_reload_inflight or getattr(self, "closing", False) or getattr(self, "_closed", False)):
            return
        token, engine = self._live_generation, self.live_engine
        self._live_reload_serial += 1
        serial = self._live_reload_serial
        self._live_reload_inflight = (token, serial)
        snapshot, bank, signature = _snapshot(self.session), self.bank, self.live_bank_signature()
        self.sync_live_controls()
        def worker():
            try:
                report = engine.update_bank(snapshot, bank) if hasattr(engine, 'update_bank') else engine.prepare(snapshot, bank)
                self.events.put(("live_reloaded", {"token": token, "serial": serial, "engine": engine,
                    "signature": signature, "report": report}))
            except Exception as exc:
                self.events.put(("live_reload_failed", {"token": token, "serial": serial, "engine": engine,
                    "signature": signature, "error": str(exc)}))
        threading.Thread(target=worker, daemon=True).start()

    def handle_live_result(self, event, payload):
        self._init_live_controls()
        if not event.startswith("live_"):
            return False
        token = payload.get("token")
        if event == "live_connected":
            resources = payload["resources"]
            self._live_pending.pop(token, None)
            if not self._live_current(token) or resources.closed:
                threading.Thread(target=resources.close, daemon=True).start()
                return True
            self._live_resources = resources
            self.live_engine, self.midi_controller = resources.engine, resources.controller
            self._live_connecting = False
            self.live_signature, self.live_failed_signature = payload["signature"], None
            self._live_route = payload["route"]
            try:
                self.player.set_output_device(payload["device"])
            except Exception as exc:
                self.disable_live_preview(quiet=True)
                self.set_status("MIDI preview off: " + str(exc))
                return True
            self.connection_settings.update({name: payload["settings"].get(name, "") for name in
                                             ("midiInputName", "audioDeviceName", "hostApiName")})
            try:
                self.save_connections()
                self.set_status("Mouse and keyboard sampler ready." if getattr(self, '_live_audio_only', False)
                                else "MIDI preview on. Hardware pads play the visible kit through the computer.")
            except OSError as exc:
                self.set_status("MIDI preview on; connection settings could not be saved: " + str(exc))
            self.sync_live_controls()
            self.schedule_live_reload()
            if hasattr(self, 'preview_bank_ready'):
                self.preview_bank_ready()
        elif event == "live_connect_failed":
            self._live_pending.pop(token, None)
            if self._live_current(token):
                self.disable_live_preview(quiet=True)
                self.set_status("MIDI preview off: " + payload["error"])
        elif not self._live_current(token):
            return True
        elif event in ("live_reloaded", "live_reload_failed"):
            if (self._live_reload_inflight != (token, payload["serial"])
                    or payload["engine"] is not self.live_engine):
                return True
            self._live_reload_inflight = None
            if event == "live_reload_failed":
                self.disable_live_preview(quiet=True)
                self.set_status("MIDI preview off: " + payload["error"])
            else:
                self.live_signature, self.live_failed_signature = payload["signature"], None
                self.sync_live_controls()
                self.schedule_live_reload()
                if hasattr(self, 'preview_bank_ready'):
                    self.preview_bank_ready()
        elif event == "live_hit":
            if payload["engine"] is self.live_engine and self.live_engine.loaded_bank == self.bank:
                self.flash_pad(payload["pad"])
        elif event == "live_midi_event":
            midi_event = payload["event"]
            if midi_event.get("type") == "disconnected":
                self.disable_live_preview(quiet=True)
                self.set_status("MIDI preview off: the MIDI input disconnected.")
            elif hasattr(self, "handle_midi_event"):
                self.handle_midi_event(midi_event)
        elif event == "live_notice":
            if not _running(self.live_engine) and not self._live_connecting:
                self.disable_live_preview(quiet=True)
                self.set_status("MIDI preview off: " + payload["message"])
            else:
                self.set_status("MIDI preview: " + payload["message"])
        self.sync_live_controls()
        return True
