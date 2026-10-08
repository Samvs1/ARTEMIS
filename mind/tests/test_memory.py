import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest import mock

from mind import memory
from mind.memory import KINDS, MemoryStore, now_local

TZ = timezone(timedelta(hours=2))


def at(day=8, hour=18, minute=40, second=12, month=10, year=2026):
    return datetime(year, month, day, hour, minute, second, tzinfo=TZ)


class FakeClock:
    def __init__(self, start=None):
        self.now = start or at()

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now = self.now + timedelta(**kwargs)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "memory"
        self.clock = FakeClock()
        self.store = MemoryStore(self.root, "owner", self.clock)
        self.addCleanup(self.tmp.cleanup)

    def read_json(self, name):
        return json.loads((self.root / "owner" / name).read_text(encoding="utf-8"))

    def quietly(self, call, *args, **kwargs):
        out = StringIO()
        with redirect_stdout(out):
            result = call(*args, **kwargs)
        return result, out.getvalue()


class FactTests(StoreCase):
    def test_now_local_has_an_offset(self):
        self.assertIsNotNone(now_local().tzinfo)

    def test_add_fact_stores_everything_the_design_lists(self):
        fact = self.store.add_fact("They drink an oat-milk latte every morning.", kind="routine", importance=4,
                                   source="talk on 2026-10-08 18:31")
        self.assertRegex(fact["id"], r"^f_20261008_[0-9a-f]{4}$")
        self.assertEqual(fact["created"], "2026-10-08T18:40:12+02:00")
        self.assertEqual(fact["updated"], fact["created"])
        self.assertIsNone(fact["last_used"])
        self.assertEqual((fact["status"], fact["replaced_by"], fact["pinned"]), ("active", None, False))
        self.assertEqual((fact["kind"], fact["importance"], fact["source"]), ("routine", 4, "talk on 2026-10-08 18:31"))
        saved = self.read_json("facts.json")
        self.assertEqual(saved["version"], 1)
        self.assertEqual(saved["facts"], [fact])
        self.assertEqual(self.store.get(fact["id"]), fact)
        self.assertIsNone(self.store.get("f_nope"))

    def test_the_file_is_readable_text(self):
        self.store.add_fact("Ils aiment le café, et les crêpes.")
        raw = (self.root / "owner" / "facts.json").read_text(encoding="utf-8")
        self.assertIn("crêpes", raw)                       # ensure_ascii=False
        self.assertIn('\n "facts"', raw)                   # indent=1

    def test_ids_stay_unique_even_when_the_random_part_repeats(self):
        with mock.patch("mind.memory._suffix", return_value="abcd"):
            ids = [self.store.add_fact(f"They like thing number {i}.")["id"] for i in range(4)]
        self.assertEqual(ids, ["f_20261008_abcd", "f_20261008_abcd_2", "f_20261008_abcd_3", "f_20261008_abcd_4"])
        more = [self.store.add_fact(f"They also like item {i}.")["id"] for i in range(60)]
        self.assertEqual(len(set(ids + more)), 64)

    def test_text_is_cleaned_and_capped(self):
        fact = self.store.add_fact("  They   like\n\ttea [emote:happy]  a lot.  ")
        self.assertEqual(fact["text"], "They like tea emote:happy a lot.")
        self.assertNotIn("[", fact["text"] + fact["source"])
        long = self.store.add_fact("x" * 500)
        self.assertEqual(len(long["text"]), 200)
        trimmed = self.store.add_fact(("word " * 100).strip())
        self.assertLessEqual(len(trimmed["text"]), 200)
        self.assertFalse(trimmed["text"].endswith(" "))
        with self.assertRaises(ValueError):
            self.store.add_fact("   [ ]  ")

    def test_kind_and_importance_are_made_safe(self):
        self.assertEqual(self.store.add_fact("a fact one", kind="nonsense")["kind"], "other")
        self.assertEqual(self.store.add_fact("a fact two", importance=99)["importance"], 10)
        self.assertEqual(self.store.add_fact("a fact three", importance=-4)["importance"], 1)
        self.assertEqual(self.store.add_fact("a fact four", importance="x")["importance"], 5)
        self.assertEqual(self.store.add_fact("a fact five", importance="7")["importance"], 7)
        for kind in KINDS:
            self.assertEqual(self.store.add_fact(f"about {kind}", kind=kind)["kind"], kind)

    def test_update_changes_fields_and_the_updated_time(self):
        fact = self.store.add_fact("They live in Bruges.", kind="other", importance=3)
        self.clock.advance(days=1)
        updated = self.store.update_fact(fact["id"], text="They live in Ghent.", kind="identity", importance=7,
                                         pinned=True, source="correction")
        self.assertEqual(updated["text"], "They live in Ghent.")
        self.assertEqual((updated["kind"], updated["importance"], updated["pinned"], updated["source"]),
                         ("identity", 7, True, "correction"))
        self.assertEqual(updated["created"], fact["created"])
        self.assertEqual(updated["updated"], "2026-10-09T18:40:12+02:00")
        self.assertEqual(self.store.get(fact["id"]), updated)
        self.assertIsNone(self.store.update_fact("f_missing", text="whatever"))
        with self.assertRaises(ValueError):
            self.store.update_fact(fact["id"], text="  ")
        self.assertEqual(self.store.get(fact["id"])["text"], "They live in Ghent.")

    def test_update_with_nothing_to_change_leaves_the_time_alone(self):
        fact = self.store.add_fact("They have a cat.")
        self.clock.advance(hours=3)
        again = self.store.update_fact(fact["id"])
        self.assertEqual(again["updated"], fact["updated"])

    def test_outdate_keeps_the_fact_but_hides_it(self):
        old = self.store.add_fact("They live in Bruges.")
        new = self.store.add_fact("They live in Ghent.")
        self.clock.advance(days=2)
        out = self.store.outdate_fact(old["id"], replaced_by=new["id"])
        self.assertEqual((out["status"], out["replaced_by"]), ("outdated", new["id"]))
        self.assertEqual(out["updated"], "2026-10-10T18:40:12+02:00")
        self.assertEqual([f["id"] for f in self.store.facts()], [new["id"]])
        self.assertEqual([f["id"] for f in self.store.facts(include_outdated=True)], [old["id"], new["id"]])
        self.assertEqual(self.store.get(old["id"])["status"], "outdated")
        self.assertIsNone(self.store.outdate_fact("f_missing"))

    def test_outdating_twice_does_not_move_the_date(self):
        fact = self.store.add_fact("They had a cold.")
        self.store.outdate_fact(fact["id"])
        first = self.store.get(fact["id"])["updated"]
        self.clock.advance(days=5)
        self.store.outdate_fact(fact["id"])
        self.assertEqual(self.store.get(fact["id"])["updated"], first)

    def test_delete_is_for_good(self):
        a, b = self.store.add_fact("They like jazz."), self.store.add_fact("They like soup.")
        self.assertTrue(self.store.delete_fact(a["id"]))
        self.assertFalse(self.store.delete_fact(a["id"]))
        self.assertEqual([f["id"] for f in self.store.facts(include_outdated=True)], [b["id"]])
        self.assertNotIn(a["id"], (self.root / "owner" / "facts.json").read_text(encoding="utf-8"))

    def test_pin_and_unpin(self):
        fact = self.store.add_fact("They are called Sam.", pinned=True)
        self.assertTrue(fact["pinned"])
        self.assertFalse(self.store.update_fact(fact["id"], pinned=False)["pinned"])
        self.assertTrue(self.store.update_fact(fact["id"], pinned=True)["pinned"])

    def test_mark_used_sets_last_used_only_for_those_facts(self):
        a, b = self.store.add_fact("They like jazz."), self.store.add_fact("They like soup.")
        self.clock.advance(hours=5)
        self.store.mark_used([a["id"], "f_missing"])
        self.assertEqual(self.store.get(a["id"])["last_used"], "2026-10-08T23:40:12+02:00")
        self.assertEqual(self.store.get(a["id"])["updated"], a["updated"])
        self.assertIsNone(self.store.get(b["id"])["last_used"])
        self.store.mark_used([])                              # nothing to do, nothing breaks

    def test_the_400_cap_retires_the_least_important_least_used_loose_fact(self):
        pinned = self.store.add_fact("They are called Sam.", importance=1, pinned=True)   # lowest importance, but pinned
        ids = []
        for i in range(399):
            self.clock.advance(minutes=1)
            ids.append(self.store.add_fact(f"Fact number {i}.", importance=5)["id"])
        self.assertEqual(len(self.store.facts()), 400)
        # make one clearly the least important, and one the least recently used among equals
        self.store.update_fact(ids[200], importance=2)
        self.store.mark_used(ids[:10])                        # the first ten were used just now, so they are the freshest
        self.clock.advance(minutes=1)
        newest = self.store.add_fact("A brand new fact.", importance=5)
        active = {f["id"] for f in self.store.facts()}
        self.assertEqual(len(active), 400)
        self.assertNotIn(ids[200], active)                    # least important went
        self.assertIn(pinned["id"], active)                   # pinned never goes
        self.assertIn(newest["id"], active)
        self.assertEqual(self.store.get(ids[200])["status"], "outdated")
        # now everything equal in importance: the one not used for longest goes (ids[10] is the oldest unused)
        self.store.update_fact(ids[200], importance=5)        # no effect: it is outdated, stays outdated
        self.clock.advance(minutes=1)
        self.store.add_fact("Another new fact.", importance=5)
        self.assertEqual(self.store.get(ids[10])["status"], "outdated")
        self.assertEqual(self.store.get(ids[0])["status"], "active")
        self.assertEqual(len(self.store.facts()), 400)

    def test_when_everything_is_pinned_nothing_is_dropped(self):
        for i in range(400):
            self.store.add_fact(f"Pinned fact {i}.", pinned=True)
        extra = self.store.add_fact("One more.")
        self.assertEqual(len(self.store.facts()), 401)
        self.assertTrue(all(f["pinned"] for f in self.store.facts() if f["id"] != extra["id"]))


