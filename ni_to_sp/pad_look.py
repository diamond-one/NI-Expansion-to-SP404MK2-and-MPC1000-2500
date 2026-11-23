"""How a pad looks: the colour of a sound type and the lit-tile glow. No Tk in here."""
import colorsys
from functools import lru_cache

import numpy as np
from PIL import Image

# One colour per kind of sound, hardware-pad bright. Chosen so neighbours in a kit stay distinct.
KIND_COLOURS = {
    'kick': '#F0503C', 'snare': '#F2A63B', 'clap': '#E9DB54', 'rim': '#B4D95C',
    'hat': '#3FCFC4', 'open_hat': '#4ADCE8', 'cymbal': '#3DA5F0', 'perc': '#4F86F2',
}
LOOP = '#5CD98B'
PRESET = '#B9A2FF'
STEEL = '#B6C0CF'
EMPTY = '#3A3A3F'
# First matching word of a family key ('oneshots:synthnote/bass') picks its colour.
_FAMILY_WORDS = (
    (('bass', 'sub'), '#8B63F0'),
    (('pad', 'pads', 'ambience', 'strings', 'atmos', 'drone'), '#D98BFF'),
    (('lead', 'synth', 'arp', 'pluck', 'mono'), '#FF80C0'),
    (('chord', 'chords', 'combo', 'stab', 'hit', 'hits', 'brass'), '#7B7CF0'),
    (('vocal', 'vocals', 'vox', 'choir', 'voice'), '#FF9F80'),
    (('sfx', 'noise', 'sweep', 'riser', 'fx', 'analogfx', 'impact', 'scratch', 'siren'), '#9BD94A'),
    (('keys', 'piano', 'organ', 'rhodes', 'lick', 'guitar', 'bells', 'mallet', 'flute', 'pianokeys'), '#5BC0F0'),
)


def hex_to_rgb(colour):
    colour = colour.lstrip('#')
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return '#%02X%02X%02X' % tuple(int(max(0, min(255, round(c)))) for c in rgb)


def pad_colour(kind, name=''):
    """Hex colour for a drum kind or library family key ('oneshots:synthnote/bass'); steel when unknown."""
    kind = kind or ''
    if kind in KIND_COLOURS:
        return KIND_COLOURS[kind]
    if kind.startswith('previews:'):
        return PRESET
    if kind.startswith('loops:'):
        return LOOP
    words = [w for part in kind.replace(':', '/').split('/') for w in (part, )]
    for key_words, colour in _FAMILY_WORDS:
        if any(w in key_words for w in reversed(words)):
            return colour
    return STEEL


def luminance(rgb):
    r, g, b = (c / 255 for c in rgb)
    return .2126 * r + .7152 * g + .0722 * b


DARK_INK = '#0B0D0E'


def contrast(a, b):
    """WCAG contrast ratio of two RGB triples."""
    high, low = sorted((luminance(_linear(a)), luminance(_linear(b))), reverse=True)
    return (high + .05) / (low + .05)


def _linear(rgb):
    """sRGB 0-255 -> a triple whose Rec.709 weighting is WCAG relative luminance."""
    return tuple(255 * (c / 255 / 12.92 if c / 255 <= .04045 else ((c / 255 + .055) / 1.055) ** 2.4) for c in rgb)


def ink_for(rgb):
    """Dark ink on lit tiles (as on the hardware); tiles are made light enough by legible()."""
    return DARK_INK


def _caption_zone(width=120, height=60):
    return slice(int(height * .1), int(height * .6)), slice(int(width * .05), int(width * .7))


LIGHT_INK = '#F6F8FA'


def _from_hsv(h, sat, val):
    return tuple(c * 255 for c in colorsys.hsv_to_rgb(h, max(0, min(1, sat)), max(0, min(1, val))))


