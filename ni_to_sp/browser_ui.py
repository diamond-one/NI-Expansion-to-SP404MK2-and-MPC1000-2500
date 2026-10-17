"""Direct kit browsing replaces capture as the everyday workflow."""
import copy
import os
from pathlib import Path
import queue
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

from .capture_ui import App as CaptureApp, BG, PANEL, CONTROL, TEXT, MUTED, ACCENT, AMBER
from .capture import load_prepared
from .direct import inspect_group, preview_session, prepare_direct, prepare_batch, usable, sound_previews
from .library import discover_kits
from .model import sp_pad_number
from .live_controls import LivePreviewMixin
from .live_midi import load_settings, save_settings, list_input_ports
from .audio import simple_audio_outputs, list_audio_outputs, audio_output_label
from .original_ui import OriginalControlsMixin


class _PreviewOutput:
    def __init__(self, app):
        self.app = app

    def set_output_device(self, device):
        self.app.output_device = device


class App(OriginalControlsMixin, LivePreviewMixin, CaptureApp):
    def __init__(self):
        self.bank, self.group, self._closed = 0, None, False
        self.pad_names = {}
        super().__init__()
        self.title('NI to SP 0.5 · Expansion kit browser')

    def _build(self):
        super()._build()
        self.connection_settings = load_settings(self.data_dir / 'connections.json')
        self.player = _PreviewOutput(self)
        self._init_live_controls()
        head = self.grid_slaves(row=0, column=0)[0]
        for widget in head.winfo_children():
            if isinstance(widget, ctk.CTkLabel) and 'captured for' in widget.cget('text'):
                widget.configure(text='Your expansion kits, ready to play')
            elif isinstance(widget, ctk.CTkLabel) and widget.cget('text').startswith('1  Select'):
                widget.configure(text='Select a kit → play the pads → export to your SP')
                widget.grid_configure(columnspan=2)
                self.preview_route_label = widget
        self.live_button = ctk.CTkButton(head, text='MIDI preview: OFF', command=self.toggle_live_preview,
            width=170, height=32, fg_color=CONTROL, text_color=TEXT)
        self.live_button.grid(row=1, column=2, columnspan=2, sticky='e', pady=(8, 0))
        self.live_colors = {'on': ACCENT, 'pending': AMBER, 'off': CONTROL, 'dark': BG, 'text': TEXT}
        self.tabs.delete('Capture in Maschine')
        self.tabs.rename('Review & send', 'Play & export')
        page = self.tabs.tab('Play & export')
        for row in (0, 1, 2):
            for widget in page.grid_slaves(row=row):
                widget.grid_remove()
        intro = self.label(page, 'Original sample files · original sound slots · no Maschine export', 14, ACCENT,
            anchor='w', wraplength=740, justify='left')
        intro.grid(row=0, column=0, sticky='ew', padx=10, pady=(12, 5))
        self.silence_var = tk.BooleanVar(value=False)
        options = ctk.CTkFrame(page, fg_color='transparent')
        options.grid(row=1, column=0, sticky='ew', padx=10, pady=(5, 8))
        self.use_previews_var = tk.BooleanVar(value=True)
        preview_check = ctk.CTkCheckBox(options, text='Use NI sound previews', variable=self.use_previews_var,
            command=lambda: self.select_kit(force=True) if self.group else None, font=ctk.CTkFont(size=12))
        preview_check.pack(side='left')
        check = ctk.CTkCheckBox(options, text='Export unresolved pads as silence', variable=self.silence_var,
            command=self.sync, font=ctk.CTkFont(size=12))
        check.pack(side='left', padx=(20, 0))
        self.controls.extend([check, preview_check])
        self.label(page, 'Blue pads use an NI preview clip, which may be a phrase. Amber pads still need a WAV or capture.',
            12, MUTED, wraplength=740, anchor='w', justify='left').grid(row=2, column=0, sticky='ew', padx=10, pady=(0, 10))
        self.controls = [widget for widget in self.controls if widget.winfo_exists()]
        for widget in self.controls:
            if isinstance(widget, ctk.CTkButton):
                if widget.cget('text') == 'Resume capture job…':
                    widget.configure(text='Capture synths & effects…', command=self.open_capture_app)
                elif widget.cget('text') == 'Open prepared WAVs':
                    widget.configure(text='Export kit WAVs…', command=self.export_current)
                    self.export_button = widget
                elif widget.cget('text') == 'Audio output…':
                    widget.configure(text='Connections…')
        self.report_button.configure(text='Pad details')
        self.send_open_button.configure(text='Send kit to SP…', command=self.send_current)
        for index, widget in self.pad_buttons.items():
            widget.bind('<Button-3>', lambda event, i=index: self.pad_menu(event, i))
        right = self.tabs.master
        batch = ctk.CTkFrame(right, fg_color=PANEL)
        self.batch_panel = batch
        batch.grid(row=3, column=0, sticky='ew', pady=(12, 0))
        batch.grid_columnconfigure(0, weight=1)
        self.batch_label = self.label(batch, 'Convert your collection', 14, TEXT, True, anchor='w')
        self.batch_label.grid(row=0, column=0, sticky='ew', padx=16, pady=(10, 0))
        self.label(batch, 'Matching follows your search. All expansions includes every installed kit.\nNI preview matching is applied automatically when enabled above.', 12, MUTED,
            wraplength=430, anchor='w', justify='left').grid(row=1, column=0, sticky='ew', padx=16, pady=(0, 12))
        self.batch_button = self.button(batch, 'Export matching kits…', self.export_batch, width=190)
        self.batch_button.grid(row=0, column=1, padx=16, pady=(10, 4))
        self.all_batch_button = self.button(batch, 'Export all expansions…',
            lambda: self.export_batch(all_expansions=True), width=190)
        self.all_batch_button.grid(row=1, column=1, padx=16, pady=(4, 12), sticky='n')
        self.kit_title.configure(text='Choose a kit. Start playing.')
        self.kit_subtitle.configure(text='Browse installed NI expansions. Sample pads are ready to preview immediately.')
        self.quality.configure(text='Select a kit to see its 16 pad positions.', text_color=MUTED)
        self.build_original_controls()
        self.sync()

    def sync(self):
        if not hasattr(self, 'batch_button'):
            return CaptureApp.sync(self)
        convertible = bool(self.group['available'] and (not self.group['unresolved'] or self.silence_var.get())) if self.group else bool(self.prepared)
        for widget in (self.send_open_button, self.export_button):
            widget.configure(state='normal' if convertible and not self.busy else 'disabled')
        self.report_button.configure(state='normal' if self.group or self.report else 'disabled')
        self.batch_button.configure(state='normal' if self.filtered and not self.busy else 'disabled')
        self.all_batch_button.configure(state='normal' if self.kits and not self.busy else 'disabled')
        for button in self.pad_buttons.values():
            button.configure(state='normal' if (self.group or self.session) and not self.busy else 'disabled')
        self.sync_live_controls()
        self.schedule_live_reload()
        self.sync_original_controls()

    def scan(self):
        if self.busy:
            return
        root = self.root_var.get()
        sound_previews.cache_clear()
        def work():
            kits = discover_kits(root, self.cancel)
            device, warning = None, None
            if self.connection_settings.get('audioDeviceName'):
                try:
                    device = self.audio_device(self.connection_settings, list_audio_outputs())
                except Exception as exc:
                    warning = str(exc)
            return kits, device, warning
        def done(result):
            kits, self.output_device, warning = result
            self.kits = kits
            self.pack_menu.configure(values=['All expansions', *sorted({k['pack'] for k in kits})])
            self.pack_var.set('All expansions')
            self.filter_kits()
            self.set_status(warning or f'Found {len(kits):,} kits. Select one to preview its sample pads immediately.')
        self.submit(work, done)

    def filter_kits(self):
        CaptureApp.filter_kits(self)
        if hasattr(self, 'batch_label'):
            self.batch_label.configure(text=f'{len(self.kits):,} installed kits · {len(self.filtered):,} matching')
            self.sync()

    def select_kit(self, _event=None, force=False):
        if self.busy or not self.kit_list.curselection():
            return
        kit = self.visible[self.kit_list.curselection()[0]]
        if self.kit and kit['id'] == self.kit['id'] and self.group and not force:
            return
        self.stop_audio()
        self.kit, self.group, self.session, self.prepared, self.report = dict(kit), None, None, None, None
        self.pad_names.clear()
        self.silence_var.set(False)
        self.kit_title.configure(text=kit['name'])
        self.kit_subtitle.configure(text=kit['pack'] + ' · Reading original sound slots…')
        for index, button in self.pad_buttons.items():
            button.configure(text=f'SP {sp_pad_number(index):02d}\nReading kit…', fg_color=CONTROL, text_color=TEXT)
        def done(group):
            self.group, self.session = group, preview_session(group)
            self.show_group()
            self.set_status('Ready to audition. Turn MIDI preview on to play this kit from your SP pads.')
        def failed(exc):
            self.quality.configure(text=str(exc), text_color=getattr(self, 'ui_warning_color', AMBER))
            self.kit_subtitle.configure(text=kit['pack'] + ' · Direct conversion unavailable for this group structure')
            for index, button in self.pad_buttons.items():
                button.configure(text=f'SP {sp_pad_number(index):02d}\nUnsupported layout', fg_color=CONTROL)
        use_previews = self.use_previews_var.get()
        self.submit(lambda: inspect_group(kit, self.cancel, use_previews), done, failed=failed)

    def show_group(self):
        group = self.group
        self.kit_subtitle.configure(text=group['kit']['pack'] + ' · Original sound slots · Direct sample conversion')
        count = len(group['unresolved'])
        preview_count = group.get('preview_count', 0)
        self.quality.configure(text=f"{group['available']}/16 pads ready" + (f' · {preview_count} NI preview clips' if preview_count else '') +
            (f' · {count} need a recording or replacement WAV' if count else ' · Ready to export'), text_color=AMBER if count else ACCENT)
        for index, row in enumerate(group['pads']):
            name = row['name']
            self.pad_names[index] = name
            short = name if len(name) <= 25 else name[:23] + '…'
            self.pad_buttons[index].configure(text=f"SP {row['sp_pad']:02d}  /  NI {index + 1:02d}\n{short}" +
                (f"\nNI preview · {row['preview_duration']:.1f}s" if row['status'] == 'preview' else '' if usable(row) else '\nNeeds WAV / capture'),
                fg_color='#334F6B' if row['status'] == 'preview' else '#315348' if usable(row) else '#614B32')
        self.sync()

    def preview_pad(self, index):
        if self.busy or not self.session:
            return
        if not self.session.pads[index].sample_id:
            self.set_status(f"{self.pad_names.get(index, 'This pad')} has no usable sample. Right-click to supply a WAV, or use Capture synths / processed kit.")
            return
        self.flash_pad(index)
        if self.live_engine and self.live_engine.running and self.live_signature == self.live_bank_signature():
            self.live_engine.trigger(index)
        else:
            CaptureApp.preview_pad(self, index)

    def play_file(self, path, limit=None):
        CaptureApp.play_file(self, path, min(limit or 120, 120))

    def flash_pad(self, index):
        if self._closed:
            return
        button = self.pad_buttons[index]
        button.configure(border_width=3, border_color=AMBER)
        self.after(120, lambda: button.configure(border_width=0) if not self._closed else None)

    def pad_menu(self, event, index):
        if not self.group or self.busy:
            return
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label='Use a recorded WAV for this pad…', command=lambda: self.replace_pad(index))
        menu.tk_popup(event.x_root, event.y_root)

    def replace_pad(self, index):
        path = filedialog.askopenfilename(parent=self, title='Choose a WAV for this original pad position', filetypes=[('Audio', '*.wav *.aif *.aiff *.flac')])
        if not path or not self.group:
            return
        source = Path(path).resolve(strict=True)
        stat = source.stat()
        row = self.group['pads'][index]
        row.update(status='sample', source=str(source), size=stat.st_size, mtime_ns=stat.st_mtime_ns, supplied_by_user=True)
        self.group['unresolved'] = [r['ni_pad'] for r in self.group['pads'] if not usable(r)]
        self.group['available'] = 16 - len(self.group['unresolved'])
        self.group['preview_count'] = sum(r['status'] == 'preview' for r in self.group['pads'])
        self.stop_audio()
        self.prepared = None
        self.session = preview_session(self.group)
        self.show_group()

    def prepare_current(self, destination, done):
        group, allow = copy.deepcopy(self.group), self.silence_var.get()
        def complete(result):
            self.prepared, self.session = result
            self.report = load_prepared(self.prepared)[1]['capture_report']
            done()
        self.submit(lambda: prepare_direct(group, destination, allow, self.notify, self.cancel), complete)

    def export_current(self):
        if self.prepared and not self.group:
            os.startfile(self.prepared)
            return
        destination = filedialog.askdirectory(parent=self, title='Export kit WAVs outside the installed NI libraries')
        if destination and self.group:
            self.prepare_current(destination, lambda: self.set_status(f'Kit exported: {self.prepared}'))

    def send_current(self):
        if self.busy:
            return
        if self.group and self.group['unresolved'] and not self.silence_var.get():
            self.set_status('Resolve unavailable pads or explicitly allow silence before sending.')
            return
        if self.prepared:
            self.transfer_dialog()
        elif self.group:
            self.prepare_current(self.data_dir / 'prepared', self.transfer_dialog)

    def export_batch(self, all_expansions=False):
        scope = self.kits if all_expansions else self.filtered
        if self.busy or not scope:
            return
        kits, allow, use_previews = copy.deepcopy(scope), self.silence_var.get(), self.use_previews_var.get()
        description = 'all installed kits' if all_expansions else 'matching kits'
        destination = filedialog.askdirectory(parent=self, title=f'Export {len(kits):,} {description}, organized by expansion')
        if not destination:
            return
        def done(result):
            path, report = result
            message = f"{len(report['converted'])} kits exported · {len(report['skipped'])} skipped" + (' · cancelled' if report['cancelled'] else '')
            previews = sum(len(row.get('preview_pads', [])) for row in report['converted'])
            self.set_status(message + f'. Report: {path}')
            messagebox.showinfo('Expansion export', message + f'\n{previews:,} pads used matching NI preview clips.\n\nCompleted kits are organized by expansion.\nKits with unresolved pads are skipped unless empty positions were explicitly allowed.\n\nReport: ' + str(path), parent=self)
        self.submit(lambda: prepare_batch(kits, destination, allow, self.notify, self.cancel, use_previews), done)

    def open_completed(self, path=None):
        if self.busy:
            return
        path = path or filedialog.askdirectory(parent=self, title='Open a completed NI to SP kit folder')
        if not path:
            return
        def work():
            session, recipe = load_prepared(path)
            if any(not row.get('name') for row in recipe['pads']):
                try:
                    original = inspect_group(recipe['capture_job']['kit'])
                    for row in recipe['pads']:
                        row['name'] = original['pads'][row['logical_slot'] - 1]['name']
                except (OSError, ValueError, KeyError):
                    pass
            return session, recipe
        def done(result):
            session, recipe = result
            self.stop_audio()
            self.group = None
            self.prepared, self.session = Path(path), session
            self.kit, self.report = recipe['capture_job']['kit'], recipe.get('capture_report')
            self.kit_title.configure(text=self.kit['name'])
            self.kit_subtitle.configure(text=self.kit['pack'] + ' · Completed ' + ('sample conversion' if recipe.get('mode') == 'direct' else 'Maschine capture'))
            self.quality.configure(text='16 verified WAVs loaded. Play the pads, then choose Send kit to SP.', text_color=ACCENT)
            for row in recipe['pads']:
                index = row['logical_slot'] - 1
                name = row.get('name', f'Maschine pad {index + 1}')
                self.pad_names[index] = name
                self.pad_buttons[index].configure(text=f"SP {row['pad']:02d}  /  NI {index + 1:02d}\n{name[:25]}" +
                    ('\nNI preview clip' if row.get('source_status') == 'preview' else '\nSilence' if row.get('intentional_empty') else ''),
                    fg_color='#334F6B' if row.get('source_status') == 'preview' else '#315348')
            self.set_status('Completed kit opened. MIDI preview works without importing samples to the SP.')
        self.submit(work, done)

    def show_check_report(self):
        if not self.group and not self.report:
            return
        window = ctk.CTkToplevel(self)
        window.title('Kit pad details')
        window.geometry('790x600')
        window.transient(self)
        textbox = ctk.CTkTextbox(window, wrap='word', font=ctk.CTkFont(size=13))
        textbox.pack(fill='both', expand=True, padx=16, pady=16)
        rows = self.group['pads'] if self.group else self.report['pads']
        lines = [self.kit['name'], self.kit['pack'], '']
        for row in rows:
            lines.append(f"NI {row['ni_pad']:02d} → SP {row['sp_pad']:02d} · {row.get('name', '')}\n{row.get('status', 'Captured')}\n{row.get('source') or 'Rendered sample'}\n")
        self.set_text(textbox, '\n'.join(lines))

    def open_capture_app(self):
        command = [sys.executable, '--capture'] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).parent.parent / 'run.py'), '--capture']
        subprocess.Popen(command)
        self.set_status('Opened the optional Maschine capture tool for synths, effects and unsupported groups.')

    def audio_device(self, settings, outputs):
        name, host = settings.get('audioDeviceName'), settings.get('hostApiName')
        if not name:
            return None
        matches = [d for d in outputs if d['name'] == name]
        if not matches:
            raise ValueError('Saved audio output is unavailable. Choose an output in Connections.')
        return next((d['index'] for d in matches if d['hostapi_name'] == host), matches[0]['index'])

    def save_connections(self):
        save_settings(self.data_dir / 'connections.json', self.connection_settings)

    def audio_settings(self):
        def work():
            outputs = simple_audio_outputs()
            try:
                inputs, error = list_input_ports(), None
            except Exception as exc:
                inputs, error = [], str(exc)
            return outputs, inputs, error
        def done(result):
            outputs, inputs, error = result
            window = ctk.CTkToplevel(self)
            window.title('SP preview connections')
            window.geometry('660x440')
            window.transient(self)
            window.grab_set()
            form = ctk.CTkFrame(window, fg_color=PANEL)
            form.pack(fill='both', expand=True, padx=16, pady=16)
            self.label(form, 'Play the kit from your SP pads', 20, ACCENT, True).pack(anchor='w', padx=18, pady=12)
            self.label(form, 'Enable PAD Note Out on the SP. Its pads trigger audio on this computer.\nChoose the SP audio output to hear it through the sampler.',
                12, MUTED, wraplength=580, justify='left').pack(anchor='w', padx=18, pady=(0, 12))
            mapping = {'Computer default': None}
            for output in outputs:
                label = audio_output_label(output)
                if label in mapping:
                    label += ' · ' + str(output['index'])
                mapping[label] = output['index']
            current = next((label for label, index in mapping.items() if index == getattr(self, 'output_device', None)), 'Computer default')
            self.label(form, 'Preview audio output', 12, MUTED).pack(anchor='w', padx=18)
            audio = tk.StringVar(value=current)
            ctk.CTkOptionMenu(form, variable=audio, values=list(mapping), width=580).pack(padx=18, pady=(4, 10))
            self.label(form, 'MIDI input', 12, MUTED).pack(anchor='w', padx=18)
            midi = tk.StringVar(value=self.connection_settings.get('midiInputName') or 'Automatic SP input')
            ctk.CTkOptionMenu(form, variable=midi, values=['Automatic SP input', *inputs], width=580).pack(padx=18, pady=(4, 10))
            profile = tk.StringVar(value='SP Mode B' if self.connection_settings.get('profile') == 'sp404_b' else 'SP Mode A')
            ctk.CTkOptionMenu(form, variable=profile, values=['SP Mode A', 'SP Mode B'], width=180).pack(anchor='w', padx=18, pady=6)
            if error:
                self.label(form, error, 12, AMBER, wraplength=580).pack(padx=18)
            def save():
                self.disable_live_preview(quiet=True)
                self.stop_audio()
                self.output_device = mapping[audio.get()]
                selected = next((d for d in outputs if d['index'] == self.output_device), None)
                self.connection_settings.update(audioDeviceName=selected['name'] if selected else '',
                    hostApiName=selected['hostapi_name'] if selected else '', midiInputName='' if midi.get() == 'Automatic SP input' else midi.get(),
                    profile='sp404_b' if profile.get() == 'SP Mode B' else 'sp404_a')
                self.save_connections()
                self.set_status('Connections saved. Turn MIDI preview on when ready.')
                window.destroy()
            ctk.CTkButton(form, text='Save connections', command=save, fg_color=ACCENT, text_color=BG, height=36).pack(pady=12)
        self.submit(work, done)

    def stop_audio(self):
        CaptureApp.stop_audio(self)
        if getattr(self, 'live_engine', None):
            self.live_engine.silence()

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind.startswith('live_'):
                    self.handle_live_result(kind, value)
                elif kind == 'progress':
                    self.set_status(value[0])
                    self.progress.set(value[1])
                elif kind in ('done', 'error'):
                    self.busy = self.sending = False
                    self.progress.set(0)
                    for widget in self.controls:
                        widget.configure(state='normal')
                    self.kit_list.configure(state='normal')
                    self.cancel_button.configure(state='disabled')
                    if self.closing:
                        self.finish_close()
                        return
                    if kind == 'done':
                        value[0](value[1])
                    else:
                        exc, failed = value
                        if failed:
                            failed(exc)
                        self.set_status(str(exc))
                        if not isinstance(exc, InterruptedError) and not failed:
                            journal = getattr(exc, 'report', {}).get('journal')
                            messagebox.showerror('NI to SP', str(exc) + (f'\nTransfer record: {journal}' if journal else ''), parent=self)
                    self.sync()
                elif kind == 'audio_error':
                    self.set_status(value)
                elif kind == 'audio_ready':
                    self.accept_audio(value)
        except queue.Empty:
            pass
        except Exception as exc:
            self.set_status('Could not finish this step: ' + str(exc))
        if not self.closed:
            self.after(60, self.poll)

    def close(self):
        self.disable_live_preview(quiet=True)
        CaptureApp.close(self)

    def finish_close(self):
        self._closed = True
        CaptureApp.finish_close(self)
