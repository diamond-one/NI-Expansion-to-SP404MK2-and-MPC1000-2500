"""The Make kits tab: review ready-made drum kits fast, keep the good ones, pack them for the MPC1000.

Keys (on this tab): Space hear the beat, Y keep, N skip, 1-8 pick a pad, R swap that hit, U undo.
"""
import copy
from math import gcd
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from types import SimpleNamespace

import customtkinter as ctk
import numpy as np
from scipy.signal import resample_poly

from . import autofill as af
from . import desk_theme as T
from . import groove, kitlib, kitmaker, kitpack
from . import pad_look as L
from .direct import preview_session
from .library import discover_kits
from .pad_widget import PadButton

# pads as they sit on the MPC1000: pad 1 bottom-left, so the top row is pads 5-8
GRID = ((4, 5, 6, 7), (0, 1, 2, 3))
CACHE_LIMIT = 600
SAVED, SAVED_INK, SAVED_NEW = ('#1F7A44', '#1F7A44'), ('#FFFFFF', '#FFFFFF'), ('#3DDC84', '#3DDC84')   # white on green: 5.4:1
SAVED_SECONDS = 2.4
# what a MIDI pad or button can be taught to do on this tab: (action, label)
CONTROLS = (('hear', 'Hear beat / stop'), ('keep', 'Keep kit'), ('skip', 'Skip kit'), ('swap', 'Swap selected hit'),
            ('undo', 'Undo'), ('pad_next', 'Select next pad'), ('pad_prev', 'Select previous pad'))


def describe_control(key):
    kind, channel, number = key.split(':')
    where = '' if channel == '*' else f' · channel {channel}'
    return f"{'CC' if kind == 'cc' else 'Note'} {number}{where}"


def default_folder():
    return Path(os.environ.get('NI_KITMAKER_DIR') or Path.home() / 'Documents' / 'NI Kit Maker')


