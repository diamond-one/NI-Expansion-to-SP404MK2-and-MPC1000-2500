"""The groovebox-panel look: pad colours, glow tiles, caption legibility, and the lit pad widget."""
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import numpy as np

from ni_to_sp import desk_theme as T
from ni_to_sp import pad_look as L
from test_swap import mixed_kit
from ni_to_sp.direct import inspect_group

FAMILY_SAMPLES = ('oneshots:synthnote/bass', 'oneshots:synthnote/pad', 'oneshots:synthnote/lead',
                  'oneshots:chord/chord', 'oneshots:sfx/sfx', 'loops:vocals/vocal', 'loops:bass/bass',
                  'previews:massivesounds', 'instruments:bass', 'oneshots:vocal/vocal', 'oneshots:combo/stab')


class ColourTests(unittest.TestCase):
    def test_every_drum_kind_has_its_own_colour(self):
        self.assertEqual(len({L.pad_colour(k) for k in L.KIND_COLOURS}), len(L.KIND_COLOURS))

    def test_families_map_by_instrument_word_loops_and_presets_by_kind(self):
        c = L.pad_colour
        self.assertEqual(c('oneshots:synthnote/bass'), c('instruments:bass'))     # a bass is a bass
        self.assertNotEqual(c('oneshots:synthnote/bass'), c('oneshots:synthnote/pad'))
        self.assertNotEqual(c('oneshots:synthnote/pad'), c('oneshots:synthnote/lead'))
        self.assertEqual(c('loops:vocals/vocal'), L.LOOP)
        self.assertEqual(c('loops:bass/bass'), L.LOOP)
        self.assertEqual(c('previews:massivesounds'), L.PRESET)
        self.assertEqual(c(None), L.STEEL)
        self.assertEqual(c('something:unheard/of'), L.STEEL)

    def test_tags_are_short_caps(self):
        self.assertEqual(L.tag_for('kick'), 'KICK')
        self.assertEqual(L.tag_for('open_hat'), 'OPEN HAT')
        self.assertEqual(L.tag_for('oneshots:synthnote/bass'), 'BASS')
        self.assertEqual(L.tag_for('previews:bass'), 'PRESET')
        self.assertEqual(L.tag_for(None), '')
        self.assertTrue(all(len(L.tag_for(k)) <= 11 for k in list(L.KIND_COLOURS) + list(FAMILY_SAMPLES)))


class GlowTests(unittest.TestCase):
    def test_glow_is_brightest_in_the_middle_with_rounded_corners(self):
        image = L.render_glow((60, 160, 220), 120, 80)
        a = np.array(image).astype(float)
        self.assertEqual(image.size, (120, 80))
        self.assertLess(a[0, 0, 3], 80)                                 # corner is mostly transparent
        self.assertEqual(a[40, 60, 3], 255)
        self.assertGreater(a[40, 60, :3].sum(), a[40, 6, :3].sum() * 1.2)  # core beats edge

    def test_lit_pressed_and_unlit_change_brightness_in_the_right_direction(self):
        mid = lambda img: np.array(img).astype(float)[30:50, 40:80, :3].mean()
        base = mid(L.render_glow((200, 120, 60), 120, 80))
        self.assertGreater(mid(L.render_glow((200, 120, 60), 120, 80, lit=1.2)), base)
        self.assertLess(mid(L.render_glow((200, 120, 60), 120, 80, pressed=True)), base)
        self.assertLess(mid(L.render_unlit((200, 120, 60), 120, 80)), base * .6)

    def test_tiny_sizes_do_not_crash(self):
        for size in ((1, 1), (8, 8), (30, 14)):
            self.assertGreaterEqual(L.render_glow((10, 200, 100), *size).size[0], 8)


