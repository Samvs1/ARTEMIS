"""Tests for body/wake.py: the wake word without the package, with a pretend model, and push to talk."""
import sys
import types
import unittest
from unittest import mock

try:
    import numpy as np
except ImportError:          # pragma: no cover
    np = None

FRAME = bytes(960)


@unittest.skipIf(np is None, "numpy is not installed")
class WakeWordTests(unittest.TestCase):
    def test_unavailable_without_package(self):
        from body.wake import WakeWord
        with mock.patch.dict(sys.modules, {"openwakeword": None}):
            w = WakeWord("hey_jarvis")
        self.assertFalse(w.available)
        self.assertIn("openwakeword", w.reason)
        self.assertFalse(w.feed(FRAME))
        w.reset()

    def test_unavailable_without_a_model_name(self):
        from body.wake import WakeWord
        w = WakeWord("")
        self.assertFalse(w.available)
        self.assertFalse(w.feed(FRAME))

    def fake_package(self, scores, fail_first=False, with_download=True, always_fail=False):
        """A pretend openwakeword whose model answers with the given scores, one per 1280-sample chunk."""
        calls = {"predict": [], "download": 0, "made": 0}
        pkg = types.ModuleType("openwakeword")

        class Model:
            def __init__(self, wakeword_models=None, inference_framework="onnx"):
                calls["made"] += 1
                if always_fail or (fail_first and calls["made"] == 1):
                    raise FileNotFoundError("model file missing")
                self.scores = list(scores)

            def predict(self, chunk):
                calls["predict"].append(len(chunk))
                return {"hey_jarvis": self.scores.pop(0) if self.scores else 0.0}

            def reset(self):
                pass

        pkg.Model = Model
        if with_download:
            pkg.utils = types.ModuleType("openwakeword.utils")
            def download_models(*a, **k):
                calls["download"] += 1
            pkg.utils.download_models = download_models
        return pkg, calls

    def test_buffers_frames_into_1280_sample_chunks_and_fires_once(self):
        from body.wake import WakeWord
        pkg, calls = self.fake_package([0.0, 0.9, 0.9])
        with mock.patch.dict(sys.modules, {"openwakeword": pkg}):
            w = WakeWord("hey_jarvis", threshold=0.5)
        self.assertTrue(w.available)
        hits = [w.feed(FRAME) for _ in range(12)]            # 12 * 480 = 5760 samples = 4 chunks of 1280 (+ 640 left)
        self.assertEqual(calls["predict"], [1280] * 4)
        self.assertEqual(sum(hits), 1)                        # the second 0.9 is inside the cool-down
        w.reset()

    def test_downloads_missing_models_once_then_retries(self):
        from body.wake import WakeWord
        pkg, calls = self.fake_package([], fail_first=True)
        with mock.patch.dict(sys.modules, {"openwakeword": pkg}):
            w = WakeWord("hey_jarvis")
        self.assertTrue(w.available)
        self.assertEqual(calls["download"], 1)

    def test_load_failure_does_not_crash(self):
        from body.wake import WakeWord
        pkg, calls = self.fake_package([], always_fail=True, with_download=False)
        with mock.patch.dict(sys.modules, {"openwakeword": pkg}):
            w = WakeWord("hey_jarvis")
            self.assertFalse(w.available)
            self.assertIn("hey_jarvis", w.reason)
            bad_file = WakeWord("/nowhere/hey_milo.onnx")
        self.assertFalse(bad_file.available)
        self.assertFalse(w.feed(FRAME))


class PushToTalkTests(unittest.TestCase):
    def test_trigger_fires_once(self):
        from body.wake import PushToTalk
        p = PushToTalk(use_stdin=False)
        self.assertFalse(p.feed(FRAME))
        p.trigger()
        self.assertTrue(p.feed(FRAME))
        self.assertFalse(p.feed(FRAME))
        p.trigger()
        p.trigger()
        self.assertTrue(p.feed(FRAME))
        self.assertFalse(p.feed(FRAME))

    def test_reset_clears_a_pending_trigger(self):
        from body.wake import PushToTalk
        p = PushToTalk(use_stdin=False)
        p.trigger()
        p.reset()
        self.assertFalse(p.feed(FRAME))

    def test_a_line_on_stdin_triggers(self):
        import io
        import time
        from body.wake import PushToTalk
        with mock.patch.object(sys, "stdin", io.StringIO("\n")):
            p = PushToTalk(use_stdin=True)
            for _ in range(50):
                if p.feed(FRAME):
                    break
                time.sleep(0.02)
            else:
                self.fail("pressing Enter did not trigger")


if __name__ == "__main__":
    unittest.main()
