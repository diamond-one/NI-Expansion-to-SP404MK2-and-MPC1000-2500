"""Input-only MIDI pad audition and local device settings.

MIDI support is optional and imported only when ports are listed or opened.
Callbacks run on the MIDI backend thread: callers should queue UI updates.
No output ports are opened and no MIDI messages are sent to hardware.

Public channels are 1-based, while incoming Mido message channels are 0-based.
SP MIDI mode A uses notes 36..51 in bottom-up physical order: note 36 is
SP pad 13, note 40 is SP pad 9, note 44 is SP pad 5, and note 48 is SP pad 1.
"""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading


SETTINGS_VERSION = 1
DEFAULT_SETTINGS = {
    "schema": SETTINGS_VERSION,
    "audioDeviceName": "",
    "hostApiName": "",
    "midiInputName": "",
    "channel": None,
    "noteOffset": 0,
    "profile": "sp404_a",
    "mapping": {},
    "cliPath": "",
    "port": "",
}
PROFILES = frozenset({"sp404_a", "sp404_b", "linear", "custom", "mpc1000", "mpc_midi"})
MPC_MIDI_NOTES = tuple(range(36, 100))
MPC_A_NOTES = (37, 36, 42, 82, 40, 38, 46, 44, 48, 47, 45, 43, 49, 55, 51, 53)
MPC_PAD_NOTES = MPC_A_NOTES + (
    54, 69, 81, 80, 65, 66, 76, 77, 56, 62, 63, 64, 73, 74, 71, 39,
    52, 57, 58, 59, 60, 61, 67, 68, 70, 72, 75, 78, 79, 35, 41, 50,
    83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98)


def normalise_mpc_notes(notes):
    if notes is None:
        return list(MPC_PAD_NOTES)
    if not isinstance(notes, (list, tuple)) or len(notes) != 64:
        raise ValueError('MPC program mapping must contain 64 pad notes.')
    result = [_integer(note, 'MPC pad note', 0, 127) for note in notes]
    if len(set(result)) != 64:
        raise ValueError('MPC pad notes must be unique.')
    return result


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a whole number from {minimum} to {maximum}.")
    try:
        converted = int(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{name} must be a whole number from {minimum} to {maximum}.") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} must be a whole number from {minimum} to {maximum}.")
    if not minimum <= converted <= maximum:
        raise ValueError(f"{name} must be a whole number from {minimum} to {maximum}.")
    return converted


def _channel(value):
    if value is None or (isinstance(value, str) and value.strip().casefold() in {"any", "", "*"}):
        return None
    return _integer(value, "MIDI channel", 1, 16)


def _mapping_key(value):
    if isinstance(value, tuple) and len(value) == 2:
        channel, note = value
    elif isinstance(value, int) and not isinstance(value, bool):
        channel, note = None, value
    elif isinstance(value, str):
        pieces = value.split(":")
        if len(pieces) == 1:
            channel, note = None, pieces[0]
        elif len(pieces) == 2:
            channel, note = pieces
        else:
            raise ValueError("A MIDI mapping key must contain channel:note or just a note number.")
    else:
        raise ValueError("A MIDI mapping key must contain a (channel, note) pair or a note number.")
    channel = _channel(channel)
    note = _integer(note, "MIDI note", 0, 127)
    return f"{'*' if channel is None else channel}:{note}"


def normalise_mapping(mapping):
    """Return a JSON-safe channel:note -> bottom-up logical pad mapping.

    Examples: {(1, 48): 12}, {48: 12}, or {"*:48": 12}. A wildcard channel
    follows the same physical pads across SP mode A's bank channels.
    """
    if mapping is None:
        return {}
    if not isinstance(mapping, dict):
        raise ValueError("The learned MIDI mapping must be an object.")
    return {_mapping_key(key): _integer(pad, "Logical pad", 0, 15)
            for key, pad in mapping.items()}


