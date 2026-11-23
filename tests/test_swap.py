"""Swap-unlocked behaviour across drum, bass, pad, loop, SFX, chord and preview pads."""
from pathlib import Path
import random
import tempfile
import unittest
import unittest.mock

import numpy as np
import soundfile as sf

from direct_fixtures import record
from ni_to_sp import autofill as af
from ni_to_sp import kit_editor as ed
from ni_to_sp.direct import inspect_group, usable

SYNTH = 'One Shots/Synth Note'
# (pad title = file name, folder under Samples). Pads 13-16 are unresolved 'Sound N' pads.
PADS = [
    ('Kick Mine', 'Drums/Kicks'), ('Snare Mine', 'Drums/Snares'), ('ClosedHH Mine', 'Drums/Hi-Hats'),
    ('Bass A Mine', SYNTH), ('Bass F Mine', SYNTH), ('Pad Am Mine', SYNTH), ('Pad Cm Mine', SYNTH),
    ('SFX Mine 1', 'One Shots/SFX'),
    ('Vocal[120] Am Mine', 'Loops/Vocals'), ('Vocal[120] Cm Mine', 'Loops/Vocals'),
    ('Chord Am Mine', 'One Shots/Chord'), ('Lead Mine', SYNTH),
]
SPARES = {
    'Drums/Kicks': [f'Kick Spare {n}' for n in range(8)],
    'Drums/Snares': [f'Snare Spare {n}' for n in range(8)],
    'Drums/Hi-Hats': [f'ClosedHH Spare {n}' for n in range(8)],
    SYNTH: ['Bass A Spare 1', 'Bass C Spare 2', 'Bass F Spare 3', 'Bass G Spare 4', 'Bass Spare 5',
            'Pad Am Spare 1', 'Pad Dm Spare 2', 'Pad Cm Spare 3', 'Pad Spare 4',
            'Lead Spare 1', 'Lead Spare 2', 'Lead Spare 3'],
    'One Shots/SFX': [f'SFX Spare {n}' for n in range(1, 5)],
    'Loops/Vocals': ['Vocal[120] Am Spare 1', 'Vocal[120] Cm Spare 2', 'Vocal[100] Am Spare 3',
                     'Vocal[120] Gm Spare 4', 'Vocal Spare 5'],
    'One Shots/Chord': ['Chord Am Spare 1', 'Chord Cm Spare 2', 'Chord Dm Spare 3', 'Chord Spare 4'],
}


def tone(path, freq, decay, seconds=.4, rate=48000):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(rate * seconds)) / rate
    sf.write(path, .5 * np.sin(2 * np.pi * freq * t) * np.exp(-t * decay), rate, subtype='PCM_24')


def mixed_kit(root, name='Mixed Kit', spares=None, pack='Test Library'):
    """A kit whose pads span drums, keyed bass/pad one-shots, tempo-tagged loops, SFX and chords."""
    root = Path(root)
    pack_dir = root / 'installed' / pack
    source = pack_dir / 'Groups/Kits' / (name + '.mxgrp')
    source.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(b'Generated test archive\0')
    for index in range(16):
        if index < len(PADS):
            title, folder = PADS[index]
            data += record(title, 1000 + index * 30, False, index == 0)
            relative = f'Samples/{folder}/{title}.wav'
            tone(pack_dir / relative, 80 + index * 37, 12 + index * 3)
            data += relative.encode() + b'\0'
        else:
            data += record(f'Sound {index + 1}', 1000 + index * 30, False, False)
    data += record(name, 2000, False)
    source.write_bytes(data)
    for folder, stems in (SPARES if spares is None else spares).items():
        for n, stem in enumerate(stems):
            tone(pack_dir / f'Samples/{folder}/{stem}.wav', 90 + n * 53, 10 + n * 4)
    return {'id': name, 'name': name, 'pack': pack, 'root': str(pack_dir), 'path': str(source), 'preview': None}


def stems(group):
    return [Path(p['source']).stem if p.get('source') else None for p in group['pads']]