class FileTests(StoreCase):
    def test_missing_files_read_as_empty_without_a_fuss(self):
        (facts, episodes, diary, state), out = self.quietly(
            lambda: (self.store.facts(), self.store.episodes(), self.store.diary(), self.store.state()))
        self.assertEqual((facts, episodes, diary, state), ([], [], [], {}))
        self.assertEqual(out, "")
        self.assertFalse((self.root / "owner").exists())     # reading never creates anything

    def test_a_broken_file_reads_as_empty_logs_once_and_is_kept_aside(self):
        folder = self.root / "owner"
        folder.mkdir(parents=True)
        (folder / "facts.json").write_text('{"version": 1, "facts": [{"id": "f_1", "te', encoding="utf-8")
        facts, out = self.quietly(self.store.facts)
        self.assertEqual(facts, [])
        self.assertIn("facts.json", out)
        self.assertEqual(len(out.strip().splitlines()), 1)
        _, again = self.quietly(self.store.facts)
        self.assertEqual(again, "")                           # once only
        self.quietly(self.store.add_fact, "They like tea.")   # saving over it works...
        self.assertEqual(len(self.store.facts()), 1)
        self.assertTrue((folder / "facts.json.broken").exists())   # ...and the broken text was kept
        self.assertIn('"te', (folder / "facts.json.broken").read_text(encoding="utf-8"))

    def test_other_broken_shapes_are_survived(self):
        folder = self.root / "owner"
        folder.mkdir(parents=True)
        for name, content in (("facts.json", "[1, 2, 3]"), ("episodes.json", ""), ("state.json", "not json at all")):
            (folder / name).write_text(content, encoding="utf-8")
        (_, out) = self.quietly(lambda: self.assertEqual(
            (self.store.facts(), self.store.episodes(), self.store.state()), ([], [], {})))
        self.assertEqual(len(out.strip().splitlines()), 3)
        (folder / "facts.json").write_bytes(b"\xff\xfe\x00 not utf-8 \xc3\x28")
        self.assertEqual(self.quietly(self.store.facts)[0], [])

    def test_bad_entries_are_skipped_and_the_rest_is_kept(self):
        folder = self.root / "owner"
        folder.mkdir(parents=True)
        good = {"id": "f_20261001_aaaa", "text": "They like tea.", "kind": "weird", "importance": 99,
                "created": "2026-10-01T10:00:00+02:00", "status": "active", "pinned": "yes"}
        (folder / "facts.json").write_text(json.dumps({"facts": [
            good, "junk", {"id": "f_x"}, {"text": "no id"}, dict(good), {"id": "f_2", "text": "[Second] one", "status": "outdated"},
        ]}), encoding="utf-8")
        facts, out = self.quietly(self.store.facts, True)
        self.assertEqual([f["id"] for f in facts], ["f_20261001_aaaa", "f_2"])
        self.assertEqual((facts[0]["kind"], facts[0]["importance"], facts[0]["pinned"]), ("other", 10, False))
        self.assertEqual(facts[0]["updated"], "2026-10-01T10:00:00+02:00")
        self.assertEqual(facts[1]["text"], "Second one")
        self.assertEqual(facts[1]["status"], "outdated")
        self.assertIn("could not be understood", out)

    def test_writes_are_atomic_and_leave_valid_json_and_no_temp_files(self):
        for i in range(5):
            self.store.add_fact(f"They like thing {i}.")
        self.store.add_episode("2026-10-08T18:31:00+02:00", "2026-10-08T18:52:00+02:00", 9, "We talked.")
        self.store.set_state(last_dream="2026-10-08")
        folder = self.root / "owner"
        for name in ("facts.json", "episodes.json", "state.json"):
            json.loads((folder / name).read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in folder.iterdir() if p.suffix == ".tmp"], [])

    def test_a_failed_write_keeps_the_old_file_and_says_so_in_plain_words(self):
        self.store.add_fact("They like tea.")
        before = (self.root / "owner" / "facts.json").read_text(encoding="utf-8")
        with mock.patch("mind.memory.os.fsync", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(memory.MemoryStoreError) as caught:
                self.store.add_fact("They like coffee.")
        self.assertIn("No space left", str(caught.exception))
        self.assertEqual((self.root / "owner" / "facts.json").read_text(encoding="utf-8"), before)
        self.assertEqual([p.name for p in (self.root / "owner").iterdir() if p.suffix == ".tmp"], [])

    def test_a_second_store_object_sees_the_same_files(self):
        fact = self.store.add_fact("They like tea.")
        other = MemoryStore(self.root, "owner", self.clock)
        self.assertEqual(other.get(fact["id"]), fact)


class EpisodeTests(StoreCase):
    def test_add_and_read_in_order_newest_last(self):
        a = self.store.add_episode("2026-10-06T09:00:00+02:00", "2026-10-06T09:10:00+02:00", 4, "First talk.", "calm")
        self.clock.advance(hours=1)
        b = self.store.add_episode("2026-10-07T09:00:00+02:00", "2026-10-07T09:10:00+02:00", 6, "Second talk.")
        self.clock.advance(hours=1)
        c = self.store.add_episode("2026-10-08T09:00:00+02:00", "2026-10-08T09:10:00+02:00", 2, "Third talk.")
        self.assertEqual([e["id"] for e in self.store.episodes()], [a["id"], b["id"], c["id"]])
        self.assertEqual([e["summary"] for e in self.store.episodes(limit=2)], ["Second talk.", "Third talk."])
        self.assertEqual(self.store.episodes(limit=0), [])
        self.assertEqual((a["turns"], a["mood"], b["mood"]), (4, "calm", ""))
        self.assertEqual(self.read_json("episodes.json")["version"], 1)

    def test_an_older_talk_added_later_still_sorts_before_newer_ones(self):
        self.store.add_episode("2026-10-08T09:00:00+02:00", "2026-10-08T09:10:00+02:00", 2, "Later talk.")
        self.store.add_episode("2026-10-05T09:00:00+02:00", "2026-10-05T09:10:00+02:00", 2, "Earlier talk.")
        self.assertEqual([e["summary"] for e in self.store.episodes()], ["Earlier talk.", "Later talk."])

    def test_ids_follow_the_design_and_stay_unique(self):
        a = self.store.add_episode("2026-10-08T18:31:00+02:00", "2026-10-08T18:52:00+02:00", 9, "One.")
        b = self.store.add_episode("2026-10-08T18:53:00+02:00", "2026-10-08T18:54:00+02:00", 1, "Two.")
        self.assertEqual(a["id"], "e_20261008_1840")
        self.assertEqual(b["id"], "e_20261008_1840_2")

    def test_summary_is_cleaned_and_capped_at_400(self):
        e = self.store.add_episode("2026-10-08T18:31:00+02:00", "2026-10-08T18:52:00+02:00", 3, "  I  [smiled]\nand listened. " + "z" * 600)
        self.assertEqual(len(e["summary"]), 400)
        self.assertTrue(e["summary"].startswith("I smiled and listened."))
        with self.assertRaises(ValueError):
            self.store.add_episode("a", "b", 1, "  ")


class DiaryAndStateTests(StoreCase):
    def test_diary_is_plain_text_overwrites_and_lists_newest_first(self):
        path = self.store.write_diary("2026-10-08", "Today we talked about tea.\n\nIt was nice.")
        self.assertEqual(path, self.root / "owner" / "diary" / "2026-10-08.md")
        self.assertEqual(path.read_text(encoding="utf-8"), "Today we talked about tea.\n\nIt was nice.\n")
        self.store.write_diary("2026-10-06", "Quiet day.")
        self.store.write_diary("2026-10-07", "Rainy day.")
        self.store.write_diary("2026-10-08", "Today I changed my mind.")
        entries = self.store.diary()
        self.assertEqual([e["date"] for e in entries], ["2026-10-08", "2026-10-07", "2026-10-06"])
        self.assertEqual(entries[0]["text"], "Today I changed my mind.")
        self.assertEqual([e["date"] for e in self.store.diary(limit=2)], ["2026-10-08", "2026-10-07"])
        self.assertEqual(self.store.diary(limit=0), [])

    def test_diary_ignores_stray_files_and_refuses_bad_input(self):
        self.store.write_diary("2026-10-08", "Fine.")
        (self.root / "owner" / "diary" / "notes.md").write_text("not a day", encoding="utf-8")
        (self.root / "owner" / "diary" / "2026-10-09.md.tmp").write_text("half", encoding="utf-8")
        self.assertEqual([e["date"] for e in self.store.diary()], ["2026-10-08"])
        for bad in ("2026-13-45", "yesterday", "../x", "2026-10-8", ""):
            with self.assertRaises(ValueError, msg=bad):
                self.store.write_diary(bad, "text")
        with self.assertRaises(ValueError):
            self.store.write_diary("2026-10-09", "  ")

    def test_state_get_and_set_merge(self):
        self.assertEqual(self.store.state(), {})
        self.store.set_state(noted_until="2026-10-08T18:52:00+02:00")
        self.store.set_state(last_dream="2026-10-08", morning_thought={"for_date": "2026-10-09", "text": "Tea?", "used": False})
        state = self.store.state()
        self.assertEqual(state["noted_until"], "2026-10-08T18:52:00+02:00")
        self.assertEqual(state["last_dream"], "2026-10-08")
        self.assertFalse(state["morning_thought"]["used"])
        self.assertNotIn("version", state)
        self.assertEqual(self.read_json("state.json")["version"], 1)
        self.store.set_state(last_dream="2026-10-09")
        self.assertEqual(self.store.state()["noted_until"], "2026-10-08T18:52:00+02:00")
        self.assertEqual(self.store.state()["last_dream"], "2026-10-09")
        self.store.set_state()                                # nothing to set is fine

    def test_state_accepts_datetimes(self):
        self.store.set_state(noted_until=at(8, 18, 52, 0))
        self.assertEqual(self.store.state()["noted_until"], "2026-10-08T18:52:00+02:00")


class ForgetAndPruneTests(StoreCase):
    def test_forget_everything_deletes_only_this_persons_folder(self):
        self.store.add_fact("They like tea.")
        self.store.add_episode("2026-10-08T18:31:00+02:00", "2026-10-08T18:52:00+02:00", 2, "Talked.")
        self.store.write_diary("2026-10-08", "Dear diary.")
        self.store.set_state(last_dream="2026-10-08")
        guest = MemoryStore(self.root, "guest", self.clock)
        guest_fact = guest.add_fact("They like jam.")
        self.store.forget_everything()
        self.assertFalse((self.root / "owner").exists())
        self.assertEqual((self.store.facts(), self.store.episodes(), self.store.diary(), self.store.state()), ([], [], [], {}))
        self.assertEqual(guest.get(guest_fact["id"]), guest_fact)
        self.assertTrue(self.root.exists())
        self.store.forget_everything()                        # nothing left: still fine
        self.store.add_fact("A new start.")                   # and it can begin again
        self.assertEqual(len(self.store.facts()), 1)

    def test_bad_person_names_are_refused(self):
        for bad in ("", "..", "../etc", "a/b", "a\\b", "Owner", "owner ", "owner\n", ".", "own er", "é", "x" * 65):
            with self.assertRaises(ValueError, msg=repr(bad)):
                MemoryStore(self.root, bad)
        self.assertEqual(MemoryStore(self.root, "house-hold_2").person, "house-hold_2")

    def test_forget_everything_refuses_to_leave_the_root(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("precious", encoding="utf-8")
        self.root.mkdir(parents=True)
        (self.root / "owner").symlink_to(outside, target_is_directory=True)
        store = MemoryStore(self.root, "owner", self.clock)
        with self.assertRaises(ValueError):
            store.forget_everything()
        self.assertTrue((outside / "keep.txt").exists())
        store.dir = self.root                                 # even a mistake inside the class cannot erase the root
        with self.assertRaises(ValueError):
            store.forget_everything()
        self.assertTrue(self.root.exists())

    def test_prune_drops_only_old_outdated_facts(self):
        old_gone = self.store.add_fact("They had a cold.")
        old_active = self.store.add_fact("They like tea.")
        recent_gone = self.store.add_fact("They had a cough.")
        self.store.outdate_fact(old_gone["id"])
        self.clock.advance(days=100)
        self.store.outdate_fact(recent_gone["id"])
        self.clock.advance(days=100)                          # old_gone: 200 days ago, recent_gone: 100 days ago
        self.assertEqual(self.store.prune(), 1)
        ids = {f["id"] for f in self.store.facts(include_outdated=True)}
        self.assertEqual(ids, {old_active["id"], recent_gone["id"]})
        self.assertEqual(self.store.prune(), 0)
        self.assertEqual(self.store.prune(days=30), 1)
        self.assertEqual({f["id"] for f in self.store.facts(include_outdated=True)}, {old_active["id"]})


class WordTests(unittest.TestCase):
    def test_tokens_drop_stop_words_and_trim_suffixes(self):
        self.assertEqual(memory._tokens("They are called Sam, and they like the dogs!"), ["call", "sam", "lik", "dog"])
        self.assertEqual(memory._tokens("Sam's cat"), ["sam", "cat"])
        self.assertEqual(memory._tokens("don't"), [])

    def test_forms_of_a_word_meet(self):
        for group in (("bake", "bakes", "baked", "baking"), ("run", "runs"), ("running", "runned"), ("walk", "walks", "walked", "walking"),
                      ("puppy", "puppies"), ("watch", "watches"), ("dance", "dances", "danced", "dancing"),
                      ("plan", "plans", "planned", "planning"), ("coffee", "coffees"), ("tire", "tired")):
            self.assertEqual(len({memory._stem(w) for w in group}), 1, group)

    def test_short_and_special_words_survive(self):
        for word in ("bus", "sing", "king", "thing", "string", "bed", "red", "gas", "yes", "its", "focus", "class", "kiss", "bring", "need", "sled", "42"):
            self.assertEqual(memory._stem(word), word, word)
        self.assertEqual(memory._tokens("I am 5"), ["5"])

    def test_bm25_prefers_the_document_with_the_query_words(self):
        docs = [memory._tokens(t) for t in ("They drink oat latte", "They play the piano", "They like oat cookies")]
        scores = memory._bm25(docs, memory._tokens("oat latte"))
        self.assertGreater(scores[0], scores[2])
        self.assertGreater(scores[2], scores[1])
        self.assertEqual(scores[1], 0.0)
        self.assertEqual(memory._bm25(docs, []), [0.0, 0.0, 0.0])


class RecallTests(StoreCase):
    def add(self, text, importance=5, days_old=0.0, **kw):
        """Add a fact whose updated time is `days_old` days before the (fixed) now."""
        real = self.clock.now
        self.clock.now = real - timedelta(days=days_old)
        try:
            return self.store.add_fact(text, importance=importance, **kw)
        finally:
            self.clock.now = real

    def ids(self, facts):
        return [f["id"] for f in facts]

    def test_nothing_remembered_means_nothing_recalled(self):
        self.assertEqual(self.store.recall("hello there"), [])

    def test_core_comes_first_highest_first_and_is_limited(self):
        pinned_low = self.add("They keep a spare key under the pot.", importance=2, pinned=True, days_old=30)
        name = self.add("They are called Sam.", importance=10, days_old=30)
        sister = self.add("Their sister is called Anna.", importance=8, days_old=30)
        trivia = self.add("They once ate a pear.", importance=2, days_old=0)
        top = self.store.recall("zzz")
        self.assertEqual(self.ids(top), [pinned_low["id"], name["id"], sister["id"], trivia["id"]])
        limited = self.store.recall("zzz", core_limit=2)
        self.assertEqual(self.ids(limited)[:2], [pinned_low["id"], name["id"]])
        self.assertNotIn(pinned_low["id"], self.ids(self.store.recall("zzz", k=0, core_limit=0)))

    def test_core_facts_cut_by_the_limit_still_compete_as_ordinary_facts(self):
        a = self.add("They are called Sam.", importance=10, days_old=0)
        b = self.add("Their sister is called Anna.", importance=9, days_old=0)
        got = self.store.recall("zzz", core_limit=1)
        self.assertEqual(self.ids(got), [a["id"], b["id"]])

    def test_relevance_beats_an_unrelated_fact(self):
        match = self.add("They are learning to play the cello.", importance=3, days_old=8)
        other = self.add("They once visited a lighthouse.", importance=3, days_old=8)
        got = self.store.recall("how is the cello going?")
        self.assertEqual(self.ids(got), [match["id"]])
        self.assertNotIn(other["id"], self.ids(got))

    def test_a_suffix_variant_still_matches(self):
        match = self.add("They went hiking in the Alps.", importance=3, days_old=8)
        self.add("They once visited a lighthouse.", importance=3, days_old=8)
        self.assertEqual(self.ids(self.store.recall("I love to hike")), [match["id"]])

    def test_recency_matters(self):
        fresh = self.add("They like pears.", importance=5, days_old=0.1)
        stale = self.add("They like plums.", importance=5, days_old=10)
        got = self.ids(self.store.recall("anything at all"))
        self.assertIn(fresh["id"], got)
        self.assertNotIn(stale["id"], got)
        self.clock.advance(days=3)                            # three days later the half-life has passed once
        self.assertIn(fresh["id"], self.ids(self.store.recall("anything at all")))
        self.clock.advance(days=30)
        self.assertEqual(self.store.recall("anything at all"), [])

    def test_using_a_fact_makes_it_feel_recent_again(self):
        fact = self.add("They like plums.", importance=4, days_old=10)
        self.assertEqual(self.store.recall("zzz"), [])
        self.store.mark_used([fact["id"]])
        self.assertEqual(self.ids(self.store.recall("zzz")), [fact["id"]])

    def test_the_half_life_is_three_days(self):
        # importance 3 -> 0.3; fresh gives 1.3, three days gives 0.8 (out), 1.5 days gives about 1.01 (in)
        fresh = self.add("They like pears.", importance=3, days_old=0)
        mid = self.add("They like figs.", importance=3, days_old=1.5)
        old = self.add("They like plums.", importance=3, days_old=3)
        self.assertEqual(self.ids(self.store.recall("zzz")), [fresh["id"], mid["id"]])
        self.assertNotIn(old["id"], self.ids(self.store.recall("zzz")))

    def test_outdated_facts_are_never_recalled(self):
        gone = self.add("They live in Bruges.", importance=10, days_old=0, pinned=True)
        self.store.outdate_fact(gone["id"])
        keep = self.add("They live in Ghent.", importance=5, days_old=0)
        self.assertEqual(self.ids(self.store.recall("where do they live in Bruges")), [keep["id"]])

    def test_score_threshold_is_above_point_nine(self):
        # importance 4 and about five days old: 0.4 + 0.5 ** (120 / 72) = 0.4 + 0.315 = 0.715 -> out
        quiet = self.add("They like figs.", importance=4, days_old=5)
        self.assertEqual(self.store.recall("zzz"), [])
        # the same fact, once the query matches it, gets up to 1 more and comes in
        self.assertEqual(self.ids(self.store.recall("figs")), [quiet["id"]])

    def test_k_limits_the_others_and_best_come_first(self):
        facts = [self.add(f"They like fruit number {i}.", importance=3 + i % 3, days_old=0) for i in range(10)]
        got = self.store.recall("zzz", k=3)
        self.assertEqual(len(got), 3)
        self.assertTrue(all(f["importance"] == 5 for f in got))
        self.assertEqual(len(self.store.recall("zzz", k=50)), 10)

    def test_the_best_match_scores_one_and_others_are_scaled_by_it(self):
        # two facts old enough to be out on their own; the weaker keyword match gets less than the best one
        best = self.add("They adore the harp and the harp festival.", importance=3, days_old=8)
        weaker = self.add("They once heard a harp.", importance=3, days_old=8)
        got = self.ids(self.store.recall("harp festival"))
        self.assertEqual(got[0], best["id"])

    def test_recall_does_not_change_the_store(self):
        self.add("They are called Sam.", importance=10)
        self.add("They like pears.", importance=4)
        folder = self.root / "owner"
        before = (folder / "facts.json").read_bytes()
        stamp = (folder / "facts.json").stat().st_mtime_ns
        for _ in range(3):
            self.store.recall("pears and Sam")
        self.assertEqual((folder / "facts.json").read_bytes(), before)
        self.assertEqual((folder / "facts.json").stat().st_mtime_ns, stamp)
        self.assertTrue(all(f["last_used"] is None for f in self.store.facts()))

    def test_recall_returns_plain_fact_dicts(self):
        self.add("They are called Sam.", importance=10)
        fact = self.store.recall("hi")[0]
        self.assertEqual(set(fact), {"id", "text", "kind", "importance", "created", "updated", "last_used",
                                     "status", "replaced_by", "pinned", "source"})


class RenderTests(StoreCase):
    NOW = datetime(2026, 10, 9, 10, 0, 0, tzinfo=TZ)           # a Friday

    def fact(self, text, created):
        return {"text": text, "created": created, "updated": created}

    def test_matches_the_design_example(self):
        facts = [self.fact('They are called Sam; you call them "Captain Biscuit".', "2026-10-03T12:00:00+02:00"),
                 self.fact("They drink an oat-milk latte every morning.", "2026-10-08T18:40:12+02:00")]
        episodes = [{"start": "2026-10-08T18:31:00+02:00", "end": "2026-10-08T18:52:00+02:00",
                     "summary": "They came home tired from work; I kept them company and we talked about their sister's visit."}]
        expected = (
            "## What you remember about them\n"
            "These are the only things you know about them from before this conversation. The date is when you learned it.\n"
            '- They are called Sam; you call them "Captain Biscuit". (3 October)\n'
            "- They drink an oat-milk latte every morning. (yesterday)\n"
            "\n"
            "## Your last talks\n"
            "- Yesterday evening, 21 minutes: They came home tired from work; I kept them company and we talked about their sister's visit."
        )
        self.assertEqual(self.store.render(facts, episodes, self.NOW), expected)

    def test_empty_gives_nothing_and_empty_sections_are_left_out(self):
        self.assertEqual(self.store.render([], [], self.NOW), "")
        self.assertEqual(self.store.render([], [], None), "")
        only_facts = self.store.render([self.fact("They like tea.", "2026-10-09T08:00:00+02:00")], [], self.NOW)
        self.assertTrue(only_facts.startswith("## What you remember about them\n"))
        self.assertNotIn("Your last talks", only_facts)
        only_talks = self.store.render([], [{"start": "2026-10-09T08:00:00+02:00", "end": "2026-10-09T08:10:00+02:00", "summary": "We chatted."}], self.NOW)
        self.assertEqual(only_talks, "## Your last talks\n- This morning, 10 minutes: We chatted.")
        self.assertEqual(self.store.render([{"text": "  "}], [{"summary": ""}], self.NOW), "")

    def dates_for(self, *created):
        facts = [self.fact("They like tea.", c) for c in created]
        lines = [l for l in self.store.render(facts, [], self.NOW).splitlines() if l.startswith("- ")]
        return [l[l.rindex("(") + 1:-1] for l in lines]

    def test_relative_dates_for_facts(self):
        self.assertEqual(self.dates_for(
            "2026-10-09T08:00:00+02:00",      # today
            "2026-10-08T23:59:00+02:00",      # yesterday
            "2026-10-07T09:00:00+02:00",      # Wednesday
            "2026-10-05T09:00:00+02:00",      # Monday
            "2026-10-04T09:00:00+02:00",      # Sunday (5 days ago)
            "2026-10-03T09:00:00+02:00",      # 6 days ago: a date
            "2026-09-01T09:00:00+02:00",
            "2025-12-24T09:00:00+02:00",      # another year: the year is added
            "2026-01-05T09:00:00+02:00",      # this year: no year
        ), ["today", "yesterday", "Wednesday", "Monday", "Sunday", "3 October", "1 September", "24 December 2025", "5 January"])

    def test_dates_use_the_calendar_day_not_24_hour_blocks(self):
        now = datetime(2026, 10, 9, 0, 30, 0, tzinfo=TZ)
        text = self.store.render([self.fact("They like tea.", "2026-10-08T23:50:00+02:00")], [], now)
        self.assertTrue(text.endswith("(yesterday)"))

    def test_other_time_zones_are_compared_in_the_clocks_zone(self):
        created = datetime(2026, 10, 9, 0, 30, 0, tzinfo=timezone.utc).isoformat()    # 02:30 on the 9th in +02:00
        text = self.store.render([self.fact("They like tea.", created)], [], self.NOW)
        self.assertTrue(text.endswith("(today)"))

    def talk(self, start, end, summary="We talked."):
        return self.store.render([], [{"start": start, "end": end, "summary": summary}], self.NOW).splitlines()[1]

    def test_talk_times_and_minutes(self):
        self.assertEqual(self.talk("2026-10-09T07:30:00+02:00", "2026-10-09T07:45:00+02:00"), "- This morning, 15 minutes: We talked.")
        self.assertEqual(self.talk("2026-10-08T13:00:00+02:00", "2026-10-08T13:02:00+02:00"), "- Yesterday afternoon, a few minutes: We talked.")
        self.assertEqual(self.talk("2026-10-08T18:31:00+02:00", "2026-10-08T18:52:00+02:00"), "- Yesterday evening, 21 minutes: We talked.")
        self.assertEqual(self.talk("2026-10-05T15:00:00+02:00", "2026-10-05T15:03:00+02:00"), "- On Monday afternoon, 3 minutes: We talked.")
        self.assertEqual(self.talk("2026-10-03T15:00:00+02:00", "2026-10-03T15:40:00+02:00"), "- On 3 October, 40 minutes: We talked.")
        self.assertEqual(self.talk("2025-03-03T15:00:00+01:00", "2025-03-03T15:40:00+01:00"), "- On 3 March 2025, 40 minutes: We talked.")
        self.assertEqual(self.talk("2026-10-07T23:15:00+02:00", "2026-10-07T23:20:00+02:00"), "- On Wednesday night, 5 minutes: We talked.")
        self.assertEqual(self.talk("2026-10-09T09:00:00+02:00", "2026-10-09T09:00:00+02:00"), "- This morning, a few minutes: We talked.")

    def test_a_talk_with_no_end_or_no_start_still_renders(self):
        self.assertEqual(self.talk("2026-10-09T07:30:00+02:00", ""), "- This morning: We talked.")
        self.assertEqual(self.talk("", ""), "- We talked.")

    def test_the_order_given_is_kept(self):
        episodes = [{"start": "2026-10-07T09:00:00+02:00", "end": "2026-10-07T09:10:00+02:00", "summary": "First."},
                    {"start": "2026-10-08T09:00:00+02:00", "end": "2026-10-08T09:10:00+02:00", "summary": "Second."}]
        lines = self.store.render([], episodes, self.NOW).splitlines()
        self.assertTrue(lines[1].endswith("First.") and lines[2].endswith("Second."))

    def test_default_now_comes_from_the_clock(self):
        self.clock.now = self.NOW
        text = self.store.render([self.fact("They like tea.", "2026-10-08T18:40:12+02:00")], [])
        self.assertTrue(text.endswith("(yesterday)"))

    def test_round_trip_through_the_store(self):
        self.store.add_fact("They are called Sam.", kind="identity", importance=10)
        self.store.add_episode("2026-10-08T18:31:00+02:00", "2026-10-08T18:52:00+02:00", 9, "We sat quietly.")
        self.clock.now = self.NOW
        text = self.store.render(self.store.recall("hello"), self.store.episodes(limit=2))
        self.assertIn("- They are called Sam. (yesterday)", text)
        self.assertIn("- Yesterday evening, 21 minutes: We sat quietly.", text)


class ThreadSafetyTests(StoreCase):
    def test_many_threads_adding_facts_lose_nothing(self):
        errors = []

        def work(n):
            try:
                for i in range(15):
                    self.store.add_fact(f"Thread {n} fact {i}.", importance=3)
                    self.store.recall(f"thread {n}")
                    if i % 5 == 0:
                        self.store.add_episode("2026-10-08T18:31:00+02:00", "2026-10-08T18:52:00+02:00", 1, f"Talk {n} {i}.")
            except Exception as e:                            # noqa: BLE001 - the test reports any failure
                errors.append(repr(e))

        threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        facts = self.store.facts()
        self.assertEqual(len(facts), 8 * 15)
        self.assertEqual(len({f["id"] for f in facts}), 8 * 15)
        self.assertEqual(len(self.store.episodes()), 8 * 3)
        saved = self.read_json("facts.json")
        self.assertEqual(len(saved["facts"]), 8 * 15)
        self.assertEqual([p.name for p in (self.root / "owner").iterdir() if p.suffix == ".tmp"], [])

    def test_two_store_objects_for_one_person_share_a_lock(self):
        other = MemoryStore(self.root, "owner", self.clock)
        self.assertIs(self.store._lock, other._lock)

        def work(store, n):
            for i in range(20):
                store.add_fact(f"Store {n} fact {i}.")

        threads = [threading.Thread(target=work, args=(s, n)) for n, s in enumerate((self.store, other))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.store.facts()), 40)


if __name__ == "__main__":
    unittest.main()