def _normalise_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("Device settings must be an object.")
    result = deepcopy(DEFAULT_SETTINGS)
    result.update(deepcopy(settings))
    version = _integer(result.get("schema", SETTINGS_VERSION), "Settings version", 1, 1_000_000)
    if version > SETTINGS_VERSION:
        raise ValueError("Device settings were created by a newer CrateBuilder version.")
    result["schema"] = SETTINGS_VERSION
    result["channel"] = _channel(result["channel"])
    result["noteOffset"] = _integer(result["noteOffset"], "Note offset", -127, 127)
    result["mapping"] = normalise_mapping(result["mapping"])
    result['mpcPadNotes'] = normalise_mpc_notes(result.get('mpcPadNotes'))
    if result["profile"] not in PROFILES:
        raise ValueError("Unknown MIDI pad profile.")
    for field in ("audioDeviceName", "hostApiName", "midiInputName", "cliPath"):
        if result[field] is None:
            result[field] = ""
        if not isinstance(result[field], str):
            raise ValueError(f"{field} must be a device name or path.")
    # Additional settings are retained, but must still be valid portable JSON.
    json.dumps(result, allow_nan=False)
    return result


def load_settings(path):
    """Read device choices; missing/broken settings do not stop the application.

    A broken file is left untouched. A settingsError string lets the interface
    explain recovery if desired. Optional MIDI dependencies are never imported.
    """
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return _normalise_settings(value)
    except FileNotFoundError:
        return deepcopy(DEFAULT_SETTINGS)
    except (OSError, ValueError, TypeError) as exc:
        result = deepcopy(DEFAULT_SETTINGS)
        result["settingsError"] = f"Saved device settings could not be read: {exc}"
        return result


read_settings = load_settings


def save_settings(path, settings):
    """Atomically replace one local settings file, preserving the previous file on failure."""
    value = _normalise_settings(settings)
    value.pop("settingsError", None)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    target = Path(path).expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         prefix=f".{target.name}.", suffix=".tmp",
                                         dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return value


def _load_backend():
    try:
        mido = importlib.import_module("mido")
        return mido.Backend("mido.backends.rtmidi")
    except ImportError as exc:
        raise RuntimeError("Live MIDI support is unavailable in this installation (mido/python-rtmidi).") from exc


def list_input_ports():
    """List MIDI inputs only; never opens a MIDI output or virtual port."""
    try:
        return list(_load_backend().get_input_names())
    except (ImportError, OSError, RuntimeError) as exc:
        raise RuntimeError(f"Could not list MIDI inputs: {exc}") from exc


list_midi_inputs = list_input_ports


