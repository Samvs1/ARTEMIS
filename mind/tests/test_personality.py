import json
import tempfile
import unittest
from pathlib import Path

from mind.brain import DemoBrain, Mind, review_reply, DirectiveStream
from mind.personality import MAX_CHARS, MAX_VERSIONS, Personality, PersonalityError

DEFAULT = "You are Milo. This is the shipped default personality, long enough to be valid."
MINE = "You are Milo, and this is my own version of the personality, a bit shorter and sillier."


class PersonalityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.default = self.dir / "character.md"
        self.default.write_text(DEFAULT, encoding="utf-8")
        self.p = Personality(self.default, self.dir / "data" / "character.md", self.dir / "data" / "history.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_uses_the_default_until_you_save(self):
        self.assertEqual(self.p.text(), DEFAULT)
        self.assertEqual(self.p.source(), "default")
        self.assertEqual(self.p.describe()["versions"], [])

    def test_save_takes_effect_immediately_and_keeps_a_history(self):
        info = self.p.save(MINE, "sillier")
        self.assertEqual(self.p.text().strip(), MINE)
        self.assertEqual(info["source"], "yours")
        self.assertEqual(info["note"], "sillier")
        self.assertEqual([v["note"] for v in info["versions"]], ["sillier"])
        self.assertEqual(self.default.read_text(encoding="utf-8"), DEFAULT)         # the shipped file is never touched

    def test_versions_are_listed_newest_first_and_can_be_restored(self):
        self.p.save(MINE + " one", "one")
        self.p.save(MINE + " two", "two")
        versions = self.p.describe()["versions"]
        self.assertEqual([v["note"] for v in versions], ["two", "one"])
        info = self.p.restore(versions[1]["id"])
        self.assertTrue(self.p.text().strip().endswith("one"))
        self.assertEqual(info["versions"][0]["note"], f"Went back to version {versions[1]['id']}")
        with self.assertRaises(PersonalityError):
            self.p.restore(999)

    def test_reset_goes_back_to_the_default_but_keeps_your_versions(self):
        self.p.save(MINE, "mine")
        info = self.p.reset()
        self.assertEqual(info["source"], "default")
        self.assertEqual(self.p.text(), DEFAULT)
        self.assertFalse((self.dir / "data" / "character.md").exists())
        self.assertEqual(len(info["versions"]), 2)
        restored = self.p.restore(info["versions"][1]["id"])                          # my version is still there
        self.assertEqual(restored["source"], "yours")

    def test_bad_text_is_refused_with_a_reason(self):
        for bad, words in (("", "empty"), ("   ", "empty"), ("short", "short"), ("x" * (MAX_CHARS + 1), "too long")):
            with self.assertRaises(PersonalityError) as ctx:
                self.p.save(bad)
            self.assertIn(words, str(ctx.exception))
        self.assertEqual(self.p.source(), "default")

    def test_windows_line_endings_are_tidied(self):
        self.p.save(MINE + "\r\nsecond line\r\n")
        self.assertNotIn("\r", self.p.text())

    def test_history_is_capped(self):
        for i in range(MAX_VERSIONS + 5):
            self.p.save(MINE + f" {i}")
        versions = self.p.describe()["versions"]
        self.assertEqual(len(versions), MAX_VERSIONS)
        self.assertTrue(self.p.text().strip().endswith(str(MAX_VERSIONS + 4)))

    def test_a_damaged_history_file_does_not_break_saving(self):
        (self.dir / "data").mkdir()
        (self.dir / "data" / "history.json").write_text("{not json", encoding="utf-8")
        info = self.p.save(MINE)
        self.assertEqual(len(info["versions"]), 1)

    def test_an_empty_own_file_falls_back_to_the_default(self):
        (self.dir / "data").mkdir()
        (self.dir / "data" / "character.md").write_text("  \n", encoding="utf-8")
        self.assertEqual(self.p.source(), "default")
        self.assertEqual(self.p.text(), DEFAULT)

    def test_a_missing_default_file_still_gives_milo_something(self):
        p = Personality(self.dir / "nope.md")
        self.assertIn("Milo", p.text())


class MindMemoryTests(unittest.TestCase):
    def test_conversation_survives_a_restart_and_can_be_forgotten(self):
        with tempfile.TemporaryDirectory() as folder:
            history = Path(folder) / "data" / "history.json"
            mind = Mind(DemoBrain(), history_file=history)
            list(mind.chat("hello", "", {}))
            self.assertTrue(history.exists())
            again = Mind(DemoBrain(), history_file=history)
            self.assertEqual(len(again.history), 2)
            self.assertEqual(again.history[0], {"role": "user", "content": "hello"})
            again.reset()
            self.assertFalse(history.exists())
            self.assertEqual(Mind(DemoBrain(), history_file=history).history, [])

    def test_a_damaged_history_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            history = Path(folder) / "history.json"
            history.write_text('[{"role": "system", "content": "sneaky"}, {"role": "user"}, "text"]', encoding="utf-8")
            self.assertEqual(Mind(DemoBrain(), history_file=history).history, [])

    def test_the_personality_is_read_on_every_reply(self):
        with tempfile.TemporaryDirectory() as folder:
            default = Path(folder) / "c.md"
            default.write_text("FIRST VERSION of the personality, which is long enough.", encoding="utf-8")
            seen = []

            class Spy:
                kind, label = "spy", "spy"

                def stream(self, messages):
                    seen.append(messages[0]["content"])
                    yield "[emote:calm] Ok."

            mind = Mind(Spy(), Personality(default))
            list(mind.chat("hi", "", {}))
            default.write_text("SECOND VERSION of the personality, which is long enough.", encoding="utf-8")
            list(mind.chat("hi", "", {}))
        self.assertIn("FIRST VERSION", seen[0])
        self.assertIn("SECOND VERSION", seen[1])


def review(text):
    stream = DirectiveStream()
    events = stream.feed(text) + stream.finish()
    return review_reply(text, events)


class ReviewTests(unittest.TestCase):
    def test_a_good_reply_has_no_warnings(self):
        notes, warnings = review("[emote:happy] Hi there! I was just thinking about you.")
        self.assertEqual(warnings, [])
        self.assertIn("2 sentences", notes[0])

    def test_problems_are_named(self):
        self.assertIn("does not start with an [emote:...]", review("Hello there.")[1])
        self.assertIn("contains markdown, a list or an emoji", review("[emote:happy] **Hi** there!")[1])
        self.assertIn("contains markdown, a list or an emoji", review("[emote:happy] Hi \U0001F60A")[1])
        self.assertIn("more than three sentences", review("[emote:happy] One is here. Two is here. Three is here. Four is here.")[1])
        self.assertIn("unknown stage direction [wiggle]", review("[emote:happy] [wiggle] Hi there friend.")[1])
        self.assertIn("says nothing", review("[emote:happy]")[1])

    def test_long_replies_are_flagged(self):
        self.assertIn("long: over 60 words", review("[emote:calm] " + "word " * 70 + "end.")[1])


if __name__ == "__main__":
    unittest.main()
