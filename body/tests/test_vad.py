"""Tests for body/vad.py with made-up sound (no microphone needed)."""
import io
import unittest
import wave

try:
    import numpy as np
except ImportError:          # pragma: no cover
    np = None


def quiet(rng, n=480, amp=20):
    return (rng.normal(0, amp, n)).astype(np.int16).tobytes()


def voiced(rng, n=480, amp=3000):
    """Speech-ish: a few tones plus a bit of noise."""
    t = np.arange(n) / 16000
    x = sum(np.sin(2 * np.pi * f * t + rng.uniform(0, 6)) for f in (180, 540, 1100, 2300)) * (amp / 4)
    return (x + rng.normal(0, 200, n)).astype(np.int16).tobytes()


@unittest.skipIf(np is None, "numpy is not installed")
class DetectorTests(unittest.TestCase):
    def setUp(self):
        from body import vad
        self.vad = vad
        self.rng = np.random.default_rng(1)

    def test_silence_is_not_speech_and_voice_is(self):
        det = self.vad.VoiceDetector(use_webrtc=False)
        self.assertFalse(det.uses_webrtc)
        results = [det.is_speech(quiet(self.rng)) for _ in range(50)]
        self.assertFalse(any(results))
        results = [det.is_speech(voiced(self.rng)) for _ in range(20)]
        self.assertTrue(all(results))
        self.assertFalse(det.is_speech(quiet(self.rng)))

    def test_noise_bursts_count_as_sound(self):
        det = self.vad.VoiceDetector(use_webrtc=False)
        for _ in range(30):
            det.is_speech(quiet(self.rng))
        self.assertTrue(det.is_speech((self.rng.normal(0, 2500, 480)).astype(np.int16).tobytes()))

    def test_adapts_to_a_noisy_room(self):
        det = self.vad.VoiceDetector(use_webrtc=False)
        for _ in range(100):
            det.is_speech(quiet(self.rng, amp=500))             # constant fan noise
        self.assertFalse(det.is_speech(quiet(self.rng, amp=500)))
        self.assertTrue(det.is_speech(voiced(self.rng, amp=9000)))

    def test_long_speech_does_not_raise_the_floor_much(self):
        det = self.vad.VoiceDetector(use_webrtc=False)
        for _ in range(30):
            det.is_speech(quiet(self.rng))
        for _ in range(200):                                      # 6 seconds of talking
            det.is_speech(voiced(self.rng))
        self.assertTrue(det.is_speech(voiced(self.rng)))

    def test_default_detector_works_without_webrtcvad(self):
        det = self.vad.VoiceDetector()
        self.assertIn(det.is_speech(bytes(960)), (True, False))
        self.assertFalse(det.is_speech(b"\x00"))                 # odd short frame does not crash


@unittest.skipIf(np is None, "numpy is not installed")
class UtteranceTests(unittest.TestCase):
    def setUp(self):
        from body import vad
        self.vad = vad
        self.rng = np.random.default_rng(2)

    def make(self, **kw):
        return self.vad.Utterances(self.vad.VoiceDetector(use_webrtc=False), **kw)

    def feed_all(self, utt, frames):
        return [r for r in (utt.feed(f) for f in frames) if r is not None]

    def test_one_sentence_with_pre_roll_and_trailing_quiet(self):
        utt = self.make()
        lead = [quiet(self.rng) for _ in range(40)]
        talk = [voiced(self.rng) for _ in range(30)]
        tail = [quiet(self.rng) for _ in range(40)]
        done = self.feed_all(utt, lead + talk + tail)
        self.assertEqual(len(done), 1)
        frames = len(done[0]) // 960
        self.assertEqual(len(done[0]) % 960, 0)
        # 300 ms (10 frames) before, the 30 speech frames, a short tail of quiet (about 6 frames)
        self.assertTrue(40 <= frames <= 50, frames)
        self.assertEqual(done[0][10 * 960:11 * 960], talk[0])                 # the speech starts after 10 frames of pre-roll
        self.assertFalse(utt.speaking())

    def test_speaking_flag_and_no_start_from_a_short_blip(self):
        utt = self.make()
        self.feed_all(utt, [quiet(self.rng) for _ in range(20)])
        self.feed_all(utt, [voiced(self.rng) for _ in range(3)])               # a cough: fewer than 6 frames
        self.assertFalse(utt.speaking())
        self.feed_all(utt, [quiet(self.rng) for _ in range(20)])
        self.assertFalse(utt.speaking())
        self.feed_all(utt, [voiced(self.rng) for _ in range(8)])
        self.assertTrue(utt.speaking())

    def test_a_short_pause_inside_a_sentence_does_not_end_it(self):
        utt = self.make(end_silence_ms=700)
        frames = ([quiet(self.rng) for _ in range(20)] + [voiced(self.rng) for _ in range(15)]
                  + [quiet(self.rng) for _ in range(10)]                        # 300 ms pause
                  + [voiced(self.rng) for _ in range(15)] + [quiet(self.rng) for _ in range(40)])
        done = self.feed_all(utt, frames)
        self.assertEqual(len(done), 1)
        self.assertGreater(len(done[0]) // 960, 40)

    def test_max_seconds_cuts_off(self):
        utt = self.make(max_seconds=1.0)
        done = self.feed_all(utt, [quiet(self.rng) for _ in range(20)] + [voiced(self.rng) for _ in range(100)])
        self.assertGreaterEqual(len(done), 1)
        self.assertLessEqual(len(done[0]) // 960, 34)                          # 1 s is 33 frames

    def test_no_speech_timeout(self):
        utt = self.make()
        utt.reset(no_speech_timeout=1.0)
        self.feed_all(utt, [quiet(self.rng) for _ in range(30)])               # 0.9 s
        self.assertFalse(utt.timed_out())
        self.feed_all(utt, [quiet(self.rng) for _ in range(5)])
        self.assertTrue(utt.timed_out())
        utt.reset(no_speech_timeout=1.0)
        self.assertFalse(utt.timed_out())

    def test_speech_prevents_timeout_and_no_timeout_by_default(self):
        utt = self.make()
        utt.reset(no_speech_timeout=0.5)
        self.feed_all(utt, [voiced(self.rng) for _ in range(10)])
        self.feed_all(utt, [quiet(self.rng) for _ in range(60)])
        self.assertFalse(utt.timed_out())
        utt.reset()
        self.feed_all(utt, [quiet(self.rng) for _ in range(300)])
        self.assertFalse(utt.timed_out())

    def test_reset_drops_a_half_heard_sentence(self):
        utt = self.make()
        self.feed_all(utt, [voiced(self.rng) for _ in range(10)])
        self.assertTrue(utt.speaking())
        utt.reset()
        self.assertFalse(utt.speaking())

    def test_two_sentences_in_a_row(self):
        utt = self.make()
        one = [voiced(self.rng) for _ in range(20)]
        frames = ([quiet(self.rng) for _ in range(30)] + one + [quiet(self.rng) for _ in range(40)]
                  + one + [quiet(self.rng) for _ in range(40)])
        self.assertEqual(len(self.feed_all(utt, frames)), 2)


class WavTests(unittest.TestCase):
    def test_pcm_to_wav(self):
        from body.vad import pcm_to_wav
        wav = pcm_to_wav(bytes(3200))
        with wave.open(io.BytesIO(wav)) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()), (16000, 1, 2, 1600))
        with wave.open(io.BytesIO(pcm_to_wav(bytes(100), 22050))) as w:
            self.assertEqual(w.getframerate(), 22050)


if __name__ == "__main__":
    unittest.main()
