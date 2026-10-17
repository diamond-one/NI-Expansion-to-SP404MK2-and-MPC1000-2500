import copy
import json
import os
from pathlib import Path
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .capture_ui import ACCENT, AMBER, MUTED, TEXT
from .original import command, create_queue, worker_identity
from .sp_transfer import _write_json


class OriginalControlsMixin:
    def build_original_controls(self):
        self.original_process = None
        self.original_queue_path = None
        self.original_report = {}
        pointer = self.data_dir / 'original-queue.json'
        try:
            saved = Path(json.loads(pointer.read_text(encoding='utf-8'))['path'])
            if saved.is_file():
                self.original_queue_path = saved
        except (OSError, ValueError, KeyError):
            pass
        page = self.tabs.add('Original Maschine sound')
        page.grid_columnconfigure(0, weight=1)
        self.label(page, 'Render the kit through Maschine', 22, ACCENT, True).grid(
            row=0, column=0, sticky='w', padx=18, pady=(18, 8))
        self.label(page, 'Records all 16 sounds with their instruments, EQ and loaded effects baked into the audio.\n'
            'One C3 note per pad · velocity 100 · half-second note hold · effect tails checked.',
            13, TEXT, wraplength=700, justify='left').grid(row=1, column=0, sticky='w', padx=18, pady=(0, 14))
        self.label(page, 'Render selection', 12, MUTED).grid(row=2, column=0, sticky='w', padx=18)
        self.original_scope = tk.StringVar(value='All expansions')
        scope = ctk.CTkOptionMenu(page, variable=self.original_scope,
            values=['All expansions', 'Matching kits', 'Selected kit'], width=240)
        scope.grid(row=3, column=0, sticky='w', padx=18, pady=(4, 12))
        self.controls.append(scope)
        self.label(page, 'Save original kits here', 12, MUTED).grid(row=4, column=0, sticky='w', padx=18)
        line = ctk.CTkFrame(page, fg_color='transparent')
        line.grid(row=5, column=0, sticky='ew', padx=18, pady=(4, 12))
        line.grid_columnconfigure(0, weight=1)
        self.original_destination = tk.StringVar(value=str(Path.home() / 'Music/SAMPLES_ALL/expansions Original Maschine sound'))
        self.entry(line, self.original_destination).grid(row=0, column=0, sticky='ew', padx=(0, 10))
        self.button(line, 'Choose folder…', self.choose_original_destination, width=130).grid(row=0, column=1)
        self.original_force = tk.BooleanVar(value=False)
        force = ctk.CTkCheckBox(page, text='Re-render completed originals', variable=self.original_force)
        force.grid(row=6, column=0, sticky='w', padx=18, pady=(0, 8))
        self.controls.append(force)
        self.label(page, 'Open Maschine 2.18.4 and save any work first. Rendering uses its window; leave it in front.\n'
            'Between kits it waits if you switch apps. Unexpected dialogs pause the queue; completed kits are kept.\n'
            'Shared compressors and effects are recorded per hit; their interaction during live playing cannot transfer.',
            12, MUTED, wraplength=700, justify='left').grid(row=7, column=0, sticky='w', padx=18, pady=(0, 12))
        actions = ctk.CTkFrame(page, fg_color='transparent')
        actions.grid(row=8, column=0, sticky='ew', padx=18, pady=(0, 12))
        self.original_start = self.button(actions, 'Render originals', self.start_original, primary=True, width=160)
        self.original_start.pack(side='left', padx=(0, 8))
        self.original_resume = self.button(actions, 'Resume queue', self.resume_original, width=130)
        self.original_resume.pack(side='left', padx=(0, 8))
        self.original_pause = self.button(actions, 'Pause', self.pause_original, width=90)
        self.original_pause.pack(side='left')
        self.original_status = self.label(page, 'No original render queue yet.', 13, TEXT,
            wraplength=700, anchor='w', justify='left')
        self.original_status.grid(row=9, column=0, sticky='ew', padx=18, pady=(0, 12))
        bottom = ctk.CTkFrame(page, fg_color='transparent')
        bottom.grid(row=10, column=0, sticky='ew', padx=18, pady=(0, 12))
        self.original_open = self.button(bottom, 'Preview latest rendered kit', self.open_latest_original, width=210)
        self.original_open.pack(side='left', padx=(0, 8))
        self.button(bottom, 'Open render report', self.open_original_report, width=160).pack(side='left')
        self.tabs.configure(command=self.original_tab_changed)
        self.sync_original_controls()
        self.after(1000, self.poll_original_queue)

    def original_tab_changed(self):
        if self.tabs.get() == 'Original Maschine sound':
            self.batch_panel.grid_remove()
        else:
            self.batch_panel.grid()

    def choose_original_destination(self):
        path = filedialog.askdirectory(parent=self, title='Save processed Maschine kits outside installed expansions')
        if path:
            self.original_destination.set(path)

    def start_original(self):
        if self.busy or self.original_running():
            return
        scope = self.original_scope.get()
        kits = self.kits if scope == 'All expansions' else self.filtered if scope == 'Matching kits' else [self.kit] if self.kit else []
        if not kits:
            self.set_status('Choose at least one installed kit to render.')
            return
        try:
            from .maschine_engine import plugin_path
            plugin_path()
            self.disable_live_preview(quiet=True)
            self.stop_audio()
            self.original_queue_path = create_queue(copy.deepcopy(kits),
                self.original_destination.get(), self.data_dir / 'original-jobs', self.original_force.get())
            _write_json(self.data_dir / 'original-queue.json', {'path': str(self.original_queue_path)})
            self.launch_original_worker()
        except Exception as exc:
            messagebox.showerror('Original Maschine sound', str(exc), parent=self)

    def launch_original_worker(self):
        pause = self.original_queue_path.with_suffix('.pause')
        if pause.exists():
            pause.unlink()
        with self.original_queue_path.with_suffix('.worker.log').open('a', encoding='utf-8') as log:
            self.original_process = subprocess.Popen(command('--original-queue', self.original_queue_path),
                stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.set_status('Original render queue started. Leave Maschine in front while it loads and captures kits.')
        self.sync_original_controls()

    def resume_original(self):
        if self.busy or self.original_running():
            return
        if self.original_queue_path is None:
            path = filedialog.askopenfilename(parent=self, title='Resume an ORIGINAL-BATCH report',
                filetypes=[('Original render report', 'ORIGINAL-BATCH-*.json')])
            if not path:
                return
            self.original_queue_path = Path(path)
        try:
            self.disable_live_preview(quiet=True)
            self.stop_audio()
            self.launch_original_worker()
        except Exception as exc:
            messagebox.showerror('Resume original rendering', str(exc), parent=self)

    def pause_original(self):
        if self.original_queue_path:
            self.original_queue_path.with_suffix('.pause').write_text('Pause requested\n', encoding='utf-8')
            self.set_status('Pausing original rendering. Completed kits are kept.')

    def original_running(self):
        if self.original_process is not None and self.original_process.poll() is None:
            return True
        report = self.original_report
        pid = report.get('worker_pid')
        return bool(pid and report.get('worker_created') and worker_identity(pid) == report['worker_created'])

    def sync_original_controls(self):
        if not hasattr(self, 'original_start'):
            return
        running = self.original_running()
        self.original_start.configure(state='normal' if self.kits and not self.busy and not running else 'disabled')
        self.original_resume.configure(state='normal' if not self.busy and not running and
            (not self.original_queue_path or self.original_report.get('pending', True)) else 'disabled')
        self.original_pause.configure(state='normal' if running else 'disabled')
        self.original_open.configure(state='normal' if self.original_report.get('completed') and not running and not self.busy else 'disabled')

    def poll_original_queue(self):
        if self.closed:
            return
        if self.original_queue_path:
            try:
                report = json.loads(self.original_queue_path.read_text(encoding='utf-8'))
                if report.get('schema') != 1 or report.get('mode') != 'original':
                    raise ValueError('This is not an original-render queue.')
                self.original_report = report
                counts = f'{len(report["completed"]):,} rendered · {len(report["failed"]):,} failed · {len(report["pending"]):,} remaining'
                self.original_status.configure(text=counts + '\n' + report['message'],
                    text_color=AMBER if report['state'] == 'needs_attention' else TEXT)
                if self.original_running():
                    self.progress.set((len(report['completed']) + len(report['failed'])) / max(1, report['total']))
            except (OSError, ValueError, KeyError) as exc:
                self.original_status.configure(text='Could not read the render report: ' + str(exc), text_color=AMBER)
        self.sync_original_controls()
        self.after(1000, self.poll_original_queue)

    def open_latest_original(self):
        completed = self.original_report.get('completed', [])
        if completed and not self.original_running():
            self.tabs.set('Play & export')
            self.original_tab_changed()
            self.open_completed(completed[-1]['folder'])

    def open_original_report(self):
        if self.original_queue_path and self.original_queue_path.is_file():
            os.startfile(self.original_queue_path)
