"""Kit Maker: house-layout kits from NI kits, the kit library, MPC1000 programs, the beat, and the tab."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from direct_fixtures import record
from ni_to_sp import autofill as af
from ni_to_sp import groove, kitlib, kitmaker as km, kitpack
from ni_to_sp import mpc1000 as mpc
from ni_to_sp.direct import inspect_group
from ni_to_sp.library import discover_kits

HOUSE = [label for label, _, _ in km.HOUSE_LAYOUT]
FOLDER = {'Kick': 'Kicks', 'Snare': 'Snares', 'ClosedHH': 'Hi-Hats', 'OpenHH': 'Hi-Hats', 'Clap': 'Claps',
          'Rim': 'Snares', 'Tom': 'Percussion', 'Crash': 'Cymbals', 'Shaker': 'Percussion'}


def tone(path, freq=200, decay=30, seconds=.4, rate=44100, channels=1, lead=0, tail=0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(rate * seconds)) / rate
    wave = .5 * np.sin(2 * np.pi * freq * t) * np.exp(-t * decay)
    wave = np.concatenate([np.zeros(int(lead * rate)), wave, np.zeros(int(tail * rate))])
    data = np.repeat(wave[:, None], channels, axis=1) if channels > 1 else wave
    sf.write(path, data, rate, subtype='PCM_16')
    return path


def role_of(title):
    return title.split()[0]


def make_kit(root, name, pads, pack='Test Library', spares=True):
    """An NI-like kit. `pads` is 16 titles ('Kick Foo') or None for an unresolved 'Sound N' pad."""
    pack_dir = Path(root) / 'installed' / pack
    source = pack_dir / 'Groups/Kits' / (name + '.mxgrp')
    source.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(b'Generated test archive\0')
    for index, title in enumerate(pads):
        if title is None:
            data += record(f'Sound {index + 1}', 1000 + index * 30, False, index == 0)
            continue
        data += record(title, 1000 + index * 30, False, index == 0)
        relative = f'Samples/Drums/{FOLDER.get(role_of(title), "Percussion")}/{title}.wav'
        tone(pack_dir / relative, 60 + index * 45, 12 + index * 3)
        data += relative.encode() + b'\0'
    data += record(name, 2000, False)
    source.write_bytes(data)
    if spares:                                       # sounds elsewhere in the expansion, for swaps and fills
        for role, folder in FOLDER.items():
            for n in range(6):
                path = pack_dir / f'Samples/Drums/{folder}/{role} Spare {n}.wav'
                if not path.exists():
                    tone(path, 90 + n * 70, 14 + n * 5)
    return pack_dir


FULL = ['Kick Mine', 'Snare Mine', 'ClosedHH Mine', 'OpenHH Mine', 'Clap Mine', 'Rim Mine', 'Tom Mine', 'Crash Mine'] + [None] * 8


def kit_dict(root, name, pack='Test Library'):
    return next(k for k in discover_kits(str(Path(root) / 'installed')) if k['name'] == name and k['pack'] == pack.removesuffix(' Library'))


def candidates_of(root, name, pack='Test Library'):
    kit = kit_dict(root, name, pack)
    return km.extract_candidates(kit, inspect_group(kit, use_previews=False))


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()


class ExtractTests(Base):
    def test_a_full_bottom_half_fills_the_house_layout_in_order(self):
        make_kit(self.root, 'Full', FULL)
        (cand,) = candidates_of(self.root, 'Full')
        self.assertEqual((cand['half'], cand['filled'], cand['native']), ('bottom', 8, 8))
        self.assertEqual([s['label'] for s in cand['slots']], HOUSE)
        self.assertEqual([s['name'].split()[0] for s in cand['slots']],
                         ['Kick', 'Snare', 'ClosedHH', 'OpenHH', 'Clap', 'Rim', 'Tom', 'Crash'])
        self.assertTrue(all(s['origin'] == 'kit' and Path(s['source']).is_file() for s in cand['slots']))

    def test_drums_on_the_top_eight_pads_are_found_too(self):
        make_kit(self.root, 'Top', [None] * 8 + FULL[:8])
        (cand,) = candidates_of(self.root, 'Top')
        self.assertEqual((cand['half'], cand['filled']), ('top', 8))
        self.assertEqual([s['pad'] for s in cand['slots']], list(range(9, 17)))      # NI pads 9-16

    def test_roles_the_half_lacks_come_from_the_kits_other_half_first(self):
        pads = ['Kick Mine', 'Snare Mine', 'ClosedHH Mine', 'Clap Mine', 'Rim Mine', None, None, None,
                'Tom Mine', 'Crash Mine', 'OpenHH Mine'] + [None] * 5
        make_kit(self.root, 'Split', pads)
        (cand,) = candidates_of(self.root, 'Split')                    # one candidate: the top half has too few drums
        self.assertEqual(cand['half'], 'bottom')
        self.assertEqual(cand['filled'], 8)
        origins = {s['label']: s['origin'] for s in cand['slots']}
        self.assertEqual((origins['KICK'], origins['TOM'], origins['CYMBAL'], origins['OP HAT']),
                         ('kit', 'kit (other half)', 'kit (other half)', 'kit (other half)'))
        self.assertEqual(cand['native'], 5)

    def test_clap_slot_takes_a_rim_and_rim_slot_a_perc_when_there_is_no_clap(self):
        make_kit(self.root, 'NoClap', ['Kick Mine', 'Snare Mine', 'ClosedHH Mine', 'Rim Mine', 'Shaker Mine'] + [None] * 11)
        (cand,) = candidates_of(self.root, 'NoClap')
        slots = {s['label']: s for s in cand['slots']}
        self.assertEqual(slots['CLAP']['name'], 'Rim Mine')
        self.assertEqual(slots['RIM']['name'], 'Shaker Mine')
        self.assertEqual(slots['CLAP']['kind'], 'rim')

    def test_one_file_on_two_pads_is_used_only_once_in_a_candidate(self):
        # two pads point at the same rim sample (as some NI kits do): CLAP takes it, RIM must not take it again
        make_kit(self.root, 'Twice', ['Kick Mine', 'Snare Mine', 'ClosedHH Mine', 'Rim Mine', 'Rim Mine'] + [None] * 11)
        (cand,) = candidates_of(self.root, 'Twice')
        sources = [s['source'] for s in cand['slots'] if s['source']]
        self.assertEqual(len(sources), len(set(sources)))
        slots = {s['label']: s for s in cand['slots']}
        self.assertEqual((slots['CLAP']['name'], slots['RIM']['origin']), ('Rim Mine', 'empty'))

    def test_kits_without_kick_snare_and_a_hat_make_no_candidate(self):
        make_kit(self.root, 'NoHat', ['Kick Mine', 'Snare Mine', 'Clap Mine', 'Rim Mine', 'Tom Mine'] + [None] * 11)
        make_kit(self.root, 'NoKick', ['Snare Mine', 'ClosedHH Mine', 'Clap Mine', 'Rim Mine', 'Tom Mine'] + [None] * 11)
        make_kit(self.root, 'Melodic', [None] * 16)
        for name in ('NoHat', 'NoKick', 'Melodic'):
            self.assertEqual(candidates_of(self.root, name), [], name)

    def test_an_open_hat_alone_satisfies_the_hat_requirement_and_the_closed_slot_is_left_to_fill(self):
        make_kit(self.root, 'OpenOnly', ['Kick Mine', 'Snare Mine', 'OpenHH Mine', 'Clap Mine', 'Rim Mine'] + [None] * 11)
        (cand,) = candidates_of(self.root, 'OpenOnly')
        slots = {s['label']: s for s in cand['slots']}
        self.assertEqual((slots['CL HAT']['origin'], slots['OP HAT']['name']), ('empty', 'OpenHH Mine'))

    def test_ids_are_stable_and_the_same_samples_are_the_same_kit(self):
        make_kit(self.root, 'One', FULL, pack='A Library')
        make_kit(self.root, 'Copy', FULL, pack='A Library')
        a, b = candidates_of(self.root, 'One', 'A Library')[0], candidates_of(self.root, 'Copy', 'A Library')[0]
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual(a['key'], b['key'])                       # identical kick + snare + hat files
        self.assertEqual(a['id'], candidates_of(self.root, 'One', 'A Library')[0]['id'])
        self.assertEqual(len(km.order_pool([a, b])), 1)


class DiscoveryTests(Base):
    def test_kits_kept_in_a_top_level_kits_folder_are_found_too(self):
        pack = make_kit(self.root, 'Normal', FULL, pack='Groups Library')
        top = Path(self.root) / 'installed' / 'Kits Library'
        (top / 'Kits').mkdir(parents=True)
        (top / 'Kits' / 'Elsewhere Kit.mxgrp').write_bytes((pack / 'Groups/Kits/Normal.mxgrp').read_bytes())
        names = {(k['pack'], k['name']) for k in discover_kits(str(Path(self.root) / 'installed'))}
        self.assertEqual(names, {('Groups', 'Normal'), ('Kits', 'Elsewhere Kit')})


class PoolTests(Base):
    def kits(self):
        make_kit(self.root, 'A1', FULL, pack='A Library')
        make_kit(self.root, 'A2', ['Kick Mine', 'Snare Mine', 'ClosedHH Mine'] + [None] * 13, pack='A Library')
        make_kit(self.root, 'B1', FULL[:7] + [None] * 9, pack='B Library')
        make_kit(self.root, 'B2', FULL, pack='B Library')
        return discover_kits(str(Path(self.root) / 'installed'))

    def test_pool_is_cached_per_kit_and_only_changed_kits_are_read_again(self):
        kits, cache = self.kits(), self.root / 'pool.sqlite'
        calls = []
        real = km.read_kit
        with patch.object(km, 'read_kit', side_effect=lambda k: calls.append(k['name']) or real(k)):
            first = km.build_pool(kits, cache)
            self.assertEqual(sorted(calls), ['A1', 'A2', 'B1', 'B2'])
            calls.clear()
            again = km.build_pool(kits, cache)
            self.assertEqual(calls, [])
            self.assertEqual(sorted(c['id'] for c in again), sorted(c['id'] for c in first))
            kits[0] = dict(kits[0], mtime_ns=kits[0]['mtime_ns'] + 1)
            km.build_pool(kits, cache)
            self.assertEqual(calls, [kits[0]['name']])

    def test_an_unreadable_kit_is_skipped_and_remembered(self):
        kits = self.kits()
        broken = Path(kits[0]['path'])
        broken.write_bytes(b'not a group file')
        kits = discover_kits(str(Path(self.root) / 'installed'))
        pool = km.build_pool(kits, self.root / 'pool.sqlite')
        self.assertNotIn(kits[0]['name'], {c['kit'] for c in pool})
        self.assertGreater(len(pool), 0)

    def test_review_order_is_balanced_complete_first_deterministic_and_skips_what_was_seen(self):
        pool = km.build_pool(self.kits(), self.root / 'pool.sqlite')
        order = km.order_pool(pool, seed=1)
        self.assertEqual(order[0]['filled'], 8)                        # complete kits first
        self.assertEqual({c['pack'] for c in order[:2]}, {'A', 'B'})   # expansions take turns
        self.assertEqual([c['id'] for c in order], [c['id'] for c in km.order_pool(pool, seed=1)])
        seen = [order[0]['id'], order[1]['id']]
        rest = km.order_pool(pool, seen=seen, seed=1)
        self.assertTrue(all(c['id'] not in seen for c in rest))
        self.assertEqual(len(rest), len(order) - 2)
        self.assertEqual(rest, [])                                       # B1 is a copy of the seen B2: it stays hidden too


class SwapTests(Base):
    def setUp(self):
        super().setUp()
        make_kit(self.root, 'Full', FULL)
        (self.cand,) = candidates_of(self.root, 'Full')
        self.sounds = af.SoundIndex(self.root / 'features.sqlite')

    def test_a_swap_gives_another_sound_of_the_same_role_not_already_in_the_kit(self):
        for index, role in ((0, 'Kick'), (1, 'Snare'), (2, 'ClosedHH'), (7, 'Crash')):
            path, name = km.suggest(self.cand, index, self.sounds, seed=1)
            self.assertNotIn(path, [s['source'] for s in self.cand['slots']])
            self.assertTrue(name.startswith(role), (index, name))

    def test_swapping_again_avoids_what_was_already_tried(self):
        tried = set()
        for seed in range(5):
            path, _ = km.suggest(self.cand, 0, self.sounds, seed=seed, avoid=tried)
            self.assertNotIn(path, tried)
            tried.add(path)
        self.assertEqual(len(tried), 5)

    def test_apply_hit_updates_the_slot_and_identity(self):
        path, name = km.suggest(self.cand, 0, self.sounds, seed=1)
        key = self.cand['key']
        km.apply_hit(self.cand, 0, path, name, 'swapped')
        self.assertEqual((self.cand['slots'][0]['source'], self.cand['slots'][0]['origin']), (path, 'swapped'))
        self.assertNotEqual(self.cand['key'], key)

    def test_empty_slots_are_filled_from_the_same_expansion(self):
        make_kit(self.root, 'Sparse', ['Kick Mine', 'Snare Mine', 'ClosedHH Mine', 'Clap Mine'] + [None] * 12)
        (cand,) = candidates_of(self.root, 'Sparse')
        self.assertEqual(cand['filled'], 4)
        left = km.fill_empty(cand, self.sounds, seed=1)
        self.assertEqual((left, cand['filled']), (0, 8))
        self.assertEqual(len({s['source'] for s in cand['slots']}), 8)                 # no sound twice
        for slot, (label, _, kinds) in zip(cand['slots'], km.HOUSE_LAYOUT):
            if slot['origin'] == 'filled':
                self.assertIn(af.classify_sample(slot['source']), kinds + ('perc',), slot)

    def test_a_role_nothing_in_the_library_can_fill_stays_empty(self):
        iso = self.root / 'iso'                                          # alone: no neighbouring expansion to borrow from
        make_kit(iso, 'Lonely', ['Kick Mine', 'Snare Mine', 'ClosedHH Mine', 'Clap Mine'] + [None] * 12, pack='Lonely Library', spares=False)
        (cand,) = candidates_of(iso, 'Lonely', 'Lonely Library')
        self.assertEqual(km.fill_empty(cand, self.sounds, seed=1), 4)
        self.assertEqual(cand['filled'], 4)

    def test_a_thin_expansion_borrows_a_role_from_its_neighbour(self):
        make_kit(self.root, 'Lonely', ['Kick Mine', 'Snare Mine', 'ClosedHH Mine', 'Clap Mine'] + [None] * 12, pack='Lonely Library', spares=False)
        (cand,) = candidates_of(self.root, 'Lonely', 'Lonely Library')   # 'Test Library' (Full) sits beside it
        self.assertEqual(km.fill_empty(cand, self.sounds, seed=1), 0)
        self.assertTrue(all('Test Library' in s['source'] for s in cand['slots'] if s['origin'] == 'filled'))


class HitTests(Base):
    def test_hits_are_resampled_trimmed_faded_and_made_mono_when_both_channels_match(self):
        path = tone(self.root / 'h.wav', rate=22050, seconds=.3, lead=.25, tail=.8, channels=2)
        hit = kitlib.load_hit(path)
        self.assertEqual(hit.shape[1], 1)                              # identical channels
        self.assertLess(len(hit) / 44100, .3 + .05)                    # leading and trailing silence gone (22.05k -> 44.1k)
        self.assertGreater(len(hit) / 44100, .25)
        self.assertEqual(hit.dtype, np.float32)
        self.assertAlmostEqual(float(abs(hit[-1, 0])), 0, places=3)    # faded, no click

    def test_real_stereo_stays_stereo(self):
        t = np.arange(8000) / 44100
        data = np.stack([np.sin(2 * np.pi * 300 * t), np.sin(2 * np.pi * 500 * t)], axis=1) * .5
        sf.write(self.root / 's.wav', data, 44100, subtype='PCM_16')
        self.assertEqual(kitlib.load_hit(self.root / 's.wav').shape[1], 2)

    def test_silent_and_broken_files_raise_and_long_ones_are_capped(self):
        sf.write(self.root / 'quiet.wav', np.zeros(4000), 44100, subtype='PCM_16')
        with self.assertRaises(ValueError):
            kitlib.load_hit(self.root / 'quiet.wav')
        (self.root / 'bad.wav').write_bytes(b'RIFFxxxx')
        with self.assertRaises(Exception):
            kitlib.load_hit(self.root / 'bad.wav')
        long = tone(self.root / 'long.wav', freq=100, decay=.1, seconds=8)
        self.assertLessEqual(len(kitlib.load_hit(long)) / 44100, kitlib.MAX_SECONDS + .01)

    def test_character_tags_use_the_snare(self):
        c = kitlib.character_tag
        self.assertEqual(c({}), '')
        self.assertEqual(c({'snare': {'centroid': 900, 'decay': .12}}), 'dark')
        self.assertEqual(c({'snare': {'centroid': 2400, 'decay': .05}}), 'mid tight')
        self.assertEqual(c({'snare': {'centroid': 4500, 'decay': .3}}), 'bright roomy')
        self.assertEqual(c({'snare': {'centroid': 1800, 'decay': .17}}), 'mid roomy')       # boundaries: >= 1800 is mid


class LibraryTests(Base):
    def setUp(self):
        super().setUp()
        make_kit(self.root, 'Full', FULL)
        make_kit(self.root, 'Other', FULL[:7] + [None] * 9, pack='Other Library')
        self.a, self.b = candidates_of(self.root, 'Full')[0], candidates_of(self.root, 'Other', 'Other Library')[0]
        self.lib = kitlib.KitLibrary(self.root / 'lib')

    def test_a_kept_kit_is_eight_44k_16bit_wavs_and_a_manifest_with_a_permanent_id(self):
        manifest = self.lib.add_kit(self.a)
        self.assertEqual((manifest['id'], manifest['number']), ('K001', 1))
        folder = self.root / 'lib/kits/K001'
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         sorted([s + '.WAV' for s in ('KICK', 'SNARE', 'CLHAT', 'OPHAT', 'CLAP', 'RIM', 'TOM', 'CYMBAL')] + ['manifest.json']))
        for slot in manifest['slots']:
            info = sf.info(folder / slot['wav'])
            self.assertEqual((info.samplerate, info.subtype), (44100, 'PCM_16'))
        self.assertEqual(manifest['source']['kit'], 'Full')
        self.assertEqual(json.loads((folder / 'manifest.json').read_text())['id'], 'K001')
        self.assertGreater(manifest['memory_bytes'], 0)

    def test_the_loudest_hit_of_a_kit_peaks_near_minus_one_db_with_relative_levels_kept(self):
        manifest = self.lib.add_kit(self.a)
        peaks = [float(np.max(np.abs(sf.read(self.lib.wav(manifest, i))[0]))) for i in range(8)]
        self.assertAlmostEqual(max(peaks), kitlib.TARGET_PEAK, delta=.02)
        sources = [float(np.max(np.abs(kitlib.load_hit(s['source'])))) for s in manifest['slots']]
        ratios = [p / s for p, s in zip(peaks, sources)]
        self.assertLess(max(ratios) - min(ratios), .02 * max(ratios))          # one gain for the whole kit

    def test_empty_slots_have_no_wav_and_ids_never_skip_or_repeat(self):
        first = self.lib.add_kit(self.b)
        self.assertEqual(first['slots'][7]['wav'], '')
        second = self.lib.add_kit(self.a)
        self.assertEqual([m['id'] for m in self.lib.kits()], ['K001', 'K002'])
        self.assertEqual(self.lib.next_number(), 3)

    def test_undoing_a_keep_removes_the_newest_kit_only_and_the_id_is_reused(self):
        self.lib.add_kit(self.b)
        newest = self.lib.add_kit(self.a)
        with self.assertRaises(ValueError):
            self.lib.remove_last(1)
        self.lib.remove_last(newest['number'])
        self.assertEqual([m['id'] for m in self.lib.kits()], ['K001'])
        self.assertEqual(self.lib.add_kit(self.a)['id'], 'K002')

    def test_the_counter_recovers_from_the_kits_on_disk_if_its_state_file_is_lost(self):
        self.lib.add_kit(self.a)
        self.lib.add_kit(self.b)
        (self.root / 'lib/library.json').unlink()
        self.assertEqual(kitlib.KitLibrary(self.root / 'lib').next_number(), 3)

    def test_a_failed_keep_leaves_no_half_written_kit(self):
        bad = json.loads(json.dumps(self.a))
        bad['slots'][0]['source'] = str(self.root / 'missing.wav')
        with self.assertRaises(Exception):
            self.lib.add_kit(bad)
        self.assertEqual(self.lib.kits(), [])
        self.assertEqual(list((self.root / 'lib/kits').iterdir()), [])
        self.assertEqual(self.lib.next_number(), 1)

    def test_a_cache_of_decoded_hits_is_used_instead_of_decoding_again(self):
        cache = {s['source']: kitlib.load_hit(s['source']) for s in self.a['slots']}
        with patch.object(kitlib, 'load_hit', side_effect=AssertionError('decoded again')):
            self.assertEqual(self.lib.add_kit(self.a, hit_cache=cache)['id'], 'K001')


class PackTests(Base):
    def setUp(self):
        super().setUp()
        make_kit(self.root, 'Full', FULL)
        self.cand = candidates_of(self.root, 'Full')[0]
        self.lib = kitlib.KitLibrary(self.root / 'lib')

    def keep(self, n):
        return [self.lib.add_kit(self.cand) for _ in range(n)]

    def test_pad_numbers_put_kit_a_on_pads_1_to_8_and_kit_b_on_9_to_16_of_each_bank(self):
        self.assertEqual(kitpack.pad_for(0, 0), 0)
        self.assertEqual(kitpack.pad_for(0, 7), 7)
        self.assertEqual(kitpack.pad_for(1, 0), 8)
        self.assertEqual(kitpack.pad_for(1, 7), 15)
        self.assertEqual(kitpack.pad_for(2, 0), 16)             # bank B
        self.assertEqual(kitpack.pad_for(7, 7), 63)             # bank D, last pad
        self.assertEqual(sorted(kitpack.pad_for(p, s) for p in range(8) for s in range(8)), list(range(64)))

    def test_the_program_names_every_sample_and_chokes_each_kits_hats_together_only(self):
        manifests = self.keep(3)
        data, names = kitpack.program_data(manifests)
        self.assertEqual(names[0], 'K001_KICK')
        self.assertEqual(names[8], 'K002_KICK')
        self.assertEqual(names[16 + 7], 'K003_CYMBAL')
        self.assertEqual(sum(bool(n) for n in names), 24)
        self.assertEqual(len(set(n for n in names if n)), 24)           # unique across the program
        for position in range(3):
            for slot in range(8):
                pad = kitpack.pad_for(position, slot)
                group = data[mpc.PAD_BASE + pad * mpc.PAD_SIZE + 0x63]
                self.assertEqual(group, position + 1 if slot in (2, 3) else 0, (position, slot))

    def test_a_built_program_is_a_valid_pgm_with_its_wavs_beside_it(self):
        manifests = self.keep(3)
        info = kitpack.build_program(self.lib, manifests, self.root / 'out')
        folder = info['folder']
        self.assertEqual(info['name'], 'KITS_001_003')
        self.assertEqual(info['validation']['assigned_pads'], 24)
        self.assertEqual(mpc.validate_program(folder / 'KITS_001_003.PGM')['format'], 'MPC1000 PGM 1.00')
        mpc.read_pad_notes(folder / 'KITS_001_003.PGM')                  # the pad-note table is intact
        self.assertEqual(len(list(folder.glob('*.WAV'))), 24)
        self.assertTrue((folder / 'LOAD_ME.txt').is_file())
        data = (folder / 'KITS_001_003.PGM').read_bytes()
        names = [data[mpc.PAD_BASE + i * mpc.PAD_SIZE:mpc.PAD_BASE + i * mpc.PAD_SIZE + 16].rstrip(b'\0').decode() for i in range(64)]
        self.assertEqual(names[8], 'K002_KICK')                           # kit B of bank A, pad 9
        self.assertEqual(names[40], '')                                  # bank C is empty

    def test_ten_kits_become_two_programs_and_a_catalogue_says_where_each_lives(self):
        self.keep(10)
        programs = kitpack.build_all(self.lib, self.root / 'out')
        self.assertEqual([p['name'] for p in programs], ['KITS_001_008', 'KITS_009_010'])
        self.assertEqual([len(p['kits']) for p in programs], [8, 2])
        import csv
        with (self.root / 'out' / 'CATALOGUE.csv').open(encoding='utf-8-sig') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 10)
        by_id = {r['kit']: r for r in rows}
        self.assertEqual((by_id['K001']['program'], by_id['K001']['bank'], by_id['K001']['pads']), ('KITS_001_008', 'A', '1-8'))
        self.assertEqual((by_id['K002']['bank'], by_id['K002']['pads']), ('A', '9-16'))
        self.assertEqual((by_id['K008']['bank'], by_id['K008']['pads']), ('D', '9-16'))
        self.assertEqual((by_id['K009']['program'], by_id['K009']['pads']), ('KITS_009_010', '1-8'))
        self.assertEqual(by_id['K001']['expansion'], 'Test')
        self.assertEqual(by_id['K001']['KICK'], 'Kick Mine')

    def test_rebuilding_replaces_a_program_instead_of_stacking_files(self):
        self.keep(2)
        kitpack.build_all(self.lib, self.root / 'out')
        before = sorted(p.name for p in (self.root / 'out/KITS_001_002').iterdir())
        kitpack.build_all(self.lib, self.root / 'out')
        self.assertEqual(sorted(p.name for p in (self.root / 'out/KITS_001_002').iterdir()), before)
        self.assertFalse(list((self.root / 'out').glob('*.tmp')))

    def test_nothing_to_build_and_overfull_programs_are_refused(self):
        with self.assertRaises(ValueError):
            kitpack.build_all(self.lib, self.root / 'out')
        with self.assertRaises(ValueError):
            kitpack.build_program(self.lib, self.keep(1) * 9, self.root / 'out')

    def test_a_missing_wav_stops_the_build_and_leaves_no_partial_program(self):
        manifests = self.keep(2)
        (self.root / 'lib/kits/K002/SNARE.WAV').unlink()
        with self.assertRaises(ValueError):
            kitpack.build_program(self.lib, manifests, self.root / 'out')
        self.assertFalse((self.root / 'out/KITS_001_002').exists())
        self.assertFalse(list((self.root / 'out').glob('*.tmp')))


class GrooveTests(unittest.TestCase):
    def hits(self, **named):
        slots = ['kick', 'snare', 'clhat', 'ophat', 'clap', 'rim', 'tom', 'cymbal']
        t = np.arange(8000) / 44100
        return [(np.sin(2 * np.pi * (100 + 40 * i) * t) * np.exp(-t * 20)).astype(np.float32)[:, None] if name not in named
                else named[name] for i, name in enumerate(slots)]

    def test_the_beat_is_two_bars_of_audible_stereo_with_headroom(self):
        out = groove.render_groove(self.hits(), bpm=92)
        step = 60 / 92 / 4
        self.assertEqual(out.shape[1], 2)
        self.assertGreaterEqual(len(out) / 44100, 32 * step)
        self.assertGreater(float(np.max(np.abs(out))), .3)
        self.assertLessEqual(float(np.max(np.abs(out))), .9 + 1e-6)

    def test_empty_slots_are_skipped_and_a_wrong_count_is_refused(self):
        hits = self.hits()
        hits[0] = None                                                   # no kick: the first beat is only the cymbal
        out = groove.render_groove(hits)
        self.assertGreater(float(np.max(np.abs(out))), 0)
        with self.assertRaises(ValueError):
            groove.render_groove(hits[:7])
        self.assertEqual(float(np.max(np.abs(groove.render_groove([None] * 8)))), 0.0)

    def test_stereo_and_mono_hits_both_work(self):
        stereo = np.random.default_rng(1).uniform(-.3, .3, (6000, 2)).astype(np.float32)
        out = groove.render_groove(self.hits(snare=stereo))
        self.assertEqual(out.shape[1], 2)

    def test_a_closed_hat_shuts_the_open_hat(self):
        long_open = np.full((44100 * 2, 1), .5, dtype=np.float32)         # rings for two seconds if nothing stops it
        click = np.zeros((200, 1), dtype=np.float32)
        click[0] = 1
        hits = [None, None, click, long_open, None, None, None, None]
        out = groove.render_groove(hits, bpm=120)
        step = int(round(60 / 120 / 4 * 44100))
        at = lambda step_index: float(np.max(np.abs(out[step_index * step:step_index * step + 50])))
        self.assertGreater(at(14) + 0, 0)                                  # the open hat sounds on step 14 of bar one
        # open hat at step 14 (level .55 * .9 * .5); the closed hat at bar-two step 2 (= step 18) cuts it off
        self.assertGreater(float(np.max(np.abs(out[15 * step:16 * step]))), .1)
        self.assertLess(float(np.max(np.abs(out[19 * step:20 * step]))), .02)


if __name__ == '__main__':
    unittest.main()