def build_page(app, page):
    """Create the tab's widgets on `page` and the Make kits state on the app."""
    ui = app.km_ui = SimpleNamespace()
    app.km = SimpleNamespace(started=False, library=None, sounds=None, order=[], pos=0, cand=None, selected=0,
                             seen=set(), history=[], cache={}, avoid={}, seed=0, tag='', filling=False,
                             swapping=False, events=queue.Queue(), cancel=threading.Event(), last_played=None,
                             pool_ready=False, total=0, exporting=False, live_key=None, live_session=None, sounding_until=0)
    page.grid_columnconfigure(0, weight=1)
    page.grid_rowconfigure(1, weight=1)

    strip = ctk.CTkFrame(page, fg_color='transparent')
    strip.grid(row=0, column=0, sticky='ew', pady=(2, 8))
    strip.grid_columnconfigure(0, weight=1)
    oled = ctk.CTkFrame(strip, fg_color=T.OLED, corner_radius=3)
    oled.grid(row=0, column=0, sticky='w')
    ui.title = ctk.CTkLabel(oled, text='Loading your kits…', font=T.font(20, True, mono=True), text_color=T.OLED_INK,
        fg_color=T.OLED, corner_radius=0, padx=14, anchor='w', height=28)
    ui.title.pack(anchor='w', pady=(7, 0))
    ui.detail = ctk.CTkLabel(oled, text='', font=T.font(11, mono=True), text_color=('#A9AFB6', '#A9AFB6'),
        fg_color=T.OLED, corner_radius=0, padx=14, anchor='w', height=18)
    ui.detail.pack(anchor='w', pady=(0, 7))
    ui.counter = T.label(strip, '', 13, T.MUTED, anchor='e', justify='right')
    ui.counter.grid(row=0, column=1, sticky='e', padx=(12, 4))
    # shown for a couple of seconds after Keep: the proof that the kit was saved, and where
    ui.saved = ctk.CTkLabel(strip, text='', font=T.font(15, True), text_color=SAVED_INK, fg_color=SAVED, corner_radius=3,
                            padx=16, height=46, justify='left')
    ui.saved.grid(row=0, column=0, sticky='e', padx=(0, 14))
    ui.saved.grid_remove()

    body = ctk.CTkFrame(page, fg_color='transparent')
    body.grid(row=1, column=0, sticky='nsew')
    body.grid_columnconfigure(0, weight=1)
    body.grid_rowconfigure(0, weight=1)
    grid = ctk.CTkFrame(body, fg_color='transparent')
    grid.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
    ui.pads = {}
    for r, row in enumerate(GRID):
        grid.grid_rowconfigure(r, weight=1, uniform='kmpads')
        for c, slot in enumerate(row):
            grid.grid_columnconfigure(c, weight=1, uniform='kmpads')
            pad = PadButton(grid, command=lambda n=slot: app.km_pad_click(n), width=120, height=90)
            pad.grid(row=r, column=c, sticky='nsew', padx=6, pady=6)
            ui.pads[slot] = pad

    side = ctk.CTkFrame(body, fg_color=T.PAPER, corner_radius=0, border_width=2, border_color=T.LINE, width=290)
    side.grid(row=0, column=1, sticky='ns')
    side.grid_propagate(False)
    side.grid_columnconfigure(0, weight=1)
    ui.pad_title = T.label(side, 'Pad 1 · KICK', 18, T.FOCUS, True, True, anchor='w')
    ui.pad_title.grid(row=0, column=0, sticky='ew', padx=12, pady=(12, 2))
    ui.pad_name = T.label(side, '', 15, T.INK, True, wraplength=262, justify='left', anchor='w')
    ui.pad_name.grid(row=1, column=0, sticky='ew', padx=12)
    ui.pad_source = T.label(side, '', 11, T.MUTED, wraplength=262, justify='left', anchor='w')
    ui.pad_source.grid(row=2, column=0, sticky='ew', padx=12, pady=(2, 10))
    ui.program = T.label(side, '', 13, T.INK, True, wraplength=262, justify='left', anchor='w')
    ui.program.grid(row=3, column=0, sticky='ew', padx=12, pady=(4, 2))
    meter = ctk.CTkFrame(side, fg_color='transparent')       # eight cells: one per kit of the current program
    meter.grid(row=4, column=0, sticky='ew', padx=12, pady=(2, 8))
    meter.grid_columnconfigure(tuple(range(8)), weight=1, uniform='meter')
    ui.meter = [ctk.CTkFrame(meter, fg_color=T.LINE, corner_radius=2, height=12) for _ in range(8)]
    for column, cell in enumerate(ui.meter):
        cell.grid(row=0, column=column, sticky='ew', padx=2)
    ui.recent = T.label(side, '', 11, T.MUTED, wraplength=262, justify='left', anchor='nw')
    ui.recent.grid(row=5, column=0, sticky='ew', padx=12)
    ui.help = T.label(side, 'Space  hear the beat\nY  keep      N  skip\n1-8  pick a pad    R  swap that hit\nU  undo',
                      12, T.MUTED, wraplength=262, justify='left', anchor='sw')
    ui.help.grid(row=6, column=0, sticky='sew', padx=12, pady=(14, 20))
    side.grid_rowconfigure(6, weight=1)

    keys = ctk.CTkFrame(page, fg_color='transparent')
    keys.grid(row=2, column=0, sticky='ew', pady=(10, 0))
    keys.grid_columnconfigure((0, 1, 2, 3, 4), weight=1, uniform='kmkeys')
    ui.hear = T.button(app, keys, 'Hear beat (Space)', app.km_hear, primary=True, height=46)
    ui.keep = T.button(app, keys, 'Keep (Y)', app.km_keep, height=46)
    ui.skip = T.button(app, keys, 'Skip (N)', app.km_skip, height=46)
    ui.swap = T.button(app, keys, 'Swap hit (R)', app.km_swap, height=46)
    ui.undo = T.button(app, keys, 'Undo (U)', app.km_undo, height=46)
    for column, key in enumerate((ui.hear, ui.keep, ui.skip, ui.swap, ui.undo)):
        key.grid(row=0, column=column, sticky='ew', padx=4)
        key._canvas.configure(takefocus=0)            # Space must stay "hear the beat", not "press the focused key"
        app.controls.remove(key)

    bottom = ctk.CTkFrame(page, fg_color=T.PAPER, corner_radius=0, border_width=2, border_color=T.LINE)
    bottom.grid(row=3, column=0, sticky='ew', pady=(10, 0))
    bottom.grid_columnconfigure(2, weight=1)
    ui.export = T.button(app, bottom, 'Export programs…', app.km_export, primary=True, width=170)
    ui.export.grid(row=0, column=0, padx=10, pady=10)
    ui.open = T.button(app, bottom, 'Open folder', app.km_open_folder, width=110)
    ui.open.grid(row=0, column=1, padx=(0, 10))
    ui.folder = T.label(bottom, '', 12, T.MUTED, anchor='w')
    ui.folder.grid(row=0, column=2, sticky='ew')
    ui.change = T.button(app, bottom, 'Change folder…', app.km_change_folder, width=140)
    ui.change.grid(row=0, column=4, padx=(0, 10))
    ui.midi = T.button(app, bottom, 'MIDI controls…', app.km_midi_dialog, width=140)
    ui.midi.grid(row=0, column=3, padx=10)
    for key in (ui.export, ui.open, ui.change, ui.midi):
        app.controls.remove(key)
    ui.status = T.label(page, '', 12, T.MUTED, anchor='w', wraplength=900, justify='left')
    ui.status.grid(row=4, column=0, sticky='ew', pady=(6, 0))

    app.bind('<KeyPress>', app.km_key, add='+')
    app.after(80, app.km_poll)
    app.after(600, app.km_start)


