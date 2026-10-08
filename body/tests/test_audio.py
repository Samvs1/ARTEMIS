"""Tests for body/audio.py: the fake microphone and speaker, WAV reading, and the missing-sounddevice error."""
import io
import sys
import threading
import time
import types
import unittest
import wave
from unittest import mock

try:
    import numpy as np
except ImportError:          # pragma: no cover
    np = None


def make_wav(samples, rate=16000, channels=1):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype("<i2").tobytes())
    return out.getvalue()


def sine(freq, seconds, rate, amp=8000):
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * freq * t) * amp).astype(np.int16)


@unittest.skipIf(np is None, "numpy is not installed")
class FakeAudioInTests(unittest.TestCase):
    def setUp(self):
        from body import audio
        self.audio = audio

    def test_frames_are_30ms_and_gaps_are_silent(self):
        wav = make_wav(sine(440, 0.5, 16000))                 # 8000 samples = 16 frames + a bit
        mic = self.audio.FakeAudioIn([wav], gap_seconds=0.3)
        frames = list(mic.frames())
        self.assertTrue(all(len(f) == 960 for f in frames))
        gap = round(0.3 * 16000 / 480)                        # 10 frames
        sound = -(-8000 // 480)                               # 17 frames, last one padded
        self.assertEqual(len(frames), gap + sound + gap)
        self.assertEqual(set(b"".join(frames[:gap])), {0})
        self.assertEqual(set(b"".join(frames[-gap:])), {0})
        self.assertNotEqual(set(frames[gap]), {0})

    def test_gap_before_every_item_and_after_last(self):
        wavs = [make_wav(sine(440, 0.03, 16000)), make_wav(sine(660, 0.03, 16000))]
        frames = list(self.audio.FakeAudioIn(wavs, gap_seconds=0.15).frames())     # 5 frames of gap
        self.assertEqual(len(frames), 5 + 1 + 5 + 1 + 5)

    def test_resamples_other_rates_and_stereo_to_16k_mono(self):
        for rate, channels in ((8000, 1), (44100, 1), (48000, 2), (22050, 2)):
            mono = sine(500, 1.0, rate)
            data = mono if channels == 1 else np.stack([mono, mono], axis=1).reshape(-1)
            wav = make_wav(data, rate, channels)
            mic = self.audio.FakeAudioIn([wav], gap_seconds=0)
            pcm = np.frombuffer(b"".join(mic.frames()), dtype=np.int16)
            self.assertLess(abs(len(pcm) - 16000), 480, f"rate {rate}")              # about one second, padded to a frame
            body = pcm[:16000].astype(float)
            spectrum = np.abs(np.fft.rfft(body))
            peak_hz = np.argmax(spectrum) * 16000 / len(body)
            self.assertAlmostEqual(peak_hz, 500, delta=5, msg=f"rate {rate}")
            self.assertGreater(np.abs(body).max(), 5000)

    def test_accepts_paths(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.wav"
            p.write_bytes(make_wav(sine(300, 0.1, 16000)))
            frames = list(self.audio.FakeAudioIn([str(p), p], gap_seconds=0).frames())
        self.assertEqual(len(frames), 2 * 4)                                         # 1600 samples = 4 frames, each

    def test_bad_input_gives_a_clear_error(self):
        with self.assertRaises(self.audio.AudioError):
            self.audio.FakeAudioIn([b"not a wav"])
        with self.assertRaises(self.audio.AudioError):
            self.audio.FakeAudioIn(["/no/such/file.wav"])

    def test_realtime_sleeps_per_frame(self):
        mic = self.audio.FakeAudioIn([], gap_seconds=0.09, realtime=True)            # 3 frames
        start = time.monotonic()
        self.assertEqual(len(list(mic.frames())), 3)
        self.assertGreaterEqual(time.monotonic() - start, 0.08)


@unittest.skipIf(np is None, "numpy is not installed")
class StreamResamplerTests(unittest.TestCase):
    def test_chunked_resampling_keeps_pitch_and_length(self):
        from body.audio import _StreamResampler
        for rate in (44100, 48000):
            rs = _StreamResampler(rate, 2)
            mono = sine(700, 2.0, rate)
            stereo = np.stack([mono, mono], axis=1)
            out = b"".join(rs.process(stereo[i:i + 1024].tobytes()) for i in range(0, len(stereo), 1024))
            pcm = np.frombuffer(out, dtype=np.int16)
            self.assertLess(abs(len(pcm) - 32000), 40)
            spectrum = np.abs(np.fft.rfft(pcm[:32000].astype(float)))
            self.assertAlmostEqual(np.argmax(spectrum) * 16000 / 32000, 700, delta=3)


@unittest.skipIf(np is None, "numpy is not installed")
class FakeAudioOutTests(unittest.TestCase):
    def setUp(self):
        from body import audio
        self.audio = audio
        self.wav = make_wav(sine(300, 0.5, 22050, amp=6000), 22050)

    def test_records_and_reports_levels(self):
        out = self.audio.FakeAudioOut()
        levels = []
        start = time.monotonic()
        self.assertTrue(out.play(self.wav, levels.append))
        self.assertLess(time.monotonic() - start, 0.2)
        self.assertEqual(out.played, [self.wav])
        self.assertGreaterEqual(len(levels), 3)
        self.assertEqual(levels[-1], 0.0)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in levels))
        self.assertTrue(any(v > 0.2 for v in levels))

    def test_realtime_and_stop(self):
        out = self.audio.FakeAudioOut(realtime=True)
        result = []
        t = threading.Thread(target=lambda: result.append(out.play(make_wav(sine(300, 3.0, 16000)))))
        t.start()
        time.sleep(0.15)
        stop_at = time.monotonic()
        out.stop()
        t.join(2)
        self.assertEqual(result, [False])
        self.assertLess(time.monotonic() - stop_at, 0.2)

    def test_realtime_levels_about_30_per_second(self):
        out = self.audio.FakeAudioOut(realtime=True)
        levels = []
        self.assertTrue(out.play(make_wav(sine(300, 0.3, 16000)), levels.append))
        self.assertEqual(len(levels), 10 + 1)                 # ten 30 ms steps and the final zero

    def test_bad_wav_is_refused(self):
        with self.assertRaises(self.audio.AudioError):
            self.audio.FakeAudioOut().play(b"junk")


