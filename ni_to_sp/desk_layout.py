"""Sampler-oriented layout, isolated from conversion and hardware transfer."""
from pathlib import Path
import tkinter as tk

import customtkinter as ctk

from . import desk_theme as T
from .direct import usable
from .kit_editor import MODES, ExpansionPool
from .live_midi import load_settings
from .model import sp_pad_number
from .waveform import Waveform


def build(app):
    app._desk_ready = False
    app.ui_warning_color = T.MISSING_INK
    app.configure(fg_color=T.WORKSPACE)
    app.geometry('1320x900')
    app.minsize(1120, 800)
    app.grid_columnconfigure(0, weight=0, minsize=246)
    app.grid_columnconfigure(1, weight=1)
    app.grid_rowconfigure(1, weight=1)
    app.connection_settings = load_settings(app.data_dir / 'connections.json')
    from .browser_ui import _PreviewOutput
    app.player = _PreviewOutput(app)
    app._init_live_controls()
    app.pool = ExpansionPool(app.data_dir / 'expansion-pads.sqlite')

    head = ctk.CTkFrame(app, fg_color=T.HEADER, corner_radius=0, height=82)
    head.grid(row=0, column=0, columnspan=2, sticky='ew')
    head.grid_columnconfigure(1, weight=1)
    identity = ctk.CTkFrame(head, fg_color='transparent')
    identity.grid(row=0, column=0, sticky='w', padx=20, pady=13)
    T.label(identity, 'NI to SP / MPC', 23, T.ON_DARK, True, True).pack(anchor='w')
    T.label(identity, 'Sample kit converter', 12, T.DARK_MUTED).pack(anchor='w')
    files = ctk.CTkFrame(head, fg_color='transparent')
    files.grid(row=0, column=1, sticky='w', padx=16)
    app.new_button = T.button(app, files, 'New kit', app.new_empty, dark=True, width=83)
    app.new_button.pack(side='left', padx=3)
    T.button(app, files, 'Open', app.open_document, dark=True, width=70).pack(side='left', padx=3)
    T.button(app, files, 'Save kit', app.save_document, dark=True, width=86).pack(side='left', padx=3)
    connections = ctk.CTkFrame(head, fg_color='transparent')
    connections.grid(row=0, column=2, sticky='e', padx=18)
    T.button(app, connections, 'Connections', app.audio_settings, dark=True, width=114).pack(side='left', padx=5)
    app.live_button = T.button(app, connections, 'MIDI preview: OFF', app.toggle_live_preview, dark=True, width=166)
    app.live_button.pack(side='left', padx=5)
    app.stop_button = T.button(app, connections, 'Stop audio', app.stop_audio, dark=True, width=96)
    app.stop_button.pack(side='left', padx=5)
    app.controls.remove(app.stop_button)  # Remains usable while a library/export job is busy.
    app.live_colors = {'on': '#A7CCDF', 'pending': '#E2C894', 'off': T.LIBRARY_INSET,
        'dark': T.INK, 'text': T.ON_DARK}

    library = ctk.CTkFrame(app, fg_color=T.LIBRARY, corner_radius=0, width=246)
    library.grid(row=1, column=0, sticky='nsew')
    library.grid_columnconfigure(0, weight=1)
    library.grid_rowconfigure(4, weight=1)
    T.label(library, 'Your expansions', 18, T.ON_DARK, True).grid(row=0, column=0, sticky='w', padx=18, pady=(18, 10))
    app.search_var = tk.StringVar()
    search_area = ctk.CTkFrame(library, fg_color='transparent')
    search_area.grid(row=1, column=0, sticky='ew', padx=14, pady=(0, 10))
    T.label(search_area, 'Search kits', 12, T.DARK_MUTED).pack(anchor='w', pady=(0, 4))
    search = ctk.CTkEntry(search_area, textvariable=app.search_var, placeholder_text='Search kits…',
        height=38, fg_color=T.LIBRARY_INSET, border_color='#748D9C', text_color=T.ON_DARK,
        placeholder_text_color=T.DARK_MUTED, font=T.font(14))
    search.pack(fill='x')
    app.controls.append(search)
    app.pack_var = tk.StringVar(value='All expansions')
    app.pack_menu = ctk.CTkOptionMenu(library, variable=app.pack_var, values=['All expansions'],
        command=lambda _: app.filter_kits(), width=218, height=34, dynamic_resizing=False,
        font=T.font(13), fg_color=T.LIBRARY_INSET, button_color=T.LIBRARY_INSET,
        text_color=T.ON_DARK, dropdown_font=T.font(13))
    app.pack_menu.grid(row=2, column=0, sticky='ew', padx=14, pady=(0, 7))
    app.controls.append(app.pack_menu)
    app.library_count = T.label(library, 'Finding installed kits…', 12, T.DARK_MUTED,
        wraplength=216, justify='left', anchor='w')
    app.library_count.grid(row=3, column=0, sticky='ew', padx=16, pady=(0, 10))
    listing = ctk.CTkFrame(library, fg_color=T.LIBRARY_INSET, corner_radius=0)
    listing.grid(row=4, column=0, sticky='nsew', padx=10)
    listing.grid_rowconfigure(0, weight=1)
    listing.grid_columnconfigure(0, weight=1)
    app.kit_list = tk.Listbox(listing, bg=T.LIBRARY_INSET, fg=T.ON_DARK, selectbackground='#B9D1DE',
        selectforeground=T.INK, font=('Segoe UI', 11), width=25, relief='flat',
        highlightthickness=1, highlightbackground=T.LIBRARY_INSET, highlightcolor=T.FOCUS,
        exportselection=False, activestyle='none')
    app.kit_list.grid(row=0, column=0, sticky='nsew')
    scroll = ctk.CTkScrollbar(listing, command=app.kit_list.yview, fg_color=T.LIBRARY_INSET,
        button_color='#6F8A9A')
    scroll.grid(row=0, column=1, sticky='ns')
    app.kit_list.configure(yscrollcommand=scroll.set)
    app.kit_list.bind('<<ListboxSelect>>', app.select_kit)
    T.button(app, library, 'Hear original kit demo', app.preview_demo, dark=True, width=216).grid(
        row=5, column=0, sticky='ew', padx=14, pady=(12, 6))
    T.button(app, library, 'Open exported kit…', app.open_completed, dark=True, width=216).grid(
        row=6, column=0, sticky='ew', padx=14, pady=(0, 12))
    app.root_var = tk.StringVar(value='C:/Users/Public/Documents')
    source = ctk.CTkFrame(library, fg_color='transparent')
    source.grid(row=7, column=0, sticky='ew', padx=14, pady=(0, 14))
    T.label(source, 'Expansion folders', 12, T.DARK_MUTED).pack(anchor='w')
    T.button(app, source, 'Change folder', app.choose_root, dark=True, width=130).pack(side='left', pady=(4, 0))
    T.button(app, source, 'Rescan', app.scan, dark=True, width=76).pack(side='right', pady=(4, 0))
    app.search_var.trace_add('write', lambda *_: app.filter_kits())

    app.tabs = ctk.CTkTabview(app, fg_color=T.WORKSPACE, corner_radius=0,
        command=app.sync,
        segmented_button_fg_color=T.SOFT, segmented_button_selected_color=T.ACTION,
        segmented_button_selected_hover_color=T.ACTION_HOVER,
        segmented_button_unselected_color=T.SOFT, segmented_button_unselected_hover_color='#C4D1D9',
        text_color=T.ON_DARK)
    app.tabs.grid(row=1, column=1, sticky='nsew', padx=(8, 16), pady=(8, 0))
    # Selected tab has white text; unselected is kept dark with a neutral label
    # through the segmented control's per-button labels below.
    play = app.tabs.add('Play & export')
    batch = app.tabs.add('Batch export')
    play.grid_columnconfigure(0, weight=1)
    play.grid_rowconfigure(1, weight=1)
    title = ctk.CTkFrame(play, fg_color='transparent')
    title.grid(row=0, column=0, sticky='ew', pady=(5, 12))
    title.grid_columnconfigure(0, weight=1)
    app.kit_title = T.label(title, 'My kit', 26, T.INK, True, True, anchor='w')
    app.kit_title.grid(row=0, column=0, sticky='ew')
    app.kit_subtitle = T.label(title, 'Start with samples or choose an expansion kit.', 13,
        T.MUTED, wraplength=630, justify='left', anchor='w')
    app.kit_subtitle.grid(row=1, column=0, sticky='ew', pady=(4, 0))
    T.button(app, title, 'Rename kit', app.rename_kit, width=112).grid(row=0, column=1, rowspan=2, padx=(12, 0))
    body = ctk.CTkFrame(play, fg_color='transparent')
    body.grid(row=1, column=0, sticky='nsew')
    body.grid_columnconfigure(0, weight=1)
    body.grid_columnconfigure(1, weight=0, minsize=272)
    body.grid_rowconfigure(0, weight=1)

    grid = ctk.CTkFrame(body, fg_color='transparent')
    grid.grid(row=0, column=0, sticky='nsew', padx=(0, 14))
    app.pad_buttons = {}
    for row in range(4):
        grid.grid_rowconfigure(row, weight=1, uniform='pads')
        for col in range(4):
            grid.grid_columnconfigure(col, weight=1, uniform='pads')
            i = (3 - row) * 4 + col
            pad = ctk.CTkButton(grid, text='', command=lambda index=i: app.preview_pad(index),
                width=90, height=64, corner_radius=8, fg_color=T.EMPTY, text_color=T.MUTED,
                hover_color=T.SOFT, border_color=T.LINE, border_width=1, font=T.font(13))
            pad.grid(row=row, column=col, sticky='nsew', padx=4, pady=4)
            app.controls.append(pad)
            app.pad_buttons[i] = pad
            pad.bind('<Button-3>', lambda event, index=i: app.pad_menu(event, index))
            pad._canvas.configure(takefocus=1)
            pad.bind('<FocusIn>', lambda event, index=i: app.focus_pad(index))
            for key, direction in (('Left', -1), ('Right', 1), ('Up', 4), ('Down', -4)):
                pad.bind('<' + key + '>', lambda event, index=i, delta=direction: app.move_pad(index, delta))
            pad.bind('<Return>', lambda event, index=i: app.keyboard_preview(index))
            pad.bind('<space>', lambda event, index=i: app.keyboard_preview(index))

    inspector = ctk.CTkScrollableFrame(body, fg_color=T.PAPER, width=254, corner_radius=8,
        scrollbar_button_color=T.LINE)
    inspector.grid(row=0, column=1, sticky='nsew')
    inspector.grid_columnconfigure(0, weight=1)
    app.selected_label = T.label(inspector, 'Pad 01', 21, T.INK, True, True, anchor='w')
    app.selected_label.grid(row=0, column=0, sticky='ew', padx=12, pady=(12, 3))
    app.sound_name_label = T.label(inspector, 'Empty', 16, T.INK, True, wraplength=228,
        justify='left', anchor='w')
    app.sound_name_label.grid(row=1, column=0, sticky='ew', padx=12, pady=(0, 6))
    app.pad_state_label = T.label(inspector, 'Choose a sample for this pad.', 12, T.MUTED,
        wraplength=228, justify='left', anchor='w')
    app.pad_state_label.grid(row=2, column=0, sticky='ew', padx=12, pady=(0, 10))
    app.waveform = Waveform(inspector)
    app.waveform.grid(row=13, column=0, sticky='ew', padx=12, pady=(0, 8))
    app.source_label = T.label(inspector, '', 12, T.MUTED, wraplength=228,
        justify='left', anchor='w')
    app.source_label.grid(row=14, column=0, sticky='ew', padx=12, pady=(0, 8))
    app.choose_sample_button = T.button(app, inspector, 'Choose sample…',
        lambda: app.replace_pad(app.selected_pad), primary=True, width=228)
    app.choose_sample_button.grid(row=3, column=0, sticky='ew', padx=12, pady=(0, 6))
    T.label(inspector, 'Or drop a sample onto a pad.', 12, T.MUTED).grid(row=4, column=0, sticky='w', padx=12, pady=(0, 12))
    T.label(inspector, 'Find a replacement', 15, T.INK, True, anchor='w').grid(row=5, column=0, sticky='ew', padx=12, pady=(0, 6))
    app.random_mode = tk.StringVar(value=MODES[2])
    app.random_expansion = tk.StringVar(value='This expansion')
    app.random_matching = tk.StringVar(value='Same pad')
    app.random_segments = []
    for row, title_text, variable, values in (
        (6, 'Search in', app.random_expansion, ['This expansion', 'All expansions']),
        (8, 'Use', app.random_matching, ['Any sample', 'Same pad'])):
        T.label(inspector, title_text, 12, T.MUTED, anchor='w').grid(row=row, column=0, sticky='ew', padx=12)
        control = ctk.CTkSegmentedButton(inspector, variable=variable, values=values,
            command=lambda _: app.update_random_scope(), height=32, font=T.font(12),
            fg_color=T.SOFT, selected_color=T.ACTION, selected_hover_color=T.ACTION_HOVER,
            unselected_color=T.SOFT, unselected_hover_color='#C4D1D9', text_color=T.INK,
            dynamic_resizing=False, width=228)
        control.grid(row=row + 1, column=0, sticky='ew', padx=12, pady=(4, 8))
        app.controls.append(control)
        app.random_segments.append((control, variable))
    app.random_button = T.button(app, inspector, 'Find another sample', app.random_pad, width=228)
    app.random_button.grid(row=10, column=0, sticky='ew', padx=12, pady=(0, 8))
    app.random_help = T.label(inspector, '', 12, T.MUTED, wraplength=228, justify='left', anchor='w')
    app.random_help.grid(row=12, column=0, sticky='ew', padx=12, pady=(0, 10))
    edits = ctk.CTkFrame(inspector, fg_color='transparent')
    edits.grid(row=11, column=0, sticky='ew', padx=12, pady=(0, 10))
    app.clear_button = T.button(app, edits, 'Clear pad', lambda: app.clear_pad(app.selected_pad), width=109)
    app.clear_button.pack(side='left')
    app.undo_button = T.button(app, edits, 'Undo', app.undo_edit, width=109)
    app.undo_button.pack(side='right')
    app.report_button = T.button(app, inspector, 'Source details', app.show_check_report, width=228)
    app.report_button.grid(row=15, column=0, sticky='ew', padx=12, pady=(0, 10))
    app.learn_button = T.button(app, inspector, 'Learn MIDI pad', lambda: app.learn_pad(app.selected_pad), width=228)
    app.learn_button.grid(row=16, column=0, sticky='ew', padx=12, pady=(0, 12))

    export = ctk.CTkFrame(play, fg_color=T.PAPER, corner_radius=8)
    export.grid(row=2, column=0, sticky='ew', pady=(14, 0))
    export.grid_columnconfigure(1, weight=1)
    app.target_var = tk.StringVar(value='SP-404MKII')
    target = ctk.CTkOptionMenu(export, variable=app.target_var, values=['SP-404MKII', 'MPC1000'],
        command=lambda _: app.change_target(), width=146, height=38, font=T.font(14),
        fg_color=T.ACTION, button_color=T.ACTION_HOVER, dropdown_font=T.font(14))
    target.grid(row=0, column=0, sticky='w', padx=12, pady=(12, 5))
    app.controls.append(target)
    app.quality = T.label(export, '', 13, T.INK, wraplength=350, justify='left', anchor='w')
    app.quality.grid(row=0, column=1, sticky='ew', padx=12, pady=(12, 5))
    app.send_open_button = T.button(app, export, 'Send to SP…', app.send_current, primary=True, width=164)
    app.send_open_button.grid(row=0, column=2, sticky='e', padx=12, pady=(12, 5))
    app.mpc_button = T.button(app, export, 'Export MPC program…', app.export_mpc, primary=True, width=164)
    app.mpc_button.grid(row=0, column=2, sticky='e', padx=12, pady=(12, 5))
    app.mpc_button.grid_remove()
    options = ctk.CTkFrame(export, fg_color='transparent')
    options.grid(row=1, column=0, columnspan=3, sticky='ew', padx=12, pady=(0, 12))
    app.use_previews_var, app.silence_var = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
    for text, variable, command in (('Use NI preview clips', app.use_previews_var, app.toggle_preview_sources),
                                   ('Allow missing pads', app.silence_var, app.sync)):
        control = ctk.CTkCheckBox(options, text=text, variable=variable, command=command,
            font=T.font(12), text_color=T.MUTED, fg_color=T.ACTION, border_color=T.MUTED,
            checkbox_height=18, checkbox_width=18, width=160)
        control.pack(side='left', padx=(0, 14))
        app.controls.append(control)
    app.export_button = T.button(app, options, 'Export SP WAVs…', app.export_current, width=144)
    app.export_button.pack(side='right')
    app.next_missing_button = T.button(app, options, 'Next missing pad', app.next_missing, width=144)
    app.next_missing_button.pack(side='right', padx=8)

    batch.grid_columnconfigure(0, weight=1)
    app.batch_panel = ctk.CTkFrame(batch, fg_color=T.PAPER, corner_radius=8)
    app.batch_panel.grid(row=0, column=0, sticky='ew', pady=16)
    app.batch_panel.grid_columnconfigure(0, weight=1)
    T.label(app.batch_panel, 'Export your collection', 23, T.INK, True, True).grid(row=0, column=0, sticky='w', padx=24, pady=(24, 8))
    app.batch_label = T.label(app.batch_panel, '', 15, T.MUTED, anchor='w')
    app.batch_label.grid(row=1, column=0, sticky='ew', padx=24, pady=(0, 12))
    T.label(app.batch_panel, 'Create SP WAV folders from the original installed kits.\nThe kit search and expansion filter define the matching selection.',
        14, T.MUTED, wraplength=660, justify='left', anchor='w').grid(row=2, column=0, sticky='ew', padx=24, pady=(0, 20))
    actions = ctk.CTkFrame(app.batch_panel, fg_color='transparent')
    actions.grid(row=3, column=0, sticky='w', padx=24, pady=(0, 20))
    app.batch_button = T.button(app, actions, 'Export matching kits…', app.export_batch, primary=True, width=210)
    app.batch_button.pack(side='left', padx=(0, 12))
    app.all_batch_button = T.button(app, actions, 'Export all expansions…',
        lambda: app.export_batch(all_expansions=True), width=210)
    app.all_batch_button.pack(side='left')
    T.label(app.batch_panel, 'The preview-clip and missing-pad options on Play & export apply here too.\nEach export includes a report of completed and skipped kits. Your current pad edits apply only to the kit you are editing.',
        13, T.MUTED, wraplength=660, justify='left', anchor='w').grid(row=4, column=0, sticky='ew', padx=24, pady=(0, 24))

    footer = ctk.CTkFrame(app, fg_color=T.WORKSPACE, corner_radius=0)
    footer.grid(row=2, column=0, columnspan=2, sticky='ew', padx=16, pady=(10, 10))
    footer.grid_columnconfigure(0, weight=1)
    app.status = T.label(footer, 'Starting…', 12, T.MUTED, anchor='w', wraplength=900)
    app.status.grid(row=0, column=0, sticky='ew')
    app.cancel_button = ctk.CTkButton(footer, text='Cancel', command=app.request_cancel,
        width=88, height=30, fg_color=T.SOFT, text_color=T.INK, state='disabled', font=T.font(12))
    app.cancel_button.grid(row=0, column=1, padx=(12, 0))
    app.preview_route_label = T.label(footer, 'Preview is off', 12, T.MUTED, anchor='w', wraplength=900)
    app.preview_route_label.grid(row=1, column=0, sticky='ew')
    app.progress = ctk.CTkProgressBar(footer, height=3, progress_color=T.ACTION, fg_color=T.SOFT)
    app.progress.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(6, 0))
    app.progress.set(0)
    app._desk_ready = True
    app._setup_drop()
    app.new_empty()
    update_random_help(app)


