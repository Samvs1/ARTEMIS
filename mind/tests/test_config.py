import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mind.config import Settings, load_env_file, parse_env_text


class EnvFileTests(unittest.TestCase):
    def test_parse(self):
        text = """
# a comment
DEEPSEEK_API_KEY=abc123
export FISH_AUDIO_API_KEY="quoted value"
EMPTY=
SINGLE='single'
WITH_COMMENT=value # trailing
not a setting
"""
        values = parse_env_text(text)
        self.assertEqual(values["DEEPSEEK_API_KEY"], "abc123")
        self.assertEqual(values["FISH_AUDIO_API_KEY"], "quoted value")
        self.assertEqual(values["EMPTY"], "")
        self.assertEqual(values["SINGLE"], "single")
        self.assertEqual(values["WITH_COMMENT"], "value")
        self.assertNotIn("not a setting", values)

    def test_real_environment_wins_and_empty_values_are_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("TEST_ONE=from_file\nTEST_TWO=from_file\nTEST_THREE=\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"TEST_ONE": "from_environment"}, clear=False):
                os.environ.pop("TEST_TWO", None)
                os.environ.pop("TEST_THREE", None)
                names = load_env_file(path)
                self.assertEqual(os.environ["TEST_ONE"], "from_environment")
                self.assertEqual(os.environ["TEST_TWO"], "from_file")
                self.assertNotIn("TEST_THREE", os.environ)
                self.assertEqual(names, ["TEST_TWO"])
                os.environ.pop("TEST_TWO", None)

    def test_missing_file_is_fine(self):
        self.assertEqual(load_env_file(Path("/nonexistent/.env")), [])


class SettingsTests(unittest.TestCase):
    def test_defaults_and_overrides(self):
        env = {"DEEPSEEK_API_KEY": " key ", "DEEPSEEK_BASE_URL": "https://example.test/", "MILO_PORT": "9001", "FISH_AUDIO_LATENCY": "normal"}
        with mock.patch.dict(os.environ, env, clear=True):
            s = Settings.from_env()
        self.assertEqual(s.deepseek_key, "key")
        self.assertEqual(s.deepseek_base, "https://example.test")
        self.assertEqual(s.port, 9001)
        self.assertEqual(s.fish_latency, "normal")
        self.assertTrue(s.use_deepseek)
        self.assertFalse(s.use_fish)

    def test_mock_ignores_keys(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "k", "FISH_AUDIO_API_KEY": "k"}, clear=True):
            s = Settings.from_env()
        s.mock = True
        self.assertFalse(s.use_deepseek)
        self.assertFalse(s.use_fish)

    def test_bad_port_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {"MILO_PORT": "not a number"}, clear=True):
            self.assertEqual(Settings.from_env().port, 8000)


if __name__ == "__main__":
    unittest.main()