class KitMakerMixin:
    # ---- folders and persistence ---------------------------------------------------------------
    def km_folder(self):
        saved = self._studio_state.get('kitmaker_dir') if hasattr(self, '_studio_state') else None
        return Path(saved) if saved else default_folder()

    def km_library(self):
        if self.km.library is None:
            self.km.library = kitlib.KitLibrary(self.km_folder())
            self.km.sounds = af.SoundIndex(self.data_dir / 'sound-features.sqlite')
            self.km.seen = self.km_load_seen()
        return self.km.library

    def km_load_seen(self):
        import json
        try:
            return set(json.loads((self.km.library.root / 'seen.json').read_text(encoding='utf-8')))
        except (OSError, ValueError, TypeError):
            return set()

    def km_save_seen(self):
        import json
        try:
            (self.km.library.root / 'seen.json').write_text(json.dumps(sorted(self.km.seen)), encoding='utf-8')
        except OSError:
            pass

    # ---- MIDI controls: teach a pad or button to press the keys -------------------------------------
    def km_control(self, action):
        """Run what a learned MIDI pad or button stands for. Only the Make kits tab listens."""
        if self.tabs.get() != 'Make kits' or self.km.library is None:
            return
        run = {'hear': self.km_hear, 'keep': self.km_keep, 'skip': self.km_skip, 'swap': self.km_swap,
               'undo': self.km_undo,
               'pad_next': lambda: self.km_pad_click((self.km.selected + 1) % 8),
               'pad_prev': lambda: self.km_pad_click((self.km.selected - 1) % 8)}.get(action)
        if run is not None:
            run()

    def handle_midi_event(self, event):
        kind = event.get('type')
        if kind == 'control':
            self.km_control(event['action'])
        elif kind == 'control_learned':
            self.connection_settings['controls'] = event['controls']
            try:
                self.save_connections()
            except OSError as exc:
                self.set_status('Could not save the MIDI controls: ' + str(exc))
            label = dict(CONTROLS).get(event['action'], event['action'])
            self.km_status(f"Learned: {label} = {describe_control(event['controls'][event['action']])}")
            self.km_midi_refresh()
        elif kind in ('learning_control', 'learn_control_cancelled'):
            self.km_midi_refresh()
        elif kind == 'note' and event.get('pad') is not None and event['pad'] < 8 and self.tabs.get() == 'Make kits':
            self.km_select(event['pad'])                  # hit a pad, and that pad is the one Swap (or R) acts on
        else:
            super().handle_midi_event(event)

    def km_set_controls(self, controls):
        self.connection_settings['controls'] = controls
        if self.midi_controller is not None:
            self.midi_controller.set_controls(controls)
        try:
            self.save_connections()
        except OSError as exc:
            self.set_status('Could not save the MIDI controls: ' + str(exc))
        self.km_midi_refresh()

    def km_midi_ready(self):
        return bool(self.midi_controller is not None and self.midi_controller.connected)

    def km_midi_learn(self, action):
        controller = self.midi_controller
        if not self.km_midi_ready():
            return
        if controller.learning_control == action:
            controller.cancel_learn_control()
        else:
            controller.learn_control(action)
            self.km_status(f"Press the pad or button for \u201c{dict(CONTROLS)[action]}\u201d on your controller.")
        self.km_midi_refresh()

    def km_midi_dialog(self):
        existing = getattr(self, '_km_midi_window', None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        window = self._km_midi_window = ctk.CTkToplevel(self)
        window.title('MIDI controls')
        window.geometry('560x500')
        window.transient(self)
        window.configure(fg_color=T.WORKSPACE)
        window.grid_columnconfigure(0, weight=1)
        T.label(window, 'MIDI controls', 18, T.INK, True).grid(row=0, column=0, sticky='w', padx=18, pady=(16, 2))
        self._km_midi_note = T.label(window, '', 12, T.MUTED, wraplength=520, justify='left', anchor='w')
        self._km_midi_note.grid(row=1, column=0, sticky='ew', padx=18)
        rows = ctk.CTkFrame(window, fg_color=T.PAPER, corner_radius=0, border_width=2, border_color=T.LINE)
        rows.grid(row=2, column=0, sticky='ew', padx=18, pady=12)
        rows.grid_columnconfigure(1, weight=1)
        self._km_midi_rows = {}
        for r, (action, label) in enumerate(CONTROLS):
            T.label(rows, label, 13, T.INK, True).grid(row=r, column=0, sticky='w', padx=(12, 8), pady=6)
            value = T.label(rows, '', 12, T.MUTED, anchor='w')
            value.grid(row=r, column=1, sticky='ew')
            learn = T.button(self, rows, 'Learn', lambda a=action: self.km_midi_learn(a), width=84)
            learn.grid(row=r, column=2, padx=4, pady=4)
            clear = T.button(self, rows, 'Clear', lambda a=action: self.km_set_controls(
                {k: v for k, v in self.connection_settings.get('controls', {}).items() if k != a}), width=70)
            clear.grid(row=r, column=3, padx=(0, 8))
            self._km_midi_rows[action] = (value, learn, clear)
        bottom = ctk.CTkFrame(window, fg_color='transparent')
        bottom.grid(row=3, column=0, sticky='ew', padx=18)
        self._km_midi_on = T.button(self, bottom, 'Turn on MIDI preview', self.enable_live_preview, primary=True, width=190)
        self._km_midi_on.pack(side='left')
        self._km_midi_clear_all = T.button(self, bottom, 'Clear all', lambda: self.km_set_controls({}), width=96)
        self._km_midi_clear_all.pack(side='left', padx=8)
        T.button(self, bottom, 'Close', lambda: self._km_midi_close(window), width=84).pack(side='right')
        window.bind('<Escape>', lambda _e: self._km_midi_close(window))
        window.protocol('WM_DELETE_WINDOW', lambda: self._km_midi_close(window))
        self.km_midi_refresh()
        window.after(400, self._km_midi_poll)

    def _km_midi_close(self, window):
        if self.midi_controller is not None and self.midi_controller.learning_control:
            self.midi_controller.cancel_learn_control()
        window.destroy()

    def _km_midi_poll(self):
        window = getattr(self, '_km_midi_window', None)
        if self._closed or window is None or not window.winfo_exists():
            return
        self.km_midi_refresh()
        window.after(400, self._km_midi_poll)

    def km_midi_refresh(self):
        window = getattr(self, '_km_midi_window', None)
        if window is None or not window.winfo_exists():
            return
        ready = self.km_midi_ready()
        controls = self.connection_settings.get('controls', {})
        learning = self.midi_controller.learning_control if ready else None
        self._km_midi_note.configure(text=(
            'Click Learn, then hit a pad or press a button on your controller. A pad or button taught here no longer '
            'plays its sound. Works only on the Make kits tab.' if ready else
            'MIDI preview is off. Turn it on, then teach your controller.'))
        if ready and self._km_midi_on.winfo_manager():
            self._km_midi_on.pack_forget()
        elif not ready and not self._km_midi_on.winfo_manager():
            self._km_midi_on.pack(side='left', before=self._km_midi_clear_all)
        for action, (value, learn, clear) in self._km_midi_rows.items():
            value.configure(text='Waiting for a pad or button…' if learning == action else
                            describe_control(controls[action]) if action in controls else 'not set')
            learn.configure(text='Cancel' if learning == action else 'Learn', state='normal' if ready else 'disabled')
            clear.configure(state='normal' if action in controls else 'disabled')

    def km_status(self, text):
        self.km_ui.status.configure(text=text)

    # ---- loading the pool ----------------------------------------------------------------------
    def km_start(self, force=False):
        """Read the kits the first time the tab is shown. NI_TO_SP_KITMAKER_AUTOSTART=0 holds it back (tests)."""
        if self.km.started or self._closed or self.tabs.get() != 'Make kits':
            return
        if not force and os.environ.get('NI_TO_SP_KITMAKER_AUTOSTART') == '0':
            return
        self.km.started = True
        library = self.km_library()
        root = self.root_var.get() if hasattr(self, 'root_var') else 'C:/Users/Public/Documents'
        events, cancel = self.km.events, self.km.cancel
        cache = self.data_dir / 'kit-pool.sqlite'
        seen = set(self.km.seen)
        self.km_ui.folder.configure(text=str(library.root))
        self.km_status('Reading your NI kits…')

        def work():
            try:
                kits = discover_kits(root)
                pool = kitmaker.build_pool(kits, cache, cancel=cancel,
                                           progress=lambda text, value: events.put(('status', text)))
                events.put(('pool', kitmaker.order_pool(pool, seen=seen, seed=int(time_seed())), len(pool)))
            except Exception as exc:
                events.put(('error', f'Could not read kits: {exc}'))
        threading.Thread(target=work, daemon=True, name='kitmaker-pool').start()

    # ---- the event loop (workers never touch Tk) -----------------------------------------------
    def km_poll(self):
        if self._closed:
            return
        try:
            while True:
                kind, *rest = self.km.events.get_nowait()
                getattr(self, '_km_on_' + kind)(*rest)
        except queue.Empty:
            pass
        self.after(60, self.km_poll)

    def _km_on_status(self, text):
        self.km_status(text)

    def _km_on_error(self, text):
        self.km.filling = self.km.swapping = self.km.exporting = False
        self.km_status(text)
        self.km_refresh()

    def _km_on_pool(self, order, total):
        self.km.order, self.km.total, self.km.pos, self.km.pool_ready = order, len(order), 0, True
        self.km_status(f'{len(order)} kits ready to review. Press Space to hear one.' if order
                       else 'No more kits to review. Everything has been kept or skipped.')
        self.km_advance(0)

    def _km_on_prepared(self, cand_id, prepared, tag):
        cand = self.km.cand
        if cand is None or cand['id'] != cand_id:
            return
        for index, slot in enumerate(prepared['slots']):
            if slot['source'] and not cand['slots'][index]['source']:
                cand['slots'][index] = slot
        cand['filled'] = sum(bool(s['source']) for s in cand['slots'])
        self.km.tag, self.km.filling = tag, False
        self.km_refresh()

    def _km_on_swapped(self, cand_id, index, found, old):
        self.km.swapping = False
        cand = self.km.cand
        if cand is None or cand['id'] != cand_id:
            return
        if found is None:
            self.km_status('No other sound of that kind was found.')
        else:
            was = cand['slots'][index]['name']
            kitmaker.apply_hit(cand, index, found[0], found[1], 'swapped')
            self.km.avoid.setdefault((cand_id, index), set()).update({old, found[0]} - {None})
            self.km_status(f"Pad {index + 1} swapped: {was} → {found[1]}.")
            self.km_refresh()
            self.km_ui.pads[index].flash()
            self.after(160, lambda: None if self._closed else self.km_refresh())      # the flash is brief
            hit = self.km_hit(found[0])               # play just the new hit, in place of whatever was sounding
            if hit is not None:
                self.stop_audio()
                self.play_array(np.repeat(hit, 2, axis=1) if hit.shape[1] == 1 else hit, groove.RATE)
            return
        self.km_refresh()

    def _km_on_exported(self, programs, folder):
        self.km.exporting = False
        total = sum(len(p['kits']) for p in programs)
        self.km_status(f'Built {len(programs)} program{"s" if len(programs) != 1 else ""} ({total} kits) in {folder}. '
                       'Copy the program folders to your MPC card.')
        self.km_refresh()

    # ---- showing a candidate -------------------------------------------------------------------
    def km_advance(self, step=1):
        km = self.km
        km.pos += step
        km.selected = 0
        km.tag = ''
        km.cand = copy.deepcopy(km.order[km.pos]) if km.pos < len(km.order) else None
        if km.cand is not None:
            self.km_prepare(km.cand)
        self.km_refresh()

    def km_prepare(self, cand):
        """Background: fill any empty slots, decode every hit, work out the tag, and warm the next kits."""
        km = self.km
        events, cache, sounds = km.events, km.cache, km.sounds
        work_copy = copy.deepcopy(cand)
        upcoming = [copy.deepcopy(c) for c in km.order[km.pos + 1:km.pos + 3]]
        km.filling = any(not s['source'] for s in cand['slots'])

        def decode(path):
            if path not in cache:
                if len(cache) > CACHE_LIMIT:
                    for stale in list(cache)[:CACHE_LIMIT // 3]:
                        cache.pop(stale, None)
                cache[path] = kitlib.load_hit(path)

        def work():
            try:
                for _ in range(2):
                    bad = []
                    kitmaker.fill_empty(work_copy, sounds, seed=km.seed,
                                        progress=lambda text, value: events.put(('status', text)))
                    for index, slot in enumerate(work_copy['slots']):
                        if slot['source']:
                            try:
                                decode(slot['source'])
                            except Exception:
                                bad.append(index)
                    if not bad:
                        break
                    for index in bad:                 # an unreadable file: drop it and fill the slot again
                        work_copy['slots'][index].update(source=None, name='', origin='empty')
                tag = kitlib.character_tag(kitlib.kit_features(work_copy))
                events.put(('prepared', work_copy['id'], work_copy, tag))
                for other in upcoming:                # next kits: just decode, so Space is instant
                    for slot in other['slots']:
                        if slot['source']:
                            try:
                                decode(slot['source'])
                            except Exception:
                                pass
            except Exception as exc:
                events.put(('error', f'Could not prepare this kit: {exc}'))
        threading.Thread(target=work, daemon=True, name='kitmaker-prepare').start()

    def km_live_session(self):
        """The current kit as a session for MIDI preview: slot 1-8 on hardware pads 1-8 (None off this tab)."""
        km = getattr(self, 'km', None)
        cand = km.cand if km is not None else None
        if cand is None or self.tabs.get() != 'Make kits':
            return None
        sources = tuple(slot['source'] or '' for slot in cand['slots'])
        if km.live_key != sources:
            rows = []
            for slot in cand['slots'] + [{'source': None}] * (16 - len(cand['slots'])):
                try:
                    stat = os.stat(slot['source'])
                    rows.append({'status': 'sample', 'source': slot['source'], 'name': slot.get('name', ''),
                                 'kind': slot.get('kind', 'perc'), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
                except (OSError, TypeError):
                    rows.append({'status': 'empty'})
            km.live_key, km.live_session = sources, preview_session({'pads': rows})
        return km.live_session

    def km_refresh(self):
        km, ui = self.km, self.km_ui
        self.schedule_live_reload()                    # MIDI preview, when on, plays the kit now showing
        library = km.library
        kept = len(library.kits()) if library is not None else 0
        first = (kept // 8) * 8 + 1 if kept % 8 or kept == 0 else kept - 7
        in_program = kept % 8 or (8 if kept else 0)
        cand = km.cand
        if cand is None:
            ui.title.configure(text='Make kits' if not km.pool_ready else 'All done')
            ui.detail.configure(text='' if km.pool_ready else 'reading your NI library…')
        else:
            ui.title.configure(text=cand['kit'])
            parts = [cand['pack'], f"{cand['half']} half", f"{cand['filled']}/8 hits"]
            if km.tag:
                parts.append(km.tag)
            ui.detail.configure(text=' · '.join(parts))
        ui.counter.configure(text=(f"Kit {km.pos + 1} of {km.total}\n" if cand is not None else '') +
                             f"{kept} kept")
        ui.program.configure(text=(f"Program {kitpack.program_name(first, first + 7)}\n{in_program}/8 kits" if kept else
                                   'Keep a kit to start your first program (8 kits each).'))
        ui.recent.configure(text='\n'.join(f"{m['id']}  {m['tag'] or '—'}  ·  {m['source']['pack']}"
                                           for m in (library.kits()[-6:][::-1] if library is not None else [])))
        for index, pad in ui.pads.items():
            slot = cand['slots'][index] if cand else None
            if slot is None:
                pad.configure(text='', flash=False, glow=L.STEEL, unlit=True, tag='', locked=False, state_text='',
                              border_width=2, border_color=None)
                continue
            label, _, _ = kitmaker.HOUSE_LAYOUT[index]
            fit = max(8, pad.winfo_width() // 7)
            name = slot['name'] if len(slot['name']) <= fit else slot['name'][:fit - 1].rstrip() + '…'
            note = {'swapped': 'swapped', 'filled': 'filled in', 'kit (other half)': 'from other half'}.get(slot['origin'], '')
            if not slot['source']:
                name, note = ('Filling…' if km.filling else 'Empty'), ''
            pad.configure(flash=False, text=f'{index + 1}\n{name}' + (f'\n{note}' if note else ''), glow=L.pad_colour(slot['kind']),
                          unlit=not slot['source'], tag=label, locked=False, state_text=note,
                          border_width=3 if index == km.selected else 2,
                          border_color='#FFFFFF' if index == km.selected else None)
        self.km_refresh_side()
        self.km_refresh_meter()
        for key, enabled in ((ui.keep, cand is not None and not km.filling), (ui.skip, cand is not None),
                             (ui.hear, cand is not None), (ui.swap, cand is not None and not km.swapping),
                             (ui.undo, bool(km.history)), (ui.export, kept > 0 and not km.exporting),
                             (ui.open, library is not None)):
            key.configure(state='normal' if enabled else 'disabled')

    def km_refresh_side(self):
        km, ui = self.km, self.km_ui
        cand = km.cand
        index = km.selected
        label = kitmaker.HOUSE_LAYOUT[index][0]
        ui.pad_title.configure(text=f'Pad {index + 1} · {label}')
        slot = cand['slots'][index] if cand else None
        ui.pad_name.configure(text=(slot['name'] or 'Empty') if slot else '')
        ui.pad_source.configure(text=(Path(slot['source']).name + '\n' + {
            'kit': 'from this kit', 'kit (other half)': "from this kit's other half", 'filled': 'filled in from the expansion',
            'swapped': 'swapped in'}.get(slot['origin'], '')) if slot and slot['source'] else '')

    # ---- actions -------------------------------------------------------------------------------
    def km_hit(self, path):
        cache = self.km.cache
        if path not in cache:
            try:
                cache[path] = kitlib.load_hit(path)
            except Exception:
                return None
        return cache[path]

    def km_hear(self, toggle=True):
        """Play the kit's beat; pressed again while audio is still sounding, stop it instead."""
        cand = self.km.cand
        if toggle and time.monotonic() < self.km.sounding_until:
            self.km.sounding_until = 0
            self.stop_audio()
            return
        if cand is None:
            return
        hits = [self.km_hit(slot['source']) if slot['source'] else None for slot in cand['slots']]
        if not any(h is not None for h in hits):
            return
        self.play_array(groove.render_groove(hits), groove.RATE)

    def km_pad_click(self, index):
        cand = self.km.cand
        self.km.selected = index
        self.km_refresh()
        if cand and cand['slots'][index]['source']:
            hit = self.km_hit(cand['slots'][index]['source'])
            if hit is not None:
                self.play_array(np.repeat(hit, 2, axis=1) if hit.shape[1] == 1 else hit, groove.RATE)

    def km_select(self, index):
        if self.km.cand is not None:
            self.km.selected = index
            self.km_refresh()

    def km_keep(self):
        km = self.km
        cand = km.cand
        if cand is None:
            return
        if km.filling:
            self.km_status('Still finding sounds for the empty pads. One moment.')
            return
        try:
            manifest = km.library.add_kit(cand, tag=km.tag or None, hit_cache=km.cache)
        except Exception as exc:
            self.km_status(f'Could not keep this kit: {exc}')
            return
        km.seen.add(cand['id'])
        km.history.append(('keep', cand, manifest['number']))
        self.km_save_seen()
        count = len(km.library.kits())
        full = count % 8 == 0
        first = count - 7 if full else (count // 8) * 8 + 1
        where = f'{count % 8 or 8}/8 in {kitpack.program_name(first, first + 7)}'
        self.km_status(f"Kept {manifest['id']} ({manifest['tag'] or 'no tag'}). " +
                       ('Program is full: export when you like.' if full else where + '.'))
        self.km_saved(f"✓  SAVED  {manifest['id']}\n{where}" + ('  ·  full, ready to export' if full else ''))
        self.km_advance()

    def km_saved(self, text):
        """Confirm a keep where the eye already is: a green badge by the title, the Keep key, the program meter."""
        ui = self.km_ui
        self._km_saved_token = token = getattr(self, '_km_saved_token', 0) + 1
        ui.saved.configure(text=text)
        ui.saved.grid()
        self._km_badge = True
        ui.keep.configure(text='Kept ✓')

        def clear():
            if self._closed or self._km_saved_token != token:
                return
            ui.saved.grid_remove()
            self._km_badge = False
            ui.keep.configure(text='Keep (Y)')
            self.km_refresh_meter()
        self.after(int(SAVED_SECONDS * 1000), clear)

    def km_refresh_meter(self):
        km, ui = self.km, self.km_ui
        kept = len(km.library.kits()) if km.library is not None else 0
        filled = kept % 8 or (8 if kept else 0)
        newest = getattr(self, '_km_badge', False)
        for index, cell in enumerate(ui.meter):
            colour = (SAVED_NEW if newest and index == filled - 1 else SAVED) if index < filled else T.LINE
            cell.configure(fg_color=colour)

    def km_skip(self):
        km = self.km
        cand = km.cand
        if cand is None:
            return
        km.seen.add(cand['id'])
        km.history.append(('skip', cand, None))
        self.km_save_seen()
        self.km_advance()

    def km_undo(self):
        km = self.km
        if not km.history:
            self.km_status('Nothing to undo.')
            return
        kind, cand, number = km.history.pop()
        self._km_saved_token = getattr(self, '_km_saved_token', 0) + 1     # the "saved" badge no longer holds
        self.km_ui.saved.grid_remove()
        self._km_badge = False
        self.km_ui.keep.configure(text='Keep (Y)')
        if kind == 'keep':
            try:
                km.library.remove_last(number)
            except ValueError as exc:
                km.history.append((kind, cand, number))
                self.km_status(str(exc))
                return
        km.seen.discard(cand['id'])
        self.km_save_seen()
        km.pos -= 1
        km.cand, km.selected, km.tag = cand, 0, ''
        self.km_prepare(cand)
        self.km_status('Undone.' if kind == 'skip' else f'Removed {kitlib.kit_name(number)}.')
        self.km_refresh()

    def km_swap(self):
        km = self.km
        cand = km.cand
        if cand is None or km.swapping:
            return
        index = km.selected
        km.swapping = True
        km.seed += 1
        snapshot, seed = copy.deepcopy(cand), km.seed
        old = cand['slots'][index]['source']
        avoid = set(km.avoid.get((cand['id'], index), set()))
        events, sounds, cache = km.events, km.sounds, km.cache
        self.km_status(f"Looking for another {kitmaker.HOUSE_LAYOUT[index][0].lower()}…")
        self.km_refresh()

        def work():
            try:
                found = kitmaker.suggest(snapshot, index, sounds, seed, avoid=avoid,
                                         progress=lambda text, value: events.put(('status', text)))
                if found:
                    cache[found[0]] = kitlib.load_hit(found[0])
                events.put(('swapped', snapshot['id'], index, found, old))
            except Exception as exc:
                events.put(('error', f'Could not swap: {exc}'))
        threading.Thread(target=work, daemon=True, name='kitmaker-swap').start()

    def km_export(self):
        km = self.km
        if km.library is None or km.exporting or not km.library.kits():
            return
        km.exporting = True
        out, library, events = km.library.root / 'programs', km.library, km.events
        self.km_status('Building MPC1000 programs…')
        self.km_refresh()

        def work():
            try:
                programs = kitpack.build_all(library, out, progress=lambda text, value: events.put(('status', text)))
                events.put(('exported', programs, str(out)))
            except Exception as exc:
                events.put(('error', f'Export failed: {exc}'))
        threading.Thread(target=work, daemon=True, name='kitmaker-export').start()

    def km_open_folder(self):
        library = self.km.library
        if library is None:
            return
        target = library.root / 'programs'
        target = target if target.is_dir() else library.root
        try:
            os.startfile(str(target))                  # Windows; opens the folder you copy to the card
        except (AttributeError, OSError):
            self.km_status(f'Your kits are in {target}')

    def km_change_folder(self):
        from tkinter import filedialog
        chosen = filedialog.askdirectory(parent=self, title='Where should your kits be kept?',
                                         initialdir=str(self.km_folder()))
        if not chosen:
            return
        self._studio_state['kitmaker_dir'] = chosen
        try:
            self.write_studio_state()
        except OSError:
            pass
        self.km.library, self.km.history = None, []
        self.km_library()
        self.km_ui.folder.configure(text=str(self.km.library.root))
        self.km_status('Kit folder changed. Restart the review to pick up kits you already skipped there.')
        self.km_refresh()

    # ---- keys and audio -------------------------------------------------------------------------
    def km_key(self, event):
        if self.tabs.get() != 'Make kits' or self._typing(event):
            return None
        key = event.keysym.lower()
        if key in ('space', 'return'):
            self.km_hear()
        elif key == 'y':
            self.km_keep()
        elif key == 'n':
            self.km_skip()
        elif key == 'r':
            self.km_swap()
        elif key == 'u':
            self.km_undo()
        elif key in tuple('12345678'):
            self.km_select(int(key) - 1)
        else:
            return None
        return 'break'

    def play_array(self, data, rate):
        """Play audio held in memory through the same device and path as the app's file previews."""
        data = np.asarray(data, dtype=np.float32)
        self.km.sounding_until = time.monotonic() + len(data) / rate
        if os.environ.get('NI_TO_SP_DISABLE_AUDIO') == '1':
            self.km.last_played = (data.shape, rate)
            return
        self.preview_serial += 1
        serial = self.preview_serial
        device = getattr(self, 'output_device', None)
        if device is not None:
            import sounddevice as sd
            info = sd.query_devices(device, 'output')
            new_rate = int(info['default_samplerate'])
            if new_rate != rate:
                divisor = gcd(new_rate, rate)
                data = resample_poly(data, new_rate // divisor, rate // divisor, axis=0).astype(np.float32)
                rate = new_rate
            if info['max_output_channels'] == 4:
                data = np.pad(data, ((0, 0), (0, 2)))
        self.events.put(('audio_ready', (serial, data, rate, device)))


def time_seed():
    import time
    return time.time() // 86400          # a new shuffle each day, stable within a day