@unittest.skipIf(np is None, "numpy is not installed")
class LevelTests(unittest.TestCase):
    def test_voice_like_sound_is_mid_range_and_silence_is_zero(self):
        from body.audio import _Level
        lv = _Level()
        voice = sine(200, 0.03, 16000, amp=5000).astype(np.float32)        # rms about 3500, a normal voice
        for _ in range(5):
            value = lv.update(voice)
        self.assertTrue(0.3 <= value <= 0.9, value)
        for _ in range(30):
            value = lv.update(np.zeros(480, dtype=np.float32))
        self.assertLess(value, 0.01)


@unittest.skipIf(np is None, "numpy is not installed")
class WavInfoTests(unittest.TestCase):
    def test_wav_info(self):
        from body.audio import AudioError, wav_info
        self.assertEqual(wav_info(make_wav(np.zeros(1000, dtype=np.int16), 22050)), (22050, 1, 1000))
        self.assertEqual(wav_info(make_wav(np.zeros(2000, dtype=np.int16), 44100, 2)), (44100, 2, 1000))
        with self.assertRaises(AudioError):
            wav_info(b"RIFFjunk")


@unittest.skipIf(np is None, "numpy is not installed")
class NoSounddeviceTests(unittest.TestCase):
    def test_real_audio_explains_what_is_missing(self):
        from body.audio import AudioError, AudioIn, AudioOut, list_devices
        with mock.patch.dict(sys.modules, {"sounddevice": None}):          # makes "import sounddevice" fail
            for make in (AudioIn, AudioOut):
                with self.assertRaises(AudioError) as ctx:
                    make()
                self.assertIn("sounddevice", str(ctx.exception))
                self.assertIn("pip install", str(ctx.exception))
            self.assertIn("sounddevice", list_devices())


