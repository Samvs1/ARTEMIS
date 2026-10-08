"""Tests for body/chirps.py: every chirp must be a short, quiet, valid WAV."""
import io
import unittest
import wave

try:
    import numpy as np
except ImportError:          # pragma: no cover
    np = None

DESIGN_NAMES = "happy curious surprised thinking excited sleepy wake listen bored poke".split()


@unittest.skipIf(np is None, "numpy is not installed")
class ChirpTests(unittest.TestCase):
    def setUp(self):
        from body import chirps
        self.chirps = chirps

    def read(self, data):
        with wave.open(io.BytesIO(data)) as w:
            rate, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            pcm = np.frombuffer(w.readframes(n), dtype="<i2")
        return rate, ch, width, pcm

    def test_names(self):
        self.assertEqual(list(self.chirps.NAMES), DESIGN_NAMES)

    def test_every_chirp_is_valid_short_and_quiet(self):
        for name in self.chirps.NAMES:
            rate, ch, width, pcm = self.read(self.chirps.chirp(name))
            self.assertIn(rate, (16000, 22050), name)
            self.assertEqual((ch, width), (1, 2), name)
            seconds = len(pcm) / rate
            self.assertTrue(0.1 <= seconds <= 0.8, f"{name}: {seconds}")
            peak_db = 20 * np.log10(np.abs(pcm).max() / 32768)
            self.assertTrue(-16 <= peak_db <= -9, f"{name}: peak {peak_db:.1f} dBFS")
            self.assertLess(abs(int(pcm[-1])), 300, f"{name} ends softly")

    def test_chirps_differ_from_each_other(self):
        self.assertEqual(len({self.chirps.chirp(n) for n in self.chirps.NAMES}), len(self.chirps.NAMES))

    def test_cached(self):
        self.assertIs(self.chirps.chirp("happy"), self.chirps.chirp("happy"))

    def test_unknown_name_gives_a_soft_default(self):
        for name in ("nonsense", "", "babble"):
            rate, ch, width, pcm = self.read(self.chirps.chirp(name))
            self.assertTrue(0.1 <= len(pcm) / rate <= 0.8)
            self.assertLess(np.abs(pcm).max() / 32768, 0.3)
        self.assertEqual(self.chirps.chirp("nonsense"), self.chirps.chirp("other"))

    def test_curious_glides_up(self):
        rate, _, _, pcm = self.read(self.chirps.chirp("curious"))
        def pitch(x):
            return np.argmax(np.abs(np.fft.rfft(x * np.hanning(len(x))))) * rate / len(x)
        n = len(pcm)
        self.assertLess(pitch(pcm[: n // 3].astype(float)), pitch(pcm[n // 3: n * 2 // 3].astype(float)) + 1)


if __name__ == "__main__":
    unittest.main()
