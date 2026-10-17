import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import mido
import numpy as np
import soundfile as sf

from fixtures import fixture, probe
from ni_to_sp import sp_transfer
from ni_to_sp.capture import (create_capture, file_hash, inspect_render, load_capture,
                              load_prepared, prepare_kit)
from ni_to_sp.library import discover_kits
from ni_to_sp.model import sp_pad_number


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.kit, self.folder, self.job, self.master = fixture(self.root)

    def check(self, **kwargs):
        return inspect_render(self.job, self.master, confirmed=True, **kwargs)

    def test_discovery_indexes_only_real_group_files_without_decoding_audio(self):
        other = self.root / "installed/Test Library/Groups/.hidden/Hidden.mxgrp"
        other.parent.mkdir()
        other.write_bytes(b"ignore")
        with patch("soundfile.SoundFile", side_effect=AssertionError("Discovery decoded audio")):
            kits = discover_kits(self.root / "installed")
        self.assertEqual(len(kits), 1)
        self.assertEqual(kits[0]["name"], "Test Kit")

    def test_midi_has_exact_root_note_mapping_velocity_hold_and_timing(self):
        midi = mido.MidiFile(self.folder / "CAPTURE.mid")
        elapsed, notes, offs = 0, [], []
        for message in midi:
            elapsed += message.time
            if message.type == "note_on":
                notes.append((message.note, elapsed, message.velocity, message.channel))
            if message.type == "note_off":
                offs.append(elapsed)
        self.assertEqual([row[0] for row in notes], list(range(36, 52)))
        self.assertEqual([round(row[1], 5) for row in notes], [2 + i * 4 for i in range(16)])
        self.assertTrue(all(row[2:] == (100, 0) for row in notes))
        self.assertTrue(all(abs(end - row[1] - .5) < 1e-8 for end, row in zip(offs, notes)))
        self.assertAlmostEqual(elapsed, 66)
        self.assertEqual(load_capture(self.folder), self.job)

    def test_capture_can_keep_the_users_maschine_tempo(self):
        folder, job = create_capture(self.kit, self.root / "captures", bpm=89)
        elapsed, starts = 0, []
        for message in mido.MidiFile(folder / "CAPTURE.mid"):
            elapsed += message.time
            if message.type == "note_on":
                starts.append(elapsed)
        self.assertEqual(job["bpm"], 89)
        for actual, slot in zip(starts, job["slots"]):
            self.assertAlmostEqual(actual, slot["start"], places=3)
        self.assertAlmostEqual(elapsed, job["duration"], places=3)

    def test_verified_capture_preserves_layout_amplitude_and_transfer_recipe(self):
        original = file_hash(self.master)
        report = self.check()
        self.assertTrue(report["ok"], report["problems"])
        output, session = prepare_kit(self.job, report, self.root / "prepared")
        restored, recipe = load_prepared(output)
        self.assertEqual([pad.sample_id for pad in session.pads], [pad.sample_id for pad in restored.pads])
        plan = sp_transfer.make_transfer_plan(restored, 0, "B", probe())
        self.assertEqual([entry.pad_number for entry in plan.entries[:4]], [13, 14, 15, 16])
        self.assertEqual([entry.pad_number for entry in plan.entries[12:]], [1, 2, 3, 4])
        _, files = sp_transfer._export_files(plan, output)
        self.assertEqual(len(files), 16)
        wave, rate = sf.read(files["B13"], dtype="float32", always_2d=True)
        with sf.SoundFile(self.master) as master:
            master.seek(2 * 48000)
            source = master.read(4 * 48000, dtype="float32", always_2d=True)
        self.assertEqual(rate, 48000)
        self.assertLess(float(np.max(np.abs(wave - source))), 1 / 32768 + 1e-7)
        self.assertEqual(file_hash(self.master), original)
        self.assertEqual(len(recipe["pads"]), 16)

    def test_human_confirmation_is_required_before_audio_is_opened(self):
        with patch("soundfile.SoundFile", side_effect=AssertionError("Should not decode")):
            with self.assertRaisesRegex(ValueError, "Confirm"):
                inspect_render(self.job, self.master)

    def test_short_or_corrupt_schedule_cannot_shift_pad_mapping(self):
        bad = copy.deepcopy(self.job)
        bad["slots"][2]["start"] += .1
        with self.assertRaisesRegex(ValueError, "timing"):
            inspect_render(bad, self.master, confirmed=True)
        sf.write(self.master, np.zeros(48000), 48000)
        with self.assertRaisesRegex(ValueError, "too short"):
            self.check()

    def test_silent_pad_blocks_transfer_until_explicitly_declared_empty(self):
        with sf.SoundFile(self.master, "r+") as audio:
            audio.seek(2 * 48000)
            audio.write(np.zeros((4 * 48000, 2), dtype=np.float32))
        report = self.check()
        self.assertFalse(report["ok"])
        self.assertIn("Silent pad", report["problems"][0])
        report = self.check(empty_pads=[1])
        self.assertTrue(report["ok"])
        output, session = prepare_kit(self.job, report, self.root / "prepared")
        self.assertEqual(len(session.samples), 16)
        data, rate = sf.read(output / "13_NI_01.wav")
        self.assertEqual(len(data), 4800)
        self.assertTrue(np.all(data == 0))

    def test_wrong_empty_pad_and_overlapping_tails_are_rejected(self):
        report = self.check(empty_pads=[2])
        self.assertFalse(report["ok"])
        self.assertIn("Marked empty but audible", report["problems"][0])
        with sf.SoundFile(self.master, "r+") as audio:
            audio.seek(round(5.8 * 48000))
            audio.write(np.full((round(.2 * 48000), 2), .05, dtype=np.float32))
        self.assertTrue(any("tail reaches" in problem for problem in self.check()["problems"]))

    def test_clipping_and_nonfinite_audio_cannot_publish_a_kit(self):
        with sf.SoundFile(self.master, "r+") as audio:
            audio.seek(round(2.2 * 48000))
            audio.write(np.full((100, 2), 1.1, dtype=np.float32))
        self.assertTrue(any("0 dBFS" in problem for problem in self.check()["problems"]))
        with sf.SoundFile(self.master, "r+") as audio:
            audio.seek(round(2.2 * 48000))
            audio.write(np.full((100, 2), np.nan, dtype=np.float32))
        with self.assertRaisesRegex(ValueError, "Invalid audio"):
            self.check()

    def test_changed_render_kit_or_schedule_invalidates_inspection(self):
        report = self.check()
        bad = copy.deepcopy(self.job)
        bad["velocity"] = 50
        with self.assertRaisesRegex(ValueError, "job changed"):
            prepare_kit(bad, report, self.root / "prepared")
        Path(self.kit["path"]).write_bytes(b"changed kit")
        with self.assertRaisesRegex(ValueError, "original kit changed"):
            prepare_kit(self.job, report, self.root / "prepared")
        with self.master.open("ab") as handle:
            handle.write(b"changed")
        with self.assertRaisesRegex(ValueError, "render changed"):
            prepare_kit(self.job, report, self.root / "prepared")

    def test_cancelled_preparation_is_incomplete_and_never_ready_for_transfer(self):
        report = self.check()
        cancel = threading.Event()
        def progress(*_):
            cancel.set()
        with self.assertRaises(InterruptedError):
            prepare_kit(self.job, report, self.root / "prepared", progress, cancel)
        folder = next((self.root / "prepared").iterdir())
        self.assertTrue((folder / "INCOMPLETE.txt").is_file())
        self.assertFalse((folder / "COMPLETE.json").exists())
        with self.assertRaisesRegex(ValueError, "completed"):
            load_prepared(folder)

    def test_installed_library_never_becomes_an_output_folder(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            create_capture(self.kit, Path(self.kit["root"]) / "captures")
        with self.assertRaisesRegex(ValueError, "outside"):
            prepare_kit(self.job, self.check(), Path(self.kit["root"]) / "prepared")

    def test_bad_export_file_is_rejected_before_any_usb_access(self):
        output, session = prepare_kit(self.job, self.check(), self.root / "prepared")
        plan = sp_transfer.make_transfer_plan(session, 0, "B", probe())
        (output / "13_NI_01.wav").write_bytes(b"corrupt")
        with patch("ni_to_sp.sp_transfer.probe", side_effect=AssertionError("USB should not be accessed")):
            with self.assertRaises(sp_transfer.TransferError):
                sp_transfer.send(plan, "fixture.exe", output, reviewed=True)


if __name__ == "__main__":
    unittest.main()