class LegibilityTests(unittest.TestCase):
    """Captions are small bold text, so they need 4.5:1 over the whole caption area of every pad colour."""

    def colours(self):
        names = list(L.KIND_COLOURS) + list(FAMILY_SAMPLES) + ['loops:x/y', 'previews:z', None]
        return {L.pad_colour(n) for n in names} | {'#FF0000', '#00FF00', '#0000FF', '#FFFFFF', '#808080', '#222222'}

    def test_dark_or_light_ink_reaches_4_5_on_every_colour_at_every_pad_size(self):
        for hexcol in sorted(self.colours()):
            tile, ink, hot, edge = L.legible(L.hex_to_rgb(hexcol))
            for width, height in ((120, 45), (150, 70), (170, 100)):
                rgb = np.array(L.render_glow(tile, width, height, hot=hot, edge=edge))[..., :3]
                zone = rgb[int(height * .1):int(height * .6), int(width * .05):int(width * .7)].reshape(-1, 3)[::5]
                worst = min(L.contrast(L.hex_to_rgb(ink), tuple(int(v) for v in px)) for px in zone)
                self.assertGreaterEqual(worst, 4.5, f'{hexcol} at {width}x{height}: {worst:.2f}')

    def test_colours_keep_their_hue(self):
        import colorsys
        for hexcol in sorted(self.colours() - {'#FFFFFF', '#808080', '#222222'}):
            tile = L.legible(L.hex_to_rgb(hexcol))[0]
            before = colorsys.rgb_to_hsv(*(c / 255 for c in L.hex_to_rgb(hexcol)))
            after = colorsys.rgb_to_hsv(*(c / 255 for c in tile))
            if before[1] > .25 and after[1] > .08:                          # a near-white tile has no real hue
                self.assertLess(min(abs(before[0] - after[0]), 1 - abs(before[0] - after[0])), .04, hexcol)

    def test_saturated_reds_blues_and_violets_stay_vivid_not_pastel(self):
        for kind in ('kick', 'perc'):
            tile = L.legible(L.hex_to_rgb(L.pad_colour(kind)))[0]
            import colorsys
            self.assertGreater(colorsys.rgb_to_hsv(*(c / 255 for c in tile))[1], .5, kind)

    def test_every_family_stays_vivid_after_the_legibility_pass(self):
        import colorsys
        for kind in list(L.KIND_COLOURS) + ['loops:x/y', 'oneshots:synthnote/bass', 'oneshots:synthnote/pad',
                                           'oneshots:synthnote/lead', 'oneshots:chord/chord', 'oneshots:sfx/sfx',
                                           'oneshots:vocal/vocal', 'previews:x']:
            tile = L.legible(L.hex_to_rgb(L.pad_colour(kind)))[0]
            self.assertGreaterEqual(colorsys.rgb_to_hsv(*(c / 255 for c in tile))[1], .33, f'{kind} washed out')

    def test_families_stay_visibly_distinct_after_the_legibility_pass(self):
        import itertools
        kinds = list(L.KIND_COLOURS) + ['loops:x/y', 'oneshots:synthnote/bass', 'oneshots:synthnote/pad',
                                        'oneshots:synthnote/lead', 'oneshots:chord/chord', 'oneshots:sfx/sfx',
                                        'oneshots:vocal/vocal', 'previews:x']
        tiles = {k: L.legible(L.hex_to_rgb(L.pad_colour(k)))[0] for k in kinds}
        for a, b in itertools.combinations(kinds, 2):
            distance = sum((x - y) ** 2 for x, y in zip(tiles[a], tiles[b])) ** .5
            self.assertGreaterEqual(distance, 28, f'{a} and {b} look alike ({distance:.0f})')

    def test_theme_text_pairs_are_readable_in_both_modes(self):
        def lum(colour, mode):
            return L.luminance(L._linear(L.hex_to_rgb(T.resolve(colour, mode))))
        for mode in ('light', 'dark'):
            for foreground, background in ((T.INK, T.WORKSPACE), (T.INK, T.PAPER), (T.MUTED, T.WORKSPACE),
                                           (T.MUTED, T.PAPER), (T.INK, T.LIBRARY), (T.DARK_MUTED, T.LIBRARY),
                                           (T.ON_DARK, T.HEADER), (T.HEADER_MUTED, T.HEADER), (T.OLED_INK, T.OLED),
                                           (T.ON_ACTION, T.ACTION), (T.INK, T.SELECTED)):
                high, low = sorted((lum(foreground, mode), lum(background, mode)), reverse=True)
                self.assertGreaterEqual((high + .05) / (low + .05), 4.5, (mode, foreground, background))


