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
            "ARTEMIS_MIND_URL": "http://pi.local:8000/", "ARTEMIS_FACE_HOST": "0.0.0.0", "ARTEMIS_FACE_PORT": "9001",
            "ARTEMIS_STT_ORDER": " Local , openai ", "OPENAI_API_KEY": " sk-test ", "ARTEMIS_OPENAI_STT_MODEL": "whisper-1",
            "ARTEMIS_LOCAL_STT_MODEL": "base.en", "ARTEMIS_WAKE_MODEL": "/models/hey_arty.onnx", "ARTEMIS_WAKE_THRESHOLD": "0.7",
            "ARTEMIS_INPUT_DEVICE": "ReSpeaker", "ARTEMIS_OUTPUT_DEVICE": "speaker", "ARTEMIS_BARGE_IN": "yes",
            "ARTEMIS_WINDOW_SECONDS": "4.5"})
        self.assertEqual(warnings, "")
        self.assertEqual(settings.mind_url, "http://pi.local:8000")          # no slash at the end
        self.assertEqual((settings.face_host, settings.face_port), ("0.0.0.0", 9001))
        self.assertEqual(settings.stt_order, ("local", "openai"))
        self.assertEqual((settings.openai_api_key, settings.openai_stt_model), ("sk-test", "whisper-1"))
        self.assertEqual(settings.local_stt_model, "base.en")
        self.assertEqual((settings.wake_model, settings.wake_threshold), ("/models/hey_arty.onnx", 0.7))
        self.assertEqual((settings.input_device, settings.output_device), ("ReSpeaker", "speaker"))
        self.assertTrue(settings.barge_in)
        self.assertEqual(settings.window_seconds, 4.5)

    def test_bad_numbers_fall_back_to_the_default_with_a_plain_warning(self):
        settings, warnings = quietly({"ARTEMIS_FACE_PORT": "eighty", "ARTEMIS_WAKE_THRESHOLD": "loud", "ARTEMIS_WINDOW_SECONDS": "-3",
                                      "ARTEMIS_BARGE_IN": "maybe"})
        defaults = BodySettings()
        self.assertEqual(settings.face_port, defaults.face_port)
        self.assertEqual(settings.wake_threshold, defaults.wake_threshold)
        self.assertEqual(settings.window_seconds, defaults.window_seconds)
        self.assertEqual(settings.barge_in, defaults.barge_in)
        for name in ("ARTEMIS_FACE_PORT", "ARTEMIS_WAKE_THRESHOLD", "ARTEMIS_WINDOW_SECONDS", "ARTEMIS_BARGE_IN"):
            self.assertIn(name, warnings)
        self.assertEqual(quietly({"ARTEMIS_WAKE_THRESHOLD": "nan"})[0].wake_threshold, defaults.wake_threshold)
        self.assertEqual(quietly({"ARTEMIS_FACE_PORT": "70000"})[0].face_port, defaults.face_port)

    def test_an_empty_wake_model_means_push_to_talk(self):
        self.assertEqual(quietly({"ARTEMIS_WAKE_MODEL": ""})[0].wake_model, "")
        self.assertEqual(quietly({"ARTEMIS_WAKE_MODEL": "none"})[0].wake_model, "")

    def test_an_empty_stt_order_uses_the_default(self):
        self.assertEqual(quietly({"ARTEMIS_STT_ORDER": " , "})[0].stt_order, ("openai", "local"))

    def test_the_env_file_is_read_and_real_variables_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("OPENAI_API_KEY=from-file\nARTEMIS_FACE_PORT=9100\nARTEMIS_MIND_URL=http://file:8000\n", encoding="utf-8")
            from mind.config import load_env_file
            real = {"ARTEMIS_MIND_URL": "http://real:8000"}
            with mock.patch.dict(os.environ, real, clear=True), \
                    mock.patch("body.config.load_env_file", lambda: load_env_file(path)):
                settings, _ = quietly(None)
        self.assertEqual(settings.openai_api_key, "from-file")
        self.assertEqual(settings.face_port, 9100)
        self.assertEqual(settings.mind_url, "http://real:8000")



class OldNameTests(unittest.TestCase):
    def test_body_settings_under_the_old_name_still_work(self):
        settings = load_settings({"MILO_FACE_PORT": "8123", "MILO_WAKE_MODEL": "push"})
        self.assertEqual(settings.face_port, 8123)
        self.assertEqual(settings.wake_model, "")


if __name__ == "__main__":
    unittest.main()
