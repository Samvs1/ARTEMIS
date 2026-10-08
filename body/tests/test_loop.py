"""The whole conversation loop, end to end: recorded audio in, the REAL mind server (mock mode),
a fake transcriber, a fake speaker and a fake face. No microphone, speaker or keys needed."""
import contextlib
import io
import tempfile
import threading
import unittest
import wave
from pathlib import Path

try:
    import numpy as np
except ImportError:                     # the audio parts need numpy
    np = None

from mind.config import Settings
from mind.server import App, Handler
from http.server import ThreadingHTTPServer


def speech_like_wav(seconds: float = 1.2, rate: int = 16000, seed: int = 0) -> bytes:
    """Loud, voice-like noise: enough for the voice detector, no words needed (the transcriber is fake)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * rate)) / rate
    sig = 0.4 * np.sin(2 * np.pi * 180 * t) * (1 + 0.5 * np.sin(2 * np.pi * 4 * t)) + 0.15 * rng.standard_normal(t.size)
    pcm = (np.clip(sig, -1, 1) * 20000).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class FakeFace:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def send(self, event):
        with self.lock:
            self.events.append(event)

    def states(self):
        with self.lock:
            return [e["name"] for e in self.events if e["type"] == "state"]


@unittest.skipIf(np is None, "numpy is not installed")
class LoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.app = App(Settings(mock=True), data_dir=Path(cls.tmp.name))
        cls.server.daemon_threads = True
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.quiet = contextlib.redirect_stdout(io.StringIO())
        cls.quiet.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.quiet.__exit__(None, None, None)
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def run_conversation(self, recordings, texts, **overrides):
        from body.__main__ import AutoWake, PacedMic
        from body.audio import FakeAudioIn, FakeAudioOut
        from body.config import load_settings
        from body.loop import Conversation
        from body.mind_client import MindClient
        from body.stt import FakeTranscriber, TranscriberChain

        settings = load_settings({"MILO_MIND_URL": self.url, **overrides})
        ref = []
        mic = PacedMic(FakeAudioIn(recordings, gap_seconds=1.0), ref)
        speaker, face = FakeAudioOut(), FakeFace()
        stt = FakeTranscriber(texts)
        convo = Conversation(settings, mic, speaker, AutoWake(), TranscriberChain([stt]), MindClient(self.url), face)
        ref.append(convo)
        done = threading.Thread(target=convo.run, daemon=True)
        done.start()
        done.join(timeout=30)
        self.assertFalse(done.is_alive(), "the conversation did not finish")
        convo.close()
        return convo, speaker, face, stt

    def test_one_exchange_goes_through_every_state(self):
        convo, speaker, face, stt = self.run_conversation([speech_like_wav()], ["hello there"])
        self.assertEqual(convo.turns, 1)
        self.assertEqual(len(stt.calls), 1)
        states = face.states()
        for a, b in (("listening", "thinking"), ("thinking", "speaking"), ("speaking", "listening")):
            self.assertIn(b, states[states.index(a) + 1:], f"{a} should be followed by {b}: {states}")
        self.assertTrue(any(e["type"] == "emote" for e in face.events))
        levels = [e["level"] for e in face.events if e["type"] == "mouth"]
        self.assertTrue(levels and levels[-1] == 0.0, "the mouth should move and then close")
        self.assertGreaterEqual(len(speaker.played), 3)     # listen chirp, thinking chirp, at least one sentence

    def test_each_spoken_turn_logs_where_the_time_went(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_conversation([speech_like_wav(seed=3)], ["hello there"])
        lines = [l for l in out.getvalue().splitlines() if "end of speech -> first voice" in l]
        self.assertEqual(len(lines), 1, out.getvalue())
        for part in ("silence wait 700", "speech to text", "mind first sentence", "voice"):
            self.assertIn(part, lines[0])

    def test_push_to_talk_says_press_enter_not_the_wake_word(self):
        from body.__main__ import PacedMic
        from body.audio import FakeAudioIn, FakeAudioOut
        from body.config import load_settings
        from body.loop import Conversation
        from body.mind_client import MindClient
        from body.stt import FakeTranscriber, TranscriberChain
        from body.wake import PushToTalk

        settings = load_settings({"MILO_MIND_URL": self.url})
        convo = Conversation(settings, PacedMic(FakeAudioIn([], gap_seconds=0), []), FakeAudioOut(),
                             PushToTalk(use_stdin=False), TranscriberChain([FakeTranscriber([])]), MindClient(self.url), FakeFace())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            convo.begin()
        convo.close()
        self.assertIn("Press Enter to talk.", out.getvalue())
        self.assertNotIn("wake word", out.getvalue())

    def test_two_recordings_are_two_turns_in_one_conversation(self):
        convo, _, _, stt = self.run_conversation([speech_like_wav(seed=1), speech_like_wav(seed=2)], ["hi", "tell me a joke"])
        self.assertEqual(convo.turns, 2)
        self.assertEqual(len(stt.calls), 2)

    def test_nothing_understood_means_no_reply(self):
        convo, _, face, _ = self.run_conversation([speech_like_wav()], [""])
        self.assertEqual(convo.turns, 0)
        self.assertNotIn("speaking", face.states())

    def test_mind_down_shows_offline_and_does_not_hang(self):
        from body.__main__ import AutoWake, PacedMic
        from body.audio import FakeAudioIn, FakeAudioOut
        from body.config import load_settings
        from body.loop import Conversation
        from body.mind_client import MindClient
        from body.stt import FakeTranscriber, TranscriberChain

        settings = load_settings({"MILO_MIND_URL": "http://127.0.0.1:1"})
        ref = []
        face = FakeFace()
        convo = Conversation(settings, PacedMic(FakeAudioIn([speech_like_wav()]), ref), FakeAudioOut(), AutoWake(),
                             TranscriberChain([FakeTranscriber(["hello"])]), MindClient("http://127.0.0.1:1"), face)
        ref.append(convo)
        t = threading.Thread(target=convo.run, daemon=True)
        t.start()
        t.join(timeout=30)
        self.assertFalse(t.is_alive())
        self.assertIn("offline", face.states())
        self.assertEqual(convo.turns, 0)


if __name__ == "__main__":
    unittest.main()