class MIDIController:
    """Route physical MIDI pad hits to the app's currently previewed kit.

    on_pad(logical_index, velocity) receives indices 0..15 and velocity 1..127.
    on_event(event_dict) reports connection, learning and note information.
    on_error(message) receives callback failures without killing the MIDI thread.
    channel=None accepts SP bank channels 1..10; explicit channels 1..16 work
    for other controllers. Clock, CC, note-off and zero-velocity notes are ignored.
    """
    def __init__(self, input_name, on_pad, on_event=lambda event: None,
                 on_error=lambda message: None, channel=None, note_offset=0,
                 profile="sp404_a", mapping=None, mpc_notes=None):
        self.input_name = str(input_name or "")
        self.on_pad, self.on_event, self.on_error = on_pad, on_event, on_error
        self.channel = _channel(channel)
        self.note_offset = _integer(note_offset, "Note offset", -127, 127)
        if profile not in PROFILES:
            raise ValueError("Unknown MIDI pad profile.")
        self.profile = profile
        self._mpc_notes = tuple(normalise_mpc_notes(mpc_notes))
        self._mpc_bank = 0
        self._mapping = normalise_mapping(mapping)
        self._port = None
        self._active = False
        self._generation = 0
        self._learn_index = None
        self._learn_any_channel = False
        self._lock = threading.RLock()

    @property
    def connected(self):
        with self._lock:
            return self._active and self._port is not None and not getattr(self._port, "closed", False)

    @property
    def mapping(self):
        with self._lock:
            return dict(self._mapping)

    @mapping.setter
    def mapping(self, value):
        value = normalise_mapping(value)
        with self._lock:
            self._mapping = value

    def set_mapping(self, value):
        self.mapping = value
        return self.mapping

    def set_mpc_bank(self, bank):
        bank = _integer(bank, 'MPC bank', 0, 3)
        with self._lock:
            # Hardware banks are input aliases for the visible sixteen pads.
            # Never reject a physical bank just because the editor bank differs.
            self._mpc_bank = bank

    @property
    def learning(self):
        with self._lock:
            return self._learn_index

    def _error(self, message):
        try:
            self.on_error(str(message))
        except Exception:
            # User callback failures must not terminate RtMidi's native thread.
            pass

    def _event(self, event):
        try:
            self.on_event(event)
        except Exception as exc:
            self._error(f"MIDI event callback failed: {exc}")

    def connect(self):
        if not self.input_name:
            raise ValueError("Choose a MIDI input first.")
        with self._lock:
            if self._active:
                return self
            self._generation += 1
            generation = self._generation
            self._active = True
        try:
            backend = _load_backend()
            port = backend.open_input(self.input_name,
                                      callback=lambda message: self._receive(message, generation))
        except Exception as exc:
            with self._lock:
                if generation == self._generation:
                    self._active = False
            raise RuntimeError(f"Could not open MIDI input '{self.input_name}': {exc}") from exc
        with self._lock:
            abandoned = not self._active or generation != self._generation
            if not abandoned:
                self._port = port
        if abandoned:
            port.close()
        else:
            self._event({"type": "connected", "input": self.input_name})
        return self

    start = connect

    def disconnect(self):
        with self._lock:
            port, was_active = self._port, self._active
            self._active = False
            self._port = None
            self._generation += 1
            self._learn_index = None
        if port is not None:
            try:
                port.close()
            except Exception as exc:
                self._error(f"Could not close MIDI input: {exc}")
        if was_active or port is not None:
            self._event({"type": "disconnected", "input": self.input_name})

    stop = disconnect

    def learn(self, index, any_channel=None):
        """Consume the next pad hit as a binding, without playing a sample.

        In the default any-bank setting the learned binding accepts the same
        note across bank channels 1..10. An explicitly selected channel learns
        only that channel unless any_channel is requested.
        """
        index = _integer(index, "Logical pad", 0, 15)
        with self._lock:
            self._learn_index = index
            self._learn_any_channel = self.channel is None if any_channel is None else bool(any_channel)
        self._event({"type": "learning", "pad": index})

    def cancel_learn(self):
        with self._lock:
            index = self._learn_index
            self._learn_index = None
        if index is not None:
            self._event({"type": "learn_cancelled", "pad": index})

    def _receive(self, message, generation):
        if getattr(message, "type", None) != "note_on":
            return
        try:
            velocity = _integer(getattr(message, "velocity", 0), "Velocity", 0, 127)
            if velocity == 0:
                return
            channel = _integer(getattr(message, "channel", -1), "Incoming MIDI channel", 0, 15) + 1
            note = _integer(getattr(message, "note", -1), "MIDI note", 0, 127)
            with self._lock:
                if not self._active or generation != self._generation:
                    return
                if self.channel is None:
                    if self.profile.startswith("sp404") and not 1 <= channel <= 10:
                        return
                elif self.channel != channel:
                    return
                if self._learn_index is not None:
                    pad = self._learn_index
                    key = f"{'*' if self._learn_any_channel else channel}:{note}"
                    self._mapping = {existing: value for existing, value in self._mapping.items()
                                     if value != pad}
                    self._mapping[key] = pad
                    self._learn_index = None
                    event = {"type": "learned", "pad": pad, "channel": channel,
                             "note": note, "mapping": dict(self._mapping)}
                    learned = True
                else:
                    pad = self._mapping.get(f"{channel}:{note}", self._mapping.get(f"*:{note}"))
                    if pad is None and self.profile != "custom":
                        if self.profile in {'mpc1000', 'mpc_midi'}:
                            notes = MPC_MIDI_NOTES if self.profile == 'mpc_midi' else self._mpc_notes
                            default_pad = notes.index(note - self.note_offset) % 16 if note - self.note_offset in notes else -1
                        else:
                            default_pad = note - 36 - self.note_offset
                        if 0 <= default_pad <= 15:
                            pad = default_pad
                    event = {"type": "note" if pad is not None else "unmapped", "pad": pad,
                             "channel": channel, "note": note, "velocity": velocity}
                    learned = False
            self._event(event)
            if not learned and pad is not None:
                try:
                    self.on_pad(pad, velocity)
                except Exception as exc:
                    self._error(f"Could not play MIDI pad: {exc}")
        except Exception as exc:
            self._error(f"Could not handle MIDI input: {exc}")
