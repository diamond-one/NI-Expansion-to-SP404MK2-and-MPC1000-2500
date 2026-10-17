"""Sample-only kit converter and pad editor. No desktop recording controls."""
import copy
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

import customtkinter as ctk

from .browser_ui import App as BrowserApp
from .capture_ui import BG, PANEL, CONTROL, TEXT, MUTED, ACCENT, AMBER
from .direct import preview_session, usable, inspect_group
from .kit_editor import (MODES, ExpansionPool, empty_kit, assign, clear, refresh,
                         sample_row, save_kit, open_kit, from_prepared)
from .live_midi import list_input_ports, MPC_PAD_NOTES
from .audio import simple_audio_outputs, audio_output_label
from .mpc1000 import export_program

PROFILE_LABELS = {'SP-404 Mode A': 'sp404_a', 'SP-404 Mode B': 'sp404_b',
    'MPC1000 / JJOS3 · program pads': 'mpc1000',
    'MPC / controller · notes 36–51': 'linear', 'Custom · learned pads only': 'custom'}


class LegacyApp(BrowserApp):
    def __init__(self):
        self.selected_pad, self.undo_history = 0, []
        super().__init__()
        self.title('NI to SP & MPC 0.7 · Sample workbench')
        self.bind('<Control-s>', lambda _: self.save_document())
        self.bind('<Control-o>', lambda _: self.open_document())
        self.bind('<Control-z>', lambda _: self.undo_edit())

    def build_original_controls(self):
        pass

    def sync_original_controls(self):
        pass

    def _build(self):
        from .studio_layout import build
        build(self)

    def transfer_dialog(self):
        if self.busy or not self.prepared:
            return
        from .transfer_dialog import TransferDialog
        existing = getattr(self, 'transfer_window', None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        self.transfer_window = TransferDialog(self)

    def _setup_drop(self):
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES
            TkinterDnD._require(self)
            for i, button in self.pad_buttons.items():
                for widget in (button, button._canvas, button._text_label):
                    if widget is not None:
                        widget.drop_target_register(DND_FILES)
                        widget.dnd_bind('<<Drop>>', lambda event, index=i: self.drop_sample(index, event.data))
            self.drop_available = True
        except (ImportError, tk.TclError) as exc:
            self.drop_available = False
            self.drop_error = str(exc)

    def sync(self):
        super().sync()
        if hasattr(self, 'mpc_button'):
            available = bool(self.group and self.group['available'] and
                (not self.group['unresolved'] or self.silence_var.get()))
            self.mpc_button.configure(state='normal' if available and not self.busy else 'disabled')
        from .studio_layout import sync
        sync(self)

    def update_random_scope(self):
        from .studio_layout import update_random_help
        mode = (2 if self.random_matching.get() == 'Same pad' else 0)
        mode += 1 if self.random_expansion.get() == 'All expansions' else 0
        self.random_mode.set(MODES[mode])
        update_random_help(self)
        self.sync()

    def change_target(self):
        self.sync()
        self.set_status('MPC1000 exports a program with samples.' if self.target_var.get() == 'MPC1000'
                        else 'SP transfer opens destination review before anything is sent.')

    def next_missing(self):
        if self.busy or not self.group or not self.group['unresolved']:
            return
        missing = [n - 1 for n in self.group['unresolved']]
        self.selected_pad = next((i for i in missing if i > self.selected_pad), missing[0])
        self.show_group()
        self.pad_buttons[self.selected_pad]._canvas.focus_set()

    def focus_pad(self, index):
        if not self.busy and self.group:
            self.selected_pad = index
            self.show_group()

    def move_pad(self, index, delta):
        if self.busy:
            return 'break'
        target = index + delta
        if target not in range(16) or abs(delta) == 1 and index // 4 != target // 4:
            return 'break'
        self.focus_pad(target)
        self.pad_buttons[target]._canvas.focus_set()
        return 'break'

    def keyboard_preview(self, index):
        self.preview_pad(index)
        return 'break'

    def _remember(self):
        self.undo_history.append(copy.deepcopy(self.group))
        self.undo_history = self.undo_history[-30:]

    def _apply(self, group, edited=True):
        self.stop_audio()
        self.group, self.kit, self.prepared, self.report = refresh(group), group['kit'], None, None
        self.session = preview_session(group)
        self.kit_title.configure(text=self.kit['name'])
        self.show_group()
        if edited:
            try:
                save_kit(group, self.data_dir / 'last-edited-kit.json')
            except OSError as exc:
                self.set_status('Kit updated; draft could not be saved: ' + str(exc))

    def new_empty(self):
        if self.busy:
            return
        self.undo_history = []
        self.silence_var.set(False)
        self._apply(empty_kit(), edited=False)
        self.set_status('Empty kit ready. Drop a sample on any pad, or select a pad and choose a sample.')

    def show_group(self):
        from .studio_layout import show_group
        show_group(self)

    def preview_pad(self, index):
        if self.busy:
            return
        self.selected_pad = index
        self.pad_buttons[index]._canvas.focus_set()
        if self.group:
            self.show_group()
            if not usable(self.group['pads'][index]):
                self.set_status('Drop a sample here, choose a file, or use one of the four Random scopes.')
                return
        super().preview_pad(index)

    def flash_pad(self, index):
        from . import desk_theme as T
        if self._closed:
            return
        self.pad_buttons[index].configure(border_width=3, border_color=T.FOCUS)
        self.after(120, lambda: self.show_group() if self.group and not self._closed else None)

    def pad_menu(self, event, index):
        if self.busy or not self.group:
            return
        self.selected_pad = index
        self.show_group()
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label='Choose sample…', command=lambda: self.replace_pad(index))
        for mode, label in enumerate(MODES):
            menu.add_command(label='Random: ' + label, command=lambda m=mode: self.random_pad(m))
        menu.add_separator()
        menu.add_command(label='Learn MIDI note for this pad', command=lambda: self.learn_pad(index))
        menu.add_command(label='Clear pad', command=lambda: self.clear_pad(index))
        menu.add_command(label='Undo last edit', command=self.undo_edit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def set_sample(self, index, path, provenance=None):
        if self.busy or not self.group:
            return False
        try:
            # Validate first so a failed replacement does not enter undo history.
            checked = copy.deepcopy(self.group)
            assign(checked, index, path, provenance)
            self._remember()
            self.selected_pad = index
            self._apply(checked)
            self.set_status(f"Pad {index + 1:02d}: {checked['pads'][index]['name']}")
            return True
        except (OSError, ValueError, RuntimeError) as exc:
            self.set_status('Sample could not be assigned: ' + str(exc))
            return False

    def replace_pad(self, index):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self, title='Choose a sample for this pad',
            filetypes=[('Audio samples', '*.wav *.wave *.aif *.aiff *.flac *.ogg')])
        if path:
            self.set_sample(index, path)

    def drop_sample(self, index, data):
        if self.busy:
            return 'refuse_drop'
        paths = self.tk.splitlist(data)
        if len(paths) != 1:
            self.set_status('Drop one sample onto a pad at a time.')
            return 'refuse_drop'
        return 'copy' if self.set_sample(index, paths[0], {'method': 'drag_and_drop'}) else 'refuse_drop'

    def clear_pad(self, index):
        if self.busy or not self.group:
            return
        self._remember()
        self._apply(clear(copy.deepcopy(self.group), index))
        self.set_status(f'Pad {index + 1:02d} cleared. Undo restores the previous sample.')

    def undo_edit(self):
        if self.busy or not self.undo_history:
            return
        self._apply(self.undo_history.pop())
        self.set_status('Last kit edit undone.')

    def random_pad(self, mode=None):
        if self.busy or not self.group:
            return
        mode = MODES.index(self.random_mode.get()) if mode is None else mode
        index, snapshot, kits = self.selected_pad, copy.deepcopy(self.group), copy.deepcopy(self.kits)
        pack = self.pack_var.get() if self.pack_var.get() != 'All expansions' else None
        # An NI kit defines its own expansion regardless of the library filter.
        if snapshot['kit'].get('root') or snapshot.get('origin_kit'):
            pack = None
        def done(result):
            self.set_sample(index, *result)
        self.submit(lambda: self.pool.choose(kits, snapshot, index, mode, pack, self.cancel, self.notify), done)

    def rename_kit(self):
        if self.busy or not self.group:
            return
        value = simpledialog.askstring('Kit name', 'Name your kit', initialvalue=self.kit['name'], parent=self)
        if value and value.strip():
            self._remember()
            group = copy.deepcopy(self.group)
            group['kit']['name'] = value.strip()[:100]
            self._apply(group)

    def save_document(self):
        if self.busy or not self.group:
            return
        path = filedialog.asksaveasfilename(parent=self, title='Save editable kit', defaultextension='.json',
            filetypes=[('Editable kit', '*.json')], initialfile='My-kit.json')
        if path:
            try:
                save_kit(self.group, path)
                self.set_status('Editable kit saved. Source sample files stay in their current folders.')
            except OSError as exc:
                self.set_status(str(exc))

    def open_document(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self, title='Open editable kit or last saved draft',
            initialdir=self.data_dir, filetypes=[('Editable kit', '*.json')])
        if path:
            try:
                group = open_kit(path)
                self.undo_history = []
                self._apply(group)
                self.set_status('Kit opened. Unavailable or changed samples are marked for replacement.')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.set_status('Could not open kit: ' + str(exc))

    def select_kit(self, _event=None, force=False):
        self.undo_history = []
        super().select_kit(_event, force)

    def toggle_preview_sources(self):
        if self.busy or not self.group or not self.group['kit'].get('path'):
            self.set_status('NI previews apply to expansion kits. Your custom sample assignments are kept.')
            return
        snapshot, use = copy.deepcopy(self.group), self.use_previews_var.get()
        original = dict(snapshot['kit'], name=Path(snapshot['kit']['path']).stem)
        def work():
            group = inspect_group(original, self.cancel, use)
            group['kit'] = snapshot['kit']
            for i, row in enumerate(snapshot['pads']):
                if row.get('replacement') or row['status'] == 'empty':
                    group['pads'][i] = row
            return refresh(group)
        def done(group):
            self._remember()
            self._apply(group)
            self.set_status('NI preview setting updated. Your replacements and intentionally empty pads were kept.')
        self.submit(work, done)


    def open_completed(self, path=None):
        if self.busy:
            return
        path = path or filedialog.askdirectory(parent=self, title='Open an exported SP kit for editing')
        if path:
            self.submit(lambda: from_prepared(path), lambda group: self._apply(group))

    def export_mpc(self):
        if self.busy or not self.group:
            return
        path = filedialog.askdirectory(parent=self, title='Export MPC1000 program and samples')
        if path:
            group, allow = copy.deepcopy(self.group), self.silence_var.get()
            def done(result):
                folder, validation = result
                self.set_status(f"MPC program exported: {folder} · {validation['sample_memory_mb']} MB")
            self.submit(lambda: export_program(group, path, allow, self.notify, self.cancel), done)

    def learn_pad(self, index):
        if not self.midi_controller or not self.midi_controller.connected:
            self.set_status('Turn MIDI preview on first, then choose Learn MIDI pad for the selected pad.')
            return
        self.midi_controller.learn(index, any_channel=self.connection_settings.get('channel') is None)
        self.set_status(f'Learning pad {index + 1:02d}: tap the matching hardware pad now.')

    def handle_midi_event(self, event):
        if event.get('type') == 'learned':
            self.connection_settings['mapping'] = event['mapping']
            self.save_connections()
            self.set_status(f"Learned pad {event['pad'] + 1:02d} · MIDI note {event['note']}")
        elif event.get('type') == 'unmapped':
            self.set_status(f"MIDI channel {event['channel']}, note {event['note']} received. " +
                ('Load the MPC program note map in Connections, or use MIDI Learn.'
                 if self.connection_settings.get('profile') == 'mpc1000'
                 else 'For an MPC, choose MPC1000 / JJOS3 in Connections.'))

    def audio_settings(self):
        from . import desk_theme as T
        def work():
            return simple_audio_outputs(), list_input_ports()
        def done(result):
            outputs, inputs = result
            window = ctk.CTkToplevel(self)
            window.title('MIDI preview connections')
            window.geometry('720x730')
            window.minsize(650, 710)
            window.configure(fg_color=T.WORKSPACE)
            window.transient(self)
            window.grab_set()
            window.controls = []
            form = ctk.CTkFrame(window, fg_color=T.PAPER, corner_radius=0, border_width=2, border_color=T.LINE)
            form.pack(fill='both', expand=True, padx=16, pady=16)
            T.label(form, 'Preview connections', 24, T.INK, True, True).pack(anchor='w', padx=24, pady=(20, 8))
            T.label(form, 'Play this kit from your hardware pads. The sound comes from the selected computer audio output.',
                14, T.MUTED, wraplength=600, justify='left').pack(anchor='w', padx=24, pady=(0, 16))
            mapping = {'Computer default': None}
            for output in outputs:
                label = audio_output_label(output)
                if label in mapping:
                    label += f" · {output['index']}"
                mapping[label] = output['index']
            current = next((k for k, v in mapping.items() if v == getattr(self, 'output_device', None)), 'Computer default')
            audio = tk.StringVar(value=current)
            midi = tk.StringVar(value=self.connection_settings.get('midiInputName') or 'Auto detect MIDI input')
            profile = tk.StringVar(value=next((k for k, v in PROFILE_LABELS.items() if v == self.connection_settings.get('profile')), 'SP-404 Mode A'))
            channel = tk.StringVar(value=str(self.connection_settings.get('channel') or 'Any'))
            for title, var, values in [('Audio output', audio, list(mapping)), ('MIDI input', midi, ['Auto detect MIDI input', *inputs]),
                    ('Pad mapping', profile, list(PROFILE_LABELS)), ('MIDI channel', channel, ['Any', *map(str, range(1, 17))])]:
                T.label(form, title, 13, T.MUTED).pack(anchor='w', padx=24)
                ctk.CTkOptionMenu(form, variable=var, values=values, width=600, height=34,
                    fg_color=T.ACTION, button_color=T.ACTION_HOVER, font=T.font(14),
                    corner_radius=0, text_color=T.ON_DARK, dropdown_fg_color=T.PAPER,
                    dropdown_text_color=T.INK, dropdown_hover_color=T.SELECTED,
                    dropdown_font=T.font(13)).pack(fill='x', padx=24, pady=(3, 12))
            pending_notes = list(self.connection_settings.get('mpcPadNotes') or MPC_PAD_NOTES)
            pending_name = self.connection_settings.get('mpcMapName') or 'Standard MPC program'
            note_label = T.label(form, '', 12, T.MUTED, anchor='w')
            note_label.pack(fill='x', padx=24, pady=(0, 5))
            note_buttons = ctk.CTkFrame(form, fg_color='transparent')
            note_buttons.pack(fill='x', padx=24, pady=(0, 10))
            def import_notes():
                nonlocal pending_notes, pending_name
                path = filedialog.askopenfilename(parent=window, title='Choose the program loaded on your MPC',
                    initialdir=Path.home() / 'Music/MPC1000/prgms', filetypes=[('MPC programs', '*.PGM *.pgm')])
                if not path:
                    return
                try:
                    from .mpc1000 import read_pad_notes
                    value = read_pad_notes(path)
                except (OSError, ValueError) as exc:
                    note_label.configure(text=str(exc), text_color=T.MISSING_INK)
                    return
                pending_notes, pending_name = value, Path(path).name
                profile.set('MPC1000 / JJOS3 · program pads')
                show_note_map()
            def reset_notes():
                nonlocal pending_notes, pending_name
                pending_notes, pending_name = list(MPC_PAD_NOTES), 'Standard MPC program'
                show_note_map()
            load_notes = T.button(window, note_buttons, 'Load MPC program map…', import_notes, width=218)
            load_notes.pack(side='left')
            reset = T.button(window, note_buttons, 'Use standard map', reset_notes, width=170)
            reset.pack(side='left', padx=10)
            def show_note_map(*_):
                mpc = PROFILE_LABELS[profile.get()] == 'mpc1000'
                note_label.configure(text=f'Pad notes: {pending_name}' if mpc else 'MPC program mapping is available with the MPC1000 profile.', text_color=T.MUTED)
                reset.configure(state='normal' if mpc else 'disabled')
            profile.trace_add('write', show_note_map)
            show_note_map()
            T.label(form, 'SP: USB with PAD Note Out enabled.\nMPC: MIDI OUT → interface IN. All four hardware banks play the visible kit bank.\nUse a MIDI track with its output assigned. JJ mpc-ctl button input is a separate function.',
                13, T.MUTED, wraplength=600, justify='left').pack(anchor='w', padx=24, pady=(2, 12))
            def save():
                self.disable_live_preview(quiet=True)
                self.stop_audio()
                self.output_device = mapping[audio.get()]
                device = next((o for o in outputs if o['index'] == self.output_device), None)
                selected = PROFILE_LABELS[profile.get()]
                if selected != self.connection_settings.get('profile') or (selected == 'mpc1000' and
                        pending_notes != self.connection_settings.get('mpcPadNotes', list(MPC_PAD_NOTES))):
                    self.connection_settings['mapping'] = {}
                self.connection_settings.update(profile=selected, channel=None if channel.get() == 'Any' else int(channel.get()),
                    midiInputName='' if midi.get() == 'Auto detect MIDI input' else midi.get(),
                    mpcPadNotes=pending_notes, mpcMapName=pending_name,
                    audioDeviceName=device['name'] if device else '', hostApiName=device['hostapi_name'] if device else '')
                self.save_connections()
                window.destroy()
                self.set_status('Connections saved. Turn MIDI preview on; right-click any pad for MIDI Learn.')
            buttons = ctk.CTkFrame(form, fg_color='transparent')
            buttons.pack(fill='x', padx=24, pady=(0, 18))
            T.button(window, buttons, 'Save connections', save, primary=True).pack(side='right')
            T.button(window, buttons, 'Cancel', window.destroy, width=100).pack(side='right', padx=10)
        self.submit(work, done)


from .studio_workflow import StudioWorkflow


class App(StudioWorkflow, LegacyApp):
    """Musician-facing workflow with shared transfer and connection code."""
    pass
