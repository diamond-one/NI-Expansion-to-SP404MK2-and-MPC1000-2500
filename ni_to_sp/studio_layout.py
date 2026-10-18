"""A native sampler workspace: play, compare, keep, then export."""
from pathlib import Path
import tkinter as tk
import textwrap
import customtkinter as ctk

from . import desk_theme as T
from .direct import usable
from .kit_editor import MODES, ExpansionPool
from .live_midi import load_settings
from .waveform import Waveform


def build(app):
    app._desk_ready = False
    app.ui_warning_color = T.MISSING_INK
    app.configure(fg_color=T.WORKSPACE)
    app.geometry('1380x920')
    app.minsize(1120, 800)
    app.grid_columnconfigure(0, minsize=254, weight=0)
    app.grid_columnconfigure(1, weight=1)
    app.grid_rowconfigure(1, weight=1)
    app.connection_settings = load_settings(app.data_dir / 'connections.json')
    from .browser_ui import _PreviewOutput
    app.player = _PreviewOutput(app)
    app._init_live_controls()
    app.pool = ExpansionPool(app.data_dir / 'expansion-pads.sqlite')

    head = ctk.CTkFrame(app, fg_color=T.HEADER, corner_radius=0)
    head.grid(row=0, column=0, columnspan=2, sticky='ew')
    head.grid_columnconfigure(1, weight=1)
    app.brand_label = T.label(head, 'Kit workshop', 27, T.INK, True, True,
        fg_color=T.SELECTED, corner_radius=0, padx=12, pady=2)
    app.brand_label.grid(row=0, column=0, padx=18, pady=12)
    files = ctk.CTkFrame(head, fg_color='transparent')
    files.grid(row=0, column=1, sticky='w', padx=10)
    app.new_button = T.button(app, files, 'New kit', app.new_empty, dark=True, width=84)
    app.new_button.pack(side='left', padx=3)
    T.button(app, files, 'Open', app.open_document, dark=True, width=68).pack(side='left', padx=3)
    T.button(app, files, 'Save', app.save_document, dark=True, width=68).pack(side='left', padx=3)
    app.undo_button = T.button(app, files, 'Undo', app.undo_edit, dark=True, width=68)
    app.undo_button.pack(side='left', padx=3)
    connections = ctk.CTkFrame(head, fg_color='transparent')
    connections.grid(row=0, column=2, sticky='e', padx=12)
    T.button(app, connections, 'Connections', app.audio_settings, dark=True, width=108).pack(side='left', padx=3)
    app.live_button = T.button(app, connections, 'MIDI preview: OFF', app.toggle_live_preview, dark=True, width=158)
    app.live_button.pack(side='left', padx=3)
    app.stop_button = T.button(app, connections, 'Stop audio', app.stop_audio, dark=True, width=92)
    app.stop_button.pack(side='left', padx=3)
    app.controls.remove(app.stop_button)
    app.live_colors = {'on': T.SELECTED, 'pending': T.SOFT, 'off': T.HEADER, 'dark': T.INK, 'text': T.ON_DARK}

    library = ctk.CTkFrame(app, fg_color=T.LIBRARY, corner_radius=0, border_width=2, border_color=T.LINE)
    library.grid(row=1, column=0, sticky='nsew')
    library.grid_columnconfigure(0, weight=1)
    library.grid_rowconfigure(6, weight=1)
    T.label(library, 'Sound library', 21, T.INK, True, True).grid(row=0, column=0, sticky='w', padx=16, pady=(16, 10))
    app.search_var = tk.StringVar()
    search_field = ctk.CTkFrame(library, fg_color='transparent')
    search_field.grid(row=2, column=0, sticky='ew', padx=12, pady=(0, 8))
    T.label(search_field, 'Search kits or expansions', 12, T.MUTED, anchor='w').pack(fill='x', pady=(0, 3))
    search = ctk.CTkEntry(search_field, textvariable=app.search_var, height=36,
        fg_color=T.LIBRARY_INSET, border_color=T.LINE, text_color=T.INK, placeholder_text_color=T.MUTED, font=T.font(14), corner_radius=0, border_width=2)
    search.pack(fill='x')
    app.controls.append(search)
    app.pack_var = tk.StringVar(value='All expansions')
    app.pack_menu = ctk.CTkOptionMenu(library, variable=app.pack_var, values=['All expansions'],
        command=lambda _: app.filter_kits(), height=32, dynamic_resizing=False, width=226,
        fg_color=T.PAPER, button_color=T.SOFT, text_color=T.INK, font=T.font(13), dropdown_font=T.font(13), corner_radius=0, dropdown_fg_color=T.PAPER, dropdown_text_color=T.INK, dropdown_hover_color=T.SELECTED)
    app.pack_menu.grid(row=3, column=0, sticky='ew', padx=12, pady=(0, 8))
    app.controls.append(app.pack_menu)
    app.library_view = tk.StringVar(value='All kits')
    view = ctk.CTkSegmentedButton(library, variable=app.library_view, values=['All kits', 'Favorites', 'Recent'],
        command=lambda _: app.filter_kits(), height=30, font=T.font(12), fg_color=T.LIBRARY_INSET,
        selected_color=T.SELECTED, selected_hover_color=T.SELECTED, unselected_color=T.LIBRARY_INSET,
        unselected_hover_color=T.SOFT, text_color=T.INK, width=226, dynamic_resizing=False, corner_radius=0, border_width=2)
    view.grid(row=4, column=0, sticky='ew', padx=12)
    app.controls.append(view)
    app.library_count = T.label(library, 'Finding installed kits…', 12, T.MUTED, anchor='w')
    app.library_count.grid(row=5, column=0, sticky='ew', padx=14, pady=8)
    listing = ctk.CTkFrame(library, fg_color=T.LIBRARY_INSET, corner_radius=0)
    listing.grid(row=6, column=0, sticky='nsew', padx=10)
    listing.grid_columnconfigure(0, weight=1)
    listing.grid_rowconfigure(0, weight=1)
    app.kit_list = tk.Listbox(listing, bg=T.LIBRARY_INSET, fg=T.INK, selectbackground=T.SELECTED,
        selectforeground=T.INK, font=('Segoe UI', 11), width=24, relief='flat', highlightthickness=2,
        highlightbackground=T.LINE, highlightcolor=T.FOCUS, exportselection=False, activestyle='none')
    app.kit_list.grid(row=0, column=0, sticky='nsew')
    scrollbar = ctk.CTkScrollbar(listing, command=app.kit_list.yview, fg_color=T.LIBRARY_INSET, button_color=T.LINE)
    scrollbar.grid(row=0, column=1, sticky='ns')
    horizontal = ctk.CTkScrollbar(listing, orientation='horizontal', command=app.kit_list.xview, height=12,
        fg_color=T.LIBRARY_INSET, button_color=T.LINE)
    horizontal.grid(row=1, column=0, sticky='ew')
    app.kit_list.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
    app.kit_list.bind('<<ListboxSelect>>', app.select_kit)
    navigation = ctk.CTkFrame(library, fg_color='transparent')
    navigation.grid(row=7, column=0, sticky='ew', padx=12, pady=8)
    T.button(app, navigation, 'Previous kit', lambda: app.adjacent_kit(-1), dark=True, width=109).pack(side='left')
    T.button(app, navigation, 'Next kit', lambda: app.adjacent_kit(1), dark=True, width=109).pack(side='right')
    app.favorite_button = T.button(app, library, 'Add to favorites', app.toggle_favorite, dark=True, width=226)
    app.favorite_button.grid(row=8, column=0, sticky='ew', padx=12, pady=(0, 6))
    app.library_detail = T.label(library, '', 12, T.MUTED, anchor='w', wraplength=222, justify='left')
    app.library_detail.grid(row=9, column=0, sticky='ew', padx=14, pady=(0, 8))
    app.auto_ni_demo = tk.BooleanVar(value=bool(app._studio_state.get('auto_ni_demo', False)))
    app.demo_toggle = ctk.CTkSwitch(library, text='Hear original NI demo', variable=app.auto_ni_demo,
        command=app.toggle_ni_demo, font=T.font(13), text_color=T.INK, progress_color=T.ACTION)
    app.demo_toggle.grid(row=10, column=0, padx=12, sticky='ew', pady=(4, 14))
    app.controls.append(app.demo_toggle)
    app.root_var = tk.StringVar(value=app._studio_state.get('expansion_root', 'C:/Users/Public/Documents'))
    folders = ctk.CTkFrame(library, fg_color='transparent')
    folders.grid(row=1, column=0, sticky='ew', padx=12, pady=(0, 10))
    app.location_button = T.button(app, folders, 'NI Expansions Location', app.choose_root, dark=True, width=165)
    app.location_button.configure(font=T.font(12))
    app.location_button.pack(side='left')
    app.rescan_button = T.button(app, folders, 'Rescan', app.scan, dark=True, width=57)
    app.rescan_button.configure(font=T.font(12))
    app.rescan_button.pack(side='right')
    app.search_var.trace_add('write', lambda *_: app.filter_kits())

    app.tabs = T.WorkbenchPages(app, command=app.sync)
    app.tabs.grid(row=1, column=1, sticky='nsew', padx=(10, 14), pady=(0, 0))
    play, batch = app.tabs.add('Play & export'), app.tabs.add('Batch export')
    play.grid_columnconfigure(0, weight=1)
    play.grid_rowconfigure(2, weight=1)
    title = ctk.CTkFrame(play, fg_color='transparent')
    title.grid(row=0, column=0, sticky='ew', pady=(2, 4))
    title.grid_columnconfigure(0, weight=1)
    app.kit_title = T.label(title, 'Untitled kit', 30, T.ON_DARK, True, True,
        fg_color=T.HEADER, corner_radius=0, padx=12, pady=3, anchor='w')
    app.kit_title.grid(row=0, column=0, sticky='w')
    T.button(app, title, 'Rename', app.rename_kit, width=78).grid(row=0, column=1, padx=(8, 0))
    app.draft_status = T.label(title, 'New kit', 12, T.MUTED, anchor='w')
    app.draft_status.grid(row=1, column=0, sticky='ew')
    app.kit_subtitle = T.label(title, '', 12, T.MUTED, anchor='w')
    app.kit_subtitle.grid(row=1, column=1, sticky='e')
    drafts = ctk.CTkFrame(play, fg_color='transparent')
    drafts.grid(row=1, column=0, sticky='ew', pady=(0, 10))
    app.draft_menu = ctk.CTkOptionMenu(drafts, values=['Session kits'], command=app.choose_draft,
        width=230, height=28, fg_color=T.PAPER, button_color=T.SOFT, font=T.font(12), text_color=T.INK, corner_radius=0, dropdown_fg_color=T.PAPER, dropdown_text_color=T.INK)
    app.draft_menu.pack(side='left')
    app.controls.append(app.draft_menu)
    app.return_button = T.button(app, drafts, 'Return to edited kit', app.restore_draft, width=159)
    app.return_button.configure(height=28)
    app.return_button.pack(side='left', padx=8)
    app.recover_button = T.button(app, drafts, 'Recover last session', lambda: app.restore_draft(app._recover_id), width=160)
    app.recover_button.configure(height=28)
    app.recover_button.pack(side='left')

    body = ctk.CTkFrame(play, fg_color='transparent')
    body.grid(row=2, column=0, sticky='nsew')
    body.grid_columnconfigure(0, weight=1)
    body.grid_columnconfigure(1, minsize=310, weight=0)
    body.grid_rowconfigure(0, weight=1)
    pads = ctk.CTkFrame(body, fg_color='transparent')
    pads.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
    pads.grid_columnconfigure(0, weight=1)
    pads.grid_rowconfigure(1, weight=1)
    bank_bar = ctk.CTkFrame(pads, fg_color='transparent')
    bank_bar.grid(row=0, column=0, sticky='ew', pady=(0, 8))
    app.bank_label = T.label(bank_bar, 'MPC1000 · Bank A', 13, T.MUTED, anchor='w')
    bank_bar.grid_columnconfigure(0, weight=1)
    app.bank_label.configure(wraplength=260, justify='left')
    app.bank_label.grid(row=0, column=0, sticky='w', padx=(0, 8))
    app.mpc_bank_var = tk.StringVar(value='A')
    app.bank_selector = ctk.CTkSegmentedButton(bank_bar, variable=app.mpc_bank_var, values=list('ABCD'),
        command=app.select_mpc_bank, width=156, height=30, font=T.font(14), dynamic_resizing=False,
        fg_color=T.SOFT, selected_color=T.SELECTED, selected_hover_color=T.SELECTED,
        unselected_color=T.SOFT, unselected_hover_color=T.SOFT, text_color=T.INK, corner_radius=0, border_width=2)
    app.bank_selector.grid(row=0, column=1, sticky='e')
    app.controls.append(app.bank_selector)
    grid = ctk.CTkFrame(pads, fg_color='transparent')
    grid.grid(row=1, column=0, sticky='nsew')
    app.pad_buttons = {}
    for r in range(4):
        grid.grid_rowconfigure(r, weight=1, uniform='pads')
        for c in range(4):
            grid.grid_columnconfigure(c, weight=1, uniform='pads')
            i = (3 - r) * 4 + c
            pad = ctk.CTkButton(grid, text='', command=lambda n=i: app.preview_pad(n), width=82, height=64,
                corner_radius=0, fg_color=T.PAPER, text_color=T.INK, hover_color=T.SOFT,
                border_color=T.LINE, border_width=2, font=T.font(13, True))
            pad.grid(row=r, column=c, sticky='nsew', padx=3, pady=3)
            pad._canvas.configure(takefocus=1)
            pad.bind('<Button-3>', lambda event, n=i: app.pad_menu(event, n))
            pad.bind('<FocusIn>', lambda event, n=i: app.focus_pad(n))
            for key, delta in (('Left', -1), ('Right', 1), ('Up', 4), ('Down', -4)):
                pad.bind('<' + key + '>', lambda event, n=i, d=delta: app.move_pad(n, d))
            pad.bind('<Return>', lambda event, n=i: app.keyboard_preview(n))
            pad.bind('<space>', lambda event, n=i: app.keyboard_preview(n))
            app.pad_buttons[i] = pad
    T.label(pads, 'Click to play · arrows to select · Space to hear · drop a sound onto any pad', 12,
        T.MUTED, wraplength=450, justify='left', anchor='w').grid(row=3, column=0, sticky='ew', pady=(5, 0))

    inspector = ctk.CTkScrollableFrame(body, fg_color=T.PAPER, corner_radius=0, width=278, border_width=2, border_color=T.LINE, scrollbar_button_color=T.LINE, scrollbar_button_hover_color=T.ACTION)
    inspector.grid(row=0, column=1, sticky='nsew')
    inspector.grid_columnconfigure(0, weight=1)
    app.selected_label = T.label(inspector, 'Pad A01', 18, T.FOCUS, True, True, anchor='w')
    app.selected_label.grid(row=0, column=0, sticky='ew', padx=12, pady=(10, 2))
    app.sound_name_label = T.label(inspector, '', 16, T.INK, True, wraplength=266, justify='left', anchor='w')
    app.sound_name_label.grid(row=1, column=0, sticky='ew', padx=12)
    app.waveform = Waveform(pads)
    app.waveform.grid(row=2, column=0, sticky='ew', padx=3, pady=(10, 0))
    transport = ctk.CTkFrame(inspector, fg_color='transparent')
    transport.grid(row=3, column=0, sticky='ew', padx=12)
    app.hear_button = T.button(app, transport, 'Hear pad', lambda: app.preview_pad(app.selected_pad), width=126)
    app.hear_button.pack(side='left')
    app.controls.remove(app.hear_button)
    app.compare_button = T.button(app, transport, 'Compare original', app.audition_original, width=126)
    app.compare_button.pack(side='right')
    app.pad_state_label = T.label(inspector, '', 12, T.MUTED, wraplength=266, justify='left', anchor='w')
    app.pad_state_label.grid(row=4, column=0, sticky='ew', padx=12, pady=(6, 3))
    app.random_mode = tk.StringVar(value=MODES[2])
    app.random_expansion = tk.StringVar(value='This expansion')
    app.random_matching = tk.StringVar(value='Same pad')
    app.random_segments = []
    for r, variable, values in ((5, app.random_expansion, ['This expansion', 'All expansions']),
                               (6, app.random_matching, ['Any sample', 'Same pad'])):
        segment = ctk.CTkSegmentedButton(inspector, variable=variable, values=values,
            command=lambda _: app.update_random_scope(), height=28, font=T.font(12), fg_color=T.WORKSPACE,
            selected_color=T.SELECTED, selected_hover_color=T.SELECTED, unselected_color=T.SOFT,
            unselected_hover_color=T.SOFT, text_color=T.INK, dynamic_resizing=False, width=266, corner_radius=0, border_width=2)
        segment.grid(row=r, column=0, sticky='ew', padx=12, pady=3)
        app.random_segments.append((segment, variable))
        app.controls.append(segment)
    app.random_help = T.label(inspector, '', 12, T.MUTED, wraplength=266, justify='left', anchor='w')
    app.random_help.grid(row=7, column=0, sticky='ew', padx=12, pady=(3, 5))
    choices = ctk.CTkFrame(inspector, fg_color='transparent')
    choices.grid(row=8, column=0, sticky='ew', padx=12)
    app.previous_button = T.button(app, choices, 'Previous', app.previous_candidate, width=84)
    app.previous_button.pack(side='left')
    app.random_button = T.button(app, choices, 'Next sound', app.random_pad, primary=True, width=170)
    app.random_button.pack(side='right')
    app.auto_audition = tk.BooleanVar(value=True)
    auto = ctk.CTkCheckBox(inspector, text='Hear candidates automatically', variable=app.auto_audition,
        font=T.font(12), height=22, checkbox_width=18, checkbox_height=18, corner_radius=0,
        text_color=T.MUTED, border_color=T.LINE, fg_color=T.ACTION)
    auto.grid(row=9, column=0, sticky='w', padx=12, pady=(8, 4))
    app.controls.append(auto)
    app.candidate_note = T.label(inspector, '', 12, T.FOCUS, wraplength=266, justify='left', anchor='w')
    app.candidate_note.grid(row=10, column=0, sticky='ew', padx=12, pady=(0, 4))
    commit = ctk.CTkFrame(inspector, fg_color='transparent')
    commit.grid(row=11, column=0, sticky='ew', padx=12)
    app.keep_button = T.button(app, commit, 'Keep sound', app.keep_candidate, primary=True, width=126)
    app.keep_button.pack(side='left')
    app.discard_button = T.button(app, commit, 'Discard', app.discard_candidate, width=126)
    app.discard_button.pack(side='right')
    files = ctk.CTkFrame(inspector, fg_color='transparent')
    files.grid(row=12, column=0, sticky='ew', padx=12, pady=(9, 4))
    app.choose_sample_button = T.button(app, files, 'Choose file…', lambda: app.replace_pad(app.selected_pad), width=126)
    app.choose_sample_button.pack(side='left')
    app.clear_button = T.button(app, files, 'Clear pad', lambda: app.clear_pad(app.selected_pad), width=126)
    app.clear_button.pack(side='right')
    app.source_label = T.label(inspector, '', 11, T.MUTED, wraplength=266, justify='left', anchor='w')
    app.source_label.grid(row=13, column=0, sticky='ew', padx=12, pady=(0, 8))
    # Advanced actions also remain available in each pad's context menu.
    app.report_button = T.button(app, inspector, 'Source details', app.show_check_report, width=100)
    app.learn_button = T.button(app, inspector, 'Learn MIDI pad', lambda: app.learn_pad(app.selected_pad), width=100)

    export = ctk.CTkFrame(play, fg_color=T.PAPER, corner_radius=0, border_width=2, border_color=T.LINE)
    export.grid(row=3, column=0, sticky='ew', pady=(12, 0))
    export.grid_columnconfigure(1, weight=1)
    app.target_var = tk.StringVar(value=app._studio_state.get('target', 'MPC1000'))
    target = ctk.CTkOptionMenu(export, variable=app.target_var, values=['MPC1000', 'SP-404MKII'],
        command=lambda _: app.change_target(), width=139, height=36, font=T.font(14), fg_color=T.SOFT,
        button_color=T.SOFT, text_color=T.INK, corner_radius=0, dropdown_fg_color=T.PAPER, dropdown_text_color=T.INK)
    target.grid(row=0, column=0, padx=12, pady=(10, 3))
    app.controls.append(target)
    app.quality = T.label(export, '', 12, T.MUTED, wraplength=310, anchor='w', justify='left')
    app.quality.grid(row=0, column=1, sticky='ew', padx=8, pady=(10, 3))
    app.send_open_button = T.button(app, export, 'Send to SP…', app.send_current, primary=True, width=174)
    app.send_open_button.grid(row=0, column=2, padx=12, pady=(10, 3))
    app.mpc_button = T.button(app, export, 'Export MPC program…', app.export_mpc, primary=True, width=174)
    app.mpc_button.grid(row=0, column=2, padx=12, pady=(10, 3))
    options = ctk.CTkFrame(export, fg_color='transparent')
    options.grid(row=1, column=0, columnspan=3, sticky='ew', padx=12, pady=(2, 10))
    app.use_previews_var, app.silence_var = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
    for text, var, command in (('NI preview clips', app.use_previews_var, app.toggle_preview_sources),
                                ('Export missing as empty', app.silence_var, app.sync)):
        check = ctk.CTkCheckBox(options, text=text, variable=var, command=command, font=T.font(12),
            text_color=T.MUTED, fg_color=T.ACTION, border_color=T.LINE, width=167 if var is app.silence_var else 125, height=22,
            checkbox_width=18, checkbox_height=18, corner_radius=0)
        check.pack(side='left', padx=(0, 8))
        app.controls.append(check)
    app.export_button = T.button(app, options, 'Export SP WAVs…', app.export_current, width=137)
    app.export_button.pack(side='right')
    app.next_missing_button = T.button(app, options, 'Next missing pad', app.next_missing, width=137)
    app.next_missing_button.pack(side='right', padx=6)
    T.button(app, options, 'Fill empty pads…', app.fill_empty_pads, width=137).pack(side='right', padx=6)

    batch.grid_columnconfigure(0, weight=1)
    batch.grid_rowconfigure(0, weight=1)
    batch_scroll = ctk.CTkScrollableFrame(batch, fg_color=T.WORKSPACE, corner_radius=0,
        scrollbar_button_color=T.LINE, scrollbar_button_hover_color=T.ACTION)
    batch_scroll.grid(row=0, column=0, sticky='nsew')
    batch_scroll.grid_columnconfigure(0, weight=1)
    app.batch_panel = ctk.CTkFrame(batch_scroll, fg_color=T.PAPER, corner_radius=0,
        border_width=2, border_color=T.LINE)
    app.batch_panel.grid(row=0, column=0, sticky='ew', padx=(0, 6), pady=(0, 14))
    app.batch_panel.grid_columnconfigure(0, weight=1)
    T.label(app.batch_panel, 'Export SP sample kits', 32, T.INK, True, True,
        anchor='w').grid(row=0, column=0, sticky='ew', padx=24, pady=(22, 4))
    app.batch_label = T.label(app.batch_panel, '', 16, T.MUTED, anchor='w')
    app.batch_label.grid(row=1, column=0, sticky='ew', padx=24, pady=(0, 16))
    T.label(app.batch_panel, 'Create 16-pad WAV folders from your installed NI kits. Choose a scope below.',
        14, T.MUTED, wraplength=650, justify='left', anchor='w').grid(row=2, column=0,
        sticky='ew', padx=24, pady=(0, 22))
    scopes = ctk.CTkFrame(app.batch_panel, fg_color='transparent')
    scopes.grid(row=3, column=0, sticky='ew', padx=24, pady=(0, 22))
    scopes.grid_columnconfigure((0, 1), weight=1, uniform='scopes')
    matched = ctk.CTkFrame(scopes, fg_color=T.SELECTED, corner_radius=0,
        border_width=2, border_color=T.LINE)
    matched.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
    matched.grid_columnconfigure(0, weight=1)
    T.label(matched, 'Matching kits', 22, T.INK, True, True, anchor='w').grid(
        row=0, column=0, sticky='w', padx=16, pady=(14, 0))
    T.label(matched, 'Uses your search, expansion and Favorites / Recent filter.', 14,
        T.INK, wraplength=268, justify='left', anchor='w', height=46).grid(row=1, column=0,
        sticky='ew', padx=16, pady=(2, 16))
    app.batch_button = T.button(app, matched, 'Export matching kits…', lambda: app.export_batch(),
        primary=True, width=218, height=46)
    app.batch_button.grid(row=2, column=0, sticky='w', padx=16, pady=(0, 16))
    all_kits = ctk.CTkFrame(scopes, fg_color=T.PAPER, corner_radius=0,
        border_width=2, border_color=T.LINE)
    all_kits.grid(row=0, column=1, sticky='nsew', padx=(8, 0))
    all_kits.grid_columnconfigure(0, weight=1)
    T.label(all_kits, 'All expansions', 22, T.INK, True, True, anchor='w').grid(
        row=0, column=0, sticky='w', padx=16, pady=(14, 0))
    T.label(all_kits, 'Every installed kit, regardless of the library filters.', 14,
        T.MUTED, wraplength=268, justify='left', anchor='w', height=46).grid(row=1, column=0,
        sticky='ew', padx=16, pady=(2, 16))
    app.all_batch_button = T.button(app, all_kits, 'Export all expansions…',
        lambda: app.export_batch(all_expansions=True), width=218, height=46)
    app.all_batch_button.grid(row=2, column=0, sticky='w', padx=16, pady=(0, 16))
    policy = ctk.CTkFrame(batch_scroll, fg_color=T.PAPER, corner_radius=0,
        border_width=2, border_color=T.LINE)
    policy.grid(row=1, column=0, sticky='ew', padx=(0, 6), pady=(0, 14))
    policy.grid_columnconfigure(0, weight=1)
    T.label(policy, 'Missing sounds & previews', 19, T.INK, True, True,
        anchor='w').grid(row=0, column=0, sticky='ew', padx=20, pady=(16, 8))
    app.batch_policy_checks = {}
    for r, text, var, command in ((1, 'Use NI preset preview clips', app.use_previews_var, app.toggle_preview_sources),
                                 (2, 'Export missing as empty', app.silence_var, app.sync)):
        check = ctk.CTkCheckBox(policy, text=text, variable=var, command=command,
            font=T.font(14), text_color=T.INK, fg_color=T.ACTION, border_color=T.LINE,
            corner_radius=0, checkbox_width=18, checkbox_height=18)
        check.grid(row=r, column=0, sticky='w', padx=20, pady=(0, 8))
        app.controls.append(check)
        app.batch_policy_checks[text] = check
    T.label(policy, 'Off: skip kits with unresolved sounds. On: export those positions as silence.\nIntentional empty pads are always allowed. NI clips may contain a musical phrase.',
        13, T.MUTED, wraplength=650, justify='left', anchor='w').grid(row=3, column=0,
        sticky='ew', padx=20, pady=(0, 16))
    reopen = ctk.CTkFrame(batch_scroll, fg_color='transparent')
    reopen.grid(row=2, column=0, sticky='ew', pady=(0, 16))
    T.button(app, reopen, 'Open exported SP kit…', app.open_completed, width=224).pack(side='left')
    T.label(reopen, 'Edited MPC programs export from Play & export.', 13, T.MUTED,
        wraplength=330, justify='left').pack(side='left', padx=16)

    footer = ctk.CTkFrame(app, fg_color=T.WORKSPACE, corner_radius=0)
    footer.grid(row=2, column=0, columnspan=2, sticky='ew', padx=14, pady=(8, 8))
    footer.grid_columnconfigure(0, weight=1)
    app.status = T.label(footer, '', 12, T.MUTED, anchor='w', wraplength=1000)
    app.status.grid(row=0, column=0, sticky='ew')
    app.cancel_button = ctk.CTkButton(footer, text='Cancel', command=app.request_cancel, width=80, height=26,
        fg_color=T.SOFT, text_color=T.INK, state='disabled', font=T.font(12))
    app.cancel_button.grid(row=0, column=1)
    app.preview_route_label = T.label(footer, 'Mouse / keyboard ready · MIDI preview off', 12, T.MUTED, anchor='w')
    app.preview_route_label.grid(row=1, column=0, sticky='ew')
    app.progress = ctk.CTkProgressBar(footer, height=3, fg_color=T.SOFT, progress_color=T.ACTION)
    app.progress.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(4, 0))
    app.progress.set(0)
    app._desk_ready = True
    app._pad_window_size, app._pad_resize_after = None, None
    def resized(event):
        if event.widget is not app or app._closed:
            return
        size = (event.width, event.height)
        if size == app._pad_window_size:
            return
        app._pad_window_size = size
        if app._pad_resize_after:
            app.after_cancel(app._pad_resize_after)
        def refresh_labels():
            app._pad_resize_after = None
            if not app._closed:
                app.show_group()
        app._pad_resize_after = app.after(90, refresh_labels)
    app.bind('<Configure>', resized, add='+')
    app._setup_drop()
    app.new_empty()