@lru_cache(maxsize=512)
def legible(rgb, target=4.5):
    """(tile colour, ink, hot-core strength, edge level) for a pad colour.

    Bright colours keep dark ink: value is raised first (saturation kept, so they stay vivid) and only
    then eased towards white, with a bright tile edge so the caption area is even. Deep, saturated
    colours take light ink and are darkened. Deep wins only when it needs clearly less change. Every lit
    tile reaches `target` contrast over its whole caption area.
    """
    rows, cols = _caption_zone()
    hue, sat, val = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))

    def passes(tile, ink, hot, edge):
        pixels = np.array(render_glow(tile, 120, 60, hot=hot, edge=edge))[..., :3][rows, cols].reshape(-1, 3)[::6]
        ink = hex_to_rgb(ink)
        return min(contrast(ink, tuple(int(v) for v in px)) for px in pixels) >= target

    light = [(sat, val + (1 - val) * k / 8) for k in range(9)] + [(sat * (1 - .4 * j / 8), 1.0) for j in range(1, 9)]
    deep = [(sat, val * (1 - k * .05)) for k in range(15)]
    lightest = next((i / (len(light) - 1) for i, (sa, va) in enumerate(light)
                     if passes(_from_hsv(hue, sa, va), DARK_INK, .22, .78)), None)
    deepest = next((k / (len(deep) - 1) for k, (sa, va) in enumerate(deep)
                    if passes(_from_hsv(hue, sa, va), LIGHT_INK, .05, .55)), None)
    if deepest is not None and (lightest is None or deepest < lightest * .6):
        sa, va = deep[round(deepest * (len(deep) - 1))]
        return tuple(int(round(c)) for c in _from_hsv(hue, sa, va)), LIGHT_INK, .05, .55
    if lightest is not None:
        sa, va = light[round(lightest * (len(light) - 1))]
        return tuple(int(round(c)) for c in _from_hsv(hue, sa, va)), DARK_INK, .22, .78
    return tuple(int(round(c + (255 - c) * .76)) for c in rgb), DARK_INK, .22, .78


def tag_for(kind):
    """Short caps tag for the pad's top-right corner."""
    if not kind:
        return ''
    if kind.startswith('previews:'):
        return 'PRESET'
    from .autofill import kind_label
    return kind_label(kind).upper()[:11]


def _rounded_mask(w, h, radius):
    yy, xx = np.mgrid[0:h, 0:w]
    px = np.minimum(xx, w - 1 - xx)
    py = np.minimum(yy, h - 1 - yy)
    inside = np.ones((h, w), dtype=float)
    corner = (px < radius) & (py < radius)
    dist = np.sqrt((radius - px - .5) ** 2 + (radius - py - .5) ** 2)
    inside[corner] = np.clip(radius - dist + .5, 0, 1)[corner]
    return inside


def render_glow(rgb, width, height, lit=1.0, pressed=False, radius=5, hot=.26, edge=.6):
    """RGBA tile: bright core fading to a darker edge, a thin top highlight and a lower shadow."""
    width, height = max(8, int(width)), max(8, int(height))
    yy, xx = np.mgrid[0:height, 0:width]
    dx = (xx - (width - 1) / 2) / (width / 2)
    dy = (yy - (height - 1) / 2) / (height / 2)
    distance = np.clip(np.sqrt(dx ** 2 + dy ** 2) / 1.12, 0, 1)
    core = (1 - distance) ** 1.55
    base = np.array(rgb, dtype=float) / 255
    rim = base * edge
    colour = rim + (np.minimum(base * 1.1, 1) - rim) * core[..., None]
    colour += (1 - colour) * (hot * core ** 3)[..., None]            # white-hot centre
    colour *= lit * (.86 if pressed else 1.0)
    rows = np.linspace(0, 1, height)[:, None, None]
    colour *= 1 - .22 * np.clip((rows - .78) / .22, 0, 1)             # shadow along the lower edge
    colour[0] = np.minimum(colour[0] * 1.18 + .05, 1)                  # top highlight
    colour[:, 0] = np.minimum(colour[:, 0] * 1.06, 1)
    alpha = _rounded_mask(width, height, radius)[..., None]
    out = np.concatenate([np.clip(colour, 0, 1), alpha], axis=2)
    return Image.fromarray((out * 255).astype('uint8'))


def render_unlit(rgb, width, height, radius=5):
    """An unlit key: a dark face that only hints at its colour."""
    return render_glow(tuple(int(.28 * c + .72 * e) for c, e in zip(rgb, hex_to_rgb(EMPTY))), width, height,
                       lit=.78, radius=radius)
