from pathlib import Path
import tempfile
import threading
import unittest
import unittest.mock

import numpy as np
import soundfile as sf

from direct_fixtures import direct_fixture, record
from ni_to_sp import autofill as af
from ni_to_sp import kit_editor as ed
from ni_to_sp.direct import inspect_group, usable


def tone(path, freq, decay, rate=48000, seconds=.5):
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(rate * seconds)) / rate
    sf.write(path, .5 * np.sin(2 * np.pi * freq * t) * np.exp(-t * decay), rate, subtype='PCM_24')
    return path


def drumsynth_fixture(root, engine='Kick', name='Synth Kit'):
    """A kit whose first pad is a DrumSynth engine and whose others are samples."""
    root = Path(root)
    pack = root / 'installed/Test Library'
    source = pack / 'Groups/Kits' / (name + '.mxgrp')
    source.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(b'Generated test archive\0')
    for index in range(16):
        title = ('Kick' if index == 0 else 'Snare' if index == 1 else 'Sound') + f' Test {index + 1}'
        data += record(title, 1000 + index * 30, False, index == 0)
        if index == 0:
            data += f'NI::MASCHINE::DATA::{engine}\0'.encode()
            continue
        relative = f'Samples/Drums/Snares/Resolved {index}.wav'
        tone(pack / relative, 200 + index * 30, 30)
        data += relative.encode() + b'\0'
    data += record(name, 2000, False)
    source.write_bytes(data)
    return {'id': name, 'name': name, 'pack': 'Test', 'root': str(pack), 'path': str(source), 'preview': None}


class ClassifyTests(unittest.TestCase):
    def test_folder_labels_win_and_loops_are_excluded(self):
        c = af.classify_sample
        self.assertEqual(c('C:/NI/X Library/Samples/Drums/Kicks/Thing.wav'), 'kick')
        self.assertEqual(c('C:/NI/X Library/Samples/Drums/Hi-Hats/OpenHH 3.wav'), 'open_hat')
        self.assertEqual(c('C:/NI/X Library/Samples/Drums/Snares/Hat slap.wav'), 'snare')
        self.assertIsNone(c('C:/NI/X Library/Samples/Loops/Kick loop.wav'))
        self.assertIsNone(c('C:/NI/X Library/Samples/Instruments/Kick synth.wav'))
        self.assertIsNone(c('C:/NI/X Library/Samples/Drums/Snares/DrumFill 1.wav'))
        # NI files rims, claps, shakers and open hats in the snare/hat folders.
        self.assertEqual(c('C:/NI/X Library/Samples/Drums/Snares/Rim ProgramSet.wav'), 'rim')
        self.assertEqual(c('C:/NI/X Library/Samples/Drums/Snares/Clap Alorean.wav'), 'clap')
        self.assertEqual(c('C:/NI/X Library/Samples/Drums/Hi-Hats/Shaker WhiteNeon.wav'), 'perc')
        self.assertEqual(c('C:/NI/X Library/Samples/Drums/Hi-Hats/ClosedHH Goma 3.wav'), 'hat')
        self.assertEqual(c('C:/NI/X Library/Samples/Drums/Snares/Snare Sansho 1.wav'), 'snare')

    def test_pad_name_beats_engine_which_beats_reference_layers(self):
        t = af.pad_target_kind
        # DrumSynth Hihat/Snare engines are reused for cymbals, open hats and claps.
        self.assertEqual(t({'engine_kind': 'hat', 'name': 'Crash MoGin'}), 'cymbal')
        self.assertEqual(t({'engine_kind': 'hat', 'name': 'OpenHH Flame'}), 'open_hat')
        self.assertEqual(t({'engine_kind': 'snare', 'name': 'Clap Spree'}), 'clap')
        self.assertEqual(t({'engine_kind': 'perc', 'name': 'MidTom CyberCobra 2'}), 'perc')
        self.assertEqual(t({'engine_kind': 'snare', 'name': 'Fractal Distant'}), 'snare')
        # A layered Crash pad whose first layer happens to be a hat is still a cymbal.
        self.assertEqual(t({'name': 'Crash Spree', 'references': ['Samples/Drums/Hi-Hats/a.wav']}), 'cymbal')
        self.assertEqual(t({'kind': 'one_shot', 'name': 'Sound', 'references': ['Samples/Drums/Claps/a.wav']}), 'clap')
        self.assertIsNone(t({'kind': 'one_shot', 'name': 'Sound 7', 'references': []}))
        self.assertIsNone(t({'name': 'Massive Valid 1'}))
        self.assertIsNone(t({'name': 'Bass Indieca'}))

    def test_name_tokens_split_camel_case_without_substring_false_positives(self):
        k = af.kind_from_name
        self.assertEqual(k('ClosedHH Spree'), 'hat')
        self.assertEqual(k('HH Open 2'), 'open_hat')
        self.assertEqual(k('Open HiHat'), 'open_hat')
        self.assertIsNone(k('Whatever'))   # contains 'hat' but is not a hat
        self.assertIsNone(k('Chatter Synth'))


class AutofillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kit = drumsynth_fixture(self.root / 'kit')
        pack = Path(self.kit['root'])
        # Dark/long kicks to bright/short kicks; a snare and a loop that must never be offered.
        for n, (freq, decay) in enumerate([(60, 8), (80, 20), (120, 45), (160, 90)]):
            tone(pack / f'Samples/Drums/Kicks/Kick {n}.wav', freq, decay)
        tone(pack / 'Samples/Drums/Snares/Snare A.wav', 800, 25)
        tone(pack / 'Samples/Loops/Kick loop.wav', 70, 5)
        self.group = inspect_group(self.kit, use_previews=False)
        self.sounds = af.SoundIndex(self.root / 'features.sqlite')

    def tearDown(self):
        self.temp.cleanup()

    def test_drumsynth_engine_is_remembered_and_fillable(self):
        pad = self.group['pads'][0]
        self.assertEqual((pad['status'], pad['engine_kind']), ('empty', 'kick'))
        self.assertEqual(af.fillable_pads(self.group), [0])
        self.assertEqual(self.group['unresolved'], [])  # why the old count missed them

    def test_user_cleared_pads_are_not_refilled(self):
        ed.clear(self.group, 5)
        self.assertNotIn(5, af.fillable_pads(self.group))

    def test_fills_with_same_type_only_and_marks_provenance(self):
        plan, skipped = af.auto_fill(self.group, self.sounds, seed=1)
        self.assertEqual(skipped, [])
        self.assertEqual(len(plan), 1)
        pad = self.group['pads'][0]
        self.assertTrue(usable(pad))
        self.assertIn('/Drums/Kicks/', pad['source'].replace('\\', '/'))
        self.assertEqual(pad['replacement']['method'], 'auto_fill')
        self.assertEqual(pad['kind'], 'kick')
        self.assertEqual(af.fillable_pads(self.group), [])

    def test_deterministic_for_seed_and_cache_is_reused(self):
        first, _ = af.plan_autofill(self.group, self.sounds.rows(self.kit['root']), seed=3)
        second, _ = af.plan_autofill(self.group, self.sounds.rows(self.kit['root']), seed=3)
        self.assertEqual(first, second)

    def test_unused_pool_exhaustion_and_missing_type_are_reported(self):
        for path in (Path(self.kit['root']) / 'Samples/Drums/Kicks').glob('*.wav'):
            path.unlink()
        plan, skipped = af.auto_fill(self.group, self.sounds)
        self.assertEqual(plan, [])
        self.assertIn('No other kick', skipped[0]['reason'])

    def test_unknown_type_pad_is_left_for_the_user(self):
        group = ed.empty_kit()
        group['kit']['root'] = self.kit['root']
        group['pads'][3].update(status='needs_render', name='Sound', kind='one_shot')
        plan, skipped = af.auto_fill(group, self.sounds)
        self.assertEqual((plan, skipped[0]['reason']), ([], 'Sound type unknown'))

    def test_cancel_stops_analysis(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            af.auto_fill(self.group, self.sounds, cancel=cancel)

    def test_next_sound_candidates_are_type_filtered(self):
        pool = ed.ExpansionPool(self.root / 'pool.sqlite')
        candidates = pool.candidates([self.kit], self.group, 0, 0)
        self.assertTrue(candidates)
        for path in candidates:
            self.assertEqual(af.classify_sample(path), 'kick')

    def test_candidates_fall_back_when_no_sound_of_that_type_exists(self):
        for path in (Path(self.kit['root']) / 'Samples/Drums/Kicks').glob('*.wav'):
            path.unlink()
        pool = ed.ExpansionPool(self.root / 'pool.sqlite')
        self.assertTrue(pool.candidates([self.kit], self.group, 0, 0))


class RankingTests(unittest.TestCase):
    """Ranking is relative: bright resolved pads should pull in a bright kick."""

    @staticmethod
    def rows(kind, count):
        return [{'path': f'/x/Samples/Drums/{kind}/{kind}{n}.wav', 'kind': kind, 'size': 1, 'mtime_ns': 1,
                 'features': {'centroid': 100. + 100 * n, 'decay': .2, 'flatness': .1, 'clipped': 0., 'leading': 0.}}
                for n in range(count)]

    def plan_with_anchor(self, anchor_centroid, temp):
        kit = drumsynth_fixture(temp)
        group = inspect_group(kit, use_previews=False)
        rows = self.rows('kick', 10) + self.rows('snare', 10)
        features = {'centroid': anchor_centroid, 'decay': .2, 'flatness': .1, 'clipped': 0., 'leading': 0.}
        with unittest.mock.patch.object(af, 'analyse', return_value=features):
            plan, _ = af.plan_autofill(group, rows, seed=0, surprise=0)
        return int(Path(plan[0]['path']).stem.removeprefix('kick'))

    def test_bright_anchors_pull_bright_kicks_and_dark_anchors_dark_ones(self):
        with tempfile.TemporaryDirectory() as temp:
            bright = self.plan_with_anchor(1000., Path(temp) / 'a')
            dark = self.plan_with_anchor(100., Path(temp) / 'b')
        self.assertGreaterEqual(bright, 7)
        self.assertLessEqual(dark, 2)


class ButtonTests(unittest.TestCase):
    """The button runs on a worker thread; it must report through the UI queue, not Tk."""

    def setUp(self):
        import os
        from unittest.mock import patch
        from ni_to_sp.converter_ui import App
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kit = drumsynth_fixture(self.root / 'kit')
        for n, (freq, decay) in enumerate([(60, 8), (120, 45)]):
            tone(Path(self.kit['root']) / f'Samples/Drums/Kicks/Kick {n}.wav', freq, decay)
        self.env = patch.dict(os.environ, {'NI_TO_SP_DATA_DIR': str(self.root / 'data'), 'NI_TO_SP_DISABLE_AUDIO': '1', 'NI_TO_SP_KITMAKER_AUTOSTART': '0'})
        self.env.start()
        with patch.object(App, 'scan', lambda _: None):
            self.app = App()
        self.app.tabs.set('Play & export')   # these tests drive the editor, not the Make kits tab
        self.app.geometry('1120x800+10000+10000')
        self.app.update()

    def tearDown(self):
        self.app.close()
        self.env.stop()
        self.temp.cleanup()

    def test_button_fills_a_drumsynth_pad_and_undo_restores_it(self):
        from test_browser import wait
        self.app.kits = [self.kit]
        self.app._install_group(inspect_group(self.kit, use_previews=False))
        self.app.auto_fill_button.invoke()
        wait(self.app)
        self.assertTrue(usable(self.app.group['pads'][0]))
        self.assertIn('1 pad filled', self.app.status.cget('text'))
        self.app.undo_edit()
        self.assertFalse(usable(self.app.group['pads'][0]))
        self.app.auto_fill_button.invoke()
        wait(self.app)
        self.app.auto_fill_button.invoke()
        wait(self.app)
        self.assertIn('No unresolved pads', self.app.status.cget('text'))


def rolekit_fixture(root, name='Role Kit', extras=8):
    """16 pads that cycle kick/snare/closed hat/open hat, each with a sample, plus spare sounds."""
    root = Path(root)
    pack = root / 'installed/Test Library'
    source = pack / 'Groups/Kits' / (name + '.mxgrp')
    source.parent.mkdir(parents=True, exist_ok=True)
    folders = {'Kick': 'Kicks', 'Snare': 'Snares', 'ClosedHH': 'Hi-Hats', 'OpenHH': 'Hi-Hats'}
    roles = list(folders)
    data = bytearray(b'Generated test archive\0')
    for index in range(16):
        role = roles[index % 4]
        title = f'{role} Test {index}'
        data += record(title, 1000 + index * 30, False, index == 0)
        relative = f'Samples/Drums/{folders[role]}/{title}.wav'
        tone(pack / relative, 80 + index * 40, 20 + index)
        data += relative.encode() + b'\0'
    data += record(name, 2000, False)
    source.write_bytes(data)
    for role in roles:
        for n in range(extras):
            tone(pack / f'Samples/Drums/{folders[role]}/{role} Spare {n}.wav', 90 + n * 70, 15 + n * 9)
    return {'id': name, 'name': name, 'pack': 'Test', 'root': str(pack), 'path': str(source), 'preview': None}


class LockSwapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kit = rolekit_fixture(self.root / 'kit')
        self.group = inspect_group(self.kit, use_previews=False)

    def tearDown(self):
        self.temp.cleanup()

    def test_swappable_pads_skip_locked_and_unknown_type(self):
        self.group['pads'][2]['locked'] = True
        self.group['pads'][3].update(name='Sound 4', kind='one_shot', references=[], status='needs_render', source=None)
        swappable = af.swappable_pads(self.group)
        self.assertNotIn(2, swappable)
        self.assertNotIn(3, swappable)
        self.assertEqual(len(swappable), 14)

    def test_plan_changes_every_target_to_its_own_type_and_never_reuses_a_sound(self):
        rows = af.SoundIndex(self.root / 'f.sqlite').rows(self.kit['root'])
        slots = af.swappable_pads(self.group)
        plan, skipped = af.plan_autofill(self.group, rows, seed=1, pads=slots, method='swap')
        self.assertEqual((len(plan), skipped), (16, []))
        current = {p['source'] for p in self.group['pads']}
        self.assertEqual(len({item['path'] for item in plan}), 16)
        for item in plan:
            self.assertNotIn(item['path'], current)
            self.assertEqual(af.classify_sample(item['path']), item['kind'])
            self.assertEqual(item['kind'], af.pad_target_kind(self.group['pads'][item['index']]))

    def test_avoid_is_soft_so_a_small_pool_still_works(self):
        rows = [r for r in af.SoundIndex(self.root / 'f.sqlite').rows(self.kit['root']) if r['kind'] == 'kick']
        for number in range(1, 16):          # leave exactly one spare kick available
            if number % 4:
                self.group['pads'][number].update(locked=True)
        only = [r for r in rows if 'Spare 0' in r['path']]
        plan, _ = af.plan_autofill(self.group, only, seed=1, pads=[0], avoid=[only[0]['path']], method='swap')
        self.assertEqual(len(plan), 1)

    def test_manual_replacement_keeps_a_pad_locked(self):
        self.group['pads'][0]['locked'] = True
        spare = Path(self.kit['root']) / 'Samples/Drums/Kicks/Kick Spare 0.wav'
        ed.assign(self.group, 0, spare)
        self.assertTrue(self.group['pads'][0].get('locked'))


class MenuAndKeyTests(unittest.TestCase):
    def setUp(self):
        import os
        from unittest.mock import patch
        from ni_to_sp.converter_ui import App
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kit = rolekit_fixture(self.root / 'kit')
        self.env = patch.dict(os.environ, {'NI_TO_SP_DATA_DIR': str(self.root / 'data'), 'NI_TO_SP_DISABLE_AUDIO': '1', 'NI_TO_SP_KITMAKER_AUTOSTART': '0'})
        self.env.start()
        with patch.object(App, 'scan', lambda _: None):
            self.app = App()
        self.app.tabs.set('Play & export')   # these tests drive the editor, not the Make kits tab
        self.app.geometry('1180x820+10000+10000')
        self.app.update()
        self.app.kits = [self.kit]
        self.app._install_group(inspect_group(self.kit, use_previews=False))
        self.app.update()

    def tearDown(self):
        self.app.busy = False
        self.app.close()
        self.env.stop()
        self.temp.cleanup()

    def sources(self):
        return [p['source'] for p in self.app.group['pads']]

    def right_click(self, widget):
        import tkinter as tk
        from unittest.mock import patch
        shown = []
        with patch.object(tk.Menu, 'tk_popup', lambda menu, x, y: shown.append(menu)):
            widget.event_generate('<Button-3>', x=10, y=10)
            self.app.update()
        return shown

    def test_right_click_works_on_the_caption_label_and_while_busy(self):
        pad = self.app.pad_buttons[5]
        self.assertIsNotNone(pad._text_label)                    # created after the button was bound
        self.assertEqual(len(self.right_click(pad._canvas)), 1)
        self.assertEqual(len(self.right_click(pad._text_label)), 1)   # was silently ignored
        self.app.busy = True
        (menu,) = self.right_click(pad._text_label)
        self.assertEqual(menu.entrycget(menu.index('Clear pad'), 'state'), 'disabled')
        self.assertEqual(self.app.selected_pad, 5)
        self.app.busy = False

    def test_menu_offers_lock_then_unlock(self):
        (menu,) = self.right_click(self.app.pad_buttons[2]._text_label)
        self.assertIn('Lock pad', [menu.entrycget(i, 'label') for i in range(menu.index('end') + 1) if menu.type(i) == 'command'])
        self.app.toggle_lock(2)
        (menu,) = self.right_click(self.app.pad_buttons[2]._text_label)
        self.assertIn('Unlock pad', [menu.entrycget(i, 'label') for i in range(menu.index('end') + 1) if menu.type(i) == 'command'])

    def test_swap_key_keeps_locked_pads_and_changes_the_rest_then_undo(self):
        from test_browser import wait
        before = self.sources()
        self.app.toggle_lock(0)
        self.app.toggle_lock(5)
        self.app.pad_buttons[3]._canvas.event_generate('<KeyPress-1>')
        wait(self.app)
        after = self.sources()
        self.assertEqual((after[0], after[5]), (before[0], before[5]))
        for i in range(16):
            if i not in (0, 5):
                self.assertNotEqual(after[i], before[i], f'pad {i} should have been swapped')
                self.assertEqual(af.classify_sample(after[i]), af.pad_target_kind(self.app.group['pads'][i]))
        self.assertTrue(self.app.group['pads'][0]['locked'] and self.app.group['pads'][5]['locked'])
        self.assertIn('Swapped 14 pads', self.app.status.cget('text'))
        self.assertIn('2 locked kept', self.app.status.cget('text'))
        self.app.swap_unlocked()          # another take: different sounds again
        wait(self.app)
        again = self.sources()
        self.assertEqual((again[0], again[5]), (before[0], before[5]))
        self.assertGreater(sum(a != b for a, b in zip(again, after)), 10)
        self.app.undo_edit()
        self.assertEqual(self.sources(), after)

    def test_keys_are_ignored_while_typing_in_a_text_field(self):
        import tkinter as tk
        from test_browser import wait
        entry = tk.Entry(self.app)
        entry.place(x=0, y=0, width=100)
        self.app.update()
        entry.focus_force()
        self.app.update()
        before = self.sources()
        entry.event_generate('<KeyPress-1>')        # would start a background swap
        self.app.update()
        wait(self.app)
        self.assertEqual(self.sources(), before)
        entry.event_generate('<KeyPress-l>')        # would lock the selected pad
        self.app.update()
        self.assertFalse(any(p.get('locked') for p in self.app.group['pads']))
        entry.destroy()

    def test_l_key_toggles_the_selected_pad(self):
        self.app.focus_pad(4)
        self.app.pad_buttons[4]._canvas.event_generate('<KeyPress-l>')
        self.app.update()
        self.assertTrue(self.app.group['pads'][4].get('locked'))
        self.assertIn('Unlock pad', self.app.lock_button.cget('text'))
        self.assertIn('locked', self.app.pad_buttons[4].cget('text').casefold())

    def test_swap_explains_why_nothing_can_be_swapped(self):
        for pad in self.app.group['pads']:
            pad.update(name='Chord C Mothership', kind='one_shot', references=[], status='sample',
                       source=str(self.root / 'elsewhere/Odd Folder/x.wav'))
        self.app.swap_unlocked()
        self.assertIn('no pad here is a drum sound', self.app.status.cget('text'))
        for pad in self.app.group['pads']:
            pad['locked'] = True
        self.app.swap_unlocked()
        self.assertIn('every pad is locked', self.app.status.cget('text'))

    def test_lock_survives_save_and_open(self):
        self.app.toggle_lock(7)
        path = self.root / 'kit.json'
        ed.save_kit(self.app.group, path)
        self.assertTrue(ed.open_kit(path)['pads'][7].get('locked'))

    def test_on_the_mpc_only_the_viewed_bank_is_swapped(self):
        import copy
        from test_browser import wait
        from ni_to_sp.mpc_banks import ensure_banks
        program = ensure_banks(self.app.group)
        program['banks'][1] = copy.deepcopy(program['banks'][0])     # bank B holds the same kit
        self.app.target_var.set('MPC1000')
        self.app._install_group(program)
        other = [p['source'] for p in self.app.group['banks'][1]['pads']]
        before = self.sources()
        self.app.swap_unlocked()
        wait(self.app)
        self.assertNotEqual(self.sources(), before)
        self.assertEqual([p['source'] for p in self.app.group['banks'][1]['pads']], other)
        self.assertIn('bank A', self.app.status.cget('text'))


class LargeLibraryTests(unittest.TestCase):
    """A real expansion can hold thousands of sounds; swap must not analyse or scan them all."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kit = rolekit_fixture(self.root / 'kit', extras=60)
        self.group = inspect_group(self.kit, use_previews=False)

    def tearDown(self):
        self.temp.cleanup()

    def test_rows_can_be_limited_to_needed_kinds_and_capped_stably(self):
        sounds = af.SoundIndex(self.root / 'f.sqlite')
        everything = sounds.rows(self.kit['root'])
        capped = sounds.rows(self.kit['root'], kinds={'kick', 'snare'}, cap=10)
        self.assertGreater(len(everything), 100)
        self.assertEqual({r['kind'] for r in capped}, {'kick', 'snare'})
        self.assertEqual(sum(r['kind'] == 'kick' for r in capped), 10)
        again = sounds.rows(self.kit['root'], kinds={'kick', 'snare'}, cap=10)
        self.assertEqual(sorted(r['path'] for r in capped), sorted(r['path'] for r in again))

    def test_auto_fill_analyses_only_the_needed_kinds_up_to_the_cap(self):
        analysed = []
        real = af.analyse
        def counting(path, *args):
            analysed.append(path)
            return real(path, *args)
        for pad in self.group['pads']:
            if not pad['name'].startswith(('Kick', 'Snare')):
                pad['locked'] = True          # swap only kicks and snares
        with unittest.mock.patch.object(af, 'analyse', counting), \
                unittest.mock.patch.object(af, 'CANDIDATES_PER_KIND', 12):
            plan, _ = af.auto_fill(self.group, af.SoundIndex(self.root / 'f.sqlite'),
                                   pads=af.swappable_pads(self.group), method='swap')
        self.assertEqual(len(plan), 8)
        spares = [p for p in map(str, analysed) if 'Spare' in p]
        self.assertLessEqual(len(spares), 4 * 12)            # at most the cap per sound type...
        self.assertLess(len(spares), 4 * 60)                  # ...not the 240 spare sounds in the library

    def test_planning_makes_no_file_system_calls_per_candidate(self):
        import time
        rows = [{'path': f'C:/nowhere/Samples/Drums/{k}/{k} {n}.wav', 'kind': k, 'size': 1, 'mtime_ns': 1,
                 'features': {'centroid': 100. + n, 'decay': .1 + n / 1e5, 'flatness': .1, 'clipped': 0., 'leading': 0.}}
                for k in ('kick', 'snare', 'hat', 'open_hat') for n in range(2500)]
        started = time.monotonic()
        with unittest.mock.patch.object(Path, 'resolve', side_effect=AssertionError('file system touched')):
            plan, _ = af.plan_autofill(self.group, rows, seed=2, pads=af.swappable_pads(self.group), method='swap')
        self.assertEqual(len(plan), 16)
        # The patch above fails on any file-system call; this only catches a pathological slowdown,
        # and has headroom for a busy machine (it takes about 1.5 s alone).
        self.assertLess(time.monotonic() - started, 12)


if __name__ == '__main__':
    unittest.main()