def update_random_help(app):
    if not getattr(app, '_desk_ready', False):
        return
    mode = MODES.index(app.random_mode.get())
    app.random_expansion.set('All expansions' if mode % 2 else 'This expansion')
    app.random_matching.set('Same pad' if mode >= 2 else 'Any sample')
    scope = 'all expansions' if mode % 2 else 'this kit’s expansion'
    if not mode % 2 and app.group and not app.group['kit'].get('root') and not app.group.get('origin_kit'):
        scope = app.pack_var.get()
        if scope == 'All expansions':
            app.random_help.configure(text='Choose an expansion on the left, or use All expansions.')
            return
    app.random_help.configure(text=(f'Original slot {app.selected_pad + 1:02d} in {scope}; instrument may differ.'
                                    if mode >= 2 else f'Any sample from {scope}.'))


def show_group(app):
    if not getattr(app, '_desk_ready', False) or not app.group:
        return
    group, mpc = app.group, app.target_var.get() == 'MPC1000'
    app.kit_title.configure(text=group['kit']['name'])
    app.kit_subtitle.configure(text='4 banks · 64-pad program' if mpc and 'banks' in group else '16-pad sample kit')
    app.draft_status.configure(text=('Modified · recovery saved locally' if app._dirty else 'Saved kit' if app._document_path else 'Original / new kit'))
    app.library_detail.configure(text=group['kit']['name'] + '\n' + group['kit']['pack'])
    app.favorite_button.configure(text='Remove from Favorites' if app.kit['id'] in app._favorites else 'Add to Favorites')
    bank = 'ABCD'[group.get('active_bank', 0)]
    app.mpc_bank_var.set(bank)
    bank_name = group.get('bank_kit', group['kit'])['name']
    app.bank_label.configure(text=(f'MPC1000 · Bank {bank} · {bank_name}' if mpc and 'banks' in group
                                  else 'MPC1000 · Bank A' if mpc else f'SP-404MKII · source bank {bank}'
                                  if 'banks' in group else 'SP-404MKII · 16-pad kit'))
    if mpc and not app.bank_selector.winfo_manager():
        app.bank_selector.grid(row=0, column=1, sticky='e')
    elif not mpc:
        app.bank_selector.grid_remove()
    for i, row in enumerate(group['pads']):
        app.pad_names[i] = row['name']
        trial = bool(app._candidate and i == app.selected_pad)
        shown = app.display_row() if trial else row
        name = shown['name']
        width = max(10, min(22, app.pad_buttons[i].winfo_width() // 8))
        lines = textwrap.wrap(name, width=width, break_long_words=True) or ['Empty']
        name = '\n'.join(lines[:2]) + ('…' if len(lines) > 2 else '')
        number = f'{bank}{i + 1:02d}' if mpc else f'SP {row["sp_pad"]:02d}'
        state = 'Trying sound' if trial else 'Needs sound' if not usable(row) and row['status'] != 'empty' else 'Drop sound' if row['status'] == 'empty' else 'NI preview' if row['status'] == 'preview' else ''
        color = T.MISSING if not usable(shown) and shown['status'] != 'empty' else T.SELECTED if i == app.selected_pad else T.EMPTY if shown['status'] == 'empty' else T.PAPER
        compact = app.pad_buttons[i].winfo_height() < 70
        # Two lines at small sizes; full identity and status stay in the inspector.
        if compact:
            marker = 'trying' if trial else 'missing' if not usable(shown) and shown['status'] != 'empty' else 'preview' if shown['status'] == 'preview' else ''
            caption = number + (' · ' + marker if marker else '')
            caption += '\n' + ('Drop a sound' if shown['status'] == 'empty' else lines[0] + ('…' if len(lines) > 1 else ''))
        else:
            caption = None
        if not compact and state and len(lines) > 1:
            name = lines[0].rstrip('…') + '…'
        app.pad_buttons[i].configure(text=caption if compact else number + '\n' + name + ('\n' + state if state else ''), fg_color=color,
            text_color=T.MISSING_INK if color == T.MISSING else T.INK,
            border_width=3 if i == app.selected_pad else 2, border_color=T.FOCUS if i == app.selected_pad else T.LINE)
    row = app.display_row()
    app.selected_label.configure(text=(f'Pad {bank}{app.selected_pad + 1:02d}' if mpc else f'Pad SP {row["sp_pad"]:02d}') +
                                 (' · original' if app._candidate and app._comparison_original else ' · candidate' if app._candidate else ''))
    app.sound_name_label.configure(text=row['name'])
    app.pad_state_label.configure(text=('Audio is missing. Choose or find a sound.' if not usable(row) and row['status'] != 'empty'
        else 'Empty pad. Add a sound to begin.' if row['status'] == 'empty'
        else 'NI preset preview; may contain a phrase.' if row['status'] == 'preview'
        else 'Original sound' if app._comparison_original else 'Try this sound before keeping it.' if app._candidate
        else 'Replacement sound' if row.get('replacement') else 'Original kit sample'))
    app.waveform.set_source(row)
    source = Path(row['source']).name if row.get('source') else 'Drop a sound here or choose a file.'
    if row.get('replacement', {}).get('kit'):
        source += '\nFrom ' + row['replacement']['kit']
    app.source_label.configure(text=source)
    cursor = app._candidate_positions.get(app.selected_pad, -1)
    app.candidate_note.configure(text='Searching… pads remain playable.' if app._candidate_loading else
        f'Candidate {cursor + 1} · not kept yet' if app._candidate else 'Your kit changes only when you keep a sound.')
    update_random_help(app)
    app.sync()


def sync(app):
    if not getattr(app, '_desk_ready', False):
        return
    group = app.group
    missing = len(group['unresolved']) if group else 0
    candidate = bool(app._candidate)
    ready = group['available'] if group else 0
    mpc = app.target_var.get() == 'MPC1000'
    if mpc and group:
        from .mpc_banks import counts
        ready, missing = counts(group)
    app.next_missing_button.configure(state='normal' if missing and not app.busy else 'disabled')
    app.undo_button.configure(state='normal' if app.undo_history and not app.busy else 'disabled')
    app.random_button.configure(state='normal' if app.kits and not app.busy and not app._candidate_loading else 'disabled',
                                text='Searching…' if app._candidate_loading else 'Next sound')
    app.previous_button.configure(state='normal' if app._candidate_positions.get(app.selected_pad, -1) > 0 and not app._candidate_loading and not app.busy else 'disabled')
    for button in (app.keep_button, app.discard_button):
        button.configure(state='normal' if candidate and not app.busy else 'disabled')
    app.keep_button.configure(fg_color=T.ACTION if candidate else T.SOFT, text_color=T.ON_ACTION if candidate else T.MUTED)
    app.compare_button.configure(state='normal' if candidate and not app.busy else 'disabled',
        text='Hear candidate' if app._comparison_original else 'Compare original')
    app.quality.configure(text=('Keep or discard the candidate before exporting.' if candidate else
        f'{ready} sounds · {missing} missing will export empty' if missing and app.silence_var.get() else
        f'{missing} missing sounds · replace them or enable export as empty' if missing else
        f'{ready}/64 sounds · export banks A–D' if mpc and ready else f'{ready}/16 sounds · export current bank' if ready else 'Add sounds to begin'))
    if app.target_var.get() == 'MPC1000':
        app.send_open_button.grid_remove()
        app.mpc_button.grid()
        app.export_button.pack_forget()
    else:
        app.mpc_button.grid_remove()
        app.send_open_button.grid()
        if not app.export_button.winfo_manager():
            app.export_button.pack(side='right', before=app.next_missing_button)
    app.mpc_button.configure(fg_color=T.ACTION if ready and not candidate and (not missing or app.silence_var.get()) else T.SOFT, text_color=T.ON_ACTION if ready and not candidate and (not missing or app.silence_var.get()) else T.MUTED)
    app.send_open_button.configure(fg_color=T.ACTION if ready and not candidate and (not missing or app.silence_var.get()) else T.SOFT, text_color=T.ON_ACTION if ready and not candidate and (not missing or app.silence_var.get()) else T.MUTED)
    update_random_help(app)