class FamilyTests(unittest.TestCase):
    def test_family_of_path(self):
        f = af.family_of_path
        self.assertEqual(f('X Library/Samples/One Shots/Synth Note/Bass A Foo.wav'), 'oneshots:synthnote/bass')
        self.assertEqual(f('X Library/Samples/One Shots/Synth Note/Pad Am Foo.wav'), 'oneshots:synthnote/pad')
        self.assertEqual(f('X Library/Samples/Loops/Vocals/Vocal[120] Am Foo.wav'), 'loops:vocals/vocal')
        self.assertEqual(f('X Library/Samples/Loops/Guitar[140] C GuitarSlim 1.wav'), 'loops:guitar/guitar')   # no sub-folder
        self.assertEqual(f('X Library/Samples/Instruments/Bass/Flatpot Samples/Flatpot B1.wav'), 'instruments:bass')
        self.assertEqual(f('X Library/Samples/Drums/Snare/DrumFill A.wav'), 'drums:fills')
        self.assertEqual(f('X Library/Samples/Drums/Kicks/Kick A.wav'), 'kick')
        self.assertEqual(f('X Library/Sounds/Massive Sounds/.previews/Foo.nmsv.ogg'), 'previews:massivesounds')
        self.assertEqual(f('X Library/Sounds/Bass/.previews/Bar.mxsnd.ogg'), 'previews:bass')
        self.assertIsNone(f('X Library/Samples/Elsewhere/Foo.wav'))
        self.assertIsNone(f('Somewhere/else/Foo.wav'))

    def test_musical_labels(self):
        m = af.musical_labels
        self.assertEqual(m('Bass[138] Cm PortMaria'), (138.0, 'Cm'))
        self.assertEqual(m('Lick Organ[76.5] Dmin TheGrudge'), (76.5, 'Dmin'))
        self.assertEqual(m('Chord Troni EbMajDown'), (None, 'Ebmaj'))
        self.assertEqual(m('Chord Combo A# Low Lights'), (None, 'A#'))
        self.assertEqual(m('Pad Am Cook'), (None, 'Am'))
        self.assertEqual(m('Sweep TwoBites'), (None, None))
        self.assertEqual(m('Bass Spare 5'), (None, None))
        self.assertEqual(m('Bass A Follower'), (None, 'A'))
        self.assertEqual(m('Pad C# Genesis'), (None, 'C#'))
        self.assertEqual(m('Vocal A cappella'), (None, None))      # an article, not a key

    def test_key_tiers(self):
        t = af.key_tier
        self.assertEqual(t('Am', 'Am'), 0)
        self.assertEqual(t('Dm', 'D'), 1)           # same root, other mode: NI often omits the 'm'
        self.assertEqual(t('Am', 'C'), 2)           # relative major
        self.assertEqual(t('C', 'Am'), 2)
        self.assertEqual(t('C#m', 'E'), 2)
        self.assertEqual(t('Am', None), 3)
        self.assertEqual(t('Am', 'F#'), 4)
        self.assertEqual(t('Dmin', 'Dmin'), 0)
        self.assertEqual(t('Ebmaj', 'D#'), 0)       # enharmonic spelling
        self.assertEqual(t(None, 'F#'), 0)          # a pad with no key accepts anything
        self.assertEqual(af.parse_key('H'), None)

    def test_preview_category_from_a_pad_name(self):
        categories = frozenset({'bass', 'massivesounds', 'pianokeys', 'bassynthsounds'})
        self.assertEqual(af.name_family('Massive Valid 1', categories), 'previews:massivesounds')
        self.assertEqual(af.name_family('Keys Lesson', categories), 'previews:pianokeys')
        self.assertEqual(af.name_family('Bass Guitar Lesson', categories), 'previews:bass')   # exact beats partial
        self.assertIsNone(af.name_family('Sound 7', categories))
        self.assertIsNone(af.name_family('Whatever', frozenset()))


class MixedKitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kit = mixed_kit(self.root / 'a')
        self.sounds = af.SoundIndex(self.root / 'features.sqlite')

    def tearDown(self):
        self.temp.cleanup()

    def group(self):
        return inspect_group(self.kit, use_previews=False)

    def swap(self, group, seed=1, **options):
        slots = af.swappable_pads(group)
        plan, skipped = af.auto_fill(group, self.sounds, seed=seed, pads=slots,
                                     anchor_pads=[i for i, p in enumerate(group['pads']) if p.get('locked')] or None,
                                     method='swap', **options)
        return slots, plan, skipped

    def test_every_pad_gets_a_recognised_family(self):
        kinds = [af.pad_target_kind(p) for p in self.group()['pads']]
        self.assertEqual(kinds[:3], ['kick', 'snare', 'hat'])
        self.assertEqual(kinds[3:5], ['oneshots:synthnote/bass'] * 2)
        self.assertEqual(kinds[5:7], ['oneshots:synthnote/pad'] * 2)
        self.assertEqual(kinds[7], 'oneshots:sfx/sfx')
        self.assertEqual(kinds[8:10], ['loops:vocals/vocal'] * 2)
        self.assertEqual(kinds[10], 'oneshots:chord/chord')
        self.assertEqual(kinds[11], 'oneshots:synthnote/lead')
        self.assertEqual(kinds[12:], [None] * 4)          # unresolved 'Sound N' pads: nothing to go on

    def test_unlocked_pads_all_swap_to_the_same_family(self):
        group = self.group()
        before = stems(group)
        slots, plan, skipped = self.swap(group)
        self.assertEqual((len(slots), len(plan), skipped), (12, 12, []))
        after = stems(group)
        for i in slots:
            self.assertNotEqual(after[i], before[i], f'pad {i} did not change')
            self.assertEqual(af.family_of_path(group['pads'][i]['source']), af.pad_target_kind(group['pads'][i]),
                             f'pad {i} ({before[i]} -> {after[i]}) changed family')
        self.assertEqual(len({p['source'] for p in group['pads'] if p.get('source')}), 12)    # no sound twice
        self.assertTrue(all(Path(p['source']).is_file() for p in group['pads'] if p.get('source')))

    def test_locked_pads_are_never_touched_and_stay_locked(self):
        group = self.group()
        for i in (0, 3, 8, 11):
            group['pads'][i]['locked'] = True
        before = [p['source'] for p in group['pads']]
        slots, plan, _ = self.swap(group)
        self.assertEqual(sorted(slots), [1, 2, 4, 5, 6, 7, 9, 10])
        for i in (0, 3, 8, 11):
            self.assertEqual(group['pads'][i]['source'], before[i])
            self.assertTrue(group['pads'][i]['locked'])
        for i in slots:
            self.assertNotEqual(group['pads'][i]['source'], before[i])

    def test_key_and_tempo_are_kept_when_a_matching_sound_exists(self):
        group = self.group()
        self.swap(group)
        s = stems(group)
        self.assertEqual(s[3], 'Bass A Spare 1')          # Bass A -> a bass in A
        self.assertEqual(s[4], 'Bass F Spare 3')          # Bass F -> a bass in F
        self.assertEqual(s[5], 'Pad Am Spare 1')
        self.assertEqual(s[6], 'Pad Cm Spare 3')
        self.assertEqual(s[8], 'Vocal[120] Am Spare 1')   # same tempo and key
        self.assertEqual(s[9], 'Vocal[120] Cm Spare 2')
        self.assertEqual(s[10], 'Chord Am Spare 1')

    def test_a_related_key_beats_an_unknown_one_which_beats_an_unrelated_one(self):
        spares = {k: list(v) for k, v in SPARES.items()}
        spares[SYNTH] = [x for x in spares[SYNTH] if not x.startswith('Pad')] + ['Pad C Spare 7', 'Pad F# Spare 8', 'Pad Spare 9']
        kit = mixed_kit(self.root / 'c', 'Mixed C', spares=spares)
        group = inspect_group(kit, use_previews=False)
        group['pads'][6]['locked'] = True                                      # only 'Pad Am Mine' swaps
        af.auto_fill(group, af.SoundIndex(self.root / 'c.sqlite'), seed=1, pads=[5], method='swap')
        self.assertEqual(stems(group)[5], 'Pad C Spare 7')                     # Am -> C (relative), not keyless, not F#
        spares[SYNTH].remove('Pad C Spare 7')
        kit = mixed_kit(self.root / 'd', 'Mixed D', spares=spares)
        group = inspect_group(kit, use_previews=False)
        af.auto_fill(group, af.SoundIndex(self.root / 'd.sqlite'), seed=1, pads=[5], method='swap')
        self.assertEqual(stems(group)[5], 'Pad Spare 9')                       # keyless beats F#

    def test_for_a_loop_tempo_outranks_the_exact_instrument(self):
        spares = {k: list(v) for k, v in SPARES.items()}
        spares['Loops/Vocals'] = ['Vocal[100] Am Spare 3', 'Choir[120] Am Spare 9', 'Vocal Spare 5']
        kit = mixed_kit(self.root / 'e', 'Mixed E', spares=spares)
        group = inspect_group(kit, use_previews=False)
        group['pads'][9]['locked'] = True
        af.auto_fill(group, af.SoundIndex(self.root / 'e.sqlite'), seed=1, pads=[8], method='swap')
        self.assertEqual(stems(group)[8], 'Choir[120] Am Spare 9')       # 120 bpm choir, not the 100 bpm vocal

    def test_for_a_loop_the_key_outranks_the_exact_instrument_too(self):
        spares = {k: list(v) for k, v in SPARES.items()}
        spares['Loops/Vocals'] = ['Vocal[120] Gm Spare 4', 'Choir[120] Am Spare 9']
        kit = mixed_kit(self.root / 'f', 'Mixed F', spares=spares)
        group = inspect_group(kit, use_previews=False)
        group['pads'][9]['locked'] = True
        af.auto_fill(group, af.SoundIndex(self.root / 'f.sqlite'), seed=1, pads=[8], method='swap')
        self.assertEqual(stems(group)[8], 'Choir[120] Am Spare 9')       # right key beats right instrument

    def test_a_sound_with_no_key_is_better_than_one_in_the_wrong_key(self):
        spares = {k: list(v) for k, v in SPARES.items()}
        spares[SYNTH] = [x for x in spares[SYNTH] if x != 'Bass A Spare 1']      # no bass in A anywhere
        kit = mixed_kit(self.root / 'b', 'Mixed B', spares=spares)
        group = inspect_group(kit, use_previews=False)
        af.auto_fill(group, af.SoundIndex(self.root / 'b.sqlite'), seed=1, pads=af.swappable_pads(group), method='swap')
        self.assertEqual(stems(group)[3], 'Bass Spare 5')                  # keyless, not 'Bass C/G/F'

    def test_a_pad_stays_a_pad_and_a_lead_stays_a_lead(self):
        for seed in range(6):
            group = self.group()
            self.swap(group, seed=seed)
            s = stems(group)
            self.assertTrue(s[5].startswith('Pad ') and s[6].startswith('Pad '), s)
            self.assertTrue(s[11].startswith('Lead '), s)
            self.assertTrue(s[3].startswith('Bass ') and s[4].startswith('Bass '), s)

    def test_another_press_gives_different_sounds_while_alternatives_last(self):
        group = self.group()
        seen = set()
        _, plan, _ = self.swap(group, seed=1)
        first = {item['index']: item['path'] for item in plan}
        seen.update(first.values())
        _, plan2, _ = self.swap(group, seed=2, avoid=seen)
        second = {item['index']: item['path'] for item in plan2}
        self.assertGreaterEqual(sum(first[i] != second[i] for i in second), 8)

    def test_random_lock_patterns_keep_every_invariant(self):
        for seed in range(25):
            rng = random.Random(seed)
            group = self.group()
            locked = set(rng.sample(range(16), rng.randint(0, 9)))
            for i in locked:
                group['pads'][i]['locked'] = True
            before = [p.get('source') for p in group['pads']]
            expect = [i for i in af.swappable_pads(group)]
            slots, plan, skipped = self.swap(group, seed=seed)
            after = [p.get('source') for p in group['pads']]
            self.assertEqual(slots, expect)
            self.assertEqual(skipped, [], f'seed {seed}')
            for i in range(16):
                if i in locked:
                    self.assertEqual(after[i], before[i], f'seed {seed}: locked pad {i} changed')
                    self.assertTrue(group['pads'][i]['locked'])
                elif i in slots:
                    self.assertNotEqual(after[i], before[i], f'seed {seed}: unlocked pad {i} did not swap')
                    self.assertEqual(af.family_of_path(after[i]), af.pad_target_kind(group['pads'][i]))
                else:
                    self.assertEqual(after[i], before[i])      # nothing to base a swap on: untouched
            sources = [s for s in after if s]
            self.assertEqual(len(sources), len(set(sources)), f'seed {seed}: a sound is on two pads')

    def test_same_seed_same_result(self):
        a, b = self.group(), self.group()
        self.swap(a, seed=7)
        self.swap(b, seed=7)
        self.assertEqual(stems(a), stems(b))

    def test_swapped_pads_remember_their_role_and_stay_swappable(self):
        group = self.group()
        self.swap(group, seed=1)
        pads = group['pads']
        self.assertEqual(pads[3]['target_kind'], 'oneshots:synthnote/bass')
        self.assertEqual(pads[3]['kind'], 'one_shot')            # public kind stays drum-only
        self.assertEqual(pads[0]['kind'], 'kick')
        self.assertEqual(af.swappable_pads(group), list(range(12)))   # and they can be swapped again
        self.swap(group, seed=2)
        self.assertTrue(af.family_of_path(pads[3]['source']).startswith('oneshots:synthnote/bass'))


class ThinFamilyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def kit_without(self, folder, keep, name):
        spares = {k: list(v) for k, v in SPARES.items()}
        spares[folder] = [x for x in spares[folder] if keep(x)]
        return mixed_kit(self.root / name, name, spares=spares, pack=name + ' Library')

    def test_a_thin_family_borrows_the_same_instrument_from_a_neighbouring_expansion(self):
        kit = self.kit_without(SYNTH, lambda x: not x.startswith('Pad'), 'Home')        # no spare pads at home
        neighbour = Path(kit['root']).parent / 'Neighbour Library'
        for n, stem in enumerate(['Pad Am Far 1', 'Pad Cm Far 2', 'Pad Em Far 3', 'Pad Far 4', 'Pad Far 5']):
            tone(neighbour / f'Samples/{SYNTH}/{stem}.wav', 100 + n * 40, 12)
        group = inspect_group(kit, use_previews=False)
        af.auto_fill(group, af.SoundIndex(self.root / 'f.sqlite'), seed=1, pads=[5, 6], method='swap')
        self.assertIn('Neighbour Library', group['pads'][5]['source'])
        self.assertEqual(Path(group['pads'][5]['source']).stem, 'Pad Am Far 1')      # still matches the key
        self.assertEqual(Path(group['pads'][6]['source']).stem, 'Pad Cm Far 2')

    def test_when_the_instrument_exists_nowhere_the_folder_family_is_the_last_resort(self):
        kit = self.kit_without(SYNTH, lambda x: not x.startswith('Lead'), 'Solo')        # no other lead anywhere
        group = inspect_group(kit, use_previews=False)
        before = group['pads'][11]['source']
        plan, skipped = af.auto_fill(group, af.SoundIndex(self.root / 'f.sqlite'), seed=1, pads=[11], method='swap')
        self.assertEqual((len(plan), skipped), (1, []))
        self.assertNotEqual(group['pads'][11]['source'], before)
        self.assertIn('Synth Note', group['pads'][11]['source'])

    def test_a_family_with_nothing_else_anywhere_is_reported_not_faked(self):
        spares = {k: list(v) for k, v in SPARES.items()}
        spares['One Shots/SFX'] = []
        kit = mixed_kit(self.root / 'x', 'Lonely', spares=spares, pack='Lonely Library')
        group = inspect_group(kit, use_previews=False)
        before = group['pads'][7]['source']
        plan, skipped = af.auto_fill(group, af.SoundIndex(self.root / 'f.sqlite'), seed=1, pads=[7], method='swap')
        self.assertEqual(plan, [])
        self.assertEqual(group['pads'][7]['source'], before)
        self.assertIn('No other', skipped[0]['reason'])


