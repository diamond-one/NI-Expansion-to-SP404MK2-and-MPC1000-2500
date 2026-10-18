"""The current workbench's reviewed SP destination and transfer workspace."""
from pathlib import Path
import queue
import tkinter as tk
from types import SimpleNamespace
import customtkinter as ctk

from . import desk_theme as T
from . import sp_transfer
from .capture import load_prepared


class TransferDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.session, recipe = load_prepared(app.prepared)
        self.export_folder = Path(app.prepared)
        self.cli = sp_transfer.discover_cli()
        self.probe_result, self.plan = None, None
        self.checking = self.sending = self.manual_bank = False
        self.bank = tk.StringVar(value='')
        self.port = tk.StringVar(value='')
        self.replace = tk.BooleanVar(value=False)
        self.backup_folder = app.data_dir / 'SP-backups'
        self._button_owner = SimpleNamespace(controls=[])
        self._progress_events = queue.Queue()
        self._poll_id = self._auto_id = None
        self.title('Send to SP-404MKII')
        self.geometry('850x850')
        self.minsize(760, 680)
        self.configure(fg_color=T.WORKSPACE)
        self.transient(app)
        self.grab_set()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        heading = ctk.CTkFrame(self, fg_color=T.HEADER, corner_radius=0)
        heading.grid(row=0, column=0, sticky='ew')
        heading.grid_columnconfigure(0, weight=1)
        T.label(heading, 'Send to SP-404MKII', 26, T.ON_DARK, True, True,
            anchor='w').grid(row=0, column=0, sticky='ew', padx=22, pady=(17, 2))
        name = recipe['capture_job']['kit']['name']
        T.label(heading, name, 14, T.ON_DARK, anchor='w', wraplength=710).grid(
            row=1, column=0, sticky='ew', padx=22, pady=(0, 16))
        body = ctk.CTkScrollableFrame(self, fg_color=T.WORKSPACE, corner_radius=0,
            scrollbar_button_color=T.LINE, scrollbar_button_hover_color=T.ACTION)
        body.grid(row=1, column=0, sticky='nsew', padx=16, pady=(14, 0))
        body.grid_columnconfigure(0, weight=1)
        self.connection = T.label(body, 'Finding your connected SP…', 15, T.INK, True,
            anchor='w', wraplength=520, justify='left')
        self.connection.grid(row=0, column=0, sticky='ew', padx=(0, 132), pady=(0, 10))
        self.refresh_button = self.button(body, 'Refresh SP', self.check, width=120)
        self.refresh_button.grid(row=0, column=0, sticky='e', pady=(0, 10))
        self.device_row = ctk.CTkFrame(body, fg_color='transparent')
        self.device_row.grid(row=1, column=0, sticky='ew', pady=(0, 12))
        T.label(self.device_row, 'Connected SP', 14, bold=True).pack(side='left', padx=(0, 12))
        self.device_menu = ctk.CTkOptionMenu(self.device_row, values=['Choose an SP'],
            variable=self.port, command=self.device_changed, width=230, height=36,
            fg_color=T.PAPER, button_color=T.SOFT, text_color=T.INK, corner_radius=0,
            font=T.font(14), dropdown_fg_color=T.PAPER, dropdown_text_color=T.INK)
        self.device_menu.pack(side='left')
        self.device_row.grid_remove()
        destination = ctk.CTkFrame(body, fg_color=T.PAPER, corner_radius=0,
            border_width=2, border_color=T.LINE)
        destination.grid(row=2, column=0, sticky='ew', pady=(0, 14))
        destination.grid_columnconfigure(0, weight=1)
        T.label(destination, 'Destination bank', 20, T.INK, True, True, anchor='w').grid(
            row=0, column=0, sticky='ew', padx=16, pady=(12, 8))
        self.bank_selector = ctk.CTkSegmentedButton(destination, values=list(sp_transfer.BANKS),
            variable=self.bank, command=self.bank_changed, width=650, height=36,
            dynamic_resizing=False, corner_radius=0, border_width=2, font=T.font(15, True),
            fg_color=T.LINE, selected_color=T.SELECTED, selected_hover_color=T.SELECTED,
            unselected_color=T.PAPER, unselected_hover_color=T.SOFT, text_color=T.INK)
        self.bank_selector.grid(row=1, column=0, sticky='ew', padx=16)
        self.bank_hint = T.label(destination, 'The bank selected on your SP will be used when detected.',
            13, T.MUTED, wraplength=650, justify='left', anchor='w')
        self.bank_hint.grid(row=2, column=0, sticky='ew', padx=16, pady=(8, 12))
        self.map_heading = T.label(body, '16 pad positions', 19, T.INK, True, True, anchor='w')
        self.map_heading.grid(row=3, column=0, sticky='ew', pady=(0, 8))
        grid = ctk.CTkFrame(body, fg_color='transparent')
        grid.grid(row=4, column=0, sticky='ew')
        self.pad_cells = {}
        for n in range(1, 17):
            r, c = divmod(n - 1, 4)
            grid.grid_columnconfigure(c, weight=1, uniform='sp-pads')
            cell = ctk.CTkFrame(grid, fg_color=T.PAPER, corner_radius=0,
                border_width=2, border_color=T.LINE, height=74)
            cell.grid(row=r, column=c, sticky='nsew', padx=(0, 6), pady=(0, 6))
            cell.grid_propagate(False)
            cell.grid_columnconfigure(0, weight=1)
            title = T.label(cell, f'—{n:02}', 13, T.INK, True, anchor='w')
            title.grid(row=0, column=0, sticky='ew', padx=9, pady=(5, 0))
            sound = T.label(cell, 'Waiting for SP', 12, T.MUTED, anchor='w',
                wraplength=144, justify='left', height=34)
            sound.grid(row=1, column=0, sticky='ew', padx=9, pady=(0, 5))
            self.pad_cells[n] = cell, title, sound
        policy = ctk.CTkFrame(body, fg_color=T.PAPER, corner_radius=0,
            border_width=2, border_color=T.LINE)
        policy.grid(row=5, column=0, sticky='ew', pady=(8, 10))
        policy.grid_columnconfigure(0, weight=1)
        self.replace_switch = ctk.CTkSwitch(policy, text='Replace existing pads',
            variable=self.replace, command=self.update_ready, font=T.font(15, True),
            text_color=T.INK, progress_color=T.ACTION)
        self.replace_switch.grid(row=0, column=0, sticky='w', padx=16, pady=(13, 6))
        self.policy_hint = T.label(policy, 'Off: existing sounds are protected. On: replace them after an automatic backup.',
            13, T.MUTED, wraplength=650, justify='left', anchor='w')
        self.policy_hint.grid(row=1, column=0, sticky='ew', padx=16, pady=(0, 12))
        T.label(body, 'Samples only. SP effects, choke groups and gate/loop settings are not configured.',
            12, T.MUTED, wraplength=660, justify='left', anchor='w').grid(
            row=6, column=0, sticky='ew', pady=(0, 12))
        footer = ctk.CTkFrame(self, fg_color=T.PAPER, corner_radius=0,
            border_width=2, border_color=T.LINE)
        footer.grid(row=2, column=0, sticky='ew', padx=16, pady=14)
        footer.grid_columnconfigure(0, weight=1)
        self.message = T.label(footer, 'Checking the SP does not import samples.', 13,
            T.MUTED, wraplength=760, justify='left', anchor='w')
        self.message.grid(row=0, column=0, columnspan=2, sticky='ew', padx=14, pady=(10, 6))
        self.close_button = self.button(footer, 'Cancel', self.close, width=104)
        self.close_button.grid(row=1, column=0, sticky='w', padx=14, pady=(0, 12))
        self.send_button = self.button(footer, 'Send to SP', self.send, primary=True, width=250)
        self.send_button.grid(row=1, column=1, sticky='e', padx=14, pady=(0, 12))
        self.progress = ctk.CTkProgressBar(footer, height=4, corner_radius=0,
            fg_color=T.SOFT, progress_color=T.ACTION)
        self.progress.grid(row=2, column=0, columnspan=2, sticky='ew', padx=2, pady=(0, 2))
        self.progress.set(0)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.update_ready()
        self._auto_id = self.after(100, self.check)
        self._poll_id = self.after(80, self.poll_progress)

    def button(self, parent, text, command, **kwargs):
        return T.button(self._button_owner, parent, text, command, **kwargs)

    def poll_progress(self):
        self._poll_id = None
        try:
            while True:
                message, amount = self._progress_events.get_nowait()
                self.message.configure(text=message)
                self.progress.set(amount)
        except queue.Empty:
            pass
        self._poll_id = self.after(80, self.poll_progress)

    def notify(self, message, amount=0):
        self._progress_events.put((message, amount))
        self.app.notify(message, amount)

    def update_ready(self):
        locked = self.checking or self.sending or self.app.busy
        for widget in (self.bank_selector, self.device_menu, self.replace_switch, self.refresh_button):
            widget.configure(state='disabled' if locked else 'normal')
        allowed = self.plan is not None and (not self.plan.overwrites or self.replace.get())
        ready = allowed and not locked
        self.send_button.configure(state='normal' if ready else 'disabled',
            fg_color=T.ACTION if ready else T.SOFT, text_color=T.ON_ACTION if ready else T.MUTED)
        self.close_button.configure(text='Stop after this pad' if self.sending else 'Close' if self.progress.get() == 1 else 'Cancel')
        if self.plan and not self.checking and not self.sending:
            for entry in self.plan.entries:
                cell, title, _ = self.pad_cells[entry.pad_number]
                occupied = entry.occupied
                title.configure(text=f'{entry.target_pad} · ' + (
                    'replace' if occupied and self.replace.get() else 'occupied' if occupied else 'empty'))
                cell.configure(fg_color=T.SELECTED if occupied and self.replace.get() else T.MISSING if occupied else T.PAPER)
            count = len(self.plan.overwrites)
            self.send_button.configure(text=f'Send 16 pads to bank {self.plan.bank}')
            if count and not self.replace.get():
                text = f'{count} existing sounds in bank {self.plan.bank}. Enable Replace existing pads, or choose another bank.'
            elif count:
                text = f'{count} existing sounds will be replaced. A recovery backup is saved automatically before writing.'
            else:
                text = f'Bank {self.plan.bank} is empty. Ready to send all 16 pad positions.'
            self.message.configure(text=text)
        elif not self.sending:
            self.send_button.configure(text='Send to SP')

    def bank_changed(self, _=None):
        self.manual_bank = True
        self.build_plan()

    def device_changed(self, _=None):
        self.manual_bank = False
        self.bank.set('')
        self.probe_result = self.plan = None
        self.replace.set(False)
        self.check()

    def build_plan(self):
        self.plan = None
        self.replace.set(False)
        if self.probe_result and self.bank.get() in sp_transfer.BANKS and self.bank.get():
            try:
                self.plan = sp_transfer.make_transfer_plan(self.session, 0, self.bank.get(), self.probe_result)
            except (sp_transfer.TransferError, ValueError) as exc:
                self.message.configure(text=str(exc))
        selected = self.probe_result.get('current_bank') if self.probe_result else None
        hint = (f'Using the bank selected on your SP: {selected}. Choose another bank above if needed.'
                if selected and self.bank.get() == selected else
                f'You selected bank {self.bank.get()}. The SP is currently on bank {selected}.' if selected else
                'Current SP bank could not be detected. Choose the destination above.')
        self.bank_hint.configure(text=hint)
        entries = {e.pad_number: e for e in self.plan.entries} if self.plan else {}
        for n, (cell, title, sound) in self.pad_cells.items():
            entry = entries.get(n)
            cell.configure(fg_color=T.MISSING if entry and entry.occupied else T.PAPER)
            title.configure(text=(f'{entry.target_pad} · replace' if entry.occupied else f'{entry.target_pad} · empty')
                if entry else (f'{self.bank.get()}{n:02}' if self.bank.get() else f'Pad {n:02}'))
            name = entry.name if entry else 'No destination checked'
            if len(name) > 34:
                name = name[:33] + '…'
            sound.configure(text=name, text_color=T.INK if entry else T.MUTED)
        self.update_ready()

    def check(self):
        if self._auto_id:
            self.after_cancel(self._auto_id)
        self._auto_id = None
        if self.app.busy or self.sending or self.checking:
            return
        self.checking = True
        self.plan = self.probe_result = None
        self.replace.set(False)
        self.connection.configure(text='Checking your SP…')
        self.message.configure(text='Reading project, bank and existing pads. No samples are being sent.')
        self.update_ready()
        requested = None if self.port.get() in ('', 'Choose an SP') else self.port.get()
        def work():
            if not self.cli:
                return [], None, 'The bundled SP USB helper is missing. Reinstall the complete app package.'
            try:
                found = sp_transfer.list_ports(self.cli)
                if not found:
                    return [], None, 'No SP found. Connect it by USB, close other SP librarian apps, then Refresh SP.'
                if requested is None and len(found) > 1:
                    return found, None, 'More than one SP is connected. Choose which one to use.'
                return found, sp_transfer.probe(self.cli, requested), None
            except Exception as exc:
                return locals().get('found', []), None, str(exc)
        def done(result):
            if not self.winfo_exists():
                return
            self.checking = False
            found, device, error = result
            if len(found) > 1:
                self.device_menu.configure(values=[p['port'] for p in found])
                if requested is None:
                    self.port.set('Choose an SP')
                self.device_row.grid()
            else:
                self.device_row.grid_remove()
            if error:
                self.connection.configure(text='Choose an SP' if len(found) > 1 and not requested else 'SP connection unavailable')
                self.message.configure(text=error)
                self.update_ready()
                return
            self.probe_result = device
            self.port.set(device['port'])
            self.connection.configure(text=f"Project {device['project']}: {device['project_name']}  ·  USB {device['port']}")
            current = device.get('current_bank')
            if not self.manual_bank or self.bank.get() not in sp_transfer.BANKS:
                self.bank.set(current if current in list(sp_transfer.BANKS) else '')
            self.build_plan()
        self.app.submit(work, done)

    def send(self):
        if self.checking or self.sending or self.app.busy or not self.plan:
            return
        if self.plan.overwrites and not self.replace.get():
            return
        plan, allow = self.plan, self.replace.get()
        self.sending = True
        self.update_ready()
        def work():
            try:
                return True, sp_transfer.send(plan, self.cli, self.export_folder,
                    reviewed=True, allow_overwrite=allow, backup_dir=self.backup_folder,
                    progress=self.notify, cancel=self.app.cancel)
            except Exception as exc:
                return False, exc
        def done(result):
            if not self.winfo_exists():
                return
            self.sending = False
            try:
                while True:
                    self._progress_events.get_nowait()
            except queue.Empty:
                pass
            ok, value = result
            if ok:
                self.plan = self.probe_result = None
                self.progress.set(1)
                text = f"{len(value['completed_pads'])} pads sent to project {plan.project}, bank {plan.bank}. Backup and report: {value['backup_dir'] or self.backup_folder}"
                self.app.set_status(text)
                self.message.configure(text=text)
            else:
                self.plan = self.probe_result = None
                self.replace.set(False)
                report = getattr(value, 'report', {})
                completed = len(report.get('completed_pads', []))
                text = str(value) + f'\n{completed} pads completed. Refresh SP before retrying.'
                if report.get('journal'):
                    text += '\nTransfer report: ' + str(report['journal'])
                self.message.configure(text=text)
                self.app.set_status(text)
            self.update_ready()
        self.app.submit(work, done, sending=True)

    def close(self):
        if self.sending and self.app.busy:
            self.app.request_cancel()
            self.message.configure(text='Stopping after the current pad. Completed transfers stay on the SP.')
            return
        self.grab_release()
        self.destroy()

    def destroy(self):
        for identifier in (getattr(self, '_auto_id', None), getattr(self, '_poll_id', None)):
            if identifier:
                self.after_cancel(identifier)
        self._auto_id = self._poll_id = None
        super().destroy()
