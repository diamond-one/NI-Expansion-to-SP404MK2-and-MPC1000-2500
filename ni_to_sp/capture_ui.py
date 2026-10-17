"""A focused, local, assisted Maschine-render to SP-404MKII workflow."""
import json
import gc
import os
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy.signal import resample_poly
from math import gcd

from . import sp_transfer
from .audio import simple_audio_outputs, audio_output_label
from .capture import create_capture, inspect_render, prepare_kit, load_capture, load_prepared, render_instructions
from .library import discover_kits
from .model import sp_pad_number

BG, PANEL, INSET, CONTROL = "#14191D", "#20292F", "#172126", "#34434A"
TEXT, MUTED, ACCENT, AMBER = "#EFF5F5", "#AFBFC7", "#9CE3CF", "#F1C58C"


class App(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        super().__init__()
        self.title("NI to SP · Maschine kit capture")
        self.geometry("1260x900")
        self.minsize(1120, 800)
        self.configure(fg_color=BG)
        self.data_dir = Path(os.environ.get("NI_TO_SP_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NIToSP")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.kits, self.filtered, self.visible = [], [], []
        self.kit, self.job, self.job_folder = None, None, None
        self.prepared, self.session, self.report = None, None, None
        self.probe_result, self.plan = None, None
        self.busy, self.sending, self.closing, self.closed = False, False, False, False
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.controls = []
        self.output_devices = {}
        self.preview_serial = 0
        self.preview_lock = threading.Lock()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _: self.stop_audio())
        self.after(60, self.poll)
        self.after(150, self.scan)

    def label(self, parent, text, size=13, color=TEXT, bold=False, **kw):
        return ctk.CTkLabel(parent, text=text, text_color=color,
            font=ctk.CTkFont(family="Segoe UI", size=size, weight="bold" if bold else "normal"), **kw)

    def button(self, parent, text, command, primary=False, **kw):
        widget = ctk.CTkButton(parent, text=text, command=command, height=36, corner_radius=7,
            fg_color=ACCENT if primary else CONTROL, text_color=BG if primary else TEXT,
            hover_color="#7CC9B4" if primary else "#455A63", **kw)
        self.controls.append(widget)
        return widget

    def entry(self, parent, variable, **kw):
        widget = ctk.CTkEntry(parent, textvariable=variable, **kw)
        self.controls.append(widget)
        return widget

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=24, pady=(20, 12))
        head.grid_columnconfigure(1, weight=1)
        self.label(head, "NI → SP", 28, ACCENT, True).grid(row=0, column=0, sticky="w")
        self.label(head, "Maschine kits, captured for your SP-404MKII", 15).grid(row=0, column=1, sticky="w", padx=24)
        self.button(head, "Audio output…", self.audio_settings, width=125).grid(row=0, column=2, padx=8)
        ctk.CTkButton(head, text="Stop audio", width=100, height=36, command=self.stop_audio,
                     fg_color=CONTROL, text_color=TEXT).grid(row=0, column=3)
        self.label(head, "1  Select a kit     →     2  Capture in Maschine     →     3  Review & send", 13, MUTED).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(10, 0))
        left = ctk.CTkFrame(self, fg_color=PANEL, width=300)
        left.grid(row=1, column=0, sticky="nsew", padx=(24, 14))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(5, weight=1)
        self.label(left, "INSTALLED KITS", 13, ACCENT, True).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))
        self.root_var = tk.StringVar(value="C:/Users/Public/Documents")
        self.entry(left, self.root_var).grid(row=1, column=0, sticky="ew", padx=14)
        bar = ctk.CTkFrame(left, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=14, pady=8)
        self.button(bar, "Choose folder", self.choose_root, width=124).pack(side="left")
        self.button(bar, "Scan", self.scan, width=100).pack(side="right")
        self.search_var = tk.StringVar()
        search = ctk.CTkFrame(left, fg_color="transparent")
        search.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.label(search, "Search kit or expansion", 11, MUTED).pack(anchor="w")
        self.entry(search, self.search_var).pack(fill="x")
        self.pack_var = tk.StringVar(value="All expansions")
        self.pack_menu = ctk.CTkOptionMenu(left, variable=self.pack_var, values=["All expansions"],
            fg_color=CONTROL, button_color=CONTROL, dynamic_resizing=False, command=lambda _: self.filter_kits())
        self.pack_menu.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.controls.append(self.pack_menu)
        listing = ctk.CTkFrame(left, fg_color=INSET)
        listing.grid(row=5, column=0, sticky="nsew", padx=14)
        listing.grid_columnconfigure(0, weight=1)
        listing.grid_rowconfigure(0, weight=1)
        self.kit_list = tk.Listbox(listing, bg=INSET, fg=TEXT, selectbackground=ACCENT, selectforeground=BG,
            font=("Segoe UI", 11), width=29, relief="flat", highlightthickness=1, highlightbackground=CONTROL,
            exportselection=False, activestyle="none")
        self.kit_list.grid(row=0, column=0, sticky="nsew")
        scroll = ctk.CTkScrollbar(listing, command=self.kit_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.kit_list.configure(yscrollcommand=scroll.set)
        self.kit_list.bind("<<ListboxSelect>>", self.select_kit)
        self.library_count = self.label(left, "Finding kits…", 11, MUTED, wraplength=270, justify="left")
        self.library_count.grid(row=6, column=0, sticky="ew", padx=14, pady=8)
        self.button(left, "Hear NI kit demo", self.preview_demo).grid(row=7, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.button(left, "Resume capture job…", self.resume).grid(row=8, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.button(left, "Open completed kit…", self.open_completed).grid(row=9, column=0, sticky="ew", padx=14, pady=(0, 14))
        self.search_var.trace_add("write", lambda *_: self.filter_kits())

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew", padx=(0, 24))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)
        self.kit_title = self.label(right, "Select an installed Maschine kit", 22, TEXT, True, anchor="w")
        self.kit_title.grid(row=0, column=0, sticky="ew")
        self.kit_subtitle = self.label(right, "The original kit stays in place. Synths are captured as one note per pad.", 12, MUTED, anchor="w", wraplength=750, justify="left")
        self.kit_subtitle.grid(row=1, column=0, sticky="ew", pady=(4, 12))
        self.tabs = ctk.CTkTabview(right, fg_color=PANEL, segmented_button_selected_color=CONTROL)
        self.tabs.grid(row=2, column=0, sticky="nsew")
        capture, review = self.tabs.add("Capture in Maschine"), self.tabs.add("Review & send")
        capture.grid_columnconfigure(0, weight=1)
        capture.grid_rowconfigure(2, weight=1)
        options = ctk.CTkFrame(capture, fg_color="transparent")
        options.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        self.label(options, "Space at 120 BPM", 12).pack(side="left")
        self.spacing_var = tk.StringVar(value="8 seconds")
        spacing = ctk.CTkOptionMenu(options, variable=self.spacing_var, values=["4 seconds", "8 seconds", "16 seconds", "32 seconds"], width=110,
            fg_color=CONTROL, button_color=CONTROL)
        spacing.pack(side="left", padx=(8, 20))
        self.controls.append(spacing)
        self.label(options, "BPM", 12).pack(side="left")
        self.bpm_var = tk.StringVar(value="120")
        self.entry(options, self.bpm_var, width=48).pack(side="left", padx=(4, 12))
        self.label(options, "Note hold").pack(side="left")
        self.hold_var = tk.StringVar(value="0.5")
        self.entry(options, self.hold_var, width=55).pack(side="left", padx=8)
        self.label(options, "sec").pack(side="left")
        self.velocity_var = tk.StringVar(value="100")
        self.label(options, "Velocity").pack(side="left", padx=(16, 4))
        self.entry(options, self.velocity_var, width=55).pack(side="left")
        capture_actions = ctk.CTkFrame(capture, fg_color="transparent")
        capture_actions.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.button(capture_actions, "Create capture job", self.create_job, primary=True, width=170).pack(side="left")
        self.button(capture_actions, "Open Maschine", self.open_maschine, width=140).pack(side="left", padx=8)
        self.button(capture_actions, "Copy kit path", self.copy_kit_path, width=125).pack(side="left")
        self.guide = ctk.CTkTextbox(capture, wrap="word", font=ctk.CTkFont(family="Segoe UI", size=13), fg_color=INSET)
        self.guide.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.set_text(self.guide, "Create a capture job for the selected kit.\n\nThe app makes a MIDI pattern that plays each of the 16 Maschine pads separately. You import it into a new pattern and export the master audio in Maschine.\n\nThat render includes synths and processing. NI to SP checks it, separates the 16 hits and prepares the SP transfer.\n\nThis first version assists the Maschine export step; it does not silently operate or render the Maschine engine for you.")
        row = ctk.CTkFrame(capture, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.button(row, "Open capture folder", self.open_capture, width=170).pack(side="left")
        self.button(row, "Copy MIDI path", self.copy_midi_path, width=145).pack(side="left", padx=8)
        self.button(row, "Continue to render review →", lambda: self.tabs.set("Review & send"), width=240).pack(side="right")

        review.grid_columnconfigure(0, weight=1)
        review.grid_rowconfigure(3, weight=1)
        file_row = ctk.CTkFrame(review, fg_color="transparent")
        file_row.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
        file_row.grid_columnconfigure(0, weight=1)
        self.render_var = tk.StringVar()
        self.entry(file_row, self.render_var, placeholder_text="Full master WAV exported from Maschine…").grid(row=0, column=0, sticky="ew")
        self.button(file_row, "Choose render…", self.choose_render, width=140).grid(row=0, column=1, padx=(8, 0))
        self.confirm_var = tk.BooleanVar(value=False)
        confirm = ctk.CTkCheckBox(review, text="I rendered this kit and capture pattern at the specified BPM, from time zero.", variable=self.confirm_var,
                                   command=self.invalidate_prepared, font=ctk.CTkFont(size=12))
        confirm.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))
        self.controls.append(confirm)
        row = ctk.CTkFrame(review, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.label(row, "Intentionally empty NI pads", 12, MUTED).pack(side="left")
        self.empty_var = tk.StringVar()
        self.entry(row, self.empty_var, width=110, placeholder_text="e.g. 4, 12").pack(side="left", padx=8)
        self.button(row, "Check & prepare kit", self.check_render, primary=True, width=180).pack(side="right")
        grid = ctk.CTkFrame(review, fg_color=INSET)
        grid.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 8))
        self.pad_buttons = {}
        for row in range(4):
            grid.grid_rowconfigure(row, weight=1)
            for column in range(4):
                grid.grid_columnconfigure(column, weight=1)
                local = (3 - row) * 4 + column
                button = self.button(grid, f"SP {sp_pad_number(local):02d}  /  NI {local + 1:02d}\nNo capture", lambda i=local: self.preview_pad(i), width=120)
                button.grid(row=row, column=column, sticky="nsew", padx=4, pady=4)
                self.pad_buttons[local] = button
        self.quality = self.label(review, "Capture all 16 pads, then check the render. Nothing is sent automatically.", 12, MUTED, wraplength=760, justify="left")
        self.quality.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 8))
        row = ctk.CTkFrame(review, fg_color="transparent")
        row.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 12))
        self.button(row, "Open prepared WAVs", self.open_prepared, width=175).pack(side="left")
        self.report_button = self.button(row, "View check report", self.show_check_report, width=155)
        self.report_button.pack(side="left", padx=8)
        self.send_open_button = self.button(row, "Review SP destination…", self.transfer_dialog, primary=True, width=225)
        self.send_open_button.pack(side="right")
        self.render_var.trace_add("write", lambda *_: self.invalidate_prepared())
        self.empty_var.trace_add("write", lambda *_: self.invalidate_prepared())
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=24, pady=12)
        footer.grid_columnconfigure(0, weight=1)
        self.status = self.label(footer, "Starting…", 12, MUTED, anchor="w", wraplength=1000)
        self.status.grid(row=0, column=0, sticky="ew")
        self.cancel_button = ctk.CTkButton(footer, text="Cancel", width=140, height=30, state="disabled", fg_color=CONTROL, command=self.request_cancel)
        self.cancel_button.grid(row=0, column=1, padx=(10, 0))
        self.progress = ctk.CTkProgressBar(footer, height=4, progress_color=ACCENT)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.progress.set(0)
        self.sync()

    def set_text(self, widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def set_status(self, text):
        self.status.configure(text=text)

    def sync(self):
        self.send_open_button.configure(state="normal" if self.prepared and not self.busy else "disabled")
        self.report_button.configure(state="normal" if self.report and not self.busy else "disabled")
        for index, button in self.pad_buttons.items():
            button.configure(state="normal" if self.prepared and not self.busy else "disabled")

    def submit(self, work, done, sending=False, failed=None):
        if self.busy:
            return
        # Dispose of unreachable Tk fonts/widgets on their owning UI thread.
        # Otherwise cyclic GC can finalize an old window in this job's worker.
        gc.collect()
        self.busy, self.sending = True, sending
        self.cancel.clear()
        for widget in self.controls:
            widget.configure(state="disabled")
        self.kit_list.configure(state="disabled")
        self.cancel_button.configure(state="normal", text="Stop after pad" if sending else "Cancel")
        self.sync()
        def worker():
            try:
                self.events.put(("done", (done, work())))
            except Exception as exc:
                self.events.put(("error", (exc, failed)))
        threading.Thread(target=worker, daemon=True).start()

    def notify(self, text, value=0):
        self.events.put(("progress", (text, value)))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.set_status(value[0])
                    self.progress.set(value[1])
                elif kind in ("done", "error"):
                    self.busy = self.sending = False
                    for widget in self.controls:
                        widget.configure(state="normal")
                    self.kit_list.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    if self.closing:
                        self.finish_close()
                        return
                    if kind == "done":
                        value[0](value[1])
                    else:
                        error, failed = value
                        if failed:
                            failed(error)
                        self.set_status(str(error))
                        if not self.closing and not isinstance(error, InterruptedError):
                            details = getattr(error, "report", {})
                            journal = details.get("journal")
                            message = str(error) + (f"\n\nTransfer record: {journal}" if journal else "")
                            messagebox.showerror("NI to SP", message, parent=self)
                    self.sync()
                    if self.closing:
                        self.finish_close()
                        return
                elif kind == "audio_error":
                    self.set_status(value)
                elif kind == "audio_ready":
                    self.accept_audio(value)
        except queue.Empty:
            pass
        except Exception as exc:
            self.set_status("Could not finish this step: " + str(exc))
        if not self.closed:
            self.after(60, self.poll)

    def request_cancel(self):
        self.cancel.set()
        self.set_status("Stopping after the current pad. Completed writes remain in the transfer journal." if self.sending else "Cancelling…")

    def choose_root(self):
        path = filedialog.askdirectory(parent=self, title="Folder containing installed NI expansions")
        if path:
            self.root_var.set(path)
            self.scan()

    def scan(self):
        if self.busy:
            return
        root = self.root_var.get()
        def done(kits):
            self.kits = kits
            self.pack_menu.configure(values=["All expansions", *sorted({kit["pack"] for kit in kits})])
            self.pack_var.set("All expansions")
            self.filter_kits()
            self.set_status(f"Found {len(kits):,} Maschine kits. Select one to create its capture job.")
        self.submit(lambda: discover_kits(root, self.cancel), done)

    def filter_kits(self):
        if self.busy:
            return
        query, pack = self.search_var.get().strip().casefold(), self.pack_var.get()
        self.filtered = [kit for kit in self.kits if (pack == "All expansions" or kit["pack"] == pack)
                         and query in (kit["name"] + " " + kit["pack"]).casefold()]
        self.visible = self.filtered[:300]
        self.kit_list.delete(0, "end")
        for kit in self.visible:
            self.kit_list.insert("end", kit["name"] + "  ·  " + kit["pack"])
        self.library_count.configure(text=f"{len(self.filtered):,} kits match" + (" · first 300 shown; refine your search" if len(self.filtered) > 300 else ""))

    def select_kit(self, _event=None):
        if self.busy or not self.kit_list.curselection():
            return
        kit = self.visible[self.kit_list.curselection()[0]]
        if self.kit and kit["id"] == self.kit["id"]:
            return
        self.stop_audio()
        self.kit, self.job, self.job_folder = dict(kit), None, None
        self.render_var.set("")
        self.confirm_var.set(False)
        self.empty_var.set("")
        self.invalidate_prepared()
        self.kit_title.configure(text=kit["name"])
        self.kit_subtitle.configure(text=kit["pack"] + " · Original 16-pad positions · Synths captured at one note")
        self.tabs.set("Capture in Maschine")
        self.set_text(self.guide, "Create a capture job to show the exact Maschine steps for this kit.\n\nThe existing kit and your CrateBuilder library will not be changed.")

    def invalidate_prepared(self):
        if self.busy:
            return
        self.prepared = self.session = self.report = self.plan = self.probe_result = None
        self.quality.configure(text="Capture all 16 pads, then check the render. Nothing is sent automatically.", text_color=MUTED)
        for index, button in self.pad_buttons.items():
            button.configure(text=f"SP {sp_pad_number(index):02d}  /  NI {index + 1:02d}\nNo checked capture", fg_color=CONTROL)
        self.sync()

    def create_job(self):
        if not self.kit:
            self.set_status("Select an installed kit first.")
            return
        try:
            spacing, hold, velocity, bpm = int(self.spacing_var.get().split()[0]), float(self.hold_var.get()), int(self.velocity_var.get()), int(self.bpm_var.get())
        except ValueError:
            self.set_status("Enter a valid note hold in seconds and velocity 1–127.")
            return
        kit = dict(self.kit)
        def done(result):
            self.job_folder, self.job = result
            self.invalidate_prepared()
            self.confirm_var.set(False)
            self.set_text(self.guide, render_instructions(self.job))
            self.set_status(f"Capture job created: {self.job_folder}. Follow the Maschine steps above.")
        self.submit(lambda: create_capture(kit, self.data_dir / "captures", spacing, hold, velocity, bpm), done)

    def resume(self):
        path = filedialog.askopenfilename(parent=self, title="Resume an NI to SP capture", filetypes=[("Capture job", "CAPTURE.json")])
        if not path:
            return
        try:
            job = load_capture(Path(path).parent)
            self.kit, self.job, self.job_folder = job["kit"], job, Path(path).parent
            self.render_var.set("")
            self.confirm_var.set(False)
            self.empty_var.set("")
            self.invalidate_prepared()
            self.kit_title.configure(text=self.kit["name"])
            self.kit_subtitle.configure(text=self.kit["pack"] + " · Resumed capture job")
            self.set_text(self.guide, render_instructions(job))
            self.tabs.set("Capture in Maschine")
        except (OSError, ValueError, KeyError) as exc:
            self.set_status(str(exc))

    def copy_kit_path(self):
        if self.kit:
            self.clipboard_clear()
            self.clipboard_append(self.kit["path"])
            self.set_status("Kit path copied. Paste it into Maschine's Group → Open dialog.")

    def open_completed(self):
        if self.busy:
            return
        path = filedialog.askdirectory(parent=self, title="Completed NI to SP kit folder", initialdir=self.data_dir / "prepared")
        if not path:
            return
        def done(result):
            session, recipe = result
            self.stop_audio()
            self.render_var.set("")
            self.empty_var.set("")
            self.confirm_var.set(False)
            self.invalidate_prepared()
            self.kit = recipe["capture_job"]["kit"]
            self.job, self.job_folder = None, None
            self.prepared, self.session = Path(path), session
            self.report = recipe.get("capture_report")
            self.kit_title.configure(text=self.kit["name"])
            self.kit_subtitle.configure(text=self.kit["pack"] + " · Completed capture")
            self.set_text(self.guide, "Completed kit opened and its 16 WAV hashes verified.\n\nAudition the pads in Review & send, then choose an SP destination.\n\nTo record another version, create a new capture job.")
            for index, button in self.pad_buttons.items():
                empty = recipe["pads"][index].get("intentional_empty", False)
                button.configure(text=f"SP {sp_pad_number(index):02d}  /  NI {index + 1:02d}\n" +
                    ("Intentional silence" if empty else "Audition capture"), fg_color="#315348")
            self.quality.configure(text="Completed kit verified. Audition the pads and review the destination before sending.", text_color=ACCENT)
            self.tabs.set("Review & send")
            self.set_status("Completed kit opened. Original render is not needed for transfer.")
        self.submit(lambda: load_prepared(path), done)

    def copy_midi_path(self):
        if self.job_folder:
            self.clipboard_clear()
            self.clipboard_append(str(self.job_folder / "CAPTURE.mid"))
            self.set_status("MIDI path copied. Paste it into Maschine's Group → Import MIDI dialog.")

    def open_capture(self):
        if self.job_folder:
            os.startfile(self.job_folder)

    def open_prepared(self):
        if self.prepared:
            os.startfile(self.prepared)

    def open_maschine(self):
        path = Path("C:/Program Files/Native Instruments/Maschine 2/Maschine 2.exe")
        if not path.is_file():
            chosen = filedialog.askopenfilename(parent=self, title="Choose the installed Maschine application", filetypes=[("Application", "*.exe")])
            if not chosen:
                return
            path = Path(chosen)
        subprocess.Popen([str(path)])
        self.set_status("Maschine opened. Load this kit in a new project; use Copy kit path.")

    def choose_render(self):
        path = filedialog.askopenfilename(parent=self, title="Full master render exported from Maschine", initialdir=self.job_folder or self.data_dir,
            filetypes=[("Audio render", "*.wav *.aif *.aiff *.flac")])
        if path:
            self.render_var.set(path)

    def check_render(self):
        if not self.job:
            self.set_status("Create or resume a capture job first.")
            return
        try:
            empty = tuple(int(part.strip()) for part in self.empty_var.get().split(",") if part.strip())
        except ValueError:
            self.set_status("Enter empty Maschine pad numbers separated by commas, e.g. 4, 12.")
            return
        job, path, confirmed = json.loads(json.dumps(self.job)), self.render_var.get(), self.confirm_var.get()
        self.invalidate_prepared()
        def work():
            report = inspect_render(job, path, empty, confirmed, self.notify, self.cancel)
            prepared = prepare_kit(job, report, self.data_dir / "prepared", self.notify, self.cancel) if report["ok"] else None
            return report, prepared
        def done(result):
            report, prepared = result
            self.report = report
            if prepared:
                self.prepared, self.session = prepared
            for index, row in enumerate(report["pads"]):
                self.pad_buttons[index].configure(text=f"SP {row['sp_pad']:02d}  /  NI {index + 1:02d}\n" +
                    ("Needs attention" if row["issues"] else "Intentional silence" if row["intentional_empty"] else "Audition capture"),
                    fg_color="#5A4232" if row["issues"] else "#315348")
            if report["ok"]:
                self.quality.configure(text="16 verified WAVs ready. Click every pad to audition. Intentional empty pads become silence on the SP.", text_color=ACCENT)
                self.set_status("Kit prepared. Review the SP project, bank and pad map before sending.")
            else:
                self.quality.configure(text=f"{len(report['problems'])} capture issues. Open the report to see every affected pad.", text_color=AMBER)
                messagebox.showwarning("Capture needs attention", "\n\n".join(report["problems"]), parent=self)
            (self.job_folder / "LAST_RENDER_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        self.submit(work, done)

    def stop_audio(self):
        self.preview_serial += 1
        sd.stop()

    def show_check_report(self):
        if not self.report:
            return
        window = ctk.CTkToplevel(self)
        window.title("Capture check report")
        window.geometry("720x560")
        window.transient(self)
        text = ctk.CTkTextbox(window, wrap="word", font=ctk.CTkFont(size=13))
        text.pack(fill="both", expand=True, padx=16, pady=16)
        lines = ["Captured kit: " + self.kit["name"], "", "Kit identity and capture timing were confirmed by you; the app does not acoustically identify the original kit.", ""]
        for row in self.report["pads"]:
            lines.append(f"NI {row['ni_pad']:02d} → SP {row['sp_pad']:02d}: " +
                         ("\n".join(row["issues"]) if row["issues"] else "Intentional silence" if row["intentional_empty"] else "Ready"))
            lines.append("")
        self.set_text(text, "\n".join(lines))

    def preview_demo(self):
        if not self.kit or self.busy:
            return
        path = self.kit.get("preview")
        if not path:
            self.set_status("This kit has no supplied NI demo preview.")
            return
        self.play_file(path, limit=20)
        self.set_status("Playing the supplied NI demo, up to 20 seconds. This is a kit demonstration, not your capture.")

    def preview_pad(self, index):
        if not self.session or self.busy:
            return
        sample = self.session.by_id[self.session.pads[index].sample_id]
        self.play_file(sample.path)

    def play_file(self, path, limit=None):
        self.preview_serial += 1
        serial = self.preview_serial
        device = getattr(self, "output_device", None)
        def worker():
            try:
                with self.preview_lock:
                    with sf.SoundFile(path) as audio:
                        rate = audio.samplerate
                        data = audio.read(round(limit * rate) if limit else -1, dtype="float32", always_2d=True)
                    if data.size == 0 or data.shape[1] not in (1, 2) or not np.isfinite(data).all():
                        raise ValueError("Preview requires valid mono/stereo audio.")
                    if data.shape[1] == 1:
                        data = np.repeat(data, 2, axis=1)
                    if device is not None:
                        info = sd.query_devices(device, "output")
                        new_rate = int(info["default_samplerate"])
                        if new_rate != rate:
                            divisor = gcd(new_rate, rate)
                            data = resample_poly(data, new_rate // divisor, rate // divisor, axis=0)
                            rate = new_rate
                        if info["max_output_channels"] == 4:
                            data = np.pad(data, ((0, 0), (0, 2)))
                    if serial == self.preview_serial and not self.closed:
                        self.events.put(("audio_ready", (serial, data, rate, device)))
            except Exception as exc:
                self.events.put(("audio_error", "Preview unavailable: " + str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def accept_audio(self, payload):
        serial, data, rate, device = payload
        if serial == self.preview_serial and not self.closed:
            try:
                sd.play(data * .65, rate, device=device)
            except Exception as exc:
                self.set_status("Preview unavailable: " + str(exc))

    def audio_settings(self):
        def done(outputs):
            window = ctk.CTkToplevel(self)
            window.title("Preview audio output")
            window.geometry("640x210")
            window.transient(self)
            window.grab_set()
            self.label(window, "Choose where you hear the captured pads", 18, TEXT, True).pack(anchor="w", padx=20, pady=16)
            mapping = {"System default": None}
            for output in outputs:
                label = audio_output_label(output)
                if label in mapping:
                    label += f" · device {output['index']}"
                mapping[label] = output["index"]
            current = next((name for name, value in mapping.items() if value == getattr(self, "output_device", None)), "System default")
            variable = tk.StringVar(value=current)
            ctk.CTkOptionMenu(window, variable=variable, values=list(mapping), width=590, dynamic_resizing=False).pack(padx=20, fill="x")
            def apply():
                self.stop_audio()
                self.output_device = mapping[variable.get()]
                window.destroy()
            ctk.CTkButton(window, text="Use output", command=apply, fg_color=ACCENT, text_color=BG).pack(pady=18)
        self.submit(simple_audio_outputs, done)

    def transfer_dialog(self):
        if self.busy or not self.prepared:
            return
        session, recipe = load_prepared(self.prepared)
        window = ctk.CTkToplevel(self)
        window.title("Send NI kit to SP · destination review")
        window.geometry("820x790")
        window.minsize(740, 650)
        window.transient(self)
        window.grab_set()
        form = ctk.CTkScrollableFrame(window, fg_color=PANEL)
        form.pack(fill="both", expand=True, padx=16, pady=16)
        name = recipe["capture_job"]["kit"]["name"]
        self.label(form, name, 22, ACCENT, True).pack(anchor="w", padx=12, pady=12)
        self.label(form, "16 pad positions · physical Maschine layout · 48 kHz / 16-bit WAV", 12, MUTED).pack(anchor="w", padx=12)
        bank = tk.StringVar(value="Choose bank")
        port = tk.StringVar(value="Automatic")
        row = ctk.CTkFrame(form, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=12)
        ports = ctk.CTkOptionMenu(row, variable=port, values=["Automatic"], width=150)
        ports.pack(side="left")
        state = {"probe": None, "plan": None, "sending": False}
        self.label(form, "Backup folder (occupied pads are backed up before replacement)", 12, MUTED).pack(anchor="w", padx=12)
        backup = tk.StringVar(value=str(self.data_dir / "SP-backups"))
        backup_entry = ctk.CTkEntry(form, textvariable=backup)
        backup_entry.pack(fill="x", padx=12, pady=(4, 10))
        view = ctk.CTkTextbox(form, height=280, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        view.pack(fill="x", padx=12, pady=8)
        self.set_text(view, "Check the connected SP, then choose a destination bank. No samples are written during checking.")
        reviewed, overwrite = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        review_check = ctk.CTkCheckBox(form, text="I checked the SP project, bank and all 16 pad assignments", variable=reviewed)
        review_check.pack(anchor="w", padx=12, pady=8)
        overwrite_check = ctk.CTkCheckBox(form, text="Allow replacing occupied pads after backup", variable=overwrite)
        overwrite_check.pack(anchor="w", padx=12, pady=8)
        note = self.label(form, "Samples only: SP choke groups, gate/loop settings and live synthesis are not configured.", 12, AMBER, wraplength=650, justify="left")
        note.pack(anchor="w", padx=12, pady=10)
        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.pack(fill="x", padx=12, pady=12)
        cli = sp_transfer.discover_cli()
        widgets = [ports, backup_entry, review_check, overwrite_check]
        def update(*_):
            plan = state["plan"]
            enabled = bool(plan and reviewed.get() and (not plan.overwrites or overwrite.get() and backup.get().strip()) and not self.busy and not state["sending"])
            send_button.configure(state="normal" if enabled else "disabled")
        def plan_changed(*_):
            reviewed.set(False)
            overwrite.set(False)
            state["plan"] = None
            if state["probe"] and bank.get() in tuple("ABCDEFGHIJ"):
                try:
                    plan = sp_transfer.make_transfer_plan(session, 0, bank.get(), state["probe"])
                    state["plan"] = plan
                    lines = [f"PROJECT {plan.project}: {plan.project_name}", f"BANK {plan.bank}  ·  {plan.port}", ""]
                    for entry in sorted(plan.entries, key=lambda e: e.pad_number):
                        lines.append(f"{entry.target_pad:4} ← NI {entry.logical_slot + 1:02d}" +
                                     (f"  REPLACES {entry.previous_name}" if entry.occupied else "  empty"))
                    lines.append(f"\n16 pads sent; {len(plan.overwrites)} occupied pads replaced.")
                    self.set_text(view, "\n".join(lines))
                    send_button.configure(text=f"Send 16 pads to bank {plan.bank}")
                except Exception as exc:
                    self.set_text(view, str(exc))
            update()
        banks = ctk.CTkOptionMenu(row, variable=bank, values=["Choose bank", *"ABCDEFGHIJ"], width=150, command=plan_changed)
        banks.pack(side="right")
        widgets.append(banks)
        def check():
            if self.busy:
                return
            if not cli:
                self.set_text(view, "Bundled Sparky USB helper is missing.")
                return
            state["plan"] = state["probe"] = None
            reviewed.set(False)
            update()
            selected_port = None if port.get() == "Automatic" else port.get()
            requested_port = port.get()
            def work():
                found = sp_transfer.list_ports(cli)
                if selected_port is None and len(found) > 1:
                    return found, None, "More than one SP is connected. Select its USB port, then check again."
                try:
                    return found, sp_transfer.probe(cli, selected_port), None
                except sp_transfer.TransferError as exc:
                    return found, None, str(exc)
            def done(result):
                if not window.winfo_exists():
                    return
                if port.get() != requested_port:
                    self.set_text(view, "Connection changed during checking. Check the SP again.")
                    return
                found, state["probe"], error = result
                ports.configure(values=["Automatic", *[p["port"] for p in found]])
                if error:
                    self.set_text(view, error)
                    update()
                    return
                self.set_text(view, f"Connected to project {state['probe']['project']}: {state['probe']['project_name']}. Choose a bank.")
                plan_changed()
            self.submit(work, done)
        def port_changed(*_):
            state["probe"] = state["plan"] = None
            reviewed.set(False)
            overwrite.set(False)
            self.set_text(view, "Connection changed. Check the SP again.")
            update()
        port.trace_add("write", port_changed)
        def send():
            if self.busy or not state["plan"] or not reviewed.get():
                return
            plan = state["plan"]
            state["sending"] = True
            update()
            folder, allow, backup_folder = self.prepared, overwrite.get(), backup.get().strip()
            for widget in widgets:
                widget.configure(state="disabled")
            def work():
                return sp_transfer.send(plan, cli, folder, reviewed=True, allow_overwrite=allow,
                    backup_dir=backup_folder or None, progress=self.notify, cancel=self.cancel)
            def done(report):
                self.set_status(f"{len(report['completed_pads'])} pads imported. Transfer record: {report['journal']}")
                if window.winfo_exists():
                    window.destroy()
            def failed(error):
                state.update(sending=False, probe=None, plan=None)
                if window.winfo_exists():
                    for widget in widgets:
                        widget.configure(state="normal")
                    reviewed.set(False)
                    overwrite.set(False)
                    self.set_text(view, str(error) + "\n\nCheck the SP again before retrying. Completed pads are recorded in the transfer journal.")
                    update()
            self.submit(work, done, sending=True, failed=failed)
        ctk.CTkButton(actions, text="Check connected SP", command=check, height=36).pack(side="left")
        send_button = ctk.CTkButton(actions, text="Send to SP", command=send, height=36, fg_color=ACCENT, text_color=BG, state="disabled")
        send_button.pack(side="right")
        for variable in (reviewed, overwrite, backup):
            variable.trace_add("write", update)
        def close_window():
            if state["sending"] and self.busy:
                self.request_cancel()
                return
            window.destroy()
        window.protocol("WM_DELETE_WINDOW", close_window)

    def close(self):
        self.stop_audio()
        if self.busy:
            self.closing = True
            self.request_cancel()
            return
        self.finish_close()

    def finish_close(self):
        self.closed = True
        for timer in self.tk.call("after", "info"):
            self.tk.call("after", "cancel", timer)
        self.destroy()
