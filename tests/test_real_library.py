"""Swap-unlocked against a real Native Instruments library.

Skipped unless NI_REAL_LIBRARY=1 (it needs an installed library and takes minutes):

    set NI_REAL_LIBRARY=1
    python -m unittest discover -s tests -p test_real_library.py -v

NI_LIBRARY_ROOT overrides C:/Users/Public/Documents, NI_REAL_KITS the number of kits tried (default 40).
Nothing is written to the library; the feature cache goes in a temporary folder.
"""
from collections import Counter
import json
import os
from pathlib import Path, PurePosixPath
import random
import tempfile
import time
import unittest

from ni_to_sp import autofill as af
from ni_to_sp.direct import inspect_group
from ni_to_sp.library import discover_kits

ROOT = os.environ.get('NI_LIBRARY_ROOT', 'C:/Users/Public/Documents')


@unittest.skipUnless(os.environ.get('NI_REAL_LIBRARY') == '1' and Path(ROOT).is_dir(),
                     'set NI_REAL_LIBRARY=1 to run against an installed NI library')
class RealLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.sounds = af.SoundIndex(Path(cls.temp.name) / 'features.sqlite')
        kits = discover_kits(ROOT)
        random.Random(2026).shuffle(kits)
        cls.stats, cls.problems, cls.slowest, cls.clashes = Counter(), [], 0., []
        wanted = int(os.environ.get('NI_REAL_KITS', '40'))
        for kit in kits:
            if cls.stats['kits'] >= wanted:
                break
            try:
                group = inspect_group(kit)
            except Exception:
                continue           # the app's own 'structure not supported' limit
            if not af.swappable_pads(group):
                cls.stats['kits with nothing swappable'] += 1
                continue
            cls._try(kit, group)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def _try(cls, kit, group):
        rng = random.Random(kit['id'])
        locked = set(rng.sample(range(16), rng.randint(0, 6)))
        for i in locked:
            group['pads'][i]['locked'] = True
        before = [p.get('source') for p in group['pads']]
        categories = af.preview_categories(kit['root'])
        kinds = [af.pad_target_kind(p, categories) for p in group['pads']]
        slots = af.swappable_pads(group)
        started = time.monotonic()
        plan, skipped = af.auto_fill(group, cls.sounds, seed=1, pads=slots,
                                     anchor_pads=sorted(locked) or None, method='swap')
        cls.slowest = max(cls.slowest, time.monotonic() - started)
        after = [p.get('source') for p in group['pads']]
        label = f"{kit['pack']} / {kit['name']}"
        cls.stats['kits'] += 1
        cls.stats['unlocked swappable pads'] += len(slots)
        cls.stats['swapped'] += sum(after[i] != before[i] for i in slots)
        cls.stats['skipped'] += len(skipped)
        for reason in skipped:
            cls.stats['skip: ' + reason['reason'][:40]] += 1
        for i in range(16):
            if i in locked and after[i] != before[i]:
                cls.problems.append(f'{label}: locked pad {i + 1} changed')
            if i not in slots and after[i] != before[i]:
                cls.problems.append(f'{label}: pad {i + 1} was not swappable but changed')
        for i in slots:
            if after[i] != before[i] and after[i] and after.count(after[i]) > 1:
                cls.problems.append(f'{label}: swapped-in sound on pad {i + 1} is also on another pad')
        for i in slots:
            if after[i] == before[i] or not after[i]:
                continue
            kind = kinds[i]
            narrow = af.family_of_path(after[i])
            cls.stats['same family'] += (narrow == kind)
            cls.stats['same folder'] += (af.coarse_kind(narrow or '') == af.coarse_kind(kind))
            if kind not in af.DRUM_KINDS:
                old_bpm, old_key = af.musical_labels(PurePosixPath(str(before[i] or '').replace(chr(92), '/')).stem)
                new_bpm, new_key = af.musical_labels(PurePosixPath(after[i].replace(chr(92), '/')).stem)
                if old_key:
                    cls.stats['key to keep'] += 1
                    cls.stats['key clash'] += bool(new_key and af.key_tier(old_key, new_key) == 4)
                    cls.stats['key exact'] += bool(new_key and af.key_tier(old_key, new_key) == 0)
                    if new_key and af.key_tier(old_key, new_key) == 4:
                        cls.clashes.append(f"{kind}: {Path(str(before[i])).stem} -> {Path(after[i]).stem}")
                if old_bpm:
                    cls.stats['tempo to keep'] += 1
                    cls.stats['tempo clash'] += bool(new_bpm and new_bpm != old_bpm)

    def test_report(self):
        print('\nreal library:', dict(self.stats), f'slowest swap {self.slowest:.1f}s')
        print('key clashes:')
        for line in self.clashes:
            print('   ', line)

    def test_locked_pads_and_unswappable_pads_never_change_and_no_sound_is_used_twice(self):
        self.assertEqual(self.problems, [])

    def test_nearly_every_unlocked_pad_with_a_known_type_swaps(self):
        total = self.stats['unlocked swappable pads']
        self.assertGreater(total, 100)
        self.assertGreaterEqual(self.stats['swapped'] / total, .97, dict(self.stats))

    def test_swaps_stay_in_their_own_instrument_family(self):
        swapped = self.stats['swapped']
        self.assertGreaterEqual(self.stats['same family'] / swapped, .88, dict(self.stats))
        self.assertGreaterEqual(self.stats['same folder'] / swapped, .97, dict(self.stats))

    def test_keys_and_tempos_rarely_clash(self):
        if self.stats['key to keep']:
            self.assertLessEqual(self.stats['key clash'] / self.stats['key to keep'], .2, dict(self.stats))
        if self.stats['tempo to keep']:
            self.assertLessEqual(self.stats['tempo clash'] / self.stats['tempo to keep'], .15, dict(self.stats))

    def test_a_swap_is_never_painfully_slow(self):
        self.assertLess(self.slowest, 60, 'even a cold first swap in a huge expansion should finish within a minute')


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(os.environ.get('NI_REAL_LIBRARY') == '1' and Path(ROOT).is_dir(),
                     'set NI_REAL_LIBRARY=1 to run against an installed NI library')
