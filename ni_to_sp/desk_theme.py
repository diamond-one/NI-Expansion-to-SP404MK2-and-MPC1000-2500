"""Punk sampler workspace: paper strips, black ink, acid selection and cut type."""
from pathlib import Path
import os
import customtkinter as ctk

HEADER = '#18171B'
LIBRARY = '#F6F3E8'
LIBRARY_INSET = '#F6F3E8'
WORKSPACE = '#E7E5DC'
PAPER = '#F6F3E8'
INK = '#18171B'
MUTED = '#53524C'
ON_DARK = '#F6F3E8'
ON_ACTION = '#E5F26A'
DARK_MUTED = '#53524C'
ACTION = '#18171B'
ACTION_HOVER = '#34313C'
LINE = '#18171B'
SOFT = '#DDD9CE'
SELECTED = '#E5F26A'
NAV_SELECTED = '#B4A4D0'
MISSING = '#F2C1BD'
MISSING_INK = '#701E0F'
PREVIEW = '#D6CAE9'
PREVIEW_INK = '#443354'
EMPTY = '#DDD9CE'
FOCUS = '#624A7A'
_fonts_loaded = False


def load_fonts():
    global _fonts_loaded
    if not _fonts_loaded:
        for path in (Path(__file__).parent / 'assets/fonts').glob('*.ttf'):
            if os.name == 'nt':
                ctk.FontManager.windows_load_font(str(path), private=True, enumerable=True)
            else:
                ctk.FontManager.load_font(str(path))
        _fonts_loaded = True


def font(size=14, bold=False, display=False):
    load_fonts()
    return ctk.CTkFont(family='Oswald' if display else 'Segoe UI',
                      size=size, weight='bold' if bold else 'normal')


def label(parent, text='', size=14, color=INK, bold=False, display=False, **kwargs):
    kwargs.setdefault('height', max(18, size + 6))
    return ctk.CTkLabel(parent, text=text, text_color=color,
                       font=font(size, bold, display), **kwargs)


def button(app, parent, text, command, primary=False, dark=False, **kwargs):
    settings = dict(height=38, corner_radius=0, border_width=2,
        border_color=LINE, font=font(14, True),
        fg_color=ACTION if primary else HEADER if dark else PAPER,
        hover_color=ACTION_HOVER if primary else '#333333' if dark else SELECTED,
        text_color=ON_ACTION if primary else ON_DARK if dark else INK,
        text_color_disabled='#797A74')
    settings.update(kwargs)
    widget = ctk.CTkButton(parent, text=text, command=command, **settings)
    app.controls.append(widget)
    widget._canvas.configure(takefocus=1)
    widget.bind('<FocusIn>', lambda _: widget.configure(border_width=3,
        border_color=SELECTED if dark or primary else ACTION))
    widget.bind('<FocusOut>', lambda _: widget.configure(border_width=2, border_color=LINE))
    def activate(_):
        widget.invoke()
        return 'break'
    widget.bind('<Return>', activate)
    widget.bind('<space>', activate)
    return widget


class WorkbenchPages(ctk.CTkFrame):
    """Two explicit workspace modes; switching never reconstructs an editor."""
    def __init__(self, parent, command=None):
        super().__init__(parent, fg_color=WORKSPACE, corner_radius=0)
        self._command = command
        self._name_list, self._pages, self.mode_buttons = [], {}, {}
        self._current = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.bar = ctk.CTkFrame(self, fg_color=WORKSPACE, corner_radius=0)
        self.bar.grid(row=0, column=0, sticky='ew', pady=(12, 16))
        self.bar.grid_columnconfigure((0, 1), weight=1, uniform='modes')

    def add(self, name):
        index = len(self._name_list)
        self._name_list.append(name)
        page = ctk.CTkFrame(self, fg_color=WORKSPACE, corner_radius=0)
        self._pages[name] = page
        button = ctk.CTkButton(self.bar, text=name, command=lambda: self.set(name),
            height=52, corner_radius=0, border_width=2, border_color=LINE,
            fg_color=PAPER, hover_color=SELECTED, text_color=INK,
            font=font(19, True, True))
        button.grid(row=0, column=index, sticky='ew', padx=(0, 8), pady=0)
        button._canvas.configure(takefocus=1)
        def keyboard(_):
            self.set(name)
            return 'break'
        button.bind('<Return>', keyboard)
        button.bind('<space>', keyboard)
        button.bind('<FocusIn>', lambda _: button.configure(border_color=ACTION, border_width=3))
        button.bind('<FocusOut>', lambda _: button.configure(border_color=LINE, border_width=2))
        self.mode_buttons[name] = button
        if self._current is None:
            self.set(name, notify=False)
        return page

    def get(self):
        return self._current

    def tab(self, name):
        return self._pages[name]

    def set(self, name, notify=True):
        if name not in self._pages:
            raise ValueError('Unknown workspace mode: ' + str(name))
        if self._current:
            self._pages[self._current].grid_remove()
        self._current = name
        self._pages[name].grid(row=1, column=0, sticky='nsew')
        for label, button in self.mode_buttons.items():
            button.configure(fg_color=NAV_SELECTED if label == name else PAPER)
        if notify and self._command:
            self._command()