class PreviewTests(unittest.TestCase):
    """NI's synth presets only have audition clips: Sounds/<category>/.previews/<preset>.ogg."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pack = self.root / 'Previews Library'
        for category, presets in (('Massive Sounds', ['Alpha', 'Bravo', 'Charlie', 'Delta']), ('Bass', ['Echo', 'Foxtrot'])):
            for n, preset in enumerate(presets):
                path = self.pack / 'Sounds' / category / '.previews' / f'{preset}.nmsv.ogg'
                path.parent.mkdir(parents=True, exist_ok=True)
                t = np.arange(24000) / 48000
                sf.write(path, .4 * np.sin(2 * np.pi * (120 + n * 70) * t) * np.exp(-t * (3 + n)), 48000, format='OGG', subtype='VORBIS')
        self.kit = {'id': 'k', 'name': 'Synths', 'pack': 'Previews', 'root': str(self.pack), 'path': None}

    def tearDown(self):
        self.temp.cleanup()

    def group(self):
        group = ed.empty_kit('Synths')
        group['kit'] = dict(self.kit)
        return group

    def test_a_preview_pad_swaps_for_another_preview_of_its_category(self):
        group = self.group()
        ed.assign(group, 0, self.pack / 'Sounds/Massive Sounds/.previews/Alpha.nmsv.ogg')
        group['pads'][0]['name'] = 'Massive Alpha'
        before = group['pads'][0]['source']
        plan, skipped = af.auto_fill(group, af.SoundIndex(self.root / 'f.sqlite'), seed=1,
                                     pads=af.swappable_pads(group), method='swap')
        self.assertEqual(skipped, [])
        self.assertNotEqual(group['pads'][0]['source'], before)
        self.assertIn('Massive Sounds', group['pads'][0]['source'])

    def test_an_unrendered_synth_pad_is_filled_from_the_preview_category_its_name_points_at(self):
        group = self.group()
        group['pads'][4].update(name='Massive Valid 1', status='needs_render', source=None, references=[], kind='one_shot')
        self.assertEqual(af.swappable_pads(group), [4])
        plan, skipped = af.auto_fill(group, af.SoundIndex(self.root / 'f.sqlite'), seed=1, pads=[4], method='swap')
        self.assertEqual(skipped, [])
        self.assertTrue(usable(group['pads'][4]))
        self.assertIn('Massive Sounds', group['pads'][4]['source'])

    def test_a_pad_name_that_matches_no_category_stays_untouched(self):
        group = self.group()
        group['pads'][4].update(name='Mystery Synth', status='needs_render', source=None, references=[], kind='one_shot')
        self.assertEqual(af.swappable_pads(group), [])


class SwapStatusTests(unittest.TestCase):
    def setUp(self):
        import os
        from unittest.mock import patch
        from ni_to_sp.converter_ui import App
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kit = mixed_kit(self.root / 'kit')
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

    def test_swap_reports_what_was_swapped_kept_and_could_not_be(self):
        from test_browser import wait
        self.app.toggle_lock(0)
        self.app.toggle_lock(1)
        before = [p.get('source') for p in self.app.group['pads']]
        self.app.swap_unlocked()
        wait(self.app)
        text = self.app.status.cget('text')
        self.assertIn('Swapped 10 pads', text)
        self.assertIn('2 locked kept', text)
        self.assertIn('4 pads have no sound to base a swap on', text)
        after = [p.get('source') for p in self.app.group['pads']]
        self.assertEqual(after[:2], before[:2])
        self.assertTrue(all(a != b for a, b in zip(after[2:12], before[2:12])))
        self.assertEqual(after[12:], before[12:])
        self.app.undo_edit()
        self.assertEqual([p.get('source') for p in self.app.group['pads']], before)

    def test_every_unlocked_pad_changes_in_the_pad_grid(self):
        from test_browser import wait
        self.app.update()
        names = list(self.app.pad_names.values())
        self.app.swap_unlocked()
        wait(self.app)
        self.app.update()
        now = list(self.app.pad_names.values())
        self.assertEqual(sum(a != b for a, b in zip(names, now)), 12)     # captions may truncate; names do not


if __name__ == '__main__':
    unittest.main()