class RealKitMakerTests(unittest.TestCase):
    """The kit pool, gap filling, library and MPC1000 packing against a real NI library."""

    @classmethod
    def setUpClass(cls):
        from ni_to_sp import kitlib, kitmaker, kitpack
        cls.km, cls.kitlib, cls.kitpack = kitmaker, kitlib, kitpack
        cls.temp = tempfile.TemporaryDirectory()
        base = Path(cls.temp.name)
        cls.sounds = af.SoundIndex(base / 'features.sqlite')
        cls.kits = discover_kits(ROOT)
        started = time.monotonic()
        cls.pool = kitmaker.build_pool(cls.kits, base / 'pool.sqlite')
        cls.pool_seconds = time.monotonic() - started
        cls.order = kitmaker.order_pool(cls.pool, seed=7)
        cls.library = kitlib.KitLibrary(base / 'lib')
        cls.base = base

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_report(self):
        complete = sum(c['filled'] == 8 for c in self.order)
        print(f"\nkit maker: {len(self.kits)} kits -> {len(self.pool)} candidates -> {len(self.order)} unique; "
              f"{complete} complete; {len({c['pack'] for c in self.order})} expansions; pool built in {self.pool_seconds:.0f}s")

    def test_a_large_balanced_pool_of_kits_is_found(self):
        self.assertGreaterEqual(len(self.order), 1000)
        self.assertGreaterEqual(sum(c['filled'] == 8 for c in self.order), 250)
        self.assertGreaterEqual(len({c['pack'] for c in self.order}), 25)
        self.assertGreaterEqual(len({c['pack'] for c in self.order[:30]}), 20)       # no expansion floods the start

    def test_the_pool_includes_drums_on_the_top_pads_and_the_kits_folder_libraries(self):
        self.assertGreater(sum(c['half'] == 'top' for c in self.order), 50)
        if any(k['pack'] in ('Deep Matter', 'Molten Veil') for k in self.kits):
            self.assertTrue(any(c['pack'] in ('Deep Matter', 'Molten Veil') for c in self.order))

    def test_every_candidate_has_the_core_roles_and_real_files(self):
        for cand in self.order[:300]:
            slots = cand['slots']
            self.assertTrue(slots[0]['source'] and slots[1]['source'] and (slots[2]['source'] or slots[3]['source']))
            self.assertEqual(cand['filled'], sum(bool(s['source']) for s in slots))
            self.assertTrue(all(Path(s['source']).is_file() for s in slots if s['source']), cand['kit'])
            self.assertEqual(len({s['source'] for s in slots if s['source']}), cand['filled'], 'a sound twice in one kit')

    def test_empty_roles_are_filled_almost_always_and_quickly(self):
        sample = random.Random(3).sample(self.order, 30)
        slow, unfilled = 0., 0
        for cand in sample:
            cand = json.loads(json.dumps(cand))
            started = time.monotonic()
            left = self.km.fill_empty(cand, self.sounds, seed=1)
            slow = max(slow, time.monotonic() - started)
            unfilled += bool(left)
            self.assertEqual(len({s['source'] for s in cand['slots'] if s['source']}), cand['filled'])
        self.assertLessEqual(unfilled, 2, f'{unfilled} of 30 kits could not be completed')
        self.assertLess(slow, 60, 'a cold first fill in a huge expansion should still finish within a minute')

    def test_keeping_kits_and_packing_them_makes_valid_mpc1000_programs(self):
        picks = [json.loads(json.dumps(c)) for c in random.Random(5).sample(self.order, 10)]
        for cand in picks:
            self.km.fill_empty(cand, self.sounds, seed=1)
            self.library.add_kit(cand)
        programs = self.kitpack.build_all(self.library, self.base / 'out')
        self.assertEqual([len(p['kits']) for p in programs], [8, 2])
        for program in programs:
            self.assertGreater(program['validation']['assigned_pads'], 0)
            self.assertLess(program['validation']['sample_memory_mb'], 30)
        self.assertTrue((self.base / 'out' / 'CATALOGUE.csv').is_file())
