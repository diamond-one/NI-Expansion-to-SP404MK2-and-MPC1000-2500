"""Groovebox panel: a matte charcoal plate, square keys with a dark bevel, DIN-style caps, lit pads.

Each colour is (light, dark). Dark is the hardware look; light is the same panel in graphite-silver.
"""
from pathlib import Path
import os
import customtkinter as ctk

HEADER = ('#2B2B2E', '#141416')            # top strip
LIBRARY = ('#CBCBD0', '#232326')           # left rail
LIBRARY_INSET = ('#BDBDC3', '#18181A')     # recessed lists and fields
WORKSPACE = ('#D6D6DA', '#2A2A2D')         # the plate
PAPER = ('#EDEDF0', '#35353A')             # key face and raised panels
INK = ('#1C1C1F', '#E9E9EB')
MUTED = ('#5A5A62', '#A2A2AA')
ON_DARK = ('#F0F0F2', '#F0F0F2')
ON_ACTION = ('#0E1112', '#0E1112')
DARK_MUTED = ('#46464E', '#A2A2AA')      # muted text on the rail
HEADER_MUTED = ('#A6A6AE', '#A2A2AA')    # muted text on the always-dark top strip
ACTION = ('#2FC4BA', '#35D0C6')            # the one lit control colour
ACTION_HOVER = ('#52D8CF', '#5BE0D7')
KEY_HOVER = ('#F8F8FA', '#434349')
DARK_KEY = ('#38383C', '#1E1E21')          # keys on the dark strip and rail
DARK_KEY_HOVER = ('#48484D', '#2D2D31')
LINE = ('#9A9AA3', '#0D0D0F')              # key bevel: near-black on the dark plate
SOFT = ('#DEDEE2', '#2F2F33')
SELECTED = ('#8DE3DC', '#216F6A')          # a pressed-in, lit-from-within key
NAV_SELECTED = ('#8DE3DC', '#216F6A')       # the active mode shares the one lit-key colour
MISSING = ('#F2B5B0', '#4A2325')
MISSING_INK = ('#7A1F18', '#FFB3AA')
PREVIEW = ('#D6CAE9', '#3A2F4D')
PREVIEW_INK = ('#443354', '#E3D4FF')
EMPTY = ('#CFCFD4', '#26262A')
FOCUS = ('#1E9E96', '#5BE0D7')
DISABLED_ON_LIT = ('#1B423F', '#1B423F')    # a disabled key that is still lit teal
DISABLED_ON_SOFT = ('#6E6E77', '#8E8E97')   # a disabled key that has gone to the soft face
OLED = ('#0B0C0D', '#050506')              # readout panel
OLED_INK = ('#EDEFF1', '#EDEFF1')
_fonts_loaded = False


def resolve(colour, mode=None):
    """Resolve a paired colour for native Tk surfaces that cannot use CTk pairs."""
    if isinstance(colour, (tuple, list)):
        return colour[1 if (mode or ctk.get_appearance_mode()).lower() == 'dark' else 0]
    return colour


def refresh_native(app):
    app.kit_list.configure(bg=resolve(LIBRARY_INSET), fg=resolve(INK),
        selectbackground=resolve(SELECTED), selectforeground=resolve(INK),
        highlightbackground=resolve(LINE), highlightcolor=resolve(FOCUS))
    app.waveform.configure(background=resolve(WORKSPACE))
    app.waveform._draw()
    for pad in getattr(app, 'pad_buttons', {}).values():
        if hasattr(pad, '_draw'):
            pad._draw()


def load_fonts():
    global _fonts_loaded
    if not _fonts_loaded:
        for path in (Path(__file__).parent / 'assets/fonts').glob('*.ttf'):
            if os.name == 'nt':
                ctk.FontManager.windows_load_font(str(path), private=True, enumerable=True)
            else:
                ctk.FontManager.load_font(str(path))
        _fonts_loaded = True


_family_cache = {}


def family(display=False, mono=False):
    """Bahnschrift (DIN-style, ships with Windows) when present, else Segoe UI / the system default."""
    key = (display, mono)
    if key not in _family_cache:
        try:
            import tkinter.font as tkfont
            names = set(tkfont.families())
        except Exception:
            names = set()
        wanted = (('Cascadia Mono', 'Consolas', 'Courier New') if mono else
                  ('Bahnschrift SemiCondensed', 'Bahnschrift', 'Segoe UI Semibold', 'Segoe UI') if display else
                  ('Bahnschrift', 'Segoe UI'))
        _family_cache[key] = next((n for n in wanted if n in names), wanted[-1])
    return _family_cache[key]


def font(size=14, bold=False, display=False, mono=False):
    load_fonts()
    return ctk.CTkFont(family=family(display, mono), size=size, weight='bold' if bold else 'normal')


def label(parent, text='', size=14, color=INK, bold=False, display=False, **kwargs):
    kwargs.setdefault('height', max(18, size + 6))
    return ctk.CTkLabel(parent, text=text, text_color=color,
                       font=font(size, bold, display), **kwargs)


def button(app, parent, text, command, primary=False, dark=False, **kwargs):
    """A square matte key; primary is the one lit key, dark is for the strip and rail."""
    settings = dict(height=38, corner_radius=3, border_width=2,
        border_color=LINE, font=font(13, True),
        fg_color=ACTION if primary else DARK_KEY if dark else PAPER,
        hover_color=ACTION_HOVER if primary else DARK_KEY_HOVER if dark else KEY_HOVER,
        text_color=ON_ACTION if primary else ON_DARK if dark else INK,
        text_color_disabled=('#1B423F', '#1B423F') if primary else ('#7A7A83', '#8E8E97'))
    settings.update(kwargs)
    widget = ctk.CTkButton(parent, text=text, command=command, **settings)
    app.controls.append(widget)
    widget._canvas.configure(takefocus=1)
    widget.bind('<FocusIn>', lambda _: widget.configure(border_width=2, border_color=FOCUS))
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
        self.bar.grid(row=0, column=0, sticky='ew', pady=(8, 8))
        self.bar.grid_columnconfigure((0, 1), weight=1, uniform='modes')

    def add(self, name):
        index = len(self._name_list)
        self._name_list.append(name)
        self.bar.grid_columnconfigure(index, weight=1, uniform='modes')
        page = ctk.CTkFrame(self, fg_color=WORKSPACE, corner_radius=0)
        self._pages[name] = page
        button = ctk.CTkButton(self.bar, text=name, command=lambda: self.set(name),
            height=36, corner_radius=3, border_width=2, border_color=LINE,
            fg_color=PAPER, hover_color=KEY_HOVER, text_color=INK,
            font=font(15, True, True))
        button.grid(row=0, column=index, sticky='ew', padx=(0, 8), pady=0)
        button._canvas.configure(takefocus=1)
        def keyboard(_):
            self.set(name)
            return 'break'
        button.bind('<Return>', keyboard)
        button.bind('<space>', keyboard)
        button.bind('<FocusIn>', lambda _: button.configure(border_color=FOCUS))
        button.bind('<FocusOut>', lambda _: button.configure(border_color=LINE))
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
