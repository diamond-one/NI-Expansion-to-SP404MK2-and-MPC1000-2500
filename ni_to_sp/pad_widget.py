"""A lit sampler pad: a canvas that draws its own glow, caption, tag and lock bar.

It answers the parts of the CTkButton interface the app already uses (configure, cget,
command, _canvas, _text_label), so the rest of the code does not care that it is custom.
"""
import tkinter as tk
from PIL import ImageTk

from . import desk_theme as T
from . import pad_look as L

_IMAGES = {}
_OWN = ('text', 'tag', 'glow', 'lit', 'locked', 'unlit', 'text_color', 'border_color', 'border_width',
        'fg_color', 'command', 'state_text', 'flash', 'hover_color', 'state')


def _hex(colour, fallback='#3A3A3F'):
    colour = T.resolve(colour) if colour is not None else fallback
    return colour if isinstance(colour, str) and colour.startswith('#') else fallback


class PadButton(tk.Canvas):
    def __init__(self, master, command=None, width=82, height=64, **options):
        super().__init__(master, width=width, height=height, highlightthickness=0, bd=0,
                         bg=_hex(T.WORKSPACE), takefocus=1)
        self._canvas = self
        self._text_label = self          # captions are drawn here, so events on them land on this canvas
        self._state = dict(text='', tag='', glow=None, lit=1.0, locked=False, unlit=False, text_color=None,
                           border_color=None, border_width=2, command=command, state_text='', flash=False)
        self._hover = self._pressed = self._focused = False
        self._photo = None
        self.bind('<Configure>', lambda _: self._draw(), add='+')
        self.bind('<Enter>', lambda _: self._set(_hover=True), add='+')
        self.bind('<Leave>', lambda _: self._set(_hover=False, _pressed=False), add='+')
        self.bind('<ButtonPress-1>', self._press, add='+')
        self.bind('<ButtonRelease-1>', self._release, add='+')
        self.bind('<FocusIn>', lambda _: self._set(_focused=True), add='+')
        self.bind('<FocusOut>', lambda _: self._set(_focused=False), add='+')
        if options:
            self.configure(**options)

    # -- the CTkButton surface the app uses ---------------------------------------------------
    def configure(self, cnf=None, **options):
        options = dict(cnf or {}, **options)
        own = {k: options.pop(k) for k in list(options) if k in _OWN}
        if options:
            super().configure(**options)
        legacy = own.pop('fg_color', None)       # old callers: a colour (pair) means "the glow colour"
        if legacy is not None and 'glow' not in own:
            own['glow'] = _hex(legacy, None)
        own.pop('hover_color', None)
        own.pop('state', None)
        self._state.update(own)
        self._draw()

    config = configure

    def cget(self, key):
        if key in self._state:
            return self._state[key]
        if key == 'state':
            return 'normal'
        return super().cget(key)

    def bind(self, sequence=None, func=None, add=None):
        # Always add: the pad's own press/hover/focus handling must survive the app's bindings.
        return super().bind(sequence, func, add='+')

    def invoke(self):
        if self._state['command']:
            self._state['command']()

    def flash(self):
        """Light up briefly; the caller redraws normally afterwards."""
        self._state['flash'] = True
        self._draw()

    # -- interaction -----------------------------------------------------------------------------
    def _set(self, **flags):
        for key, value in flags.items():
            setattr(self, key, value)
        self._draw()

    def _press(self, _event):
        self._set(_pressed=True)

    def _release(self, event):
        was = self._pressed
        self._set(_pressed=False)
        if was and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    # -- drawing ----------------------------------------------------------------------------------
    def _tile(self, rgb, width, height, lit, pressed, unlit, hot=.26, edge=.6):
        key = (rgb, width, height, round(lit, 2), pressed, unlit, hot, edge)
        if key not in _IMAGES:
            if len(_IMAGES) > 400:
                _IMAGES.clear()
            image = L.render_unlit(rgb, width, height) if unlit else L.render_glow(rgb, width, height, lit, pressed, hot=hot, edge=edge)
            _IMAGES[key] = image
        return ImageTk.PhotoImage(_IMAGES[key], master=self)

    def _draw(self):
        width, height = self.winfo_width(), self.winfo_height()
        if width < 12 or height < 12:
            width, height = int(self.cget('width')), int(self.cget('height'))
        state = self._state
        super().configure(bg=_hex(T.WORKSPACE))
        self.delete('all')
        glow = state['glow'] or L.STEEL
        rgb = L.hex_to_rgb(_hex(glow, L.STEEL))
        tile_ink, hot, edge = None, .26, .6
        if not state['unlit']:
            rgb, tile_ink, hot, edge = L.legible(rgb)      # keeps the colour, guarantees readable captions
        selected = state['border_width'] >= 3 and state['border_color'] is not None
        lit = state['lit'] * (1.1 if self._hover else 1.0) * (1.18 if state['flash'] else 1.0) * (1.12 if selected else 1.0)
        unlit = state['unlit']
        self._photo = self._tile(rgb, width - 2, height - 2, lit, self._pressed, unlit, hot, edge)
        self.create_image(1, 1, image=self._photo, anchor='nw')
        compact = height < 70
        ink = state['text_color'] and _hex(state['text_color'], '#F4F6F8') or ('#C9C9CF' if unlit else tile_ink)
        dim = '#8E8E95' if unlit else ink
        pad_x, pad_y = (8, 5) if compact else (10, 8)
        lines = str(state['text']).split('\n')
        size = 10 if compact else 11 if height < 130 else 13 if height < 180 else 15
        y = pad_y
        if lines:
            self.create_text(pad_x, y, text=lines[0], anchor='nw', fill=ink, font=('Bahnschrift SemiBold', size))
            y += size + (6 if compact else 8)
        for line in lines[1:]:
            last = bool(state['state_text']) and line == state['state_text']
            self.create_text(pad_x, y, text=line, anchor='nw', fill=dim if last else ink,
                             font=('Bahnschrift', size - (1 if last else 0), 'normal' if last else 'bold'))
            y += size + (4 if compact else 5)
        if state['tag'] and not state['locked']:
            self.create_text(width - pad_x, pad_y, text=state['tag'], anchor='ne', fill=dim,
                             font=('Bahnschrift', max(8, size - 2)))
        if state['locked']:
            self.create_text(width - pad_x, pad_y, text='LOCK', anchor='ne', fill=ink,
                             font=('Bahnschrift SemiBold', max(8, size - 2)))
            self.create_rectangle(8, height - 9, width - 8, height - 6, fill='#FFFFFF', outline='')
        if selected:
            self.create_rectangle(1, 1, width - 2, height - 2, outline=_hex(state['border_color'], '#FFFFFF'),
                                  width=2)
        elif state['border_color'] is not None:        # e.g. a pad that still needs a sound
            self.create_rectangle(1, 1, width - 2, height - 2, outline=_hex(state['border_color'], '#E0564B'), width=2)
        elif self._focused:
            self.create_rectangle(2, 2, width - 3, height - 3, outline='#5BE0D7', width=2, dash=(3, 2))
        else:
            self.create_rectangle(0, 0, width - 1, height - 1, outline='#0E0E10', width=1)
