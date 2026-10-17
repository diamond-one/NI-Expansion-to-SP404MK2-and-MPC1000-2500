from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from direct_fixtures import direct_fixture
from ni_to_sp.direct import inspect_group, preview_session
from ni_to_sp.preview import LiveSampler


class PreviewTests(unittest.TestCase):
    def test_preloaded_pads_mix_without_file_access_in_the_audio_callback(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = preview_session(inspect_group(direct_fixture(temporary)))
            engine = LiveSampler(samplerate=48000)
            report = engine.prepare(session)
            self.assertEqual(report['loaded'], 16)
            with patch('ni_to_sp.preview.load_audio', side_effect=AssertionError('No callback file reads')):
                self.assertTrue(engine.trigger(0))
                self.assertTrue(engine.trigger(1, 90))
                data = engine._render(256)
                self.assertEqual(data.shape, (256, 2))
                self.assertGreater(float(np.max(np.abs(data))), .05)
                self.assertLessEqual(float(np.max(np.abs(data))), .95)
                engine.silence()
                self.assertTrue(np.all(engine._render(64) == 0))

    def test_hat_choke_uses_sound_type_instead_of_cratebuilder_pad_numbers(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = preview_session(inspect_group(direct_fixture(temporary)))
            engine = LiveSampler(samplerate=48000)
            engine.prepare(session)
            engine.trigger(3)
            engine._render(64)
            engine.trigger(2)
            engine._render(128)
            self.assertEqual([v.pad for v in engine._voices], [2])

    def test_replacement_preserves_existing_voices_and_reuses_unchanged_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = preview_session(inspect_group(direct_fixture(temporary)))
            engine = LiveSampler(samplerate=48000)
            engine.prepare(session)
            engine.trigger(0)
            engine._render(32)
            voice = engine._voices[0]
            session.pads[1].sample_id = None
            with patch('ni_to_sp.preview.load_audio', side_effect=AssertionError('unchanged pads must be cached')):
                engine.update_bank(session)
            engine._render(32)
            self.assertIs(engine._voices[0], voice)
            self.assertEqual(voice.position, 64)
            self.assertFalse(engine.trigger(1))

    def test_audio_stream_is_created_and_closed_in_its_own_com_lifetime(self):
        events = []
        @contextmanager
        def apartment():
            events.append(('enter', threading.get_ident()))
            try:
                yield
            finally:
                events.append(('exit', threading.get_ident()))
        engine = LiveSampler(samplerate=48000)
        def opened():
            events.append(('open', threading.get_ident()))
            engine._stream = SimpleNamespace(active=True)
        def closed():
            events.append(('close', threading.get_ident()))
            engine._stream = None
        with patch('ni_to_sp.preview.audio_apartment', apartment), patch.object(engine, '_open_stream', opened), patch.object(engine, '_close_stream', closed):
            engine.start()
            self.assertTrue(engine.running)
            engine.stop()
        self.assertEqual([e[0] for e in events], ['enter', 'open', 'close', 'exit'])
        self.assertEqual(len({e[1] for e in events}), 1)
        self.assertNotEqual(events[0][1], threading.get_ident())


if __name__ == '__main__':
    unittest.main()