def update_random_help(app):
    if not getattr(app, '_desk_ready', False):
        return
    mode = MODES.index(app.random_mode.get())
    app.random_expansion.set('All expansions' if mode % 2 else 'This expansion')
    app.random_matching.set('Same pad' if mode >= 2 else 'Any sample')
    scope = 'all installed expansions' if mode % 2 else 'this kit’s expansion'
    if mode % 2 == 0 and app.group and not app.group['kit'].get('root') and not app.group.get('origin_kit'):
        anchor = app.pack_var.get()
        help_text = ('Choose an expansion in the library filter.' if anchor == 'All expansions'
                     else f"Use {'pad ' + str(app.selected_pad + 1) if mode >= 2 else 'any sample'} from {anchor}.")
    else:
        help_text = (f'Pad {app.selected_pad + 1:02d} in another kit from {scope}.'
                     if mode >= 2 else f'Any sample from {scope}.')
    app.random_help.configure(text=help_text)


def show_group(app):
    if not getattr(app, '_desk_ready', False) or not app.group:
        return
    group = app.group
    app.kit_title.configure(text=group['kit']['name'])
    app.kit_subtitle.configure(text=group['kit']['pack'] +
        ('  |  Direct sample conversion' if group['kit'].get('root') else '  |  Your own sample kit'))
    missing, ready = len(group['unresolved']), group['available']
    app.quality.configure(text=f'{ready}/16 pads ready' + (f'   {missing} ' + ('needs a sample' if missing == 1 else 'need samples') if missing else '   Add a sample to begin' if not ready else '   Ready to export'),
                          text_color=T.MISSING_INK if missing else T.INK)
    for i, row in enumerate(group['pads']):
        app.pad_names[i] = row['name']
        state = row['status']
        text = 'NI preview' if state == 'preview' else 'Drop sample' if state == 'empty' else 'Needs sample' if not usable(row) else 'Replaced' if row.get('replacement') else 'Sample'
        color = T.PREVIEW if state == 'preview' else T.EMPTY if state == 'empty' else T.PAPER if usable(row) else T.MISSING
        selected = i == app.selected_pad
        app.pad_buttons[i].configure(text=f"SP {row['sp_pad']:02d} / MPC {i + 1:02d}\n{row['name'][:18] + ('…' if len(row['name']) > 18 else '')}\n{text}",
            fg_color=T.SELECTED if selected and usable(row) else color, hover_color='#CBDCE6',
            text_color=T.MISSING_INK if not usable(row) and state != 'empty' else T.INK,
            border_width=3 if selected else 1, border_color=T.ACTION if selected else T.LINE)
    row = group['pads'][app.selected_pad]
    app.selected_label.configure(text=f"Pad {app.selected_pad + 1:02d}    SP {row['sp_pad']:02d}")
    app.sound_name_label.configure(text=row['name'])
    state = row['status']
    note = ('Preset audition clip; may be a phrase.' if state == 'preview' else
            'Intentionally empty. Add a sample below.' if state == 'empty' else
            'Audio is missing. Choose a file or find a replacement.' if not usable(row) else
            'Replacement sample. Undo restores the previous pad.' if row.get('replacement') else 'Original sample from this kit.')
    app.pad_state_label.configure(text=note, text_color=T.MISSING_INK if not usable(row) and state != 'empty' else T.MUTED)
    origin = row.get('replacement') or {}
    source = row.get('source')
    source_text = Path(source).name if source else 'No sample assigned'
    if origin.get('kit'):
        source_text += '\nFrom ' + origin['kit']
    app.source_label.configure(text=source_text)
    app.waveform.set_source(row)
    update_random_help(app)
    app.sync()


def sync(app):
    if not getattr(app, '_desk_ready', False):
        return
    group = app.group
    missing = bool(group and group['unresolved'])
    app.next_missing_button.configure(state='normal' if missing and not app.busy else 'disabled')
    app.undo_button.configure(state='normal' if app.undo_history and not app.busy else 'disabled')
    app.random_button.configure(state='normal' if app.kits and not app.busy else 'disabled')
    update_random_help(app)
    if app.target_var.get() == 'MPC1000':
        app.send_open_button.grid_remove()
        app.mpc_button.grid()
        app.export_button.pack_forget()
    else:
        app.mpc_button.grid_remove()
        app.send_open_button.grid()
        if not app.export_button.winfo_manager():
            app.export_button.pack(side='right', before=app.next_missing_button)
    # Segmented controls have dark labels on pale unselected choices and
    # light labels on blue selected choices.
    segments = [(control, var.get()) for control, var in app.random_segments]
    segments.append((app.tabs._segmented_button, app.tabs.get()))
    for control, value in segments:
        for text, button in control._buttons_dict.items():
            button.configure(text_color=T.ON_DARK if text == value else T.INK)
