"""Tests for the body settings."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from body.config import BodySettings, load_settings


def quietly(env):
    """load_settings(env) with the warnings captured, so they can be checked and the test output stays clean."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        settings = load_settings(env)
    return settings, out.getvalue()


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        settings, warnings = quietly({})
        self.assertEqual(settings, BodySettings())
        self.assertEqual(warnings, "")
        self.assertEqual(settings.mind_url, "http://127.0.0.1:8000")
        self.assertEqual(settings.stt_order, ("openai", "local"))
        self.assertEqual(settings.wake_model, "hey_jarvis")
        self.assertFalse(settings.barge_in)

    def test_every_value_can_be_set(self):
        settings, warnings = quietly({
            "MILO_MIND_URL": "http://pi.local:8000/", "MILO_FACE_HOST": "0.0.0.0", "MILO_FACE_PORT": "9001",
            "MILO_STT_ORDER": " Local , openai ", "OPENAI_API_KEY": " sk-test ", "MILO_OPENAI_STT_MODEL": "whisper-1",
            "MILO_LOCAL_STT_MODEL": "base.en", "MILO_WAKE_MODEL": "/models/hey_milo.onnx", "MILO_WAKE_THRESHOLD": "0.7",
            "MILO_INPUT_DEVICE": "ReSpeaker", "MILO_OUTPUT_DEVICE": "speaker", "MILO_BARGE_IN": "yes",
            "MILO_WINDOW_SECONDS": "4.5"})
        self.assertEqual(warnings, "")
        self.assertEqual(settings.mind_url, "http://pi.local:8000")          # no slash at the end
        self.assertEqual((settings.face_host, settings.face_port), ("0.0.0.0", 9001))
        self.assertEqual(settings.stt_order, ("local", "openai"))
        self.assertEqual((settings.openai_api_key, settings.openai_stt_model), ("sk-test", "whisper-1"))
        self.assertEqual(settings.local_stt_model, "base.en")
        self.assertEqual((settings.wake_model, settings.wake_threshold), ("/models/hey_milo.onnx", 0.7))
        self.assertEqual((settings.input_device, settings.output_device), ("ReSpeaker", "speaker"))
        self.assertTrue(settings.barge_in)
        self.assertEqual(settings.window_seconds, 4.5)

    def test_bad_numbers_fall_back_to_the_default_with_a_plain_warning(self):
        settings, warnings = quietly({"MILO_FACE_PORT": "eighty", "MILO_WAKE_THRESHOLD": "loud", "MILO_WINDOW_SECONDS": "-3",
                                      "MILO_BARGE_IN": "maybe"})
        defaults = BodySettings()
        self.assertEqual(settings.face_port, defaults.face_port)
        self.assertEqual(settings.wake_threshold, defaults.wake_threshold)
        self.assertEqual(settings.window_seconds, defaults.window_seconds)
        self.assertEqual(settings.barge_in, defaults.barge_in)
        for name in ("MILO_FACE_PORT", "MILO_WAKE_THRESHOLD", "MILO_WINDOW_SECONDS", "MILO_BARGE_IN"):
            self.assertIn(name, warnings)
        self.assertEqual(quietly({"MILO_WAKE_THRESHOLD": "nan"})[0].wake_threshold, defaults.wake_threshold)
        self.assertEqual(quietly({"MILO_FACE_PORT": "70000"})[0].face_port, defaults.face_port)

    def test_an_empty_wake_model_means_push_to_talk(self):
        self.assertEqual(quietly({"MILO_WAKE_MODEL": ""})[0].wake_model, "")
        self.assertEqual(quietly({"MILO_WAKE_MODEL": "none"})[0].wake_model, "")

    def test_an_empty_stt_order_uses_the_default(self):
        self.assertEqual(quietly({"MILO_STT_ORDER": " , "})[0].stt_order, ("openai", "local"))

    def test_the_env_file_is_read_and_real_variables_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("OPENAI_API_KEY=from-file\nMILO_FACE_PORT=9100\nMILO_MIND_URL=http://file:8000\n", encoding="utf-8")
            from mind.config import load_env_file
            real = {"MILO_MIND_URL": "http://real:8000"}
            with mock.patch.dict(os.environ, real, clear=True), \
                    mock.patch("body.config.load_env_file", lambda: load_env_file(path)):
                settings, _ = quietly(None)
        self.assertEqual(settings.openai_api_key, "from-file")
        self.assertEqual(settings.face_port, 9100)
        self.assertEqual(settings.mind_url, "http://real:8000")


if __name__ == "__main__":
    unittest.main()