class FakeStream:
    """Stands in for a sounddevice stream."""
    latency = 0.06

    def __init__(self, owner, kind, **kw):
        if kw.get("samplerate") in owner.refuse_rates:
            raise RuntimeError("Invalid sample rate")
        self.kw, self.owner, self.kind = kw, owner, kind
        self.written = bytearray()
        self.aborted = False
        owner.streams.append(self)

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass

    def abort(self):
        self.aborted = True

    def write(self, data):
        self.written += data
        time.sleep(0.03)


class FakeSounddevice:
    def __init__(self, refuse_rates=()):
        self.refuse_rates = refuse_rates
        self.streams = []
        self.default = types.SimpleNamespace(device=(0, 1))

    def query_devices(self, device=None, kind=None):
        devices = [{"name": "ReSpeaker Mic Array", "max_input_channels": 4, "max_output_channels": 2, "default_samplerate": 48000.0},
                   {"name": "Built-in Speaker", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 44100.0}]
        if device is None:
            return devices if kind is None else devices[0 if kind == "input" else 1]
        return devices[device]

    def RawInputStream(self, **kw):
        return FakeStream(self, "in", **kw)

    def RawOutputStream(self, **kw):
        return FakeStream(self, "out", **kw)


@unittest.skipIf(np is None, "numpy is not installed")
class WithPretendSounddeviceTests(unittest.TestCase):
    def test_microphone_falls_back_to_native_rate_and_resamples(self):
        from body.audio import AudioIn
        sd = FakeSounddevice(refuse_rates=(16000,))
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            mic = AudioIn("respeaker")                       # found by part of its name, any case
            self.assertEqual(mic.rate, 48000)
            stream = sd.streams[-1]
            self.assertEqual(stream.kw["device"], 0)
            frames = mic.frames()
            tone = sine(500, 0.3, 48000)
            threading.Timer(0.1, lambda: [stream.kw["callback"](tone[i:i + 960].tobytes(), 960, None, None)
                                          for i in range(0, len(tone), 960)]).start()
            got = [next(frames) for _ in range(8)]
            self.assertTrue(all(len(f) == 960 for f in got))
            mic.close()

    def test_microphone_unknown_name_lists_devices(self):
        from body.audio import AudioError, AudioIn
        with mock.patch.dict(sys.modules, {"sounddevice": FakeSounddevice()}):
            with self.assertRaises(AudioError) as ctx:
                AudioIn("nothing like this")
            self.assertIn("ReSpeaker", str(ctx.exception))

    def test_speaker_plays_resampled_and_stops_fast(self):
        from body.audio import AudioOut
        sd = FakeSounddevice()
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            out = AudioOut("built-in")
            levels = []
            self.assertTrue(out.play(make_wav(sine(300, 0.3, 22050, amp=6000), 22050), levels.append))
            written = np.frombuffer(bytes(sd.streams[-1].written), dtype=np.int16)
            self.assertGreater(len(written), int(0.3 * 44100))              # resampled up to the device's 44.1 kHz
            self.assertEqual(levels[-1], 0.0)
            self.assertGreaterEqual(len(levels), 9)

            result = []
            t = threading.Thread(target=lambda: result.append(out.play(make_wav(sine(300, 5.0, 16000)))))
            t.start()
            time.sleep(0.2)
            stopped = time.monotonic()
            out.stop()
            t.join(2)
            self.assertEqual(result, [False])
            self.assertLess(time.monotonic() - stopped, 0.15)
            self.assertTrue(sd.streams[-1].aborted)

    def test_list_devices(self):
        from body.audio import list_devices
        with mock.patch.dict(sys.modules, {"sounddevice": FakeSounddevice()}):
            text = list_devices()
        self.assertIn("ReSpeaker Mic Array", text)
        self.assertIn("speaker (default)", text)


if __name__ == "__main__":
    unittest.main()