class KeyTests(unittest.TestCase):
    def test_a_disabled_lit_key_keeps_readable_text(self):
        """Disabled primary keys stay teal; their label must still be legible (it was pinkish grey on teal)."""
        import customtkinter as ctk
        from types import SimpleNamespace
        root = tk.Tk()
        try:
            app = SimpleNamespace(controls=[])
            key = T.button(app, root, 'Export', lambda: None, primary=True)
            pair = key.cget('text_color_disabled')
            for mode, index in (('light', 0), ('dark', 1)):
                text = L.hex_to_rgb(pair[index] if isinstance(pair, (tuple, list)) else pair)
                face = L.hex_to_rgb(T.resolve(T.ACTION, mode))
                self.assertGreaterEqual(L.contrast(text, face), 4.0, mode)
        finally:
            root.destroy()


class PadWidgetTests(unittest.TestCase):
    def setUp(self):
        from ni_to_sp.pad_widget import PadButton
        self.root = tk.Tk()
        self.root.geometry('400x300+10000+10000')
        self.clicks = []
        self.pad = PadButton(self.root, command=lambda: self.clicks.append(1), width=160, height=90)
        self.pad.pack(padx=10, pady=10)
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def items(self, kind):
        return [self.pad.itemcget(i, 'text') for i in self.pad.find_all() if self.pad.type(i) == kind]

    def test_it_answers_the_button_interface_the_app_uses(self):
        self.pad.configure(text='A05\nKick Mine\nNI preview', tag='KICK', glow='#F0503C', state_text='NI preview')
        self.assertEqual(self.pad.cget('text'), 'A05\nKick Mine\nNI preview')
        self.assertIs(self.pad._canvas, self.pad)
        self.assertIs(self.pad._text_label, self.pad)
        self.assertGreater(self.pad.winfo_height(), 40)
        self.assertEqual(self.pad.cget('state'), 'normal')

    def test_captions_tag_and_state_are_drawn(self):
        self.pad.configure(text='A05\nKick Mine\nNI preview', tag='KICK', state_text='NI preview', glow='#F0503C')
        self.root.update()
        drawn = self.items('text')
        for expected in ('A05', 'Kick Mine', 'NI preview', 'KICK'):
            self.assertIn(expected, drawn)

    def test_locked_pads_show_lock_and_a_bar_instead_of_the_tag(self):
        self.pad.configure(text='A02\nSnare', tag='SNARE', glow='#F2A63B', locked=False)
        bars_before = len([i for i in self.pad.find_all() if self.pad.type(i) == 'rectangle'])
        self.pad.configure(locked=True)
        self.root.update()
        self.assertIn('LOCK', self.items('text'))
        self.assertNotIn('SNARE', self.items('text'))
        self.assertGreater(len([i for i in self.pad.find_all() if self.pad.type(i) == 'rectangle']), bars_before)

    def test_click_runs_the_command_but_dragging_off_does_not(self):
        self.pad.event_generate('<ButtonPress-1>', x=20, y=20)
        self.pad.event_generate('<ButtonRelease-1>', x=20, y=20)
        self.assertEqual(len(self.clicks), 1)
        self.pad.event_generate('<ButtonPress-1>', x=20, y=20)
        self.pad.event_generate('<ButtonRelease-1>', x=900, y=900)
        self.assertEqual(len(self.clicks), 1)
        self.pad.invoke()
        self.assertEqual(len(self.clicks), 2)

    def test_app_bindings_add_to_the_pads_own_and_never_replace_them(self):
        seen = []
        self.pad.bind('<FocusIn>', lambda e: seen.append('app'))
        self.pad.focus_force()
        self.root.update()
        self.assertEqual(seen, ['app'])
        self.assertTrue(self.pad._focused)                  # the pad's own FocusIn handler still ran

    def test_selected_missing_and_flash_states_draw_without_error_and_reuse_images(self):
        from ni_to_sp import pad_widget
        self.pad.configure(text='A01\nX', glow='#3FCFC4', border_width=3, border_color='#FFFFFF')
        self.root.update()
        self.pad.configure(border_width=2, border_color='#E0564B', unlit=True)
        self.root.update()
        self.pad.flash()
        before = len(pad_widget._IMAGES)
        for _ in range(5):
            self.pad.configure(glow='#3FCFC4')
        self.assertEqual(len(pad_widget._IMAGES), before)    # the glow tile is cached, not re-rendered

    def test_legacy_colour_pairs_are_accepted_as_the_glow(self):
        self.pad.configure(fg_color=T.SELECTED, text_color=T.INK, border_color=T.LINE, border_width=2)
        self.root.update()
        self.assertTrue(self.pad._state['glow'].startswith('#'))


