"""The memory keeper (mind/keeper.py): a fake mind that answers from a queue, a real store in a temp folder,
a fake clock and a fake history. Nothing here talks to the network."""
import json
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from mind.brain import BrainError, DemoBrain
from mind.keeper import DREAM_PROMPT, GAVE_UP_SUMMARY, NOTE_PROMPT, MemoryKeeper, parse_answer
from mind.memory import MemoryStore

TZ = timezone(timedelta(hours=2))
T0 = datetime(2026, 10, 8, 18, 0, tzinfo=TZ)            # a Thursday evening


def at(day=8, hour=18, minute=0, month=10):
    return datetime(2026, month, day, hour, minute, tzinfo=TZ)


def msg(role, content, minutes, **extra):
    return {"role": role, "content": content, "time": (T0 + timedelta(minutes=minutes)).isoformat(), **extra}


def answer(summary="I kept them company.", mood="calm", facts=None, **extra):
    return json.dumps({"summary": summary, "mood": mood, "facts": facts if facts is not None else [], **extra})


def dream_answer(**parts):
    base = {"merge": [], "outdate": [], "importance": [], "diary": "", "morning_thought": ""}
    base.update(parts)
    return json.dumps(base)


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class FakeBrain:
    """complete() hands out the queued answers (an Exception in the queue is raised) and records what it got."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def queue(self, *answers):
        self.answers.extend(answers)

    def complete(self, messages, max_tokens=900, json_mode=True):
        self.calls.append({"messages": messages, "max_tokens": max_tokens, "json_mode": json_mode})
        if not self.answers:
            raise RuntimeError("the test did not expect another call to the mind")
        nxt = self.answers.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def user_text(self, call=-1):
        return self.calls[call]["messages"][1]["content"]


class KeeperCase(unittest.TestCase):
    def setUp(self):
        self.out = StringIO()
        quiet = redirect_stdout(self.out)
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock(T0 + timedelta(minutes=90))          # 19:30, well after the talks below
        self.store = MemoryStore(Path(self.tmp.name) / "memory", "owner", self.clock)
        self.brain = FakeBrain()
        self.history = []
        self.keeper = MemoryKeeper(self.brain, self.store, lambda: [dict(m) for m in self.history], self.clock)

    def talk(self, start, *lines, **extra):
        """Messages one minute apart from minute `start`, alternating person and Arty."""
        for i, text in enumerate(lines):
            self.history.append(msg("user" if i % 2 == 0 else "assistant", text, start + i, **extra))
        return start + len(lines) - 1

    def noted_until(self):
        return self.store.state().get("noted_until")

    def active(self):
        return {f["id"]: f for f in self.store.facts()}

    def episode(self, end, summary="I listened to them.", start=None, mood="calm"):
        end = end if isinstance(end, str) else end.isoformat()
        return self.store.add_episode(start or end, end, 4, summary, mood)

    def first_tick_done(self):
        """Use up the 'first tick after start' (the catch-up), on an empty store, so the night rules can be tested alone."""
        self.keeper.tick()
        self.assertEqual(self.brain.calls, [])


# ---------------------------------------------------------------------------
# Reading the AI's answer
# ---------------------------------------------------------------------------

class ParseTests(unittest.TestCase):
    def test_plain_fenced_and_surrounded_json_are_read(self):
        self.assertEqual(parse_answer('{"a": 1}'), {"a": 1})
        self.assertEqual(parse_answer('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_answer('```\n{"a": {"b": 2}}\n```'), {"a": {"b": 2}})
        self.assertEqual(parse_answer('Sure! Here it is: {"a": 1} Hope that helps.'), {"a": 1})

    def test_anything_else_is_refused(self):
        for bad in ("", None, "no json here", "[1, 2]", '{"a": 1', "```json\n```", '"just text"', "{broken}"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_answer(bad)


# ---------------------------------------------------------------------------
# Noting talks
# ---------------------------------------------------------------------------

class NoteTests(KeeperCase):
    def test_a_finished_talk_becomes_facts_and_an_episode(self):
        home = self.store.add_fact("They live in Antwerp.", "identity", 7)["id"]
        coffee = self.store.add_fact("They drink coffee.", "preference", 3)["id"]
        last = self.talk(0, "I just moved to Ghent!", "[emote:happy] Ghent! Tell me everything.", "I start work on Friday.", "Ooh.")
        self.brain.queue(answer("I heard all about their move to Ghent.", "excited", [
            {"op": "add", "text": "They start a new job on Friday 10 October.", "kind": "plan", "importance": 12},
            {"op": "update", "id": coffee, "text": "They drink oat-milk lattes.", "importance": 5},
            {"op": "outdate", "id": home, "replaced_by_text": "They now live in Ghent.", "kind": "identity", "importance": 8},
        ]))

        self.assertEqual(self.keeper.note_talks(), 1)

        facts = self.active()
        new_job = next(f for f in facts.values() if f["text"].startswith("They start a new job"))
        self.assertEqual((new_job["kind"], new_job["importance"], new_job["source"]), ("plan", 10, "talk on 2026-10-08 18:00"))
        self.assertEqual((facts[coffee]["text"], facts[coffee]["importance"]), ("They drink oat-milk lattes.", 5))
        ghent = next(f for f in facts.values() if f["text"] == "They now live in Ghent.")
        self.assertEqual((ghent["kind"], ghent["importance"]), ("identity", 8))
        old_home = self.store.get(home)
        self.assertEqual((old_home["status"], old_home["replaced_by"]), ("outdated", ghent["id"]))
        self.assertNotIn(home, facts)

        [episode] = self.store.episodes()
        self.assertEqual(episode["summary"], "I heard all about their move to Ghent.")
        self.assertEqual(episode["mood"], "excited")
        self.assertEqual(episode["turns"], 4)
        self.assertEqual((episode["start"], episode["end"]), (T0.isoformat(), (T0 + timedelta(minutes=last)).isoformat()))
        self.assertEqual(self.noted_until(), (T0 + timedelta(minutes=last)).isoformat())
        self.assertEqual(len(self.brain.calls), 1)
        self.assertIn("1 outdated", self.out.getvalue())
        self.assertTrue(self.keeper.status.startswith("Noted a talk of 4 messages: 2 new, 1 updated, 1 outdated"))

    def test_what_the_ai_is_given(self):
        coffee = self.store.add_fact("They drink coffee.", "preference", 3)["id"]
        pinned = self.store.add_fact("They are allergic to nuts.", "identity", 6, pinned=True)["id"]
        self.talk(0, "I had my coffee already.", "[emote:happy] [look:left] *waves* Good morning! \U0001F600", "Yes.")
        self.brain.queue(answer())
        self.keeper.note_talks()

        [call] = self.brain.calls
        self.assertTrue(call["json_mode"])
        system, user = call["messages"]
        self.assertEqual((system["role"], system["content"]), ("system", NOTE_PROMPT))
        self.assertEqual(user["role"], "user")
        text = user["content"]
        self.assertIn("Today is Thursday 8 October 2026, 19:30.", text)
        self.assertIn(f"{coffee} | preference | 3 | They drink coffee.", text)
        self.assertIn(f"{pinned} | identity | 6 | They are allergic to nuts.", text)
        self.assertLess(text.index("Pinned facts"), text.index(f"{pinned} |"))
        self.assertGreater(text.index("Pinned facts"), text.index(f"{coffee} |"))       # the pinned one is not in the first list
        self.assertIn("18:00 They: I had my coffee already.", text)
        self.assertIn("18:01 Arty: Good morning!", text)
        self.assertIn("18:02 They: Yes.", text)
        for stage_direction in ("[emote", "[look", "*waves*", "\U0001F600"):
            self.assertNotIn(stage_direction, text)

    def test_the_prompt_asks_for_the_json_shape_and_every_rule(self):
        for needle in ('"summary"', '"mood"', '"facts"', '"op": "add"', '"op": "update"', '"op": "outdate"',
                       "replaced_by_text", "third person", "forget", "nickname", "importance 9", "first person",
                       "past tense", "at most 12", "pinned", "near-duplicate", "Small talk"):
            self.assertIn(needle, NOTE_PROMPT)
        for needle in ('"merge"', '"outdate"', '"importance"', '"diary"', '"morning_thought"', "pinned"):
            self.assertIn(needle, DREAM_PROMPT)

    def test_an_unfinished_talk_waits(self):
        self.talk(0, "Hello Arty.", "Hi!")
        self.clock.now = T0 + timedelta(minutes=1 + 3)          # three minutes after the last message
        self.assertEqual(self.keeper.note_talks(), 0)
        self.assertEqual(self.brain.calls, [])
        self.assertIsNone(self.noted_until())
        self.assertEqual(self.store.episodes(), [])

        self.brain.queue(answer())
        self.clock.now = T0 + timedelta(minutes=1 + 11)         # eleven minutes: settled
        self.assertEqual(self.keeper.note_talks(), 1)

    def test_a_long_talk_is_noted_even_when_it_is_not_over(self):
        lines = [f"message {i}" for i in range(24)]
        self.talk(0, *lines)
        self.clock.now = T0 + timedelta(minutes=24)             # one minute after the last message
        self.brain.queue(answer())
        self.assertEqual(self.keeper.note_talks(), 1)
        self.assertEqual(self.store.episodes()[0]["turns"], 24)

    def test_twenty_three_messages_still_wait(self):
        self.talk(0, *[f"message {i}" for i in range(23)])
        self.clock.now = T0 + timedelta(minutes=24)
        self.assertEqual(self.keeper.note_talks(), 0)
        self.assertEqual(self.brain.calls, [])

    def test_force_notes_everything_waiting(self):
        self.talk(0, "Hello Arty.", "Hi!")
        self.clock.now = T0 + timedelta(minutes=2)
        self.assertEqual(self.keeper.note_talks(), 0)
        self.brain.queue(answer("We said hello."))
        self.assertEqual(self.keeper.note_talks(force=True), 1)
        self.assertEqual(self.store.episodes()[0]["summary"], "We said hello.")
        self.assertEqual(self.keeper.note_talks(force=True), 0)         # nothing left
        self.assertEqual(self.keeper.status, "Nothing new to note.")

    def test_nothing_waiting_means_no_call(self):
        self.assertEqual(self.keeper.note_talks(), 0)
        self.assertEqual(self.brain.calls, [])

    def test_a_noted_talk_is_not_noted_again_and_new_messages_are(self):
        self.talk(0, "Hello Arty.", "Hi!")
        self.brain.queue(answer("We said hello."), answer("We talked again."))
        self.assertEqual(self.keeper.note_talks(), 1)
        self.assertEqual(self.keeper.note_talks(), 0)
        self.assertEqual(len(self.brain.calls), 1)

        self.talk(200, "I am back.", "Welcome back!")
        self.clock.now = T0 + timedelta(minutes=300)
        self.assertEqual(self.keeper.note_talks(), 1)
        self.assertEqual(len(self.store.episodes()), 2)
        self.assertNotIn("Hello Arty", self.brain.user_text())          # only the new messages were sent
        self.assertIn("I am back.", self.brain.user_text())

    def test_events_started_by_arty_are_not_in_the_transcript_but_move_noted_until(self):
        self.history += [
            msg("user", "(Event: you have been alone and quiet for a while)", 0, event="wants_company"),
            msg("assistant", "[emote:curious] Psst. Tell me something small?", 0),
            msg("user", "A pigeon sat on my window.", 1),
            msg("assistant", "A pigeon!", 2),
            msg("user", "(Event: you have been alone and quiet for a while)", 3, event="wants_company"),   # last, with no reply
        ]
        self.brain.queue(answer())
        self.assertEqual(self.keeper.note_talks(), 1)
        text = self.brain.user_text()
        self.assertNotIn("Event", text)
        self.assertIn("18:00 Arty: Psst. Tell me something small?", text)
        self.assertIn("18:01 They: A pigeon sat on my window.", text)
        self.assertEqual(self.store.episodes()[0]["turns"], 3)
        self.assertEqual(self.noted_until(), (T0 + timedelta(minutes=3)).isoformat())

    def test_a_talk_where_only_arty_spoke_is_skipped_without_asking(self):
        self.history += [
            msg("user", "(Event: you would like some company)", 0, event="wants_company"),
            msg("assistant", "Psst. Anyone there?", 0),
        ]
        self.assertEqual(self.keeper.note_talks(), 0)
        self.assertEqual(self.brain.calls, [])
        self.assertEqual(self.store.episodes(), [])
        self.assertEqual(self.noted_until(), T0.isoformat())

    def test_two_talks_split_by_a_gap_are_two_episodes(self):
        self.talk(0, "Morning!", "Good morning.")
        self.talk(60, "Evening!", "Good evening.")                  # 59 minutes of quiet: a new talk
        self.clock.now = T0 + timedelta(minutes=200)
        self.brain.queue(answer("First talk."), answer("Second talk."))
        self.assertEqual(self.keeper.note_talks(), 2)
        self.assertEqual([e["summary"] for e in self.store.episodes()], ["First talk.", "Second talk."])
        self.assertIn("Morning!", self.brain.user_text(0))
        self.assertNotIn("Evening!", self.brain.user_text(0))
        self.assertIn("Evening!", self.brain.user_text(1))
        self.assertEqual(self.noted_until(), (T0 + timedelta(minutes=61)).isoformat())

    def test_a_gap_just_under_the_limit_keeps_one_talk(self):
        self.talk(0, "Morning!", "Good morning.")
        self.talk(1 + 45, "Still here.", "Me too.")                 # exactly 45 minutes: still the same talk
        self.clock.now = T0 + timedelta(minutes=200)
        self.brain.queue(answer())
        self.assertEqual(self.keeper.note_talks(), 1)

    def test_an_older_talk_is_noted_while_the_newest_still_waits(self):
        self.talk(0, "Morning!", "Good morning.")
        self.talk(100, "Evening!", "Good evening.")
        self.clock.now = T0 + timedelta(minutes=102)               # the second talk ended one minute ago
        self.brain.queue(answer("First talk."))
        self.assertEqual(self.keeper.note_talks(), 1)
        self.assertEqual(len(self.brain.calls), 1)
        self.assertEqual(self.noted_until(), (T0 + timedelta(minutes=1)).isoformat())

    def test_the_date_is_written_when_a_talk_goes_past_midnight(self):
        base = at(8, 23, 50)
        for i, text in enumerate(["Still up?", "Yes!", "Midnight snack.", "Nice."]):
            self.history.append({"role": "user" if i % 2 == 0 else "assistant", "content": text,
                                 "time": (base + timedelta(minutes=5 * i)).isoformat()})
        self.clock.now = at(9, 9, 0)
        self.brain.queue(answer())
        self.keeper.note_talks()
        text = self.brain.user_text()
        self.assertIn("-- Friday 9 October 2026 --", text)
        self.assertIn("23:50 They: Still up?", text)
        self.assertIn("00:00 They: Midnight snack.", text)

    def test_messages_without_a_time_are_old_and_ignored(self):
        self.history += [{"role": "user", "content": "from before times were kept"},
                         {"role": "assistant", "content": "an old reply"}]
        self.talk(0, "Hello Arty.", "Hi!")
        self.brain.queue(answer())
        self.assertEqual(self.keeper.note_talks(), 1)
        self.assertNotIn("before times", self.brain.user_text())

    def test_pinned_facts_are_never_changed(self):
        pinned = self.store.add_fact("They are called Sam.", "identity", 9, pinned=True)["id"]
        self.talk(0, "Call me Alex now.", "Okay!")
        self.brain.queue(answer(facts=[
            {"op": "update", "id": pinned, "text": "They are called Alex."},
            {"op": "outdate", "id": pinned, "replaced_by_text": "They are called Alex.", "kind": "identity"},
        ]))
        self.assertEqual(self.keeper.note_talks(), 1)
        fact = self.store.get(pinned)
        self.assertEqual((fact["text"], fact["status"], fact["replaced_by"]), ("They are called Sam.", "active", None))
        self.assertEqual(len(self.store.facts(include_outdated=True)), 1)       # no replacement was added either

    def test_unknown_ids_empty_texts_and_outdated_facts_are_ignored(self):
        gone = self.store.add_fact("They had a cat called Tom.", "relationship", 5)["id"]
        self.store.outdate_fact(gone)
        keep = self.store.add_fact("They like tea.", "preference", 4)["id"]
        self.talk(0, "Tea again.", "Nice.")
        self.brain.queue(answer(facts=[
            {"op": "update", "id": "f_nope", "text": "They like soup."},
            {"op": "outdate", "id": "f_nope"},
            {"op": "outdate", "id": "f_nope", "replaced_by_text": "They like soup."},
            {"op": "update", "id": gone, "text": "They had a dog."},
            {"op": "update", "id": keep, "text": "   "},
            {"op": "update", "id": keep},
            {"op": "update"},
            {"op": "add", "text": ""},
            {"op": "add", "text": "   "},
            {"op": "add"},
            {"op": "add", "text": ["not", "text"]},
            {"op": "frobnicate", "text": "They like soup."},
        ]))
        self.assertEqual(self.keeper.note_talks(), 1)
        self.assertEqual([f["text"] for f in self.store.facts(include_outdated=True)],
                         ["They had a cat called Tom.", "They like tea."])
        self.assertEqual(self.store.get(keep)["text"], "They like tea.")
        self.assertEqual(self.store.get(gone)["status"], "outdated")
        self.assertEqual(len(self.store.episodes()), 1)

    def test_at_most_twelve_operations_are_applied(self):
        self.talk(0, "I like many things.", "Do tell.")
        self.brain.queue(answer(facts=[{"op": "add", "text": f"They like thing number {i}.", "kind": "preference",
                                        "importance": 3} for i in range(15)]))
        self.keeper.note_talks()
        self.assertEqual(len(self.store.facts()), 12)
        self.assertTrue(self.store.facts()[0]["text"].endswith("number 0."))
        self.assertTrue(self.store.facts()[-1]["text"].endswith("number 11."))

    def test_odd_answers_are_handled(self):
        self.talk(0, "A lot happened.", "Go on.")
        self.brain.queue(answer(mood="x" * 100, facts=[
            {"text": "They have a sister called Anna.", "kind": "relationship", "importance": 8},      # no "op": an add
            "They like rain.",                                                                          # a string: skipped
            42,
            None,
            {"op": "add", "text": "They [sigh] love toast.", "kind": "banana", "importance": 0},
            {"op": "ADD", "text": "They own a bike.", "kind": "other", "importance": "7"},
            {"op": "add", "text": "They own a boat.", "importance": "very"},
            {"op": "add", "text": "They own a hat.", "importance": 99, "kind": "event"},
            {"op": "add", "text": "They own a " + "very " * 80 + "long scarf."},
        ]))
        self.keeper.note_talks()
        by_text = {f["text"][:18]: f for f in self.store.facts()}
        self.assertEqual(len(self.store.facts()), 6)
        anna = by_text["They have a sister"]
        self.assertEqual((anna["kind"], anna["importance"]), ("relationship", 8))
        toast = by_text["They sigh love toa"]
        self.assertEqual((toast["kind"], toast["importance"]), ("other", 1))
        self.assertEqual(by_text["They own a bike."]["importance"], 7)
        self.assertEqual(by_text["They own a boat."]["importance"], 5)
        self.assertEqual((by_text["They own a hat."]["kind"], by_text["They own a hat."]["importance"]), ("event", 10))
        self.assertLessEqual(max(len(f["text"]) for f in self.store.facts()), 200)
        self.assertLessEqual(len(self.store.episodes()[0]["mood"]), 40)

    def test_a_replacement_for_a_changed_fact_defaults_to_the_old_kind_and_importance(self):
        job = self.store.add_fact("They work at the bakery.", "identity", 6)["id"]
        self.talk(0, "I left the bakery.", "Oh!")
        self.brain.queue(answer(facts=[{"op": "outdate", "id": job, "replaced_by_text": "They left the bakery."}]))
        self.keeper.note_talks()
        new = next(f for f in self.store.facts() if f["text"] == "They left the bakery.")
        self.assertEqual((new["kind"], new["importance"]), ("identity", 6))
        self.assertEqual(self.store.get(job)["replaced_by"], new["id"])

    def test_forgetting_outdates_without_a_replacement(self):
        secret = self.store.add_fact("They are saving for a surprise party.", "plan", 5)["id"]
        self.talk(0, "Please forget the party thing.", "Forgotten.")
        self.brain.queue(answer(facts=[{"op": "outdate", "id": secret}]))
        self.keeper.note_talks()
        fact = self.store.get(secret)
        self.assertEqual((fact["status"], fact["replaced_by"]), ("outdated", None))
        self.assertEqual(self.store.facts(), [])

    def test_the_same_fact_is_not_added_twice(self):
        self.store.add_fact("They like tea.", "preference", 4)
        self.talk(0, "I like tea.", "Me too.")
        self.brain.queue(answer(facts=[{"op": "add", "text": "they like tea", "kind": "preference"}]))
        self.keeper.note_talks()
        self.assertEqual(len(self.store.facts()), 1)
        self.assertIn("0 new", self.keeper.status)

    def test_fenced_json_and_words_around_it_are_accepted(self):
        for wrapped in ("```json\n" + answer("Fenced.") + "\n```", "Here you go:\n" + answer("Chatty.") + "\nAnything else?"):
            with self.subTest(wrapped=wrapped[:12]):
                self.setUp()
                self.talk(0, "Hello Arty.", "Hi!")
                self.brain.queue(wrapped)
                self.assertEqual(self.keeper.note_talks(), 1)
                self.assertIn(self.store.episodes()[0]["summary"], ("Fenced.", "Chatty."))

    def test_a_summary_with_stage_directions_is_cleaned(self):
        self.talk(0, "Hello Arty.", "Hi!")
        self.brain.queue(answer("I said [emote:happy] hello to them."))
        self.keeper.note_talks()
        self.assertEqual(self.store.episodes()[0]["summary"], "I said emote:happy hello to them.")

    # ---- failures ----

    def test_an_unreachable_mind_never_uses_up_the_tries(self):
        self.talk(0, "My sister visits on Friday.", "How lovely!")
        self.brain.queue(*[BrainError("Could not reach DeepSeek.") for _ in range(5)])
        for _ in range(5):                                                  # five minutes without internet
            self.assertEqual(self.keeper.note_talks(), 0)
        self.assertEqual((self.store.episodes(), self.noted_until()), ([], None))
        self.assertIn("Waiting for the mind", self.keeper.status)
        self.brain.queue(answer("We talked about their sister's visit."))
        self.assertEqual(self.keeper.note_talks(), 1)                       # the internet is back: noted properly
        self.assertEqual(self.store.episodes()[0]["summary"], "We talked about their sister's visit.")

    def test_garbage_is_retried_then_given_up_after_three_tries(self):
        self.talk(0, "Hello Arty.", "Hi!")
        self.brain.queue("I am sorry, I cannot do that.", "{not json", "[]")
        self.keeper.tick()
        self.assertEqual((self.store.episodes(), self.noted_until()), ([], None))
        self.assertIn("try again", self.keeper.status)
        self.keeper.tick()
        self.assertEqual((self.store.episodes(), self.noted_until()), ([], None))

        self.keeper.tick()                                                  # the third failure: move on
        [episode] = self.store.episodes()
        self.assertEqual(episode["summary"], GAVE_UP_SUMMARY)
        self.assertEqual((episode["start"], episode["end"]), (T0.isoformat(), (T0 + timedelta(minutes=1)).isoformat()))
        self.assertEqual(self.noted_until(), (T0 + timedelta(minutes=1)).isoformat())
        self.assertEqual(self.store.facts(), [])
        self.assertTrue(self.keeper.status.startswith("Gave up"))

        self.keeper.tick()
        self.keeper.tick()
        self.assertEqual(len(self.brain.calls), 3)                          # no fourth try
        self.assertEqual(len(self.store.episodes()), 1)

    def test_other_errors_count_like_garbage(self):
        self.talk(0, "Hello Arty.", "Hi!")
        self.brain.queue(ValueError("odd answer"), RuntimeError("boom"), TimeoutError())
        self.assertEqual(self.keeper.note_talks(), 0)
        self.assertEqual(self.keeper.note_talks(), 0)
        self.assertEqual(self.store.episodes(), [])
        self.assertEqual(self.keeper.note_talks(), 1)                       # the third failure: moved on
        self.assertEqual(self.store.episodes()[0]["summary"], GAVE_UP_SUMMARY)

    def test_each_kind_of_nonsense_fails_the_try_without_changing_anything(self):
        for nonsense in ("", "plain words", "[1, 2, 3]", '{"summary": "cut off', answer(""), json.dumps({"facts": []}),
                         json.dumps({"summary": 12, "facts": []})):
            with self.subTest(nonsense=nonsense[:20]):
                self.setUp()
                self.store.add_fact("They like tea.", "preference", 4)
                self.talk(0, "Hello Arty.", "Hi!")
                self.brain.queue(nonsense)
                self.assertEqual(self.keeper.note_talks(), 0)
                self.assertEqual(self.store.episodes(), [])
                self.assertIsNone(self.noted_until())
                self.assertEqual(len(self.store.facts()), 1)

    def test_a_good_answer_after_a_failure_resets_the_tries(self):
        self.talk(0, "Hello Arty.", "Hi!")
        self.brain.queue("garbage", "garbage", answer("It worked."))
        self.keeper.note_talks()
        self.keeper.note_talks()
        self.assertEqual(self.keeper.note_talks(), 1)
        self.assertEqual(self.store.episodes()[0]["summary"], "It worked.")

    def test_a_failing_older_talk_holds_back_the_newer_one(self):
        self.talk(0, "Morning!", "Good morning.")
        self.talk(100, "Evening!", "Good evening.")
        self.clock.now = T0 + timedelta(minutes=300)
        self.brain.queue("garbage")
        self.assertEqual(self.keeper.note_talks(), 0)
        self.assertEqual(len(self.brain.calls), 1)                          # the second talk was not tried first
        self.assertIsNone(self.noted_until())

    def test_a_broken_history_does_not_raise(self):
        keeper = MemoryKeeper(self.brain, self.store, lambda: 1 / 0, self.clock)
        self.assertEqual(keeper.note_talks(force=True), 0)
        keeper.tick()
        self.assertIn("Could not", keeper.status)

    def test_tick_notes_finished_talks(self):
        self.talk(0, "Hello Arty.", "Hi!")
        self.brain.queue(answer("We said hello."))
        self.keeper.tick()
        self.assertEqual(self.store.episodes()[0]["summary"], "We said hello.")

    def test_a_run_that_is_going_blocks_a_second_one_instead_of_noting_twice(self):
        class Slow(FakeBrain):
            def __init__(self, *answers):
                super().__init__(*answers)
                self.inside, self.release = threading.Event(), threading.Event()

            def complete(self, messages, max_tokens=900, json_mode=True):
                self.inside.set()
                self.release.wait(5)
                return super().complete(messages, max_tokens, json_mode)

        slow = Slow(answer("Noted once."))
        keeper = MemoryKeeper(slow, self.store, lambda: list(self.history), self.clock)
        self.talk(0, "Hello Arty.", "Hi!")
        results = {}
        first = threading.Thread(target=lambda: results.update(first=keeper.note_talks(force=True)))
        first.start()
        self.assertTrue(slow.inside.wait(5))
        self.assertEqual(keeper.note_talks(), 0)                            # the thread's own check: busy, skip at once
        self.assertFalse(keeper.dream())
        second = threading.Thread(target=lambda: results.update(second=keeper.note_talks(force=True)))
        second.start()                                                      # a forced run waits its turn
        time.sleep(0.1)
        slow.release.set()
        first.join(5)
        second.join(5)
        self.assertEqual(results, {"first": 1, "second": 0})
        self.assertEqual(len(slow.calls), 1)
        self.assertEqual(len(self.store.episodes()), 1)

    def test_demo_mind_output_is_handled(self):
        keeper = MemoryKeeper(DemoBrain(), self.store, lambda: list(self.history), self.clock)
        self.talk(0, "Hello Arty.", "Hi!")
        self.assertEqual(keeper.note_talks(), 1)
        [episode] = self.store.episodes()
        self.assertEqual(episode["summary"], "We had a little demo chat.")
        self.assertEqual(self.store.facts(), [])
        self.assertTrue(keeper.status.startswith("Noted a talk"))


# ---------------------------------------------------------------------------
# Dreaming
# ---------------------------------------------------------------------------

class DreamScheduleTests(KeeperCase):
    def setUp(self):
        super().setUp()
        self.first_tick_done()
        self.store.add_fact("They like tea.", "preference", 4)
        self.episode(at(8, 18, 20), "I listened to them talk about tea.")

    def tick_at(self, day, hour, minute=0):
        self.clock.now = at(day, hour, minute)
        self.keeper.tick()

    def test_it_dreams_from_two_until_six_local_time(self):
        self.brain.queue(dream_answer())
        self.tick_at(9, 1, 59)
        self.assertEqual(self.brain.calls, [])
        self.tick_at(9, 6, 0)
        self.assertEqual(self.brain.calls, [])
        self.tick_at(9, 5, 59)
        self.assertEqual(len(self.brain.calls), 1)

    def test_it_dreams_at_two(self):
        self.brain.queue(dream_answer())
        self.tick_at(9, 2, 0)
        self.assertEqual(len(self.brain.calls), 1)
        state = self.store.state()
        self.assertEqual(state["last_dream"], "2026-10-09")
        self.assertEqual(state["last_dream_at"], at(9, 2, 0).isoformat())

    def test_it_dreams_once_a_day(self):
        self.brain.queue(dream_answer(), dream_answer())
        self.tick_at(9, 3)
        self.assertEqual(len(self.brain.calls), 1)
        self.episode(at(9, 3, 40), "I heard them come home late.")
        self.tick_at(9, 4)
        self.tick_at(9, 5, 30)
        self.assertEqual(len(self.brain.calls), 1)
        self.tick_at(10, 3)                                                 # the next night
        self.assertEqual(len(self.brain.calls), 2)
        self.assertIn("come home late", self.brain.user_text())
        self.assertNotIn("talk about tea", self.brain.user_text())          # the first talk was already dreamed about

    def test_with_nothing_new_there_is_no_call_and_it_does_not_look_again_that_night(self):
        self.store.set_state(last_dream="2026-10-08", last_dream_at=at(8, 23, 0).isoformat())
        self.tick_at(9, 3)
        self.assertEqual(self.brain.calls, [])
        self.assertEqual(self.store.state()["last_dream"], "2026-10-09")
        self.assertEqual(self.store.state()["last_dream_at"], at(8, 23, 0).isoformat())       # unchanged
        self.episode(at(9, 3, 30), "A late chat.")
        self.tick_at(9, 4)
        self.assertEqual(self.brain.calls, [])                              # already checked tonight
        self.brain.queue(dream_answer())
        self.tick_at(10, 3)                                                 # tomorrow night it counts
        self.assertEqual(len(self.brain.calls), 1)

    def test_a_talk_that_could_not_be_summarised_is_not_worth_a_dream(self):
        self.store.set_state(last_dream="2026-10-08", last_dream_at=at(8, 23, 0).isoformat())
        self.episode(at(9, 1, 0), GAVE_UP_SUMMARY)
        self.tick_at(9, 3)
        self.assertEqual(self.brain.calls, [])

    def test_the_catch_up_on_the_first_tick_after_start(self):
        self.keeper._first_tick = True                                      # what start() does
        self.brain.queue(dream_answer())
        self.tick_at(9, 15)                                                 # mid-afternoon, never dreamed, a new talk
        self.assertEqual(len(self.brain.calls), 1)
        self.assertEqual(self.store.state()["last_dream_at"], at(9, 15).isoformat())

    def test_the_catch_up_happens_on_the_first_tick_only(self):
        self.brain.queue(dream_answer())
        self.tick_at(9, 15)                                                 # not the first tick (setUp used it)
        self.assertEqual(self.brain.calls, [])

    def test_the_catch_up_needs_the_last_dream_to_be_over_twenty_hours_old(self):
        for hours_ago, expected_calls in ((10, 0), (19, 0), (21, 1), (60, 1)):
            with self.subTest(hours_ago=hours_ago):
                self.setUp()
                self.keeper._first_tick = True
                self.store.set_state(last_dream="2026-10-07", last_dream_at=(at(9, 15) - timedelta(hours=hours_ago)).isoformat())
                self.episode(at(9, 14), "A recent chat.")                  # newer than any of those dreams
                self.brain.queue(dream_answer())
                self.tick_at(9, 15)
                self.assertEqual(len(self.brain.calls), expected_calls)

    def test_the_catch_up_needs_a_new_talk(self):
        self.keeper._first_tick = True
        self.store.set_state(last_dream="2026-10-07", last_dream_at=at(8, 20, 0).isoformat())    # after the tea talk
        self.tick_at(9, 18)                                                 # over twenty hours later
        self.assertEqual(self.brain.calls, [])

    def test_a_failed_dream_is_tried_again_but_only_three_times_a_night(self):
        self.brain.queue("garbage", "{oops", "[]", dream_answer())
        for minute in range(5):
            self.tick_at(9, 3, minute)
        self.assertEqual(len(self.brain.calls), 3)
        self.assertNotIn("last_dream", self.store.state())
        self.assertTrue(self.keeper.status.startswith("Could not dream"))
        self.tick_at(10, 3)                                                 # a new night starts fresh
        self.assertEqual(len(self.brain.calls), 4)
        self.assertEqual(self.store.state()["last_dream"], "2026-10-10")


class DreamTests(KeeperCase):
    def setUp(self):
        super().setUp()
        self.clock.now = at(9, 3, 0)
        self.a = self.store.add_fact("They like green tea.", "preference", 4)["id"]
        self.b = self.store.add_fact("They like black tea.", "preference", 4)["id"]
        self.c = self.store.add_fact("They have a dentist visit on Monday 6 October.", "plan", 5)["id"]
        self.d = self.store.add_fact("They enjoy jazz.", "preference", 6)["id"]
        self.e = self.store.add_fact("They once saw a heron.", "event", 2)["id"]
        self.p = self.store.add_fact("They are called Sam.", "identity", 9, pinned=True)["id"]
        self.episode(at(8, 18, 20), "I listened to them talk about tea.", start=at(8, 18, 0))
        self.episode(at(8, 21, 0), "We sat quietly while they listened to jazz.", start=at(8, 20, 40), mood="calm")

    def test_it_applies_merges_outdates_and_importance_changes(self):
        self.brain.queue(dream_answer(
            merge=[{"ids": [self.a, self.b], "text": "They like green and black tea.", "kind": "preference", "importance": 5}],
            outdate=[{"id": self.c}, {"id": self.p}, {"id": "f_nope"}, self.e, "junk", 7, None],
            importance=[{"id": self.d, "importance": 8}, {"id": self.p, "importance": 1}, {"id": "f_nope", "importance": 9},
                        {"id": self.a, "importance": "high"}, "junk"]))
        self.assertTrue(self.keeper.dream(force=True))

        merged = next(f for f in self.store.facts() if f["text"] == "They like green and black tea.")
        self.assertEqual((merged["kind"], merged["importance"], merged["source"]), ("preference", 5, "dream on 2026-10-09"))
        for old in (self.a, self.b):
            fact = self.store.get(old)
            self.assertEqual((fact["status"], fact["replaced_by"]), ("outdated", merged["id"]))
        self.assertEqual(self.store.get(self.c)["status"], "outdated")
        self.assertEqual(self.store.get(self.e)["status"], "outdated")
        self.assertEqual(self.store.get(self.d)["importance"], 8)
        pinned = self.store.get(self.p)
        self.assertEqual((pinned["status"], pinned["importance"], pinned["text"]), ("active", 9, "They are called Sam."))
        self.assertIn("1 merged, 2 outdated, 1 importance changed", self.keeper.status)

    def test_merges_need_two_real_unpinned_facts_and_some_words(self):
        self.brain.queue(dream_answer(merge=[
            {"ids": [self.a, self.p], "text": "They like tea and are called Sam."},         # one is pinned
            {"ids": [self.a, "f_nope"], "text": "They like tea."},                          # one is unknown
            {"ids": [self.a, self.a], "text": "They like green tea."},                      # the same one twice
            {"ids": [self.a, self.b], "text": "   "},                                       # no words
            {"ids": [self.a], "text": "They like green tea a lot."},                        # only one
            {"ids": "f_a", "text": "x"}, {"text": "They like tea."}, "junk",
        ]))
        self.assertTrue(self.keeper.dream(force=True))
        self.assertEqual({f["id"] for f in self.store.facts()}, {self.a, self.b, self.c, self.d, self.e, self.p})

    def test_a_merge_without_a_kind_or_importance_takes_them_from_the_old_facts(self):
        self.store.update_fact(self.b, importance=7)
        self.brain.queue(dream_answer(merge=[{"ids": [self.a, self.b], "text": "They love tea."},
                                             {"ids": [self.c, self.d], "text": "They have plans for jazz.", "kind": "banana"}]))
        self.keeper.dream(force=True)
        tea = next(f for f in self.store.facts() if f["text"] == "They love tea.")
        self.assertEqual((tea["kind"], tea["importance"]), ("preference", 7))
        jazz = next(f for f in self.store.facts() if f["text"] == "They have plans for jazz.")
        self.assertEqual(jazz["kind"], "other")

    def test_it_can_change_nothing(self):
        before = self.store.facts()
        self.brain.queue(dream_answer())
        self.assertTrue(self.keeper.dream(force=True))
        self.assertEqual(self.store.facts(), before)

    def test_changes_per_dream_are_limited(self):
        ids = [self.store.add_fact(f"They know trivia number {i}.", "other", 2)["id"] for i in range(30)]
        self.brain.queue(dream_answer(outdate=[{"id": i} for i in ids]))
        self.keeper.dream(force=True)
        self.assertEqual(len([i for i in ids if self.store.get(i)["status"] == "outdated"]), 20)

    def test_the_diary_is_written_for_the_date_of_the_latest_talk(self):
        self.brain.queue(dream_answer(diary="Today I learned about tea.\n\nIt was a calm day."))
        self.assertTrue(self.keeper.dream())                                # 03:00 on the 9th: a real night dream
        [entry] = self.store.diary()
        self.assertEqual(entry["date"], "2026-10-08")
        self.assertEqual(entry["text"], "Today I learned about tea.\n\nIt was a calm day.")
        self.assertIn("The diary entry is for: Thursday 8 October 2026.", self.brain.user_text())

    def test_the_diary_goes_under_todays_date_when_there_is_no_new_talk(self):
        self.store.set_state(last_dream_at=at(8, 23, 0).isoformat())
        self.clock.now = at(9, 10, 30)
        self.brain.queue(dream_answer(diary="A quiet morning."))
        self.assertTrue(self.keeper.dream(force=True))
        self.assertEqual([d["date"] for d in self.store.diary()], ["2026-10-09"])
        self.assertIn("(none: it was a quiet time)", self.brain.user_text())

    def test_an_empty_diary_writes_nothing(self):
        self.brain.queue(dream_answer(diary="  "))
        self.assertTrue(self.keeper.dream(force=True))
        self.assertEqual(self.store.diary(), [])

    def test_the_morning_thought_is_for_today_before_noon_and_tomorrow_after(self):
        self.brain.queue(dream_answer(morning_thought="Did you listen to more jazz?"))
        self.keeper.dream(force=True)                                       # 03:00
        self.assertEqual(self.store.state()["morning_thought"],
                         {"for_date": "2026-10-09", "text": "Did you listen to more jazz?", "used": False})

        self.clock.now = at(9, 11, 59)
        self.brain.queue(dream_answer(morning_thought="Before noon."))
        self.keeper.dream(force=True)
        self.assertEqual(self.store.state()["morning_thought"]["for_date"], "2026-10-09")

        self.clock.now = at(9, 12, 0)
        self.brain.queue(dream_answer(morning_thought="After noon."))
        self.keeper.dream(force=True)
        self.assertEqual(self.store.state()["morning_thought"],
                         {"for_date": "2026-10-10", "text": "After noon.", "used": False})

    def test_an_empty_morning_thought_leaves_the_old_one_alone(self):
        old = {"for_date": "2026-10-09", "text": "Ask about the heron.", "used": False}
        self.store.set_state(morning_thought=old)
        self.brain.queue(dream_answer(morning_thought=""))
        self.keeper.dream(force=True)
        self.assertEqual(self.store.state()["morning_thought"], old)

    def test_the_dream_is_recorded_and_old_outdated_facts_are_pruned(self):
        self.clock.now = at(1, 12, 0, month=3)                              # months ago
        stale = self.store.add_fact("They used to live in a tent.", "other", 3)["id"]
        self.store.outdate_fact(stale)
        self.assertIsNotNone(self.store.get(stale))
        self.clock.now = at(9, 3, 0)
        self.brain.queue(dream_answer())
        self.assertTrue(self.keeper.dream())
        self.assertIsNone(self.store.get(stale))
        state = self.store.state()
        self.assertEqual((state["last_dream"], state["last_dream_at"]), ("2026-10-09", at(9, 3, 0).isoformat()))
        self.assertIn("1 old fact cleared out", self.keeper.status)

    def test_what_the_ai_is_given(self):
        self.store.set_state(last_dream_at=at(8, 18, 30).isoformat())       # the tea talk ended before this dream
        self.brain.queue(dream_answer())
        self.assertTrue(self.keeper.dream(force=True))
        [call] = self.brain.calls
        self.assertTrue(call["json_mode"])
        system, user = call["messages"]
        self.assertEqual(system["content"], DREAM_PROMPT)
        text = user["content"]
        self.assertIn("Now: Friday 9 October 2026, 03:00.", text)
        self.assertIn(f"{self.d} | preference | 6 | They enjoy jazz.", text)
        self.assertIn(f"{self.p} | identity | 9 | They are called Sam.", text)
        self.assertLess(text.index("Pinned facts"), text.index(f"{self.p} |"))
        self.assertLess(text.index(f"{self.d} |"), text.index(f"{self.a} |"))        # most important first
        self.assertLess(text.index(f"{self.a} |"), text.index(f"{self.e} |"))
        self.assertIn("We sat quietly while they listened to jazz.", text)
        self.assertIn("(mood: calm)", text)
        self.assertNotIn("talk about tea", text)

    def test_at_most_150_facts_are_shown_most_important_first(self):
        for i in range(155):
            self.store.add_fact(f"They know trivia number {i}.", "other", 1 + i % 3)
        self.brain.queue(dream_answer())
        self.keeper.dream(force=True)
        lines = [ln for ln in self.brain.user_text().splitlines() if ln.startswith("f_")]
        self.assertEqual(len(lines), 150)
        importances = [int(ln.split(" | ")[2]) for ln in lines if "trivia" in ln or "tea" in ln or "jazz" in ln or "heron" in ln or "dentist" in ln]
        self.assertEqual(importances, sorted(importances, reverse=True))
        self.assertTrue(any(ln.startswith(self.d) for ln in lines))                  # important ones are never cut

    def test_forcing_a_dream_with_nothing_at_all_does_not_ask(self):
        keeper = MemoryKeeper(self.brain, MemoryStore(Path(self.tmp.name) / "other", "owner", self.clock),
                              lambda: [], self.clock)
        self.assertFalse(keeper.dream(force=True))
        self.assertEqual(self.brain.calls, [])
        self.assertEqual(keeper.status, "Nothing to dream about yet.")

    def test_forcing_skips_the_checks(self):
        self.clock.now = at(9, 15, 0)                                       # the middle of the afternoon
        self.store.set_state(last_dream="2026-10-09", last_dream_at=at(9, 14, 0).isoformat())    # and just dreamed
        self.brain.queue(dream_answer(), dream_answer())
        self.assertFalse(self.keeper.dream())                               # not due
        self.assertEqual(self.brain.calls, [])
        self.assertTrue(self.keeper.dream(force=True))
        self.assertEqual(len(self.brain.calls), 1)

    def test_a_forced_dream_with_facts_but_no_talks_still_asks(self):
        self.store.set_state(last_dream_at=at(8, 23, 0).isoformat())
        self.brain.queue(dream_answer())
        self.assertTrue(self.keeper.dream(force=True))
        self.assertEqual(len(self.brain.calls), 1)

    def test_nonsense_from_the_ai_is_not_a_dream(self):
        for nonsense in ("", "I dreamed of electric sheep.", "[1]", '{"merge": [], "diary": "cut'):
            with self.subTest(nonsense=nonsense[:12]):
                self.brain.queue(nonsense)
                self.assertFalse(self.keeper.dream(force=True))
                self.assertNotIn("last_dream", self.store.state())
                self.assertEqual(self.store.diary(), [])

    def test_wrong_shapes_inside_a_valid_answer_are_skipped(self):
        self.brain.queue(json.dumps({"merge": "no", "outdate": {"id": self.a}, "importance": 5, "diary": ["x"],
                                     "morning_thought": {"text": "y"}}))
        self.assertTrue(self.keeper.dream(force=True))
        self.assertEqual(len(self.store.facts()), 6)
        self.assertEqual(self.store.diary(), [])

    def test_demo_mind_output_is_handled(self):
        keeper = MemoryKeeper(DemoBrain(), self.store, lambda: [], self.clock)
        before = self.store.facts()
        self.assertTrue(keeper.dream(force=True))
        self.assertEqual(self.store.facts(), before)
        self.assertEqual(self.store.diary(), [])
        self.assertNotIn("morning_thought", self.store.state())
        self.assertEqual(self.store.state()["last_dream"], "2026-10-09")
        keeper.tick()                                                       # and the schedule copes with it too


# ---------------------------------------------------------------------------
# The thread
# ---------------------------------------------------------------------------

class ThreadTests(KeeperCase):
    def setUp(self):
        super().setUp()
        self.keeper = MemoryKeeper(self.brain, self.store, lambda: [dict(m) for m in self.history], self.clock,
                                   interval=0.02)
        self.store.set_state(last_dream="2026-10-08", last_dream_at=at(8, 19, 0).isoformat())     # no catch-up dream
        self.addCleanup(self.keeper.stop)

    def wait_for(self, condition, seconds=5.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if condition():
                return True
            time.sleep(0.01)
        return False

    def test_start_notes_a_talk_and_stop_ends_the_thread_quickly(self):
        self.talk(0, "Hello Arty.", "Hi!")
        self.brain.queue(answer("We said hello."))
        self.assertFalse(self.keeper.running)
        self.keeper.start()
        self.assertTrue(self.keeper.running)
        self.assertTrue(self.wait_for(lambda: self.store.episodes()))
        started = time.monotonic()
        self.keeper.stop()
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(self.keeper.running)
        self.assertEqual(len(self.brain.calls), 1)                          # noted once, however many ticks there were

    def test_it_keeps_ticking_and_picks_up_later_talks(self):
        self.keeper.start()
        self.brain.queue(answer("Later."))
        self.history.extend([msg("user", "Hello Arty.", 0), msg("assistant", "Hi!", 1)])      # both at once
        self.assertTrue(self.wait_for(lambda: self.store.episodes()))
        self.assertEqual(self.store.episodes()[0]["summary"], "Later.")

    def test_starting_twice_makes_one_thread_and_it_can_start_again_after_a_stop(self):
        self.keeper.start()
        thread = self.keeper._thread
        self.keeper.start()
        self.assertIs(self.keeper._thread, thread)
        self.keeper.stop()
        self.assertFalse(self.keeper.running)
        self.keeper.start()
        self.assertTrue(self.keeper.running)

    def test_stopping_without_starting_is_fine(self):
        self.keeper.stop()
        self.assertFalse(self.keeper.running)

    def test_the_thread_survives_trouble(self):
        calls = {"n": 0}

        def flaky_history():
            calls["n"] += 1
            if calls["n"] <= 3:
                raise OSError("the history file is on fire")
            return [dict(m) for m in self.history]

        keeper = MemoryKeeper(self.brain, self.store, flaky_history, self.clock, interval=0.02)
        self.addCleanup(keeper.stop)
        self.talk(0, "Hello Arty.", "Hi!")
        self.brain.queue(answer("Survived."))
        keeper.start()
        self.assertTrue(self.wait_for(lambda: self.store.episodes()))
        self.assertTrue(keeper.running)
        self.assertEqual(self.store.episodes()[0]["summary"], "Survived.")

    def test_the_catch_up_dream_happens_on_the_first_tick_after_start(self):
        self.store.set_state(last_dream="2026-10-06", last_dream_at=at(6, 3, 0).isoformat())
        self.episode(at(8, 18, 20), "A talk nobody dreamed about.")
        self.brain.queue(dream_answer(diary="Catching up."))
        self.keeper.start()
        self.assertTrue(self.wait_for(lambda: self.store.diary()))
        self.keeper.stop()
        self.assertEqual(len(self.brain.calls), 1)

    def test_status_starts_plain(self):
        self.assertIsInstance(self.keeper.status, str)
        self.assertTrue(self.keeper.status)


if __name__ == "__main__":
    unittest.main()
