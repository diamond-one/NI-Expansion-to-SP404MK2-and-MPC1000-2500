"""Non-destructive sound comparisons and persistent, playable working kits."""
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
from tkinter import filedialog

from .direct import inspect_group, preview_session, usable
from .kit_editor import MODES, assign, empty_kit, open_kit, refresh, sample_row, save_kit
from .sp_transfer import _write_json


class StudioWorkflow:
    def __init__(self):
        self._drafts, self._draft_choices = {}, {}
        self._dirty, self._document_path = False, None
        self._candidate = None
        self._candidate_history, self._candidate_positions = {}, {}
        self._comparison_original = False
        self._candidate_serial, self._candidate_loading = 0, False
        self._candidate_cancel = threading.Event()
        self._music_events = queue.Queue()
        self._pending_hit = None
        self._pad_flash_tokens = {}
        self._last_working = None
        directory = Path(os.environ.get('NI_TO_SP_DATA_DIR') or Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'NIToSP')
        try:
            self._studio_state = json.loads((directory / 'studio-session.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self._studio_state = {}
        if not isinstance(self._studio_state, dict):
            self._studio_state = {}
        for field in ('favorites', 'recents'):
            value = self._studio_state.get(field)
            self._studio_state[field] = [key for key in value if isinstance(key, str)] if isinstance(value, list) else []
        if self._studio_state.get('target') not in ('MPC1000', 'SP-404MKII'):
            self._studio_state['target'] = 'MPC1000'
        self._favorites = set(self._studio_state.get('favorites', []))
        self._recents = list(self._studio_state.get('recents', []))
        names = self._studio_state.get('drafts', {})
        self._saved_drafts = {key: value for key, value in names.items() if isinstance(key, str) and isinstance(value, str)} if isinstance(names, dict) else {}
        self._recover_id = self._studio_state.get('current')
        super().__init__()
        self.title('NI to SP & MPC 0.14 · Kit workshop')
        if not self._recover_id and (self.data_dir / 'last-edited-kit.json').is_file():
            try:
                previous = open_kit(self.data_dir / 'last-edited-kit.json')
                self._recover_id = previous['kit']['id']
                save_kit(previous, self.draft_path(self._recover_id))
                self._saved_drafts[self._recover_id] = previous['kit']['name']
                self.write_studio_state()
            except (OSError, ValueError, KeyError, TypeError):
                pass
        self.after(40, self.poll_music)
        self.refresh_draft_menu()

    def draft_path(self, identity):
        key = hashlib.sha256(str(identity).encode()).hexdigest()[:32]
        (self.data_dir / 'working-kits').mkdir(parents=True, exist_ok=True)
        return self.data_dir / 'working-kits' / (key + '.json')

    def write_studio_state(self):
        state = {'current': self.kit['id'] if self.kit and self._dirty else self._recover_id,
                 'favorites': sorted(self._favorites), 'recents': self._recents[:100],
                 'target': self.target_var.get(),
                 'auto_ni_demo': self.auto_ni_demo.get(), 'expansion_root': self.root_var.get(),
                 'drafts': self._saved_drafts}
        _write_json(self.data_dir / 'studio-session.json', state)
        self._studio_state = state

    def stash_working(self):
        if not self.group:
            return
        from .mpc_banks import sync_banks
        sync_banks(self.group)
        identity = self.group['kit']['id']
        self._drafts[identity] = {'group': copy.deepcopy(self.group), 'undo': copy.deepcopy(self.undo_history),
            'dirty': self._dirty, 'path': self._document_path, 'selected': self.selected_pad}
        if self._dirty:
            self._last_working = identity
            self._recover_id = identity
            try:
                save_kit(self.group, self.draft_path(identity))
                save_kit(self.group, self.data_dir / 'last-edited-kit.json')
                self._saved_drafts[identity] = self.group['kit']['name']
                self.write_studio_state()
            except OSError as exc:
                self.set_status('Kit stays in this session; recovery could not be saved: ' + str(exc))

    def refresh_draft_menu(self):
        if not hasattr(self, 'draft_menu'):
            return
        choices = {}
        entries = {identity: (name, False) for identity, name in self._saved_drafts.items()}
        entries.update({identity: (draft['group']['kit']['name'], draft['dirty']) for identity, draft in self._drafts.items()})
        for identity, (name, dirty) in entries.items():
            label = name + (' · modified' if dirty else '')
            if label in choices:
                label += ' · ' + str(identity)[:6]
            choices[label] = identity
        self._draft_choices = choices
        self.draft_menu.configure(values=list(choices) or ['Session kits'])
        self.draft_menu.set(next((label for label, identity in choices.items() if self.kit and identity == self.kit['id']), 'Session kits'))
        can_return = bool(self._last_working and self.kit and self._last_working != self.kit['id'])
        self.return_button.configure(state='normal' if can_return and not self.busy else 'disabled')
        self.recover_button.configure(state='normal' if self._recover_id and not self.busy else 'disabled')

    def choose_draft(self, label):
        identity = self._draft_choices.get(label)
        if identity:
            self.restore_draft(identity)

    def restore_draft(self, identity=None):
        if self.busy:
            return
        identity = identity or self._last_working or self._recover_id
        if not identity:
            return
        self.stash_working()
        draft = self._drafts.get(identity)
        if draft is None:
            try:
                group = open_kit(self.draft_path(identity))
                draft = {'group': group, 'undo': [], 'dirty': True, 'path': None, 'selected': 0}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.set_status('Could not recover that working kit: ' + str(exc))
                return
        self.discard_candidate(refresh_ui=False)
        self.undo_history = copy.deepcopy(draft['undo'])
        self._dirty, self._document_path = draft['dirty'], draft['path']
        self.selected_pad = draft['selected']
        self._install_group(copy.deepcopy(draft['group']))
        self.set_status('Working kit restored, including this session’s Undo history.')

    def _install_group(self, group):
        self.group = refresh(group)
        self.kit, self.prepared, self.report = self.group['kit'], None, None
        self.session = preview_session(self.group)
        self._candidate_history, self._candidate_positions = {}, {}
        if hasattr(self, 'kit_list'):
            self.kit_list.selection_clear(0, 'end')
            for n, kit in enumerate(self.visible):
                if kit['id'] == self.kit['id']:
                    self.kit_list.selection_set(n)
                    self.kit_list.see(n)
                    break
        self.show_group()
        self.refresh_draft_menu()

    def _apply(self, group, edited=True):
        self.discard_candidate(refresh_ui=False)
        from .mpc_banks import sync_banks
        if 'banks' in group:
            group['program_name'] = group['kit']['name']
        sync_banks(group)
        self._dirty = edited or self._dirty
        self.group, self.kit, self.prepared, self.report = refresh(group), group['kit'], None, None
        self.session = preview_session(self.group)
        self.stash_working()
        self.show_group()
        self.refresh_draft_menu()

    def new_empty(self):
        if self.busy:
            return
        self.stash_working()
        self.discard_candidate(refresh_ui=False)
        self.undo_history, self._dirty, self._document_path = [], False, None
        self.selected_pad = 0
        self.silence_var.set(False)
        self._install_group(empty_kit('Untitled kit'))
        self.set_status('New 16-pad kit. Drop sounds on pads or use Fill empty pads. Export as an MPC program or SP kit.')

    def select_kit(self, _event=None, force=False):
        if self.busy or not self.kit_list.curselection():
            return
        kit = self.visible[self.kit_list.curselection()[0]]
        if 'banks' in self.group and self.target_var.get() == 'MPC1000':
            self.load_bank_kit(kit)
            return
        if self.kit and self.kit['id'] == kit['id'] and self.group and not force:
            return
        if kit['id'] in self._drafts and not force:
            self.restore_draft(kit['id'])
            self.maybe_ni_demo()
            return
        # The old group and audio stay playable until inspection completes.
        self.stash_working()
        self.discard_candidate()
        use_previews = self.use_previews_var.get()
        def done(group):
            self.undo_history, self._dirty, self._document_path = [], False, None
            self._install_group(group)
            self._recents = [kit['id'], *[key for key in self._recents if key != kit['id']]][:100]
            self.stash_working()
            self.write_studio_state()
            self.set_status('Kit ready. Play the pads; select a sound to try alternatives.')
            self.maybe_ni_demo()
        self.submit(lambda: inspect_group(kit, self.cancel, use_previews), done,
                    failed=lambda exc: self.set_status('Kit could not be opened; your working kit is unchanged. ' + str(exc)))

    def filter_kits(self):
        if self.busy or not hasattr(self, 'library_view'):
            return
        query, pack = self.search_var.get().strip().casefold(), self.pack_var.get()
        view = self.library_view.get()
        self.filtered = [kit for kit in self.kits if (pack == 'All expansions' or kit['pack'] == pack)
            and query in (kit['name'] + ' ' + kit['pack']).casefold()
            and (view != 'Favorites' or kit['id'] in self._favorites)
            and (view != 'Recent' or kit['id'] in self._recents)]
        if view == 'Recent':
            order = {identity: n for n, identity in enumerate(self._recents)}
            self.filtered.sort(key=lambda kit: order.get(kit['id'], 1000))
        self.visible = self.filtered[:]
        self.kit_list.delete(0, 'end')
        for n, kit in enumerate(self.visible):
            self.kit_list.insert('end', kit['name'])
            if self.kit and self.kit['id'] == kit['id']:
                self.kit_list.selection_set(n)
                self.kit_list.see(n)
        self.library_count.configure(text=f'{len(self.visible):,} kits · all matches available')
        self.batch_label.configure(text=f'{len(self.kits):,} installed · {len(self.filtered):,} matching kits')
        self.sync()

    def adjacent_kit(self, step):
        if self.busy or not self.visible:
            return
        selection = self.kit_list.curselection()
        n = min(max((selection[0] if selection else -1) + step, 0), len(self.visible) - 1)
        self.kit_list.selection_clear(0, 'end')
        self.kit_list.selection_set(n)
        self.kit_list.see(n)
        self.select_kit()

    def toggle_favorite(self):
        if not self.kit:
            return
        identity = self.kit['id']
        self._favorites.symmetric_difference_update({identity})
        try:
            self.write_studio_state()
        except OSError as exc:
            self.set_status('Favorite could not be saved: ' + str(exc))
        self.filter_kits()
        self.show_group()

    def maybe_ni_demo(self):
        if self.auto_ni_demo.get():
            self.preview_demo()

    def toggle_ni_demo(self):
        if self.auto_ni_demo.get():
            self.preview_demo()
        else:
            self.stop_audio()
        try:
            self.write_studio_state()
        except OSError as exc:
            self.set_status('Demo preference could not be saved: ' + str(exc))

    def preview_demo(self):
        self.stop_audio()
        super().preview_demo()

    def choose_root(self):
        super().choose_root()
        try:
            self.write_studio_state()
        except OSError as exc:
            self.set_status('Location preference could not be saved: ' + str(exc))

    def select_mpc_bank(self, letter):
        if self.busy or letter not in 'ABCD':
            return
        from .mpc_banks import activate
        index = 'ABCD'.index(letter)
        if index == self.group.get('active_bank', 0):
            return
        self.discard_candidate(refresh_ui=False)
        self._apply(activate(self.group, index), edited=False)
        self.set_status(f'Bank {letter} selected. Sounds in the other MPC banks are kept.')

    def load_bank_kit(self, kit):
        from .mpc_banks import ensure_banks, activate
        program = ensure_banks(self.group)
        active = program['active_bank']
        self.stash_working()
        self.discard_candidate()
        use_previews = self.use_previews_var.get()
        def done(group):
            # Browsing replaces only the selected bank. The old document remains
            # in the session selector, and Undo also retains the prior program.
            history = copy.deepcopy(self.undo_history)
            history.append(copy.deepcopy(self.group))
            program['banks'][active] = copy.deepcopy(group)
            program['program_id'], program['program_name'] = kit['id'], kit['name']
            group.update(banks=program['banks'], active_bank=active, program_id=kit['id'],
                         program_name=kit['name'], bank_kit=copy.deepcopy(group['kit']))
            self.undo_history = history[-30:]
            self._dirty, self._document_path = True, None
            self._install_group(group)
            self.stash_working()
            self._recents = [kit['id'], *[key for key in self._recents if key != kit['id']]][:100]
            self.write_studio_state()
            self.set_status(f'Loaded {kit["name"]} into bank {"ABCD"[active]}; other banks kept. Undo restores this bank.')
            self.maybe_ni_demo()
        self.submit(lambda: inspect_group(kit, self.cancel, use_previews), done,
                    failed=lambda exc: self.set_status('Bank unchanged: ' + str(exc)))

    def focus_pad(self, index):
        if self.group:
            if index != self.selected_pad:
                self.discard_candidate(refresh_ui=False)
                self.selected_pad = index
            self.show_group()

    def next_missing(self):
        if self.busy or not self.group:
            return
        if self.target_var.get() != 'MPC1000' or 'banks' not in self.group:
            return super().next_missing()
        from .mpc_banks import all_banks
        missing = [(b, n - 1) for b, bank in enumerate(all_banks(self.group)) for n in bank['unresolved']]
        if missing:
            current = (self.group.get('active_bank', 0), self.selected_pad)
            bank, pad = next((item for item in missing if item > current), missing[0])
            self.select_mpc_bank('ABCD'[bank])
            self.focus_pad(pad)

    def move_pad(self, index, delta):
        target = index + delta
        if target in range(16) and not (abs(delta) == 1 and index // 4 != target // 4):
            self.focus_pad(target)
            self.pad_buttons[target]._canvas.focus_set()
        return 'break'

    def display_row(self):
        return self._candidate if self._candidate and not self._comparison_original else self.group['pads'][self.selected_pad]

    def preview_pad(self, index):
        self.focus_pad(index)
        if self.group and usable(self.display_row()):
            self.request_play(index)
        else:
            self.set_status('This pad is empty or missing. Drop a sound, choose a file, or try a random candidate.')

    def request_play(self, index, exclusive=False):
        if os.environ.get('NI_TO_SP_DISABLE_AUDIO') == '1':
            return
        self._pending_hit = (index, self.live_bank_signature(), exclusive)
        if self.live_engine and self.live_engine.running:
            if self.loaded_pad_matches(index):
                self._pending_hit = None
                if exclusive:
                    self.live_engine.silence_pad(index)
                self.live_engine.trigger(index)
                self.flash_pad(index)
                return
            self.schedule_live_reload()
            self.preview_bank_ready()
        elif not self._live_connecting:
            self.enable_live_preview(audio_only=True)

    def preview_bank_ready(self):
        if self.midi_controller and hasattr(self.midi_controller, 'set_mpc_bank'):
            self.midi_controller.set_mpc_bank(self.group.get('active_bank', 0))
        pending = self._pending_hit
        if pending and self.live_signature == pending[1] == self.live_bank_signature():
            self._pending_hit = None
            if pending[2]:
                self.live_engine.silence_pad(pending[0])
            if self.live_engine.trigger(pending[0]):
                self.flash_pad(pending[0])

    def toggle_live_preview(self):
        if self._live_requested and not getattr(self, '_live_audio_only', False):
            self.disable_live_preview(quiet=True)
            self.set_status('MIDI preview off. Mouse and keyboard still audition sounds.')
        else:
            self.enable_live_preview(audio_only=False)

    def flash_pad(self, index):
        from . import desk_theme as T
        if self._closed:
            return
        token = self._pad_flash_tokens.get(index, 0) + 1
        self._pad_flash_tokens[index] = token
        self.pad_buttons[index].configure(border_width=3, border_color=T.FOCUS, fg_color=T.SELECTED)
        def finish():
            if not self._closed and self._pad_flash_tokens.get(index) == token:
                self.show_group()
        self.after(130, finish)

    def discard_candidate(self, refresh_ui=True):
        self._candidate_serial += 1
        self._candidate_cancel.set()
        self._candidate_cancel = threading.Event()
        self._candidate_loading = False
        had_candidate = self._candidate is not None
        self._candidate, self._comparison_original, self._pending_hit = None, False, None
        if had_candidate and self.group:
            self.session = preview_session(self.group)
        if refresh_ui and self.group:
            self.show_group()

    def stage_candidate(self, row, provenance=None):
        original = self.group['pads'][self.selected_pad]
        row = dict(row, ni_pad=self.selected_pad + 1, sp_pad=original['sp_pad'],
                   original_name=original.get('original_name', original['name']), references=[],
                   replacement=provenance or {'method': 'chosen_file'})
        history = self._candidate_history.setdefault(self.selected_pad, [])
        cursor = self._candidate_positions.get(self.selected_pad, -1)
        del history[cursor + 1:]
        history.append(row)
        self._candidate_positions[self.selected_pad] = len(history) - 1
        self.use_candidate(row)

    def use_candidate(self, row):
        self._candidate, self._comparison_original = row, False
        temporary = copy.deepcopy(self.group)
        temporary['pads'][self.selected_pad] = row
        self.session = preview_session(temporary)
        self.show_group()
        if self.auto_audition.get():
            self.request_play(self.selected_pad, exclusive=True)

    def previous_candidate(self):
        cursor = self._candidate_positions.get(self.selected_pad, -1)
        if cursor > 0 and not self._candidate_loading:
            self._candidate_positions[self.selected_pad] = cursor - 1
            self.use_candidate(self._candidate_history[self.selected_pad][cursor - 1])

    def random_pad(self, mode=None):
        if self.busy or not self.group or self._candidate_loading:
            return
        mode = MODES.index(self.random_mode.get()) if mode is None else mode
        self.random_mode.set(MODES[mode])
        if mode != getattr(self, '_history_mode', None):
            self._candidate_history, self._candidate_positions = {}, {}
            self._history_mode = mode
        history = self._candidate_history.get(self.selected_pad, [])
        cursor = self._candidate_positions.get(self.selected_pad, -1)
        if cursor + 1 < len(history):
            self._candidate_positions[self.selected_pad] = cursor + 1
            self.use_candidate(history[cursor + 1])
            return
        self._candidate_serial += 1
        serial, index, identity = self._candidate_serial, self.selected_pad, self.kit['id']
        snapshot, kits = copy.deepcopy(self.group), copy.deepcopy(self.kits)
        pack = self.pack_var.get() if self.pack_var.get() != 'All expansions' else None
        if snapshot['kit'].get('root') or snapshot.get('origin_kit'):
            pack = None
        cancel, previous = self._candidate_cancel, {row['source'] for row in history}
        self._candidate_loading = True
        self.show_group()
        def worker():
            try:
                try:
                    path, provenance = self.pool.choose(kits, snapshot, index, mode, pack, cancel, excluded=previous)
                except ValueError:
                    if not previous:
                        raise
                    # A new pass is allowed only after the unused pool is exhausted.
                    path, provenance = self.pool.choose(kits, snapshot, index, mode, pack, cancel)
                row = sample_row(path)
                self._music_events.put((serial, index, identity, (row, provenance), None))
            except Exception as exc:
                self._music_events.put((serial, index, identity, None, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll_music(self):
        try:
            while True:
                serial, index, identity, result, error = self._music_events.get_nowait()
                if serial != self._candidate_serial or index != self.selected_pad or identity != self.kit['id']:
                    continue
                self._candidate_loading = False
                if error:
                    self.candidate_note.configure(text=error)
                    self.set_status(error)
                    self.sync()
                else:
                    self.stage_candidate(*result)
        except queue.Empty:
            pass
        if not self.closed:
            self.after(40, self.poll_music)

    def audition_original(self):
        if not self._candidate:
            self.preview_pad(self.selected_pad)
            return
        self._comparison_original = not self._comparison_original
        temporary = copy.deepcopy(self.group)
        if not self._comparison_original:
            temporary['pads'][self.selected_pad] = self._candidate
        self.session = preview_session(temporary)
        self.show_group()
        if usable(self.display_row()):
            self.request_play(self.selected_pad, exclusive=True)

    def keep_candidate(self):
        if self.busy or not self._candidate:
            return
        candidate = self._candidate
        if self.set_sample(self.selected_pad, candidate['source'], candidate.get('replacement')):
            self._candidate_history.pop(self.selected_pad, None)
            self._candidate_positions.pop(self.selected_pad, None)
            self.set_status('Sound kept. Undo restores the previous pad.')

    def replace_pad(self, index):
        if self.busy:
            return
        self.focus_pad(index)
        path = filedialog.askopenfilename(parent=self, title='Try a sound on this pad',
            filetypes=[('Audio samples', '*.wav *.wave *.aif *.aiff *.flac *.ogg')])
        if path:
            try:
                self.stage_candidate(sample_row(path))
            except (OSError, ValueError, RuntimeError) as exc:
                self.candidate_note.configure(text=str(exc))

    def fill_empty_pads(self):
        if self.busy:
            return
        slots = [i for i in range(16) if not usable(self.group['pads'][i])]
        if not slots:
            self.set_status('Every pad has audio. Select a pad to replace its sound.')
            return
        paths = filedialog.askopenfilenames(parent=self, title=f'Choose up to {len(slots)} sounds for empty or missing pads',
            filetypes=[('Audio samples', '*.wav *.wave *.aif *.aiff *.flac *.ogg')])
        if not paths:
            return
        if len(paths) > len(slots):
            self.set_status(f'Choose {len(slots)} sounds or fewer; occupied pads are kept.')
            return
        snapshot = copy.deepcopy(self.group)
        def work():
            for index, path in zip(slots, paths):
                assign(snapshot, index, path)
            return snapshot
        def done(group):
            self._remember()
            self._apply(group)
            self.set_status(f'{len(paths)} sounds added. Existing sounds stayed in their pad positions.')
        self.submit(work, done, failed=lambda exc: self.set_status('No pads changed: ' + str(exc)))

    def save_document(self):
        if self.busy:
            return
        if self._candidate:
            self.set_status('Keep or discard the auditioned sound before saving.')
            return
        path = filedialog.asksaveasfilename(parent=self, title='Save working kit', defaultextension='.json',
            filetypes=[('Editable kit', '*.json')], initialfile=self.kit['name'] + '.json')
        if path:
            try:
                save_kit(self.group, path)
                self._dirty, self._document_path = False, str(path)
                self.stash_working()
                # Keep a recovery copy even after an explicit save.
                save_kit(self.group, self.draft_path(self.kit['id']))
                self._recover_id = self.kit['id']
                self._saved_drafts[self.kit['id']] = self.kit['name']
                self.write_studio_state()
                self.show_group()
                self.set_status('Kit saved. Source samples stay in their existing folders.')
            except OSError as exc:
                self.set_status('Kit could not be saved: ' + str(exc))

    def update_random_scope(self):
        self.discard_candidate(refresh_ui=False)
        self._candidate_history, self._candidate_positions = {}, {}
        super().update_random_scope()
        self.show_group()

    def undo_edit(self):
        if self._candidate:
            self.discard_candidate()
            self.set_status('Candidate discarded; your kept sound is unchanged.')
        else:
            super().undo_edit()

    def drop_sample(self, index, data):
        result = super().drop_sample(index, data)
        if result == 'copy' and self.auto_audition.get():
            self.request_play(index)
        return result

    def open_document(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self, title='Open an editable kit', filetypes=[('Editable kit', '*.json')])
        if path:
            try:
                group = open_kit(path)
                self.stash_working()
                self.discard_candidate(refresh_ui=False)
                self.undo_history, self._dirty, self._document_path = [], False, str(path)
                self._install_group(group)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.set_status('Could not open that kit: ' + str(exc))

    def change_target(self):
        self.show_group()
        try:
            self.write_studio_state()
        except OSError as exc:
            self.set_status('Target selected; preference could not be saved: ' + str(exc))

    def sync(self):
        super().sync()
        if not getattr(self, '_desk_ready', False):
            return
        # A background job must not disable the instrument itself.
        for button in self.pad_buttons.values():
            button.configure(state='normal')
        if self._candidate:
            for button in (self.mpc_button, self.send_open_button, self.export_button):
                button.configure(state='disabled')
        elif self.target_var.get() == 'MPC1000':
            from .mpc_banks import counts
            ready, missing = counts(self.group)
            self.mpc_button.configure(state='normal' if ready and (not missing or self.silence_var.get()) and not self.busy else 'disabled')
        self.refresh_draft_menu()

    def open_completed(self, path=None):
        from .kit_editor import from_prepared
        if self.busy:
            return
        path = path or filedialog.askdirectory(parent=self, title='Open an exported SP kit')
        if path:
            self.stash_working()
            self.discard_candidate()
            def done(group):
                self.undo_history, self._dirty, self._document_path = [], False, None
                self._install_group(group)
            self.submit(lambda: from_prepared(path), done)

    def stop_audio(self):
        self._pending_hit = None
        super().stop_audio()

    def export_mpc(self):
        if self._candidate:
            self.set_status('Keep or discard the auditioned sound before exporting.')
            return
        from .mpc_banks import sync_banks
        sync_banks(self.group)
        super().export_mpc()

    def prepare_current(self, destination, done):
        from .direct import prepare_direct
        from .capture import load_prepared
        if self._candidate:
            self.set_status('Keep or discard the auditioned sound before exporting.')
            return
        group, allow = copy.deepcopy(self.group), self.silence_var.get()
        def complete(result):
            self.prepared = result[0]
            self.report = load_prepared(self.prepared)[1]['capture_report']
            done()
        self.submit(lambda: prepare_direct(group, destination, allow, self.notify, self.cancel), complete)

    def close(self):
        if getattr(self, '_pad_resize_after', None):
            self.after_cancel(self._pad_resize_after)
            self._pad_resize_after = None
        self.stash_working()
        self.discard_candidate(refresh_ui=False)
        super().close()