class AppLookTests(unittest.TestCase):
    def setUp(self):
        from ni_to_sp.converter_ui import App
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kit = mixed_kit(self.root / 'kit')
        self.env = patch.dict(os.environ, {'NI_TO_SP_DATA_DIR': str(self.root / 'data'), 'NI_TO_SP_DISABLE_AUDIO': '1', 'NI_TO_SP_KITMAKER_AUTOSTART': '0'})
        self.env.start()
        with patch.object(App, 'scan', lambda _: None):
            self.app = App()
        self.app.tabs.set('Play & export')   # these tests drive the editor, not the Make kits tab
        self.app.geometry('1380x920+10000+10000')      # roomy pads: corner tag and the full state line
        self.app.update()
        self.app.kits = [self.kit]
        self.app._install_group(inspect_group(self.kit, use_previews=False))
        self.app.update()

    def tearDown(self):
        self.app.busy = False
        self.app.close()
        self.env.stop()
        self.temp.cleanup()

    def pad(self, i):
        return self.app.pad_buttons[i]

    def test_the_app_starts_in_the_dark_panel_look(self):
        self.assertEqual(self.app.theme_mode, 'dark')
        self.assertEqual(self.app.theme_button.cget('text'), 'Theme: Dark')

    def test_pads_are_coloured_by_what_they_hold(self):
        # mixed kit: kick, snare, closed hat, bass, bass, pad, pad, sfx, loops, loops, chord, lead
        expected = ['kick', 'snare', 'hat', 'oneshots:synthnote/bass', 'oneshots:synthnote/bass',
                    'oneshots:synthnote/pad', 'oneshots:synthnote/pad', 'oneshots:sfx/sfx', 'loops:vocals/vocal',
                    'loops:vocals/vocal', 'oneshots:chord/chord', 'oneshots:synthnote/lead']
        for i, kind in enumerate(expected):
            self.assertEqual(self.pad(i)._state['glow'], L.pad_colour(kind), f'pad {i}')
            self.assertFalse(self.pad(i)._state['unlit'])
        self.assertEqual(self.pad(0)._state['tag'], 'KICK')
        self.assertEqual(self.pad(3)._state['tag'], 'BASS')

    def test_pads_without_a_sound_are_unlit_and_the_unresolved_ones_have_a_red_rim(self):
        for i in range(12, 16):                       # 'Sound 13..16': nothing to play, nothing to call them
            self.assertTrue(self.pad(i)._state['unlit'])
            self.assertEqual(self.pad(i)._state['border_color'], '#E0564B')
            self.assertIn('Needs sound', self.pad(i).cget('text'))

    def test_compact_pads_drop_the_tag_but_keep_number_name_and_state_words(self):
        self.app.geometry('1120x800+10000+10000')
        self.app.update()
        self.app.show_group()
        self.app.update()
        self.assertEqual(self.pad(0)._state['tag'], '')
        self.assertIn('A01', self.pad(0).cget('text'))
        self.assertIn('missing', self.pad(12).cget('text'))
        self.assertGreaterEqual(self.pad(0).winfo_height(), 44)

    def test_the_selected_pad_has_a_white_rim_and_others_do_not(self):
        self.app.focus_pad(5)
        self.app.update()
        self.assertEqual(self.pad(5)._state['border_color'], '#FFFFFF')
        self.assertGreaterEqual(self.pad(5)._state['border_width'], 3)
        self.assertNotEqual(self.pad(4)._state['border_color'], '#FFFFFF')

    def test_locking_shows_the_lock_marker_and_unlocking_removes_it(self):
        self.app.toggle_lock(2)
        self.app.update()
        self.assertTrue(self.pad(2)._state['locked'])
        self.app.toggle_lock(2)
        self.app.update()
        self.assertFalse(self.pad(2)._state['locked'])

    def test_swapping_a_pad_keeps_its_colour_family(self):
        from test_browser import wait
        before = [self.pad(i)._state['glow'] for i in range(12)]
        self.app.swap_unlocked()
        wait(self.app)
        self.app.update()
        self.assertEqual([self.pad(i)._state['glow'] for i in range(12)], before)

    def test_the_theme_toggle_repaints_pads_without_losing_their_colour(self):
        glows = [self.pad(i)._state['glow'] for i in range(16)]
        self.app.toggle_theme()
        self.app.update()
        self.assertEqual(self.app.theme_mode, 'light')
        self.assertEqual([self.pad(i)._state['glow'] for i in range(16)], glows)
        self.assertEqual(self.pad(0).cget('bg').lower(), T.resolve(T.WORKSPACE, 'light').lower())
        self.app.toggle_theme()
        self.app.update()
        self.assertEqual(self.pad(0).cget('bg').lower(), T.resolve(T.WORKSPACE, 'dark').lower())

    def test_the_readout_follows_the_selected_pad(self):
        self.app.focus_pad(3)
        self.app.update()
        self.assertTrue(self.app.oled_detail.cget('text').startswith('A04'))
        self.assertIn('Bass A Mine', self.app.oled_detail.cget('text'))
        self.app.focus_pad(0)
        self.app.update()
        self.assertIn('Kick Mine', self.app.oled_detail.cget('text'))

    def test_lock_and_swap_are_reachable_without_scrolling_at_the_minimum_window(self):
        self.app.geometry('1120x800+10000+10000')
        self.app.update()
        self.app.show_group()
        self.app.update()
        for key in (self.app.lock_button, self.app.swap_button):
            self.assertLess(key.winfo_rooty() + key.winfo_height(), self.app.mpc_button.winfo_rooty())
            self.assertGreater(key.winfo_rooty(), self.app.winfo_rooty())

    def test_compact_pads_show_the_whole_name_when_it_fits(self):
        self.app.geometry('1120x800+10000+10000')
        self.app.update()
        self.app.show_group()
        self.app.update()
        self.assertIn('Bass A Mine', self.pad(3).cget('text'))           # not 'Bass…'
        long_name = self.pad(8).cget('text').split('\n')[1]
        self.assertTrue(long_name.endswith('…') or long_name == 'Vocal[120] Am Mine', long_name)
        self.assertGreater(len(long_name), 8)

    def test_disabled_keys_keep_readable_text_on_whichever_face_they_wear(self):
        # no candidate: Keep sound is disabled on the soft face; it must not be dark-teal-on-grey
        pair = self.app.keep_button.cget('text_color_disabled')
        for mode, index in (('light', 0), ('dark', 1)):
            ink = L.hex_to_rgb(pair[index])
            face = L.hex_to_rgb(T.resolve(self.app.keep_button.cget('fg_color'), mode))
            self.assertGreaterEqual(L.contrast(ink, face), 3.0, mode)

    def test_the_active_mode_tab_uses_the_one_lit_key_colour_not_a_second_accent(self):
        self.assertEqual(T.NAV_SELECTED, T.SELECTED)

    def test_pads_get_most_of_the_vertical_space_and_wide_gutters(self):
        heights = {self.pad(i).winfo_height() for i in range(16)}
        self.assertGreaterEqual(min(heights), 80)                     # roomy tiles at the default size
        grid = self.pad(0).grid_info()
        self.assertGreaterEqual(int(grid['padx']) + int(grid['pady']), 12)

    def test_the_kit_name_is_shown_in_the_oled_readout(self):
        self.assertEqual(self.app.kit_title.cget('text'), 'Mixed Kit')
        self.assertEqual(T.resolve(self.app.kit_title.cget('fg_color'), 'dark'), T.resolve(T.OLED, 'dark'))

    def test_right_click_and_drop_registration_still_work_on_the_new_pads(self):
        from unittest.mock import patch as mock
        shown = []
        with mock.object(tk.Menu, 'tk_popup', lambda menu, x, y: shown.append(menu)):
            self.pad(3).event_generate('<Button-3>', x=10, y=10)
            self.app.update()
        self.assertEqual(len(shown), 1)
        self.assertEqual(self.app.selected_pad, 3)


if __name__ == '__main__':
    unittest.main()
